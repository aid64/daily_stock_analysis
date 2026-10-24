# -*- coding: utf-8 -*-
"""Pytest compatibility hooks."""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import gc
import shutil
import tempfile
import time
import threading
from pathlib import Path
from collections.abc import Awaitable, Callable
from contextvars import copy_context
from functools import wraps
from typing import Any, TypeVar
from warnings import warn

import anyio.to_thread
import fastapi.testclient
import pytest
import httpx
import starlette.testclient
from anyio._backends import _asyncio

T = TypeVar("T")

_original_call_soon_threadsafe = asyncio.BaseEventLoop.call_soon_threadsafe


async def _shutdown_default_executor_inline(
    self: asyncio.BaseEventLoop,
    timeout: float | None = None,
) -> None:
    """Avoid lost wakeups while asyncio.run() tears down test-only executors."""
    del timeout
    executor = getattr(self, "_default_executor", None)
    if executor is None:
        return
    self._executor_shutdown_called = True
    self._default_executor = None
    executor.shutdown(wait=True)


def _call_soon_threadsafe_with_extra_wakeup(
    self: asyncio.BaseEventLoop,
    callback,
    *args,
    context=None,
):
    """Wake the selector again for sandboxed test runs where the first wake is lost."""
    handle = _original_call_soon_threadsafe(self, callback, *args, context=context)
    write_to_self = getattr(self, "_write_to_self", None)
    if callable(write_to_self):
        write_to_self()
        threading.Timer(0.001, write_to_self).start()
    return handle


asyncio.BaseEventLoop.call_soon_threadsafe = _call_soon_threadsafe_with_extra_wakeup
asyncio.BaseEventLoop.shutdown_default_executor = _shutdown_default_executor_inline


async def _run_sync_via_asyncio_to_thread(
    func: Callable[..., T],
    *args: Any,
    abandon_on_cancel: bool = False,
    cancellable: bool | None = None,
    limiter: Any = None,
) -> T:
    """Use asyncio's executor path when AnyIO worker queues miss wakeups."""
    del abandon_on_cancel, limiter
    if cancellable is not None:
        warn(
            "The `cancellable=` keyword argument to `anyio.to_thread.run_sync` is "
            "deprecated since AnyIO 4.1.0; use `abandon_on_cancel=` instead",
            DeprecationWarning,
            stacklevel=2,
        )
    future: concurrent.futures.Future[T] = concurrent.futures.Future()
    context = copy_context()

    def runner() -> None:
        try:
            future.set_result(context.run(func, *args))
        except BaseException as exc:
            future.set_exception(exc)

    threading.Thread(target=runner, name="pytest-anyio-worker", daemon=True).start()
    while not future.done():
        await asyncio.sleep(0.001)
    return future.result()


def _wait_for_cross_thread_result(loop: asyncio.AbstractEventLoop, future: concurrent.futures.Future[T]) -> T:
    write_to_self = getattr(loop, "_write_to_self", None)
    while not future.done():
        if callable(write_to_self):
            write_to_self()
        time.sleep(0.001)
    return future.result()


def _run_sync_from_thread_with_wakeup(
    cls,
    func: Callable[..., T],
    args: tuple[Any, ...],
    token: object,
) -> T:
    @wraps(func)
    def wrapper() -> None:
        try:
            _asyncio.set_current_async_library("asyncio")
            future.set_result(func(*args))
        except BaseException as exc:
            future.set_exception(exc)
            if not isinstance(exc, Exception):
                raise

    loop = token or _asyncio.threadlocals.current_token.native_token
    if loop.is_closed():
        raise _asyncio.RunFinishedError
    future: concurrent.futures.Future[T] = concurrent.futures.Future()
    loop.call_soon_threadsafe(wrapper)
    return _wait_for_cross_thread_result(loop, future)


def _run_async_from_thread_with_wakeup(
    cls,
    func: Callable[..., Awaitable[T]],
    args: tuple[Any, ...],
    token: object,
) -> T:
    loop = token or _asyncio.threadlocals.current_token.native_token
    if loop.is_closed():
        raise _asyncio.RunFinishedError
    context = copy_context()
    context.run(_asyncio.set_current_async_library, "asyncio")
    future = context.run(asyncio.run_coroutine_threadsafe, func(*args), loop=loop)
    return _wait_for_cross_thread_result(loop, future)


anyio.to_thread.run_sync = _run_sync_via_asyncio_to_thread
_asyncio.AsyncIOBackend.run_sync_from_thread = classmethod(_run_sync_from_thread_with_wakeup)
_asyncio.AsyncIOBackend.run_async_from_thread = classmethod(_run_async_from_thread_with_wakeup)


class _ThreadlessTestClient:
    """Small TestClient replacement that avoids AnyIO's cross-thread portal."""

    def __init__(
        self,
        app,
        base_url: str = "http://testserver",
        raise_server_exceptions: bool = True,
        follow_redirects: bool = True,
        **_: Any,
    ) -> None:
        self.app = app
        self.base_url = base_url
        self.raise_server_exceptions = raise_server_exceptions
        self.follow_redirects = follow_redirects
        self.cookies = httpx.Cookies()
        self._lifespan_ctx = None
        self._lifespan_enter_count = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._async_client: httpx.AsyncClient | None = None

    def _get_lifespan_context(self):
        return getattr(getattr(self.app, "router", None), "lifespan_context", None)

    def _build_async_client(self, follow_redirects: bool) -> httpx.AsyncClient:
        transport = httpx.ASGITransport(
            app=self.app,
            raise_app_exceptions=self.raise_server_exceptions,
        )
        return httpx.AsyncClient(
            transport=transport,
            base_url=self.base_url,
            follow_redirects=follow_redirects,
            cookies=self.cookies,
        )

    def __enter__(self):
        if self._lifespan_enter_count == 0:
            self._loop = asyncio.new_event_loop()
            lifespan_context = self._get_lifespan_context()
            if callable(lifespan_context):
                self._lifespan_ctx = lifespan_context(self.app)
                self._loop.run_until_complete(self._lifespan_ctx.__aenter__())
            self._async_client = self._build_async_client(self.follow_redirects)
        self._lifespan_enter_count += 1
        return self

    def __exit__(self, *args: Any) -> None:
        if self._lifespan_enter_count == 0:
            return None

        self._lifespan_enter_count -= 1
        if self._lifespan_enter_count == 0 and self._loop is not None:
            try:
                async def _close() -> None:
                    if self._async_client is not None:
                        await self._async_client.aclose()
                    if self._lifespan_ctx is not None:
                        await self._lifespan_ctx.__aexit__(*args)

                self._loop.run_until_complete(_close())
            finally:
                self._lifespan_ctx = None
                self._async_client = None
                self._loop.close()
                self._loop = None
        return None

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        follow_redirects = kwargs.pop("follow_redirects", self.follow_redirects)
        kwargs.pop("allow_redirects", None)

        if self._lifespan_enter_count > 0 and self._loop is not None and self._async_client is not None:
            response = self._loop.run_until_complete(
                self._async_client.request(method, url, follow_redirects=follow_redirects, **kwargs)
            )
            self.cookies = httpx.Cookies(self._async_client.cookies)
            return response

        async def _send() -> httpx.Response:
            async with self._build_async_client(follow_redirects) as client:
                response = await client.request(method, url, **kwargs)
                self.cookies = httpx.Cookies(client.cookies)
                return response

        return asyncio.run(_send())

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def patch(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("PATCH", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("PUT", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("DELETE", url, **kwargs)

    def head(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("HEAD", url, **kwargs)


fastapi.testclient.TestClient = _ThreadlessTestClient
starlette.testclient.TestClient = _ThreadlessTestClient


def safe_cleanup_temp_dir(temp_dir, *, max_retries: int = 3, base_delay: float = 0.05) -> None:
    """Safely cleanup a TemporaryDirectory, tolerating Windows file lock errors.

    On Windows, SQLAlchemy's engine.dispose() may not immediately release SQLite
    file handles, causing TemporaryDirectory.cleanup() to raise PermissionError
    [WinError 32]. This helper retries cleanup with exponential backoff, and
    falls back to shutil.rmtree(ignore_errors=True) on persistent failure.

    Args:
        temp_dir: TemporaryDirectory instance to cleanup.
        max_retries: Maximum number of cleanup attempts before fallback.
        base_delay: Initial delay between retries in seconds (doubles each retry).
    """
    delay = base_delay
    for attempt in range(max_retries):
        try:
            temp_dir.cleanup()
            return
        except PermissionError:
            # Windows 文件句柄延迟释放，强制垃圾回收后重试
            gc.collect()
            if attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2
            else:
                # 最后一次重试失败，使用 ignore_errors 强制清理
                shutil.rmtree(temp_dir.name, ignore_errors=True)
        except Exception:
            shutil.rmtree(temp_dir.name, ignore_errors=True)
            return


# 干净基线在 conftest 导入时（即任何测试模块导入之前）捕获：collection
# 阶段测试模块在模块级写入 os.environ 的行为（如个别文件导入时
# load_dotenv 真实 .env）会在会话开始前被一次性 undo。
# 进程级默认 ENV_FILE 在基线捕获**之前**设置，使其成为基线的一部分：
# 任何测试 teardown pop 掉它之后，conftest 的逐测试快照恢复都会把它
# 带回来——后台线程（AlertWorker / task_queue worker 等，跨测试存活）
# 的任意 get_config() → setup_env() 因此永远读极简 .env，而不是仓库
# 真实 .env（跨线程污染的终级根治）。
_SESSION_ENV_DIR = tempfile.mkdtemp(prefix="dsa-session-env-")
_SESSION_ENV_FILE = Path(_SESSION_ENV_DIR) / ".env"
_SESSION_ENV_FILE.write_text("STOCK_LIST=000001.SZ\n", encoding="utf-8")
os.environ["ENV_FILE"] = str(_SESSION_ENV_FILE)
_ENV_CLEAN_BASELINE = dict(os.environ)


@pytest.fixture(scope="session", autouse=True)
def _restore_clean_env_at_session_start():
    """会话开始（首个测试前）把 os.environ 恢复到干净基线（含默认
    ENV_FILE）。

    undo collection 阶段的模块级环境写入（如个别测试文件导入时
    load_dotenv 真实 .env），保证第一个测试从干净环境出发。
    """
    os.environ.clear()
    os.environ.update(_ENV_CLEAN_BASELINE)
    yield
    os.environ.pop("ENV_FILE", None)


_LEAK_AUDIT_KEYS = (
    "LITELLM_MODEL",
    "LITELLM_FALLBACK_MODELS",
    "LLM_CHANNELS",
    "DEEPSEEK_API_KEY",
)


@pytest.fixture(autouse=True)
def _isolate_process_env():
    """每个测试恢复到"该测试开始前"的环境状态（修复跨测试污染）。

    根因（Bug-1，2026-08-29）：``ENV_FILE`` 未设置时 ``Config.setup_env()``
    会把仓库真实 ``.env`` 的键值 ``load_dotenv`` 进 ``os.environ``（只增
    不减、无人清理）。残留的 LITELLM_MODEL / LLM_CHANNELS 等真实用户配置
    会污染后续所有用 ``ENV_FILE`` + ``Config.reset_instance()`` 做隔离的
    测试——全量套件 57-81 个混沌失败的根源（受影响集合逐次运行不同，
    是不同污染路径叠加所致）。

    恢复目标是"本测试开始前的快照"而非全局基线：setUpClass 里设置、供
    整个测试类使用的环境变量（如 test_auth_api 的 ENV_FILE/DATABASE_PATH）
    必须在类内各测试之间保持；逐测试恢复只 undo 本测试自身对环境的改动。
    collection 阶段的污染由 session 级 fixture 在首个测试前统一 undo。
    """
    # A background thread can call Config.setup_env() between per-test
    # snapshots and inject the real .env into os.environ. Those user keys
    # must never be class-level fixtures; tests that need them use
    # patch.dict/monkeypatch after this fixture starts.
    for key in _LEAK_AUDIT_KEYS:
        os.environ.pop(key, None)
    snapshot = dict(os.environ)
    yield
    # 泄漏审计：测试运行期间若有真实 .env 的用户配置键渗入 os.environ
    # （后台线程 setup_env 的跨线程竞态路径），立即打印并标记，随后由
    # 下方的快照恢复 undo——失败定位信息保留在输出里。
    leaked_now = {k: os.environ[k] for k in _LEAK_AUDIT_KEYS if os.environ.get(k)}
    if leaked_now:
        import warnings

        warnings.warn(
            f"[env-leak-audit] user config leaked into os.environ during "
            f"{os.environ.get('PYTEST_CURRENT_TEST', '?')}: {leaked_now}",
            stacklevel=1,
        )
    # pytest 在 teardown 阶段管理 PYTEST_CURRENT_TEST（晚于本恢复动作
    # 删除该变量），恢复环境时必须先取出并回填，否则 pytest 的
    # os.environ.pop 会 KeyError。
    current_test = os.environ.get("PYTEST_CURRENT_TEST")
    os.environ.clear()
    os.environ.update(snapshot)
    if current_test is not None:
        os.environ["PYTEST_CURRENT_TEST"] = current_test
    # 第二层（Bug-1 后续，2026-08-30）：Config 的 bootstrap 运行时覆盖
    # 捕获是进程级一次性动作（首个 Config 初始化时按当时环境快照）。
    # 若首个初始化发生在被污染/不同 ENV_FILE 状态下，捕获结果会残留到
    # 后续所有测试。重置标志让每个测试在自身环境下重新捕获。
    try:
        from src.config import Config

        Config._BOOTSTRAP_RUNTIME_ENV_OVERRIDES_CAPTURED = False
    except Exception:  # pragma: no cover - src.config 未导入时无需处理
        pass


@pytest.fixture()
def isolated_db(monkeypatch, tmp_path: Path):
    """隔离的 PortfolioService DB（DATABASE_PATH → tmp），防触碰真实库。

    用法：测试签名加 isolated_db；配合 DatabaseManager.reset_instance()。
    """
    db_path = tmp_path / "isolated_test.db"
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("ENV_FILE", str(env_file))
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    from src.config import Config
    from src.storage import DatabaseManager

    Config.reset_instance()
    DatabaseManager.reset_instance()
    yield db_path
    DatabaseManager.reset_instance()
    Config.reset_instance()
    monkeypatch.delenv("ENV_FILE", raising=False)
    monkeypatch.delenv("DATABASE_PATH", raising=False)


@pytest.fixture(autouse=True)
def isolated_feishu_dispatch_log(tmp_path, monkeypatch):
    """全局隔离飞书出站台账（U1 message_id 留存）——任何测试的发送成功
    都不得写入真实 data/feishu_dispatch.jsonl（那是生产漏斗关联数据）。"""
    from src.notification_sender import feishu_sender
    monkeypatch.setattr(
        feishu_sender, "_dispatch_log_path",
        lambda: tmp_path / "feishu_dispatch.jsonl")
    yield

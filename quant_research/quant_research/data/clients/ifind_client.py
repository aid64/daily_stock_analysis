"""同花顺 iFinD 数据客户端（可转债 PIT 历史快照专用）

背景
----
iFinD 的 bond MCP 服务对大批量时序查询（如"单券 × 单年"的逐日数据）会
在返回的 answer 里只给部分样例数据，并附带全量 CSV 下载 URL。本客户端：

1. 用 bond_market_data 按"单券 × 单年"请求逐日收盘价 + 转股溢价率，
   截断后直接下载全量 CSV（该 URL 无需鉴权，已验证可访问）；
2. 用 bond_market_data 请求债券历史信用评级（带生效日期）与上市日期，
   从紧凑 markdown 表解析，供"按生效日期前向映射到每日"使用。

说明
----
- 本客户端不参与 OHLC 回测 feed 的 fallback 链，只服务 PIT 快照构建，
  因此不继承 AbstractDataClient，避免实现一堆用不到的 OHLC 抽象方法。
- token 从环境变量 IFIND_AUTH_TOKEN 读取，绝不写死。
- 请求并发受限流约束：免费档要求并发 <= 2/s，这里按串行 + 间隔控制。
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import time
import urllib3
from typing import Optional

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .base import DataClientError

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_BOND_SERVER = (
    "https://api-mcp.51ifind.com:8643/ds-mcp-servers/hexin-ifind-ds-bond-mcp"
)
_CSV_URL_RE = re.compile(r"https?://\S+?\.csv")
_ROW_RE = re.compile(r"^\s*\|(?P<symbol>[^|]+)\|(?P<rest>.*)\|\s*$")
_RATING_LINE_RE = re.compile(
    r"^\s*(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<agency>.+?)\s+(?P<rating>[A-Za-z][A-Za-z0-9+\-]*)\s*$"
)


def extract_csv_url(answer: str) -> Optional[str]:
    """从 answer 文本提取全量 CSV 下载 URL；无截断时为 None"""
    m = _CSV_URL_RE.search(answer or "")
    return m.group(0) if m else None


def _find_col_mapping(headers: list[str]) -> dict[str, str]:
    """按表头关键词定位列 -> 返回 {语义: 列名}"""
    mapping: dict[str, str] = {}
    for col in headers:
        c = str(col).strip()
        if not c:
            continue
        if "证券代码" in c:
            mapping["symbol"] = c
        elif "日期" in c:
            mapping["date"] = c
        elif "收盘价" in c:
            mapping["close"] = c
        elif "转股溢价率" in c:
            mapping["premium"] = c
    return mapping


def parse_daily_csv(content: bytes) -> pd.DataFrame:
    """解析下载的全量日线 CSV -> date/close/premium_rate

    CSV 为 UTF-8（带 BOM），表头形如：
        证券代码,证券简称,日期,收盘价,转股溢价率
    周末/停牌行转股溢价率为空（NaN）。

    对已退市等特殊券，iFinD 可能返回列结构不一致的表；此时跳过坏行，
    若仍缺必要列则返回空表（视为"无数据"，由调用方跳过该标的）。
    """
    try:
        df = pd.read_csv(
            io.BytesIO(content), encoding="utf-8-sig", dtype=str,
            on_bad_lines="skip",
        )
    except Exception:
        return pd.DataFrame(columns=["date", "close", "premium_rate"])
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "close", "premium_rate"])
    mapping = _find_col_mapping(list(df.columns))
    need = {"date", "close", "premium"}
    if not need.issubset(mapping):
        return pd.DataFrame(columns=["date", "close", "premium_rate"])

    out = pd.DataFrame(
        {
            "date": df[mapping["date"]],
            "close": df[mapping["close"]],
            "premium_rate": df[mapping["premium"]],
        }
    )
    out["date"] = pd.to_datetime(out["date"], errors="coerce", format="%Y%m%d")
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    out["premium_rate"] = pd.to_numeric(out["premium_rate"], errors="coerce")
    out = out.dropna(subset=["date", "close"])
    return out.sort_values("date").reset_index(drop=True)


def parse_daily_markdown(answer: str) -> pd.DataFrame:
    """解析未截断时 answer 内嵌的日线 markdown 表 -> date/close/premium_rate"""
    rows: list[dict] = []
    headers: list[str] = []
    colmap: dict[str, str] = {}
    in_table = False
    for line in (answer or "").splitlines():
        line = line.strip()
        if line.startswith("|") and ("日期" in line or "收盘价" in line):
            headers = [c.strip() for c in line.strip("|").split("|")]
            colmap = _find_col_mapping(headers)
            in_table = "date" in colmap and "close" in colmap
            continue
        if not in_table or not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != len(headers):
            continue
        row = dict(zip(headers, cells))
        d = row.get(colmap["date"], "")
        if not re.fullmatch(r"\d{8}", d):
            continue
        rows.append(
            {
                "date": pd.to_datetime(d, format="%Y%m%d", errors="coerce"),
                "close": pd.to_numeric(row.get(colmap["close"], ""), errors="coerce"),
                "premium_rate": pd.to_numeric(
                    row.get(colmap.get("premium", ""), ""), errors="coerce"
                ),
            }
        )
    if not rows:
        return pd.DataFrame(columns=["date", "close", "premium_rate"])
    out = pd.DataFrame(rows).dropna(subset=["date", "close"])
    return out.sort_values("date").reset_index(drop=True)


def parse_rating_table(answer: str, symbol: str) -> tuple[Optional[str], list[tuple[str, str]]]:
    """从 answer 解析债券历史信用评级与上市日期

    注意：列顺序不固定（实测"评级|上市日期"与"上市日期|评级"两种都存在），
    因此按表头关键字定位列位，不依赖固定位置。

    Returns:
        (list_date, [(生效日期 YYYY-MM-DD, 评级), ...])
        list_date 为 YYYY-MM-DD 字符串或 None；评级事件按生效日期升序。
    """
    list_date: Optional[str] = None
    events: list[tuple[str, str]] = []
    in_rating = False
    idx_symbol = idx_list = idx_rating = -1
    pending: list[str] = []  # 当前数据行累计（评级单元格内可能含真实换行）

    def _flush():
        nonlocal list_date, events
        if not pending:
            return
        line = "\n".join(pending)
        cells = [c.strip() for c in line.strip("|").split("|")]
        pending.clear()
        if idx_symbol < 0 or idx_list < 0 or idx_rating < 0:
            return
        if len(cells) <= max(idx_symbol, idx_list, idx_rating):
            return
        if not (cells[idx_symbol].startswith("11") or cells[idx_symbol].startswith("12")):
            return
        if symbol and cells[idx_symbol].upper() != symbol.upper():
            return
        if re.fullmatch(r"\d{8}", cells[idx_list]):
            dt = pd.to_datetime(cells[idx_list], format="%Y%m%d")
            list_date = dt.strftime("%Y-%m-%d")
        for sub in cells[idx_rating].replace("\\n", "\n").splitlines():
            m = _RATING_LINE_RE.match(sub)
            if m:
                events.append((m.group("date"), m.group("rating")))

    for line in (answer or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("|") and "债券历史信用评级" in line:
            in_rating = True
            head = [c.strip() for c in line.strip("|").split("|")]
            idx_symbol = next(
                (i for i, c in enumerate(head) if "证券代码" in c), -1
            )
            idx_list = next(
                (i for i, c in enumerate(head) if "上市日期" in c), -1
            )
            idx_rating = next(
                (i for i, c in enumerate(head) if "债券历史信用评级" in c), -1
            )
            continue
        if not in_rating:
            continue
        if line.startswith("|"):
            _flush()
            pending.append(line)
        else:
            # 评级单元格内真实换行续接（未以 | 开头的行属于上一数据行）
            if pending:
                pending.append(line)
    _flush()

    events = sorted(set(events), key=lambda x: x[0])
    return list_date, events


class IFinDClient:
    """iFinD bond 服务客户端（PIT 可转债历史数据）"""

    name = "ifind"

    def __init__(
        self,
        token: Optional[str] = None,
        rate_delay: float = 1.2,
        logger: Optional[logging.Logger] = None,
        timeout: int = 120,
    ):
        self.token = token or os.getenv("IFIND_AUTH_TOKEN")
        if not self.token or self.token.startswith("${connector."):
            raise DataClientError(
                "IFIND_AUTH_TOKEN 未配置：请通过环境变量注入 iFinD 密钥"
            )
        self.rate_delay = max(0.5, float(rate_delay))
        self.timeout = int(timeout)
        self.logger = logger or logging.getLogger(self.name)
        self._session_id: Optional[str] = None
        self._last_ts = 0.0
        self._req_id = 0

        self._http = requests.Session()
        self._http.verify = False  # 与 iFinD 官方 call.py 一致，证书链不受信
        retry = Retry(
            total=3,
            backoff_factor=1.5,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        self._http.mount("https://", HTTPAdapter(max_retries=retry))
        self._initialize()

    # ── 传输层 ──────────────────────────────────────────
    def _throttle(self) -> None:
        elapsed = time.time() - self._last_ts
        if elapsed < self.rate_delay:
            time.sleep(self.rate_delay - elapsed)
        self._last_ts = time.time()

    def _headers(self, with_session: bool = False) -> dict:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": self.token,
        }
        if with_session and self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        return h

    def _initialize(self) -> None:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "ifind-client", "version": "1.0.0"},
            },
        }
        resp = self._http.post(
            _BOND_SERVER, json=payload, headers=self._headers(), timeout=30
        )
        resp.raise_for_status()
        sid = resp.headers.get("Mcp-Session-Id")
        if not sid:
            raise DataClientError("iFinD initialize 未返回 Mcp-Session-Id")
        self._session_id = sid
        self._http.post(
            _BOND_SERVER,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers=self._headers(with_session=True),
            timeout=10,
        )

    def _call_tool(self, query: str) -> tuple[str, dict]:
        self._req_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._req_id,
            "method": "tools/call",
            "params": {"name": "bond_market_data", "arguments": {"query": query}},
        }
        resp = self._http.post(
            _BOND_SERVER,
            json=payload,
            headers=self._headers(with_session=True),
            timeout=self.timeout,
        )
        resp.raise_for_status()
        try:
            rj = resp.json()
            text = rj["result"]["content"][0]["text"]
            inner = json.loads(text)
            data = json.loads(inner["data"])
            return str(data.get("answer", "")), data.get("indicators_params", {})
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
            raise DataClientError(f"iFinD 响应解析失败: {e}") from e

    def _download_csv(self, url: str) -> bytes:
        self._throttle()
        resp = self._http.get(url, timeout=self.timeout)
        resp.raise_for_status()
        return resp.content

    # ── 业务接口 ────────────────────────────────────────
    def fetch_convertible_daily(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        """单券 [start,end] 逐日收盘价 + 转股溢价率

        优先走全量 CSV 下载；短区间未截断时回退解析 answer 内嵌表。
        返回列：date/close/premium_rate。
        """
        self._throttle()
        answer, _ = self._call_tool(
            f"查询可转债 {symbol} {start} 至 {end} 每个交易日的收盘价、转股溢价率"
        )
        url = extract_csv_url(answer)
        if url:
            content = self._download_csv(url)
            df = parse_daily_csv(content)
        else:
            df = parse_daily_markdown(answer)
        if df.empty:
            self.logger.warning(f"[ifind] {symbol} [{start}~{end}] 无数据")
        else:
            self.logger.info(f"[ifind] {symbol} [{start}~{end}]: {len(df)} 行")
        return df

    def fetch_convertible_meta(self, symbol: str) -> tuple[Optional[str], list[tuple[str, str]]]:
        """单券历史评级（带生效日期）与上市日期"""
        self._throttle()
        answer, _ = self._call_tool(
            f"查询可转债 {symbol} 的债券历史信用评级、上市日期"
        )
        list_date, events = parse_rating_table(answer, symbol)
        self.logger.info(
            f"[ifind] {symbol} meta: list_date={list_date}, "
            f"评级事件 {len(events)} 条"
        )
        return list_date, events

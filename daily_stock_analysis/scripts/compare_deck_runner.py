# -*- coding: utf-8 -*-
"""多策略对比盘确定性执行器（策略对比盘的唯一执行引擎）。

取代 ZCode 定时提示词的"AI 解释执行"：本脚本是普通 Python 程序，
由 Windows 计划任务 / main.py --schedule 触发，每次运行做且只做
模式规定的事，结果原子写台账，动作发飞书。所有决策都是确定性
函数，可单元测试、可审计、幂等。

模式：
  --mode intraday  盘中风控轮：事件套利扫描+核价差、熔断、信用、异动
  --mode close     收盘轮：净值重估 + 周一/月度调仓 + 收盘小结
  --dry-run        只计算与打印，不写台账、不发通知、不执行调仓

数据源契约：
  - 实时行情：data_provider.get_realtime_quote（六源降级+熔断，用户配置）
  - 小市值选股：tushare daily_basic/stock_basic（15000 积分接口）
  - A/B 信号：只读 quant_research/output/feeds_snapshot_v*.pkl（跨项目文件契约，不 import）
  - E 目标：子进程调 quant_research/scripts/run_paper_tracking.py --export-plan
  - 事件公告：EventArbitrageMonitorService + 东财公告正文 API
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import pandas as pd
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib import request as urlrequest

PROJECT_DIR = Path(__file__).resolve().parents[1]
LEDGER = PROJECT_DIR.parent / ".scratch" / "dsa-strategy-validation" / "compare_strategies.json"
CAPITAL = 12411.0
LEDGER_SCHEMA_VERSION = 3
_TRADING_OK = True  # 交易闸: _main_inner 按时段设置(15:10 后收盘补结算为 False); 测试/直调默认放行
_session_now: datetime | None = None  # 入口捕获的会话时间（交易所戳优先）; 慢行情后不得二次取时
EX_DATE = date.today()  # 交易所日历日（_main_inner 启动时取墙钟日期；旧行情戳只用于报价新鲜度，不倒拨运行日期）
COMM_RATE, COMM_MIN = 0.0000845, 5.0
STAMP_SELL = 0.0005  # 股票卖出印花税（ETF 免）

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("compare_deck")

sys.path.insert(0, str(PROJECT_DIR))
os.chdir(PROJECT_DIR)  # .env / data/ 均以项目目录为根


# ---------------------------------------------------------------- 基础设施
_LOCK_PATH = PROJECT_DIR / "data" / "cache" / "compare_deck.lock"


def _pid_alive(pid: int) -> bool:
    """跨平台进程探活。Windows 上 os.kill(pid, 0) 会终止进程（Win32 无信号0语义），
    必须用 ctypes OpenProcess + GetExitCodeProcess 探测；POSIX 用信号 0。"""
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False                     # 进程不存在 → 死
            try:
                code = ctypes.c_ulong()
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return code.value == STILL_ACTIVE
                return False
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return True                          # 探活机制失效 → 保守视为存活(不抢锁)
    try:
        os.kill(pid, 0)                          # POSIX: 信号0仅探活不投递
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True                              # 存在但属他人 → 存活
    except OSError:
        return False


def acquire_lock() -> bool:
    """O_EXCL 独占锁 + JSON 属主探活（四分支）:

    ① 锁不存在 → 创建并写入 {"pid","started"} → True
    ② 属主进程存活 → False（活跃长任务不得仅因运行时长被抢）
    ③ 属主进程已退出 → 判定残留, 清除后重试
    ④ 锁内容不可解析 → 保守不抢（未知格式可能是手写/他因, 需人工清理）

    旧实现按 mtime 60 分钟阈值强抢, 会误杀超过 1 小时的活跃长任务（如慢数据日）;
    属主探活替代时长阈值后, 死锁自愈仍成立(③), 且不再有 ② 的误杀。"""
    _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, json.dumps(
            {"pid": os.getpid(), "started": datetime.now().isoformat()}).encode())
        os.close(fd)
        return True
    except FileExistsError:
        try:
            owner = json.loads(_LOCK_PATH.read_text(encoding="utf-8"))
            pid = int(owner["pid"])
        except Exception:
            log.warning("锁内容不可解析, 保守不抢(需人工清理): %s", _LOCK_PATH)
            return False
        if _pid_alive(pid):
            return False
        log.warning("锁属主 pid=%s 已退出, 视为残留清除", pid)
        try:
            _LOCK_PATH.unlink()
        except FileNotFoundError:
            pass
        return acquire_lock()


def release_lock() -> None:
    try:
        _LOCK_PATH.unlink()
    except FileNotFoundError:
        pass


def load_ledger() -> dict:
    """读台账; 主文件损坏时回退 .bak(上一轮有效状态)。"""
    try:
        return json.loads(LEDGER.read_text(encoding="utf-8"))
    except Exception:
        bak = LEDGER.with_suffix(".json.bak")
        if bak.exists():
            log.warning("台账主文件损坏, 回退 .bak: %r", bak)
            return json.loads(bak.read_text(encoding="utf-8"))
        raise


def save_ledger(led: dict) -> None:
    """原子写：临时文件 + os.replace；写前滚动保留一份 .bak（台账在 .scratch，防误删）。

    主文件损坏（load_ledger 刚从 .bak 恢复过的场景）时不回滚备份：
    现有 .bak 是唯一好副本，不能被坏主文件覆盖。"""
    led.setdefault("_meta", {})["schema_version"] = LEDGER_SCHEMA_VERSION
    if not LEDGER.parent.exists():
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
    if LEDGER.exists():
        try:
            json.loads(LEDGER.read_text(encoding="utf-8"))
        except Exception:
            log.warning("台账主文件损坏, 跳过备份回滚以保留现有 .bak")
        else:
            try:
                shutil.copyfile(LEDGER, LEDGER.with_suffix(".json.bak"))
            except Exception as exc:
                log.warning("台账备份失败(不影响主写): %r", exc)
    fd, tmp = tempfile.mkstemp(dir=str(LEDGER.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(led, f, ensure_ascii=False, indent=2)
        os.replace(tmp, LEDGER)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def notify(content: str, dedup_key: str, dry: bool = False) -> bool:
    if dry:
        log.info("[dry-run] 通知抑制: %s", dedup_key)
        return False
    try:
        from src.notification import NotificationService
        return NotificationService().send(content, route_type="strategy_compare", dedup_key=dedup_key)
    except Exception as exc:  # 通知失败不阻断主流程
        log.warning("通知发送失败(%s): %r", dedup_key, exc)
        return False


def _is_cb_symbol(symbol: str) -> bool:
    """转债走腾讯直连；六源实时链对带后缀转债代码覆盖不到（文档 §3 数据源）。"""
    try:
        from src.core.paper_execution import AssetClass
        from src.services import deck_registry
        return deck_registry.classify_symbol(symbol) == AssetClass.CB
    except Exception:
        return False


def _fill_cb_prices(px: dict, symbols: list[str]) -> dict:
    missing = [s for s in symbols
               if not px.get(s, {}).get("price") and _is_cb_symbol(s)]
    if not missing:
        return px
    codes6 = []
    for s in missing:
        code = str(s).split(".")[0]
        prefix = "sh" if (str(s).upper().endswith(".SH")
                          or code.startswith(("110", "111", "113", "118"))) else "sz"
        codes6.append(prefix + code)
    quotes = tencent_quotes(codes6)
    for s in missing:
        info = quotes.get(str(s).split(".")[0])
        if info and info.get("price"):
            px[s] = {**info, "source": "tencent_cb"}
    return px


def ensure_prices(px: dict, symbols: list[str]) -> dict:
    """补齐候选标的缺失报价(新槽首建仓时 symbol 不在已持仓预热列表)——就地更新 px。

    转债（E/H）不在六源实时链覆盖范围，走腾讯直连；其余走项目实时链。
    """
    missing = [s for s in symbols if not px.get(s, {}).get("price")]
    if not missing:
        return px
    cb = [s for s in missing if _is_cb_symbol(s)]
    other = [s for s in missing if s not in cb]
    if other:
        for code, info in realtime_quotes(other).items():
            if info.get("price"):
                px[code] = info
    if cb:
        _fill_cb_prices(px, cb)
    return px


def slot_equity(slot: dict, px: dict) -> float:
    """调仓时点的当前权益（现金+持仓市值，缺价回退成本价）——定仓基数统一口径。"""
    eq = float(slot.get("cash", 0.0))
    for h in slot.get("holds", []):
        if h.get("fixed_value"):
            eq += float(h.get("notional", 0.0))
            continue
        price = (px.get(h["symbol"]) or {}).get("price") or h.get("price", 0)
        eq += float(h.get("qty", 0)) * float(price or 0)
    return eq


def _run_cmd(cmd: list[str], cwd: Path, timeout: int):
    """跨项目子进程统一 UTF-8 解码：任务环境设置 PYTHONIOENCODING=utf-8，
    父进程默认按 cp936 解码会在读取线程静默抛 UnicodeDecodeError（PR #14 同类）。"""
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def _chunks(lst: list, n: int):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def realtime_quotes(codes: list[str]) -> dict[str, dict]:
    """走项目实时行情链（六源降级+熔断）。先分批预热（tickflow 单批上限5），再并发逐只取缓存。"""
    out: dict[str, dict] = {c: {"price": 0.0, "pct": 0.0, "source": "unavailable"} for c in codes}
    try:
        from data_provider.base import DataFetcherManager
        from concurrent.futures import ThreadPoolExecutor
        mgr = DataFetcherManager()
        for chunk in _chunks(codes, 5):
            try:
                mgr.prefetch_realtime_quotes(chunk)
            except Exception as exc:
                log.warning("prefetch 失败(降级逐只) %s: %r", chunk, exc)

        def _one(code: str) -> tuple[str, dict]:
            try:
                q = mgr.get_realtime_quote(code, log_final_failure=False)
                if q is None or getattr(q, "price", None) in (None, 0):
                    return code, {"price": 0.0, "pct": 0.0, "source": "unavailable"}
                return code, {"price": float(q.price), "pct": float(q.change_pct or 0.0),
                              "source": getattr(q.source, "value", str(q.source)),
                              "ts": getattr(q, "provider_timestamp", None)}
            except Exception as exc:
                log.warning("行情失败 %s: %r", code, exc)
                return code, {"price": 0.0, "pct": 0.0, "source": "error"}

        with ThreadPoolExecutor(max_workers=4) as pool:
            for code, info in pool.map(_one, codes):
                out[code] = info
    except Exception as exc:
        log.error("实时行情链整体失败: %r", exc)
    return out


def is_trading_session(now: datetime | None = None) -> bool:
    """A股运行时段（含 15:00-15:10 收盘处理窗）。午休/开盘前/周末为 False。

    撮合窗截止 15:00: 15:00-15:10 为只结算窗（新成交闸关闭, 见 _fills_open/_session_gate）。"""
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    hm = now.hour * 100 + now.minute
    return (930 <= hm <= 1130) or (1300 <= hm <= 1510)


def _fills_open(now: datetime | None) -> bool:
    """撮合窗: 运行时段内且 ≤15:00。15:00 后连续竞价已止, 1501-1510 只结算不成交。"""
    return bool(now) and is_trading_session(now) and (now.hour * 100 + now.minute) <= 1500


_CORE_BENCHMARKS = ("C_沪深300持有", "D_红利低波持有")


def _core_missing_quotes(led: dict, px: dict) -> list[str]:
    """核心基准持仓中仍缺现价的标的；空列表表示收盘可落 marks。"""
    missing = []
    for name in _CORE_BENCHMARKS:
        for h in led.get("strategies", {}).get(name, {}).get("holds", []):
            if h.get("fixed_value"):
                continue
            if not (px.get(h["symbol"]) or {}).get("price"):
                missing.append(h["symbol"])
    return sorted(set(missing))


def _session_fills_ok() -> bool:
    """本次运行捕获的成交闸：会话闸在启动时的交易所时间 + _TRADING_OK。

    收盘轮里 C/D 行情预取可能耗时数十秒，期间二次 exchange_now() 失败会回退墙钟
    15:02 并误判"非撮合时段"；注册表和子任务必须用启动时捕获的会话时间，
    而不是再次取时。
    """
    if not _TRADING_OK:
        return False
    session_now = globals().get("_session_now")
    if session_now is None or session_now.date() != EX_DATE:
        session_now = exchange_now() or datetime.now()
    return _fills_open(session_now)


def exchange_now() -> datetime | None:
    """用交易所行情时间戳作为真实时间基准（本机时钟不可信时唯一可靠来源）。"""
    try:
        raw = urlrequest.urlopen(urlrequest.Request(
            "http://qt.gtimg.cn/q=sh512890", headers={"User-Agent": "Mozilla/5.0"}),
            timeout=8).read().decode("gbk")
        ts = raw.split('="')[1].rstrip('";').split("~")[30]
        return datetime.strptime(ts[:14], "%Y%m%d%H%M%S")
    except Exception as exc:
        log.warning("交易所时间戳获取失败, 回退本机时钟: %r", exc)
        return None


def slot_prices(led: dict, extra: list[str] | None = None) -> dict[str, dict]:
    codes: list[str] = []
    for s in led["strategies"].values():
        for h in s.get("holds", []):
            codes.append(h["symbol"])
    codes += list(extra or [])
    return _fill_cb_prices(realtime_quotes(list(dict.fromkeys(codes))), codes)


# ---------------------------------------------------------------- 交易原语
def trade_fee(notional: float, side: str, is_stock: bool) -> float:
    fee = max(notional * COMM_RATE, COMM_MIN)
    if side == "SELL" and is_stock:
        fee += notional * STAMP_SELL
    return round(fee, 2)


_OLD_LOT_DAY = date(2000, 1, 1)  # 未知购入日的旧仓批次：视为非当日买入（可卖）


def _lot_buy_day(h: dict) -> date:
    raw = h.get("held_since")
    if raw:
        try:
            return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
        except ValueError:
            pass
    return _OLD_LOT_DAY


def _apply_orders_intent(o: dict, side: str, slot_name: str | None):
    """订单 dict → Intent。资产类别解析顺序：显式 asset > 槽位注册表 > 代码前缀。"""
    from src.core.paper_execution import AssetClass, Intent
    from src.services import deck_registry
    explicit = o.get("asset")
    if explicit:
        cls = AssetClass(explicit)
    else:
        spec = deck_registry.get_slot(slot_name) if slot_name else None
        cls = spec.asset_class if spec is not None else deck_registry.classify_symbol(o["symbol"])
    return Intent(symbol=o["symbol"], side=side, qty=int(o["qty"]), asset_class=cls,
                  name=o.get("name", ""), price_hint=o.get("price"))


def apply_orders(slot: dict, sells: list[dict], buys: list[dict], px: dict,
                 slot_name: str | None = None) -> list[str]:
    """旧签名执行适配器：全部交易约束在 src/core/paper_execution 实现，此处只做形态转换。

    - 报价闸：无报价/零价 → 拒绝（不再按意图价或成本价成交）。
    - 幂等：同槽同日同标的同方向同数量只成交一次（键持久化在 slot._executed_keys）。
    - _TRADING_OK=False 时该批全部拒绝（修复"全局闸关闭仍成交"）。
    - 现金不足 → 记录拒绝（不再静默跳过）。
    返回拒绝说明列表；成交结果直接写回 slot（cash/holds/fees_paid）。"""
    from src.core.paper_execution import (
        ExecutionContext, Intent, Lot, Quote, execute_intents, merge_lots_view)

    name_by_symbol = {h["symbol"]: h.get("name", "") for h in slot.get("holds", [])}
    for o in sells + buys:
        if o.get("name"):
            name_by_symbol.setdefault(o["symbol"], o["name"])

    intents: list[Intent] = []
    rejects: list[str] = []
    for o in sells:
        try:
            intents.append(_apply_orders_intent(o, "SELL", slot_name))
        except ValueError as exc:
            rejects.append(f"SELL {o['symbol']} 无法识别代码: {exc}")
    for b in buys:
        try:
            intents.append(_apply_orders_intent(b, "BUY", slot_name))
        except ValueError as exc:
            rejects.append(f"BUY {b['symbol']} 无法识别代码: {exc}")

    lots = [Lot(h["symbol"], int(h["qty"]), float(h.get("price") or 0.0), _lot_buy_day(h))
            for h in slot.get("holds", [])]
    quotes: dict[str, Quote] = {}
    for sym in {o["symbol"] for o in sells + buys}:
        info = px.get(sym) or {}
        raw = info.get("price")
        if raw is None:
            continue                      # 无报价 → 核心判 NO_QUOTE
        try:
            quotes[sym] = Quote(symbol=sym, price=float(raw))
        except (TypeError, ValueError):
            continue                      # 非数值价 → 核心判 NO_QUOTE

    day_keys = slot.setdefault("_executed_keys", {}).setdefault(str(EX_DATE), [])
    ctx = ExecutionContext(slot=slot_name or "deck", trading_day=EX_DATE,
                           trading_ok=_TRADING_OK, executed_order_keys=frozenset(day_keys))
    result = execute_intents(float(slot.get("cash", 0.0)), lots, intents, quotes, ctx)
    for key in result.order_keys:
        if key not in day_keys:
            day_keys.append(key)

    slot["cash"] = result.cash
    view = merge_lots_view(result.lots)
    earliest: dict[str, date] = {}
    for l in result.lots:
        earliest[l.symbol] = min(earliest.get(l.symbol, l.buy_day), l.buy_day)
    holds = []
    for sym, v in sorted(view.items()):
        row = {"symbol": sym, "name": name_by_symbol.get(sym, sym),
               "qty": v["qty"], "price": v["avg_cost"],
               "notional": round(v["qty"] * v["avg_cost"], 2)}
        if earliest[sym] > _OLD_LOT_DAY:
            row["held_since"] = earliest[sym].isoformat()
        holds.append(row)
    slot["holds"] = holds
    slot["fees_paid"] = round(slot.get("fees_paid", 0.0) + result.fees_total, 2)
    rejects += [f"{r.side} {r.symbol} x{r.qty} 拒绝[{r.reason.value}] {r.detail}"
                for r in result.rejections]
    for r in rejects:
        log.warning("apply_orders: %s", r)
    return rejects


def dividend_accrued(ts_code: str, since: str, spot: float, qty: int) -> tuple[float, str]:
    """tushare fund_div 查询 since 之后的基金分红, 返回 (应计现金, 描述)。失败返回 (0.0, "")。"""
    try:
        df = ts_client().call("fund_div", ts_code=ts_code, cache_ttl_days=7)
        if df is None or df.empty:
            return 0.0, ""
        df = df[df["ex_date"].astype(str) > since]
        total, detail = 0.0, []
        for _, row in df.iterrows():
            unit = float(row.get("div_cash") or 0)  # 每份分红(元)
            if unit > 0:
                total += unit * qty
                detail.append(f"{row.get('ex_date')} 每份{unit}元")
        if total > 0:
            return round(total, 2), "; ".join(detail)
        return 0.0, ""
    except Exception as exc:
        log.warning("分红查询失败 %s: %r", ts_code, exc)
        return 0.0, ""


def repo_maturity_pass(led: dict, dry: bool) -> list[str]:
    """Y 槽逆回购到期回填: 到期日≤EX_DATE 的 fixed_value 持仓 → 转现金(本金+利息)。

    利息 = notional × 年化 × 计息天数 / 365（计息天在建仓 note 声明）。"""
    msgs = []
    slot = led["strategies"].get("Y_确定性收益")
    if not slot:
        return msgs
    remaining = []
    for h in slot.get("holds", []):
        if not h.get("fixed_value"):
            remaining.append(h)
            continue
        mature = h.get("maturity_date", "")
        if mature and mature <= EX_DATE.isoformat():
            interest = h.get("interest", 0.0)
            slot["cash"] = round(slot.get("cash", 0) + h.get("notional", 0) + interest, 2)
            if not dry:
                slot.setdefault("tuning", []).append(
                    {"date": EX_DATE.isoformat(), "change": f"逆回购到期回填 +¥{h['notional'] + interest:.2f}",
                     "why": f"本金¥{h['notional']:.0f}+利息¥{interest:.2f}(年化{h.get('rate_pct')}%)"})
            msgs.append(f"Y槽逆回购到期回填: 本金¥{h['notional']:.0f}+利息¥{interest:.2f} → 现金")
        else:
            remaining.append(h)
    slot["holds"] = remaining
    return msgs


def revalue(slot: dict, px: dict) -> float:
    """估值：缺价持仓按成本价兜底并显式标记 valuation_status。

    不能把缺价持仓直接排除出净值：一次行情源故障会把 C/D 记成「只剩现金」，
    产生 -97% 的假净值。成本价兜底与 ``slot_equity`` 同口径，仍然如实标记
    ``unvalued``；核心基准缺价时 ``run_close`` 会整轮顺延，不写 marks。

    fixed_value 行（逆回购等面值计价）按名义额计入，与执行内核口径一致。"""
    eq = float(slot.get("cash", 0.0))
    unvalued: dict[str, str] = {}
    for h in slot.get("holds", []):
        if h.get("fixed_value"):
            eq += h.get("notional", 0.0)
            continue
        p = (px.get(h["symbol"]) or {}).get("price")
        if not p:
            unvalued[h["symbol"]] = "missing"
            p = h.get("price")
        if p:
            eq += h["qty"] * float(p)
    slot["equity"] = round(eq, 2)
    slot["valuation_status"] = {"fully_valued": not unvalued, "unvalued": unvalued}
    return slot["equity"]


# ---------------------------------------------------------------- 信号计算
def _snapshot_last_date(path) -> date | None:
    """快照内所有标的的最大数据日期；损坏/为空返回 None。"""
    try:
        import pandas as pd
        data = pd.read_pickle(path)
        dates = [pd.to_datetime(df["date"]).max().date()
                 for df in data.values()
                 if df is not None and len(df) and "date" in df.columns]
        return max(dates) if dates else None
    except Exception:
        return None


def _last_completed_trading_day(until: date) -> date:
    d = until - timedelta(days=1)
    for _ in range(15):
        if is_exchange_trading_day(d):
            return d
        d -= timedelta(days=1)
    return d


def maybe_refresh_snapshot(dry: bool) -> str:
    """A/B 信号的历史快照超过 7 天未更新 **或数据末端早于上一交易日** 时重建。

    只看文件 mtime 会漏掉"刚重建但数据源仍滞后"的快照（10-19 实测末端 10-16）。
    失败时保留旧快照照常出信号（口径略陈旧，日志+消息标注）。"""
    out_dir = PROJECT_DIR.parent / "quant_research" / "output"
    snaps = sorted(out_dir.glob("feeds_snapshot_v*.pkl"), key=lambda p: p.stat().st_mtime, reverse=True)
    newest = snaps[0] if snaps else None
    last_data = _snapshot_last_date(newest) if newest else None
    last_trading = _last_completed_trading_day(EX_DATE)
    data_stale = newest is None or last_data is None or last_data < last_trading
    file_fresh = newest is not None and (datetime.now() - datetime.fromtimestamp(newest.stat().st_mtime)).days < 7
    if file_fresh and not data_stale:
        return ""
    target = newest.with_name("feeds_snapshot_v5.pkl") if newest else out_dir / "feeds_snapshot_v5.pkl"
    if dry:
        return f"[dry] 快照需重建(末端 {last_data}, 上一交易日 {last_trading})→{target.name}"
    try:
        start = (EX_DATE - __import__("datetime").timedelta(days=450)).isoformat()
        _run_cmd([sys.executable, str(PROJECT_DIR.parent / "quant_research" / "scripts" / "build_feeds_snapshot.py"),
                  "--start", start, "--output", str(target)],
                 PROJECT_DIR.parent / "quant_research", 900)
        if target.exists():
            new_last = _snapshot_last_date(target)
            log.info("快照已刷新: %s (末端 %s)", target.name, new_last)
            return f"快照已刷新({target.name}, 至 {new_last})"
        log.error("快照重建后未找到输出 %s", target)
        return "【异常】快照重建失败(信号沿用旧快照)"
    except Exception as exc:
        log.error("快照重建失败: %r", exc)
        return "【异常】快照重建失败(信号沿用旧快照)"


def _load_snapshot():
    import glob
    import pandas as pd
    snaps = sorted(glob.glob(str(PROJECT_DIR.parent / "quant_research" / "output" / "feeds_snapshot_v*.pkl")),
                   key=os.path.getmtime, reverse=True)
    if not snaps:
        return None
    return pd.read_pickle(snaps[0])


def pe_percentile(history: list, current: float, window_years: int = 10) -> float:
    """纯函数: 当前 PE 在近 N 年历史中的分位(0-100, 越低越便宜); 样本不足返回 -1。"""
    vals = [float(v) for v in history if v and float(v) > 0]
    if len(vals) < 250 or not current:
        return -1.0
    cutoff = vals[-250 * window_years:] if len(vals) > 250 * window_years else vals
    below = sum(1 for v in cutoff if v <= current)
    return round(below / len(cutoff) * 100, 1)


def erp_decision(percentile: float, state: str) -> str:
    """纯函数: PE分位+当前状态 → 动作。滞回阈值: >80 进防御, <60 退出(防震荡)。"""
    if percentile < 0:
        return "no_data"
    if state != "defensive" and percentile > 80:
        return "enter_defensive"
    if state == "defensive" and percentile < 60:
        return "exit_defensive"
    return "hold"


def pd_median(vals: list) -> float:
    vals = sorted(vals)
    n = len(vals)
    if not n:
        return 0.0
    mid = n // 2
    return vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2


def e2_gate_decision(median: float, state: str) -> str:
    """纯函数: 转债中位数+E2状态 → 动作。锚点插值阈值: ≥127(≈80%分位)减半, <118(≈65%)恢复。

    锚点(120.87→68.3% 的实测族): 122.87@68.3%, 130.05@~85%(2026-09-30), 133.77@92.4%。
    阈值属先验插值, 未回测校准——已知局限清单第 6 条。"""
    if not median:
        return "no_data"
    if state != "defensive" and median >= 127.0:
        return "enter_defensive"
    if state == "defensive" and median < 118.0:
        return "exit_defensive"
    return "hold"


def select_with_buffer(holds_syms: list, ranked_syms: list, buffer: int, slots: int):
    """纯函数: 缓冲带选股——持仓排名未跌出 buffer 位则保留, 空位按排名补新。返回 (keep, new)。"""
    rank = {s: i + 1 for i, s in enumerate(ranked_syms)}
    keep = [s for s in holds_syms if rank.get(s, 10 ** 9) <= buffer][:slots]
    new = [s for s in ranked_syms if s not in keep][:max(slots - len(keep), 0)]
    return keep, new


def _parse_engine_stdout(stdout: str) -> dict:
    """子进程 stdout 可能含 tushare login 等噪声——提取最后一个完整 JSON 对象。"""
    out = (stdout or "").strip()
    start = out.rfind('}\n{')  # JSON 主体前若有噪声行, 取最后一个 '{' 块
    if start >= 0:
        start += 1
    else:
        start = out.find("{")
    if start < 0:
        raise ValueError(f"引擎输出无 JSON: {out[:80]!r}")
    return json.loads(out[start:])


def momentum_target_from_engine(data: dict) -> dict[str, float]:
    """纯函数：引擎 JSON → 目标权重。防御态满仓 511010；否则 top_k 等权。可测。"""
    if data.get("defensive_mode"):
        return {"511010.SH": 1.0}
    ranked = [x["symbol"] for x in (data.get("ranked") or [])][: int(data.get("top_k", 3))]
    return {s: 1.0 / len(ranked) for s in ranked} if ranked else {}


def momentum_signal(led: dict, px: dict, dry: bool, fills_ok: bool | None = None) -> list[dict]:
    """A_动量：引擎口径（extract_mwu_targets.py 探针提取 ranked/defensive_mode）。

    微调保留：货币腿 511880 权重以现金持有（一手 ¥1 万+，功能等价现金）。
    """
    slot = led["strategies"]["A_动量"]
    if dry:
        return ["A_动量(dry) 引擎信号提取"]
    try:
        r = _run_cmd(
            [sys.executable, str(PROJECT_DIR.parent / "quant_research" / "scripts" / "extract_mwu_targets.py"),
             "--strategy", "etf_momentum", "--strategy-config", "config/strategy/etf_momentum.yaml",
             "--config", "config/default.yaml"],
            PROJECT_DIR.parent / "quant_research", 600)
        data = _parse_engine_stdout(r.stdout)
        target = momentum_target_from_engine(data)
        if not target:
            return ["【异常】A_动量 引擎输出为空, 持仓不变"]
        cur = {h["symbol"]: h["qty"] for h in slot.get("holds", [])}
        ensure_prices(px, sorted(set(target) | set(cur)))
        prices = {sym: (px.get(sym, {}).get("price") or 0) for sym in set(target) | set(cur)}
        missing = [s for s in target if not prices.get(s)]
        if missing:
            return [f"【异常】A_动量 目标缺价 {missing}, 持仓不变"]
        sells, buys = mwu_target_orders(target, cur, prices, capital=slot_equity(slot, px))
        buys = [b for b in buys if b["symbol"] != "511880.SH"]  # 货币腿以现金持有(微调)
        for o in sells + buys:
            o["is_stock"] = False
        changed = bool(sells or buys)
        tradable = fills_ok if fills_ok is not None else _session_fills_ok()
        now = globals().get("_session_now") or datetime.now()
        rejects: list[str] = []
        if changed and not tradable:
            log.warning("非交易时段(交易所 %s), A 槽调仓抑制为只记录", now)
        if changed and tradable:
            rejects = apply_orders(slot, sells, buys, px)
        slot["signal"] = {"as_of": data.get("as_of"), "ranked": data.get("ranked"),
                          "defensive_mode": data.get("defensive_mode"), "target": target}
        slot["note"] = f"引擎信号 {data.get('as_of')} defensive={data.get('defensive_mode')}"
        # 消息据实: 未实际成交不得声称"调仓→"（仓库硬约束）。
        if not changed:
            return [f"A_动量 目标未变(as_of {data.get('as_of')})"]
        if not tradable:
            return [f"【顺延】A_动量 目标→{sorted(target)} (非撮合时段, 未成交, 待续做)"]
        if rejects:
            return [f"A_动量 调仓→{sorted(target)} (部分未成交: {len(rejects)} 笔被拒)"]
        return [f"A_动量 调仓→{sorted(target)}"]
    except Exception as exc:
        log.error("A 动量引擎信号失败: %r", exc)
        return ["【异常】A_动量 引擎信号提取失败, 持仓不变"]


def rotation_signal(led: dict, px: dict, dry: bool, fills_ok: bool | None = None) -> list[dict]:
    """B_轮动：核心3资产25日收益选最强，全负→空仓。"""
    feeds = _load_snapshot()
    if feeds is None:
        return []
    pool = ["518880.SH", "159915.SZ", "510300.SH"]
    scores = {}
    for sym in pool:
        close = feeds[sym].sort_values("date")["close"].values
        spot = px.get(sym, {}).get("price") or float(close[-1])
        scores[sym] = spot / float(close[-26]) - 1
    best = max(scores, key=scores.get)
    slot = led["strategies"]["B_轮动"]
    target = [] if scores[best] <= 0 else [best]
    current = {h["symbol"] for h in slot.get("holds", [])}
    if set(target) == current:
        return []
    if dry:
        return [f"B_轮动(dry) 25日收益{ {k: f'{v:+.1%}' for k, v in scores.items()} }→{target or '空仓'}"]
    ensure_prices(px, sorted(set(target) | current))
    if target and not (px.get(target[0]) or {}).get("price"):
        return [f"【异常】B_轮动 目标 {target[0]} 无有效报价, 持仓不变"]
    sells = [{"symbol": h["symbol"], "qty": h["qty"], "price": px.get(h["symbol"], {}).get("price") or h["price"]}
             for h in slot.get("holds", []) if h["symbol"] not in target]
    buys = []
    if target:
        price = px.get(target[0], {}).get("price")
        qty = int(slot_equity(slot, px) * 0.98 // (price * 100)) * 100
        if qty:
            buys.append({"symbol": target[0], "qty": qty, "price": price})
    now = globals().get("_session_now") or datetime.now()
    tradable = fills_ok if fills_ok is not None else _session_fills_ok()
    rejects: list[str] = []
    if tradable:
        rejects = apply_orders(slot, sells, buys, px)
    else:
        # 此前无撮合窗判定: 内核会整体 SESSION_CLOSED 拒单，而消息仍报"调仓→"
        # → 状态未变却声称已调仓（违仓库硬约束）。
        log.warning("非交易时段(交易所 %s), B 槽调仓抑制为只记录", now)
    slot["note"] = f"信号 {datetime.now():%m-%d %H:%M} 25日{scores[best]:+.2%}→{target or '空仓'}"
    if not tradable:
        return [f"【顺延】B_轮动 目标→{target or '空仓'} (非撮合时段, 未成交, 待续做)"]
    if rejects:
        return [f"B_轮动 调仓→{target or '空仓'} (部分未成交: {len(rejects)} 笔被拒)"]
    return [f"B_轮动 调仓→{target or '空仓'}"]


def smallcap_universe(month_end: str, exclude_new: bool, top: int) -> list[dict]:
    """tushare 15000 积分接口选小市值。返回候选（circ_mv 升序）。"""
    pro = ts_client()
    db = pro.call("daily_basic", trade_date=month_end, fields="ts_code,circ_mv,close")
    sb = pro.call("stock_basic", list_status="L", fields="ts_code,name,list_date")
    if db is None or sb is None:
        raise RuntimeError("daily_basic/stock_basic 数据源不可用")
    df = db.merge(sb, on="ts_code", how="left").dropna(subset=["circ_mv", "name"])
    df = df[~df["name"].str.contains("ST", na=False)]
    df = df[~df["ts_code"].str.startswith(("8", "4", "9", "688"))]
    df = df[df["close"] > 0]
    if exclude_new:
        df["list_date"] = df["list_date"].astype(str)
        df = df[df["list_date"] <= (datetime.strptime(month_end, "%Y%m%d") - __import__("datetime").timedelta(days=365)).strftime("%Y%m%d")]
    df = df.sort_values("circ_mv").head(top * 2)
    return df.to_dict("records")


def smallcap_rebalance(led: dict, slot_name: str, slots: int, cap_per: float,
                       exclude_new: bool, px: dict, dry: bool) -> list[dict]:
    slot = led["strategies"][slot_name]
    month_end = _prev_month_end()
    recs = smallcap_universe(month_end, exclude_new, slots)
    main_board = ("000", "001", "002", "003", "600", "601", "603", "605")
    if "神帖" in slot_name:
        recs = [r for r in recs if r["ts_code"][:3] in main_board][:8]
    def tcode(ts_code: str) -> str:
        c, m = ts_code.split(".")
        return ("SH" if m == "SH" else "SZ") + c
    # 纯小市值排序(23月回测: 缓冲带月均拖累0.72pp——去缓冲)
    ensure_prices(px, [tcode(r["ts_code"]) for r in recs])
    # 12,411 资金下 20×2500 / 5×4000 结构性不可行：按槽内 equity/持仓数分配每只预算，
    # 再按 100 股整手向下取整；cap_per 仍是单票上限。
    equity = slot_equity(slot, px)
    per_budget = min(cap_per, equity / slots) if slots > 0 else 0.0
    picks_syms = []
    picks = []
    for r in recs:
        if len(picks_syms) >= slots:
            break
        sym = tcode(r["ts_code"])
        if sym in picks_syms:
            continue
        price = px.get(sym, {}).get("price")
        if not price:
            continue
        qty = int(per_budget // (price * 100)) * 100
        if qty < 100:
            continue
        picks_syms.append(sym)
        picks.append({"symbol": sym, "name": r["name"], "price": price, "qty": qty})
    sells = [{"symbol": h["symbol"], "qty": h["qty"], "price": px.get(h["symbol"], {}).get("price") or h["price"], "is_stock": True}
             for h in slot.get("holds", []) if h["symbol"] not in picks_syms]
    if dry:
        return [f"{slot_name}(dry) 目标{picks_syms}"]
    rejects = apply_orders(slot, sells, [
        {"symbol": p["symbol"], "name": p["name"], "qty": p["qty"], "price": p["price"], "is_stock": True} for p in picks], px)
    slot["universe_rule"] = slot.get("universe_rule", "") + f" | 最后选股 {month_end}"
    if rejects:
        slot["note"] = f"{EX_DATE} 调仓被拒: {rejects[0][:80]}"
        return [f"【顺延】{slot_name} 未成交: {'; '.join(rejects)[:160]}"]
    return [f"{slot_name} 月度再平衡→{[p['symbol'] for p in picks] or '空仓'}"]


def pead_rebalance(led: dict, px: dict, dry: bool) -> list[str]:
    """I_PEAD: 月度窗重取候选(最近40天预增) → 与持仓 diff。无新预告维持旧仓。"""
    slot = led["strategies"].get("I_PEAD业绩预告")
    if not slot:
        return []
    if dry:
        return ["I_PEAD(dry) 候选重取"]
    try:
        cands = pead_universe()
        if not cands:
            slot["note"] = f"{EX_DATE} 无新预增候选, 维持旧仓"
            return [f"I_PEAD 无新预增候选, 维持旧仓"]
        cand_syms = [("SH" if r["ts_code"].endswith(".SH") else "SZ") + r["ts_code"][:6] for r in cands]
        ensure_prices(px, cand_syms)
        picks = []
        for r, sym in zip(cands, cand_syms):
            p = px.get(sym, {}).get("price") or 0
            if p and p * 100 <= 2500:
                picks.append({"symbol": sym, "name": names_of(cands).get(r["ts_code"], sym), "price": p, "qty": 100})
            if len(picks) >= 5:
                break
        if not picks and not any((px.get(s) or {}).get("price") for s in cand_syms):
            slot["note"] = f"{EX_DATE} 候选无报价, 维持旧仓"
            return ["【异常】I_PEAD 候选无报价, 持仓不变"]
        # 持有期上限: PEAD漂移窗20-60日, 持有超过60个日历日的标的强制轮出(月度窗自然覆盖大半)
        stale = [h for h in slot.get("holds", [])
                 if (EX_DATE - datetime.strptime(h.get("held_since", "2026-09-28"), "%Y-%m-%d").date()).days > 60]
        sells = [{"symbol": h["symbol"], "qty": h["qty"], "price": px.get(h["symbol"], {}).get("price") or h.get("price", 0), "is_stock": True}
                 for h in slot.get("holds", []) if h["symbol"] not in {p["symbol"] for p in picks} or h in stale]
        buys = [{"symbol": p["symbol"], "name": p["name"], "qty": p["qty"], "price": p["price"], "is_stock": True}
                for p in picks if p["symbol"] not in {h["symbol"] for h in slot.get("holds", [])}]
        for h in slot.get("holds", []):
            h.setdefault("held_since", EX_DATE.isoformat())
        rejects = apply_orders(slot, sells, buys, px)
        if rejects:
            slot["note"] = f"{EX_DATE} 调仓被拒: {rejects[0][:80]}"
            return [f"【顺延】I_PEAD 未成交: {'; '.join(rejects)[:160]}"]
        slot["note"] = f"{EX_DATE} 候选{len(cands)}→持仓{len(picks)}"
        return [f"I_PEAD 调仓→{[p['symbol'] for p in picks] or '空仓'}"]
    except Exception as exc:
        log.error("I_PEAD 调仓失败: %r", exc)
        return ["【异常】I_PEAD 候选重取失败, 持仓不变"]


def reversal_rebalance(led: dict, px: dict, dry: bool) -> list[str]:
    """J_反转: 月度窗重取 21 日跌幅最深候选 → 与持仓 diff。"""
    slot = led["strategies"].get("J_短期反转")
    if not slot:
        return []
    if dry:
        return ["J_反转(dry) 候选重取"]
    try:
        cands = reversal_universe()
        if not cands:
            return ["J_反转 无候选(全市场无合格超跌), 维持旧仓"]
        cand_syms = [("SH" if r["ts_code"].endswith(".SH") else "SZ") + r["ts_code"][:6] for r in cands]
        ensure_prices(px, cand_syms)
        picks = []
        for r, sym in zip(cands, cand_syms):
            p = px.get(sym, {}).get("price") or 0
            if p and p * 100 <= 2500:
                picks.append({"symbol": sym, "name": names_of(cands).get(r["ts_code"], sym),
                              "price": p, "qty": 100, "ret21": r["ret21"]})
            if len(picks) >= 5:
                break
        if not picks and not any((px.get(s) or {}).get("price") for s in cand_syms):
            slot["note"] = f"{EX_DATE} 候选无报价, 维持旧仓"
            return ["【异常】J_反转 候选无报价, 持仓不变"]
        sells = [{"symbol": h["symbol"], "qty": h["qty"], "price": px.get(h["symbol"], {}).get("price") or h.get("price", 0), "is_stock": True}
                 for h in slot.get("holds", []) if h["symbol"] not in {p["symbol"] for p in picks}]
        buys = [{"symbol": p["symbol"], "name": p["name"], "qty": p["qty"], "price": p["price"], "is_stock": True}
                for p in picks if p["symbol"] not in {h["symbol"] for h in slot.get("holds", [])}]
        rejects = apply_orders(slot, sells, buys, px)
        if rejects:
            slot["note"] = f"{EX_DATE} 调仓被拒: {rejects[0][:80]}"
            return [f"【顺延】J_反转 未成交: {'; '.join(rejects)[:160]}"]
        slot["note"] = f"{EX_DATE} 21日跌幅最深候选: " + " ".join(f"{p['name']}{p['ret21']:+.0f}%" for p in picks[:3])
        return [f"J_反转 调仓→{[p['symbol'] for p in picks] or '空仓'}"]
    except Exception as exc:
        log.error("J_反转 调仓失败: %r", exc)
        return ["【异常】J_反转 候选重取失败, 持仓不变"]


def buywatch_universe(top: int = 5) -> list[dict]:
    """K_项目选股: 复用项目 buy_watchlist 漏斗+打分（东财快照缺列时用 tushare daily_basic 补列）。

    这是"用项目自带能力"的兑现——不做任何手搓因子。"""
    from src.services import market_scan_service as mss
    from src.services.buy_watchlist_service import build_buy_watchlist
    try:
        mss.fetch_and_store_snapshot()
    except Exception as exc:
        log.warning("东财快照刷新失败, 用现有快照+tushare补列: %r", exc)
    snap = mss.load_snapshot()
    merged = snap
    if snap is not None and snap.get("pe", pd.Series(dtype=float)).notna().sum() < 100:
        try:
            pro = ts_client()
            db = None
            for back in range(0, 8):  # 休市日回退找最近有数据的交易日
                day = (EX_DATE - timedelta(days=back)).strftime("%Y%m%d")
                db = pro.call("daily_basic", trade_date=day,
                              fields="ts_code,turnover_rate,volume_ratio,pe,circ_mv,close")
                if db is not None and len(db):
                    break
            if db is not None and len(db):
                db["code"] = db["ts_code"].apply(lambda x: ("sh" if x.endswith(".SH") else "sz") + x[:6])
                db = db.set_index("code")
                merged = snap.set_index("code").copy()
                for col, src, mult in (("turnover_rate", "turnover_rate", 1),
                                       ("volume_ratio", "volume_ratio", 1),
                                       ("pe", "pe", 1),
                                       ("float_market_cap", "circ_mv", 10000)):
                    merged[col] = db[src] * mult
                merged = merged.reset_index()
                merged["close"] = merged["close"].fillna(merged["code"].map(db["close"]))
                log.info("tushare daily_basic 补列完成(%s)", day)
        except Exception as exc:
            log.warning("tushare 补列失败: %r", exc)
            merged = snap
    if merged is None:
        # 当日快照不可用: 回落服务自带近7天回退+新鲜度审计, 不静默用旧数据
        wl = build_buy_watchlist()
        return wl.get("candidates", []) or []
    wl = build_buy_watchlist(snapshot=merged)  # 参数注入, 不再 monkeypatch 服务模块
    return wl.get("candidates", []) or []


def buywatch_rebalance(led: dict, px: dict, dry: bool) -> list[str]:
    """K_项目选股: 项目买入观察池 top5 → 与持仓 diff。"""
    slot = led["strategies"].get("K_项目选股")
    if not slot:
        return []
    if dry:
        return ["K_项目选股(dry) 候选重取"]
    try:
        cands = buywatch_universe()
        if not cands:
            slot["note"] = f"{EX_DATE} 项目漏斗无候选, 维持旧仓"
            return ["K_项目选股 无候选, 维持旧仓"]
        syms = [("SH" if str(c.get("symbol", "")).startswith("6") else "SZ") + str(c.get("symbol", ""))
                for c in cands]
        ensure_prices(px, syms)
        picks = []
        for c, sym in zip(cands, syms):
            p = px.get(sym, {}).get("price")
            if p and p * 100 <= 2500:
                picks.append({"symbol": sym, "name": c.get("name", sym), "price": p, "qty": 100,
                              "score": c.get("score")})
            if len(picks) >= 5:
                break
        quoted = any((px.get(s) or {}).get("price") for s in syms)
        if not picks:
            slot["note"] = f"{EX_DATE} 候选无报价, 持仓不变"
            return (["【异常】K_项目选股 候选无报价, 持仓不变"] if not quoted
                    else ["K_项目选股 无预算内候选, 维持旧仓"])
        sells = [{"symbol": h["symbol"], "qty": h["qty"], "price": px.get(h["symbol"], {}).get("price") or h.get("price", 0), "is_stock": True}
                 for h in slot.get("holds", []) if h["symbol"] not in {p["symbol"] for p in picks}]
        buys = [{"symbol": p["symbol"], "name": p["name"], "qty": p["qty"], "price": p["price"], "is_stock": True}
                for p in picks if p["symbol"] not in {h["symbol"] for h in slot.get("holds", [])}]
        rejects = apply_orders(slot, sells, buys, px)
        if rejects:
            slot["note"] = f"{EX_DATE} 调仓被拒: {rejects[0][:80]}"
            return [f"【顺延】K_项目选股 未成交: {'; '.join(rejects)[:160]}"]
        slot["note"] = f"{EX_DATE} " + " ".join(f"{p['name']}(分{p['score']})" for p in picks[:3])
        return [f"K_项目选股 调仓→{[p['symbol'] for p in picks]}"]
    except Exception as exc:
        log.error("K_项目选股 调仓失败: %r", exc)
        return ["【异常】K_项目选股 候选重取失败, 持仓不变"]


def names_of(cands: list[dict]) -> dict[str, str]:
    try:
        sb = ts_client().call("stock_basic", list_status="L", fields="ts_code,name", cache_ttl_days=7)
        return dict(sb.values.tolist()) if sb is not None else {}
    except Exception:
        return {}


def _prev_month_end() -> str:
    t = EX_DATE.replace(day=1) - timedelta(days=1)
    return f"{t:%Y%m%d}"


def _is_month_first_trading_day() -> bool:
    """EX_DATE 是否本月首个交易日（tushare trade_cal；失败回退 day<=5 的旧窗）。"""
    if not is_exchange_trading_day(EX_DATE):
        return False
    if getattr(_is_month_first_trading_day, "_cache", None) == f"{EX_DATE:%Y%m%d}":
        return _is_month_first_trading_day._result  # type: ignore[attr-defined]
    result = False
    try:
        import tushare as ts
        token = ""
        for line in (PROJECT_DIR / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("TUSHARE_TOKEN="):
                token = line.split("=", 1)[1].strip().strip('"').strip("'")
        cal = ts.pro_api(token).trade_cal(exchange="SSE", start_date=f"{EX_DATE:%Y%m}01",
                                          end_date=f"{EX_DATE:%Y%m%d}", fields="cal_date,is_open")
        open_days = sorted(str(x) for x in cal[cal["is_open"] == 1]["cal_date"])  # trade_cal 返回降序, 必须排序取月首
        if open_days:
            result = open_days[0] == f"{EX_DATE:%Y%m%d}"
    except Exception as exc:
        log.warning("月首交易日判定失败, 回退 day<=5 窗: %r", exc)
        result = EX_DATE.day <= 5
    _is_month_first_trading_day._cache = f"{EX_DATE:%Y%m%d}"  # type: ignore[attr-defined]
    _is_month_first_trading_day._result = result  # type: ignore[attr-defined]
    return result


def _lot_size(symbol: str) -> int:
    """ETF/股票 100 股/手；转债 10 张/手。

    511010 等债券 ETF 的最小交易单位同样是 100 份；此前按 10 份处理会与
    paper_execution 的 ETF 政策冲突，买入被 lot_size 拒绝。
    """
    try:
        from src.core.paper_execution import AssetClass
        from src.services import deck_registry
        if deck_registry.classify_symbol(symbol) == AssetClass.CB:
            return 10
    except Exception:
        pass
    return 100


def mwu_target_orders(target: dict[str, float], cur: dict[str, int], prices: dict[str, float],
                      capital: float = CAPITAL) -> tuple[list[dict], list[dict]]:
    """纯函数：MWU 权重目标 → 整手 sells/buys（转债/511010 10张/手，其余 100股）。可测。"""
    sells: list[dict] = []
    buys: list[dict] = []
    for sym, w in target.items():
        lot = _lot_size(sym)
        p = prices.get(sym) or 0
        if not p:
            continue
        want = int(float(w) * capital // (p * lot)) * lot
        have = cur.get(sym, 0)
        if want > have:
            qty = (want - have) // lot * lot   # 买入必须是整手；奇数旧仓向下取整
            if qty:
                buys.append({"symbol": sym, "qty": qty, "price": p})
        elif want < have:
            sells.append({"symbol": sym, "qty": have - want, "price": p})
    for sym, qty in cur.items():
        if sym not in target and qty > 0:
            p = prices.get(sym) or 0
            if p:
                sells.append({"symbol": sym, "qty": qty, "price": p})
    return sells, buys


def mwu_rebalance(led: dict, px: dict, dry: bool, slot_name: str = "G_MWU融合",
                  strategy: str = "etf_mwu", strategy_config: str = "config/strategy/etf_mwu.yaml",
                  fills_ok: bool | None = None) -> list[str]:
    """MWU 槽位：跑引擎提取最新目标 → 与持仓 diff 先卖后买。失败不影响收盘落账。"""
    slot = led["strategies"].get(slot_name)
    if not slot:
        return []
    if dry:
        return [f"{slot_name}(dry) 引擎目标提取"]
    try:
        r = _run_cmd(
            [sys.executable, str(PROJECT_DIR.parent / "quant_research" / "scripts" / "extract_mwu_targets.py"),
             "--strategy", strategy, "--strategy-config", strategy_config, "--config", "config/default.yaml"],
            PROJECT_DIR.parent / "quant_research", 600)
        data = _parse_engine_stdout(r.stdout)
        target = {k: float(v) for k, v in (data.get("target") or {}).items()}
        cur = {h["symbol"]: h["qty"] for h in slot.get("holds", [])}
        ensure_prices(px, sorted(set(target) | set(cur)))
        prices = {sym: (px.get(sym, {}).get("price") or 0) for sym in set(target) | set(cur)}
        missing = [s for s in target if not prices.get(s)]
        if missing:
            return [f"【异常】{slot_name} 目标缺价 {missing}, 持仓不变"]
        sells, buys = mwu_target_orders(target, cur, prices, capital=slot_equity(slot, px))
        for o in sells + buys:
            o["is_stock"] = False
        now = globals().get("_session_now") or datetime.now()
        tradable = fills_ok if fills_ok is not None else _session_fills_ok()
        rejects: list[str] = []
        if (sells or buys) and not tradable:
            log.warning("非交易时段(交易所 %s), %s 调仓抑制为只记录", now, slot_name)
        if (sells or buys) and tradable:
            rejects = apply_orders(slot, sells, buys, px)
        slot["signal"] = {"as_of": data.get("as_of"), "expert_weights": data.get("expert_weights"),
                          "target": target}
        # 消息据实: 未实际成交不得声称"调仓→"（仓库硬约束）。
        if not (sells or buys):
            return [f"{slot_name} 目标未变(as_of {data.get('as_of')})"]
        if not tradable:
            return [f"【顺延】{slot_name} 目标→{sorted(target)} (非撮合时段, 未成交, 待续做)"]
        if rejects:
            return [f"{slot_name} 调仓→{sorted(target)} (部分未成交: {len(rejects)} 笔被拒)"]
        return [f"{slot_name} 调仓→{sorted(target)}"]
    except Exception as exc:
        log.error("%s 调仓失败: %r", slot_name, exc)
        return [f"【异常】{slot_name} 引擎目标提取失败, 持仓保持不变"]


def cb_target_lots(targets: dict, px: dict, e2_defensive: bool, capital: float = CAPITAL) -> dict[str, int]:
    """纯函数: 引擎权重目标 → 整手张数(转债10张/手); e2_defensive 时目标×0.5(择时穿透月度调仓)。可测。"""
    want = {}
    factor = 0.5 if e2_defensive else 1.0
    for sym, meta in targets.items():
        price = (px.get(sym, {}) or {}).get("price") or float(meta.get("t1_close") or 0)
        if not price:
            continue
        lots = int(float(meta.get("weight", 0)) * capital * factor // (price * 10)) * 10
        if lots > 0:
            want[sym] = lots
    return want


def cb_rebalance(led: dict, px: dict, dry: bool) -> list[dict]:
    """E 月度调仓：重取引擎目标 → 按 weight 换算整手张数 → 与持仓 diff 先卖后买。"""
    ctrl = led.setdefault("_control", {})
    if dry:
        return ["E_可转债双低(dry) 月度重取目标"]
    try:
        r = _run_cmd([sys.executable, str(PROJECT_DIR.parent / "quant_research" / "scripts" / "run_paper_tracking.py"),
                      "--export-plan", "--once", "--capital", str(int(CAPITAL))],
                     PROJECT_DIR.parent / "quant_research", 300)
        if r.returncode != 0:
            # 导出失败必须显式失败: 继续读旧 current_plan.json 会执行过期目标
            log.error("E 月度调仓: quant 导出失败(exit=%s) %s", r.returncode, (r.stderr or "")[-300:])
            return [f"【异常】E 月度调仓: quant 导出失败(exit={r.returncode}), 持仓不变"]
        plan = json.loads((PROJECT_DIR.parent / "quant_research" / "output" / "paper_tracking" / "current_plan.json").read_text(encoding="utf-8"))
        targets = {k: v for k, v in (plan.get("targets") or {}).items()
                   if isinstance(v, dict) and v.get("signal_state") == "executable"}
        slot = led["strategies"]["E_可转债双低"]
        cur = {h["symbol"]: h["qty"] for h in slot.get("holds", [])}
        ensure_prices(px, sorted(targets))
        missing = [s for s in targets if not (px.get(s) or {}).get("price")]
        if missing:
            return [f"【异常】E 月度调仓: 目标缺价 {missing}, 持仓不变"]
        # 目标资金按当前 equity(现金+持仓市值), 不再用常数 CAPITAL——
        # 盈亏后仍按初始本金配比会系统性偏离目标权重
        want = cb_target_lots(targets, px, e2_defensive=(ctrl.get("e2_state") == "defensive"),
                              capital=max(slot_equity(slot, px), 1.0))
        price_of = {sym: px.get(sym, {}).get("price") for sym in want}
        sells = [{"symbol": h["symbol"], "qty": h["qty"] - want.get(h["symbol"], 0),
                  "price": px.get(h["symbol"], {}).get("price") or h.get("price", 0)}
                 for h in slot.get("holds", []) if h["qty"] > want.get(h["symbol"], 0)]
        sells = [s for s in sells if s["qty"] > 0]
        buys = [{"symbol": sym, "qty": lots - cur.get(sym, 0), "price": price_of[sym]}
                for sym, lots in want.items() if lots > cur.get(sym, 0)]
        rejects = apply_orders(slot, sells, buys, px)
        if rejects:
            slot["note"] = f"月度调仓 {EX_DATE} 被拒: {rejects[0][:80]}"
            return [f"【顺延】E_可转债双低 未成交: {'; '.join(rejects)[:160]}"]
        slot["note"] = f"月度调仓 {EX_DATE} 目标{len(want)}只 executable"
        return [f"E_可转债双低 月度调仓→{sorted(want)}"]
    except Exception as exc:
        log.error("E 月度调仓失败: %r", exc)
        return [f"【异常】E 月度调仓失败: {exc!r:.120}"]


# ---------------------------------------------------------------- 事件套利
OFFER_API = "https://np-cnotice-stock.eastmoney.com/api/content/ann?art_code={code}&client_source=web&page_index={page}"
ANN_LIST_API = "https://np-anotice-stock.eastmoney.com/api/security/ann?sr=-1&page_size={size}&page_index=1&ann_type=A&stock_list={code}"
WATCH_TRIGGER = ("现金选择权", "要约收购", "吸收合并", "主动终止上市", "终止上市", "退市方案", "清算", "重组方案")
PRICE_PATTERNS = (r"要约价格(?:为)?\s*([\d.]+)\s*元",
                  r"现金选择权(?:价格)?(?:为)?\s*([\d.]+)\s*元",
                  r"换股吸收合并[^。]{0,40}([\d.]+)\s*元/股")


def classify_tender_offer(text: str) -> str:
    """纯函数：公告正文 → 要约类型（partial/full/unknown）。

    部分要约按预定比例收购，剩余持仓承担价格风险——不是确定性价差，
    一律转人工评估，不自动建仓。"""
    if "部分要约" in (text or ""):
        return "partial"
    if "全面要约" in (text or ""):
        return "full"
    return "unknown"


def parse_price_from_text(text: str) -> float | None:
    """从公告正文提取对价（要约价/现金选择权/吸收合并换股价），纯函数可测。"""
    for pat in PRICE_PATTERNS:
        m = re.search(pat, text or "")
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                continue
    return None


def _fetch_content(art_code: str, pages: int = 7) -> str:
    full = ""
    for p in range(1, pages + 1):
        try:
            d = json.loads(urlrequest.urlopen(urlrequest.Request(
                OFFER_API.format(code=art_code, page=p),
                headers={"User-Agent": "Mozilla/5.0"}), timeout=15).read())
        except Exception as exc:
            log.warning("公告正文页失败(%s p%s): %r", art_code, p, exc)
            break
        full += d.get("data", {}).get("notice_content") or ""
    return full


def fetch_offer_price(art_code: str) -> float | None:
    return parse_price_from_text(_fetch_content(art_code))


def fetch_recent_announcements(code: str, size: int = 8) -> list[dict]:
    d = json.loads(urlrequest.urlopen(urlrequest.Request(
        ANN_LIST_API.format(code=code, size=size), headers={"User-Agent": "Mozilla/5.0"}), timeout=15).read())
    return [{"art_code": str(it.get("art_code", "")), "title": str(it.get("title", "")),
             "date": str(it.get("notice_date", ""))[:10]}
            for it in ((d.get("data", {}) or {}).get("list") or [])]


def event_round(led: dict, dry: bool) -> list[dict]:
    """事件扫描 + 价差核验 + X 槽建仓/否决。"""
    try:
        import akshare as ak
        import datetime as _dt
        today = EX_DATE
        keywords = ("要约收购", "现金选择权", "私有化", "主动退市", "终止上市", "吸收合并")
        noise = ("风险提示", "可能因", "配售", "全资子公司", "完成吸收合并")  # 内部合并/提示类无套利意义
        new_events = []
        seen_titles = {str(v.get("title") or v.get("event") or "")
                       for v in (led["strategies"].get("X_事件套利", {}).get("veto_log", []))}
        seen_titles |= {str(v.get("title") or "") for v in (led["strategies"].get("X_事件套利", {}).get("watchlist", []))}
        fetch_failures = 0
        for d in (today, today - _dt.timedelta(days=1)):
            try:
                df = ak.stock_notice_report(symbol="全部", date=d.strftime("%Y%m%d"))
            except Exception:
                fetch_failures += 1
                continue
            if df is None:
                fetch_failures += 1
                continue
            for _, row in df.iterrows():
                title = str(row.get("公告标题", ""))
                if any(k in title for k in keywords) and not any(n in title for n in noise) and title not in seen_titles:
                    new_events.append({"date": d.isoformat(), "code": str(row.get("代码", "")),
                                       "name": str(row.get("名称", "")), "title": title,
                                       "url": str(row.get("网址", ""))})
        msgs = []
        if fetch_failures:
            msgs.append(f"【异常】事件扫描数据源失败/不完整({fetch_failures}/2), 待下轮重试")
        slot = led["strategies"]["X_事件套利"]
        px = realtime_quotes([e["code"] for e in new_events]) if new_events else {}
        for ev in new_events:
            art = (re.search(r"/([A-Z0-9]+)\.html", ev["url"]) or [None, ""])[1]
            offer = fetch_offer_price(art) if ("要约收购" in ev["title"] or "吸收合并" in ev["title"]) else None
            spot = px.get(ev["code"], {}).get("price") or 0.0
            offer_type = classify_tender_offer(_fetch_content(art, pages=4)) if art else "unknown"
            record = {**ev, "offer_price": offer, "spot": spot, "offer_type": offer_type}
            if offer_type != "full":
                record["decision"] = f"HOLD({offer_type}要约需人工评估收购比例)"
                if not dry:
                    msgs.append(f"X事件 HOLD {ev['name']}: {offer_type}要约非确定性价差, 转人工")
            elif offer and spot and spot < offer * (0.98 if "ST" not in ev["name"] else 0.95):
                qty = int(min(1241.0, slot.get("cash", 0)) // (spot * 100)) * 100
                if qty and not dry:
                    apply_orders(slot, [], [{"symbol": ev["code"], "name": ev["name"], "qty": qty,
                                             "price": spot, "is_stock": True}], px)
                    msgs.append(f"X事件套利 买入 {ev['name']}({ev['code']}) {qty}股@{spot} 要约价{offer}")
                record["decision"] = "BUY"
            else:
                record["decision"] = "VETO" + ("" if not offer else f"(溢价{spot/offer-1:+.1%})")
                msgs.append(f"X事件否决 {ev['name']}: 要约价{offer} 现价{spot} {record['decision']}")
            slot.setdefault("veto_log", []).append(record)
        return [] if dry else msgs
    except Exception as exc:
        log.error("事件轮失败: %r", exc)
        return [] if dry else [f"【异常】事件扫描失败: {exc!r:.80}"]


def watchlist_round(led: dict, dry: bool) -> list[dict]:
    """X 观察名单跟踪：每交易日核查候选事件公司的新公告 → 提取对价 → 按 rule 核价差。"""
    slot = led["strategies"].get("X_事件套利", {})
    msgs: list[dict] = []
    failures = 0
    try:
        for item in slot.get("watchlist", []):
            code = str(item.get("code", ""))
            if not code or item.get("last_checked") == EX_DATE.isoformat():
                continue
            try:
                anns = fetch_recent_announcements(code)
            except Exception as exc:
                log.warning("观察名单公告获取失败 %s: %r", code, exc)
                failures += 1
                continue
            item["last_checked"] = EX_DATE.isoformat()
            seen = set(item.get("seen_titles", []))
            for a in anns:
                title = a["title"]
                if a["art_code"] in seen or title in seen or not title:
                    continue
                if not any(k in title for k in WATCH_TRIGGER):
                    continue
                seen.add(a["art_code"]); seen.add(title)
                spot = (realtime_quotes([code]).get(code, {}) or {}).get("price") or 0.0
                full_text = _fetch_content(a["art_code"], pages=4)
                price = parse_price_from_text(full_text)
                name = str(item.get("name", code))
                is_st = "ST" in name
                otype = classify_tender_offer(full_text) if full_text else "unknown"
                record = {"date": EX_DATE.isoformat(), "code": code, "name": name,
                          "title": title[:60], "offer_price": price, "spot": spot, "offer_type": otype}
                if otype != "full":
                    record["decision"] = f"HOLD({otype}要约需人工评估)"
                elif price and spot and spot < price * (0.95 if is_st else 0.98):
                    qty = int(min(1241.0, slot.get("cash", 0)) // (spot * 100)) * 100
                    if qty and not dry:
                        apply_orders(slot, [], [{"symbol": code, "name": name, "qty": qty,
                                                 "price": spot, "is_stock": True}], {})
                        msgs.append(f"X事件套利 买入 {name}({code}) {qty}股@{spot} 对价{price} ({title[:24]})")
                    record["decision"] = "BUY"
                else:
                    record["decision"] = "HOLD" + (f"(对价{price}/现价{spot})" if price else "(对价未披露)")
                    if not dry:
                        msgs.append(f"X观察 {name}: {title[:36]} → {record['decision']}")
                slot.setdefault("veto_log", []).append(record)
            item["seen_titles"] = sorted(seen)[-20:]
        if failures:
            msgs.append(f"【异常】观察名单公告获取失败 {failures} 只, 待下轮重试")
    except Exception as exc:
        log.error("观察名单轮失败: %r", exc)
        if not dry:
            msgs.append(f"【异常】观察名单轮失败: {exc!r:.80}")
    return [] if dry else msgs


# ---------------------------------------------------------------- 风控
CREDIT_WORDS = ("下调", "回售", "违约", "兑付", "本息", "评级", "质押冻结", "逾期")


def tencent_quotes(codes6: list[str]) -> dict[str, dict]:
    """转债/指数等六源链覆盖不到的品种：腾讯直连（6位裸码）。返回 price/pct/name。"""
    out: dict[str, dict] = {}
    try:
        raw = urlrequest.urlopen(urlrequest.Request(
            "http://qt.gtimg.cn/q=" + ",".join(codes6), headers={"User-Agent": "Mozilla/5.0"}),
            timeout=10).read().decode("gbk")
        for line in raw.strip().split("\n"):
            if "=" not in line:
                continue
            f = line.split('="', 1)[1].rstrip('";').split("~")
            if f[3] and float(f[3]) > 0:
                out[f[2]] = {"price": float(f[3]), "pct": float(f[32]) if f[32] else 0.0,
                             "name": f[1]}
    except Exception as exc:
        log.warning("腾讯直连行情失败: %r", exc)
    return out


def risk_round(led: dict, px: dict, dry: bool) -> list[dict]:
    """三层风控 + 分级熔断（三年数据校准 2026-09-30，见台账 crash_study）。

    触发信号(任一): 微盘中位数≤-4 / 国证2000日跌≤-3 / 国证2000峰值回撤≤-5。
    分级(normal→watch→defensive): 首触减1/3+停新买入; 30日历日内再触清仓;
    恢复: 月度窗F重建持仓时自动回normal。数据教训: 单次触发后20日平均+3.77%
    (16次里13次反弹)——清仓清在反弹前, 连续触发才是系统性危机特征。"""
    msgs = []
    ctrl = led.setdefault("_control", {})
    crash = ctrl.get("crash_state", "normal")
    micro = sorted(px[h["symbol"]].get("pct", 0) for s in ("F_小市值20", "F2_小市值5只神帖对照")
                   for h in led["strategies"][s].get("holds", [])
                   if px.get(h["symbol"], {}).get("pct") is not None)
    med = micro[len(micro)//2] if micro else 0.0
    idx = tencent_quotes(["sz399303"]).get("399303", {})
    idx_pct = idx.get("pct", 0.0)
    limit_dn = 0
    try:
        lld = ts_client().call("limit_list_d", trade_date=EX_DATE.strftime("%Y%m%d"), limit_type="D")
        limit_dn = len(lld) if lld is not None else 0
    except Exception as exc:
        log.warning("跌停家数获取失败: %r", exc)
    triggered = (micro and med <= -4.0) or idx_pct <= -3.0 or limit_dn >= 50
    # 熔断动作/状态迁移只在可交易时段生效（仿 e2_gate）：15:10 后 catch-up 窗口 _TRADING_OK=False，
    # 卖单会被内核拒绝；若状态照迁移将出现"已减仓/已清仓"假记录与失实消息。非交易时段只记录。
    now = exchange_now()
    tradable = bool(now) and _fills_open(now)
    if triggered:
        cause = ("跌停家数 " + str(limit_dn)) if limit_dn >= 50 else (f"微盘中位数 {med}%" if med <= -4.0 else f"国证2000 {idx_pct}%")
        trigger_key = EX_DATE.isoformat()  # 观察期"再触"按日判定: 同日行情微变（med/idx 抖动）不构成新事件
        if crash == "normal":
            if not dry and not tradable:
                log.warning("非交易时段, 熔断一级迁移抑制为只记录: %s", cause)
            else:
                reduced_any = True          # dry 模拟: 假定动作执行, 保持原语义
                pending: list[str] = []
                if not dry:
                    reduced_any = False
                    for s in ("F_小市值20", "F2_小市值5只神帖对照"):
                        slot = led["strategies"][s]
                        before = sum(h["qty"] for h in slot.get("holds", []))
                        sells = [{"symbol": h["symbol"], "qty": h["qty"] // 3,
                                  "price": px.get(h["symbol"], {}).get("price") or h.get("price", 0),
                                  "is_stock": True}
                                 for h in slot.get("holds", []) if h["qty"] // 3 > 0]
                        apply_orders(slot, sells, [], px)
                        after = sum(h["qty"] for h in slot.get("holds", []))
                        if after < before:
                            reduced_any = True
                        elif before > 0:
                            pending.append(s)   # 有仓但卖单全被拒(如T+1) → 不得声称已减仓
                if reduced_any:
                    crash = "watch"
                    ctrl["crash_state"] = "watch"
                    ctrl["crash_watch_until"] = (EX_DATE + timedelta(days=30)).isoformat()
                    ctrl["crash_cause"] = cause
                    ctrl["crash_trigger_key"] = trigger_key
                    msgs.append(f"【熔断一级】{cause} → F/F2 减1/3"
                                + (f"(未成交: {','.join(pending)})" if pending else "")
                                + ", 进入观察期(30日历日内再触即清仓)")
                else:
                    msgs.append(f"【熔断一级】{cause} → F/F2 减1/3 卖单未成交, 保持原状态待下轮重试")
        elif crash == "watch":
            until = ctrl.get("crash_watch_until", "")
            new_event = trigger_key != ctrl.get("crash_trigger_key")
            if until and EX_DATE.isoformat() <= until and new_event:
                if not dry and not tradable:
                    log.warning("非交易时段, 熔断二级迁移抑制为只记录: %s", cause)
                else:
                    cleared = True          # dry 模拟: 假定动作执行
                    pending_clear: list[str] = []
                    if not dry:
                        for s in ("F_小市值20", "F2_小市值5只神帖对照"):
                            slot = led["strategies"][s]
                            before = sum(h["qty"] for h in slot.get("holds", []))
                            sells = [{"symbol": h["symbol"], "qty": h["qty"],
                                      "price": px.get(h["symbol"], {}).get("price") or h.get("price", 0),
                                      "is_stock": True}
                                     for h in slot.get("holds", [])]
                            apply_orders(slot, sells, [], px)
                            after = sum(h["qty"] for h in slot.get("holds", []))
                            if before > 0 and after >= before:
                                cleared = False
                                pending_clear.append(s)   # 清仓卖单全被拒 → 不得声称已清仓
                    if cleared:
                        crash = "defensive"
                        ctrl["crash_state"] = "defensive"
                        msgs.append(f"【熔断二级】观察期内再次触发({cause}) → F/F2 全部清仓")
                    else:
                        msgs.append(f"【熔断二级】观察期内再次触发({cause}) → F/F2 清仓未成交"
                                    f"({','.join(pending_clear)}), 保持观察待下轮重试")
            elif new_event:
                if not dry and not tradable:
                    log.warning("非交易时段, 熔断观察窗重设抑制为只记录: %s", cause)
                else:
                    ctrl["crash_watch_until"] = (EX_DATE + timedelta(days=30)).isoformat()
                    ctrl["crash_trigger_key"] = trigger_key
                    ctrl["crash_cause"] = cause
        # defensive 状态: 持仓已空, 无需动作
    # E 信用监测
    prices = tencent_quotes(["sh113062", "sh113631", "sh113691", "sh118031"])
    lowest = min((v["price"] for v in prices.values() if v), default=0)
    ew = led["strategies"]["E_可转债双低"].setdefault("credit_watch", {})
    if lowest > 0:
        ew.update({"min_price": lowest, "bonds": {k: v["price"] for k, v in prices.items()},
                   "checked": EX_DATE.isoformat()})
    ew.setdefault("seen_alerts", [])
    if 0 < lowest < 100 and not dry:
        if not ew.get("credit_half_active"):
            if not tradable:
                log.warning("非交易时段, E 信用减半抑制为只记录")
            else:
                slot = led["strategies"]["E_可转债双低"]
                before = sum(h["qty"] for h in slot.get("holds", []))
                sells = [{"symbol": h["symbol"], "qty": h["qty"] // 2,
                          "price": prices.get(h["symbol"].split(".")[0], {}).get("price") or h["price"]}
                         for h in slot.get("holds", [])]
                apply_orders(slot, sells, [], px)
                if before <= 0 or sum(h["qty"] for h in slot.get("holds", [])) < before:
                    ew["credit_half_active"] = True
                    msgs.append(f"【信用告警】转债最低 {lowest} → E 减半仓")
                else:
                    msgs.append(f"【信用告警】转债最低 {lowest} → E 减半卖单未成交, 待下轮重试")
        # 已减半则同一阈值事件不重复减半; 价格回升≥100 后重新武装
    elif lowest >= 100:
        ew["credit_half_active"] = False
    for code6 in ("113062", "113631", "113691", "118031"):
        try:
            anns = fetch_recent_announcements(code6, size=3)
        except Exception as exc:
            log.warning("转债信用公告获取失败 %s: %r", code6, exc)
            continue
        for a in anns:
            title = a["title"]
            if a["art_code"] in ew["seen_alerts"] or not title:
                continue
            if any(w in title for w in CREDIT_WORDS):
                ew["seen_alerts"].append(a["art_code"])
                msgs.append(f"【信用事件】{code6} {title[:44]}")
                if not dry:
                    ew["credit_flag"] = title[:60]
    ew["seen_alerts"] = ew["seen_alerts"][-30:]
    for s, th in (("F_小市值20", 5), ("F2_小市值5只神帖对照", 5), ("E_可转债双低", 3)):
        for h in led["strategies"][s].get("holds", []):
            pct = px.get(h["symbol"], {}).get("pct")
            if pct is not None and abs(pct) >= th:
                msgs.append(f"异动 {h.get('name', h['symbol'])} {pct:+.1f}%")
    return [] if dry else msgs

def slot_drawdown(marks: list, slot_name: str, current_eq: float) -> float:
    """槽位当前净值相对历史峰值的回撤(%), 无历史返回 0。"""
    peak = current_eq
    for m in marks:
        v = (m.get("equity") or {}).get(slot_name)
        if v and v > peak:
            peak = v
    return round((1 - current_eq / peak) * 100, 2) if peak > 0 else 0.0


def _is_month_last_trading_day() -> bool:
    """EX_DATE 是否本月最后一个交易日。

    逐日询问 EX_DATE 之后的当月日期（is_exchange_trading_day 自带按日缓存与保守回落），
    任一为交易日即非月末。旧实现 trade_cal 查询区间终点=EX_DATE 自身，
    月初/月中交易日会被误判为月末（查询终点即自身的自证缺陷）。
    """
    if not is_exchange_trading_day(EX_DATE):
        return False
    day_key = f"{EX_DATE:%Y%m%d}"
    if getattr(_is_month_last_trading_day, "_cache", None) == day_key:
        return _is_month_last_trading_day._result
    result = True
    import calendar
    for day in range(EX_DATE.day + 1, calendar.monthrange(EX_DATE.year, EX_DATE.month)[1] + 1):
        if is_exchange_trading_day(EX_DATE.replace(day=day)):
            result = False
            break
    _is_month_last_trading_day._cache = day_key
    _is_month_last_trading_day._result = result
    return result


def monthly_report(led: dict, dry: bool) -> str:
    """月度报告: 各槽本月收益/最大回撤/累计费用 + 预注册假设状态。"""
    marks = led.get("marks", [])
    month_key = f"{EX_DATE:%Y-%m}"
    month_marks = [m for m in marks if (m.get("date") or "").startswith(month_key)]
    if not month_marks:
        return ""
    lines = [f"【月度报告 {month_key}】"]
    for name, s in led.get("strategies", {}).items():
        start_eq = month_marks[0].get("equity", {}).get(name)
        end_eq = s.get("equity", 0)
        if not start_eq:
            lines.append(f"{name}: 本月无起点净值(月中建仓?) 现值{end_eq:,.0f}")
            continue
        r = (end_eq / start_eq - 1) * 100
        dd = slot_drawdown(marks, name, end_eq)
        fees = s.get("fees_paid", 0.0)
        pr = (led.get("prereg", {}).get(name) or {})
        pred = str(pr.get("prediction", pr.get("hypothesis", "")))[:36]
        lines.append(f"{name}: 本月{r:+.2f}% 回撤{dd:.2f}% 费用{fees:,.0f} | {pred}")
    meta = led.get("prereg", {}).get("_meta", {})
    if meta.get("review"):
        lines.append(f"预注册复核: {meta['review']}")
    idle = sum(s.get("cash", 0) for s in led.get("strategies", {}).values())
    lines.append(f"闲置现金合计 ¥{idle:,.0f} (真钱阶段: 月末/节前逆回购化)")
    if EX_DATE.month == 12:
        lines.append("⚠ 预注册硬复核临近(12-31): 18 张卡逐一对照晋级/死亡标准")
    return chr(10).join(lines)

_CAL_CACHE: dict = {}


def is_exchange_trading_day(d: date) -> bool:
    """tushare trade_cal 交易日校验；接口失败时保守视为交易日（close_done 幂等兜底）。

    周末直接 False（确定性判定, 不依赖 API 可用性: 修复两套日历函数周末降级行为不一致）。"""
    if d.weekday() >= 5:
        return False
    if _CAL_CACHE.get(d.isoformat()) is not None:
        return _CAL_CACHE[d.isoformat()]
    result = True
    try:
        import tushare as ts
        token = ""
        for line in (PROJECT_DIR / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("TUSHARE_TOKEN="):
                token = line.split("=", 1)[1].strip().strip('"').strip("'")
        cal = ts.pro_api(token).trade_cal(exchange="SSE", start_date=d.strftime("%Y%m%d"),
                                          end_date=d.strftime("%Y%m%d"), fields="is_open")
        result = str(cal["is_open"].iloc[0]) == "1"
    except Exception as exc:
        log.warning("交易日历获取失败(%s), 保守视为交易日: %r", d, exc)
    _CAL_CACHE[d.isoformat()] = result
    return result


def run_intraday(dry: bool) -> str:
    led = load_ledger()
    if not is_exchange_trading_day(EX_DATE):
        return f"休市(交易所日历 {EX_DATE})，跳过"
    px = slot_prices(led)
    # 事件全量扫描节流：每半天最多一次（公告流一次 ~800 行，逐轮扫既慢又浪费）
    ctrl = led.setdefault("_control", {})
    session_now = globals().get("_session_now") or datetime.now()
    halfday = f"{EX_DATE:%Y%m%d}_{ 'AM' if session_now.hour < 12 else 'PM'}"
    scan = ctrl.get("event_scan_last") != halfday
    scan_msgs = ((event_round(led, dry) + watchlist_round(led, dry)) if scan else [])
    msgs = scan_msgs + risk_round(led, px, dry)
    # 扫描失败不写节流键: 下一 20 分钟轮重试（未完成不得标记完成）
    if scan and not dry and not any(
            isinstance(m, str) and m.startswith("【异常】") for m in scan_msgs):
        ctrl["event_scan_last"] = halfday
    if (msgs or scan) and not dry:
        today = EX_DATE.isoformat()
        acts = led.setdefault("_actions", {}).setdefault(today, [])
        led["_actions"][today] = sorted(set(acts) | {m for m in msgs if "买入" in m or "卖出" in m or "清仓" in m})
        save_ledger(led)
        for i, m in enumerate(set(msgs)):
            notify(m, dedup_key=f"deck_intraday_{EX_DATE:%Y%m%d}_{i}")
    out = "盘中轮: " + ("; ".join(sorted(set(msgs))) if msgs else "无触发")
    # 收盘自动触发：交易所时间(仅当日有效)或墙钟 ≥15:00 且当日收盘轮未跑 → 补跑（幂等）。
    # 行情源故障时 exchange_now() 为空/旧戳不得导致"进来了却结不了算且无告警"——回退墙钟。
    ex_now = exchange_now()
    session_now = ex_now if (ex_now and ex_now.date() == EX_DATE) else datetime.now()
    if session_now.hour * 100 + session_now.minute >= 1500 and is_exchange_trading_day(EX_DATE):
        done_key = EX_DATE.isoformat()
        if ctrl.get("close_done") == done_key:
            out += " | 收盘轮当日已执行, 跳过"
        else:
            close_out = run_close(dry)
            out += " | " + close_out
            # 核心基准缺价的【异常】收盘不得写 close_done：后续行情恢复的轮次
            # 还要补写当日 marks。
            if not dry and not close_out.startswith("【异常】"):
                led2 = load_ledger()
                led2.setdefault("_control", {})["close_done"] = done_key
                save_ledger(led2)
    return out


def e2_gate(led: dict, px: dict, dry: bool) -> list[str]:
    """E2 转债估值择时：全市场转债中位数自峰值回撤 → E 槽减半/恢复（峰值回撤响应机制）。

    95 周回测：水平门(127/118)所有阈值均跑输满仓且回撤相同——高位恒定半仓是系统性拖累。
    峰值回撤响应仅在真崩(自峰值跌≥5%)时减半, 恢复(收窄至2%内)加回——与分级熔断设计语言一致。
    数据: tushare cb_daily, 只取最新交易日全市场截面中位数（不混合多日样本）,
    逐日积累进 credit_watch.median_history; 缺数据保持仓位（median=0 不得当作 -100% 回撤）。
    恢复满仓=按减半前登记数量买回（登记持久于 _control，缺登记则按现有持仓×2）；
    减半/恢复均按 apply_orders 实际结果转状态与报消息，卖买被拒时保持原状态待下轮重试。"""
    ctrl = led.setdefault("_control", {})
    msgs: list[str] = []
    try:
        df = ts_client().call("cb_daily", start_date=(EX_DATE - timedelta(days=7)).strftime("%Y%m%d"),
                              end_date=EX_DATE.strftime("%Y%m%d"), fields="trade_date,close")
        if df is not None and not df.empty and "close" in df.columns:
            df = df.dropna(subset=["close"]).sort_values("trade_date")
            latest = str(df["trade_date"].iloc[-1])
            prices = [float(x) for x in df[df["trade_date"] == latest]["close"] if x and float(x) > 0]
        else:
            prices = []
    except Exception as exc:
        log.warning("E2 数据失败: %r", exc)
        prices = []
    ew = led["strategies"]["E_可转债双低"].setdefault("credit_watch", {})
    hist = ew.setdefault("median_history", [])
    state = ctrl.get("e2_state", "normal")
    if not prices:  # 缺数据必须保持仓位: 不得把 median=0 当作自峰值 -100% 回撤
        ew["e2"] = {"median": 0.0, "peak": round(max((h["median"] for h in hist), default=0.0), 2),
                    "dd_from_peak": None, "state": state, "action": "no_data"}
        return msgs
    median = round(pd_median(prices), 2)
    if median:
        hist.append({"date": EX_DATE.isoformat(), "median": median})
        ew["median_history"] = hist[-400:]
    # 峰值回撤: 中位数序列内的历史峰值 → 当前回撤
    peak = max((h["median"] for h in hist), default=median)
    dd_from_peak = (median / peak - 1) if peak > 0 else 0.0
    # 回撤响应: 跌≥5% 减半, 恢复至跌幅<2% 加回
    if state == "normal" and dd_from_peak <= -0.05:
        action = "enter_defensive"
    elif state == "defensive" and dd_from_peak >= -0.02:
        action = "exit_defensive"
    elif state == "defensive":
        action = "hold_defensive"
    else:
        action = "hold_normal"
    ew["e2"] = {"median": median, "peak": round(peak, 2), "dd_from_peak": round(dd_from_peak*100, 1),
                "state": state, "action": action}
    now = exchange_now()
    tradable = bool(now) and _fills_open(now)
    if action == "enter_defensive" and not dry:
        if not tradable:
            log.warning("非交易时段, E2 减半抑制为只记录")
            msgs.append(f"【E2择时】中位数 {median} 自峰值回撤 {dd_from_peak*100:.1f}% → 待交易时段减半")
        else:
            slot = led["strategies"]["E_可转债双低"]
            total = sum(h["qty"] for h in slot.get("holds", []))
            sells = [{"symbol": h["symbol"], "qty": h["qty"] // 2,
                      "price": (px or {}).get(h["symbol"], {}).get("price") or h.get("price", 0)}
                     for h in slot.get("holds", []) if h["qty"] // 2 > 0]
            apply_orders(slot, sells, [], px)
            reduced = total <= 0 or sum(h["qty"] for h in slot.get("holds", [])) < total
            if reduced:
                ctrl["e2_state"] = "defensive"
                ew["e2"]["state"] = "defensive"
                # 恢复目标持久于 _control: ew["e2"] 每轮整体重建, 存其中必丢
                ctrl["e2_pre_defensive_qty"] = total
                msgs.append(f"【E2择时】中位数 {median} 自峰值回撤 {dd_from_peak*100:.1f}% → E 减半")
            else:
                msgs.append(f"【E2择时】中位数 {median} 自峰值回撤 {dd_from_peak*100:.1f}% → E 减半卖单未成交, 保持正常待下轮重试")
    elif action == "exit_defensive" and not dry:
        if not tradable:
            log.warning("非交易时段, E2 恢复买回抑制为只记录")
            msgs.append(f"【E2择时】中位数 {median} 回撤收窄至 {dd_from_peak*100:.1f}% → 待交易时段恢复满仓")
        else:
            slot = led["strategies"]["E_可转债双低"]
            cur = sum(h["qty"] for h in slot.get("holds", []))
            target = int(ctrl.get("e2_pre_defensive_qty") or cur * 2)
            if cur <= 0:
                # 无持仓可恢复: 择时条件已解除, 据实报(不声称恢复满仓)
                ctrl["e2_state"] = "normal"
                ew["e2"]["state"] = "normal"
                ctrl.pop("e2_pre_defensive_qty", None)
                msgs.append(f"【E2择时】中位数 {median} 回撤收窄至 {dd_from_peak*100:.1f}% → 无持仓可恢复, 状态转正常")
            elif target <= cur:
                ctrl["e2_state"] = "normal"
                ew["e2"]["state"] = "normal"
                ctrl.pop("e2_pre_defensive_qty", None)
                msgs.append(f"【E2择时】中位数 {median} 回撤收窄至 {dd_from_peak*100:.1f}% → 仓位已足({cur}张), 状态转正常")
            else:
                # 买回须为转债手单位(10张/手)整数倍: 执行内核对 CB 买入强制 LOT_SIZE,
                # 旧 round() 按张分配产生零股买单 → 恒被拒 → 恢复死锁。
                from src.core.paper_execution import AssetClass, DEFAULT_ASSET_POLICIES
                lot = DEFAULT_ASSET_POLICIES[AssetClass.CB].lot_size
                deficit = target - cur
                if deficit < lot:
                    # 距目标不足 1 手(减半零股卖出的固有差额): 买不进整手, 据实转正常而非死等
                    ctrl["e2_state"] = "normal"
                    ew["e2"]["state"] = "normal"
                    ctrl.pop("e2_pre_defensive_qty", None)
                    msgs.append(f"【E2择时】中位数 {median} 回撤收窄至 {dd_from_peak*100:.1f}% → "
                                f"距目标不足1手({cur}/{target}张), 状态转正常")
                    return msgs
                remaining_lots = deficit // lot
                holds = slot.get("holds", [])
                buys: list[dict] = []
                for i, h in enumerate(holds):
                    if i == len(holds) - 1:
                        lots = remaining_lots               # 末位吸收剩余整手
                    else:
                        lots = min(remaining_lots,
                                   max(1, round(deficit * h["qty"] / cur) // lot))
                    share = lots * lot
                    price = (px or {}).get(h["symbol"], {}).get("price") or h.get("price", 0)
                    if share <= 0 or price <= 0:
                        continue
                    buys.append({"symbol": h["symbol"], "qty": share, "price": price})
                    remaining_lots -= lots
                rejects = apply_orders(slot, [], buys, px) if buys else []
                filled = sum(h["qty"] for h in slot.get("holds", []))
                if filled >= target:
                    ctrl["e2_state"] = "normal"
                    ew["e2"]["state"] = "normal"
                    ctrl.pop("e2_pre_defensive_qty", None)
                    msgs.append(f"【E2择时】中位数 {median} 回撤收窄至 {dd_from_peak*100:.1f}% → E 恢复满仓"
                                + (f"(买回{filled - cur}张)" if filled > cur else ""))
                elif filled > cur:
                    # 部分成交: 保持防御, 下轮按差额补足(登记保留)
                    msgs.append(f"【E2择时】中位数 {median} 回撤收窄至 {dd_from_peak*100:.1f}% → E 部分恢复"
                                f"(+{filled - cur}张, 未达{target}), 保持防御待下轮补足")
                else:
                    msgs.append(f"【E2择时】中位数 {median} 回撤收窄至 {dd_from_peak*100:.1f}% → 恢复买回未成交"
                                + (f"({'; '.join(rejects[:2])})" if rejects else "(无有效报价)")
                                + ", 保持防御待下轮重试")
    return msgs


def erp_gate(led: dict, px: dict, dry: bool) -> list[str]:
    """PE 分位总闸（原称 ERP——PE 历史分位并非股权风险溢价，按 spec 改名；每周一查，滞回 80/60）：
    沪深300 PE 10年分位 >80% → 转入防御（主动槽减半由 erp_scale_pass 执行）；<60% 解除。

    研究依据：估值分位只在极值区有效（国信实证 R²=0.13/0.24），中性区不动。
    纯决策：只迁移 erp_state 与登记，不直接下卖单——执行统一走 erp_scale_pass
    （据实 + 被拒次日补做）；作用域以 deck_registry risk_scope 为唯一真源。"""
    ctrl = led.setdefault("_control", {})
    last = ctrl.get("erp_last_check")
    if last and (EX_DATE - datetime.strptime(last, "%Y-%m-%d").date()).days < 5:
        return []
    try:
        df = ts_client().call("index_dailybasic", ts_code="000300.SH",
                              start_date=(EX_DATE - timedelta(days=3652)).strftime("%Y%m%d"),
                              end_date=EX_DATE.strftime("%Y%m%d"), fields="trade_date,pe_ttm",
                              cache_ttl_days=5)
        pe = df.dropna(subset=["pe_ttm"]).sort_values("trade_date")
        hist = [float(x) for x in pe["pe_ttm"] if x and float(x) > 0]
    except Exception as exc:
        log.warning("PE分位总闸数据失败(乐咕兜底): %r", exc)
        hist = []
        try:
            import akshare as ak
            df2 = ak.stock_index_pe_lg(symbol="沪深300")
            col = "滚动市盈率" if "滚动市盈率" in df2.columns else "pe_ttm"
            hist = [float(x) for x in df2[col].tolist() if x and float(x) > 0]
        except Exception as exc2:
            log.warning("PE分位总闸 乐咕兜底也失败: %r", exc2)
    cur = hist[-1] if hist else 0.0
    pct = pe_percentile(hist, cur)
    state = ctrl.get("erp_state", "normal")
    action = erp_decision(pct, state)
    ctrl["erp_last_check"] = EX_DATE.isoformat()
    ctrl["erp"] = {"pe": round(cur, 2), "percentile": pct, "state": state, "action": action}
    msgs = []
    if action == "enter_defensive" and not dry:
        ctrl["erp_state"] = "defensive"
        msgs.append(f"【PE分位总闸】沪深300 PE分位 {pct}% >80 → 转入防御, 主动槽减半执行中")
    elif action == "exit_defensive" and not dry:
        ctrl["erp_state"] = "normal"
        ctrl.pop("erp_scaled", None)   # 解除: 清缩放登记, 不再补做
        for s in _erp_scale_slots():
            led.get("strategies", {}).get(s, {}).pop("erp_note", None)
        msgs.append(f"【PE分位总闸】PE分位 {pct}% <60 → 解除防御")
    return msgs


def _erp_scale_slots() -> tuple[str, ...]:
    """ERP 主动槽（单一真源=deck_registry）: risk_scope ∈ (etf_cycle, stock_cycle) 且非观察态。

    L_北向择时按槽名排除（观察态冻结禁卖；解除观察后移除此排除即自动纳入）。
    E/H（cb）不受 PE 分位总闸（与 E2 只作用 E 槽对称）；C/D（基准）/X（事件）/Y（现金）不适用。"""
    from src.services.deck_registry import SLOTS
    return tuple(s.slot for s in SLOTS
                 if s.risk_scope in ("etf_cycle", "stock_cycle") and s.slot != "L_北向择时")


def erp_scale_pass(led: dict, px: dict, dry: bool) -> list[str]:
    """ERP 缩放执行侧（决策/执行分离）: 防御态下对未标记主动槽 qty//2 卖出, 逐槽据实标记。

    - erp_state != defensive → 空操作；槽访问用 .get() 容忍 fixture 台账缺槽。
    - 无拒绝 → 标记 erp_scaled[slot]=EX_DATE + erp_note；被拒（T+1/闸关/无报价）→ 不标记,
      消息注明"待次日补做", 次日 close 自然重试（逐槽增量语义, 区别于月度整批顺延）。
    - 周度/月度重建成功的槽由重建方清除 erp_scaled 标记 → 本 pass 重新应用 ×0.5（重建后重缩放）。
    """
    ctrl = led.setdefault("_control", {})
    if ctrl.get("erp_state") != "defensive":
        return []
    scaled = ctrl.setdefault("erp_scaled", {})
    msgs: list[str] = []
    for s in _erp_scale_slots():
        if scaled.get(s):
            continue  # 已缩放: 不重复减半
        slot = led.get("strategies", {}).get(s)
        if slot is None:
            continue
        half = [{"symbol": h["symbol"], "qty": h["qty"] // 2,
                 "price": px.get(h["symbol"], {}).get("price") or h.get("price", 0)}
                for h in slot.get("holds", []) if h["qty"] // 2 > 0]
        if not half:
            scaled[s] = EX_DATE.isoformat()   # 空仓/零股: 目标状态已达成
            continue
        if dry:
            msgs.append(f"【总闸缩放】(dry) {s} 计划减半 {len(half)} 笔")
            continue
        rejects = apply_orders(slot, half, [], px, slot_name=s)
        if rejects:
            msgs.append(f"【总闸缩放】{s} 减半卖单被拒({'; '.join(rejects)[:100]}), 待次日补做")
        else:
            scaled[s] = EX_DATE.isoformat()
            slot["erp_note"] = f"PE分位防御 {EX_DATE} 减半"
            msgs.append(f"【总闸缩放】{s} 主动槽减半")
    return msgs


class TushareClient:
    """统一 tushare 数据访问层: 单例 pro、令牌桶限频(480/min<500)、调用缓存、失败计数审计。

    各策略函数一律通过 ts_client.call(api_name, **params) 取数, 不再各自直连。"""
    _instance = None

    def __init__(self):
        import tushare as ts
        token = ""
        for line in (PROJECT_DIR / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("TUSHARE_TOKEN="):
                token = line.split("=", 1)[1].strip().strip('"').strip("'")
        self.pro = ts.pro_api(token)
        self._min_interval = 60.0 / 480  # 500次/分钟, 留 4% 余量
        self._last_call = 0.0
        self._cache: dict = {}
        self.calls_today = 0
        self.errors_today = 0

    @classmethod
    def instance(cls) -> "TushareClient":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def call(self, api_name: str, cache_ttl_days: float = 0.0, **params):
        """调 pro 接口。cache_ttl_days>0 时按 (api, params) 缓存(适合财报/户数等低频变化)。"""
        import time as _time
        key = (api_name, tuple(sorted((k, str(v)) for k, v in params.items())))
        if cache_ttl_days > 0:
            cached = self._cache.get(key)
            if cached and (_time.time() - cached[0]) < cache_ttl_days * 86400:
                return cached[1]
        wait = self._last_call + self._min_interval - _time.time()
        if wait > 0:
            _time.sleep(wait)
        self._last_call = _time.time()
        try:
            df = getattr(self.pro, api_name)(**params)
            self.calls_today += 1
        except Exception as exc:
            self.errors_today += 1
            log.warning("tushare %s 失败: %r", api_name, exc)
            return None
        if cache_ttl_days > 0:
            self._cache[key] = (_time.time(), df)
        return df


def ts_client() -> TushareClient:
    return TushareClient.instance()


def _recent_trading_days(n: int, end: date) -> list[str]:
    """最近 n 个交易日(含 end, 升序 YYYYMMDD)。trade_cal 优先, 不可用时按工作日回退。

    回退路径不含法定假日(仅周末), 是日历服务不可用时的诚实降级: 窗口可能
    混入 1-2 个休市日, 调用方对返回行数做容差判断。"""
    try:
        cal = ts_client().call("trade_cal", exchange="SSE",
                               start_date=(end - timedelta(days=n * 2 + 20)).strftime("%Y%m%d"),
                               end_date=end.strftime("%Y%m%d"),
                               fields="cal_date,is_open", cache_ttl_days=1)
        if cal is not None and len(cal) and "is_open" in cal.columns:
            opens = sorted(str(x) for x in cal.loc[cal["is_open"] == 1, "cal_date"].tolist())
            if len(opens) >= n:
                return opens[-n:]
    except Exception as exc:
        log.warning("trade_cal 失败, 交易日窗口按工作日回退: %r", exc)
    out, day = [], end
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day.strftime("%Y%m%d"))
        day -= timedelta(days=1)
    return list(reversed(out))


def _stock_basic_names() -> dict[str, str]:
    """上市股票 {ts_code: name}(list_status=L)。取数失败返回空 dict, 调用方保守处理。"""
    sb = ts_client().call("stock_basic", list_status="L", fields="ts_code,name", cache_ttl_days=7)
    if sb is None or not len(sb):
        return {}
    return dict(sb.values.tolist())


def _float_or_none(raw) -> float | None:
    """None/''/NaN/不可解析 → None, 其余转 float。"""
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    return val if val == val else None


def _ok_symbol(name: str, ts_code: str) -> bool:
    return ("ST" not in name) and (not ts_code.startswith(("8", "4", "9", "688")))


def pead_universe(days_back: int = 40, top: int = 8) -> list[dict]:
    """I_预增动量 候选: 最近 N 天公告的预增(预告净利同比下限≥50%) → 公告日新→旧排序。

    字段契约(tushare forecast, 官方 doc_id=45): p_change_min/p_change_max(%),
    net_profit_min(万元, 下限为正才排雷"基数型假预增"); 不存在的 p_change 字段
    不得作为"缺字段放行"的依据。同股多公告按 ann_date 降序去重, 取最新版本。

    口径澄清: 同比预增≠相对市场预期的超预期(PEAD/SUE), 本槽是"预增动量"前向检验。"""
    pro = ts_client()
    seen, out, ok_days = set(), [], 0
    for d in range(1, days_back + 1):  # 新→旧遍历: 同股首次命中即最新公告
        day = (EX_DATE - timedelta(days=d)).strftime("%Y%m%d")
        df = pro.call("forecast", ann_date=day,
                      fields="ts_code,ann_date,type,p_change_min,p_change_max,net_profit_min")
        if df is None:
            continue
        ok_days += 1
        if df.empty:
            continue
        for _, r in df.iterrows():
            code = str(r.get("ts_code", ""))
            typ = str(r.get("type", ""))
            chg_min = _float_or_none(r.get("p_change_min"))
            np_min = _float_or_none(r.get("net_profit_min"))
            if typ != "预增" or chg_min is None or chg_min < 50:
                continue  # 下限缺失/不足 50% 均拒绝; 缺字段不再放行
            if np_min is not None and np_min <= 0:
                continue
            if code in seen or not _ok_symbol("", code):
                continue
            seen.add(code)
            out.append({"ts_code": code, "ann_date": day, "p_change_min": chg_min,
                        "p_change_max": _float_or_none(r.get("p_change_max"))})
    if ok_days == 0:
        raise RuntimeError("forecast 数据源全部不可用")
    return out[:top]


def reversal_universe(days: int = 21, top: int = 8) -> list[dict]:
    """J_短期反转候选: 全市场 21 日跌幅最深 + 流动性过滤(成交额≥3000万) + 剔ST/北交所/科创板。

    数据: tushare trade_cal 取交易日窗口(doc), daily 逐日(daily 单次上限6000行)。
    单位契约(tushare doc_id=27): daily.amount 为千元 → 换算成元后再比较阈值。
    机制: A股短期反转(LSY2019/STREV实证)。"""
    pro = ts_client()
    sessions = _recent_trading_days(days + 1, EX_DATE)  # days 期收益 = days+1 个收盘
    series: dict[str, dict[str, tuple[float, float]]] = {}
    ok_days = 0
    for day in sessions:
        df = pro.call("daily", trade_date=day, fields="ts_code,close,amount")
        if df is None:
            continue
        ok_days += 1
        if df.empty:
            continue
        for _, r in df.iterrows():
            amount_yuan = (_float_or_none(r.get("amount")) or 0.0) * 1000.0  # 千元 → 元
            series.setdefault(str(r["ts_code"]), {})[day] = (float(r["close"]), amount_yuan)
    if ok_days == 0:
        raise RuntimeError("daily 数据源全部不可用")
    out = []
    for code, pxmap in series.items():
        days_sorted = sorted(pxmap)
        if len(days_sorted) < days:
            continue
        c0, c1 = pxmap[days_sorted[0]][0], pxmap[days_sorted[-1]][0]
        amt = pxmap[days_sorted[-1]][1]
        if c0 <= 0 or amt < 3e7:
            continue
        out.append({"ts_code": code, "ret21": round((c1 / c0 - 1) * 100, 2)})
    names = _stock_basic_names()
    if not names:
        raise RuntimeError("stock_basic 不可用, 无法过滤 ST/板块")
    out = [r for r in out if _ok_symbol(names.get(r["ts_code"], ""), r["ts_code"])]
    out.sort(key=lambda r: r["ret21"])
    # 崩刀过滤: 跌幅最深候选当日主力净流出≥3000万 → 真崩非超跌, 剔除
    # moneyflow 经 TushareClient 统一限频/审计; 日期回退: 月首交易日前一天可能是休市日
    for r in out:
        r["net_mf"] = None
        for back in range(1, 6):
            day = (EX_DATE - timedelta(days=back)).strftime("%Y%m%d")
            mf = pro.call("moneyflow", trade_date=day, ts_code=r["ts_code"],
                          fields="net_mf_amount")
            if mf is not None and len(mf):
                r["net_mf"] = _float_or_none(mf["net_mf_amount"].iloc[0])
                break
    out = [r for r in out if r.get("net_mf") is None or r["net_mf"] > -3000]
    return out[:top]


def north_percentile(north_series: list, current: float) -> float:
    """纯函数: 昨日北向净买入在近250日中的分位(0-100)。str/None 容错。"""
    try:
        cur = float(current)
    except (TypeError, ValueError):
        return -1.0
    vals = []
    for v in north_series:
        try:
            vals.append(float(v))
        except (TypeError, ValueError):
            continue
    if len(vals) < 60:
        return -1.0
    below = sum(1 for v in vals if v <= cur)
    return round(below / len(vals) * 100, 1)


def north_decision(pct: float) -> str:
    """纯函数: 北向分位 → 仓位动作(60/40阈值)。"""
    if pct < 0:
        return "no_data"
    if pct >= 60:
        return "hold_index"
    if pct < 40:
        return "cash"
    return "keep"


def e_take_profit_pass(led: dict, px: dict, dry: bool) -> list[str]:
    """E 槽单券止盈: 持仓券现价≥130 元(强赎博弈区) → 全部卖出落袋。社区共识口径。"""
    slot = led["strategies"].get("E_可转债双低")
    if not slot:
        return []
    sells = []
    for h in slot.get("holds", []):
        p = px.get(h["symbol"], {}).get("price") or 0
        if p >= 130:
            sells.append({"symbol": h["symbol"], "qty": h["qty"], "price": p})
    if not sells:
        return []
    if not dry:
        apply_orders(slot, sells, [], px)
        save_ledger(led)
        slot.setdefault("tuning", []).append(
            {"date": EX_DATE.isoformat(), "change": f"单券止盈 {[s['symbol'] for s in sells]}",
             "why": "≥130元强赎博弈区(社区共识口径)"})
    return [f"E单券止盈: {' '.join(s['symbol'] for s in sells)} @≥130"]


def north_rebalance(led: dict, px: dict, dry: bool) -> list[str]:
    """L_北向择时: **观察态**——信号照算并记录, 但持仓冻结, 不产生任何新交易。

    依据: 上交所上证发〔2024〕106号(2024-08-19 实施)后, 沪股通每日只公布成交
    总额/笔数/前十大活跃证券成交总额, 单只证券持股改季度披露; moneyflow_hsgt
    现值是否为"官方每日净买入"无法证实。拿未证实字段驱动真金交易不负责任,
    故本槽只做前向观察记录, 等字段语义有官方结论后再评估是否恢复交易。
    信号计算保留(纯函数 north_percentile/north_decision 仍可测)。"""
    slot = led["strategies"].get("L_北向择时")
    if not slot:
        return []
    if dry:
        return ["L_北向(dry) 观察态: 信号计算, 不交易"]
    try:
        pro = ts_client()
        df = pro.call("moneyflow_hsgt", start_date=(EX_DATE - timedelta(days=400)).strftime("%Y%m%d"),
                                end_date=EX_DATE.strftime("%Y%m%d"), fields="trade_date,north_money")
        df = df.dropna(subset=["north_money"]).sort_values("trade_date")
        series = df["north_money"].tolist()
        yesterday = float(df["north_money"].iloc[-1]) if df["trade_date"].iloc[-1] == EX_DATE.strftime("%Y%m%d") else (float(df["north_money"].iloc[-2]) if len(df) > 1 else None)
        pct = north_percentile(series[:-1], yesterday)  # 用截至昨日的窗口, 信号=昨日值
        action = north_decision(pct)
        slot["signal"] = {"date": EX_DATE.isoformat(), "north_yesterday": yesterday,
                          "pct": pct, "action": action, "mode": "observation",
                          "note": "106号后字段语义未证, 观察态不交易"}
        held = sum(h["qty"] for h in slot.get("holds", []))
        return [f"L_北向择时[观察态] {action}(北向分位{pct}%) 持仓冻结{held}份, 不交易"]
    except Exception as exc:
        log.error("L_北向 信号失败: %r", exc)
        return ["【异常】L_北向 信号计算失败, 持仓不变"]


def _holder_period_frame(pro, period: str) -> pd.DataFrame:
    """单报告期股东户数(ts_code, holder_num): 同股多次披露按 ann_date 取最新版本。

    官方 doc_id=166: ann_date=公告日(可能晚于报告期), end_date=截止日, 同一报告期
    可因更正/补充披露出现多行 → 不去重会产生同股重复候选(笛卡尔积)。"""
    df = pro.call("stk_holdernumber", enddate=period,
                  fields="ts_code,ann_date,end_date,holder_num", cache_ttl_days=30)
    if df is None or df.empty:
        return pd.DataFrame(columns=["ts_code", "holder_num"])
    df = df.copy()
    if "ann_date" in df.columns:
        # NaN 公告日按"最旧"处理(na_position=first), keep=last 确保取到最大真实 ann_date
        df = df.sort_values("ann_date", kind="stable", na_position="first")
    return df.drop_duplicates(subset=["ts_code"], keep="last")[["ts_code", "holder_num"]]


def holder_concentration_universe(top: int = 8) -> list[dict]:
    """M_筹码集中度候选: 最新报告期股东户数降幅≥10% 排序(筹码向主力集中)。

    数据: tushare stk_holdernumber 按报告期全市场单次查询(季度频率, doc_id=166)。
    过滤: ST/北交所/科创板; 流动性与市值约束在 px 层做。
    口径降级声明: 户数下降不必然等于"主力建仓"(可能只是散户离场), 低获利比例
    亦不等于安全区——cyq 仅作剔除"筹码已高位"的辅助筛, 不构成正向依据。"""
    pro = ts_client()
    cur_q = _latest_report_period(EX_DATE)
    prev_q = _prev_report_period(cur_q)
    df = _holder_period_frame(pro, cur_q)
    prev = _holder_period_frame(pro, prev_q)
    if df.empty or prev.empty:
        # 报告期数据缺失多为数据源不可用；空列表会被注册表当成"成功无候选"，
        # 月度槽当月不再重试。显式抛出，由注册表按失败顺延。
        raise RuntimeError(f"stk_holdernumber 数据缺失 {cur_q}/{prev_q}")
    m = df.merge(prev, on="ts_code", suffixes=("_new", "_prev"))
    m = m[(m["holder_num_new"] > 0) & (m["holder_num_prev"] > 0)]
    m["chg"] = (m["holder_num_new"] / m["holder_num_prev"] - 1) * 100
    m = m[m["chg"] <= -10].sort_values("chg")
    names = _stock_basic_names()
    if not names:
        raise RuntimeError("stock_basic 不可用, 无法过滤 ST/板块")
    out = []
    for _, r in m.iterrows():
        code = str(r["ts_code"])
        nm = names.get(code, "")
        if not _ok_symbol(nm, code):
            continue
        # cyq_perf 二次验证: 最新获利比例>60% → 筹码已高位, 剔除(防追已完成行情);
        # 接口异常(None) → 显式跳过该候选, 不静默放行未经校验的标的
        wr = None
        cyq = pro.call("cyq_perf", ts_code=code,
                       start_date=(EX_DATE - timedelta(days=14)).strftime("%Y%m%d"),
                       end_date=EX_DATE.strftime("%Y%m%d"), fields="trade_date,winner_rate")
        if cyq is None:
            log.warning("M: cyq_perf 不可用(%s), 跳过该候选", code)
            continue
        if len(cyq):
            wr = _float_or_none(cyq.sort_values("trade_date")["winner_rate"].iloc[-1])
        if wr is not None and wr > 60:
            continue
        out.append({"ts_code": code, "name": nm, "chg": round(float(r["chg"]), 1),
                    "winner_rate": round(wr, 1) if wr is not None else None})
        if len(out) >= top:
            break
    return out


def _latest_report_period(d: date) -> str:
    """最近已披露完毕的报告期(披露截止≈季末后45天): 11/12月用Q3, 8/9/10月用Q2。"""
    y, mth = d.year, d.month
    if mth >= 11:            # 三季报(0930)披露截止10-31 → 11月起可用
        return f"{y}0930"
    if mth >= 8:             # 中报(0630)披露截止08-31 → 8-10月用Q2
        return f"{y}0630"
    if mth >= 5:             # 一季报(0331)披露截止04-30 → 5-7月用Q1
        return f"{y}0331"
    if mth >= 2:             # 年报(1231)披露截止04-30前多数在3月, 保守2月起用上年Q3? — 年报预告多在1月
        return f"{y-1}0930"
    return f"{y-1}0930"


def _prev_report_period(period: str) -> str:
    y, m = int(period[:4]), int(period[4:6])
    if m == 12:
        return f"{y}0930"
    if m == 9:
        return f"{y}0630"
    if m == 6:
        return f"{y}0331"
    return f"{y-1}1231"


def holder_rebalance(led: dict, px: dict, dry: bool) -> list[str]:
    """M_筹码集中度: 季度数据月度窗重取 → 市值/流动性过滤 → 与持仓 diff。"""
    slot = led["strategies"].get("M_筹码集中度")
    if not slot:
        return []
    if dry:
        return ["M_筹码集中度(dry) 候选重取"]
    try:
        cands = holder_concentration_universe()
        cand_syms = [("SH" if r["ts_code"].endswith(".SH") else "SZ") + r["ts_code"][:6] for r in cands]
        ensure_prices(px, cand_syms)
        picks = []
        for r, sym in zip(cands, cand_syms):
            p = px.get(sym, {}).get("price") or 0
            if p and p * 100 <= 2500:
                picks.append({"symbol": sym, "name": r["name"], "price": p, "qty": 100,
                              "holder_chg": r["chg"]})
            if len(picks) >= 5:
                break
        if cands and not picks and not any((px.get(s) or {}).get("price") for s in cand_syms):
            slot["note"] = f"{EX_DATE} 候选无报价, 维持旧仓"
            return ["【异常】M_筹码集中度 候选无报价, 持仓不变"]
        sells = [{"symbol": h["symbol"], "qty": h["qty"], "price": px.get(h["symbol"], {}).get("price") or h.get("price", 0), "is_stock": True}
                 for h in slot.get("holds", []) if h["symbol"] not in {p["symbol"] for p in picks}]
        buys = [{"symbol": p["symbol"], "name": p["name"], "qty": p["qty"], "price": p["price"], "is_stock": True}
                for p in picks if p["symbol"] not in {h["symbol"] for h in slot.get("holds", [])}]
        rejects = apply_orders(slot, sells, buys, px)
        if rejects:
            slot["note"] = f"{EX_DATE} 调仓被拒: {rejects[0][:80]}"
            return [f"【顺延】M_筹码集中度 未成交: {'; '.join(rejects)[:160]}"]
        slot["note"] = f"{EX_DATE} 户数降幅候选: " + " ".join(f"{p['name']}{p['holder_chg']:+.0f}%" for p in picks[:3])
        return [f"M_筹码集中度 调仓→{[p['symbol'] for p in picks] or '空仓'}"]
    except Exception as exc:
        log.error("M_筹码集中度 调仓失败: %r", exc)
        return ["【异常】M_筹码集中度 候选重取失败, 持仓不变"]


_SEQUOIA_DB = PROJECT_DIR.parent / "quant_research" / "vendor" / "Sequoia-X" / "data" / "sequoia_v2.db"
_SEQUOIA_MIN_ROWS = 2_000_000  # 实测全量231万, 阈值取整200万


def sequoia_ready() -> bool:
    """Sequoia-X 数据就绪: 行数≥_SEQUOIA_MIN_ROWS 且 最新bar在近10天内。"""
    try:
        import sqlite3
        con = sqlite3.connect(_SEQUOIA_DB)
        n = con.execute("SELECT COUNT(*) FROM stock_daily").fetchone()[0]
        latest = con.execute("SELECT MAX(date) FROM stock_daily").fetchone()[0]
        from datetime import datetime as _dt
        age_days = (_dt.now() - _dt.strptime(latest, "%Y-%m-%d")).days if latest else 9999
        ready = n >= _SEQUOIA_MIN_ROWS and age_days <= 10
        if not ready:
            log.info("Sequoia-X 数据未就绪: %s 行, 最新 %s", f"{n:,}", latest)
        return ready
    except Exception:
        return False


def sequoia_scan(led: dict, px: dict, dry: bool) -> list[str]:
    """N_技术形态对照: Sequoia-X 七策略扫描（命中≥2策略优先），月度窗调用。

    条件式: 数据就绪(≥200万行且最新bar在10天内)才扫描, 否则跳过。"""
    if dry:
        return ["N_技术形态(dry) 待数据就绪"]
    if not sequoia_ready():
        return []
    driver = PROJECT_DIR.parent / "quant_research" / "vendor" / "Sequoia-X" / "n_slot_driver.py"
    try:
        r = _run_cmd([sys.executable, str(driver)], driver.parent, 1200)
        data = json.loads(r.stdout.strip().splitlines()[-1])
        hits: dict[str, int] = {}
        for strat, codes in data.items():
            if isinstance(codes, list):
                for c in codes:
                    hits[c] = hits.get(c, 0) + 1
        ranked = sorted(hits, key=lambda c: -hits[c])
        slot = led["strategies"].get("N_技术形态对照")
        if not slot:
            return []
        cand_syms = [("SH" if c.startswith(("6", "5")) else "SZ") + c[:6] for c in ranked]
        px = ensure_prices(px, cand_syms)
        picks = []
        for c, sym in zip(ranked, cand_syms):
            p = px.get(sym, {}).get("price") or 0
            if p and p * 100 <= 2500:
                picks.append({"symbol": sym, "qty": 100, "price": p, "hits": hits[c]})
            if len(picks) >= 5:
                break
        if ranked and not picks and not any((px.get(s) or {}).get("price") for s in cand_syms):
            slot["note"] = f"{EX_DATE} 候选无报价, 维持旧仓"
            return ["【异常】N_技术形态 候选无报价, 持仓不变"]
        sells = [{"symbol": h["symbol"], "qty": h["qty"], "price": px.get(h["symbol"], {}).get("price") or h.get("price", 0), "is_stock": True}
                 for h in slot.get("holds", []) if h["symbol"] not in {p["symbol"] for p in picks}]
        buys = [{"symbol": p["symbol"], "qty": p["qty"], "price": p["price"], "is_stock": True}
                for p in picks if p["symbol"] not in {h["symbol"] for h in slot.get("holds", [])}]
        rejects = apply_orders(slot, sells, buys, px)
        if rejects:
            slot["note"] = f"{EX_DATE} 调仓被拒: {rejects[0][:80]}"
            return [f"【顺延】N_技术形态 未成交: {'; '.join(rejects)[:160]}"]
        slot["note"] = f"{EX_DATE} 多策略命中: " + " ".join(f"{p['symbol']}×{p['hits']}" for p in picks[:3])
        return [f"N_技术形态 调仓→{[p['symbol'] for p in picks] or '空仓'}"]
    except Exception as exc:
        log.error("N_技术形态 扫描失败: %r", exc)
        return ["【异常】N_技术形态 扫描失败, 持仓不变"]


def industry_momentum_universe(top_industries: int = 3, per_industry: int = 2, top: int = 8) -> list[dict]:
    """O_行业轮动候选: 申万 L1 行业最近 20 个交易日动量排序 → 前 N 行业 → 成分股按流通市值选龙头。

    数据: tushare index_classify(src=SW2021 钉版本, 行业分类口径不随默认值漂移)
    + sw_daily(行业指数, 先升序再算动量) + index_member_all(全量成分, 不再先截 200,
    避免漏掉大市值龙头) + daily_basic(市值)。窗口: trade_cal 最近 21 个收盘 = 20 期收益。"""
    pro = ts_client()
    lv1 = pro.call("index_classify", level="L1", src="SW2021", fields="index_code")
    if lv1 is None or lv1.empty:
        raise RuntimeError("index_classify 数据源不可用")
    codes = lv1["index_code"].tolist()
    sessions = _recent_trading_days(21, EX_DATE)
    start, end = sessions[0], EX_DATE.strftime("%Y%m%d")
    # 窗口完整性下限(75%): trade_cal 不可用按工作日回退时会混入休市日, 少于 15 根不评分
    min_rows = max(10, int(len(sessions) * 0.75))
    scored = []
    sw_ok = 0
    for c in codes:
        d = pro.call("sw_daily", ts_code=c, start_date=start, end_date=end,
                     fields="trade_date,close")
        if d is None:
            continue
        sw_ok += 1
        if d.empty:
            continue
        d = d.dropna(subset=["close"]).sort_values("trade_date")  # 官方降序返回 → 先升序
        if len(d) < min_rows:
            continue
        closes = d["close"].astype(float).tail(len(sessions)).tolist()
        if closes[0] <= 0:
            continue
        ret20 = (closes[-1] / closes[0] - 1) * 100
        scored.append((c, ret20))
    if sw_ok == 0:
        raise RuntimeError("sw_daily 数据源全部不可用")
    scored.sort(key=lambda x: -x[1])
    top_ind = [c for c, _ in scored[:top_industries]]
    if not top_ind:
        raise RuntimeError("行业动量无有效数据")
    # 成分股(每行业按流通市值取前 per_industry 只, 全量成员再排序)
    pool = []
    for ind in top_ind:
        members = pro.call("index_member_all", l1_code=ind, fields="ts_code")
        if members is None or members.empty:
            continue
        syms = {str(x) for x in members["ts_code"].tolist()}
        db = None
        for back in range(0, 8):  # 休市日回退
            db = pro.call("daily_basic", trade_date=(EX_DATE - timedelta(days=back)).strftime("%Y%m%d"),
                          fields="ts_code,circ_mv")
            if db is not None and len(db):
                break
        if db is None or db.empty:
            continue
        db = db[db["ts_code"].isin(syms)].dropna(subset=["circ_mv"])
        if db.empty:
            continue
        # 行业β代表: 选行业内市值最大 per_industry 只(流动性龙头)——不混小市值因子, 保持行业动量归因纯净
        for _, r in db.nlargest(per_industry, "circ_mv").iterrows():
            pool.append({"ts_code": r["ts_code"], "industry": ind})
    if not pool:
        raise RuntimeError("行业成分/市值数据不可用")
    return pool[:top]


def industry_rebalance(led: dict, px: dict, dry: bool) -> list[str]:
    """O_行业轮动: 月度窗重取行业动量+成分龙头 → 与持仓 diff。"""
    slot = led["strategies"].get("O_行业轮动")
    if not slot:
        return []
    if dry:
        return ["O_行业轮动(dry) 候选重取"]
    try:
        cands = industry_momentum_universe()
        if not cands:
            slot["note"] = f"{EX_DATE} 无候选, 维持旧仓"
            return ["O_行业轮动 无候选, 维持旧仓"]
        cand_syms = [("SH" if r["ts_code"].endswith(".SH") else "SZ") + r["ts_code"][:6] for r in cands]
        ensure_prices(px, cand_syms)
        picks = []
        for r, sym in zip(cands, cand_syms):
            p = px.get(sym, {}).get("price") or 0
            if p and p * 100 <= 2500 and sym not in {q["symbol"] for q in picks}:
                picks.append({"symbol": sym, "price": p, "qty": 100})
            if len(picks) >= 5:
                break
        if not picks and not any((px.get(s) or {}).get("price") for s in cand_syms):
            slot["note"] = f"{EX_DATE} 候选无报价, 维持旧仓"
            return ["【异常】O_行业轮动 候选无报价, 持仓不变"]
        sells = [{"symbol": h["symbol"], "qty": h["qty"], "price": px.get(h["symbol"], {}).get("price") or h.get("price", 0), "is_stock": True}
                 for h in slot.get("holds", []) if h["symbol"] not in {p["symbol"] for p in picks}]
        buys = [{"symbol": p["symbol"], "qty": p["qty"], "price": p["price"], "is_stock": True}
                for p in picks if p["symbol"] not in {h["symbol"] for h in slot.get("holds", [])}]
        rejects = apply_orders(slot, sells, buys, px)
        if rejects:
            slot["note"] = f"{EX_DATE} 调仓被拒: {rejects[0][:80]}"
            return [f"【顺延】O_行业轮动 未成交: {'; '.join(rejects)[:160]}"]
        slot["note"] = f"{EX_DATE} 行业动量Top3龙头轮动"
        return [f"O_行业轮动 调仓→{[p['symbol'] for p in picks] or '空仓'}"]
    except Exception as exc:
        log.error("O_行业轮动 调仓失败: %r", exc)
        return ["【异常】O_行业轮动 候选重取失败, 持仓不变"]


def build_close_summary(led: dict, px: dict) -> str:
    """收盘消息：排名 + 今日操作 + 每槽持仓明细（数量/成本/现价/盈亏）+ 风险状态。"""
    equity = {name: s.get("equity", 0) for name, s in led["strategies"].items()}
    ret = {name: round((eq / CAPITAL - 1) * 100, 2) for name, eq in equity.items()}
    rank = sorted(equity, key=lambda k: equity[k], reverse=True)
    today = EX_DATE.isoformat()
    lines = [f"【对比盘收盘 {today}】", "▎排名"]
    for i, k in enumerate(rank, 1):
        dd = slot_drawdown(led.get("marks", []), k, equity[k])
        lines.append(f"{i}. {k} ¥{equity[k]:,.0f} ({ret[k]:+.2f}%) 回撤{dd:.1f}%")
    lines.append("▎今日操作")
    actions = (led.get("_actions", {}).get(today) or [])
    lines += [f"· {a}" for a in actions] if actions else ["· 无调仓"]
    lines.append("▎持仓明细")
    for name in ("A_动量", "B_轮动", "E_可转债双低", "F_小市值20", "F2_小市值5只神帖对照", "X_事件套利", "G_MWU融合", "H_MWU_CB融合", "I_PEAD业绩预告", "J_短期反转", "K_项目选股", "L_北向择时", "M_筹码集中度", "N_技术形态对照", "O_行业轮动", "C_沪深300持有", "D_红利低波持有"):
        holds = led["strategies"][name].get("holds", [])
        if not holds:
            lines.append(f"{name}: 空仓")
            continue
        parts = []
        for h in holds:
            cost = h.get("price", 0)
            now = (px.get(h["symbol"], {}).get("price")) or cost
            unit = "张" if ("转债" in h.get("name", "") or name.startswith("E_")
                            or h["symbol"] == "511010.SH") else "股"
            pnl = (now / cost - 1) * 100 if cost else 0
            parts.append(f"{h.get('name', h['symbol'])} {h['qty']}{unit}@{cost}→{now}({pnl:+.1f}%)")
        lines.append(f"{name}: " + " ".join(parts))
    ew = led["strategies"]["E_可转债双低"].get("credit_watch", {})
    lines.append(f"▎风险 信用最低{ew.get('min_price', '—')} | 本金¥{CAPITAL:,.0f}/槽")
    if _is_month_last_trading_day():
        lines.append("▎Y槽窗口: 今日为月末/节前最后交易日, 14:30后可操作逆回购GC001(¥12,000)")
    if EX_DATE.weekday() == 4 and led.get("prereg"):  # 周五: 预注册假设状态
        lines.append("▎假设状态(预注册)")
        for name, pr in led["prereg"].items():
            if name.startswith("_"):
                continue
            pred = str(pr.get("prediction", pr.get("hypothesis", "")))[:46]
            lines.append(f"{name}: {pred} | 复核 {(pr.get('review') or '2026-12-31')[:10]}")
    return "\n".join(lines)


def daily_dividend_pass(led: dict, px: dict, dry: bool) -> list[str]:
    """每日一次：股票/ETF 持仓的除息分红计入现金（红利策略语义=分红到账）。

    幂等：分红按事件入账——每 (槽位, 证券) 登记已入账事件（desc 含除息日集），
    同事件跨日/跨进程不重复入账；起算日随入账推进到 EX_DATE，历史事件不再重复聚合。
    缓存键 (sym, since, qty) 含持仓数量：同证券不同账户各按自身份额计。"""
    ctrl = led.setdefault("_control", {})
    if ctrl.get("dividend_check_last") == EX_DATE.isoformat():
        return []
    msgs = []
    since_base = (led.get("start_date") or "2026-09-28").replace("-", "")
    booked = ctrl.setdefault("dividend_booked", {})   # "槽位|证券" -> 已入账事件
    since_mark = ctrl.setdefault("dividend_since", {})  # "槽位|证券" -> 已入账至 YYYYMMDD
    for name, s in led["strategies"].items():
        for h in s.get("holds", []):
            sym = h["symbol"]
            if not sym.endswith((".SH", ".SZ")) or sym.startswith(("SH204001",)):
                continue
            acct = f"{name}|{sym}"
            since = max(since_base, since_mark.get(acct, ""))
            key = (sym, since, h["qty"])
            if key not in _DIV_CACHE:
                _DIV_CACHE[key] = dividend_accrued(sym, since, h.get("price", 0), h["qty"])
            cash, desc = _DIV_CACHE[key]
            prior = booked.get(acct)
            if cash > 0 and prior and prior.get("desc") == desc:
                continue  # 同一分红事件已入账（跨日重复聚合），跳过
            if cash > 0 and not dry:
                s["cash"] = round(s.get("cash", 0) + cash, 2)
                booked[acct] = {"since": since, "desc": desc, "amount": cash}
                since_mark[acct] = EX_DATE.strftime("%Y%m%d")
                s.setdefault("tuning", []).append(
                    {"date": EX_DATE.isoformat(), "change": f"分红到账 ¥{cash}", "why": desc})
                msgs.append(f"分红 {name} {h.get('name', sym)} +¥{cash} ({desc})")
    ctrl["dividend_check_last"] = EX_DATE.isoformat()
    return msgs


_DIV_CACHE: dict = {}


_WEEK_CACHE: dict = {}


def _is_week_first_trading_day() -> bool:
    """EX_DATE 是否本周(周一~今天)首个交易日——假期周(如10-08周四开盘)不跳过周频信号。

    缓存键必须含具体日期：旧实现按"周"作键，同进程跨日复调用会返回前一天的结果。
    """
    day_key = f"{EX_DATE:%Y%m%d}"
    if _WEEK_CACHE.get(day_key) is not None:
        return _WEEK_CACHE[day_key]
    result = True
    for back in range(1, EX_DATE.weekday() + 1):
        d = EX_DATE - timedelta(days=back)
        if is_exchange_trading_day(d):
            result = False
            break
    _WEEK_CACHE[day_key] = result
    return result


def _monthly_tasks(led: dict, px: dict, dry: bool, fills_ok: bool) -> list:
    """月度调仓注册表: (标签, 槽位元组, 调用) 列表。函数内构造, 探针 ns 可逐个替换策略。

    - 标签: rebalance_done 月度记录的子任务标识; 全部成功登记 "all",
      部分成功登记已成功标签列表(次日 close 经列表门续做, 避免"整月丢失")。
    - 槽位元组: 子任务重建的槽位, 成功后清除 erp_scaled 标记
      (重建后重缩放: 否则月度重建满仓后标记仍在, 防御系数永久丢失)。
    """
    return [
        ("smallcap20", ("F_小市值20",),
         lambda: smallcap_rebalance(led, "F_小市值20", 8, 2500, True, px, dry)),
        ("smallcap5", ("F2_小市值5只神帖对照",),
         lambda: smallcap_rebalance(led, "F2_小市值5只神帖对照", 3, 4000, False, px, dry)),
        ("cb", ("E_可转债双低",),
         lambda: cb_rebalance(led, px, dry)),
        ("mwu_cb", ("H_MWU_CB融合",),
         lambda: mwu_rebalance(led, px, dry, slot_name="H_MWU_CB融合",
                               strategy="etf_mwu_cb",
                               strategy_config="config/strategy/etf_mwu_cb_monthly.yaml",
                               fills_ok=fills_ok)),
        ("pead", ("I_PEAD业绩预告",),
         lambda: pead_rebalance(led, px, dry)),
        ("reversal", ("J_短期反转",),
         lambda: reversal_rebalance(led, px, dry)),
        ("buywatch", ("K_项目选股",),
         lambda: buywatch_rebalance(led, px, dry)),
        ("holder", ("M_筹码集中度",),
         lambda: holder_rebalance(led, px, dry)),
        ("industry", ("O_行业轮动",),
         lambda: industry_rebalance(led, px, dry)),
        ("sequoia", ("N_技术形态对照",),
         lambda: sequoia_scan(led, px, dry)),
    ]


def _weekly_tasks(led: dict, px: dict, dry: bool, fills_ok: bool) -> list:
    """周度调仓注册表: (标签, 槽位元组, 调用) 列表。

    与月度同构——周频槽此前是裸调用（非撮合时段只记 log、被拒不重试、消息照报
    "调仓→"），与月频/总闸语义不一致；改由注册表统一承载
    「子任务幂等 + 失败重试 + 非撮合窗顺延」。
    """
    return [
        ("momentum", ("A_动量",), lambda: momentum_signal(led, px, dry, fills_ok=fills_ok)),
        ("rotation", ("B_轮动",), lambda: rotation_signal(led, px, dry, fills_ok=fills_ok)),
        ("mwu", ("G_MWU融合",), lambda: mwu_rebalance(led, px, dry, fills_ok=fills_ok)),
    ]


def _subtask_registry_pass(led: dict, ctrl: dict, registry_key: str, key: str,
                           tasks: list, *, tag: str, fills_ok: bool) -> list[str]:
    """子任务注册表的**唯一实现**（月频与周频共用，避免同一语义两套代码）。

    语义：
    - 非撮合时段 → 只顺延不执行；用空列表占位（已有部分完成记录不被覆盖）。
    - 撮合时段 → 逐个执行未完成子任务；子任务自报【异常】/【顺延】则不登记（后续同窗续做）。
    - 全部成功登记 "all"，部分成功登记已完成标签列表 → 后续同窗（月内每日 /
      同周后续交易日）经列表门续做至齐备。
    - 子任务成功即清除该槽 `erp_scaled` 标记（重建后待总闸重新缩放）。

    `fills_ok` 必须由 run_close 在启动时捕获（见 `_session_fills_ok`）——慢行情
    预取后再取时可能失败/回退墙钟，导致合法的 15:00 收盘轮被误判为盘后补跑。
    """
    msgs: list[str] = []
    done = ctrl.setdefault(registry_key, {})
    if not fills_ok:
        # 非撮合时段(如 15:0x 补结算轮 / 补跑轮): 只顺延不执行。
        if not isinstance(done.get(key), list):
            done[key] = []
        msgs.append(f"【{tag}调仓】非撮合时段, {tag}调仓顺延 (待撮合窗续做)")
        return msgs
    ok = list(done.get(key)) if isinstance(done.get(key), list) else []
    for label, slots, call in tasks:
        if label in ok:
            continue  # 幂等: 已完成子任务不重跑
        try:
            sub = list(call() or [])
        except Exception as exc:                      # noqa: BLE001
            log.error("%s子任务 %s 失败(不影响净值落账): %r", tag, label, exc)
            msgs.append(f"【异常】{tag}子任务 {label} 失败: {exc!r:.120}")
            continue
        msgs.extend(sub)
        if any(isinstance(m, str) and m.startswith(("【异常】", "【顺延】")) for m in sub):
            continue  # 子策略自报失败/顺延: 不登记, 后续同窗续做
        ok.append(label)
        scaled = ctrl.get("erp_scaled")
        if scaled:
            for s in slots:
                scaled.pop(s, None)
    done[key] = "all" if len(ok) == len(tasks) else ok
    return msgs


# ---------------------------------------------------------------- 基准一次性建仓
# 买入持有基准 C/D 的标的一经开账即**永久持有**，其余槽都由周/月/事件/日历注册表
# 负责建仓，**唯独 C/D 无人负责** → 新 cohort 清空持仓后它们恒为现金，C 长期 0%，
# 令"主动槽跑输 C 即降级"这条核心评价纪律失效。故补一次性建仓（幂等，走同一注册表）。
BENCHMARK_BUILD = (("C_沪深300持有", "510300.SH"), ("D_红利低波持有", "512890.SH"))


def benchmark_build_one(led: dict, slot_name: str, symbol: str, px: dict, dry: bool) -> list[str]:
    """基准槽一次性建仓：以槽内全部现金买满整手目标 ETF（预留 2% 缓冲，与其它槽同口径）。

    幂等双保险：已有持仓直接跳过；一次性门由 `_control.bootstrap_done` 承担。
    无报价 / 不足一手 → 自报【异常】（不登记），由注册表在后续撮合窗续做。
    """
    slot = led["strategies"][slot_name]
    if slot.get("holds"):
        return [f"{slot_name} 已有持仓, 跳过建仓"]
    if not dry:
        ensure_prices(px, [symbol])
    price = float((px.get(symbol) or {}).get("price") or 0.0)
    if price <= 0:
        return [f"【异常】{slot_name} 无 {symbol} 有效报价, 建仓待续做"]
    qty = int(float(slot.get("cash", 0.0)) * 0.98 // (price * 100)) * 100
    if qty <= 0:
        return [f"【异常】{slot_name} 现金不足一手 {symbol}"]
    if dry:
        return [f"{slot_name}(dry) 建仓→{symbol} x{qty}"]
    rejects = apply_orders(slot, [], [{"symbol": symbol, "qty": qty, "price": price,
                                       "is_stock": False}], px, slot_name)
    if rejects:
        return [f"【异常】{slot_name} 建仓被拒: {'; '.join(rejects)[:100]}"] + rejects
    slot["note"] = f"基准建仓 {symbol} x{qty} @{price}（买入持有）"
    return [f"{slot_name} 建仓→{symbol} x{qty}"]


def _bootstrap_tasks(led: dict, px: dict, dry: bool) -> list:
    """一次性建仓注册表（买入持有基准 C/D），与周/月度同构并复用 `_subtask_registry_pass`。"""
    return [(name, (name,), (lambda n=name, s=sym: benchmark_build_one(led, n, s, px, dry)))
            for name, sym in BENCHMARK_BUILD]


def run_close(dry: bool) -> str:
    # 交易日守卫: run_intraday 内部 catch-up 会直调本函数, 15:10 后 CLI close 模式也无盘中守卫,
    # 非交易日（周末/节假日）不得补结算写 marks；接口失败时 is_exchange_trading_day 保守 True（幂等兜底）。
    if not is_exchange_trading_day(EX_DATE):
        return f"休市(交易所日历 {EX_DATE})，跳过"
    # 启动时捕获成交闸：后面 C/D/引擎行情预取可能耗时数十秒，不能二次取时。
    fills_ok = _session_fills_ok()
    led = load_ledger()
    px = slot_prices(led)
    today = EX_DATE.isoformat()
    # 核心基准缺价时整轮顺延：否则 marks 会被记成「只剩现金」，且 close_done
    # 会让后续恢复的轮次不再补写。重试一次行情后仍缺即返回【异常】。
    core_missing = _core_missing_quotes(led, px)
    if core_missing:
        ensure_prices(px, core_missing)
        core_missing = _core_missing_quotes(led, px)
    if core_missing:
        msg = f"【异常】收盘缺价 {core_missing}，本轮不写 marks/注册表，待后续轮重试"
        log.warning(msg)
        return msg
    snap_msg = maybe_refresh_snapshot(dry)
    repo_msgs = [] if dry else repo_maturity_pass(led, dry)
    div_msgs = [] if dry else daily_dividend_pass(led, px, dry)
    msgs: list[str] = []
    if snap_msg:
        msgs.append(snap_msg)
    msgs.extend(repo_msgs)
    msgs.extend(div_msgs)
    msgs.extend(erp_gate(led, px, dry))
    msgs.extend(e2_gate(led, px, dry))
    msgs.extend(e_take_profit_pass(led, px, dry))
    msgs.extend(north_rebalance(led, px, dry))
    if _is_month_last_trading_day() and not dry:
        rep = monthly_report(led, dry)
        if rep:
            notify(rep, dedup_key=f"deck_monthly_{EX_DATE:%Y-%m}")
    ctrl = led.setdefault("_control", {})
    # 基准一次性建仓: 买入持有槽 C/D 无周/月/事件/日历注册表覆盖，开账清空后必须补建，
    # 否则 C 恒为现金 → "跑输 C 即降级"的评价纪律失效。非撮合窗顺延、后续撮合窗续做。
    if not dry and ctrl.setdefault("bootstrap_done", {}).get("once") != "all":
        msgs.extend(_subtask_registry_pass(
            led, ctrl, "bootstrap_done", "once",
            _bootstrap_tasks(led, px, dry), tag="建仓", fills_ok=fills_ok))
    # 周度注册表: 与月度同构（同一语义单一实现 `_subtask_registry_pass`）。
    # 门: 周首交易日启动; 非撮合窗顺延; 部分完成(列表记录)则**同周后续交易日**续做至齐备
    #     （周频窗口只有"周首交易日"这一个，此前裸调用错过即整周丢失，且消息仍报"调仓→"）。
    iso = EX_DATE.isocalendar()
    week_key = f"{iso[0]}-W{int(iso[1]):02d}"
    wdone = ctrl.setdefault("week_rebalance_done", {})
    if (not dry and wdone.get(week_key) != "all"
            and (_is_week_first_trading_day() or isinstance(wdone.get(week_key), list))):
        msgs.extend(_subtask_registry_pass(
            led, ctrl, "week_rebalance_done", week_key,
            _weekly_tasks(led, px, dry, fills_ok), tag="周度", fills_ok=fills_ok))
    month_key = today[:7]
    done = ctrl.setdefault("rebalance_done", {})
    # 月度注册表: 子策略粒度幂等 + 失败重试 + 非撮合窗顺延。
    # 门: 月首交易日启动; 部分完成(列表记录)则每日 close 续做至齐备。
    if (not dry and done.get(month_key) != "all"
            and (_is_month_first_trading_day() or isinstance(done.get(month_key), list))):
        msgs.extend(_subtask_registry_pass(
            led, ctrl, "rebalance_done", month_key,
            _monthly_tasks(led, px, dry, fills_ok), tag="月度", fills_ok=fills_ok))
    # ERP 缩放执行侧: 周度/月度重建之后调用(重建清标记 → 此处重新应用 ×0.5; 据实 + 被拒次日补做)
    msgs.extend(erp_scale_pass(led, px, dry))
    # marks 在注册表/成交之后计算: 首日即可反映成交与费用，而不是先记全现金、拖到次日。
    marks = led.setdefault("marks", [])
    equity, ret = {}, {}
    for name, s in led["strategies"].items():
        eq = revalue(s, px)
        equity[name], ret[name] = eq, round((eq / CAPITAL - 1) * 100, 2)
    existing = next((m for m in marks if m["date"] == today), None)
    if existing:
        existing.update({"equity": equity, "return_pct": ret})
    else:
        marks.append({"date": today, "equity": equity, "return_pct": ret})
    # 本轮操作先并入 _actions 再出 summary: 收盘通知的"今日操作"必须含当轮成交,
    # 旧顺序 summary 先构建 → 通知声称"无调仓"而台账实际有交易。
    if not dry:
        acts = led.setdefault("_actions", {}).setdefault(today, [])
        led["_actions"][today] = sorted(set(acts) | set(msgs))
    summary = build_close_summary(led, px)
    if not dry:
        save_ledger(led)
        for m in msgs:
            notify(m, dedup_key=f"deck_close_{today}_{m[:12]}")
        notify(summary, dedup_key=f"deck_close_summary_{today}")
    return summary + ("\n调仓: " + "; ".join(msgs) if msgs else "\n无调仓")


def main() -> int:
    ap = argparse.ArgumentParser(description="多策略对比盘确定性执行器")
    ap.add_argument("--mode", choices=["intraday", "close"], required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="忽略交易时段判断(测试用)")
    args = ap.parse_args()
    if not acquire_lock():
        print("另一实例运行中, 跳过本轮")
        return 0
    try:
        return _main_inner(args)
    finally:
        release_lock()


def _session_gate(stamp: datetime | None, wall: datetime, force: bool):
    """会话闸（运行窗 + 撮合窗）: 返回 (运行日期, 时段基准, 成交闸, 拒绝原因)。

    - 运行日期只取墙钟: 旧行情戳不得倒拨（休市时行情戳停在过去）。
    - 时段基准: 交易所时间戳优先（本机时钟不可信）。
    - 成交闸 = force or _fills_open: 15:00 后新成交关闭, 15:10 后 catch-up 只结算。
    - 旧行情戳拒入 / 15:10 后 catch-up 仅交易日放行, 语义原样保留。
    拒绝原因非空时调用方跳过本轮。"""
    ex_date = wall.date()
    session_now = stamp or wall
    hm = session_now.hour * 100 + session_now.minute
    trading_ok = force or _fills_open(session_now)
    reason = ""
    if not force:
        stale_intraday = (stamp is not None and stamp.date() != ex_date and hm <= 1510)
        # 盘后 catch-up 必须**同属一个日历日**：旧戳的时分不得套用到新的一天。
        # 反例（2026-10-20 实测）：凌晨 00:22 腾讯仍返回**前一日**盘后戳（10-19 16:14，
        # hm=1614>1510）→ 原判据 after_close=True，配"运行日=10-20 是交易日"即放行整轮
        # 盘中（并把 event_scan_last 写成 "20261020_AM"，令当日上午扫描被节流跳过）。
        # 无戳时回退墙钟（其日期即 ex_date），故 stamp is None 仍然放行同日盘后 catch-up。
        after_close = hm > 1510 and (stamp is None or stamp.date() == ex_date)
        if after_close and not is_exchange_trading_day(ex_date):
            # 15:10 后 catch-up 只对交易日放行: 周末/节假日不得补结算写 marks
            reason = f"非交易日(交易所日历 {ex_date})，跳过"
        elif stale_intraday or (not is_trading_session(session_now) and not after_close):
            reason = f"非交易时段(交易所时间 {session_now:%m-%d %H:%M})，跳过"
    return ex_date, session_now, trading_ok, reason


def _main_inner(args: argparse.Namespace) -> int:
    global EX_DATE, _TRADING_OK, _session_now
    stamp = exchange_now()
    wall = datetime.now()
    EX_DATE, _session_now, _TRADING_OK, reject = _session_gate(stamp, wall, args.force)
    if reject:
        print(f"{reject} {args.mode} 轮")
        return 0
    out = run_intraday(args.dry_run) if args.mode == "intraday" else run_close(args.dry_run)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

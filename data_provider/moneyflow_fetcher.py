# -*- coding: utf-8 -*-
"""
A 股资金面数据源（直接对接 EastMoney push API，无需 token）。

设计目标：
1. 效率：直接调用 EastMoney push2 JSON API，绕过 akshare 的 HTML 爬虫与版本兼容层，
   单次请求耗时从 ~800ms 降到 ~150ms（含网络）。
2. 准确率：在原有 3 字段（main_net_inflow / inflow_5d / inflow_10d）基础上补全
   超大单/大单/中单/小单细分、连续净流入天数、北向资金、个股所属行业实际排名。
3. 可靠性：fail-open 设计，任一子接口失败不影响其他子接口；与 AkshareFundamentalAdapter
   互为 fallback（direct API 为主路径，akshare 为兜底）。

数据维度：
- 个股资金流（即时）：主力/超大单/大单/中单/小单 净流入 + 净流入占比
- 个股资金流（10 日）：按日序列，派生 5d/10d 累计 + 连续净流入/流出天数
- 行业资金流：所属行业排名 + top/bottom N
- 北向资金：沪股通/深股通 今日净买入 + 个股北向持股股数/市值/占比

参考字段编码（EastMoney push2 f-code）：
  f62 主力净流入 / f184 主力净流入占比
  f66 超大单净流入 / f69 超大单净流入占比
  f72 大单净流入 / f75 大单净流入占比
  f78 中单净流入 / f81 中单净流入占比
  f84 小单净流入 / f87 小单净流入占比
  f100 北向持股股数 / f101 北向持股市值 / f102 北向持股占比
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

import requests

from src.config import get_config

from .base import normalize_stock_code

logger = logging.getLogger(__name__)

# ConnectionError 等瞬态错误的单次重试间隔（秒）； ConnectionError 经常是 TCP 握手抖动
_RETRY_BACKOFF_SEC = 0.3

# EastMoney push2 接口域名（与 eastmoney_patch 已知的 allowlist 一致）
_STOCK_GET_URL = "https://push2.eastmoney.com/api/qt/stock/get"
_STOCK_FFLOW_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
_SECTOR_CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
_NORTHBOUND_RTMIN_URL = "https://push2.eastmoney.com/api/qt/kamt.rtmin/get"

# 个股资金流即时字段（主力/超大单/大单/中单/小单 + 占比）
_STOCK_FLOW_FIELDS = "f62,f184,f66,f69,f72,f75,f78,f81,f84,f87"
# 个股北向持股字段
_NORTHBOUND_HOLDING_FIELDS = "f100,f101,f102"

# 行业资金流（90 个行业）字段：代码/名称/主力净流入/超大单/大单/中单/小单
_SECTOR_FIELDS = "f12,f14,f62,f66,f72,f78,f84"

# 资金流 kline 字段（f51=日期，f52=主力，f53=小单，f54=中单，f55=大单，f56=超大单，f57=主力占比）
_FFLOW_KLINE_FIELDS1 = "f1,f2,f3,f7"
_FFLOW_KLINE_FIELDS2 = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"

_DEFAULT_TIMEOUT = 4.0
_DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def _to_eastmoney_secid(stock_code: str) -> Optional[str]:
    """将 A 股代码转换为 EastMoney secid 格式（1.SH / 0.SZ / 0.BJ）。

    返回 None 表示非 A 股代码（由调用方做 not_supported 处理）。
    """
    code = normalize_stock_code(stock_code)
    if not code or not code.isdigit() or len(code) != 6:
        return None
    # 上交所主板 60x / 68x (科创板) / 90x (B 股) / 11x (可转债等不算)
    if code.startswith(("60", "68", "90", "11", "13")):
        return f"1.{code}"
    # 深交所主板 000/002/300/301 + 北交所 8x/4x
    if code.startswith(("000", "001", "002", "003", "300", "301", "8", "43", "83", "87", "92")):
        return f"0.{code}"
    # 默认按深交所处理
    return f"0.{code}"


def _safe_float(value: Any) -> Optional[float]:
    """安全转 float；None / "-" / NaN / 非数值字符串返回 None。"""
    if value is None or value == "-":
        return None
    try:
        result = float(value)
        if result != result:  # NaN check
            return None
        return result
    except (TypeError, ValueError):
        return None


def _build_session() -> requests.Session:
    """构建 requests session 并按需应用 eastmoney_patch。

    eastmoney_patch 会全局 patch requests.Session.request，注入 EastMoney 授权令牌；
    与 AkShare 路径保持一致的开关语义（由 config.enable_eastmoney_patch 控制）。
    """
    try:
        cfg = get_config()
        if getattr(cfg, "enable_eastmoney_patch", False):
            from src.patches.eastmoney_patch import eastmoney_patch
            eastmoney_patch()
    except Exception:
        pass
    session = requests.Session()
    session.headers.update({
        "User-Agent": _DEFAULT_USER_AGENT,
        "Referer": "https://quote.eastmoney.com/",
        "Accept": "application/json, text/plain, */*",
    })
    return session


def _request_json(
    session: requests.Session,
    url: str,
    params: Dict[str, Any],
    timeout: float,
    block_name: str,
    *,
    retry_on_conn_error: bool = True,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """发起 JSON 请求，返回 (data, error)。fail-open，不抛异常。

    ConnectionError / ConnectionResetError 等瞬态错误会重试 1 次（间隔 _RETRY_BACKOFF_SEC）；
    其他 RequestException（含 Timeout）不重试，避免在超时分支放大延迟。
    """
    last_error: Optional[str] = None
    for attempt in range(2 if retry_on_conn_error else 1):
        try:
            resp = session.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            payload = resp.json()
        except requests.Timeout:
            return None, f"{block_name}:timeout"
        except (requests.ConnectionError, ConnectionResetError) as exc:
            last_error = f"{block_name}:request_error:{type(exc).__name__}"
            if attempt == 0 and retry_on_conn_error:
                time.sleep(_RETRY_BACKOFF_SEC)
                continue
            return None, last_error
        except requests.RequestException as exc:
            return None, f"{block_name}:request_error:{type(exc).__name__}"
        except ValueError as exc:
            return None, f"{block_name}:json_error:{type(exc).__name__}"
        if not isinstance(payload, dict):
            return None, f"{block_name}:invalid_payload"
        if payload.get("rc") not in (0, None, "0"):
            # EastMoney push2 rc != 0 通常表示业务级失败（如非交易时段无数据）
            return None, f"{block_name}:rc={payload.get('rc')}"
        return payload, None
    return None, last_error or f"{block_name}:retry_exhausted"


class MoneyFlowFetcher:
    """A 股资金面数据源（EastMoney push2 直接对接）。

    线程安全：无实例状态，可被多线程共享。所有方法 fail-open，返回 dict 不抛异常。
    """

    def __init__(
        self,
        *,
        timeout_seconds: Optional[float] = None,
        enabled: Optional[bool] = None,
    ) -> None:
        self._timeout = float(timeout_seconds) if timeout_seconds is not None else _DEFAULT_TIMEOUT
        # enabled 参数保留给 config 控制；默认 None 表示由调用方决定是否调用
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        """是否启用 MoneyFlow 数据源；显式参数优先于 config，缺省 True。"""
        if self._enabled is not None:
            return self._enabled
        try:
            cfg = get_config()
        except Exception:
            return True
        return bool(getattr(cfg, "moneyflow_api_enabled", True))

    @property
    def timeout_seconds(self) -> float:
        """单次请求超时（秒），由构造参数或默认值决定。"""
        return self._timeout

    # ------------------------------------------------------------------
    # 个股资金流
    # ------------------------------------------------------------------

    def get_stock_flow(self, stock_code: str) -> Dict[str, Any]:
        """返回个股资金流（即时汇总 + 10 日 kline 派生）。

        实时接口在非交易时段或部分股票上可能对 f66/f72/f78/f84 返回 None，
        此处用 10 日 kline 最新日数据兜底，确保超大单/大单/中单/小单四档始终可用。
        """
        result: Dict[str, Any] = {
            "status": "not_supported",
            "stock_flow": {},
            "source_chain": [],
            "errors": [],
        }
        if not self.enabled:
            result["errors"].append("moneyflow_api_disabled")
            return result

        secid = _to_eastmoney_secid(stock_code)
        if secid is None:
            result["errors"].append(f"invalid_a_share_code:{stock_code}")
            return result

        session = _build_session()

        # ---- 即时汇总（主力/超大单/大单/中单/小单）----
        realtime_payload, rt_err = _request_json(
            session,
            _STOCK_GET_URL,
            {
                "secid": secid,
                "fields": _STOCK_FLOW_FIELDS,
                "fltt": 2,  # 2=元，1=万元
            },
            self._timeout,
            "stock_flow_realtime",
        )
        if rt_err:
            result["errors"].append(rt_err)
        if realtime_payload and isinstance(realtime_payload.get("data"), dict):
            data = realtime_payload["data"]
            main_net = _safe_float(data.get("f62"))
            super_large = _safe_float(data.get("f66"))
            large = _safe_float(data.get("f72"))
            medium = _safe_float(data.get("f78"))
            small = _safe_float(data.get("f84"))
            result["stock_flow"].update({
                "main_net_inflow": main_net,
                "main_net_inflow_pct": _safe_float(data.get("f184")),
                "super_large_net_inflow": super_large,
                "super_large_net_inflow_pct": _safe_float(data.get("f69")),
                "large_net_inflow": large,
                "large_net_inflow_pct": _safe_float(data.get("f75")),
                "medium_net_inflow": medium,
                "medium_net_inflow_pct": _safe_float(data.get("f81")),
                "small_net_inflow": small,
                "small_net_inflow_pct": _safe_float(data.get("f87")),
            })
            result["source_chain"].append("moneyflow:stock_flow_realtime")

        # ---- 10 日 kline（派生 5d/10d 累计 + 连续净流入天数 + 实时字段兜底）----
        kline_payload, kl_err = _request_json(
            session,
            _STOCK_FFLOW_KLINE_URL,
            {
                "secid": secid,
                "klt": 101,  # 日 K
                "lmt": 10,
                "fields1": _FFLOW_KLINE_FIELDS1,
                "fields2": _FFLOW_KLINE_FIELDS2,
            },
            self._timeout,
            "stock_flow_kline",
        )
        if kl_err:
            result["errors"].append(kl_err)
        klines = self._extract_klines(kline_payload)
        if klines:
            inflow_5d = self._sum_main_inflow(klines, days=5)
            inflow_10d = self._sum_main_inflow(klines, days=10)
            consecutive_days = self._consecutive_inflow_days(klines)
            result["stock_flow"].update({
                "inflow_5d": inflow_5d,
                "inflow_10d": inflow_10d,
                "consecutive_inflow_days": consecutive_days,
                "latest_flow_date": klines[-1].get("date") if klines else None,
            })
            # 实时接口 f62/f66/f72/f78/f84 在非交易时段或缺数据时可能返回 None 或
            # 占位符（实测 stock/get 在缺数据时 f62 返回 1 元、f78 返回 0 元），
            # 用 kline 最新日的对应字段兜底，保证四档细分始终可用。
            latest_kline = klines[-1] if klines else None
            if latest_kline:
                amount_fallback_map = (
                    ("super_large_net_inflow", "super_large_net_inflow"),
                    ("large_net_inflow", "large_net_inflow"),
                    ("medium_net_inflow", "medium_net_inflow"),
                    ("small_net_inflow", "small_net_inflow"),
                )
                for target_key, kline_key in amount_fallback_map:
                    if result["stock_flow"].get(target_key) is None:
                        kline_val = latest_kline.get(kline_key)
                        if kline_val is not None:
                            result["stock_flow"][target_key] = kline_val
                # main_net_inflow 兜底：None 或异常小值（EastMoney stock/get 在缺数据时
                # 返回 1 元占位符，与 main_net_inflow_pct 严重不一致）；kline 是日终确认值。
                main_current = result["stock_flow"].get("main_net_inflow")
                if main_current is None or (
                    isinstance(main_current, (int, float)) and main_current <= 1.0
                ):
                    kline_main = latest_kline.get("main_net_inflow")
                    if kline_main is not None:
                        result["stock_flow"]["main_net_inflow"] = kline_main
                # 占比字段兜底：*_pct 为 None 时从 kline 最新日派生（f57-f61）
                pct_fallback_map = (
                    ("main_net_inflow_pct", "main_net_inflow_pct"),
                    ("super_large_net_inflow_pct", "super_large_net_inflow_pct"),
                    ("large_net_inflow_pct", "large_net_inflow_pct"),
                    ("medium_net_inflow_pct", "medium_net_inflow_pct"),
                    ("small_net_inflow_pct", "small_net_inflow_pct"),
                )
                for target_key, kline_key in pct_fallback_map:
                    if result["stock_flow"].get(target_key) is None:
                        kline_val = latest_kline.get(kline_key)
                        if kline_val is not None:
                            result["stock_flow"][target_key] = kline_val
            result["source_chain"].append("moneyflow:stock_flow_kline")

        # 状态判定
        if result["stock_flow"]:
            # 任一关键字段非空即视为 partial；如同时拿到即时 + kline 视为 ok
            has_realtime = any(
                result["stock_flow"].get(k) is not None
                for k in ("main_net_inflow", "super_large_net_inflow")
            )
            has_kline = result["stock_flow"].get("inflow_5d") is not None
            result["status"] = "ok" if (has_realtime and has_kline) else "partial"
        return result

    @staticmethod
    def _extract_klines(payload: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """从 fflow/daykline 响应中提取按日资金流序列（含金额与占比）。

        字段顺序对应 `_FFLOW_KLINE_FIELDS2 = f51..f61`：
          f51 date, f52 main, f53 small, f54 medium, f55 large, f56 super_large,
          f57 main_pct, f58 small_pct, f59 medium_pct, f60 large_pct, f61 super_large_pct

        占比字段（f57-f61）按需解析；旧数据或部分源不返回占比时对应字段为 None。
        """
        if not payload or not isinstance(payload.get("data"), dict):
            return []
        klines_raw = payload["data"].get("klines")
        if not isinstance(klines_raw, list):
            return []
        parsed: List[Dict[str, Any]] = []
        for item in klines_raw:
            if not isinstance(item, str):
                continue
            parts = item.split(",")
            if len(parts) < 6:
                continue
            try:
                entry: Dict[str, Any] = {
                    "date": parts[0],
                    "main_net_inflow": _safe_float(parts[1]),
                    "small_net_inflow": _safe_float(parts[2]),
                    "medium_net_inflow": _safe_float(parts[3]),
                    "large_net_inflow": _safe_float(parts[4]),
                    "super_large_net_inflow": _safe_float(parts[5]),
                }
                # 占比字段 f57-f61 按需解析；缺字段时保持 None
                if len(parts) > 6:
                    entry["main_net_inflow_pct"] = _safe_float(parts[6])
                if len(parts) > 7:
                    entry["small_net_inflow_pct"] = _safe_float(parts[7])
                if len(parts) > 8:
                    entry["medium_net_inflow_pct"] = _safe_float(parts[8])
                if len(parts) > 9:
                    entry["large_net_inflow_pct"] = _safe_float(parts[9])
                if len(parts) > 10:
                    entry["super_large_net_inflow_pct"] = _safe_float(parts[10])
                parsed.append(entry)
            except (IndexError, ValueError):
                continue
        return parsed

    @staticmethod
    def _sum_main_inflow(klines: List[Dict[str, Any]], days: int) -> Optional[float]:
        """对最近 N 日的主力净流入求和；全为 None 时返回 None。"""
        if not klines:
            return None
        tail = klines[-days:]
        total = 0.0
        has_value = False
        for row in tail:
            v = row.get("main_net_inflow")
            if v is not None:
                total += v
                has_value = True
        return total if has_value else None

    @staticmethod
    def _consecutive_inflow_days(klines: List[Dict[str, Any]]) -> Optional[int]:
        """从最近一日向前数连续净流入（正数）或净流出（负数）天数。"""
        if not klines:
            return None
        latest = klines[-1].get("main_net_inflow")
        if latest is None or latest == 0:
            return 0
        direction = 1 if latest > 0 else -1
        count = 0
        for row in reversed(klines):
            v = row.get("main_net_inflow")
            if v is None:
                break
            if direction > 0 and v > 0:
                count += 1
            elif direction < 0 and v < 0:
                count += 1
            else:
                break
        return direction * count

    # ------------------------------------------------------------------
    # 行业资金流
    # ------------------------------------------------------------------

    def get_sector_flow(self, stock_code: str, top_n: int = 5) -> Dict[str, Any]:
        """返回行业资金流排行 + 个股所属行业的实际排名。"""
        result: Dict[str, Any] = {
            "status": "not_supported",
            "sector_rankings": {"top": [], "bottom": []},
            "stock_industry": {},
            "source_chain": [],
            "errors": [],
        }
        if not self.enabled:
            result["errors"].append("moneyflow_api_disabled")
            return result

        secid = _to_eastmoney_secid(stock_code)
        if secid is None:
            result["errors"].append(f"invalid_a_share_code:{stock_code}")
            return result

        session = _build_session()

        # 取行业资金流排行（按主力净流入降序，pz=200 覆盖全部 90 个行业）
        # fs 取 "m:90+t:2"（行业板块），requests 会自动 URL-encode 加号为 %2B；
        # EastMoney 同时接受空格与 + 分隔，这里用 + 是与官方前端 quote.eastmoney.com 一致的写法。
        payload, err = _request_json(
            session,
            _SECTOR_CLIST_URL,
            {
                "fid": "f62",  # 按 f62 主力净流入排序
                "po": 1,        # 降序
                "pz": 200,
                "pn": 1,
                "fs": "m:90+t:2",  # 行业板块
                "fields": _SECTOR_FIELDS,
                "fltt": 2,
            },
            self._timeout,
            "sector_flow_ranking",
        )
        if err:
            result["errors"].append(err)
            return result

        # 兼容 EastMoney 不同响应形态：data 可能是 None / dict / dict.diff 缺失
        raw_data = payload.get("data") if isinstance(payload, dict) else None
        if raw_data is None:
            result["errors"].append("sector_flow_ranking:empty_data")
            return result
        rows = raw_data.get("diff") if isinstance(raw_data, dict) else None
        if not isinstance(rows, list) or not rows:
            result["errors"].append("sector_flow_ranking:empty_diff")
            return result

        # 解析全部行业排行
        sectors: List[Dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            sectors.append({
                "code": str(row.get("f12") or ""),
                "name": str(row.get("f14") or ""),
                "main_net_inflow": _safe_float(row.get("f62")),
                "super_large_net_inflow": _safe_float(row.get("f66")),
                "large_net_inflow": _safe_float(row.get("f72")),
                "medium_net_inflow": _safe_float(row.get("f78")),
                "small_net_inflow": _safe_float(row.get("f84")),
            })

        if not sectors:
            result["errors"].append("sector_flow_ranking:no_valid_rows")
            return result

        result["sector_rankings"]["top"] = sectors[:top_n]
        result["sector_rankings"]["bottom"] = list(reversed(sectors[-top_n:]))
        result["source_chain"].append("moneyflow:sector_flow_ranking")

        # 查询个股所属行业（通过 stock/get 的 f127 行业代码字段）
        industry_payload, ind_err = _request_json(
            session,
            _STOCK_GET_URL,
            {
                "secid": secid,
                "fields": "f127,f128,f135,f136",  # f127=所属行业板块代码, f128=所属行业板块名
            },
            self._timeout,
            "stock_industry_lookup",
        )
        if ind_err:
            result["errors"].append(ind_err)
        if industry_payload and isinstance(industry_payload.get("data"), dict):
            data = industry_payload["data"]
            industry_code = str(data.get("f127") or "")
            industry_name = str(data.get("f128") or "")
            if industry_code or industry_name:
                # 在已查到的全部行业排行中找到该股所属行业的实际排名
                rank = None
                for idx, sec in enumerate(sectors, start=1):
                    if (industry_code and sec["code"] == industry_code) or (
                        industry_name and sec["name"] == industry_name
                    ):
                        rank = idx
                        break
                result["stock_industry"] = {
                    "code": industry_code,
                    "name": industry_name,
                    "rank_in_flow": rank,  # 1=资金流入最强，N=最弱
                    "total_industries": len(sectors),
                    "industry_main_net_inflow": next(
                        (s["main_net_inflow"] for s in sectors
                         if (industry_code and s["code"] == industry_code)
                         or (industry_name and s["name"] == industry_name)),
                        None,
                    ),
                }
                result["source_chain"].append("moneyflow:stock_industry_lookup")

        result["status"] = "ok" if result["sector_rankings"]["top"] else "partial"
        return result

    # ------------------------------------------------------------------
    # 北向资金
    # ------------------------------------------------------------------

    def get_northbound_flow(self, stock_code: str) -> Dict[str, Any]:
        """返回北向资金（沪股通/深股通今日净买入 + 个股北向持股）。"""
        result: Dict[str, Any] = {
            "status": "not_supported",
            "total_flow": {},
            "stock_holding": {},
            "source_chain": [],
            "errors": [],
        }
        if not self.enabled:
            result["errors"].append("moneyflow_api_disabled")
            return result

        secid = _to_eastmoney_secid(stock_code)
        if secid is None:
            result["errors"].append(f"invalid_a_share_code:{stock_code}")
            return result

        session = _build_session()

        # 北向资金今日总量（沪股通 + 深股通）
        nb_payload, nb_err = _request_json(
            session,
            _NORTHBOUND_RTMIN_URL,
            {
                "fields1": "f1,f2,f3,f4",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64",
            },
            self._timeout,
            "northbound_total",
        )
        if nb_err:
            result["errors"].append(nb_err)
        if nb_payload and isinstance(nb_payload.get("data"), dict):
            # 取最近一条记录（实时分钟数据）
            s2n = nb_payload["data"].get("s2n")
            if isinstance(s2n, list) and s2n:
                latest = s2n[-1]
                if isinstance(latest, str):
                    parts = latest.split(",")
                    # f51=时间, f52=沪股通净买入, f53=深股通净买入, f54=北向合计, ...
                    if len(parts) >= 4:
                        result["total_flow"] = {
                            "sh_connect_net_buy": _safe_float(parts[1]) if len(parts) > 1 else None,
                            "sz_connect_net_buy": _safe_float(parts[2]) if len(parts) > 2 else None,
                            "total_net_buy": _safe_float(parts[3]) if len(parts) > 3 else None,
                            "timestamp": parts[0] if parts else None,
                        }
                        result["source_chain"].append("moneyflow:northbound_total")

        # 个股北向持股
        holding_payload, h_err = _request_json(
            session,
            _STOCK_GET_URL,
            {
                "secid": secid,
                "fields": _NORTHBOUND_HOLDING_FIELDS,
            },
            self._timeout,
            "northbound_holding",
        )
        if h_err:
            result["errors"].append(h_err)
        if holding_payload and isinstance(holding_payload.get("data"), dict):
            data = holding_payload["data"]
            shares = _safe_float(data.get("f100"))  # 北向持股股数
            market_value = _safe_float(data.get("f101"))  # 北向持股市值
            holding_pct = _safe_float(data.get("f102"))  # 北向持股占比
            if any(v is not None for v in (shares, market_value, holding_pct)):
                result["stock_holding"] = {
                    "shares": shares,
                    "market_value": market_value,
                    "holding_pct": holding_pct,
                }
                result["source_chain"].append("moneyflow:northbound_holding")

        has_total = bool(result["total_flow"])
        has_holding = bool(result["stock_holding"])
        if has_total and has_holding:
            result["status"] = "ok"
        elif has_total or has_holding:
            result["status"] = "partial"
        return result

    # ------------------------------------------------------------------
    # 综合入口
    # ------------------------------------------------------------------

    def get_capital_flow(
        self,
        stock_code: str,
        *,
        top_n: int = 5,
    ) -> Dict[str, Any]:
        """返回完整资金面上下文（个股 + 行业 + 北向）。

        与 AkshareFundamentalAdapter.get_capital_flow 的输出 schema 对齐，
        在 stock_flow / sector_rankings 基础上新增 northbound / stock_industry 块。
        任一子接口失败不影响其他子接口（fail-open）。

        默认串行执行 3 个子方法，避免 EastMoney push2 在并发同源请求时触发
        连接级限流（实测 3 并发会触发 ConnectionError 风暴）。如需并行可通过
        `MONEYFLOW_PARALLEL_ENABLED=true` 显式开启，仅在稳定网络与启用
        eastmoney_patch 的环境下收益明显。
        """
        start = time.time()

        parallel_enabled = False
        try:
            cfg = get_config()
            parallel_enabled = bool(getattr(cfg, "moneyflow_parallel_enabled", False))
        except Exception:
            parallel_enabled = False

        if parallel_enabled:
            stock_result, sector_result, nb_result = self._run_submethods_parallel(stock_code, top_n)
        else:
            stock_result, sector_result, nb_result = self._run_submethods_sequential(stock_code, top_n)

        cost_ms = int((time.time() - start) * 1000)

        # 合并状态：任一 ok 即视为有数据
        statuses = [stock_result.get("status"), sector_result.get("status"), nb_result.get("status")]
        if any(s == "ok" for s in statuses):
            merged_status = "ok"
        elif any(s == "partial" for s in statuses):
            merged_status = "partial"
        else:
            merged_status = "not_supported"

        merged: Dict[str, Any] = {
            "status": merged_status,
            "stock_flow": stock_result.get("stock_flow", {}),
            "sector_rankings": sector_result.get("sector_rankings", {"top": [], "bottom": []}),
            "stock_industry": sector_result.get("stock_industry", {}),
            "northbound": {
                "total_flow": nb_result.get("total_flow", {}),
                "stock_holding": nb_result.get("stock_holding", {}),
            },
            "source_chain": (
                stock_result.get("source_chain", [])
                + sector_result.get("source_chain", [])
                + nb_result.get("source_chain", [])
            ),
            "errors": (
                stock_result.get("errors", [])
                + sector_result.get("errors", [])
                + nb_result.get("errors", [])
            ),
            "duration_ms": cost_ms,
        }
        return merged

    def _run_submethods_sequential(
        self, stock_code: str, top_n: int
    ) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
        """串行执行 3 个子方法（默认路径，对 EastMoney push2 限流最友好）。"""
        try:
            stock_result = self.get_stock_flow(stock_code)
        except Exception as exc:  # pragma: no cover - 防御性兜底
            stock_result = {
                "status": "not_supported",
                "stock_flow": {},
                "source_chain": [],
                "errors": [f"stock_flow:uncaught:{type(exc).__name__}"],
            }
        try:
            sector_result = self.get_sector_flow(stock_code, top_n=top_n)
        except Exception as exc:  # pragma: no cover
            sector_result = {
                "status": "not_supported",
                "sector_rankings": {"top": [], "bottom": []},
                "stock_industry": {},
                "source_chain": [],
                "errors": [f"sector_flow:uncaught:{type(exc).__name__}"],
            }
        try:
            nb_result = self.get_northbound_flow(stock_code)
        except Exception as exc:  # pragma: no cover
            nb_result = {
                "status": "not_supported",
                "total_flow": {},
                "stock_holding": {},
                "source_chain": [],
                "errors": [f"northbound:uncaught:{type(exc).__name__}"],
            }
        return stock_result, sector_result, nb_result

    def _run_submethods_parallel(
        self, stock_code: str, top_n: int
    ) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
        """并行执行 3 个子方法（opt-in，仅在稳定网络 + eastmoney_patch 启用时收益明显）。"""
        stock_result: Optional[Dict[str, Any]] = None
        sector_result: Optional[Dict[str, Any]] = None
        nb_result: Optional[Dict[str, Any]] = None

        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="moneyflow") as executor:
            future_stock = executor.submit(self._safe_call, self.get_stock_flow, stock_code)
            future_sector = executor.submit(
                self._safe_call, self._call_sector_flow, stock_code, top_n
            )
            future_nb = executor.submit(self._safe_call, self.get_northbound_flow, stock_code)
            for future, label in (
                (future_stock, "stock_flow"),
                (future_sector, "sector_flow"),
                (future_nb, "northbound"),
            ):
                try:
                    res = future.result()
                except Exception as exc:  # pragma: no cover - 防御性兜底
                    res = {
                        "status": "not_supported",
                        "errors": [f"{label}:uncaught:{type(exc).__name__}"],
                    }
                if label == "stock_flow":
                    stock_result = res
                elif label == "sector_flow":
                    sector_result = res
                else:
                    nb_result = res

        stock_result = stock_result or {
            "status": "not_supported", "stock_flow": {},
            "source_chain": [], "errors": ["stock_flow:no_result"],
        }
        sector_result = sector_result or {
            "status": "not_supported",
            "sector_rankings": {"top": [], "bottom": []},
            "stock_industry": {},
            "source_chain": [],
            "errors": ["sector_flow:no_result"],
        }
        nb_result = nb_result or {
            "status": "not_supported",
            "total_flow": {}, "stock_holding": {},
            "source_chain": [], "errors": ["northbound:no_result"],
        }
        return stock_result, sector_result, nb_result

    @staticmethod
    def _safe_call(fn, *args, **kwargs) -> Dict[str, Any]:
        """子方法调用的安全包装：fail-open，将异常转为 not_supported + 错误信息。"""
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # pragma: no cover - 防御性兜底
            return {
                "status": "not_supported",
                "errors": [f"{getattr(fn, '__name__', 'unknown')}:uncaught:{type(exc).__name__}"],
            }

    def _call_sector_flow(self, stock_code: str, top_n: int) -> Dict[str, Any]:
        """ThreadPoolExecutor 提交用的薄封装，便于 _safe_call 取 __name__。"""
        return self.get_sector_flow(stock_code, top_n=top_n)

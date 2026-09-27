# -*- coding: utf-8 -*-
"""策略准入裁决管道（v4.2 计划 P3：strategy_intake）。

跨项目边界（AGENTS.md / domain.md）：dsa 与 quant_research 独立运行、
禁止互相 import——策略从"研究"到"实盘授权"只走**文件契约**：
quant_research 侧产出回测裁决报告 JSON，dsa 侧在此校验并推进注册表状态。

职责（不打通代码，只消费文件）：
  - 裁决报告 schema 校验（schema_version / 必填字段 / verdict 合法性）；
  - **数据新鲜度门**（批评#4）：data_as_of 滞后超阈值 → 拒绝 promote 并告警，
    不静默用过期数据裁决（E2 分位在生产已滞后两天，正是此门要拦的场景）；
  - register / promote / demote 状态推进 strategies.yaml（可审计），
    状态机对齐 strategy-catalog.md：registered→shadow→active，任意→dormant。

铁律：LLM 不进裁决；准入是规则性校验 + 阈值判断，全部可复算。
失败一律拒绝推进并给明确理由，绝不"放行以免麻烦"（fail-closed）。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 跨项目文件契约：quant 侧把裁决报告写到此目录，dsa 侧消费（不 import 代码）
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INTAKE_DIR = (
    PROJECT_ROOT.parent / "quant_research" / "output" / "strategy_intake"
)
DEFAULT_REGISTRY_PATH = PROJECT_ROOT.parent / "strategies.yaml"

# 裁决报告契约（quant 侧产出、dsa 侧消费）
INTAKE_SCHEMA = "strategy_intake_verdict_v1"
REQUIRED_FIELDS = (
    "schema_version", "strategy_id", "pit_end", "data_as_of",
    "generated_at", "oos_window", "metrics", "verdict",
)
VALID_VERDICTS = ("promote", "hold", "reject")

# 状态机（对齐 strategy-catalog.md）
STATUS_REGISTERED = "registered"
STATUS_SHADOW = "shadow"
STATUS_ACTIVE = "active"
STATUS_DORMANT = "dormant"
STATUS_REJECTED = "rejected"

# promote 允许的状态跃迁（不允许 registered 直接跳 active——必须过 shadow）
PROMOTE_PATH = {STATUS_REGISTERED: STATUS_SHADOW, STATUS_SHADOW: STATUS_ACTIVE}

# 状态层级（promotion_cap 上限判断用；数字越大级别越高）
STATUS_RANK = {STATUS_REGISTERED: 0, STATUS_SHADOW: 1, STATUS_ACTIVE: 2}

# 数据新鲜度门：裁决数据滞后超过此交易日数 → 拒绝 promote
DEFAULT_DATA_STALE_DAYS = 5


@dataclass
class IntakeResult:
    """一次准入裁决的结果（可审计）。"""
    strategy_id: str
    accepted: bool                       # 报告是否通过校验
    action: str = "none"                 # promoted / demoted / held / rejected / none
    from_status: str = ""
    to_status: str = ""
    reasons: List[str] = field(default_factory=list)
    verdict: str = ""
    data_as_of: str = ""
    stale_days: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_id": self.strategy_id, "accepted": self.accepted,
            "action": self.action, "from_status": self.from_status,
            "to_status": self.to_status, "reasons": list(self.reasons),
            "verdict": self.verdict, "data_as_of": self.data_as_of,
            "stale_days": self.stale_days,
        }


# ── 报告校验 ────────────────────────────────────────────────
PLACEHOLDER_APPEND_BELOW = None


def _count_trading_days_gap(data_as_of: str, today: Optional[date] = None) -> Optional[int]:
    """data_as_of 距今的交易日数（复用 trading_calendar 逐日计数；失败退化为自然日）。"""
    from datetime import timedelta
    today = today or date.today()
    try:
        as_of = date.fromisoformat(str(data_as_of)[:10])
    except (ValueError, TypeError):
        return None
    if as_of >= today:
        return 0
    try:
        from src.core.trading_calendar import is_market_open
        gap = 0
        cursor = as_of + timedelta(days=1)
        while cursor <= today:
            if is_market_open("cn", cursor):
                gap += 1
            cursor += timedelta(days=1)
        return gap
    except Exception:  # noqa: BLE001 - 日历不可用退化为自然日估算
        return (today - as_of).days


def validate_report(report: Any) -> Tuple[bool, List[str]]:
    """校验裁决报告结构合法性（不含新鲜度门——那是 promote 时的独立门）。

    Returns (ok, reasons)。fail-closed：任何结构问题都拒绝。
    """
    reasons: List[str] = []
    if not isinstance(report, dict):
        return False, ["报告非 JSON 对象"]
    if report.get("schema_version") != INTAKE_SCHEMA:
        reasons.append(
            f"schema_version 不匹配（期望 {INTAKE_SCHEMA}，实为 "
            f"{report.get('schema_version')!r}）")
    for f in REQUIRED_FIELDS:
        if f not in report:
            reasons.append(f"缺必填字段 {f}")
    verdict = str(report.get("verdict") or "")
    if verdict and verdict not in VALID_VERDICTS:
        reasons.append(f"verdict 非法：{verdict!r}（应为 {VALID_VERDICTS}）")
    metrics = report.get("metrics")
    if metrics is not None and not isinstance(metrics, dict):
        reasons.append("metrics 必须为对象")
    return (not reasons), reasons


def load_report(path: Path) -> Optional[Dict[str, Any]]:
    """读裁决报告 JSON；缺失/损坏 → None（记日志，不抛）。"""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("[strategy-intake] 裁决报告读取失败 %s: %r", path, exc)
        return None


# ── 状态机推进 ──────────────────────────────────────────────

def _load_registry(path: Path) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    import yaml
    with Path(path).open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    entries = [e for e in (data.get("strategies") or []) if isinstance(e, dict)]
    return data, entries


def _save_registry(path: Path, data: Dict[str, Any]) -> None:
    import yaml
    tmp = Path(path).with_suffix(".yaml.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False,
                       default_flow_style=False)
    import os
    os.replace(tmp, path)


def _find_entry(entries: List[Dict[str, Any]], strategy_id: str) -> Optional[Dict[str, Any]]:
    for e in entries:
        if str(e.get("id")) == strategy_id:
            return e
    return None


def evaluate_intake(
    report: Dict[str, Any],
    *,
    registry_path: Path,
    stale_days_threshold: int = DEFAULT_DATA_STALE_DAYS,
    apply: bool = False,
    today: Optional[date] = None,
) -> IntakeResult:
    """消费一份裁决报告，按 verdict + 新鲜度门推进注册表状态。

    - verdict=reject → demote 到 rejected；
    - verdict=hold  → 不推进（保持现状）；
    - verdict=promote → 先过**数据新鲜度门**（data_as_of 滞后超阈值则拒绝
      并告警），再按 PROMOTE_PATH 推进一档（registered→shadow→active）。
    apply=False 时只裁决不落盘（dry-run），apply=True 才写 strategies.yaml。
    """
    strategy_id = str(report.get("strategy_id") or "")
    ok, reasons = validate_report(report)
    result = IntakeResult(strategy_id=strategy_id, accepted=ok, reasons=reasons,
                          verdict=str(report.get("verdict") or ""),
                          data_as_of=str(report.get("data_as_of") or ""))
    if not ok:
        result.action = "rejected"
        return result

    _data, entries = _load_registry(registry_path)
    entry = _find_entry(entries, strategy_id)
    if entry is None:
        result.accepted = False
        result.action = "none"
        result.reasons.append(f"注册表无此策略：{strategy_id}")
        return result

    cur = str(entry.get("status") or "").lower()
    result.from_status = cur
    verdict = result.verdict

    if verdict == "reject":
        result.action = "demoted"
        result.to_status = STATUS_REJECTED
        result.reasons.append("裁决 reject → rejected")
        if apply:
            entry["status"] = STATUS_REJECTED
            _save_registry(registry_path, _data)
        return result

    if verdict == "hold":
        result.action = "held"
        result.to_status = cur
        result.reasons.append("裁决 hold → 保持现状")
        return result

    # verdict == promote：先过数据新鲜度门
    stale = _count_trading_days_gap(result.data_as_of, today)
    result.stale_days = stale
    if stale is None:
        result.action = "rejected"
        result.to_status = cur
        result.reasons.append(f"data_as_of 无法解析：{result.data_as_of!r}——拒绝 promote")
        return result
    if stale > stale_days_threshold:
        result.action = "rejected"
        result.to_status = cur
        result.reasons.append(
            f"数据新鲜度门拦截：data_as_of {result.data_as_of} 滞后 {stale} "
            f"交易日（阈值 {stale_days_threshold}）——拒绝 promote 并告警")
        logger.warning("[strategy-intake] %s promote 被新鲜度门拦截（滞后 %s 日）",
                       strategy_id, stale)
        return result

    nxt = PROMOTE_PATH.get(cur)
    if nxt is None:
        result.action = "held"
        result.to_status = cur
        result.reasons.append(
            f"当前状态 {cur} 无 promote 路径（已 active 或非 registered/shadow）")
        return result

    # promotion_cap（生产者声明的推进上限，fail-closed）：报告内嵌 cap（如
    # shadow）时，超过上限的跃迁一律拒绝——"shadow→active 需独立证据"不再
    # 只是文档承诺（生产端 build_intake_verdict 默认 promotion_cap=shadow）。
    # cap 值非法同样 fail-closed；缺省无 cap = 兼容既有报告。
    cap = str(report.get("promotion_cap") or "").strip().lower()
    if cap:
        if cap not in STATUS_RANK:
            result.action = "held"
            result.to_status = cur
            result.reasons.append(
                f"promotion_cap 值非法：{cap!r}（应为 registered/shadow/active）"
                "——fail-closed 不推进")
            return result
        if STATUS_RANK.get(nxt, 99) > STATUS_RANK[cap]:
            result.action = "held"
            result.to_status = cur
            result.reasons.append(
                f"promotion_cap={cap} 限制：{cur} → {nxt} 超出报告声明的推进"
                "上限——更高级别需独立证据（本报告不授权）")
            return result

    result.action = "promoted"
    result.to_status = nxt
    result.reasons.append(f"裁决 promote + 数据新鲜（滞后 {stale} 日）：{cur} → {nxt}")
    if apply:
        entry["status"] = nxt
        _save_registry(registry_path, _data)
    return result


def set_registry_status(
    strategy_id: str,
    status: str,
    *,
    registry_path: Path,
) -> bool:
    """把注册表中某策略状态改为指定值（幂等，仅当存在且不同才写盘）。

    autostop 暂停某策略时调用（status=dormant），使"运行态暂停"与
    "注册表状态"打通——此前 autostop 用独立表、与注册表 dormant 脱节。
    返回是否发生实际写入。策略不存在或状态已一致 → False。
    """
    try:
        _data, entries = _load_registry(registry_path)
    except Exception as exc:  # noqa: BLE001 - 注册表不可读不阻塞 autostop
        logger.warning("[strategy-intake] 注册表读取失败，跳过状态同步: %r", exc)
        return False
    entry = _find_entry(entries, strategy_id)
    if entry is None:
        return False
    if str(entry.get("status") or "").lower() == status.lower():
        return False
    entry["status"] = status
    try:
        _save_registry(registry_path, _data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[strategy-intake] 注册表状态同步写盘失败: %r", exc)
        return False
    logger.info("[strategy-intake] %s 注册表状态 → %s", strategy_id, status)
    return True


def process_intake_dir(
    intake_dir: Path,
    *,
    registry_path: Path,
    stale_days_threshold: int = DEFAULT_DATA_STALE_DAYS,
    apply: bool = False,
    today: Optional[date] = None,
) -> List[IntakeResult]:
    """批量消费一个目录下的所有裁决报告 JSON（*.json），逐一裁决。

    单份报告失败只记该份、不影响其余（fail-closed 到该策略不推进）。
    """
    results: List[IntakeResult] = []
    d = Path(intake_dir)
    if not d.exists():
        logger.info("[strategy-intake] 裁决目录不存在: %s", d)
        return results
    for f in sorted(d.glob("*.json")):
        report = load_report(f)
        if report is None:
            results.append(IntakeResult(
                strategy_id=f.stem, accepted=False, action="rejected",
                reasons=[f"报告读取失败：{f.name}"]))
            continue
        try:
            results.append(evaluate_intake(
                report, registry_path=registry_path,
                stale_days_threshold=stale_days_threshold,
                apply=apply, today=today))
        except Exception as exc:  # noqa: BLE001 - 单报告失败不拖垮批处理
            logger.warning("[strategy-intake] 裁决 %s 失败: %r", f.name, exc)
            results.append(IntakeResult(
                strategy_id=str(report.get("strategy_id") or f.stem),
                accepted=False, action="rejected",
                reasons=[f"裁决异常：{exc}"]))
    return results

"""实验注册层：每次回测/扫描/验证 = 一条可追溯的注册记录

重构 R4（2026-08-29）：痛点 5（实验结果散落 output/*.csv、试验次数
无法机器追溯）。qlib Recorder 语义的轻量实现：

- run_id = UTC 时间戳 + 参数哈希前 8 位（同参重跑可辨别）
- output/runs/<run_id>/meta.json：params / metrics / git SHA /
  数据快照 SHA（调用方提供） / 时间戳 / 标签
- output/runs/index.jsonl：每次运行追加一行（试验数 N 的机器来源，
  供 P0-2 DSR 把"试过多少次"算进及格线）

不引 MLflow：单机文件系统足够，接口形状与其对齐，后期可迁移。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


def _git_sha() -> Optional[str]:
    """当前 commit SHA；非 git 环境返回 None（不致命）"""
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip() or None
    except Exception:
        return None


def _params_hash(params: Mapping[str, Any]) -> str:
    payload = json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]


@dataclass
class ExperimentRun:
    """一次注册运行的描述"""

    run_id: str
    params: dict = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)
    artifacts: dict = field(default_factory=dict)  # {名称: 路径字符串}
    tags: dict = field(default_factory=dict)
    timestamp: str = ""
    git_sha: Optional[str] = None
    status: str = "completed"


# 运行状态：completed=产出有效结果；failed=运行崩溃/未产出；smoke=冒烟
# （合成数据/连通性检查）。DSR 的 N 只数 completed——但注意诚实边界：
# 一次"跑完且结果难看"的实验仍是 completed，禁止为缩小 N 标成 failed。
VALID_STATUSES = ("completed", "failed", "smoke")


def record_run(
    params: Mapping[str, Any],
    metrics: Optional[Mapping[str, Any]] = None,
    artifacts: Optional[Mapping[str, str]] = None,
    tags: Optional[Mapping[str, Any]] = None,
    runs_dir: Optional[Path] = None,
    status: str = "completed",
) -> ExperimentRun:
    """注册一次运行：写 meta.json + 追加 index.jsonl，返回 run 对象

    params/metrics 必须可 JSON 序列化（不可序列化的值用 default=str 兜底）。
    status 取值见 VALID_STATUSES；非法值直接拒绝（宁可崩不可静默混入 N）。
    """
    if status not in VALID_STATUSES:
        raise ValueError(f"status 须为 {VALID_STATUSES} 之一，收到 {status!r}")
    root = Path(runs_dir) if runs_dir else _default_runs_dir()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    run_id = f"{ts}_{_params_hash(params)}"

    run = ExperimentRun(
        run_id=run_id,
        params=dict(params),
        metrics={k: v for k, v in (metrics or {}).items()},
        artifacts={k: str(v) for k, v in (artifacts or {}).items()},
        tags=dict(tags or {}),
        timestamp=datetime.now(timezone.utc).isoformat(),
        git_sha=_git_sha(),
        status=status,
    )

    run_dir = root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "meta.json").write_text(
        json.dumps(run.__dict__, ensure_ascii=False, indent=1, default=str),
        encoding="utf-8",
    )
    with (root / "index.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(run.__dict__, ensure_ascii=False, default=str) + "\n")
    return run


def list_runs(
    runs_dir: Optional[Path] = None,
    tags: Optional[Mapping[str, Any]] = None,
    status: Optional[str] = None,
) -> list[dict]:
    """按 index.jsonl 倒序返回注册记录；tags/status 过滤（子集匹配）

    status=None 不过滤（展示用）；F4 之前的旧行无 status 字段，
    视为 completed——这些是真实跑完并产出结果的注册，把旧行从 N 里
    剔除会缩小 DSR 惩罚基数（让多重检验更容易通过），方向不诚实。
    """
    root = Path(runs_dir) if runs_dir else _default_runs_dir()
    index = root / "index.jsonl"
    if not index.exists():
        return []
    rows = []
    for line in index.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if tags and not all(row.get("tags", {}).get(k) == v for k, v in tags.items()):
            continue
        if status is not None and row.get("status", "completed") != status:
            continue
        rows.append(row)
    rows.sort(key=lambda r: r.get("run_id", ""), reverse=True)
    return rows


def count_trials(
    runs_dir: Optional[Path] = None,
    tags: Optional[Mapping[str, Any]] = None,
    status: Optional[str] = "completed",
) -> int:
    """试验总数 N（诚实统计，供 DSR/HLZ 多重检验校正）

    默认只数 completed：failed（未产出结果）与 smoke（合成冒烟）不是
    独立的候选策略试验；status=None 返回全部行数（审计对照用）。
    """
    return len(list_runs(runs_dir, tags, status=status))


def trial_status_breakdown(
    runs_dir: Optional[Path] = None,
    tags: Optional[Mapping[str, Any]] = None,
) -> dict[str, int]:
    """按 status 分组的行数（审计报告里与 N 一并展示，防 N 被洗小）"""
    rows = list_runs(runs_dir, tags)
    breakdown: dict[str, int] = {"completed": 0, "failed": 0, "smoke": 0, "legacy": 0}
    for row in rows:
        status = row.get("status")
        if status is None:
            breakdown["legacy"] += 1
        elif status in breakdown:
            breakdown[status] += 1
        else:
            breakdown[status] = breakdown.get(status, 0) + 1
    return breakdown


def _default_runs_dir() -> Path:
    # 本文件位于 <项目根>/quant_research/core/experiments.py → 项目根为 parents[2]
    return Path(__file__).resolve().parents[2] / "output" / "runs"

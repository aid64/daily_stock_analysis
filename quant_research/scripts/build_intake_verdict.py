# -*- coding: utf-8 -*-
"""strategy_intake 裁决报告生产端（跨项目桥的 quant 侧缺口补齐，2026-09-27）

把既有锁定证据（walk-forward CSV + 过拟合审计 JSON + 快照 sha256）打包为
dsa 消费端期待的 `strategy_intake_verdict_v1` JSON，写入 output/strategy_intake/。

纪律边界（见 docs/CB-RESEARCH-PLAN.md 2026-09-27 豁免记录）：
- 本脚本只做"证据搬运与格式封装"，不做任何研究判断；
- verdict 默认 promote 仅指推进一档（registered→shadow）——报告内嵌
  promotion_cap=shadow 声明（ETF 动量"禁止实盘"判定维持，HANDOFF 0.3）；
- 数据边界以 WF CSV 的测试窗末端为准（ Evidence 只对它实际覆盖的数据负责）。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_walk_forward(csv_path: Path) -> List[Dict[str, str]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _f(row: Dict[str, str], key: str) -> Optional[float]:
    try:
        return float(row[key])
    except (KeyError, TypeError, ValueError):
        return None


def build_report(
    *,
    strategy_id: str,
    wf_csv: Path,
    snapshot_path: Optional[Path],
    audit_path: Optional[Path],
    verdict: str,
    caveats: List[str],
) -> Dict[str, Any]:
    rows = _load_walk_forward(wf_csv)
    if not rows:
        raise SystemExit(f"walk-forward CSV 为空: {wf_csv}")

    test_rows = [r for r in rows if _f(r, "test_total_return") is not None]
    folds = []
    compounded = 1.0
    worst_dd = 0.0
    for r in test_rows:
        total = _f(r, "test_total_return") or 0.0
        dd = _f(r, "test_max_drawdown") or 0.0
        compounded *= (1.0 + total)
        worst_dd = max(worst_dd, dd)
        folds.append({
            "fold": int(_f(r, "fold") or 0),
            "test_window": f"{r.get('test_start', '')}~{r.get('test_end', '')}",
            "locked_params": {
                k: r[k] for k in ("lookback", "top_k", "ma_filter")
                if r.get(k)
            },
            "test_total_return": round(total, 6),
            "test_annual_return": round(_f(r, "test_annual_return") or 0.0, 6),
            "test_max_drawdown": round(dd, 6),
        })

    positive = sum(1 for f_ in folds if f_["test_total_return"] > 0)
    oos_start = folds[0]["test_window"].split("~")[0]
    oos_end = folds[-1]["test_window"].split("~")[1]

    audit: Dict[str, Any] = {}
    if audit_path and Path(audit_path).exists():
        raw = json.loads(Path(audit_path).read_text(encoding="utf-8"))
        audit = {
            "dsr": raw.get("dsr"),
            "gate_dsr_pass": raw.get("gate_dsr_pass"),
            "selected_config": raw.get("selected_config"),
            "n_trials_honest": raw.get("n_trials_honest"),
            "audit_file_sha256": _sha256_file(Path(audit_path)),
        }

    snapshot_sha = _sha256_file(snapshot_path) if snapshot_path else None
    metrics: Dict[str, Any] = {
        "n_folds": len(folds),
        "positive_folds": positive,
        "negative_folds": len(folds) - positive,
        "oos_compounded_total": round(compounded - 1.0, 6),
        "max_test_drawdown": round(worst_dd, 6),
        "folds": folds,
        "overfit_audit": audit,
        "walk_forward_csv_sha256": _sha256_file(wf_csv),
        "snapshot_sha256": snapshot_sha,
        "caveats": caveats,
    }

    return {
        "schema_version": "strategy_intake_verdict_v1",
        "strategy_id": strategy_id,
        "pit_end": oos_end,
        "data_as_of": oos_end,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "oos_window": f"{oos_start}~{oos_end}",
        "metrics": metrics,
        "verdict": verdict,
        "promotion_cap": "shadow",
        "evidence_files": {
            "walk_forward_csv": str(wf_csv),
            "overfit_audit_json": str(audit_path) if audit_path else None,
            "snapshot_pkl": str(snapshot_path) if snapshot_path else None,
        },
    }


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="打包 strategy_intake 裁决报告")
    p.add_argument("--strategy-id", default="etf_momentum")
    p.add_argument("--wf-csv", required=True, help="walk-forward 结果 CSV")
    p.add_argument("--audit-json", default="output/overfit_audit_etf_momentum.json")
    p.add_argument("--snapshot", default=None, help="快照 pkl（取 sha256）")
    p.add_argument("--verdict", default="promote",
                   choices=["promote", "hold", "reject"])
    p.add_argument("--caveat", action="append", default=[],
                   help="附加 caveats（可多次）")
    p.add_argument("--output-dir", default="output/strategy_intake")
    args = p.parse_args(argv)

    default_caveats = [
        "walk-forward 前 5 折（2023-2025H1）连续为负——无跨 regime 稳定参数，"
        "实盘禁令维持（HANDOFF.md:47 与第 0.3 节对账）；",
        "verdict=promote 仅指 registered→shadow（影子展示不落单），"
        "shadow→active 需独立证据且不得引用本报告；",
        "audit selected_config(lookback=180) 与 WF 网格口径(60,90/ma200)不同源，"
        "部署参数以 yaml 默认+WF 锁定口径为准，禁止混用。",
    ]
    report = build_report(
        strategy_id=args.strategy_id,
        wf_csv=Path(args.wf_csv),
        snapshot_path=Path(args.snapshot) if args.snapshot else None,
        audit_path=Path(args.audit_json) if args.audit_json else None,
        verdict=args.verdict,
        caveats=default_caveats + list(args.caveat or []),
    )

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    out = out_dir / f"{args.strategy_id}_verdict_{stamp}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"verdict written: {out}")
    print(f"  oos_window={report['oos_window']} "
          f"folds={report['metrics']['n_folds']} "
          f"(+{report['metrics']['positive_folds']}/-"
          f"{report['metrics']['negative_folds']}) "
          f"compounded={report['metrics']['oos_compounded_total']:+.2%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

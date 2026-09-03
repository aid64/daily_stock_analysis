# -*- coding: utf-8 -*-
"""F7 单因子诊断：双低 / YTM 代理 / 溢价率动量（联动差值代理）

对 PIT 截面注册因子跑 IC/RankIC/覆盖率报告（evaluate_factor），
窗口截到 holdout 边界前（2026-08-08），遵守 C2 期数据纪律。
输出 output/factor_diagnostics.json。

口径说明：
- ytm_proxy 是零息近似（无票息数据），系统性低估真实 YTM，仅作排序因子；
- premium_momentum_20 是"正股-转债联动差值"的可计算代理（PIT 无正股价，
  溢价率变化即联动残差的观测值）。
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Optional, Sequence

import pandas as pd

_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from quant_research.backtest.convertible import load_pit_snapshot
from quant_research.core.experiments import record_run
from quant_research.core.factors import evaluate_factor
from quant_research.core.panel import Panel
from quant_research.runner import sha256_file

logger = logging.getLogger("factor_diagnostics")

FACTORS = ("double_low", "ytm_proxy", "premium_momentum_20")
PERIODS = (5, 10, 20)
HOLDOUT_START = "2026-08-09"  # C2 期数据纪律：研究窗口不得触碰
END = "2026-08-08"


def main(argv: Optional[Sequence[str]] = None) -> int:
    from quant_research.core.holdout import assert_holdout_clean
    assert_holdout_clean(END, task="factor_diagnostics")
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", force=True)
    pit = load_pit_snapshot(_PROJECT_ROOT / "data" / "pit" / "convertible_pit.parquet")
    pit = pit[pit["as_of"] <= pd.Timestamp(END)]
    panel = Panel(pit)
    logger.info("诊断窗口: %s..%s, %d 只", panel.dates.min().date(),
                panel.dates.max().date(), len(panel.symbols))

    reports = {}
    for name in FACTORS:
        report = evaluate_factor(panel, name, periods=PERIODS)
        reports[name] = report.to_dict(orient="records")
        rank_rows = report[report["method"] == "rank"]
        logger.info("%s: RankIC 均值区间 [%.4f, %.4f], t 绝对值最大 %.2f",
                    name, rank_rows["mean_ic"].min(), rank_rows["mean_ic"].max(),
                    rank_rows["t_stat"].abs().max())

    combined = pd.concat(
        [pd.DataFrame(rows) for rows in reports.values()], ignore_index=True)
    out_json = _PROJECT_ROOT / "output" / "factor_diagnostics.json"
    out_json.write_text(json.dumps({
        "task": "F7 factor diagnostics (double_low / ytm_proxy / premium_momentum_20)",
        "window": f"{panel.dates.min().date()}..{END}",
        "holdout_start": HOLDOUT_START,
        "pit_sha256": sha256_file(_PROJECT_ROOT / "data" / "pit" / "convertible_pit.parquet"),
        "notes": [
            "ytm_proxy 为零息近似，系统性低估真实 YTM，仅作截面排序因子",
            "premium_momentum_20 为联动差值代理（PIT 无正股价）",
            "HLZ 门槛 |t|>3：多因子报告仅作诊断，未过门槛不得据此调参入库",
        ],
        "reports": reports,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    combined.to_csv(_PROJECT_ROOT / "output" / "factor_diagnostics.csv",
                    index=False, encoding="utf-8-sig")
    print(combined.to_string(index=False))

    record_run(
        params={"kind": "factor_diagnostics", "factors": list(FACTORS),
                "periods": list(PERIODS), "end": END},
        metrics={"n_rows": int(len(combined))},
        artifacts={"report": str(out_json)},
        tags={"kind": "factor_diagnostics"},
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

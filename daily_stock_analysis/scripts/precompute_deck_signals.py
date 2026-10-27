# -*- coding: utf-8 -*-
"""盘前预计算对比盘引擎目标，供收盘/开盘轮直接复用。

H_MWU_CB 的 853 只 CB feeds 引擎提取在冷缓存下约 10 分钟。14:30 预计算
落盘后，15:00 收盘轮读取同一目标，避免把收盘窗口耗在重复计算上。
缓存缺失/过期/策略配置不匹配时，runner 自动回退实时引擎，不改变信号口径。
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
QUANT_ROOT = PROJECT_DIR.parent / "quant_research"
CACHE_DIR = PROJECT_DIR / "output" / "deck_signal_cache"
EXTRACTOR = QUANT_ROOT / "scripts" / "extract_mwu_targets.py"

logger = logging.getLogger("deck_precompute")


def _parse_engine_stdout(stdout: str) -> dict:
    out = (stdout or "").strip()
    start = out.rfind("}\n{")
    start = start + 1 if start >= 0 else out.find("{")
    if start < 0:
        raise ValueError(f"引擎输出无 JSON: {out[:120]!r}")
    return json.loads(out[start:])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="预计算对比盘引擎目标")
    ap.add_argument("--strategy", default="etf_mwu_cb")
    ap.add_argument("--strategy-config", default="config/strategy/etf_mwu_cb_monthly.yaml")
    ap.add_argument("--config", default="config/default.yaml")
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args(argv)

    cmd = [
        sys.executable, str(EXTRACTOR),
        "--strategy", args.strategy,
        "--strategy-config", args.strategy_config,
        "--config", args.config,
    ]
    logger.info("预计算开始: %s (%s)", args.strategy, args.strategy_config)
    try:
        result = subprocess.run(
            cmd, cwd=str(QUANT_ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=args.timeout)
    except subprocess.TimeoutExpired:
        logger.error("预计算超时 %ss: %s", args.timeout, args.strategy)
        return 1
    if result.returncode != 0:
        logger.error("预计算失败 rc=%s: %s", result.returncode, (result.stderr or "")[-500:])
        return 1
    try:
        data = _parse_engine_stdout(result.stdout)
    except (ValueError, json.JSONDecodeError) as exc:
        logger.error("预计算输出解析失败: %r", exc)
        return 1
    target = data.get("target")
    if not isinstance(target, dict) or not target:
        logger.error("预计算无目标: %s", args.strategy)
        return 1

    data["cache_date"] = date.today().isoformat()
    data["generated_at"] = datetime.now().isoformat(timespec="seconds")
    data["strategy_config"] = args.strategy_config
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = CACHE_DIR / f"{args.strategy}_{date.today():%Y%m%d}.json"
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(out)
    logger.info("预计算完成: %s -> %s (as_of=%s, target=%s)",
                args.strategy, out, data.get("as_of"), sorted(target))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    sys.exit(main())

#!/usr/bin/env bash
# quant_research 一键重建脚本：从 raw 缓存重建 PIT → 构建快照 → 运行测试
#
# 用法：
#   bash scripts/rebuild_all.sh                  # PIT 2018 起 + 测试
#   bash scripts/rebuild_all.sh --skip-tests    # 只重建数据，不跑测试
#
# 前置：pip install -r requirements.lock.txt && pip install -e .
set -euo pipefail
cd "$(dirname "$0")/.."

PIT_START="${PIT_START:-2018-01-01}"
PIT_END="${PIT_END:-$(date +%Y-%m-%d)}"
SKIP_TESTS=false
for arg in "$@"; do
  if [ "$arg" = "--skip-tests" ]; then SKIP_TESTS=true; fi
done

echo "=== quant_research 一键重建 ==="
echo "PIT 窗口: ${PIT_START} ~ ${PIT_END}"

# 1) 增量更新 PIT raw 缓存尾部（akshare value_analysis，已最新的跳过）
echo "--- [1/3] 增量更新 raw 缓存尾部 ---"
py scripts/update_pit_tail.py

# 2) 从 raw 缓存重组 PIT（--start 需 2018：默认 2021 会丢 46 只早期退市券）
echo "--- [2/3] 重组 PIT ---"
py scripts/build_pit_snapshot.py \
  --assemble-only \
  --universe-file data/universe_cb_full.txt \
  --start "${PIT_START}" \
  --end "${PIT_END}" \
  --pit data/pit/convertible_pit.parquet \
  --coverage-out data/pit/coverage.json

echo "--- [2b/3] 构建 ETF 快照 ---"
py scripts/build_feeds_snapshot.py \
  --start 2012-01-01 \
  --output output/feeds_snapshot_v3.pkl \
  --source akshare \
  --baseline output/feeds_snapshot_v2.pkl || echo "(ETF 快照构建失败：检查东财连接；不影响已有 v2 快照)"

# 3) 测试
if [ "${SKIP_TESTS}" = "false" ]; then
  echo "--- [3/3] 运行测试 ---"
  py -m pytest -q
fi

echo "=== 完成 ==="

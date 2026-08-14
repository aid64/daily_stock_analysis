"""脚本入口：作为 pyproject.toml [project.scripts] 的注册点

提供：
    qr-download  -> quant_research.scripts_entry:download_main
    qr-backtest  -> quant_research.scripts_entry:backtest_main（P2 启用）
"""

from __future__ import annotations

import sys
from typing import Optional, Sequence


def download_main(argv: Optional[Sequence[str]] = None) -> int:
    """数据下载入口"""
    from scripts.download_data import main as _main
    return _main(argv if argv is not None else sys.argv[1:])


def backtest_main(argv: Optional[Sequence[str]] = None) -> int:
    """回测入口（P2 启用）"""
    print("Backtest 入口尚未实现，将在 P2 阶段启用", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(download_main())

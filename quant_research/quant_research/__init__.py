"""quant_research: 独立量化研究子项目

模块结构：
    config/     配置层（Hydra + Pydantic）
    data/       数据层（多源 Client + Parquet 缓存 + Universe）
    strategies/ 策略层（P1 启用）
    backtest/   回测层（P2 启用）
    risk/       风控层（P2 启用）
    analyze/    分析层（P4 启用）
    notify/     通知层（P5 启用）

设计依据：docs/quant_research_architecture_research.md
"""

__version__ = "0.1.0"

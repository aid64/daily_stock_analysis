"""配置层测试"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from quant_research.config.schema import (
    AppConfig,
    DataConfig,
    DataSource,
    TushareConfig,
    RiskConfig,
    AdjustMethod,
)
from quant_research.config.loader import load_config, get_default_config_path


class TestSchema:
    def test_data_config_defaults(self):
        cfg = DataConfig()
        assert cfg.primary == DataSource.TUSHARE
        assert DataSource.BAOSTOCK in cfg.fallbacks
        assert cfg.adjust == AdjustMethod.HFQ

    def test_data_config_invalid_cache_format(self):
        with pytest.raises(ValidationError):
            DataConfig(cache_format="csv")

    def test_data_config_empty_fallbacks(self):
        with pytest.raises(ValidationError):
            DataConfig(fallbacks=[])

    def test_tushare_config_token_strip(self):
        cfg = TushareConfig(token="  abc123  ")
        assert cfg.token == "abc123"

    def test_risk_config_ratios_sum_check(self):
        with pytest.raises(ValidationError):
            RiskConfig(three_tier_ratios={"base": 0.5, "tactical": 0.4, "cash": 0.2})

    def test_risk_config_ratios_valid(self):
        cfg = RiskConfig(three_tier_ratios={"base": 0.5, "tactical": 0.3, "cash": 0.2})
        assert cfg.three_tier_ratios["base"] == 0.5

    def test_app_config_minimal(self):
        cfg = AppConfig(
            data=DataConfig(),
            tushare=TushareConfig(token="test"),
        )
        assert cfg.data.primary == DataSource.TUSHARE
        assert cfg.backtest.initial_capital == 10000.0


class TestLoader:
    def test_get_default_config_path_exists(self):
        p = get_default_config_path()
        assert p.exists(), f"默认配置文件不存在: {p}"
        assert p.name == "default.yaml"

    def test_load_default_config(self):
        cfg = load_config()
        assert cfg.data.primary == DataSource.TUSHARE
        assert cfg.tushare.rate_limit_per_min == 200

    def test_load_nonexistent_config(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_config(tmp_path / "nonexistent.yaml")

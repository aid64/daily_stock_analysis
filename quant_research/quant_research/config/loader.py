"""配置加载器

用法：
    from quant_research.config import load_config
    cfg = load_config()  # 加载默认 config/default.yaml
    cfg = load_config("config/env/dev.yaml")  # 加载指定文件

特性：
1. 支持 OmegaConf 语法（${oc.env:VAR,default}）
2. 自动从 .env 加载环境变量
3. Pydantic 校验失败立即抛出
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from omegaconf import OmegaConf
from dotenv import load_dotenv

from .schema import AppConfig, StrategyConfig


def get_default_config_path() -> Path:
    """返回默认配置文件路径：quant_research/config/default.yaml"""
    # 本文件位于 quant_research/quant_research/config/loader.py
    # 上溯到 quant_research/config/default.yaml
    here = Path(__file__).resolve().parent  # .../quant_research/quant_research/config
    project_root = here.parent.parent        # .../quant_research
    return project_root / "config" / "default.yaml"


def _resolve_env_file(start_dir: Path) -> Optional[Path]:
    """从 start_dir 向上查找 .env 文件"""
    current = start_dir
    for _ in range(5):  # 最多向上 5 层
        env_path = current / ".env"
        if env_path.exists():
            return env_path
        if current.parent == current:
            break
        current = current.parent
    return None


def load_config(config_path: Optional[str | Path] = None) -> AppConfig:
    """加载配置文件并返回校验后的 AppConfig

    Args:
        config_path: 配置文件路径。None 时使用默认 config/default.yaml

    Returns:
        AppConfig 实例

    Raises:
        FileNotFoundError: 配置文件不存在
        pydantic.ValidationError: 配置校验失败
    """
    cfg_path = Path(config_path) if config_path else get_default_config_path()
    if not cfg_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {cfg_path}")

    # 自动加载 .env（从配置文件目录向上查找）
    env_file = _resolve_env_file(cfg_path.parent)
    if env_file:
        load_dotenv(env_file, override=False)

    # 1. 加载 YAML，OmegaConf 解析 ${oc.env:VAR,default} 语法
    raw = OmegaConf.load(cfg_path)
    raw_dict = OmegaConf.to_container(raw, resolve=True)

    # 2. Pydantic 校验
    app_cfg = AppConfig(**raw_dict)

    # 3. 配置根日志
    _setup_logging(app_cfg.logging)

    return app_cfg


def _setup_logging(log_cfg) -> None:
    """根据配置初始化根日志"""
    level = getattr(logging, log_cfg.level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format=log_cfg.format,
        force=True,  # 覆盖之前的配置
    )


def get_strategy_config_path(name: str) -> Path:
    """按策略名返回 config/strategy/<name>.yaml 路径"""
    here = Path(__file__).resolve().parent
    project_root = here.parent.parent
    return project_root / "config" / "strategy" / f"{name}.yaml"


def load_strategy_config(
    config_path: Optional[str | Path] = None,
    name: Optional[str] = None,
) -> StrategyConfig:
    """加载策略配置（universe + 参数），Pydantic 校验失败立即抛出

    Args:
        config_path: 策略 yaml 路径；与 name 二选一
        name: 策略名，解析为 config/strategy/<name>.yaml

    Raises:
        FileNotFoundError: 配置文件不存在
        pydantic.ValidationError: 配置校验失败
    """
    if config_path is None:
        if not name:
            raise ValueError("config_path 与 name 必须提供其一")
        config_path = get_strategy_config_path(name)

    cfg_path = Path(config_path)
    if not cfg_path.exists():
        raise FileNotFoundError(f"策略配置文件不存在: {cfg_path}")

    raw = OmegaConf.load(cfg_path)
    raw_dict = OmegaConf.to_container(raw, resolve=True)
    return StrategyConfig(**raw_dict)

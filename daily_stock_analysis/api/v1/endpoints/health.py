# -*- coding: utf-8 -*-
"""
===================================
健康检查接口
===================================

职责：
1. 提供 /api/v1/health 健康检查接口
2. 用于负载均衡器和监控系统
3. 提供详细的组件健康状态检查
"""

import time
from datetime import datetime
from typing import Dict, Any

from fastapi import APIRouter

from api.v1.schemas.common import HealthResponse
from src.config import get_config

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """
    健康检查接口
    
    用于负载均衡器或监控系统检查服务状态
    
    Returns:
        HealthResponse: 包含服务状态和时间戳
    """
    return HealthResponse(
        status="ok",
        timestamp=datetime.now().isoformat()
    )


@router.get("/health/detailed")
async def health_check_detailed() -> Dict[str, Any]:
    """
    详细健康检查接口
    
    返回各组件的健康状态，包括：
    - 系统状态
    - 数据库连接
    - LLM后端状态
    - 通知渠道状态
    
    Returns:
        Dict[str, Any]: 包含各组件健康状态的详细信息
    """
    start_time = time.time()
    results = {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "components": {},
        "latency_ms": 0,
    }
    
    # 检查配置
    try:
        config = get_config()
        results["components"]["config"] = {
            "status": "ok",
            "message": "配置加载成功",
            "has_stock_list": bool(config.stock_list),
            "has_llm_channels": bool(config.llm_channels),
        }
    except Exception as e:
        results["components"]["config"] = {
            "status": "error",
            "message": str(e),
        }
        results["status"] = "degraded"
    
    # 检查数据库
    try:
        from sqlalchemy import text

        from src.storage import DatabaseManager

        with DatabaseManager.get_instance().get_session() as session:
            session.execute(text("SELECT 1"))
        results["components"]["database"] = {
            "status": "ok",
            "message": "数据库连接正常",
        }
    except Exception as e:
        results["components"]["database"] = {
            "status": "error",
            "message": str(e),
        }
        results["status"] = "degraded"
    
    # 检查LLM后端
    try:
        from src.services.system_config_service import SystemConfigService
        service = SystemConfigService()
        backend_status = service.get_generation_backend_status()
        primary = backend_status.get("primary") or {}
        fallback = backend_status.get("fallback") or {}
        primary_available = bool(primary.get("available"))
        fallback_available = bool(fallback.get("available"))

        results["components"]["llm_backend"] = {
            "status": "ok" if primary_available else "warning",
            "primary_available": primary_available,
            "fallback_available": fallback_available,
            "primary_backend": primary.get("backend_id"),
            "fallback_backend": fallback.get("backend_id"),
            "message": "LLM后端正常" if primary_available else "主后端不可用，检查API配置",
        }
    except Exception as e:
        results["components"]["llm_backend"] = {
            "status": "error",
            "message": str(e),
        }
        results["status"] = "degraded"
    
    # 检查通知渠道
    try:
        config = get_config()
        feishu_configured = bool(getattr(config, "feishu_webhook_url", None)) or all(
            getattr(config, key, None)
            for key in ("feishu_app_id", "feishu_app_secret", "feishu_chat_id")
        )
        configured_channels = ["feishu"] if feishu_configured else []

        results["components"]["notifications"] = {
            "status": "ok" if configured_channels else "warning",
            "configured_channels": configured_channels,
            "message": "已配置飞书通知" if configured_channels else "未配置飞书通知",
        }
    except Exception as e:
        results["components"]["notifications"] = {
            "status": "error",
            "message": str(e),
        }
    
    # 检查数据源
    try:
        from data_provider.akshare_fetcher import AkshareFetcher  # noqa: F401
        from data_provider.efinance_fetcher import EfinanceFetcher  # noqa: F401
        from data_provider.tushare_fetcher import TushareFetcher  # noqa: F401

        results["components"]["data_source"] = {
            "status": "ok",
            "message": "数据源模块加载正常（Tushare/AkShare/Efinance）",
        }
    except Exception as e:
        results["components"]["data_source"] = {
            "status": "warning",
            "message": f"数据源检查失败: {str(e)}",
        }
    
    # 计算延迟
    results["latency_ms"] = int((time.time() - start_time) * 1000)
    
    return results

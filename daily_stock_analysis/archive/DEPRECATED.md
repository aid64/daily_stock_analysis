# ⚠️ 此 Fork 版本已废弃 (DEPRECATED)

> 归档日期: 2026-07-17
> 原始仓库: `zmm1234/daily_stock_analysis`

## 说明

此 Fork 基于上游极早期版本 (远早于 v3.26.0)，包含针对企业微信推送、定时任务、图表汇总等功能的 10+ 次自定义修改。

**所有功能已于 2026-07-17 迁移合并到项目根目录**（上游 v3.26.0）。

## 迁移对照表

| Fork 自定义功能 | 上游已支持方式 |
|----------------|--------------|
| 企业微信分条推送 `send_batch_notifications()` | 设置 `WECHAT_BATCH_MODE=true` |
| 图表汇总图片 `generate_summary_image()` | 设置 `WECHAT_SUMMARY_IMAGE_ENABLED=true` |
| 定时18:00执行 | 设置 `SCHEDULE_TIME=18:00` |
| 中文 matplotlib 字体 | 上游已内置 CJK 字体支持 |
| 请求限流（并发=1） | 设置 `MAX_WORKERS=1` |
| 火山引擎豆包模型 | 通过 LiteLLM 渠道配置 `LLM_CHANNELS=volcengine` |
| 多模型 fallback | 上游 LiteLLM 原生支持多渠道自动 fallback |

## 保留原因

此目录保留仅用于：
1. 历史提交记录追溯
2. 自定义修改作为配置参考

**请勿在此目录中继续开发。**

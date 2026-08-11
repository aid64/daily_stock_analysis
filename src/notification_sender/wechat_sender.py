# -*- coding: utf-8 -*-
"""
Daily Stock Analysis — 企业微信通知发送器

职责：
1. 通过企业微信 Webhook 发送文本/图片消息
2. 分批推送（汇总 + 逐条个股），优化手机阅读体验
3. 生成汇总图表（imgkit 主方案，matplotlib 自动降级）
4. 长消息智能分片
"""
import logging
import base64
import hashlib
import re
import requests
import time
from datetime import datetime
from typing import Optional, List, Dict, Any

from src.config import Config
from src.formatters import chunk_content_by_max_bytes, strip_hidden_markdown_metadata
from src.report_language import get_signal_level, localize_trend_prediction


logger = logging.getLogger(__name__)


# WeChat Work image msgtype limit ~2MB (base64 payload)
WECHAT_IMAGE_MAX_BYTES = 2 * 1024 * 1024

class WechatSender:
    
    def __init__(self, config: Config):
        """
        初始化企业微信配置

        Args:
            config: 配置对象
        """
        self._wechat_url = config.wechat_webhook_url
        self._wechat_max_bytes = getattr(config, 'wechat_max_bytes', 4000)
        self._wechat_msg_type = getattr(config, 'wechat_msg_type', 'markdown')
        self._webhook_verify_ssl = getattr(config, 'webhook_verify_ssl', True)
        self._wechat_batch_mode = getattr(config, 'wechat_batch_mode', False)
        self._wechat_batch_delay_sec = getattr(config, 'wechat_batch_delay_sec', 1.0)
        self._wechat_summary_image_enabled = getattr(config, 'wechat_summary_image_enabled', False)
        
    def send_to_wechat(self, content: str, *, timeout_seconds: Optional[float] = None) -> bool:
        """
        推送消息到企业微信机器人
        
        企业微信 Webhook 消息格式：
        支持 markdown 类型以及 text 类型, markdown 类型在微信中无法展示，可以使用 text 类型,
        markdown 类型会解析 markdown 格式,text 类型会直接发送纯文本。

        markdown 类型示例：
        {
            "msgtype": "markdown",
            "markdown": {
                "content": "## 标题\n\n内容"
            }
        }
        
        text 类型示例：
        {
            "msgtype": "text",
            "text": {
                "content": "内容"
            }
        }

        注意：企业微信 Markdown 限制 4096 字节（非字符）, Text 类型限制 2048 字节，超长内容会自动分批发送
        可通过环境变量 WECHAT_MAX_BYTES 调整限制值
        
        Args:
            content: Markdown 格式的消息内容
            
        Returns:
            是否发送成功
        """
        if not self._wechat_url:
            logger.warning("企业微信 Webhook 未配置，跳过推送")
            return False
        
        sanitized_content = strip_hidden_markdown_metadata(content).strip()
        if not sanitized_content:
            logger.warning("企业微信消息内容为空，跳过推送")
            return False

        # 根据消息类型动态限制上限，避免 text 类型超过企业微信 2048 字节限制
        if self._wechat_msg_type == 'text':
            max_bytes = min(self._wechat_max_bytes, 2000)  # 预留一定字节给系统/分页标记
        else:
            max_bytes = self._wechat_max_bytes  # markdown 默认 4000 字节

        # 检查字节长度，超长则分批发送
        content_bytes = len(sanitized_content.encode('utf-8'))
        if content_bytes > max_bytes:
            logger.info(f"消息内容超长({content_bytes}字节/{len(content)}字符)，将分批发送")
            return self._send_wechat_chunked(sanitized_content, max_bytes)
        
        try:
            return self._send_wechat_message(sanitized_content, timeout_seconds=timeout_seconds)
        except Exception as e:
            logger.error(f"发送企业微信消息失败: {e}")
            return False

    def _send_wechat_image(self, image_bytes: bytes) -> bool:
        """Send image via WeChat Work webhook msgtype image (Issue #289)."""
        if not self._wechat_url:
            return False
        if len(image_bytes) > WECHAT_IMAGE_MAX_BYTES:
            logger.warning(
                "企业微信图片超限 (%d > %d bytes)，拒绝发送，调用方应 fallback 为文本",
                len(image_bytes), WECHAT_IMAGE_MAX_BYTES,
            )
            return False
        try:
            b64 = base64.b64encode(image_bytes).decode("ascii")
            md5_hash = hashlib.md5(image_bytes).hexdigest()
            payload = {
                "msgtype": "image",
                "image": {"base64": b64, "md5": md5_hash},
            }
            response = requests.post(
                self._wechat_url, json=payload, timeout=30, verify=self._webhook_verify_ssl
            )
            if response.status_code == 200:
                result = response.json()
                if result.get("errcode") == 0:
                    logger.info("企业微信图片发送成功")
                    return True
                logger.error("企业微信图片发送失败: %s", result.get("errmsg", ""))
            else:
                logger.error("企业微信请求失败: HTTP %s", response.status_code)
            return False
        except Exception as e:
            logger.error("企业微信图片发送异常: %s", e)
            return False
    
    def _send_wechat_message(self, content: str, *, timeout_seconds: Optional[float] = None) -> bool:
        """发送企业微信消息"""
        payload = self._gen_wechat_payload(content)
        
        response = requests.post(
            self._wechat_url,
            json=payload,
            timeout=timeout_seconds or 10,
            verify=self._webhook_verify_ssl
        )
        
        if response.status_code == 200:
            result = response.json()
            if result.get('errcode') == 0:
                logger.info("企业微信消息发送成功")
                return True
            else:
                logger.error(f"企业微信返回错误: {result}")
                return False
        else:
            logger.error(f"企业微信请求失败: {response.status_code}")
            return False
        
    def _send_wechat_chunked(self, content: str, max_bytes: int) -> bool:
        """
        分批发送长消息到企业微信
        
        按股票分析块（以 --- 或 ### 分隔）智能分割，确保每批不超过限制
        
        Args:
            content: 完整消息内容
            max_bytes: 单条消息最大字节数
            
        Returns:
            是否全部发送成功
        """
        chunks = chunk_content_by_max_bytes(content, max_bytes, add_page_marker=True)
        total_chunks = len(chunks)
        success_count = 0
        for i, chunk in enumerate(chunks):
            if self._send_wechat_message(chunk):
                success_count += 1
            else:
                logger.error(f"企业微信第 {i+1}/{total_chunks} 批发送失败")
            if i < total_chunks - 1:
                time.sleep(1)
        return success_count == len(chunks)

    def _gen_wechat_payload(self, content: str) -> dict:
        """生成企业微信消息 payload"""
        sanitized_content = strip_hidden_markdown_metadata(content).strip()
        if self._wechat_msg_type == 'text':
            return {
                "msgtype": "text",
                "text": {
                    "content": sanitized_content
                }
            }
        else:
            return {
                "msgtype": "markdown",
                "markdown": {
                    "content": sanitized_content
                }
            }

    def send_batch_notifications(self, results: List[Any], summary_content: str) -> bool:
        """
        分批发送通知（优化用户体验）
        1. 发送汇总信息
        2. 发送汇总图片（可选）
        3. 逐条发送个股信息（按评分排序）

        Args:
            results: 分析结果列表
            summary_content: 汇总内容

        Returns:
            是否至少有一个渠道发送成功（部分成功也算成功）
        """
        if not self._wechat_url:
            logger.warning("企业微信 Webhook 未配置，跳过推送")
            return False

        success_count = 0
        fail_count = 0
        total_steps = (2 if self._wechat_summary_image_enabled else 1) + len(results)
        failed_stocks = []

        # 1. 发送汇总（最多重试 2 次）
        logger.info("发送企业微信汇总消息...")
        if self._send_with_retry(summary_content, max_retries=2):
            success_count += 1
        else:
            fail_count += 1
            logger.error("企业微信汇总消息发送失败")

        # 2. 发送汇总图片（可选）
        if self._wechat_summary_image_enabled:
            time.sleep(self._wechat_batch_delay_sec)
            image_bytes = self._generate_summary_image_bytes(results)
            if image_bytes:
                logger.info("发送企业微信汇总图片...")
                if self._send_image_with_retry(image_bytes, max_retries=2):
                    success_count += 1
                else:
                    fail_count += 1
                    logger.error("企业微信汇总图片发送失败")
            else:
                logger.warning("无法生成汇总图片，跳过")

        # 3. 逐个发送个股（按评分从高到低）
        sorted_results = sorted(results, key=lambda x: getattr(x, 'sentiment_score', 0), reverse=True)
        
        for i, result in enumerate(sorted_results):
            time.sleep(self._wechat_batch_delay_sec)
            
            msg = self._format_wechat_stock_msg(result)
            stock_name = getattr(result, 'name', result.code)
            logger.info(f"正在推送 {stock_name} ({i+1}/{len(results)}) ...")
            
            if self._send_with_retry(msg, max_retries=1):
                success_count += 1
            else:
                fail_count += 1
                failed_stocks.append(stock_name)
                logger.error(f"企业微信个股 {stock_name} 推送失败，继续发送下一个")

        # 4. 发送失败汇总（如果有失败）
        if failed_stocks:
            time.sleep(self._wechat_batch_delay_sec)
            fail_summary = f"❌ 部分推送失败（{len(failed_stocks)}/{len(results)}）:\n"
            fail_summary += "\n".join(f"   • {name}" for name in failed_stocks)
            self.send_to_wechat(fail_summary)

        logger.info(f"企业微信分批推送完成: 成功 {success_count}，失败 {fail_count}")
        
        # 部分成功也算成功（至少汇总发送成功）
        return success_count > 0

    def _send_with_retry(self, content: str, max_retries: int = 1) -> bool:
        """
        带重试的消息发送

        Args:
            content: 消息内容
            max_retries: 最大重试次数

        Returns:
            是否发送成功
        """
        for attempt in range(max_retries + 1):
            try:
                if self.send_to_wechat(content):
                    return True
                if attempt < max_retries:
                    logger.warning(f"发送失败，重试 {attempt + 1}/{max_retries}...")
                    time.sleep(1.0 * (attempt + 1))
            except Exception as e:
                logger.warning(f"发送异常: {e}")
                if attempt < max_retries:
                    time.sleep(1.0 * (attempt + 1))
        return False

    def _send_image_with_retry(self, image_bytes: bytes, max_retries: int = 1) -> bool:
        """
        带重试的图片发送

        Args:
            image_bytes: 图片字节流
            max_retries: 最大重试次数

        Returns:
            是否发送成功
        """
        for attempt in range(max_retries + 1):
            try:
                if self._send_wechat_image(image_bytes):
                    return True
                if attempt < max_retries:
                    logger.warning(f"图片发送失败，重试 {attempt + 1}/{max_retries}...")
                    time.sleep(1.0 * (attempt + 1))
            except Exception as e:
                logger.warning(f"图片发送异常: {e}")
                if attempt < max_retries:
                    time.sleep(1.0 * (attempt + 1))
        return False

    def _format_wechat_stock_msg(self, result: Any) -> str:
        """
        生成单只股票的企业微信消息（优化格式，适合手机阅读）

        Args:
            result: 分析结果

        Returns:
            Markdown 格式的单股消息
        """
        lines = []
        code = getattr(result, 'code', '')
        name = getattr(result, 'name', '')
        sentiment_score = getattr(result, 'sentiment_score', 0)
        trend_prediction = getattr(result, 'trend_prediction', '')
        analysis_summary = getattr(result, 'analysis_summary', '')
        operation_advice = getattr(result, 'operation_advice', '')
        
        # 获取信号等级
        signal_text, signal_emoji, _ = get_signal_level(
            operation_advice, sentiment_score, 'zh'
        )

        # 股票名称
        stock_name = name if name and not name.startswith('股票') else f'股票{code}'

        # 标题行：信号等级 + 股票名称
        lines.append(f"### {signal_emoji} **{signal_text}** | {stock_name}({code})")
        lines.append("")

        # 核心决策（一句话）
        if analysis_summary:
            lines.append(f"📌 **{analysis_summary[:100]}**" if len(analysis_summary) > 100 else f"📌 **{analysis_summary}**")
            lines.append("")

        # Score & Trend
        lines.append(f"评分: {sentiment_score}分 | 趋势: {localize_trend_prediction(trend_prediction, 'zh')}")
        lines.append("")

        # Dashboard 数据（如果有）
        dashboard = getattr(result, 'dashboard', {})
        if dashboard:
            # 重要信息区
            intel = dashboard.get('intelligence', {})
            if intel:
                info_lines = []
                if intel.get('earnings_outlook'):
                    info_lines.append(f"📊 业绩: {intel['earnings_outlook']}")
                if intel.get('sentiment_summary'):
                    info_lines.append(f"💭 舆情: {intel['sentiment_summary']}")
                if info_lines:
                    lines.extend(info_lines)
                    lines.append("")

                # 风险警报
                risks = intel.get('risk_alerts', [])
                if risks:
                    lines.append("🚨 **风险**:")
                    for risk in risks[:3]:
                        lines.append(f"   • {risk[:50]}")
                    lines.append("")

                # 利好催化
                catalysts = intel.get('positive_catalysts', [])
                if catalysts:
                    lines.append("✨ **利好**:")
                    for cat in catalysts[:3]:
                        lines.append(f"   • {cat[:50]}")
                    lines.append("")

            # 狙击点位
            battle = dashboard.get('battle_plan', {})
            sniper = battle.get('sniper_points', {}) if battle else {}
            if sniper:
                ideal_buy = sniper.get('ideal_buy', '')
                stop_loss = sniper.get('stop_loss', '')
                take_profit = sniper.get('take_profit', '')
                
                points = []
                if ideal_buy:
                    points.append(f"🎯买点: **{ideal_buy}**")
                if stop_loss:
                    points.append(f"🛑止损: {stop_loss}")
                if take_profit:
                    points.append(f"🎊目标: {take_profit}")
                
                if points:
                    lines.append(" | ".join(points))
                    lines.append("")

            # 持仓建议
            core = dashboard.get('core_conclusion', {}) if dashboard else {}
            pos_advice = core.get('position_advice', {})
            if pos_advice:
                no_pos = pos_advice.get('no_position', '')
                has_pos = pos_advice.get('has_position', '')
                if no_pos:
                    lines.append(f"🆕空仓: {no_pos}")
                if has_pos:
                    lines.append(f"💼持仓: {has_pos}")
                lines.append("")

            # 检查清单未通过项（Fork 移植功能）
            battle = dashboard.get('battle_plan', {}) if dashboard else {}
            checklist = battle.get('action_checklist', []) if battle else []
            if checklist:
                failed_checks = [
                    c for c in checklist
                    if isinstance(c, str) and (c.startswith('❌') or c.startswith('⚠️'))
                ]
                if failed_checks:
                    lines.append("**⚠️ 未通过项**:")
                    for check in failed_checks[:5]:
                        lines.append(f"   {check[:60]}")
                    lines.append("")

        # 操作理由
        if hasattr(result, 'buy_reason') and result.buy_reason:
            reason = result.buy_reason[:80] + "..." if len(result.buy_reason) > 80 else result.buy_reason
            lines.append(f"💡 {reason}")
            lines.append("")

        # 风险提示
        if hasattr(result, 'risk_warning') and result.risk_warning:
            risk = result.risk_warning[:50] + "..." if len(result.risk_warning) > 50 else result.risk_warning
            lines.append(f"⚠️ {risk}")
            lines.append("")

        lines.append(f"*生成时间: {datetime.now().strftime('%H:%M')}*")

        return "\n".join(lines)

    # ── 辅助函数 ──────────────────────────────────────────────────────────
    @staticmethod
    def _extract_price(text: Any) -> str:
        """从文本中提取价格数字（Fork 移植）。处理 '~1800元'、'1500-1600' 等格式。"""
        if text is None or text == '':
            return '-'
        match = re.search(r'\d+(\.\d+)?', str(text))
        return match.group(0) if match else '-'


    def _generate_summary_image_bytes(self, results: List[Any]) -> Optional[bytes]:
        """
        生成汇总图片。
        优先使用 imgkit（轻量），不可用时自动降级到 matplotlib（Fork 移植）。
        """
        # 尝试 imgkit（主方案）
        img_bytes = self._generate_image_imgkit(results)
        if img_bytes is not None:
            return img_bytes

        # 降级到 matplotlib（Fork 移植：无外部依赖，更可靠）
        logger.info("imgkit 不可用，降级使用 matplotlib 生成汇总图片")
        return self._generate_image_matplotlib(results)


    def _generate_image_imgkit(self, results: List[Any]) -> Optional[bytes]:
        """使用 markdown2 + imgkit 生成图片。需要系统安装 wkhtmltopdf。"""
        try:
            from src.formatters import markdown_to_html_document

            sorted_results = sorted(
                results, key=lambda x: getattr(x, 'sentiment_score', 0), reverse=True
            )
            table_rows = [
                "| 排名 | 股票 | 评分 | 建议 | 买点 | 卖点 | 止损 |",
                "|------|------|------|------|------|------|------|",
            ]
            for idx, r in enumerate(sorted_results[:15], 1):
                code = getattr(r, 'code', '')
                name = getattr(r, 'name', '')
                score = getattr(r, 'sentiment_score', 0)
                advice = getattr(r, 'operation_advice', '')
                buy = sell = stop = '-'
                dashboard = getattr(r, 'dashboard', {}) or {}
                battle = dashboard.get('battle_plan', {}) or {}
                sniper = battle.get('sniper_points', {}) or {}
                buy = self._extract_price(sniper.get('ideal_buy'))
                sell = self._extract_price(sniper.get('take_profit'))
                stop = self._extract_price(sniper.get('stop_loss'))
                stock_name = name if name else f'股票{code}'
                table_rows.append(
                    f"| {idx} | {stock_name} | {score} | {advice} | {buy} | {sell} | {stop} |"
                )

            summary_md = (
                f"# 📊 股票分析汇总 ({datetime.now().strftime('%Y-%m-%d')})\n\n"
                f"共分析 **{len(results)}** 只股票\n\n"
                f"{chr(10).join(table_rows)}\n\n"
                f"*生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*"
            )
            html_content = markdown_to_html_document(summary_md)

            import imgkit
            import os

            font_paths = [
                'C:/Windows/Fonts/msyh.ttc',
                'C:/Windows/Fonts/simhei.ttf',
                '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc',
                '/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc',
                '/System/Library/Fonts/PingFang.ttc',
            ]
            valid_font = next((f for f in font_paths if os.path.exists(f)), None)
            options = {'format': 'png', 'encoding': 'UTF-8', 'quiet': '', 'width': 900}
            if valid_font:
                options['font-family'] = valid_font
                logger.debug("imgkit 使用字体: %s", valid_font)
            else:
                logger.warning("未找到中文字体文件，imgkit 图片中文可能显示异常")

            image_bytes = imgkit.from_string(html_content, False, options=options)
            logger.info("imgkit 汇总图片生成成功，大小: %d 字节", len(image_bytes))
            return image_bytes

        except ImportError:
            logger.info("imgkit 未安装，跳过")
            return None
        except Exception as e:
            logger.warning("imgkit 生成图片失败: %s", e)
            return None


    def _generate_image_matplotlib(self, results: List[Any]) -> Optional[bytes]:
        """
        使用 matplotlib 生成颜色编码的汇总表格图片（Fork 移植方案）。
        无需外部依赖，中文渲染更可靠。
        """
        import io
        import os
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import pandas as pd

        # ── 中文字体自动检测（Fork 移植）──
        _CJK_FONT_CANDIDATES = [
            'WenQuanYi Micro Hei',  # Linux (GitHub Actions)
            'Microsoft YaHei',       # Windows
            'SimHei',                # Windows
            'PingFang SC',           # macOS
            'Arial Unicode MS',      # macOS fallback
            'Cymzen',                # Linux fallback
            'DejaVu Sans',           # generic fallback
        ]
        for font_name in _CJK_FONT_CANDIDATES:
            try:
                plt.rcParams['font.sans-serif'] = [font_name] + plt.rcParams['font.sans-serif']
                plt.rcParams['axes.unicode_minus'] = False
                break
            except Exception:
                continue

        data = []
        for r in results:
            dashboard = getattr(r, 'dashboard', {}) or {}
            battle = dashboard.get('battle_plan', {}) or {}
            sniper = battle.get('sniper_points', {}) or {}
            data.append({
                '代码': getattr(r, 'code', ''),
                '名称': getattr(r, 'name', ''),
                '评分': getattr(r, 'sentiment_score', 0),
                '建议': getattr(r, 'operation_advice', ''),
                '趋势': getattr(r, 'trend_prediction', ''),
                '买点': self._extract_price(sniper.get('ideal_buy')),
                '卖点': self._extract_price(sniper.get('take_profit')),
                '止损': self._extract_price(sniper.get('stop_loss')),
            })

        df = pd.DataFrame(data).sort_values('评分', ascending=False)
        if df.empty:
            return None

        row_h, header_h = 0.45, 0.7
        fig_h = len(df) * row_h + header_h + 1.2
        fig, ax = plt.subplots(figsize=(12, fig_h))
        ax.axis('off')

        date_str = datetime.now().strftime('%Y-%m-%d')
        plt.title(
            f"A股智能分析汇总 ({date_str})",
            fontsize=15, pad=18, fontweight='bold', color='#2c3e50',
        )

        table = ax.table(
            cellText=df.values, colLabels=df.columns,
            cellLoc='center', loc='center', bbox=[0, 0, 1, 1],
        )
        table.auto_set_font_size(False)
        table.set_fontsize(10)

        for (row, col), cell in table.get_celld().items():
            cell.set_edgecolor('#e0e0e0')
            cell.set_linewidth(0.4)
            if row == 0:  # 表头
                cell.set_facecolor('#2c3e50')
                cell.set_text_props(color='white', weight='bold', size=10)
                cell.set_height(0.07)
            else:
                cell.set_height(0.05)
                if col == 3:  # 建议列 — 颜色编码
                    text = str(df.iloc[row - 1]['建议'])
                    if any(kw in text for kw in ('买', '增', '加')):
                        cell.set_text_props(color='#2e7d32', weight='bold')
                        cell.set_facecolor('#e8f5e9')
                    elif any(kw in text for kw in ('卖', '减', '出')):
                        cell.set_text_props(color='#c62828', weight='bold')
                        cell.set_facecolor('#ffebee')
                    else:
                        cell.set_text_props(color='#f57f17', weight='bold')
                        cell.set_facecolor('#fffde7')

        buf = io.BytesIO()
        plt.savefig(buf, format='png', bbox_inches='tight', dpi=150, pad_inches=0.2)
        plt.close()
        buf.seek(0)
        image_bytes = buf.getvalue()
        logger.info("matplotlib 汇总图片生成成功，大小: %d 字节", len(image_bytes))
        return image_bytes

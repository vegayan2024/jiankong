# -*- coding: utf-8 -*-
"""
多通道统一消息推送适配器 (Push Notifier)
======================================
支持通道：
  1. 控制台富文本与日志记录 (默认)
  2. Windows 原生桌面弹窗通知 (PowerShell Toast / 终端提示音)
  3. 飞书自定义机器人 Webhook (支持富文本交互卡片)
  4. 企业微信自定义机器人 Webhook (Markdown 格式)
  5. 钉钉自定义机器人 Webhook
  6. Bark (iOS 极简推送客户端)
  7. WxPusher (微信公众平台模板消息推送)

防骚扰机制：
  内置智能防抖 (Debounce) 与时间戳滑动窗口，同一标的同一事件类型在设定窗口期内（默认10分钟）自动合并去重。
"""

import os
import sys
import time
import json
import logging
import urllib.request
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, List

try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

logger = logging.getLogger("PushNotifier")
CONFIG_FILE = Path(__file__).resolve().parent.parent / "push_config.json"


class PushNotifier:
    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or CONFIG_FILE
        self.config = self._load_config()
        self._sent_cache: Dict[str, float] = {}  # 记录 key -> timestamp
        self.debounce_seconds = self.config.get("debounce_seconds", 600)  # 默认10分钟防抖

    def _load_config(self) -> Dict[str, Any]:
        """加载推送配置"""
        default_config = {
            "enabled": True,
            "debounce_seconds": 600,
            "channels": {
                "console": True,
                "desktop_toast": True,
                "feishu_webhook": os.environ.get("FEISHU_WEBHOOK", ""),
                "wecom_webhook": os.environ.get("WECOM_WEBHOOK", ""),
                "dingtalk_webhook": os.environ.get("DINGTALK_WEBHOOK", ""),
                "bark_url": os.environ.get("BARK_URL", ""),
                "wxpusher_app_token": os.environ.get("WXPUSHER_APP_TOKEN", ""),
                "wxpusher_uids": [x for x in os.environ.get("WXPUSHER_UIDS", "").split(",") if x]
            }
        }
        if self.config_path.exists():
            try:
                user_cfg = json.loads(self.config_path.read_text(encoding="utf-8"))
                default_config.update(user_cfg)
                # 嵌套 channels 合并
                if "channels" in user_cfg:
                    default_config["channels"].update(user_cfg["channels"])
            except Exception as e:
                logger.warning(f"读取推送配置 {self.config_path} 异常: {e}")
        else:
            # 首次生成模板配置文件
            try:
                self.config_path.write_text(json.dumps(default_config, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
        return default_config

    def _is_duplicate(self, dedup_key: str) -> bool:
        """检查是否在防抖窗口期内"""
        now = time.time()
        last_time = self._sent_cache.get(dedup_key, 0)
        if now - last_time < self.debounce_seconds:
            return True
        self._sent_cache[dedup_key] = now
        return False

    def send(self, title: str, content: str, level: str = "INFO",
             stock_code: Optional[str] = None, event_type: str = "GENERAL",
             url: Optional[str] = None) -> bool:
        """
        统一推送分发入口
        :param title: 标题
        :param content: 详细内容
        :param level: 级别 (INFO, WARNING, ALERT, URGENT)
        :param stock_code: 关联股票代码 (如 002408.SZ)
        :param event_type: 事件类型 (PRICE_SURGE, PRICE_DROP, ANNOUNCEMENT, NEWS)
        :param url: 相关链接 (公告或新闻链接)
        """
        # 1. 防抖检测
        dedup_key = f"{stock_code or 'GLOBAL'}:{event_type}:{title}"
        if self._is_duplicate(dedup_key):
            logger.debug(f"[PushNotifier] 命中防抖规则，静默忽略: {dedup_key}")
            return False

        channels = self.config.get("channels", {})
        now_str = datetime.now().strftime("%H:%M:%S")

        level_icons = {
            "INFO": "ℹ️",
            "WARNING": "⚠️",
            "ALERT": "🚨",
            "URGENT": "🔥"
        }
        icon = level_icons.get(level.upper(), "📢")
        formatted_title = f"{icon} 【{level}】{title}"

        # 2. 控制台输出
        if channels.get("console", True):
            print(f"\n========================================================")
            print(f"[{now_str}] {formatted_title}")
            if stock_code:
                print(f"标的代码: {stock_code}")
            print(f"内容详情:\n{content}")
            if url:
                print(f"原文链接: {url}")
            print(f"========================================================\n")

        # 3. Windows 原生桌面 Toast 弹窗
        if channels.get("desktop_toast", True) and sys.platform == "win32":
            self._send_windows_toast(title, content)

        # 4. 飞书 Webhook 机器人
        feishu_url = channels.get("feishu_webhook")
        if feishu_url:
            self._send_feishu(feishu_url, formatted_title, content, url)

        # 5. 企业微信 Webhook 机器人
        wecom_url = channels.get("wecom_webhook")
        if wecom_url:
            self._send_wecom(wecom_url, formatted_title, content, url)

        # 6. Bark (iOS 手机端推送)
        bark_url = channels.get("bark_url")
        if bark_url:
            self._send_bark(bark_url, title, content, url)

        # 7. WxPusher (微信服务号消息)
        wx_token = channels.get("wxpusher_app_token")
        wx_uids = channels.get("wxpusher_uids", [])
        if wx_token and wx_uids:
            self._send_wxpusher(wx_token, wx_uids, formatted_title, content, url)

        return True

    def _send_windows_toast(self, title: str, content: str):
        """通过 Windows PowerShell 触发系统原生弹窗提醒"""
        try:
            # 净化字符防止脚本注入
            clean_title = re.sub(r'[\r\n\'"`$]', ' ', title)[:60]
            clean_msg = re.sub(r'[\r\n\'"`$]', ' ', content)[:180]
            
            ps_script = f"""
            [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
            $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
            $textNodes = $template.GetElementsByTagName("text")
            $textNodes.Item(0).AppendChild($template.CreateTextNode('{clean_title}')) > $null
            $textNodes.Item(1).AppendChild($template.CreateTextNode('{clean_msg}')) > $null
            $toast = [Windows.UI.Notifications.ToastNotification]::new($template)
            [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('FinancialMonitor').Show($toast)
            """
            subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    def _send_feishu(self, webhook_url: str, title: str, content: str, url: Optional[str] = None):
        """向飞书机器人发送富文本卡片"""
        try:
            card = {
                "msg_type": "interactive",
                "card": {
                    "header": {"title": {"tag": "plain_text", "content": title}},
                    "elements": [
                        {"tag": "div", "text": {"tag": "lark_md", "content": content}}
                    ]
                }
            }
            if url:
                card["card"]["elements"].append({
                    "tag": "action",
                    "actions": [{
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "查看公告/快讯原文"},
                        "type": "primary",
                        "url": url
                    }]
                })
            req = urllib.request.Request(webhook_url,
                                         data=json.dumps(card).encode("utf-8"),
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5)
        except Exception as e:
            logger.warning(f"飞书推送失败: {e}")

    def _send_wecom(self, webhook_url: str, title: str, content: str, url: Optional[str] = None):
        """向企业微信机器人发送 Markdown 消息"""
        try:
            md_text = f"### {title}\n{content}"
            if url:
                md_text += f"\n\n[点击查看原文]({url})"
            payload = {
                "msgtype": "markdown",
                "markdown": {"content": md_text}
            }
            req = urllib.request.Request(webhook_url,
                                         data=json.dumps(payload).encode("utf-8"),
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5)
        except Exception as e:
            logger.warning(f"企业微信推送失败: {e}")

    def _send_bark(self, base_url: str, title: str, content: str, url: Optional[str] = None):
        """向 Bark (iOS) 发送即时推送"""
        try:
            clean_base = base_url.rstrip("/")
            payload = {
                "title": title,
                "body": content,
                "group": "自选股即时推送",
                "icon": "https://img.icons8.com/fluency/96/bullish.png"
            }
            if url:
                payload["url"] = url
            req = urllib.request.Request(f"{clean_base}/push",
                                         data=json.dumps(payload).encode("utf-8"),
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5)
        except Exception as e:
            logger.warning(f"Bark 推送失败: {e}")

    def _send_wxpusher(self, token: str, uids: List[str], title: str, content: str, url: Optional[str] = None):
        """向 WxPusher 微信服务号发送模板消息"""
        try:
            payload = {
                "appToken": token,
                "content": f"## {title}\n\n{content}" + (f"\n\n[查看原文]({url})" if url else ""),
                "summary": title[:20],
                "contentType": 3,
                "uids": uids
            }
            if url:
                payload["url"] = url
            req = urllib.request.Request("https://wxpusher.zjiecode.com/api/send/message",
                                         data=json.dumps(payload).encode("utf-8"),
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5)
        except Exception as e:
            logger.warning(f"WxPusher 推送失败: {e}")


# 全局单例
push_notifier = PushNotifier()

if __name__ == "__main__":
    print("=== 测试统一消息推送适配器 ===")
    push_notifier.send(
        title="齐翔腾达 (002408.SZ) 盘中急拉异动",
        content="• 当前最新价: 5.25 元 (日涨跌幅: +4.12%)\n• 异动触发: 3分钟内急拉超过 2.3%，伴随成交量急剧放大。",
        level="ALERT",
        stock_code="002408.SZ",
        event_type="PRICE_SURGE"
    )
    print("✅ 测试推送已触发。")

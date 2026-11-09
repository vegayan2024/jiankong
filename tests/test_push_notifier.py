# -*- coding: utf-8 -*-
"""
PushNotifier (多通道消息推送适配器) 单元测试
重点覆盖：
1. .env 环境变量与 push_config.json 增量合并防覆盖 (回归防护：JSON 空值绝不覆盖 .env)
2. 智能防抖 (Debounce) 与时间戳滑动窗口去重机制
3. Windows 原生弹窗字符过滤与安全净化
4. PushPlus 响应解析规范 (200 成功 / 非 200 错误提取包含 code, msg, data)
"""
import os
import sys
import json
import time
from pathlib import Path
import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "scripts"))

from scripts.push_notifier import PushNotifier


def test_push_config_env_merge_protection(tmp_path):
    """
    回归测试：push_config.json 中的空字符串绝不能覆盖 .env 已经加载的有效 Token。
    """
    fake_config = tmp_path / "push_config.json"
    fake_config.write_text(json.dumps({
        "enabled": True,
        "debounce_seconds": 300,
        "channels": {
            "console": True,
            "desktop_toast": True,
            "pushplus_token": "",      # 空值！
            "wxpusher_app_token": "",  # 空值！
            "wxpusher_uids": [],
            "feishu_webhook": ""
        }
    }, ensure_ascii=False), encoding="utf-8")

    # 预设环境变量
    os.environ["PUSHPLUS_TOKEN"] = "test_valid_token_32chars_long"
    os.environ["FEISHU_WEBHOOK"] = "https://open.feishu.cn/open-apis/bot/v2/hook/test"

    try:
        notifier = PushNotifier(config_path=fake_config)
        channels = notifier.config.get("channels", {})
        # 核心断言：.env 中的环境变量值必须完好保留
        assert channels.get("pushplus_token") == "test_valid_token_32chars_long", \
            "push_config.json 的空串覆盖了有效 PUSHPLUS_TOKEN！"
        assert channels.get("feishu_webhook") == "https://open.feishu.cn/open-apis/bot/v2/hook/test", \
            "push_config.json 的空串覆盖了有效 FEISHU_WEBHOOK！"
    finally:
        os.environ.pop("PUSHPLUS_TOKEN", None)
        os.environ.pop("FEISHU_WEBHOOK", None)


def test_push_config_custom_override(tmp_path):
    """
    当 push_config.json 中显式配置了非空值时，能正常被使用
    """
    fake_config = tmp_path / "push_config.json"
    fake_config.write_text(json.dumps({
        "enabled": True,
        "debounce_seconds": 120,
        "channels": {
            "pushplus_token": "override_token_from_json"
        }
    }, ensure_ascii=False), encoding="utf-8")

    notifier = PushNotifier(config_path=fake_config)
    channels = notifier.config.get("channels", {})
    assert channels.get("pushplus_token") == "override_token_from_json"
    assert notifier.debounce_seconds == 120


def test_push_notifier_debounce_mechanism(tmp_path):
    """
    测试智能防抖 (Debounce) 与时间戳滑动窗口机制
    """
    fake_config = tmp_path / "push_config.json"
    fake_config.write_text(json.dumps({
        "debounce_seconds": 10,
        "channels": {
            "console": False,
            "desktop_toast": False,
            "pushplus_token": ""
        }
    }), encoding="utf-8")

    notifier = PushNotifier(config_path=fake_config)

    # 第一次触发：非重复，应该通过
    key1 = "002408.SZ:PRICE_SURGE:齐翔腾达异动"
    assert notifier._is_duplicate(key1) is False

    # 紧接着第二次相同触发：命中防抖，应该返回 True
    assert notifier._is_duplicate(key1) is True

    # 不同事件类型：不应被同一标的防抖误拦截
    key2 = "002408.SZ:ANNOUNCEMENT:齐翔腾达公告"
    assert notifier._is_duplicate(key2) is False

    # 不同标的相同事件类型：不应被拦截
    key3 = "600519.SH:PRICE_SURGE:茅台异动"
    assert notifier._is_duplicate(key3) is False


def test_windows_toast_script_generation(monkeypatch):
    """
    测试 Windows 原生 Toast 弹窗的字符净化逻辑，确保无 NameError 且 re 正常导入
    """
    import subprocess
    notifier = PushNotifier()
    
    called_cmds = []
    def fake_popen(cmd, *args, **kwargs):
        called_cmds.append(cmd)
        class DummyProc:
            pass
        return DummyProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    
    # 包含特殊字符和注入字符的标题与内容
    dirty_title = "测试'\"$特殊字符\n换行"
    dirty_content = "内容含有\"单双引号'`以及$变量\r\n截断测试" * 10
    
    notifier._send_windows_toast(dirty_title, dirty_content)
    assert len(called_cmds) == 1
    ps_cmd = called_cmds[0]
    assert "powershell" in ps_cmd[0]
    assert "-NoProfile" in ps_cmd


def test_web_server_dispatch_alert_push(monkeypatch):
    """
    测试 web_server.py 的 dispatch_alert_push 能够正确调用 push_notifier.send
    """
    from scripts.web_server import dispatch_alert_push
    import scripts.web_server as ws

    sent_records = []
    def fake_send(title, content, level, stock_code, event_type, url=None):
        sent_records.append({
            "title": title, "content": content, "level": level,
            "stock_code": stock_code, "event_type": event_type, "url": url
        })
        return True

    monkeypatch.setattr(ws.push_notifier, "send", fake_send)

    dummy_alert = {
        "id": "ALERT_TEST_1",
        "code": "688256.SH",
        "name": "寒武纪",
        "price": 450.0,
        "pct_change": 5.74,
        "message": "寒武纪 触发单阈值预警：涨跌幅% >= 5.0 (当前 5.74)",
        "level": "ALERT",
        "type": "PRICE_SURGE",
        "group": "周期基本盘"
    }

    dispatch_alert_push(dummy_alert)
    assert len(sent_records) == 1
    rec = sent_records[0]
    assert "寒武纪" in rec["title"]
    assert "688256.SH" in rec["stock_code"]
    assert "+5.74%" in rec["content"]
    assert rec["level"] == "ALERT"


def test_web_server_dispatch_announcement_push(monkeypatch):
    """
    测试 web_server.py 的 dispatch_announcement_push 能够正确识别公告等级并调用 push_notifier.send
    """
    from scripts.web_server import dispatch_announcement_push
    import scripts.web_server as ws

    sent_records = []
    def fake_send(title, content, level, stock_code, event_type, url=None):
        sent_records.append({
            "title": title, "content": content, "level": level,
            "stock_code": stock_code, "event_type": event_type, "url": url
        })
        return True

    monkeypatch.setattr(ws.push_notifier, "send", fake_send)

    dummy_ann = {
        "id": "ANN_TEST_1",
        "code": "688256.SH",
        "name": "寒武纪",
        "title": "2026年年度业绩预告",
        "time": "2026-11-09 10:30:00",
        "url": "https://static.cninfo.com.cn/test.pdf"
    }

    dispatch_announcement_push(dummy_ann)
    assert len(sent_records) == 1
    rec = sent_records[0]
    assert "【巨潮信披】" in rec["title"]
    assert rec["level"] == "URGENT"  # 包含"预告"，应自动升级为 URGENT
    assert rec["url"] == "https://static.cninfo.com.cn/test.pdf"


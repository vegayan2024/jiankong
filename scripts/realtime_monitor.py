# -*- coding: utf-8 -*-
"""
资产即时监控与推送守护程序 (Real-time Financial Monitor Daemon) - Jiankong Edition
================================================================================
包含三大标的类型：
  - 【主要指数】：上证、沪深300、创业板指、科创50、恒生指数、恒科等
  - 【热点ETF】：近2年高波动/高关注黄金、半导体、芯片、算力CPO、机器人、创新药等
  - 【个股标的】：Terminal产业基本盘 + 2年高波动风格题材领涨龙头

核心监控维度：
  1. 【盘中量价异动】：A股 + 港股毫秒级量价扫描（单日阈值、短时急拉/急跌、涨跌停监控）；
  2. 【法定信披公告】：巨潮资讯网 (CNINFO) A股法定公告权威监听（业绩预告、分红、增减持、重组等）；
  3. 【7x24 财经快讯】：实时财经电报流精准匹配自选股公司名与代码。
"""

import os
import re
import sys
import time
import json
import logging
import argparse
import urllib.request
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any, Set, Tuple

os.environ["NO_PROXY"] = "*"

try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("RealtimeMonitor")

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "scripts"))

from scripts.watchlist_manager import WatchlistManager
from scripts.push_notifier import push_notifier
from scripts.cninfo_client import cninfo_client

CORE_ANNOUNCEMENT_KEYWORDS = [
    "业绩预告", "业绩快报", "年度报告", "半年度报告", "一季度报告", "三季度报告",
    "净利润", "分红", "派息", "回购", "增持", "减持", "重大合同", "中标",
    "投资设立", "对外投资", "资产重组", "发行股份", "立案", "处罚", "监管函",
    "停牌", "复牌", "要约收购", "控制权变更", "被动减持", "违约", "诉讼"
]


class RealtimeMonitorDaemon:
    def __init__(self, interval: int = 20):
        self.interval = interval
        self.manager = WatchlistManager()
        self.price_history: Dict[str, List[Tuple[float, float]]] = {}  # code -> [(timestamp, price)]
        self.seen_announcements: Set[str] = set()
        self.seen_news_ids: Set[str] = set()

    def get_active_items(self) -> List[Dict[str, Any]]:
        """获取当前所有启用的自选标的 (含指数、ETF与股票)"""
        self.manager.load()
        return [it for it in self.manager.items if it.get("enabled", True)]

    # ==========================================
    # 1. 盘中量价异动检测 (指数、ETF、A股、港股)
    # ==========================================
    def check_intraday_quotes(self, items: List[Dict[str, Any]]):
        """批量获取行情并评估异动"""
        if not items:
            return

        chunk_size = 50
        now_ts = time.time()

        for i in range(0, len(items), chunk_size):
            chunk = items[i:i + chunk_size]
            symbols_map = {}
            for s in chunk:
                market = s.get("market", "").upper()
                sym = s.get("symbol", "")
                if market == "HK":
                    t_sym = f"hk{sym}"
                elif market in ["SH", "SZ", "BJ"]:
                    t_sym = f"{market.lower()}{sym}"
                else:
                    t_sym = sym.lower()
                symbols_map[t_sym.lower()] = s

            url = f"http://qt.gtimg.cn/q={','.join(symbols_map.keys())}"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=6) as resp:
                    text = resp.read().decode("gbk", errors="ignore")
                    for line in text.splitlines():
                        line = line.strip()
                        if not line or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        t_sym = k.replace("v_", "").strip().lower()
                        if t_sym not in symbols_map:
                            continue

                        cfg = symbols_map[t_sym]
                        parts = v.strip('";').split("~")
                        if len(parts) < 35:
                            continue

                        name = parts[1].strip() or cfg.get("name", "")
                        price = float(parts[3]) if parts[3] else 0.0
                        prev_close = float(parts[4]) if parts[4] else 0.0
                        pct_change = float(parts[32]) if parts[32] else 0.0
                        high_price = float(parts[33]) if parts[33] else 0.0
                        low_price = float(parts[34]) if parts[34] else 0.0

                        if price <= 0:
                            continue

                        code = cfg["code"]
                        asset_type = cfg.get("asset_type", "stock")
                        unit = "点" if asset_type == "index" else ("元" if cfg.get("market") != "HK" else "港元")

                        # 记录历史价格滑动窗口（近10次）
                        if code not in self.price_history:
                            self.price_history[code] = []
                        self.price_history[code].append((now_ts, price))
                        if len(self.price_history[code]) > 10:
                            self.price_history[code].pop(0)

                        threshold = cfg.get("alert_threshold_pct", 3.0)

                        # A. 单日涨跌幅超阈值提醒
                        if abs(pct_change) >= threshold:
                            direction = "🚀 大幅上涨" if pct_change > 0 else "📉 大幅下挫"
                            event_type = "PRICE_SURGE" if pct_change > 0 else "PRICE_DROP"
                            level = "URGENT" if abs(pct_change) >= 7.0 else "ALERT"

                            type_label = "【基准指数】" if asset_type == "index" else ("【热点ETF】" if asset_type == "etf" else "【个股异动】")

                            push_notifier.send(
                                title=f"{type_label} {name} ({code}) {direction} {pct_change:+.2f}%",
                                content=f"• 当前最新值: {price} {unit} (昨收: {prev_close} {unit})\n"
                                        f"• 今日最高: {high_price} {unit} | 最低: {low_price} {unit}\n"
                                        f"• 预警阈值: ±{threshold}% | 分类: {cfg.get('group', '自选')}",
                                level=level,
                                stock_code=code,
                                event_type=event_type
                            )

                        # B. 短时急拉/急跌提醒 (过去 30秒~5分钟 内变化超阈值)
                        history = self.price_history[code]
                        if len(history) >= 3:
                            base_time, base_price = history[0]
                            time_diff = now_ts - base_time
                            if 30 <= time_diff <= 360 and base_price > 0:
                                rapid_change = ((price - base_price) / base_price) * 100
                                trigger_threshold = 1.0 if asset_type == "index" else 2.0
                                if abs(rapid_change) >= trigger_threshold:
                                    rapid_dir = "⚡ 盘中急拉" if rapid_change > 0 else "💥 盘中急跌"
                                    push_notifier.send(
                                        title=f"{name} ({code}) {rapid_dir} {rapid_change:+.2f}% (近{int(time_diff)}秒)",
                                        content=f"• 当前值: {price} {unit} (前序值: {base_price} {unit})\n"
                                                f"• 今日总涨幅: {pct_change:+.2f}%\n"
                                                f"• 短时剧烈波动，请关注成交变化！",
                                        level="ALERT",
                                        stock_code=code,
                                        event_type="RAPID_MOVE"
                                    )

            except Exception as e:
                logger.warning(f"获取行情异常 (chunk {i}): {e}")

    # ==========================================
    # 2. 巨潮资讯 (CNINFO) 法定信披监听
    # ==========================================
    def check_cninfo_announcements(self, items: List[Dict[str, Any]]):
        """轮询巨潮资讯今日最新披露 (仅针对监控公司或基金)"""
        stocks = [s for s in items if s.get("asset_type") in ["stock", "etf"] and s.get("monitor_announcement", True)]
        active_codes = {s["symbol"]: s for s in stocks}
        today_str = datetime.today().strftime("%Y-%m-%d")

        exchanges = [
            ("szse", "sz", "深市"),
            ("sse", "sh", "沪市"),
            ("bj", "bj", "北交所")
        ]

        for column, plate, label in exchanges:
            try:
                query_data = {
                    "pageNum": 1,
                    "pageSize": 50,
                    "tabName": "fulltext",
                    "column": column,
                    "plate": plate,
                    "seDate": f"{today_str}~{today_str}"
                }
                resp = cninfo_client.session.post(
                    "http://www.cninfo.com.cn/new/hisAnnouncement/query",
                    data=query_data,
                    timeout=8
                )
                if resp.status_code == 200:
                    announcements = resp.json().get("announcements", []) or []
                    for ann in announcements:
                        ann_id = str(ann.get("announcementId", ""))
                        if not ann_id or ann_id in self.seen_announcements:
                            continue
                        self.seen_announcements.add(ann_id)

                        sec_code = str(ann.get("secCode", ""))
                        if sec_code in active_codes:
                            cfg = active_codes[sec_code]
                            title = ann.get("announcementTitle", "").strip()
                            adjunct_url = ann.get("adjunctUrl", "")
                            full_url = f"https://static.cninfo.com.cn/{adjunct_url}" if adjunct_url else None
                            pub_time = ann.get("announcementTime", "")

                            level = "URGENT" if any(w in title for w in ["预告", "快报", "分红", "重组", "立案", "减持", "回购"]) else "INFO"

                            push_notifier.send(
                                title=f"【巨潮信披】{cfg['name']} ({cfg['code']})",
                                content=f"• 公告标题: {title}\n"
                                        f"• 披露时间: {pub_time}\n"
                                        f"• 官方来源: 巨潮资讯网 (CSRC 法定基准)",
                                level=level,
                                stock_code=cfg["code"],
                                event_type="ANNOUNCEMENT",
                                url=full_url
                            )
            except Exception as e:
                logger.warning(f"轮询巨潮公告 ({label}) 异常: {e}")

    # ==========================================
    # 3. 7x24 实时财经电报流监控
    # ==========================================
    def check_flash_news(self, items: List[Dict[str, Any]]):
        """抓取新浪 7x24 财经电报并匹配自选标的"""
        try:
            url = "https://zhibo.sina.com.cn/api/zhibo/feed?zhibo_id=152&page=1&page_size=20"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                feed_list = data.get("result", {}).get("data", {}).get("feed", {}).get("list", [])

                for item in feed_list:
                    news_id = str(item.get("id", ""))
                    if not news_id or news_id in self.seen_news_ids:
                        continue
                    self.seen_news_ids.add(news_id)

                    text_content = item.get("rich_text", "") or item.get("text", "")
                    create_time = item.get("create_time", "")

                    for s in items:
                        if not s.get("monitor_news", True):
                            continue
                        name = s.get("name", "")
                        sym = s.get("symbol", "")
                        if (name and len(name) >= 2 and name in text_content) or (sym and len(sym) >= 5 and sym in text_content):
                            clean_text = re.sub(r"<[^>]+>", "", text_content).strip()
                            push_notifier.send(
                                title=f"【快讯命中】{name} ({s['code']})",
                                content=f"• 发布时间: {create_time}\n"
                                        f"• 消息正文: {clean_text[:200]}...",
                                level="INFO",
                                stock_code=s["code"],
                                event_type="FLASH_NEWS"
                            )
        except Exception as e:
            logger.warning(f"获取 7x24 快讯异常: {e}")

    def run_sweep(self):
        """执行一轮完整巡检"""
        items = self.get_active_items()
        logger.info(f"🔍 开始执行全维度巡检 (当前监控标的: {len(items)} 个，含指数、ETF与个股)...")

        # 1. 量价异动
        self.check_intraday_quotes(items)

        # 2. 巨潮公告
        self.check_cninfo_announcements(items)

        # 3. 7x24 快讯
        self.check_flash_news(items)

        logger.info("🏁 本轮巡检完成。")

    def run_daemon(self):
        """以守护进程模式常驻运行"""
        print(f"🚀 自选资产即时监控系统已启动 (Jiankong Edition)！")
        print(f"• 监控池配置文件: {self.manager.filepath}")
        print(f"• 轮询周期: 每 {self.interval} 秒")
        print(f"• 标的序列涵盖：主要基准指数、2年热点高波动ETF、风格题材高波动股、Terminal产业基本盘")
        print("按 Ctrl+C 可停止运行。\n")

        while True:
            try:
                self.run_sweep()
                time.sleep(self.interval)
            except KeyboardInterrupt:
                print("\n🛑 监控守护进程已手动停止。")
                break
            except Exception as e:
                logger.error(f"守护主循环异常: {e}", exc_info=True)
                time.sleep(5)


def main():
    parser = argparse.ArgumentParser(description="自选资产即时信息监控系统守护进程")
    parser.add_argument("--once", action="store_true", help="单次巡检后立即退出")
    parser.add_argument("--daemon", action="store_true", help="守护进程模式持续运行")
    parser.add_argument("--interval", type=int, default=20, help="轮询间隔秒数 (默认 20 秒)")
    args = parser.parse_args()

    monitor = RealtimeMonitorDaemon(interval=args.interval)

    if args.once or not args.daemon:
        monitor.run_sweep()
    else:
        monitor.run_daemon()


if __name__ == "__main__":
    main()

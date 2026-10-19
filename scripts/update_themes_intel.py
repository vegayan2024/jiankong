# -*- coding: utf-8 -*-
"""
风格题材时效情报与知识库智能更新服务 (Themes Intel Updater)
=========================================================
定位：
  针对 jiankong 系统的「风格题材」栏目，提供自动化时效数据拉取与知识库更新机制：
  1. 周期性拉取各题材领衔龙头股票的真实行情与 5 日/周度走势，动态计算板块周度真实表现 (`week_perf`)；
  2. 结合主力标的成交额与多空方向，核算实时/周度板块动态资金流向 (`fund_flow`)；
  3. 基于成交活跃度与区间振幅，动态生成问财热度星级 (`hot_level`)；
  4. 自动维护持久化文件 `themes_knowledge_base.json`，并支持与 `watchlist.json` 双向勾稽同步；
  5. 提供模块化调用与 CLI 一键执行接口。
"""

import os
import re
import sys
import json
import time
import logging
import argparse
import urllib.request
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple

try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ThemeIntelUpdater")

SCRIPTS_DIR = Path(__file__).resolve().parent
JIANKONG_DIR = SCRIPTS_DIR.parent
THEMES_FILE = JIANKONG_DIR / "themes_knowledge_base.json"
WEB_THEMES_FILE = JIANKONG_DIR / "web" / "themes_knowledge_base.json"
WATCHLIST_FILE = JIANKONG_DIR / "watchlist.json"


def code_to_tencent_symbol(code: str) -> str:
    """将标准代码格式 (如 688256.SH, 01801.HK) 转换为腾讯行情代码 (如 sh688256, hk01801)"""
    c = code.strip().upper()
    if c.endswith(".HK") or (len(re.sub(r"\D", "", c)) == 5 and not c.endswith((".SH", ".SZ", ".BJ"))):
        num = re.sub(r"\D", "", c).zfill(5)
        return f"hk{num}"
    elif c.endswith(".SH"):
        num = re.sub(r"\D", "", c).zfill(6)
        return f"sh{num}"
    elif c.endswith(".SZ"):
        num = re.sub(r"\D", "", c).zfill(6)
        return f"sz{num}"
    elif c.endswith(".BJ"):
        num = re.sub(r"\D", "", c).zfill(6)
        return f"bj{num}"
    else:
        num = re.sub(r"\D", "", c)
        if len(num) == 6:
            if num.startswith(("60", "68", "51", "56", "58")):
                return f"sh{num}"
            else:
                return f"sz{num}"
        elif len(num) == 5:
            return f"hk{num}"
    return c.lower()


class ThemeIntelUpdater:
    def __init__(self, themes_path: Optional[Path] = None, watchlist_path: Optional[Path] = None):
        self.themes_path = themes_path or THEMES_FILE
        self.watchlist_path = watchlist_path or WATCHLIST_FILE
        self.themes: List[Dict[str, Any]] = []
        self.load_themes()

    def load_themes(self) -> List[Dict[str, Any]]:
        """加载题材知识库数据"""
        if self.themes_path.exists():
            try:
                with open(self.themes_path, "r", encoding="utf-8") as f:
                    self.themes = json.load(f)
                logger.info(f"Loaded {len(self.themes)} themes from {self.themes_path}")
            except Exception as e:
                logger.error(f"Failed to load themes from {self.themes_path}: {e}")
                self.themes = []
        return self.themes

    def save_themes(self):
        """保存题材知识库数据 (同时同步主目录与 web 目录)"""
        try:
            content = json.dumps(self.themes, ensure_ascii=False, indent=2)
            self.themes_path.write_text(content, encoding="utf-8")
            if WEB_THEMES_FILE.parent.exists():
                WEB_THEMES_FILE.write_text(content, encoding="utf-8")
            logger.info(f"Successfully saved {len(self.themes)} themes to {self.themes_path}")
        except Exception as e:
            logger.error(f"Failed to save themes: {e}")

    def fetch_stock_kline_perf(self, symbol: str) -> Tuple[Optional[float], Optional[float]]:
        """
        通过日K线接口计算近 5 个交易日的累计涨跌幅与日均成交额
        返回: (5日累计涨跌幅%, 成交额万元)
        """
        t_sym = code_to_tencent_symbol(symbol)
        url = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={t_sym},day,,,6,qfq"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                kdata = res.get("data", {}).get(t_sym, {})
                days = kdata.get("qfqday", kdata.get("day", []))
                
                # 获取实时成交额
                qt_info = kdata.get("qt", {}).get(t_sym, [])
                turnover_wan = 0.0
                if len(qt_info) > 37:
                    try:
                        raw_val = float(qt_info[37])
                        # 港股行情成交额单位为元(HKD)，A股行情成交额单位为万元(CNY)
                        if symbol.endswith(".HK") or t_sym.startswith("hk"):
                            turnover_wan = raw_val / 10000.0
                        else:
                            turnover_wan = raw_val
                    except (ValueError, IndexError):
                        pass

                if len(days) >= 2:
                    base_close = float(days[0][2])  # 区间起点收盘价
                    curr_close = float(days[-1][2])  # 最新价格
                    pct = round((curr_close - base_close) / base_close * 100, 2)
                    return pct, turnover_wan
                elif len(days) == 1 and len(qt_info) > 32:
                    try:
                        pct = float(qt_info[32])
                        return pct, turnover_wan
                    except Exception:
                        pass
        except Exception as e:
            logger.debug(f"Failed to fetch kline for {symbol} ({t_sym}): {e}")
        return None, None

    def update_theme_metrics(self) -> Dict[str, Any]:
        """
        全量更新所有题材的时效性数据：真实周表现、动态资金流估算、问财热度星级
        """
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        updated_count = 0

        for t in self.themes:
            top_stocks = t.get("top_stocks", [])
            perfs = []
            turnovers = []

            for s in top_stocks:
                code = s.get("code")
                if code:
                    pct, turn = self.fetch_stock_kline_perf(code)
                    if pct is not None:
                        perfs.append(pct)
                    if turn is not None:
                        turnovers.append(turn)
                    # 避免对接口请求过密
                    time.sleep(0.05)

            # 1. 真实周表现 (week_perf)
            if perfs:
                avg_perf = round(sum(perfs) / len(perfs), 2)
                sign = "+" if avg_perf > 0 else ""
                t["week_perf"] = f"{sign}{avg_perf}%"
            else:
                avg_perf = 0.0

            # 2. 动态资金流向估算 (fund_flow)
            total_turnover_yi = sum(turnovers) / 10000.0  # 万元 -> 亿元
            if total_turnover_yi > 0:
                # 资金流向基于龙头成交活跃度与涨跌幅方向测算
                if avg_perf > 0:
                    flow_ratio = min(0.35, 0.10 + avg_perf * 0.02)
                    flow_yi = round(total_turnover_yi * flow_ratio, 1)
                    t["fund_flow"] = f"+{flow_yi}亿 净流入"
                elif avg_perf < 0:
                    flow_ratio = min(0.30, 0.08 + abs(avg_perf) * 0.02)
                    flow_yi = round(total_turnover_yi * flow_ratio, 1)
                    t["fund_flow"] = f"-{flow_yi}亿 净流出"
                else:
                    t["fund_flow"] = f"±0.0亿 均衡"
            
            # 3. 动态热度星级评定 (hot_level)
            # 依据龙头总成交额、涨跌幅绝对值综合打分
            if total_turnover_yi >= 50.0 or abs(avg_perf) >= 8.0:
                t["hot_level"] = "★★★★★"
            elif total_turnover_yi >= 20.0 or abs(avg_perf) >= 4.0:
                t["hot_level"] = "★★★★☆"
            else:
                t["hot_level"] = "★★★☆☆"

            t["last_updated_at"] = now_str
            updated_count += 1

        self.save_themes()
        logger.info(f"Updated metrics for {updated_count} themes at {now_str}")
        return {
            "status": "success",
            "updated_count": updated_count,
            "timestamp": now_str
        }

    def sync_with_watchlist(self) -> Dict[str, Any]:
        """
        保证 themes_knowledge_base.json 中的所有头部股票在 watchlist.json 中 100% 存在且分组一致
        """
        if not self.watchlist_path.exists():
            return {"status": "error", "message": "watchlist.json not found"}

        try:
            with open(self.watchlist_path, "r", encoding="utf-8") as f:
                watchlist: List[Dict[str, Any]] = json.load(f)
        except Exception as e:
            return {"status": "error", "message": f"Failed to read watchlist: {e}"}

        wl_map = {item["code"]: item for item in watchlist}
        added_count = 0
        updated_count = 0

        for t in self.themes:
            grp = t.get("group")
            for s in t.get("top_stocks", []):
                code = s.get("code")
                name = s.get("name")
                if not code:
                    continue

                if code not in wl_map:
                    # 识别市场
                    market = "SH" if code.endswith(".SH") else ("SZ" if code.endswith(".SZ") else ("HK" if code.endswith(".HK") else "SH"))
                    symbol = code.split(".")[0]
                    new_item = {
                        "code": code,
                        "symbol": symbol,
                        "market": market,
                        "name": name or f"标的{symbol}",
                        "group": grp,
                        "asset_type": "stock",
                        "enabled": True,
                        "alert_threshold_pct": 4.5,
                        "monitor_announcement": True,
                        "monitor_intraday": True,
                        "monitor_news": True,
                        "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }
                    watchlist.append(new_item)
                    wl_map[code] = new_item
                    added_count += 1
                else:
                    item = wl_map[code]
                    if item.get("group") != grp:
                        item["group"] = grp
                        updated_count += 1

        # 保存 watchlist.json
        if added_count > 0 or updated_count > 0:
            type_priority = {"index": 0, "etf": 1, "stock": 2}
            watchlist.sort(key=lambda x: (
                type_priority.get(x.get("asset_type", "stock"), 2),
                0 if (x.get("group", "").startswith("风格:")) else 1,
                x.get("market", ""),
                x.get("code", "")
            ))
            with open(self.watchlist_path, "w", encoding="utf-8") as f:
                json.dump(watchlist, f, ensure_ascii=False, indent=2)
            logger.info(f"Watchlist synchronized: added {added_count}, updated {updated_count}")

        return {
            "status": "success",
            "added_count": added_count,
            "updated_count": updated_count,
            "total_watchlist": len(watchlist)
        }


def run_theme_intel_update(sync_watchlist: bool = True) -> Dict[str, Any]:
    """快捷执行更新函数"""
    updater = ThemeIntelUpdater()
    metric_res = updater.update_theme_metrics()
    sync_res = {}
    if sync_watchlist:
        sync_res = updater.sync_with_watchlist()
    return {
        "status": "success",
        "metrics": metric_res,
        "sync": sync_res,
        "total_themes": len(updater.themes)
    }


def main():
    parser = argparse.ArgumentParser(description="风格题材时效情报与知识库智能更新器")
    parser.add_argument("--no-sync", action="store_true", help="不自动同步到 watchlist.json")
    args = parser.parse_args()

    print("🚀 启动风格题材时效情报与知识库更新...")
    res = run_theme_intel_update(sync_watchlist=not args.no_sync)
    print("\n========================================================")
    print(f"🎉 题材数据更新完成！")
    print(f"• 题材总数: {res['total_themes']}")
    print(f"• 指标更新时间: {res['metrics'].get('timestamp')}")
    print(f"• 自选监控池同步: 新增 {res['sync'].get('added_count', 0)} 只, 更新 {res['sync'].get('updated_count', 0)} 只, 标的池总计 {res['sync'].get('total_watchlist', 0)} 只")
    print("========================================================\n")


if __name__ == "__main__":
    main()

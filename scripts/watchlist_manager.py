# -*- coding: utf-8 -*-
"""
自选监控池管理器 (Watchlist Manager) - jiankong 专属版
===================================================
支持：
  1. 股票标的 (A股 & 港股)
  2. 核心大盘与市场基准指数 (A股核心指数 + 港股恒生指数/恒生科技)
  3. 近2年核心热点与高弹性行业 ETF (半导体、CPO通信、黄金、机器人、创新药等)

分类体系：
  - 【主要指数】：上证指数、沪深300、中证500、中证1000、创业板指、科创50、恒生指数、恒生科技
  - 【热点ETF】：黄金ETF、恒生科技ETF、半导体ETF、芯片ETF、5G通信/CPO ETF、机器人ETF、创新药ETF等
  - 【风格题材盘 (高波动)】：算力光模块、低空经济、自主算力芯片、人形机器人、战略贵金属、创新药出海
  - 【Terminal 周期基本盘】：化工、矿业、民爆、造纸、农产品加工、通用资源
"""

import os
import re
import sys
import json
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

JIANKONG_DIR = Path(__file__).resolve().parent.parent
WATCHLIST_FILE = JIANKONG_DIR / "watchlist.json"
PARENT_DIR = JIANKONG_DIR.parent
TERMINAL_DIR = PARENT_DIR / "terminal"

# 知名指数代码映射
SPECIAL_INDICES = {
    "000001.SH": {"symbol": "000001", "market": "SH", "name": "上证指数", "tencent": "sh000001"},
    "000300.SH": {"symbol": "000300", "market": "SH", "name": "沪深300", "tencent": "sh000300"},
    "000905.SH": {"symbol": "000905", "market": "SH", "name": "中证500", "tencent": "sh000905"},
    "000852.SH": {"symbol": "000852", "market": "SH", "name": "中证1000", "tencent": "sh000852"},
    "000688.SH": {"symbol": "000688", "market": "SH", "name": "科创50", "tencent": "sh000688"},
    "399006.SZ": {"symbol": "399006", "market": "SZ", "name": "创业板指", "tencent": "sz399006"},
    "HSI.HK": {"symbol": "HSI", "market": "HK", "name": "恒生指数", "tencent": "hkHSI"},
    "HSTECH.HK": {"symbol": "HSTECH", "market": "HK", "name": "恒生科技指数", "tencent": "hkHSTECH"}
}


def normalize_stock_code(raw: str) -> Optional[Dict[str, str]]:
    """规范化代码（支持指数、ETF、股票）"""
    raw_s = str(raw).strip().upper()

    # 1. 检查特殊指数
    if raw_s in SPECIAL_INDICES:
        item = SPECIAL_INDICES[raw_s]
        return {
            "code": raw_s,
            "symbol": item["symbol"],
            "market": item["market"],
            "tencent_symbol": item["tencent"],
            "asset_type": "index"
        }
    if raw_s in ["HSI", "恒生指数", "HKHSI"]:
        return {
            "code": "HSI.HK", "symbol": "HSI", "market": "HK",
            "tencent_symbol": "hkHSI", "asset_type": "index"
        }
    if raw_s in ["HSTECH", "恒生科技", "恒生科技指数"]:
        return {
            "code": "HSTECH.HK", "symbol": "HSTECH", "market": "HK",
            "tencent_symbol": "hkHSTECH", "asset_type": "index"
        }

    # 2. 港股股票识别
    if raw_s.endswith(".HK") or raw_s.startswith("HK") or (len(re.sub(r"\D", "", raw_s)) == 5 and not raw_s.endswith((".SH", ".SZ", ".BJ"))):
        digits = re.sub(r"\D", "", raw_s)
        if not digits:
            return None
        symbol_5 = digits.zfill(5)
        return {
            "code": f"{symbol_5}.HK",
            "symbol": symbol_5,
            "market": "HK",
            "tencent_symbol": f"hk{symbol_5}",
            "asset_type": "stock"
        }

    # 3. A股代码 (6位)
    digits = re.sub(r"\D", "", raw_s)
    if not digits:
        return None

    symbol_6 = digits.zfill(6)
    
    # 指数代码处理
    if symbol_6 in ["000001", "000300", "000905", "000852", "000688"] and (raw_s.endswith(".SH") or "指数" in raw_s or raw_s == symbol_6 and symbol_6 != "000001"):
        return {
            "code": f"{symbol_6}.SH", "symbol": symbol_6, "market": "SH",
            "tencent_symbol": f"sh{symbol_6}", "asset_type": "index"
        }
    if symbol_6 == "399006":
        return {
            "code": "399006.SZ", "symbol": "399006", "market": "SZ",
            "tencent_symbol": "sz399006", "asset_type": "index"
        }

    # ETF 判断 (51xxxx, 15xxxx, 56xxxx, 58xxxx)
    is_etf = symbol_6.startswith(("51", "15", "56", "58"))
    asset_type = "etf" if is_etf else "stock"

    if raw_s.endswith(".SH") or raw_s.startswith("SH"):
        market = "SH"
    elif raw_s.endswith(".SZ") or raw_s.startswith("SZ"):
        market = "SZ"
    elif raw_s.endswith(".BJ") or raw_s.startswith("BJ"):
        market = "BJ"
    else:
        if symbol_6.startswith(("60", "68", "51", "56", "58", "90")):
            market = "SH"
        elif symbol_6.startswith(("00", "30", "15", "20")):
            market = "SZ"
        elif symbol_6.startswith(("4", "8", "92")):
            market = "BJ"
        else:
            market = "SH"

    return {
        "code": f"{symbol_6}.{market}",
        "symbol": symbol_6,
        "market": market,
        "tencent_symbol": f"{market.lower()}{symbol_6}",
        "asset_type": asset_type
    }


def fetch_online_stock_name_and_price(market: str, symbol: str) -> Tuple[Optional[str], Optional[float]]:
    """在线抓取标的最新名称与现价"""
    norm = normalize_stock_code(f"{symbol}.{market}" if not symbol.endswith(f".{market}") else symbol)
    if not norm:
        return None, None
    url = f"http://qt.gtimg.cn/q={norm['tencent_symbol']}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            text = resp.read().decode("gbk", errors="ignore")
            if "=" in text:
                val_str = text.split("=", 1)[1].strip('";')
                parts = val_str.split("~")
                if len(parts) > 3:
                    name = parts[1].strip()
                    try:
                        price = float(parts[3])
                    except (ValueError, IndexError):
                        price = 0.0
                    return name, price
    except Exception:
        pass
    return None, None


class WatchlistManager:
    def __init__(self, filepath: Optional[Path] = None):
        self.filepath = filepath or WATCHLIST_FILE
        self.items: List[Dict[str, Any]] = []
        self.load()

    def load(self) -> List[Dict[str, Any]]:
        if self.filepath.exists():
            try:
                data = json.loads(self.filepath.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    self.items = data
                    return self.items
            except Exception as e:
                print(f"⚠️ 读取 {self.filepath} 异常: {e}")
        self.items = []
        return self.items

    def save(self):
        # 排序：指数 -> ETF -> 风格题材 -> 基础盘股票
        type_priority = {"index": 0, "etf": 1, "stock": 2}
        self.items.sort(key=lambda x: (
            type_priority.get(x.get("asset_type", "stock"), 2),
            0 if (x.get("group", "").startswith("风格:")) else 1,
            x.get("market", ""),
            x.get("code", "")
        ))
        content = json.dumps(self.items, ensure_ascii=False, indent=2)
        # 原子替换写入：先写入临时文件，再原子替换目标文件，彻底杜绝断电或并发写损坏
        tmp_file = self.filepath.with_suffix(".tmp")
        try:
            tmp_file.write_text(content, encoding="utf-8")
            os.replace(tmp_file, self.filepath)
        except Exception as e:
            # 备选回退直接写入
            self.filepath.write_text(content, encoding="utf-8")

    def add(self, raw_code: str, name: Optional[str] = None, group: str = "自选",
            threshold: float = 3.0, asset_type: Optional[str] = None, enabled: bool = True) -> Dict[str, Any]:
        norm = normalize_stock_code(raw_code)
        if not norm:
            raise ValueError(f"无法识别的代码: {raw_code}")

        code = norm["code"]
        symbol = norm["symbol"]
        market = norm["market"]
        detected_type = asset_type or norm.get("asset_type", "stock")

        if not name:
            fetched_name, _ = fetch_online_stock_name_and_price(market, symbol)
            name = fetched_name or f"标的{symbol}"

        target = None
        for item in self.items:
            if item["code"] == code:
                target = item
                break

        if target:
            target["name"] = name
            target["group"] = group or target.get("group", "自选")
            target["alert_threshold_pct"] = threshold
            target["asset_type"] = detected_type
            target["enabled"] = enabled
            action = "更新"
        else:
            target = {
                "code": code,
                "symbol": symbol,
                "market": market,
                "name": name,
                "group": group or "自选",
                "asset_type": detected_type,
                "enabled": enabled,
                "alert_threshold_pct": threshold,
                "monitor_announcement": True if detected_type == "stock" else False,
                "monitor_intraday": True,
                "monitor_news": True,
                "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            self.items.append(target)
            action = "添加"

        self.save()
        print(f"✅ 成功{action}标的: [{target['code']}] {target['name']} | 类型: {target['asset_type']} | 分组: {target['group']}")
        return target

    def remove(self, query: str) -> Optional[Dict[str, Any]]:
        query_s = query.strip().upper()
        found = None
        for i, it in enumerate(self.items):
            if it["code"] == query_s or it["symbol"] == query_s or it["symbol"].lstrip("0") == query_s.lstrip("0") or it["name"] == query:
                found = self.items.pop(i)
                break
        if found:
            self.save()
            print(f"🗑️ 已成功移除标的: [{found['code']}] {found['name']}")
            return found
        return None

    def find(self, query: str) -> Optional[Dict[str, Any]]:
        q = query.strip().upper()
        for it in self.items:
            if it["code"] == q or it["symbol"] == q or it["symbol"].lstrip("0") == q.lstrip("0") or it["name"] == query.strip():
                return it
        return None

    def get_stats(self) -> Dict[str, Any]:
        total = len(self.items)
        enabled_count = sum(1 for it in self.items if it.get("enabled", True))
        market_counts = {}
        group_counts = {}
        type_counts = {}
        for it in self.items:
            m = it.get("market", "未知")
            g = it.get("group", "未分类")
            t = it.get("asset_type", "stock")
            market_counts[m] = market_counts.get(m, 0) + 1
            group_counts[g] = group_counts.get(g, 0) + 1
            type_counts[t] = type_counts.get(t, 0) + 1
        return {
            "total": total,
            "enabled": enabled_count,
            "markets": market_counts,
            "groups": group_counts,
            "types": type_counts
        }


# ==========================================
# 初始化全量监控序列 (Terminal公司池 + 风格题材 + 指数 + 热门ETF)
# ==========================================
def initialize_full_jiankong_pool(manager: WatchlistManager):
    print("🚀 正在构建完整监控序列 (主要指数 + 2年热点ETF + 风格题材 + Terminal公司池)...")

    # 1. 继承原有股票库 (若存在)
    src_watchlist = PARENT_DIR / "watchlist.json"
    if src_watchlist.exists():
        try:
            existing = json.loads(src_watchlist.read_text(encoding="utf-8"))
            for item in existing:
                item["asset_type"] = "stock"
                manager.items.append(item)
            print(f"• 已从原股票库继承 {len(existing)} 只公司标的")
        except Exception as e:
            print(f"• 继承原股票库异常: {e}")

    # 2. 注入核心大盘主要指数
    core_indices = [
        ("000001.SH", "上证指数", "主要指数", 1.5),
        ("000300.SH", "沪深300", "主要指数", 1.5),
        ("000905.SH", "中证500", "主要指数", 2.0),
        ("000852.SH", "中证1000", "主要指数", 2.0),
        ("000688.SH", "科创50", "主要指数", 2.5),
        ("399006.SZ", "创业板指", "主要指数", 2.0),
        ("HSI.HK", "恒生指数", "主要指数", 1.5),
        ("HSTECH.HK", "恒生科技指数", "主要指数", 2.0),
    ]
    for code, name, grp, th in core_indices:
        manager.add(code, name=name, group=grp, threshold=th, asset_type="index")

    # 3. 注入最近2年热点高弹性行业 ETF
    trending_etfs = [
        ("518880.SH", "黄金ETF", "热点ETF:大宗贵金属", 2.0),
        ("512400.SH", "有色金属ETF", "热点ETF:战略大宗", 2.5),
        ("512480.SH", "半导体ETF", "热点ETF:硬科技芯片", 3.0),
        ("159995.SZ", "芯片ETF", "热点ETF:硬科技芯片", 3.0),
        ("515050.SH", "5G通信ETF", "热点ETF:算力CPO", 3.0),
        ("562500.SH", "机器人ETF", "热点ETF:人形机器人", 3.0),
        ("512010.SH", "医药ETF", "热点ETF:创新药", 2.5),
        ("159869.SZ", "游戏ETF", "热点ETF:AI应用传媒", 3.5),
        ("513180.SH", "恒生科技ETF", "热点ETF:港股科技互联网", 2.5),
        ("513050.SH", "中概互联ETF", "热点ETF:全球中概互联", 2.5),
        ("513090.SH", "香港证券ETF", "热点ETF:港股通大金融", 3.0),
        ("159928.SZ", "消费ETF", "热点ETF:大消费", 2.0)
    ]
    for code, name, grp, th in trending_etfs:
        manager.add(code, name=name, group=grp, threshold=th, asset_type="etf")

    stats = manager.get_stats()
    print(f"\n========================================================")
    print(f"🎉 监控池全部构建完成！总标的数: {stats['total']} 家/只")
    print(f"• 资产类型: {stats['types']}")
    print(f"• 市场分布: {stats['markets']}")
    print(f"========================================================\n")


def main():
    manager = WatchlistManager()
    if not manager.items:
        initialize_full_jiankong_pool(manager)
    else:
        stats = manager.get_stats()
        print(f"📊 当前监控池标的数: {stats['total']} (类型: {stats['types']})")


if __name__ == "__main__":
    main()

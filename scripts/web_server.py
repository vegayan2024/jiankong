# -*- coding: utf-8 -*-
"""
自选股、指数与ETF即时监控系统 - Web 服务端 (FastAPI + WebSocket)
=============================================================
位于: c:\\ai\\antigravity\\jiankong\\scripts\\web_server.py
"""

import os
import re
import sys
import time
import json
import asyncio
import logging
import urllib.request
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any, Set, Tuple

os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

import uvicorn
import mimetypes
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import Response, FileResponse, HTMLResponse
from pydantic import BaseModel

# Fix Windows Registry MIME type issue where .css is registered as application/x-css
mimetypes.init()
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/html", ".html")

try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("JiankongServer")

JIANKONG_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(JIANKONG_DIR))
sys.path.insert(0, str(JIANKONG_DIR / "scripts"))

from scripts.watchlist_manager import WatchlistManager, normalize_stock_code, fetch_online_stock_name_and_price, SPECIAL_INDICES
from scripts.cninfo_client import cninfo_client

app = FastAPI(title="A股/港股全资产实时监控终端 (含指数与ETF)", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

watchlist_manager = WatchlistManager(filepath=JIANKONG_DIR / "watchlist.json")
WEB_DIR = JIANKONG_DIR / "web"

cached_quotes: Dict[str, Dict[str, Any]] = {}
recent_alerts: List[Dict[str, Any]] = []
recent_announcements: List[Dict[str, Any]] = []
recent_news: List[Dict[str, Any]] = []
seen_announcement_ids: Set[str] = set()
seen_news_ids: Set[str] = set()
price_history: Dict[str, List[Tuple[float, float]]] = {}


class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: Dict[str, Any]):
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                self.disconnect(connection)

ws_manager = ConnectionManager()


def fetch_batch_quotes():
    """批量获取股票、指数、ETF 最新行情"""
    global cached_quotes
    items = watchlist_manager.items
    if not items:
        return

    chunk_size = 50
    now_ts = time.time()
    now_str = datetime.now().strftime("%H:%M:%S")
    today_str = datetime.now().strftime("%Y-%m-%d")
    full_datetime_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for i in range(0, len(items), chunk_size):
        chunk = items[i:i + chunk_size]
        symbols_map = {}
        for s in chunk:
            code = s["code"]
            if code in SPECIAL_INDICES:
                t_sym = SPECIAL_INDICES[code]["tencent"]
            elif s.get("asset_type") == "index" and code.startswith("HSI"):
                t_sym = "hkHSI"
            elif s.get("asset_type") == "index" and code.startswith("HSTECH"):
                t_sym = "hkHSTECH"
            else:
                t_sym = f"{s['market'].lower()}{s['symbol']}"
            symbols_map[t_sym] = s

        url = f"http://qt.gtimg.cn/q={','.join(symbols_map.keys())}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                lines = resp.read().decode("gbk", errors="ignore").splitlines()
                for line in lines:
                    line = line.strip()
                    if not line or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    t_sym = k.replace("v_", "").strip()
                    if t_sym not in symbols_map:
                        continue

                    cfg = symbols_map[t_sym]
                    parts = v.strip('";').split("~")
                    if len(parts) < 33:
                        continue

                    code = cfg["code"]
                    asset_type = cfg.get("asset_type", "stock")
                    name = parts[1].strip() or cfg.get("name", "")
                    price = float(parts[3]) if parts[3] else 0.0
                    prev_close = float(parts[4]) if parts[4] else 0.0
                    pct_change = float(parts[32]) if parts[32] else 0.0
                    high_p = float(parts[33]) if len(parts) > 33 and parts[33] else price
                    low_p = float(parts[34]) if len(parts) > 34 and parts[34] else price
                    volume = float(parts[6]) if len(parts) > 6 and parts[6] else 0.0
                    amount = float(parts[37]) if len(parts) > 37 and parts[37] else 0.0

                    if price <= 0:
                        continue

                    # 记录价格滑动历史
                    if code not in price_history:
                        price_history[code] = []
                    price_history[code].append((now_ts, price))
                    if len(price_history[code]) > 12:
                        price_history[code].pop(0)

                    threshold = cfg.get("alert_threshold_pct", 3.0)
                    is_enabled = cfg.get("enabled", True)

                    # 警报触发判断 (指数阈值通常为 1.5%~2.0%，个股为 3%~5%)
                    if is_enabled and abs(pct_change) >= threshold:
                        alert_id = f"{code}:{pct_change:+.1f}:{datetime.now().strftime('%Y%m%d%H%M')[:11]}"
                        if not any(a["id"] == alert_id for a in recent_alerts[:20]):
                            new_alert = {
                                "id": alert_id,
                                "time": full_datetime_str,
                                "date": today_str,
                                "time_short": now_str,
                                "code": code,
                                "name": name,
                                "asset_type": asset_type,
                                "type": "SURGE" if pct_change > 0 else "DROP",
                                "level": "URGENT" if abs(pct_change) >= (threshold * 2) else "ALERT",
                                "message": f"{name} 波动达 {pct_change:+.2f}%，触及预警阈值 (±{threshold}%)",
                                "price": price,
                                "pct_change": pct_change,
                                "group": cfg.get("group", "自选")
                            }
                            recent_alerts.insert(0, new_alert)
                            if len(recent_alerts) > 100:
                                recent_alerts.pop()

                    cached_quotes[code] = {
                        "code": code,
                        "symbol": cfg["symbol"],
                        "market": cfg["market"],
                        "name": name,
                        "group": cfg.get("group", "自选"),
                        "asset_type": asset_type,
                        "price": price,
                        "prev_close": prev_close,
                        "pct_change": pct_change,
                        "high": high_p,
                        "low": low_p,
                        "volume": volume,
                        "amount": amount,
                        "threshold": threshold,
                        "enabled": is_enabled,
                        "update_time": now_str,
                        "update_datetime": full_datetime_str,
                        "update_date": today_str
                    }
        except Exception as e:
            logger.warning(f"行情拉取异常: {e}")


def fetch_cninfo_announcements_sync():
    """抓取巨潮资讯今日法定披露"""
    global recent_announcements
    today_str = datetime.today().strftime("%Y-%m-%d")
    now_str = datetime.now().strftime("%H:%M:%S")
    active_stocks = {s["symbol"]: s for s in watchlist_manager.items if s.get("asset_type") == "stock"}

    for col, plate in [("szse", "sz"), ("sse", "sh"), ("bj", "bj")]:
        try:
            query_data = {
                "pageNum": 1, "pageSize": 40, "tabName": "fulltext",
                "column": col, "plate": plate, "seDate": f"{today_str}~{today_str}"
            }
            resp = cninfo_client.session.post("http://www.cninfo.com.cn/new/hisAnnouncement/query",
                                              data=query_data, timeout=8)
            if resp.status_code == 200:
                ann_list = resp.json().get("announcements", []) or []
                for ann in ann_list:
                    ann_id = str(ann.get("announcementId", ""))
                    if not ann_id or ann_id in seen_announcement_ids:
                        continue
                    seen_announcement_ids.add(ann_id)

                    sec_code = str(ann.get("secCode", ""))
                    if sec_code in active_stocks:
                        stock = active_stocks[sec_code]
                        title = ann.get("announcementTitle", "")
                        adjunct = ann.get("adjunctUrl", "")
                        full_url = f"https://static.cninfo.com.cn/{adjunct}" if adjunct else ""
                        raw_pub = ann.get("announcementTime", "")
                        if isinstance(raw_pub, (int, float)) or (isinstance(raw_pub, str) and str(raw_pub).isdigit()):
                            formatted_pub_time = datetime.fromtimestamp(int(raw_pub) / 1000).strftime("%Y-%m-%d %H:%M:%S")
                        elif isinstance(raw_pub, str) and len(raw_pub) <= 8 and ":" in raw_pub:
                            formatted_pub_time = f"{today_str} {raw_pub}"
                        elif isinstance(raw_pub, str) and raw_pub:
                            formatted_pub_time = raw_pub
                        else:
                            formatted_pub_time = f"{today_str} {now_str}"

                        recent_announcements.insert(0, {
                            "id": ann_id,
                            "time": formatted_pub_time,
                            "date": formatted_pub_time.split(" ")[0] if " " in formatted_pub_time else today_str,
                            "code": stock["code"],
                            "name": stock["name"],
                            "title": title,
                            "url": full_url,
                            "source": "巨潮资讯 (CNINFO)"
                        })
                        if len(recent_announcements) > 50:
                            recent_announcements.pop()
        except Exception:
            pass


def get_watchlist_kw_map():
    """构建用于 7x24 电报匹配的关键词词典"""
    kw_map = {}
    for s in watchlist_manager.items:
        name = s.get("name", "")
        sym = s.get("symbol", "")
        if name and len(name) >= 2:
            kw_map[name] = s
        if sym and len(sym) >= 4:
            kw_map[sym] = s
        if name == "贵州茅台": kw_map["茅台"] = s
        elif name == "腾讯控股": kw_map["腾讯"] = s
        elif name == "宁德时代": kw_map["宁德"] = s
        elif name == "中国神华": kw_map["神华"] = s
        elif name == "海螺水泥": kw_map["海螺"] = s
        elif name == "紫金矿业": kw_map["紫金"] = s
        elif name == "万华化学": kw_map["万华"] = s
        elif name == "恒生指数": kw_map["恒指"] = s
        elif name == "恒生科技指数": kw_map["恒生科技"] = s
        elif name == "创业板指": kw_map["创业板"] = s
        elif name == "上证指数": kw_map["沪指"] = s; kw_map["上证综指"] = s
        elif name == "科创50": kw_map["科创板50"] = s
    return kw_map


def fetch_flash_news_sync():
    """抓取全网 7x24 财经电报流，并高亮匹配自选股与核心指数"""
    global recent_news
    try:
        kw_map = get_watchlist_kw_map()
        url = "https://zhibo.sina.com.cn/api/zhibo/feed?zhibo_id=152&page=1&page_size=80"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            feed_list = data.get("result", {}).get("data", {}).get("feed", {}).get("list", [])
            
            new_items = []
            for item in feed_list:
                news_id = str(item.get("id", ""))
                if not news_id or news_id in seen_news_ids:
                    continue
                seen_news_ids.add(news_id)

                raw_text = item.get("rich_text", "") or item.get("text", "")
                clean_text = re.sub(r"<[^>]+>", "", raw_text).strip()
                if not clean_text:
                    continue
                
                create_time = item.get("create_time", "")
                time_part = create_time.split(" ")[-1] if " " in create_time else create_time

                # 提取标题与正文 (如 【高盛：...】)
                title = ""
                m = re.match(r"【(.*?)】", clean_text)
                if m:
                    title = m.group(1)
                    body = clean_text[m.end():].strip()
                else:
                    body = clean_text

                # 匹配监控池中的标的
                matched_list = []
                seen_codes = set()
                for kw, s in kw_map.items():
                    if kw in clean_text:
                        code = s["code"]
                        if code not in seen_codes:
                            seen_codes.add(code)
                            matched_list.append({
                                "code": code,
                                "name": s["name"],
                                "group": s.get("group", "自选"),
                                "asset_type": s.get("asset_type", "stock")
                            })

                new_items.append({
                    "id": news_id,
                    "time": create_time,  # Full date and time: e.g. 2026-09-28 17:25:21
                    "time_short": time_part,
                    "datetime": create_time,
                    "date": create_time.split(" ")[0] if " " in create_time else "",
                    "title": title,
                    "content": clean_text,
                    "body": body or clean_text,
                    "is_hit": len(matched_list) > 0,
                    "matched_stocks": matched_list,
                    "docurl": item.get("docurl", "")
                })

            if new_items:
                # 新电报放在最前面
                for nit in reversed(new_items):
                    recent_news.insert(0, nit)
                while len(recent_news) > 150:
                    recent_news.pop()
                hit_c = sum(1 for n in recent_news if n.get("is_hit"))
                logger.info(f"⚡ 7x24快讯已更新: 现存 {len(recent_news)} 条，自选命中 {hit_c} 条 (新增 {len(new_items)} 条)")
    except Exception as e:
        logger.warning(f"7x24快讯拉取异常: {e}")


async def background_polling_loop():
    logger.info("📡 启动 jiankong 后台高频行情与信披监听协程...")
    loop_count = 0
    while True:
        try:
            await asyncio.to_thread(fetch_batch_quotes)
            
            # 7x24 快讯每 15 秒更新一次
            if loop_count % 5 == 0:
                await asyncio.to_thread(fetch_flash_news_sync)

            # 巨潮信披每 30 秒更新一次
            if loop_count % 10 == 0:
                await asyncio.to_thread(fetch_cninfo_announcements_sync)

            now_dt = datetime.now()
            await ws_manager.broadcast({
                "type": "TICK",
                "timestamp": now_dt.strftime("%H:%M:%S"),
                "datetime": now_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "date": now_dt.strftime("%Y-%m-%d"),
                "quotes_count": len(cached_quotes),
                "alerts_count": len(recent_alerts),
                "news_count": len(recent_news),
                "news_hit_count": sum(1 for n in recent_news if n.get("is_hit"))
            })

            loop_count += 1
            await asyncio.sleep(3)
        except Exception as e:
            logger.error(f"轮询异常: {e}")
            await asyncio.sleep(3)


@app.on_event("startup")
async def on_startup():
    logger.info("🚀 正在预热行情、7x24快讯与巨潮信披数据...")
    await asyncio.to_thread(fetch_batch_quotes)
    await asyncio.to_thread(fetch_flash_news_sync)
    await asyncio.to_thread(fetch_cninfo_announcements_sync)
    asyncio.create_task(background_polling_loop())


# ==========================================
# REST API 接口
# ==========================================
@app.get("/api/quotes")
async def get_quotes(group: Optional[str] = None, market: Optional[str] = None,
                     asset_type: Optional[str] = None, search: Optional[str] = None):
    result = list(cached_quotes.values())
    if asset_type and asset_type != "ALL":
        result = [q for q in result if q.get("asset_type") == asset_type.lower()]
    if group:
        result = [q for q in result if q.get("group") == group]
    if market and market != "ALL":
        result = [q for q in result if q.get("market") == market.upper()]
    if search:
        kw = search.strip().lower()
        result = [q for q in result if kw in q["code"].lower() or kw in q["name"].lower() or kw in q.get("group", "").lower()]

    up_count = sum(1 for q in cached_quotes.values() if q.get("pct_change", 0) > 0)
    down_count = sum(1 for q in cached_quotes.values() if q.get("pct_change", 0) < 0)
    flat_count = len(cached_quotes) - up_count - down_count

    idx_count = sum(1 for q in cached_quotes.values() if q.get("asset_type") == "index")
    etf_count = sum(1 for q in cached_quotes.values() if q.get("asset_type") == "etf")
    stock_count = sum(1 for q in cached_quotes.values() if q.get("asset_type") == "stock")

    now_dt = datetime.now()
    return {
        "status": "success",
        "date": now_dt.strftime("%Y-%m-%d"),
        "time": now_dt.strftime("%H:%M:%S"),
        "updated_at": now_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(cached_quotes),
        "index_count": idx_count,
        "etf_count": etf_count,
        "stock_count": stock_count,
        "up_count": up_count,
        "down_count": down_count,
        "flat_count": flat_count,
        "data": result
    }


@app.get("/api/alerts")
async def get_alerts():
    return {"status": "success", "data": recent_alerts}


@app.get("/api/announcements")
async def get_announcements():
    return {"status": "success", "data": recent_announcements}


@app.get("/api/news")
async def get_news(only_hit: bool = False, search: Optional[str] = None):
    res = list(recent_news)
    hit_count = sum(1 for n in res if n.get("is_hit"))
    if only_hit:
        res = [n for n in res if n.get("is_hit")]
    if search:
        kw = search.strip().lower()
        res = [n for n in res if kw in n["content"].lower() or kw in (n.get("title") or "").lower()]
    return {
        "status": "success",
        "total": len(recent_news),
        "hit_count": hit_count,
        "returned": len(res),
        "data": res
    }


@app.post("/api/news/refresh")
async def refresh_news_endpoint():
    await asyncio.to_thread(fetch_flash_news_sync)
    hit_c = sum(1 for n in recent_news if n.get("is_hit"))
    return {
        "status": "success",
        "total": len(recent_news),
        "hit_count": hit_c,
        "data": recent_news
    }


# ==============================================================================
# 同花顺问财 (iwencai.com) 标准：最近一周热点题材、风格、概念全景知识库
# ==============================================================================
THEME_KNOWLEDGE_BASE = [
    {
        "id": "theme-ai-corpus",
        "name": "AI语料与多模态应用",
        "category": "泛科技 · 数据要素 · 大模型",
        "group": "风格:AI语料与应用",
        "hot_level": "★★★★★",
        "fund_flow": "+14.8亿 净流入",
        "week_perf": "+9.62%",
        "driver_info": "多模态大模型(如Sora/GPT-4o/Claude 3.7)高频迭代演进，推理侧长文本与图生视频对高质量版权语料库需求爆发式增长，国家数据要素X战略落地催化数字内容版权价值重估。",
        "catalyst": "大模型推理升级与模型内嵌多模态推理，头部大厂密集采买出版与图文正版语料",
        "top_stocks": [
            {
                "code": "600825.SH",
                "name": "新华传媒",
                "role": "龙一 · 国资版权语料领航者",
                "desc": "上海报业集团旗下唯一上市公司，垄断性拥有海量权威合规出版与主流媒体版权数据，深度与国内前列大模型企业签署语料采买战略合作。"
            },
            {
                "code": "603533.SH",
                "name": "掌阅科技",
                "role": "龙二 · 网文IP与长文本数据龙头",
                "desc": "国内数字阅读头部平台，坐拥数十万册正版网文与原创IP，全面接入MiniMax、月之暗面(Kimi)大模型生态，AI漫剧与语料出海变现加速。"
            }
        ]
    },
    {
        "id": "theme-solid-battery",
        "name": "固态电池与新型储能",
        "category": "新质生产力 · 固态电池 · 新材料",
        "group": "风格:固态电池与新材料",
        "hot_level": "★★★★★",
        "fund_flow": "+21.5亿 净流入",
        "week_perf": "+11.45%",
        "driver_info": "七部门联合印发新型电池产业高质量发展规划，全固态电池中试线与千公里级装车实验频传捷报，固态电解质、硅碳负极与高精度真空镀膜涂布装备需求放量。",
        "catalyst": "工信部高能量密度电池试点推进，全固态电芯中试线点火投产",
        "top_stocks": [
            {
                "code": "300438.SZ",
                "name": "鹏辉能源",
                "role": "龙一 · 全固态电芯领航者",
                "desc": "率先发布无机固体电解质全固态电池，能量密度达280Wh/kg以上且针刺不起火，中试线投产与产业化进度行业领跑。"
            },
            {
                "code": "603396.SH",
                "name": "金辰股份",
                "role": "龙二 · 固态电芯真空装备龙头",
                "desc": "光伏与锂电真空装备专家，攻克固态电池干法电极及高压实真空等静压设备关键技术，深度切入一线电池厂商装备供应链。"
            }
        ]
    },
    {
        "id": "theme-advanced-packaging",
        "name": "先进封装与半导体材料",
        "category": "半导体 · 玻璃基板 · Chiplet",
        "group": "风格:先进封装与半导体",
        "hot_level": "★★★★★",
        "fund_flow": "+19.2亿 净流入",
        "week_perf": "+8.30%",
        "driver_info": "AI高算力芯片突破摩尔定律物理极限，英伟达与台积电全面加码玻璃基板与2.5D/3D Chiplet先进封装，高密度蚀刻引线框架与封测产能全面告急。",
        "catalyst": "玻璃基板成为下一代算力芯片载体主流共识，先进封装产能持续饱满",
        "top_stocks": [
            {
                "code": "002156.SZ",
                "name": "通富微电",
                "role": "龙一 · 全球顶尖先进封测巨头",
                "desc": "全球封测行业前四强，深度绑定AMD CPU/GPU核心封装，国内首屈一指具备大面积高密度Chiplet和超高算力倒装量产能力。"
            },
            {
                "code": "002119.SZ",
                "name": "康强电子",
                "role": "龙二 · 半导体引线框架龙头",
                "desc": "国内半导体引线框架与键合丝行业隐形冠军，高密度蚀刻型引线框架全面适配先进封装，市占率连续多年位居全国第一。"
            }
        ]
    },
    {
        "id": "theme-commercial-space",
        "name": "商业航天与空间卫星",
        "category": "低轨卫星 · 航天强国 · 军民融合",
        "group": "风格:商业航天与卫星",
        "hot_level": "★★★★☆",
        "fund_flow": "+12.3亿 净流入",
        "week_perf": "+7.15%",
        "driver_info": "千帆低轨通信星座开启大规模高密度组网发射，空间站货物运输与低成本可重复使用火箭竞标落地，星载激光通信与星上处理载荷迎来批量交付潮。",
        "catalyst": "中国星网与千帆星座百星连发计划加速，可重复使用运载火箭测试提速",
        "top_stocks": [
            {
                "code": "600879.SH",
                "name": "航天电子",
                "role": "龙一 · 星载测控与电子系统核心主导",
                "desc": "航天九院旗下核心上市旗舰，垄断性承揽国家卫星导航、空间通信遥测、星间微波链路与宇航级连接器，纯正航天总装配套标的。"
            },
            {
                "code": "601698.SH",
                "name": "中国卫通",
                "role": "龙二 · 卫星空间通信运营国家队",
                "desc": "我国唯一拥有自主可控商用通信广播卫星资源的卫星通信运营企业，独占核心优质轨道与Ka高频段空间频谱资源。"
            }
        ]
    },
    {
        "id": "theme-cross-border-ip",
        "name": "跨境电商与IP潮玩经济",
        "category": "消费出海 · 谷子经济 · 新零售",
        "group": "风格:跨境电商与IP经济",
        "hot_level": "★★★★☆",
        "fund_flow": "+10.7亿 净流入",
        "week_perf": "+6.85%",
        "driver_info": "跨境电商出海保持双位数高增，海外假日备货周期启动，同时二次元谷子经济与国潮IP出海呈现爆发式增长，数字化供应链与自主支付赋能显著。",
        "catalyst": "中东及拉美新兴市场订单倍增，谷子经济IP衍生品全球爆发",
        "top_stocks": [
            {
                "code": "600415.SH",
                "name": "小商品城",
                "role": "龙一 · 全球小商品实体与数字出海枢纽",
                "desc": "义乌全球商贸旗舰，Chinagoods平台与Yiwu Pay跨境支付牌照覆盖150+国家，二次元文创谷子出海集散策源地。"
            },
            {
                "code": "002614.SZ",
                "name": "奥佳华",
                "role": "龙二 · 跨境自主品牌出口领军者",
                "desc": "智能健康硬件全球品牌矩阵，自主品牌畅销欧美与亚太60多个国家，跨境电商直销平台持续高速增长。"
            }
        ]
    },
    {
        "id": "theme-indep-compute",
        "name": "自主算力与国产芯片底座",
        "category": "国产替代 · AI算力 · 晶圆代工",
        "group": "风格:自主算力芯片",
        "hot_level": "★★★★★",
        "fund_flow": "+28.9亿 净流入",
        "week_perf": "+12.18%",
        "driver_info": "外部出口管制加码背景下，国内智算中心与政企大模型全面转向自主可控底层算力生态，本土GPU架构演进与先进制程代工产能利用率持续打满。",
        "catalyst": "智算基础设施自主化采购比例大幅提高，国产大模型软硬件适配成熟",
        "top_stocks": [
            {
                "code": "688256.SH",
                "name": "寒武纪",
                "role": "龙一 · 通用AI算力芯片第一梯队",
                "desc": "国内云端训练与推理GPU核心开拓者，思元系列芯片深度适配大模型推理全栈生态，深入支撑国家级智算中心标杆项目。"
            },
            {
                "code": "688981.SH",
                "name": "中芯国际",
                "role": "龙二 · 高阶制程晶圆代工中流砥柱",
                "desc": "中国大陆技术最先进、配套最完善的晶圆代工巨头，承载国内先进逻辑制程与自研算力芯片流片量产的战略底座。"
            }
        ]
    },
    {
        "id": "theme-cpo-optics",
        "name": "算力光模块与CPO高速互联",
        "category": "算力底座 · 光通信 · 高速连接",
        "group": "风格:算力光模块",
        "hot_level": "★★★★★",
        "fund_flow": "+26.4亿 净流入",
        "week_perf": "+10.90%",
        "driver_info": "AI超大规模集群训练促使光模块从800G加速向1.6T演进，功耗与延迟严苛要求推动CPO及硅光方案成为全球头部CSP云厂商标准标配，业绩持续高兑现。",
        "catalyst": "北美CSP云巨头资本开支上调，1.6T光模块与硅光芯片量产出货",
        "top_stocks": [
            {
                "code": "300308.SZ",
                "name": "中际旭创",
                "role": "龙一 · 全球光模块出货量与技术双冠王",
                "desc": "在北美主流云厂商中份额绝对领先，800G/1.6T高端光模块研发与良率全球领跑，AI算力基础设施核心业绩受益标的。"
            },
            {
                "code": "300502.SZ",
                "name": "新易盛",
                "role": "龙二 · 高速光互联高弹性领头羊",
                "desc": "海外市场拓展极其亮眼，800G LPO及线性直驱方案具备显著成本与功耗优势，硅光芯片整合能力位居国内第一梯队。"
            }
        ]
    },
    {
        "id": "theme-humanoid-robot",
        "name": "具身智能与人形机器人",
        "category": "新质生产力 · 具身智能 · 工业机器人",
        "group": "风格:人形机器人",
        "hot_level": "★★★★☆",
        "fund_flow": "+13.1亿 净流入",
        "week_perf": "+7.80%",
        "driver_info": "国内外头部科技巨头量产定型在即，本体运控算法与灵巧手、减速器、力矩传感器等核心硬件供应链迎来Tier 1定点潮与试产测试。",
        "catalyst": "头部车企与科技巨头公布人形机器人实训视频，核心零部件供应链量产前夜",
        "top_stocks": [
            {
                "code": "002050.SZ",
                "name": "三花智控",
                "role": "龙一 · 人形机器人执行器总成核心供应商",
                "desc": "全球热管理与微通道核心龙头，依托精密机电制造积淀全面承接头部人形机器人旋转与直线执行器总成总包研发与规模量产。"
            },
            {
                "code": "688017.SH",
                "name": "绿的谐波",
                "role": "龙二 · 高精密谐波减速器破壁者",
                "desc": "国内谐波减速器市占率稳居第一，技术指标全面媲美日本哈默纳科，为人形机器人高承载、高灵敏关节提供不可替代的传动部件。"
            }
        ]
    },
    {
        "id": "theme-low-altitude",
        "name": "低空经济与eVTOL飞行器",
        "category": "空天科技 · 新型交通 · 新质基建",
        "group": "风格:低空经济",
        "hot_level": "★★★★☆",
        "fund_flow": "+11.6亿 净流入",
        "week_perf": "+6.40%",
        "driver_info": "全国首批低空飞行服务保障体系与适航认证标准加速出台，无人机物流与文旅观光、城际空中接驳航线常态化落地，带动空域管理与整机部件高景气。",
        "catalyst": "多省市空域管理改革细则发布，城际eVTOL接驳常态化航线开通",
        "top_stocks": [
            {
                "code": "002085.SZ",
                "name": "万丰奥威",
                "role": "龙一 · 轻量化机身与eVTOL整机先锋",
                "desc": "旗下钻石飞机拥有成熟通航整机设计制造体系，携手全球头部车企推进eVTOL整机适航取证，轻量化镁铝合金材料全球市占率领先。"
            },
            {
                "code": "000099.SZ",
                "name": "中信海直",
                "role": "龙二 · 国内通用航空与低空运营国家队",
                "desc": "国内规模最大的通航直升机运营企业，全面布局大湾区等城市空中交通(UAM)网络，低空空域商业化飞行小时数行业第一。"
            }
        ]
    },
    {
        "id": "theme-biopharma-export",
        "name": "创新药出海与GLP-1生物科技",
        "category": "生物医药 · 出海授权 · 减重降糖",
        "group": "风格:创新药出海",
        "hot_level": "★★★☆☆",
        "fund_flow": "+8.9亿 净流入",
        "week_perf": "+4.95%",
        "driver_info": "国内创新药企对外授权(License-out)交易额屡创历史新高，ADC抗体偶联药物与双抗在全球主流肿瘤学大会崭露头角，GLP-1多靶点减重降糖管线商业化预期强烈。",
        "catalyst": "重磅创新药全球三期临床达到终点，跨国巨头巨额授权预付款落地",
        "top_stocks": [
            {
                "code": "688235.SH",
                "name": "百济神州",
                "role": "龙一 · 全球化商业化创新药旗舰",
                "desc": "核心重磅BTK抑制剂百悦泽全球销售额突破十亿美元大关，PD-1获FDA批准，是国内首屈一指具备全球多中心自建销售体系的创新药巨擘。"
            },
            {
                "code": "01801.HK",
                "name": "信达生物",
                "role": "龙二 · 双抗与代谢减重多靶点先锋",
                "desc": "GLP-1R/GCGR双重激动剂玛仕度肽国内减重和降糖适应症推进迅猛，抗肿瘤双抗管线海外权益授权获跨国巨头大额里程碑付款。"
            }
        ]
    },
    {
        "id": "theme-precious-metals",
        "name": "战略大宗与贵金属周期",
        "category": "大宗商品 · 避险资产 · 资源壁垒",
        "group": "风格:战略贵金属",
        "hot_level": "★★★★☆",
        "fund_flow": "+17.5亿 净流入",
        "week_perf": "+8.10%",
        "driver_info": "全球央行购金需求持续高位，地缘避险与美联储降息周期交织推动贵金属与战略矿产估值中枢抬升，铜金等高品位矿山资源稀缺性凸显。",
        "catalyst": "国际黄金现货与伦敦铜震荡走高，全球优质矿产资源兼并重组加速",
        "top_stocks": [
            {
                "code": "601899.SH",
                "name": "紫金矿业",
                "role": "龙一 · 全球有色与贵金属资源跨国巨头",
                "desc": "铜、金资源储量与产量国内第一，海外大型卡莫阿铜矿与巨龙铜矿改扩建持续贡献充沛现金流，低成本逆周期并购能力全球瞩目。"
            },
            {
                "code": "600547.SH",
                "name": "山东黄金",
                "role": "龙二 · 黄金采选与资源整合绝对主力",
                "desc": "纯正黄金采掘旗舰，坐拥焦家、三山岛等世界级黄金生产基地，海外加纳等项目注入增厚资源储备，深度受益金价上行周期。"
            }
        ]
    }
]


@app.get("/api/themes/summary")
async def get_themes_summary():
    """获取最近一周热点题材、风格、概念全景数据 (同花顺问财口径，联动实时行情)"""
    result = []
    for item in THEME_KNOWLEDGE_BASE:
        t_copy = dict(item)
        top_stocks_with_quotes = []
        for s in item["top_stocks"]:
            code = s["code"]
            q = cached_quotes.get(code, {})
            top_stocks_with_quotes.append({
                "code": code,
                "name": s["name"],
                "role": s["role"],
                "desc": s["desc"],
                "price": q.get("price", 0.0),
                "pct_change": q.get("pct_change", 0.0),
                "market": q.get("market", ""),
                "symbol": q.get("symbol", ""),
                "update_time": q.get("update_time", "")
            })
        t_copy["top_stocks"] = top_stocks_with_quotes
        valid_pcts = [s["pct_change"] for s in top_stocks_with_quotes if s.get("price", 0) > 0]
        t_copy["realtime_avg_pct"] = round(sum(valid_pcts) / len(valid_pcts), 2) if valid_pcts else 0.0
        result.append(t_copy)
    return {"status": "success", "total": len(result), "data": result}


@app.get("/api/watchlist/groups")
async def get_groups():
    groups = set()
    for s in watchlist_manager.items:
        groups.add(s.get("group", "自选"))
    return {"status": "success", "data": sorted(list(groups))}


class AddStockRequest(BaseModel):
    code: str
    name: Optional[str] = None
    group: Optional[str] = "自选"
    threshold: Optional[float] = 3.0
    asset_type: Optional[str] = None


@app.post("/api/watchlist/add")
async def add_stock(req: AddStockRequest):
    try:
        item = watchlist_manager.add(req.code, name=req.name, group=req.group or "自选",
                                     threshold=req.threshold or 3.0, asset_type=req.asset_type)
        await asyncio.to_thread(fetch_batch_quotes)
        return {"status": "success", "data": item}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/watchlist/{code}")
async def remove_stock(code: str):
    item = watchlist_manager.remove(code)
    if not item:
        raise HTTPException(status_code=404, detail="未找到该标的")
    if code in cached_quotes:
        cached_quotes.pop(code, None)
    return {"status": "success", "data": item}


@app.patch("/api/watchlist/{code}")
async def update_stock(code: str, enabled: Optional[bool] = None, threshold: Optional[float] = None, group: Optional[str] = None):
    item = watchlist_manager.find(code)
    if not item:
        raise HTTPException(status_code=404, detail="未找到该标的")

    if enabled is not None:
        item["enabled"] = enabled
    if threshold is not None:
        item["alert_threshold_pct"] = threshold
    if group is not None:
        item["group"] = group

    watchlist_manager.save()
    if code in cached_quotes:
        if enabled is not None:
            cached_quotes[code]["enabled"] = enabled
        if threshold is not None:
            cached_quotes[code]["threshold"] = threshold
        if group is not None:
            cached_quotes[code]["group"] = group

    return {"status": "success", "data": item}


@app.get("/api/check-code/{code}")
async def check_code(code: str):
    norm = normalize_stock_code(code)
    if not norm:
        return {"valid": False, "message": "无法识别的代码格式"}
    name, price = await asyncio.to_thread(fetch_online_stock_name_and_price, norm["market"], norm["symbol"])
    return {
        "valid": True,
        "code": norm["code"],
        "symbol": norm["symbol"],
        "market": norm["market"],
        "asset_type": norm.get("asset_type", "stock"),
        "name": name or f"标的{norm['symbol']}",
        "price": price or 0.0
    }


@app.websocket("/ws/live")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


@app.post("/api/alerts/clear")
async def clear_alerts():
    global recent_alerts
    recent_alerts.clear()
    return {"status": "success", "message": "异动警报已清空"}


@app.get("/api/stock/{code}")
async def get_stock_detail(code: str):
    stock_cfg = watchlist_manager.find(code)
    quote = cached_quotes.get(code)
    if not stock_cfg and not quote:
        raise HTTPException(status_code=404, detail="标的不存在")
    
    sym = stock_cfg["symbol"] if stock_cfg else code.split(".")[0]
    name = stock_cfg["name"] if stock_cfg else (quote["name"] if quote else "")
    
    matched_anns = [a for a in recent_announcements if a.get("code") == code or a.get("sec_code") == sym or (name and name in a.get("title", ""))]
    matched_news = [
        n for n in recent_news
        if any(s.get("code") == code for s in n.get("matched_stocks", []))
        or (name and len(name) >= 2 and name in n.get("content", ""))
        or (sym and len(sym) >= 4 and sym in n.get("content", ""))
    ]
    matched_alerts = [a for a in recent_alerts if a.get("code") == code]
    
    return {
        "status": "success",
        "data": {
            "quote": quote or stock_cfg,
            "announcements": matched_anns[:15],
            "news": matched_news[:15],
            "alerts": matched_alerts[:15]
        }
    }


@app.get("/style.css")
async def get_style_css():
    css_file = WEB_DIR / "style.css"
    if css_file.exists():
        return Response(content=css_file.read_text(encoding="utf-8"), media_type="text/css; charset=utf-8")
    raise HTTPException(status_code=404, detail="style.css not found")


@app.get("/app.js")
async def get_app_js():
    js_file = WEB_DIR / "app.js"
    if js_file.exists():
        return Response(content=js_file.read_text(encoding="utf-8"), media_type="application/javascript; charset=utf-8")
    raise HTTPException(status_code=404, detail="app.js not found")


if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")


def run():
    print("========================================================")
    print("🌐 QUANT RADAR (jiankong) 资产实时监控 Web 应用程序正在启动...")
    print("👉 本地访问地址: http://127.0.0.1:8765")
    print("========================================================")
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")


if __name__ == "__main__":
    run()

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
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

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
from scripts.aihot_engine import aihot_engine
from scripts.macro_compass import macro_compass

# ==============================================================================
# 高性能全局 HTTP 连接池与并发执行器 (大幅降低 TCP 握手开销与 I/O 阻塞)
# ==============================================================================
http_session = requests.Session()
adapter = HTTPAdapter(
    pool_connections=16,
    pool_maxsize=32,
    max_retries=Retry(total=2, backoff_factor=0.1, status_forcelist=[500, 502, 503, 504])
)
http_session.mount("http://", adapter)
http_session.mount("https://", adapter)
http_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Connection": "keep-alive"
})

# 行情抓取多线程并发池 (实测提速 4x)
quote_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="quote_worker")

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


def _fetch_single_chunk(chunk_items: List[Dict[str, Any]], now_ts: float, now_str: str, today_str: str, full_datetime_str: str):
    """单个切片工作协程：并发拉取 60~70 只标的，耗时约 150~200ms"""
    symbols_map = {}
    for s in chunk_items:
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
    chunk_quotes = {}
    chunk_alerts = []

    try:
        resp = http_session.get(url, timeout=3.5)
        if resp.status_code == 200:
            lines = resp.content.decode("gbk", errors="ignore").splitlines()
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
                try:
                    price = float(parts[3]) if parts[3] else 0.0
                except (ValueError, IndexError):
                    price = 0.0

                if price <= 0:
                    continue

                prev_close = float(parts[4]) if len(parts) > 4 and parts[4] else 0.0
                pct_change = float(parts[32]) if len(parts) > 32 and parts[32] else 0.0
                high_p = float(parts[33]) if len(parts) > 33 and parts[33] else price
                low_p = float(parts[34]) if len(parts) > 34 and parts[34] else price
                volume = float(parts[6]) if len(parts) > 6 and parts[6] else 0.0
                amount = float(parts[37]) if len(parts) > 37 and parts[37] else 0.0

                threshold = cfg.get("alert_threshold_pct", 3.0)
                is_enabled = cfg.get("enabled", True)

                # 警报触发判断 (指数通常 1.5%~2.0%，个股 3%~5%)
                if is_enabled and abs(pct_change) >= threshold:
                    alert_id = f"{code}:{pct_change:+.1f}:{datetime.now().strftime('%Y%m%d%H%M')[:11]}"
                    chunk_alerts.append({
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
                    })

                chunk_quotes[code] = {
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
        logger.warning(f"并发块行情拉取异常: {e}")

    return chunk_quotes, chunk_alerts


def fetch_batch_quotes():
    """并发批量获取股票、指数、ETF 最新行情 (多线程 Keep-Alive 会话池)"""
    global cached_quotes
    items = watchlist_manager.items
    if not items:
        return

    # 186 只标的按 65 只分块，只需 3 组并发，总时延降至单次 RTT (~200ms)
    chunk_size = 65
    chunks = [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]
    now_ts = time.time()
    now_str = datetime.now().strftime("%H:%M:%S")
    today_str = datetime.now().strftime("%Y-%m-%d")
    full_datetime_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    futures = [
        quote_executor.submit(_fetch_single_chunk, chk, now_ts, now_str, today_str, full_datetime_str)
        for chk in chunks
    ]

    for f in futures:
        try:
            chunk_q, chunk_a = f.result(timeout=4.0)
            cached_quotes.update(chunk_q)
            for al in chunk_a:
                if not any(a["id"] == al["id"] for a in recent_alerts[:25]):
                    recent_alerts.insert(0, al)
        except Exception as e:
            logger.warning(f"获取并发结果异常: {e}")

    if len(recent_alerts) > 100:
        del recent_alerts[100:]


def fetch_cninfo_announcements_sync():
    """抓取巨潮资讯今日法定披露 (带集合容量修剪)"""
    global recent_announcements, seen_announcement_ids
    today_str = datetime.today().strftime("%Y-%m-%d")
    now_str = datetime.now().strftime("%H:%M:%S")
    active_stocks = {s["symbol"]: s for s in watchlist_manager.items if s.get("asset_type") == "stock"}

    # 内存边界防御：防止历史 ID 集合无限膨胀
    if len(seen_announcement_ids) > 1000:
        seen_announcement_ids = {a["id"] for a in recent_announcements if a.get("id")}

    for col, plate in [("szse", "sz"), ("sse", "sh"), ("bj", "bj")]:
        try:
            query_data = {
                "pageNum": 1, "pageSize": 40, "tabName": "fulltext",
                "column": col, "plate": plate, "seDate": f"{today_str}~{today_str}"
            }
            resp = cninfo_client.session.post("http://www.cninfo.com.cn/new/hisAnnouncement/query",
                                              data=query_data, timeout=6)
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
        except Exception as e:
            logger.debug(f"巨潮信披抓取偶发跳过: {e}")


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
    """抓取全网 7x24 财经电报流 (复用 HTTP Keep-Alive 连接池)"""
    global recent_news, seen_news_ids
    try:
        kw_map = get_watchlist_kw_map()
        url = "https://zhibo.sina.com.cn/api/zhibo/feed?zhibo_id=152&page=1&page_size=80"
        
        # 内存边界防御：防止历史已读新闻 ID 集合无限膨胀
        if len(seen_news_ids) > 1500:
            seen_news_ids = {n["id"] for n in recent_news if n.get("id")}

        resp = http_session.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
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
                    "time": create_time,
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

            # AIHOT 行业情报与日报每 3 分钟 (60 次循环) 全域增量扫描一次
            if loop_count > 0 and loop_count % 60 == 0:
                asyncio.create_task(asyncio.to_thread(aihot_engine.refresh))

            now_dt = datetime.now()
            # 联动宏观与风格罗盘动量微调
            macro_compass.update_from_quotes(cached_quotes)
            await ws_manager.broadcast({
                "type": "TICK",
                "timestamp": now_dt.strftime("%H:%M:%S"),
                "datetime": now_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "date": now_dt.strftime("%Y-%m-%d"),
                "quotes_count": len(cached_quotes),
                "alerts_count": len(recent_alerts),
                "news_count": len(recent_news),
                "news_hit_count": sum(1 for n in recent_news if n.get("is_hit")),
                "macro_compass": macro_compass.get_full_status()
            })

            loop_count += 1
            await asyncio.sleep(3)
        except Exception as e:
            logger.error(f"轮询异常: {e}")
            await asyncio.sleep(3)


# ==============================================================================
# FastAPI 现代异步生命周期管理器 (彻底消除 @app.on_event 弃用告警与资源残留)
# ==============================================================================
@asynccontextmanager
async def lifespan(app_inst: FastAPI):
    logger.info("🚀 正在预热行情、7x24快讯与巨潮信披数据...")
    await asyncio.to_thread(fetch_batch_quotes)
    await asyncio.to_thread(fetch_flash_news_sync)
    await asyncio.to_thread(fetch_cninfo_announcements_sync)
    # 异步预热 AIHOT 行业情报引擎 (不阻塞主服务启动)
    asyncio.create_task(asyncio.to_thread(aihot_engine.refresh))
    polling_task = asyncio.create_task(background_polling_loop())
    yield
    polling_task.cancel()
    quote_executor.shutdown(wait=False)
    http_session.close()


app = FastAPI(title="A股/港股全资产实时监控终端 (含指数与ETF)", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==============================================================================
# REST API 接口
# ==============================================================================
@app.get("/api/overview")
async def get_overview(group: Optional[str] = None, market: Optional[str] = None,
                       asset_type: Optional[str] = None, search: Optional[str] = None,
                       only_hit_news: bool = False):
    """
    【高性能聚合接口】一次性返回 行情矩阵、预警、信披、7x24快讯 及 宏观统计指标
    替代客户端每 3 秒发起 4 个独立 HTTP 请求，降低 75% 网络请求数，极大提升响应速度与稳定性
    """
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
    filtered_news = [n for n in recent_news if n.get("is_hit")] if only_hit_news else list(recent_news[:60])

    return {
        "status": "success",
        "date": now_dt.strftime("%Y-%m-%d"),
        "time": now_dt.strftime("%H:%M:%S"),
        "updated_at": now_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "stats": {
            "total": len(cached_quotes),
            "up_count": up_count,
            "down_count": down_count,
            "flat_count": flat_count,
            "index_count": idx_count,
            "etf_count": etf_count,
            "stock_count": stock_count,
            "alerts_count": len(recent_alerts),
            "announcements_count": len(recent_announcements),
            "news_count": len(recent_news),
            "news_hit_count": sum(1 for n in recent_news if n.get("is_hit"))
        },
        "quotes": result,
        "alerts": recent_alerts[:40],
        "announcements": recent_announcements[:30],
        "news": filtered_news
    }
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
# AIHOT 行业信息与情报聚合接口 (融合 KKKKhazix/AIHOT 体系)
# ==============================================================================
@app.get("/api/industry/news")
async def get_industry_news(category: Optional[str] = None, min_score: float = 0.0, limit: int = 60):
    items = await asyncio.to_thread(aihot_engine.get_news, category, min_score)
    return {
        "status": "success",
        "total": len(items),
        "category": category or "ALL",
        "min_score": min_score,
        "data": items[:limit]
    }


@app.get("/api/industry/daily")
async def get_industry_daily():
    daily = await asyncio.to_thread(aihot_engine.get_daily)
    return {
        "status": "success",
        "data": daily
    }


@app.get("/api/industry/sources")
async def get_industry_sources():
    sources = aihot_engine.get_sources_meta()
    return {
        "status": "success",
        "total": len(sources),
        "data": sources
    }


@app.post("/api/industry/refresh")
async def refresh_industry_intel():
    try:
        result = await asyncio.to_thread(aihot_engine.refresh)
        return {
            "status": result.get("status", "success"),
            "message": result.get("message", "全域权威信源扫描完成"),
            "data": result
        }
    except Exception as e:
        logger.error(f"行业情报刷新异常: {e}", exc_info=True)
        return {
            "status": "error",
            "message": f"全域扫描执行失败: {str(e)}",
            "data": None
        }


# ==============================================================================
# 同花顺问财 (iwencai.com) 标准：风格题材全景知识库与动态加载机制
# ==============================================================================
THEMES_KNOWLEDGE_BASE_FILE = JIANKONG_DIR / "themes_knowledge_base.json"


def load_themes_knowledge_base() -> List[Dict[str, Any]]:
    """优先从 themes_knowledge_base.json 载入最新全景题材，若异常则安全返回空列表"""
    if THEMES_KNOWLEDGE_BASE_FILE.exists():
        try:
            with open(THEMES_KNOWLEDGE_BASE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list) and len(data) > 0:
                    return data
        except Exception as e:
            logger.warning(f"读取题材库 {THEMES_KNOWLEDGE_BASE_FILE} 异常: {e}")
    return []


@app.get("/api/themes/summary")
async def get_themes_summary():
    """获取热点题材、风格、概念全景数据 (同花顺问财口径，联动实时行情与时效指标)"""
    themes = load_themes_knowledge_base()
    result = []
    for item in themes:
        t_copy = dict(item)
        top_stocks_with_quotes = []
        for s in item.get("top_stocks", []):
            code = s.get("code", "")
            q = cached_quotes.get(code, {})
            top_stocks_with_quotes.append({
                "code": code,
                "name": s.get("name", ""),
                "role": s.get("role", ""),
                "desc": s.get("desc", ""),
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


@app.post("/api/themes/refresh")
async def refresh_themes_summary():
    """触发风格题材时效指标重新计算与知识库刷新"""
    try:
        from scripts.update_themes_intel import run_theme_intel_update
        res = await asyncio.to_thread(run_theme_intel_update, True)
        # 刷新完成后触发一次批量行情拉取
        await asyncio.to_thread(fetch_batch_quotes)
        return {
            "status": "success",
            "message": "风格题材时效指标及标的池已完成智能刷新",
            "data": res
        }
    except Exception as e:
        logger.error(f"刷新风格题材时效指标异常: {e}", exc_info=True)
        return {
            "status": "error",
            "message": f"刷新失败: {str(e)}",
            "data": None
        }


@app.get("/api/macro/compass")
async def get_macro_compass():
    """获取宏观与风格罗盘数据 (南方基金「指数观澜」量化框架与本地模型对齐)"""
    return {"status": "success", "data": macro_compass.get_full_status()}


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


@app.get("/api/stock/{code}/history")
async def get_stock_history_endpoint(code: str):
    """
    获取标的自上市首日至最新交易日的全景走势线、PB、PE 与 ROE 走势及分位数指标
    """
    try:
        from scripts.stock_history_provider import get_stock_history_and_valuation
        res = await asyncio.to_thread(get_stock_history_and_valuation, code)
        return res
    except Exception as e:
        logger.error(f"获取标的 {code} 历史走势与估值失败: {e}", exc_info=True)
        return {
            "status": "error",
            "message": str(e),
            "summary": {},
            "monthly_history": [],
            "daily_history": [],
            "valuation_history": [],
            "roe_history": []
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


def ensure_port_available(port: int = 8765):
    """确保端口可用，若被旧进程占用则自动安全终止旧进程，避免 Errno 10048。"""
    try:
        import psutil
        current_pid = os.getpid()
        for conn in psutil.net_connections(kind="inet"):
            if conn.laddr and conn.laddr.port == port and conn.status == "LISTEN":
                pid = conn.pid
                if pid and pid != current_pid:
                    try:
                        p = psutil.Process(pid)
                        logger.info(f"🔄 检测到端口 {port} 已被旧进程 (PID: {pid}, {p.name()}) 占用，正在释放...")
                        p.terminate()
                        p.wait(timeout=2)
                    except Exception:
                        try:
                            p.kill()
                        except Exception:
                            pass
                    time.sleep(0.5)
    except Exception as e:
        logger.warning(f"检查端口占用时出现警告 (可忽略): {e}")


def run():
    ensure_port_available(8765)
    print("========================================================")
    print("🌐 QUANT RADAR (jiankong) 资产实时监控 Web 应用程序正在启动...")
    print("👉 本地访问地址: http://127.0.0.1:8765")
    print("========================================================")
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")


if __name__ == "__main__":
    run()

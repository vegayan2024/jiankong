#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
问财热点 · 板块头部代表公司二级内容数据端口
===========================================

为「问财热点」每个热度板块提供悬浮二级内容的数据:
1. 头部代表公司 (1~3 家): 同花顺问财开放平台 query2data
   查询「{板块名}概念成分股按总市值排名」, 总市值降序取前 3 (TOP_N)
2. 简单介绍: 问财原生「纳入概念原因」原文 (优先【板块名】条目) + 所属同花顺行业标签
3. 近7日走势线: 腾讯行情 fqkline 前复权(qfq)日K 最近 7 个交易日收盘序列
   (与 stock_history_provider 的腾讯日K端口同源)

真实性纪律:
- 成分股/市值/简介 100% 透传问财真实返回字段, 禁止估算/补值/静态拼凑
- 归属校验: 返回行的「所属概念」必须含目标板块名, 不含则剔除; 全部被剔除则显式报错
- 走势取数失败 → trend=None 并附 trend_error 真实原因, 禁止静默补线
- 问财查询失败 → 抛 IwencaiProviderError 透传真实原因, 禁止跨数据源静默降级
- 逐板块当日缓存 (scratch/theme_board_leaders_cache.json), 跨日自动失效

查询口径选型 (2026-11-18 实测定稿):
- 采用名称型「{板块名}概念成分股按总市值排名」: 一次返回总市值[YYYYMMDD]/所属概念/
  纳入概念原因/所属同花顺行业 全字段, 且名称边角写法 (共封装光学(CPO)、芯片概念、AI应用) 均可用
- 不采用代码型合并查询「886033.TI成分股按总市值排名、纳入概念原因」: 实测其「纳入原因/
  概念名称」列与目标概念错位 (返回接入deepseek的纳入原因), 口径漂移, 拒绝使用
"""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from scripts.iwencai_provider import IwencaiProviderError, query2data
from scripts.stock_history_provider import clean_symbol, fetch_day_kline

JIANKONG_DIR = Path(__file__).resolve().parent.parent
CACHE_FILE = JIANKONG_DIR / "scratch" / "theme_board_leaders_cache.json"

TOP_N = 3                # 头部代表公司数量 (1~3 家)
LEADERS_QUERY_LIMIT = 30  # 问财返回行数上限 (已按总市值降序, 取前3需覆盖归属剔除损耗)
TREND_BARS = 7           # 近7日走势线交易点数

# 总市值为带日期窗口列名: 总市值[20261118]
MARKET_CAP_PATTERN = re.compile(r"^总市值\[(\d{8})\]$")

TREND_SOURCE = "腾讯行情 fqkline 前复权(qfq)日K"
LEADERS_SOURCE = "同花顺问财开放平台 query2data (hithink-industry-query skill 内化端口)"


def build_constituent_query(board_name: str) -> str:
    """构造成分股市值排名查询。板块名已含「概念」后缀则不重复追加 (如「芯片概念」)"""
    name = str(board_name).strip()
    if not name:
        raise IwencaiProviderError("板块名为空, 无法构造成分股查询")
    concept_name = name if name.endswith("概念") else f"{name}概念"
    return f"{concept_name}成分股按总市值排名"


def _to_float(v: Any) -> Optional[float]:
    """真实值数值化: 数字直接用, 数字字符串忠实解析; 其余 None, 不估算"""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return None
    return None


def _find_market_cap(row: Dict[str, Any]) -> Optional[float]:
    """从带日期窗口的总市值列取值 (总市值[YYYYMMDD])"""
    for key, val in row.items():
        if MARKET_CAP_PATTERN.match(str(key)):
            return _to_float(val)
    return None


def _is_board_member(row: Dict[str, Any], board_name: str) -> bool:
    """归属校验: 行的「所属概念」必须含目标板块名, 防止同名/近名板块串数据"""
    concepts = row.get("所属概念")
    if isinstance(concepts, list):
        return any(str(c).strip() == board_name for c in concepts)
    if isinstance(concepts, str):
        # 问财偶发返回字符串化列表, 仍按原文包含判定
        return board_name in concepts
    return False


def _pick_intro(row: Dict[str, Any], board_name: str) -> str:
    """提取「纳入概念原因」原文: 优先【板块名】条目, 否则取首条; 无则空串(不编造)"""
    reasons = row.get("纳入概念原因")
    if isinstance(reasons, str):
        reasons = [reasons]
    if not isinstance(reasons, list) or not reasons:
        return ""
    prefix = f"【{board_name}】"
    for r in reasons:
        if isinstance(r, str) and r.startswith(prefix):
            return r.strip()
    first = reasons[0]
    return first.strip() if isinstance(first, str) else ""


def _industry_list(row: Dict[str, Any]) -> List[str]:
    """所属同花顺行业标签 (原生列表)"""
    ind = row.get("所属同花顺行业")
    if isinstance(ind, list):
        return [str(x) for x in ind]
    if isinstance(ind, str) and ind.strip():
        return [ind.strip()]
    return []


def fetch_recent_trend(full_code: str) -> Dict[str, Any]:
    """
    近 TREND_BARS 个交易日走势线 (腾讯 qfq 日K)。
    成功: {"dates": [...], "closes": [...], "pct_7d": float, "source": ...}
    失败: {"error": "<真实原因>", "source": ...} —— 显式上抛给前端, 不补线
    """
    try:
        info = clean_symbol(full_code)
        bars = fetch_day_kline(info["tencent_sym"], count=TREND_BARS + 3)
    except Exception as e:
        return {"error": f"腾讯日K获取失败: {e}", "source": TREND_SOURCE}
    if not bars:
        return {"error": "腾讯日K返回空序列", "source": TREND_SOURCE}
    window = bars[-TREND_BARS:]
    if len(window) < 2:
        return {"error": f"腾讯日K有效点不足 (仅{len(window)}个交易日)", "source": TREND_SOURCE}
    dates = [b["date"] for b in window]
    closes = [float(b["close"]) for b in window]
    pct_7d = (closes[-1] / closes[0] - 1.0) * 100.0 if closes[0] else None
    return {
        "dates": dates,
        "closes": closes,
        "pct_7d": round(pct_7d, 2) if pct_7d is not None else None,
        "source": TREND_SOURCE,
    }


def _load_cache() -> Dict[str, Any]:
    if not CACHE_FILE.exists():
        return {}
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_cache(cache: Dict[str, Any]) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def fetch_board_leaders(board_code: str, board_name: str, refresh: bool = False) -> Dict[str, Any]:
    """
    计算某热度板块的头部代表公司二级内容 (当日缓存)。

    返回:
        {
          "board": {"code": "886050.TI", "name": "算力租赁"},
          "selection_rule": "成分股按总市值降序取前3 (问财原生总市值[YYYYMMDD]列)",
          "query": "算力租赁概念成分股按总市值排名",
          "trace_id": "...",
          "generated_at": "2026-11-18 15:30:00",
          "sources": {"leaders": 问财..., "intro": "同花顺问财·纳入概念原因", "trend": 腾讯...},
          "leaders": [
            {"rank": 1, "code": "600941.SH", "name": "中国移动",
             "price": 107.38, "pct_today": 2.07, "market_cap": 1.53e12,
             "industry": ["通信", ...], "intro": "【算力租赁】...原文...",
             "trend": {"dates": [...], "closes": [...], "pct_7d": 3.21, "source": ...}}
          ]
        }
    任何一步不满足口径 → 抛 IwencaiProviderError, 不出数。
    """
    board_code = str(board_code).strip()
    board_name = str(board_name).strip()
    today = datetime.now().strftime("%Y-%m-%d")

    cache = _load_cache()
    raw_day = cache.get(today)
    day_cache: Dict[str, Any] = raw_day if isinstance(raw_day, dict) else {}
    if not refresh and board_code in day_cache:
        return day_cache[board_code]

    query = build_constituent_query(board_name)
    resp = query2data(query, limit=LEADERS_QUERY_LIMIT)
    trace_id = str(resp.get("trace_id", ""))
    datas = resp.get("datas") or []
    if not datas:
        raise IwencaiProviderError(
            f"问财成分股查询返回空数据集 (query={query}, trace_id={trace_id})"
        )

    # 归属校验 + 总市值降序取前 TOP_N
    verified = [row for row in datas if _is_board_member(row, board_name)]
    if not verified:
        raise IwencaiProviderError(
            f"问财成分股查询结果归属校验失败: 返回 {len(datas)} 行的「所属概念」均不含「{board_name}」"
            f" (query={query}, trace_id={trace_id}), 拒绝出数"
        )
    verified.sort(key=lambda r: (_find_market_cap(r) is not None, _find_market_cap(r) or 0.0), reverse=True)
    top_rows = verified[:TOP_N]

    # 逐股并行拉取近7日走势 (单股失败不拖垮整板, 显式标注 trend_error)
    def _trend_of(row: Dict[str, Any]) -> Dict[str, Any]:
        return fetch_recent_trend(str(row.get("股票代码", "")))

    with ThreadPoolExecutor(max_workers=max(1, len(top_rows))) as pool:
        trends = list(pool.map(_trend_of, top_rows))

    leaders: List[Dict[str, Any]] = []
    for i, (row, trend) in enumerate(zip(top_rows, trends), start=1):
        trend_payload: Optional[Dict[str, Any]] = None
        trend_error: Optional[str] = None
        if isinstance(trend, dict) and "error" in trend:
            trend_error = str(trend["error"])
        elif isinstance(trend, dict):
            trend_payload = trend
        leaders.append({
            "rank": i,
            "code": str(row.get("股票代码", "")),
            "name": str(row.get("股票简称", "")),
            "price": _to_float(row.get("最新价")),
            "pct_today": _to_float(row.get("最新涨跌幅")),
            "market_cap": _find_market_cap(row),
            "industry": _industry_list(row),
            "intro": _pick_intro(row, board_name),
            "intro_source": "同花顺问财·纳入概念原因",
            "trend": trend_payload,
            "trend_error": trend_error,
        })

    result = {
        "board": {"code": board_code, "name": board_name},
        "selection_rule": "成分股按总市值降序取前3 (问财原生总市值[YYYYMMDD]列, 归属校验: 所属概念含板块名)",
        "query": query,
        "trace_id": trace_id,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "sources": {
            "leaders": LEADERS_SOURCE,
            "intro": "同花顺问财·纳入概念原因 (原文透传)",
            "trend": TREND_SOURCE,
        },
        "leaders": leaders,
    }

    # 当日缓存: 只保留当日 key, 防止跨日旧数据膨胀
    try:
        _save_cache({today: {**day_cache, board_code: result}})
    except Exception:
        pass  # 缓存落盘失败不影响本次出数
    return result

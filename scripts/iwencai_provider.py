#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
问财热点数据源 · skill 内化数据提供端口
=======================================

将 hithink-industry-query 技能(同花顺问财开放平台)内化为「问财热点」栏目的
数据提供端口。协议与 ~/.claude/skills/hithink-industry-query/scripts/cli.py 1:1 对齐:
- 端点: https://openapi.iwencai.com/v1/query2data (POST)
- Authorization: Bearer IWENCAI_API_KEY (环境变量优先, 其次 jiankong/.env)
- 每请求携带 8 个 X-Claw-* Header, X-Claw-Trace-Id 为 64 位十六进制唯一 ID

核心能力: fetch_hot_ranking_top15()
- 查询「近3个交易日概念板块热度排名」, 取问财原生 `板块热度[YYYYMMDD-YYYYMMDD]` 窗口字段
- 全宇宙(390 只概念指数)按板块热度降序, 取前 15 —— 即「最近3个工作日热度排行前15」
- 校验热度窗口恰为 3 个交易日且窗口截止为最近交易日; 不满足则显式报错, 绝不硬算/冒充

真实性纪律:
- 只透传问财真实返回字段, 禁止任何估算/补值/静态拼凑
- 失败即抛 IwencaiProviderError 并附真实原因, 由上层显式提示, 禁止静默降级
"""

import json
import os
import re
import secrets
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

JIANKONG_DIR = Path(__file__).resolve().parent.parent
RANKING_CACHE_FILE = JIANKONG_DIR / "wencai_hot_ranking.json"

API_URL = "https://openapi.iwencai.com/v1/query2data"
SKILL_ID = "hithink-industry-query"
SKILL_VERSION = "1.0.0"

# 生产查询措辞(已实测锁定): 返回原生 3 交易日窗口 `板块热度[YYYYMMDD-YYYYMMDD]`。
# 禁止与其他字段合并查询 —— 合并会使热度列退化为单日窗口, 口径失真。
HOT_QUERY = "近3个交易日概念板块热度排名"
TOP_N = 15
REQUEST_LIMIT = 400  # 一次拉全概念宇宙(实测 code_count=390), 保证全局排序正确

# 热度列窗口模式: 板块热度[20261116-20261118]
HEAT_WINDOW_PATTERN = re.compile(r"^板块热度\[(\d{8})-(\d{8})\]$")

# 直连, 绕过系统代理(与 update_themes_intel.py 一致)
_URL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class IwencaiProviderError(Exception):
    """问财数据端口异常: 消息即真实失败原因, 上层必须显式透传给用户"""


def _load_api_key() -> str:
    """API Key: 环境变量 IWENCAI_API_KEY 优先, 其次 jiankong/.env (bat 启动场景)"""
    key = os.environ.get("IWENCAI_API_KEY", "").strip()
    if key:
        return key
    env_file = JIANKONG_DIR / ".env"
    if env_file.exists():
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("IWENCAI_API_KEY="):
                    key = line.split("=", 1)[1].strip()
                    if key:
                        return key
        except Exception as e:
            raise IwencaiProviderError(f"读取 {env_file} 中 IWENCAI_API_KEY 失败: {e}")
    raise IwencaiProviderError(
        "IWENCAI_API_KEY 未配置: 请设置环境变量或写入 jiankong/.env, "
        "获取路径 https://www.iwencai.com/skillhub → 登录 → Skill → 安装方式-Agent用户"
    )


def query2data(query: str, limit: int = REQUEST_LIMIT, page: str = "1",
               timeout: int = 30) -> Dict[str, Any]:
    """问财开放平台 query2data 协议调用(与技能 cli.py 一致)"""
    api_key = _load_api_key()
    trace_id = secrets.token_hex(32)
    payload = {
        "query": query,
        "page": page,
        "limit": str(limit),
        "is_cache": "1",
        "expand_index": "true",
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-Claw-Call-Type": "normal",
        "X-Claw-Skill-Id": SKILL_ID,
        "X-Claw-Skill-Version": SKILL_VERSION,
        "X-Claw-Plugin-Id": "none",
        "X-Claw-Plugin-Version": "none",
        "X-Claw-Trace-Id": trace_id,
    }
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with _URL_OPENER.open(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        raise IwencaiProviderError(
            f"问财网关 HTTP {e.code} {e.reason} (trace_id={trace_id}): {err_body[:300]}"
        )
    except urllib.error.URLError as e:
        raise IwencaiProviderError(f"问财网关网络错误 (trace_id={trace_id}): {e.reason}")
    except Exception as e:
        raise IwencaiProviderError(f"问财网关请求异常 (trace_id={trace_id}): {e}")

    if not body.strip():
        raise IwencaiProviderError(f"问财网关返回空响应 (trace_id={trace_id})")
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        raise IwencaiProviderError(
            f"问财网关返回非 JSON (trace_id={trace_id}): {body[:300]}"
        )
    if not isinstance(parsed, dict):
        raise IwencaiProviderError(
            f"问财网关返回结构异常 (trace_id={trace_id}): {str(parsed)[:300]}"
        )
    if "datas" not in parsed:
        # 网关层错误(额度不足/次数超限等): 透传原始响应, 不改写不淹没
        raise IwencaiProviderError(
            f"问财网关未返回数据 (trace_id={trace_id}): "
            f"{json.dumps(parsed, ensure_ascii=False)[:300]}"
        )
    parsed["trace_id"] = parsed.get("trace_id") or trace_id
    return parsed


def _load_trading_dates(year: int) -> List[str]:
    """读取本地交易日历(scratch/trade_dates_YYYY.json, baostock 缓存), 格式 YYYY-MM-DD"""
    cache_file = JIANKONG_DIR / "scratch" / f"trade_dates_{year}.json"
    if not cache_file.exists():
        raise IwencaiProviderError(
            f"交易日历缺失: {cache_file} 不存在, 无法校验热度窗口是否为最近3个交易日"
        )
    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [str(d) for d in data]
    except Exception as e:
        raise IwencaiProviderError(f"交易日历读取失败 {cache_file}: {e}")


def _validate_window_3_trading_days(start: date, end: date) -> List[str]:
    """校验 [start, end] 恰含 3 个交易日, 且 end 为 ≤今天的最近交易日; 返回窗口内交易日列表"""
    if end < start:
        raise IwencaiProviderError(f"热度窗口非法: 起 {start} 晚于 止 {end}")
    years = range(start.year, end.year + 1)
    trading = set()
    for y in years:
        trading.update(_load_trading_dates(y))
    days = []
    d = start
    while d <= end:
        if d.strftime("%Y-%m-%d") in trading:
            days.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    if len(days) != 3:
        raise IwencaiProviderError(
            f"热度窗口 {start}~{end} 内交易日为 {len(days)} 个({days}), "
            f"非「最近3个交易日」口径, 拒绝出数"
        )
    # end 必须是 ≤今天的最近交易日
    today = date.today()
    recent = max((s for s in trading if s <= today.strftime("%Y-%m-%d")), default=None)
    if recent != end.strftime("%Y-%m-%d"):
        raise IwencaiProviderError(
            f"热度窗口截止日 {end} 非最近交易日({recent}), 问财口径漂移, 拒绝出数"
        )
    return days


def fetch_hot_ranking_top15() -> Dict[str, Any]:
    """
    计算「最近3个交易日概念板块热度排行前15」并落盘缓存。

    返回:
        {
          "window": {"start": "2026-11-16", "end": "2026-11-18",
                     "label": "2026-11-16 ~ 2026-11-18", "trading_days": [...]},
          "generated_at": "2026-11-18 13:05:22",
          "query": HOT_QUERY,
          "trace_id": "...",
          "source": "同花顺问财开放平台 query2data (hithink-industry-query skill 内化端口)",
          "items": [
            {"rank": 1, "code": "886050.TI", "name": "算力租赁",
             "heat": 4862062.3, "pct_today": 2.71, "close": 1195.384,
             "index_type": "同花顺概念指数", "sector": "A股指数"}, ...
          ],
          "total_universe": 390
        }
    任何一步不满足口径 → 抛 IwencaiProviderError, 不落盘、不出数。
    """
    resp = query2data(HOT_QUERY, limit=REQUEST_LIMIT)
    trace_id = resp.get("trace_id", "")
    datas = resp.get("datas") or []
    if not datas:
        raise IwencaiProviderError(f"问财返回空数据集 (trace_id={trace_id})")

    # 定位窗口热度列
    heat_col = None
    for key in datas[0].keys():
        m = HEAT_WINDOW_PATTERN.match(key)
        if m:
            heat_col = key
            win_start = datetime.strptime(m.group(1), "%Y%m%d").date()
            win_end = datetime.strptime(m.group(2), "%Y%m%d").date()
            break
    if heat_col is None:
        raise IwencaiProviderError(
            f"问财未返回 3 日窗口板块热度列(实际列: {list(datas[0].keys())}), "
            f"口径不符, 拒绝出数 (trace_id={trace_id})"
        )

    # 窗口口径校验: 恰 3 个交易日且截止为最近交易日
    trading_days = _validate_window_3_trading_days(win_start, win_end)

    # 全宇宙按板块热度降序取前 15
    def heat_of(row: Dict[str, Any]) -> float:
        v = _to_float(row.get(heat_col))
        return v if v is not None else float("-inf")

    ranked = sorted(datas, key=heat_of, reverse=True)

    items: List[Dict[str, Any]] = []
    for i, row in enumerate(ranked[:TOP_N], start=1):
        items.append({
            "rank": i,
            "code": str(row.get("指数代码", "")),
            "name": str(row.get("指数简称", "")),
            "heat": _to_float(row.get(heat_col)),
            "pct_today": _to_float(row.get("涨跌幅")),
            "close": _to_float(row.get("收盘价")),
            "index_type": str(row.get("指数类型", "")),
            "sector": str(row.get("成分领域", "")),
        })

    result = {
        "window": {
            "start": win_start.strftime("%Y-%m-%d"),
            "end": win_end.strftime("%Y-%m-%d"),
            "label": f"{win_start.strftime('%Y-%m-%d')} ~ {win_end.strftime('%Y-%m-%d')}",
            "trading_days": trading_days,
        },
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "query": HOT_QUERY,
        "trace_id": trace_id,
        "heat_field": heat_col,
        "source": "同花顺问财开放平台 query2data (hithink-industry-query skill 内化端口)",
        "items": items,
        "total_universe": len(datas),
    }
    try:
        with open(RANKING_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    except Exception as e:
        raise IwencaiProviderError(f"排行缓存落盘失败 {RANKING_CACHE_FILE}: {e}")
    return result


def load_ranking_cache() -> Optional[Dict[str, Any]]:
    """读取最近一次成功计算的排行缓存; 无则 None(上层显式提示, 不用静态内容顶替)"""
    if not RANKING_CACHE_FILE.exists():
        return None
    try:
        with open(RANKING_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _to_float(v: Any) -> Optional[float]:
    """真实值数值化: 数字直接用, 数字字符串(问财收盘价实测为 str)忠实解析; 其余 None, 不估算"""
    if _is_number(v):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return None
    return None


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        res = fetch_hot_ranking_top15()
        print(json.dumps(res, ensure_ascii=False, indent=2))
    except IwencaiProviderError as e:
        print(json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False))
        raise SystemExit(1)

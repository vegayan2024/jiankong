# -*- coding: utf-8 -*-
"""
交易时段判定模块 (Market Session Detector)
==========================================
为首页大屏提供「当前时间点 + 交易时间/非交易时间」标注。

覆盖：
- A股 (沪深京) 交易时段：集合竞价 / 上午连续竞价 / 午间休市 / 下午连续竞价 / 收盘
- 周末休市
- 法定节假日休市 (通过 baostock 交易日历缓存全年交易日，跨年或过期自动刷新)

港股标的另行提示 (港股 9:30-12:00 / 13:00-16:00)，首页主徽章以 A股时段为准 (监控主体)。
"""

import json
import logging
from pathlib import Path
from datetime import datetime, date, time as dtime
from typing import Dict, Any, Optional

logger = logging.getLogger("MarketSession")

CACHE_DIR = Path(__file__).resolve().parent.parent / "scratch"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# 全年交易日内存缓存 (年份 -> 交易日集合)，避免高频 tick 每次读文件
_YEAR_DATES_MEM: Dict[int, set] = {}


def _is_weekend(d: date) -> bool:
    return d.weekday() >= 5  # 5=周六, 6=周日


def _load_trade_dates_year(year: int) -> Optional[set]:
    """读取全年交易日集合 (优先内存缓存，其次磁盘缓存)。"""
    if year in _YEAR_DATES_MEM:
        return _YEAR_DATES_MEM[year]
    cache_file = CACHE_DIR / f"trade_dates_{year}.json"
    try:
        if cache_file.exists():
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                _YEAR_DATES_MEM[year] = set(data)
                return _YEAR_DATES_MEM[year]
    except Exception:
        pass
    return None


def _fetch_trade_dates_year(year: int) -> Optional[set]:
    """用 baostock 拉取全年交易日并缓存。失败返回 None (退化为仅周末判断)。"""
    try:
        import baostock as bs  # type: ignore
        lg = bs.login()
        if lg.error_code != "0":
            return None
        try:
            rs = bs.query_trade_dates(start_date=f"{year}-01-01", end_date=f"{year}-12-31")
            if rs.error_code != "0":
                return None
            trading = []
            while rs.next():
                row = rs.get_row_data()  # [trade_date, is_trading_day]
                if len(row) >= 2 and row[1] == "1":
                    trading.append(row[0])
        finally:
            bs.logout()
        if not trading:
            return None
        cache_file = CACHE_DIR / f"trade_dates_{year}.json"
        cache_file.write_text(json.dumps(trading, ensure_ascii=False), encoding="utf-8")
        _YEAR_DATES_MEM[year] = set(trading)
        logger.info(f"已缓存 {year} 年交易日历: {len(trading)} 个交易日")
        return _YEAR_DATES_MEM[year]
    except Exception as e:
        logger.warning(f"拉取 {year} 年交易日历失败 (将退化为周末判断): {e}")
        return None


def is_trading_day(d: Optional[date] = None) -> bool:
    """判断是否为 A股交易日 (非周末且在交易日历内)。日历不可用时退化为：非周末即交易日。"""
    d = d or date.today()
    if _is_weekend(d):
        return False
    year = d.year
    trading_dates = _load_trade_dates_year(year)
    if trading_dates is None:
        trading_dates = _fetch_trade_dates_year(year)
    if trading_dates is None:
        return True  # 退化：工作日视为交易日 (可能误判节假日，但保证可用)
    return d.strftime("%Y-%m-%d") in trading_dates


def is_hk_trading_day(d: Optional[date] = None) -> bool:
    """判断是否为港股交易日 (排除周末与已知港股法定节假日)"""
    d = d or date.today()
    if _is_weekend(d):
        return False
    # 常用已知港股休市固定节假日（元旦、劳动节、国庆、圣诞节等）
    hk_fixed_holidays = {
        "01-01", "05-01", "07-01", "10-01", "12-25", "12-26"
    }
    if d.strftime("%m-%d") in hk_fixed_holidays:
        return False
    return True


def get_cn_session(now: Optional[datetime] = None) -> Dict[str, Any]:
    """返回 A股 (沪深京) 当前交易时段状态"""
    now = now or datetime.now()
    d = now.date()
    t = now.time()

    if _is_weekend(d):
        return {"is_trading_day": False, "is_trading": False, "session": "weekend",
                "session_label": "周末休市", "note": "周末闭市"}
    if not is_trading_day(d):
        return {"is_trading_day": False, "is_trading": False, "session": "holiday",
                "session_label": "节假日休市", "note": "A股法定节假日闭市"}

    call_auction = dtime(9, 15)
    morning_open = dtime(9, 30)
    morning_close = dtime(11, 30)
    afternoon_open = dtime(13, 0)
    afternoon_close = dtime(15, 0)

    if t < call_auction:
        return {"is_trading_day": True, "is_trading": False, "session": "pre_open",
                "session_label": "尚未开盘", "note": "开盘集合竞价 9:15 开始"}
    elif t < morning_open:
        return {"is_trading_day": True, "is_trading": False, "session": "pre_open",
                "session_label": "集合竞价", "note": "9:30 开始连续竞价"}
    elif t < morning_close:
        return {"is_trading_day": True, "is_trading": True, "session": "morning",
                "session_label": "上午交易中", "note": "连续竞价 9:30-11:30"}
    elif t < afternoon_open:
        return {"is_trading_day": True, "is_trading": False, "session": "lunch",
                "session_label": "午间休市", "note": "13:00 恢复交易"}
    elif t < afternoon_close:
        return {"is_trading_day": True, "is_trading": True, "session": "afternoon",
                "session_label": "下午交易中", "note": "连续竞价 13:00-15:00"}
    else:
        return {"is_trading_day": True, "is_trading": False, "session": "closed",
                "session_label": "已收盘", "note": "15:00 收盘"}


def get_hk_session(now: Optional[datetime] = None) -> Dict[str, Any]:
    """返回 港股 (HKEX) 当前交易时段状态 (覆盖 11:30-12:00 及 15:00-16:00 交易时段)"""
    now = now or datetime.now()
    d = now.date()
    t = now.time()

    if _is_weekend(d):
        return {"is_trading_day": False, "is_trading": False, "session": "weekend",
                "session_label": "周末休市", "note": "港股周末休市"}
    if not is_hk_trading_day(d):
        return {"is_trading_day": False, "is_trading": False, "session": "holiday",
                "session_label": "港股假期休市", "note": "港股法定节假日休市"}

    pos_open = dtime(9, 0)
    morning_open = dtime(9, 30)
    morning_close = dtime(12, 0)
    afternoon_open = dtime(13, 0)
    afternoon_close = dtime(16, 0)
    cas_close = dtime(16, 10)

    if t < pos_open:
        return {"is_trading_day": True, "is_trading": False, "session": "pre_open",
                "session_label": "尚未开盘", "note": "开市前时段 9:00 开始"}
    elif t < morning_open:
        return {"is_trading_day": True, "is_trading": False, "session": "pre_open",
                "session_label": "开市前竞价", "note": "9:30 开始连续竞价"}
    elif t < morning_close:
        return {"is_trading_day": True, "is_trading": True, "session": "morning",
                "session_label": "早市交易中", "note": "早市连续竞价 9:30-12:00"}
    elif t < afternoon_open:
        return {"is_trading_day": True, "is_trading": False, "session": "lunch",
                "session_label": "午市休市", "note": "13:00 恢复午市交易"}
    elif t < afternoon_close:
        return {"is_trading_day": True, "is_trading": True, "session": "afternoon",
                "session_label": "午市交易中", "note": "午市连续竞价 13:00-16:00"}
    elif t < cas_close:
        return {"is_trading_day": True, "is_trading": False, "session": "closing_auction",
                "session_label": "收市竞价", "note": "收市竞价时段 16:00-16:10"}
    else:
        return {"is_trading_day": True, "is_trading": False, "session": "closed",
                "session_label": "已收市", "note": "16:00/16:10 收市"}


def get_market_session(now: Optional[datetime] = None) -> Dict[str, Any]:
    """
    返回综合跨市场交易时段状态 (A股 + 港股双引擎独立感知)。
    保证在 11:30-12:00 及 15:00-16:00 港股交易时段系统精准标注并维持高频轮询！
    """
    now = now or datetime.now()
    cn = get_cn_session(now)
    hk = get_hk_session(now)

    is_trading = cn["is_trading"] or hk["is_trading"]
    is_trading_day = cn["is_trading_day"] or hk["is_trading_day"]

    if cn["is_trading"] and hk["is_trading"]:
        status_label = "全市场交易中"
        session_label = "A+H 连续竞价"
        status_icon = "🟢"
    elif cn["is_trading"]:
        status_label = "A股交易中"
        session_label = f"A股{cn['session_label']} (港股{hk['session_label']})"
        status_icon = "🟢"
    elif hk["is_trading"]:
        status_label = "港股交易中"
        session_label = f"港股{hk['session_label']} (A股{cn['session_label']})"
        status_icon = "🟢"
    elif cn["session"] == "pre_open" or hk["session"] == "pre_open":
        status_label = "集合竞价"
        session_label = f"A股{cn['session_label']} / 港股{hk['session_label']}"
        status_icon = "🟡"
    elif cn["session"] == "lunch" and hk["session"] == "lunch":
        status_label = "午间休市"
        session_label = "午间休市中"
        status_icon = "🟡"
    else:
        status_label = "非交易时间"
        session_label = cn["session_label"]
        status_icon = "🔴"

    return {
        "server_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "date": now.strftime("%Y-%m-%d"),
        "weekday": "一二三四五六日"[now.weekday()],
        "is_trading_day": is_trading_day,
        "is_trading": is_trading,
        "status_label": status_label,
        "status_icon": status_icon,
        "session_label": session_label,
        "cn": cn,
        "hk": hk,
        "note": f"A股: {cn['note']} | 港股: {hk['note']}"
    }


market_session = get_market_session  # 便捷别名


if __name__ == "__main__":
    from datetime import datetime as _dt
    # 自测若干时间点
    for ts in ["2026-10-29 10:30:00", "2026-10-29 12:15:00", "2026-10-29 16:30:00",
               "2026-10-25 11:00:00", "2026-10-29 08:00:00"]:
        r = get_market_session(_dt.strptime(ts, "%Y-%m-%d %H:%M:%S"))
        print(f"{ts} -> {r['status_icon']} {r['status_label']} | {r['session_label']} | {r['note']}")

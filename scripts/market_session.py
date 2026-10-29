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


def get_market_session(now: Optional[datetime] = None) -> Dict[str, Any]:
    """
    返回当前 A股交易时段状态，用于首页「交易时间/非交易时间」标注。

    返回字段:
        server_time      当前时间 YYYY-MM-DD HH:MM:SS
        is_trading_day   是否交易日 (非周末/节假日)
        is_trading       是否处于连续竞价交易时段 (交易时间=True)
        session          时段代码: pre_open/morning/lunch/afternoon/closed/weekend/holiday
        session_label    时段中文标签
        status_label     核心标注: "交易时间" / "非交易时间"
        status_icon      状态图标: 🟢交易 / 🟡午休 / 🔴休市
        note             补充说明 (如下一开盘时间)
    """
    now = now or datetime.now()
    d = now.date()
    t = now.time()

    base = {
        "server_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "date": d.strftime("%Y-%m-%d"),
        "weekday": "一二三四五六日"[d.weekday()],
    }

    # 周末
    if _is_weekend(d):
        return {**base, "is_trading_day": False, "is_trading": False,
                "session": "weekend", "session_label": "周末休市",
                "status_label": "非交易时间", "status_icon": "🔴",
                "note": "周末闭市，行情为最近交易日收盘数据"}

    # 法定节假日
    if not is_trading_day(d):
        return {**base, "is_trading_day": False, "is_trading": False,
                "session": "holiday", "session_label": "节假日休市",
                "status_label": "非交易时间", "status_icon": "🔴",
                "note": "法定节假日闭市"}

    # 交易日内的时段划分 (北京时间，A股)
    morning_open = dtime(9, 30)
    morning_close = dtime(11, 30)
    afternoon_open = dtime(13, 0)
    afternoon_close = dtime(15, 0)
    call_auction = dtime(9, 15)

    if t < call_auction:
        session, label, trading, icon = "pre_open", "尚未开盘", False, "🔴"
        note = "开盘集合竞价 9:15 开始"
    elif t < morning_open:
        session, label, trading, icon = "pre_open", "集合竞价", False, "🟡"
        note = "9:30 开始连续竞价"
    elif t < morning_close:
        session, label, trading, icon = "morning", "上午交易中", True, "🟢"
        note = "上午连续竞价 9:30-11:30"
    elif t < afternoon_open:
        session, label, trading, icon = "lunch", "午间休市", False, "🟡"
        note = "13:00 恢复下午交易"
    elif t < afternoon_close:
        session, label, trading, icon = "afternoon", "下午交易中", True, "🟢"
        note = "下午连续竞价 13:00-15:00"
    else:
        session, label, trading, icon = "closed", "已收盘", False, "🔴"
        note = "15:00 收盘，次一交易日 9:30 开盘"

    return {**base,
            "is_trading_day": True, "is_trading": trading,
            "session": session, "session_label": label,
            "status_label": "交易时间" if trading else "非交易时间",
            "status_icon": icon, "note": note}


market_session = get_market_session  # 便捷别名


if __name__ == "__main__":
    from datetime import datetime as _dt
    # 自测若干时间点
    for ts in ["2026-10-29 10:30:00", "2026-10-29 12:15:00", "2026-10-29 16:30:00",
               "2026-10-25 11:00:00", "2026-10-29 08:00:00"]:
        r = get_market_session(_dt.strptime(ts, "%Y-%m-%d %H:%M:%S"))
        print(f"{ts} -> {r['status_icon']} {r['status_label']} | {r['session_label']} | {r['note']}")

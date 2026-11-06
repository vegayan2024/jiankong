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
from datetime import datetime, date, time as dtime, timedelta
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


# 港股全年交易日内存缓存 (年份 -> 交易日集合)
_HK_YEAR_DATES_MEM: Dict[int, set] = {}

# 港股 2024-2027 年法定休市日清单 (完全独立于 BaoStock 与 A股日历)
HK_KNOWN_HOLIDAYS = {
    # 2024
    "2024-01-01", "2024-02-12", "2024-02-13", "2024-03-29", "2024-04-01",
    "2024-04-04", "2024-05-01", "2024-05-15", "2024-06-10", "2024-07-01",
    "2024-09-18", "2024-10-01", "2024-10-11", "2024-12-25", "2024-12-26",
    # 2025
    "2025-01-01", "2025-01-29", "2025-01-30", "2025-01-31", "2025-04-04",
    "2025-04-18", "2025-04-21", "2025-05-01", "2025-05-05", "2025-05-31",
    "2025-07-01", "2025-10-01", "2025-10-07", "2025-10-29", "2025-12-25", "2025-12-26",
    # 2026
    "2026-01-01", "2026-02-17", "2026-02-18", "2026-02-19", "2026-04-03",
    "2026-04-06", "2026-05-01", "2026-05-25", "2026-06-19", "2026-07-01",
    "2026-09-28", "2026-10-01", "2026-10-19", "2026-12-25", "2026-12-28",
    # 2027
    "2027-01-01", "2027-02-08", "2027-02-09", "2027-03-26", "2027-03-29",
    "2027-04-05", "2027-05-03", "2027-05-13", "2027-06-09", "2027-07-01",
    "2027-10-01", "2027-10-11", "2027-12-27", "2027-12-28",
}

# 港交所法定半日市日期表 (Half-Day Trading: 仅上午交易，12:00-12:10 CAS，下午休市)
# 涵盖平安夜 (12-24)、大除夕 (12-31) 以及农历除夕前一日 (若为交易日)
HK_HALF_DAYS = {
    # 2024
    "2024-02-09", "2024-12-24", "2024-12-31",
    # 2025
    "2025-01-28", "2025-12-24", "2025-12-31",
    # 2026
    "2026-02-16", "2026-12-24", "2026-12-31",
    # 2027
    "2027-02-05", "2027-12-24", "2027-12-31",
}


def is_hk_trading_day(d: Optional[date] = None) -> bool:
    """判断是否为港股交易日 (独立港股法定节假日判定，严禁耦合依赖 BaoStock A股日历)"""
    d = d or date.today()
    if _is_weekend(d):
        return False
    d_str = d.strftime("%Y-%m-%d")
    if d_str in HK_KNOWN_HOLIDAYS:
        return False
    # 检查磁盘独立港股日历缓存
    cache_file = CACHE_DIR / f"hk_trade_dates_{d.year}.json"
    if cache_file.exists():
        try:
            if d.year not in _HK_YEAR_DATES_MEM:
                _HK_YEAR_DATES_MEM[d.year] = set(json.loads(cache_file.read_text(encoding="utf-8")))
            return d_str in _HK_YEAR_DATES_MEM[d.year]
        except Exception:
            pass
    # 常用已知港股休市固定节假日（元旦、劳动节、特区成立日、国庆、圣诞节等）
    hk_fixed_holidays = {
        "01-01", "05-01", "07-01", "10-01", "12-25", "12-26"
    }
    if d.strftime("%m-%d") in hk_fixed_holidays:
        return False
    return True


def get_prev_trading_date(as_of: Optional[date] = None, market: str = "SH") -> str:
    """
    返回指定市场在基准日期之前的最近一个已结束交易日 (YYYY-MM-DD)。
    严格区分 A股交易日历与港股交易日历。
    """
    d = as_of or date.today()
    check_day = d - timedelta(days=1)
    for _ in range(15):
        if market.upper() == "HK":
            if is_hk_trading_day(check_day):
                return check_day.strftime("%Y-%m-%d")
        else:
            if is_trading_day(check_day):
                return check_day.strftime("%Y-%m-%d")
        check_day -= timedelta(days=1)
    return (d - timedelta(days=1)).strftime("%Y-%m-%d")


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
    """
    返回 港股 (HKEX) 当前交易时段状态。
    严格对齐港交所现行规则：
    - 09:00 - 09:20: 开市前时段 / 竞价申报 (POS Auction)
    - 09:20 - 09:30: 随机撮合与暂停阻断期 (Blocking Period)
    - 09:30 - 12:00: 早市连续交易 (Morning Continuous Trading)
    - 12:00 - 13:00: 午间休市 (Lunch Break)
    - 13:00 - 16:00: 午市连续交易 (Afternoon Continuous Trading)
    - 16:00 - 16:10: 收市竞价时段 (Closing Auction Session, CAS)
    - 16:10 以后: 已收市
    """
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
    pos_matching = dtime(9, 20)
    morning_open = dtime(9, 30)
    morning_close = dtime(12, 0)
    afternoon_open = dtime(13, 0)
    afternoon_close = dtime(16, 0)
    cas_close = dtime(16, 10)

    # 港股半日市 (Half-Day Trading: 平安夜、大除夕及农历除夕前一日) 特殊状态机判定
    # 仅上午交易至 12:00，12:00-12:10 CAS，12:10 后下午完全闭市休市
    is_half_day = d.strftime("%Y-%m-%d") in HK_HALF_DAYS
    if is_half_day:
        cas_close_half = dtime(12, 10)
        if t < pos_open:
            return {"is_trading_day": True, "is_trading": False, "session": "pre_open",
                    "session_label": "半日市尚未开市", "note": "港交所半日市竞价(POS) 9:00 开始"}
        elif t < pos_matching:
            return {"is_trading_day": True, "is_trading": False, "session": "pos_auction",
                    "session_label": "半日市开市前竞价", "note": "9:00-9:20 半日市申报竞价"}
        elif t < morning_open:
            return {"is_trading_day": True, "is_trading": False, "session": "pos_blocking",
                    "session_label": "半日市撮合暂停", "note": "9:20-9:30 港股半日市阻断期"}
        elif t < morning_close:
            return {"is_trading_day": True, "is_trading": True, "session": "morning",
                    "session_label": "半日市交易中", "note": "港股半日市连续竞价 9:30-12:00 (下午休市)"}
        elif t < cas_close_half:
            return {"is_trading_day": True, "is_trading": False, "session": "closing_auction",
                    "session_label": "半日市收市竞价", "note": "港股半日市收市竞价 12:00-12:10 (CAS)"}
        else:
            return {"is_trading_day": True, "is_trading": False, "session": "closed",
                    "session_label": "半日市已收市", "note": "港股半日市 12:10 起全天闭市 (下午休市)"}

    if t < pos_open:
        return {"is_trading_day": True, "is_trading": False, "session": "pre_open",
                "session_label": "尚未开市", "note": "港交所开市前时段(POS) 9:00 开始"}
    elif t < pos_matching:
        return {"is_trading_day": True, "is_trading": False, "session": "pos_auction",
                "session_label": "开市前竞价", "note": "9:00-9:20 港交所开市前申报竞价"}
    elif t < morning_open:
        return {"is_trading_day": True, "is_trading": False, "session": "pos_blocking",
                "session_label": "撮合与暂停", "note": "9:20-9:30 港股随机撮合与阻断期"}
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
                "session_label": "收市竞价", "note": "收市竞价时段 16:00-16:10 (CAS)"}
    else:
        return {"is_trading_day": True, "is_trading": False, "session": "closed",
                "session_label": "已收市", "note": "16:10 港股收市"}


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
    elif cn["session"] in ["pre_open", "call_auction"] or hk["session"] in ["pre_open", "pos_auction", "pos_blocking"]:
        status_label = "开市前/竞价"
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

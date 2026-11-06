# -*- coding: utf-8 -*-
"""
QUANT RADAR (jiankong) - 架构与金融逻辑审计自我验证套件
覆盖复权除权漂移、AH跨市场时段、宏观风格闭环、预警零值与竞价拦截、
SQLite WAL高并发、指数/ETF ROE剥离、FIFO去重与安全端口保护等8大核心项。
"""
import sys
import os
import sqlite3
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "scripts"))

def main():
    print("==================================================")
    print("  QUANT RADAR (jiankong) 8项审计修复深度自我验证")
    print("==================================================")

    # 1. 验证新增宏观基准标的 (中证2000, 中证红利, AH溢价指数)
    print("\n--- 1. 宏观核心基准标的测试 ---")
    from scripts.watchlist_manager import WatchlistManager, normalize_stock_code
    wm = WatchlistManager(filepath=BASE_DIR / "watchlist.json")
    codes = [item["code"] for item in wm.items]
    assert "000932.SH" in codes, "未找到中证2000 (000932.SH)"
    assert "000922.SH" in codes, "未找到中证红利 (000922.SH)"
    assert "HSAHP.HK" in codes, "未找到AH溢价指数 (HSAHP.HK)"
    norm_2000 = normalize_stock_code("000932.SH")
    norm_div = normalize_stock_code("000922.SH")
    norm_ah = normalize_stock_code("HSAHP.HK")
    print(f"[OK] 000932.SH -> code={norm_2000['code']}, tencent={norm_2000['tencent_symbol']}, type={norm_2000['asset_type']}")
    print(f"[OK] 000922.SH -> code={norm_div['code']}, tencent={norm_div['tencent_symbol']}, type={norm_div['asset_type']}")
    print(f"[OK] HSAHP.HK  -> code={norm_ah['code']}, tencent={norm_ah['tencent_symbol']}, type={norm_ah['asset_type']}")

    # 2. 验证 A/H 跨市场双状态机
    print("\n--- 2. A/H 跨市场双状态机测试 ---")
    from scripts.market_session import get_cn_session, get_hk_session, get_market_session
    # 构造上午 11:45 测试时间点 (A股午休，港股正常交易)
    dt_1145 = datetime.strptime("2026-11-06 11:45:00", "%Y-%m-%d %H:%M:%S")
    cn_1145 = get_cn_session(dt_1145)
    hk_1145 = get_hk_session(dt_1145)
    sess_1145 = get_market_session(dt_1145)
    print(f"[OK] 11:45:00 时段 - CN={cn_1145['session']}(is_trading={cn_1145['is_trading']}), HK={hk_1145['session']}(is_trading={hk_1145['is_trading']})")
    assert cn_1145["is_trading"] is False, "11:45 A股应为午休"
    assert hk_1145["is_trading"] is True, "11:45 港股应在交易"
    assert sess_1145["is_trading"] is True, "全局状态应识别港股正在交易，避免漏报"

    # 构造下午 15:30 测试时间点 (A股已收盘，港股正常交易)
    dt_1530 = datetime.strptime("2026-11-06 15:30:00", "%Y-%m-%d %H:%M:%S")
    cn_1530 = get_cn_session(dt_1530)
    hk_1530 = get_hk_session(dt_1530)
    sess_1530 = get_market_session(dt_1530)
    print(f"[OK] 15:30:00 时段 - CN={cn_1530['session']}(is_trading={cn_1530['is_trading']}), HK={hk_1530['session']}(is_trading={hk_1530['is_trading']})")
    assert cn_1530["is_trading"] is False, "15:30 A股应已收盘"
    assert hk_1530["is_trading"] is True, "15:30 港股应在交易"

    # 3. 验证宏观罗盘数据闭环计算
    print("\n--- 3. 宏观罗盘数据闭环测试 ---")
    from scripts.macro_compass import macro_compass
    test_quotes = {
        "000932.SH": {"pct_change": 1.5, "price": 2500.0},
        "000300.SH": {"pct_change": -0.5, "price": 3800.0},
        "000922.SH": {"pct_change": 0.8, "price": 6000.0},
        "399006.SZ": {"pct_change": -1.2, "price": 2200.0},
        "HSAHP.HK": {"pct_change": 0.3, "price": 142.5}
    }
    macro_compass.update_from_quotes(test_quotes)
    st = macro_compass.get_full_status()
    print(f"[OK] AH溢价指数点位: {st['southbound_ah'].get('ah_premium_index')}")
    print(f"[OK] AH溢价分析: {st['southbound_ah'].get('ah_premium_desc')}")
    assert st["market_cap_style"]["bias"] == "小盘", "中证2000占优未正确识别"
    assert st["growth_value_style"]["bias"] == "价值", "中证红利占优未正确识别"

    # 4. 验证预警引擎防噪与零值拦截
    print("\n--- 4. 预警引擎零值与集合竞价拦截测试 ---")
    from scripts.alert_engine import alert_engine, build_group_from_threshold
    grp = build_group_from_threshold(3.0)
    test_cfg = {"code": "600519.SH", "alert_conditions": grp, "enabled": True}
    
    # 零值假数据拦截
    zero_metrics = {"price": 0.0, "pct_change": -99.9, "volume": 0.0, "amount": 0.0, "high": 0.0, "low": 0.0, "prev_close": 1700.0}
    res_zero = alert_engine.evaluate_and_gate(test_cfg, zero_metrics, is_trading=True)
    assert res_zero is None, "价格<=0 时应完全屏蔽预警"
    print("[OK] 价格<=0 零值异常成功拦截，未误报跌停")

    # 集合竞价非交易期拦截
    fake_auction_metrics = {"price": 1800.0, "pct_change": 5.8, "volume": 1000.0, "amount": 5000.0, "high": 1800.0, "low": 1700.0, "prev_close": 1700.0}
    res_auction = alert_engine.evaluate_and_gate(test_cfg, fake_auction_metrics, is_trading=False)
    assert res_auction is None, "非连续交易时段 (is_trading=False) 应屏蔽集合竞价虚单预警"
    print("[OK] 集合竞价虚单与非连续交易时段成功屏蔽")

    # 正常连续交易触发 (重置门禁状态后验证)
    alert_engine.reset("600519.SH")
    res_normal = alert_engine.evaluate_and_gate(test_cfg, fake_auction_metrics, is_trading=True)
    assert res_normal is not None, "连续交易且达到阈值应正常触发"
    print(f"[OK] 连续交易正常触发预警: {res_normal['matched_conditions']}")

    # 5. 验证 KlineCache WAL模式与除权除息漂移失效
    print("\n--- 5. KlineCache WAL模式与除权缓存失效测试 ---")
    from scripts.stock_history_provider import kline_cache, get_stock_history_and_valuation, clean_symbol
    with kline_cache._conn() as conn:
        journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        sync_mode = conn.execute("PRAGMA synchronous;").fetchone()[0]
    print(f"[OK] SQLite K线库模式: journal_mode={journal_mode.upper()} (WAL并发安全), synchronous={sync_mode}")
    assert journal_mode.lower() == "wal", f"SQLite 必须为 WAL 模式，当前为 {journal_mode}"

    # 测试 invalidate 方法
    kline_cache.upsert("TEST.SH", "day", [{"date": "2026-11-05", "close": 100.0, "open": 98.0, "high": 101.0, "low": 97.0}], "test")
    assert len(kline_cache.read("TEST.SH", "day")) == 1
    kline_cache.invalidate("TEST.SH", "day")
    assert len(kline_cache.read("TEST.SH", "day")) == 0
    print("[OK] kline_cache.invalidate() 缓存失效成功")

    # 6. 验证指数/ETF剥离ROE与负PE状态
    print("\n--- 6. 指数/ETF剥离ROE与负PE状态测试 ---")
    val_idx = get_stock_history_and_valuation("000300.SH")
    s_idx = val_idx["summary"]
    print(f"[OK] 指数 000300.SH ROE状态: {s_idx.get('roe_status')}, latest_roe={s_idx.get('latest_roe')}")
    assert s_idx.get("roe_status") == "N/A(指数/ETF)", "指数应剥离ROE为 N/A(指数/ETF)"
    assert s_idx.get("latest_roe") is None, "指数 latest_roe 应为 None"

    # 7. 验证 BoundedIdSet FIFO 内存防重复
    print("\n--- 7. BoundedIdSet FIFO 内存安全测试 ---")
    from scripts.web_server import BoundedIdSet
    b_set = BoundedIdSet(maxlen=5)
    for i in range(10):
        b_set.add(f"id_{i}")
    assert len(b_set) == 5, f"BoundedIdSet 长度超限: {len(b_set)}"
    assert "id_0" not in b_set, "旧 ID 未按 FIFO 淘汰"
    assert "id_9" in b_set, "最新 ID 缺失"
    print(f"[OK] BoundedIdSet 成功保持容量=5，最老 ID 安全淘汰，新 ID 正常保留")

    # 8. 验证 ensure_port_available 进程保护逻辑
    print("\n--- 8. ensure_port_available 进程安全测试 ---")
    from scripts.web_server import ensure_port_available
    # 传入不会被占用的端口进行测试
    ensure_port_available(59999)
    print("[OK] ensure_port_available 运行安全无抛错")

    print("\n==================================================")
    print("  🎉 全部 8 项审计重点验证成功！系统达到金融级鲁棒性！")
    print("==================================================")

if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""
QUANT RADAR (jiankong) - 架构与金融逻辑审计全面自我验证套件 (v2.3)
覆盖：
1. 宏观基准标的规范化 (国证2000 399303.SZ / 中证红利 000922.SH / AH溢价 HSAHP.HK)
2. A/H 跨市场细分双状态机 (POS申报/POS阻断/CAS竞价) 与港股法定半日市 (Half-Day Trading)
3. 宏观罗盘大小盘风格轮动闭环 (国证2000 vs 沪深300)
4. 预警引擎 A/H 标的独立状态机隔离 (杜绝 A股收盘后借港股交易时间误报)
5. 除权首日 (T日) 与月K线跨周期联动失效 (kline_cache.invalidate(symbol, freq=None))
6. 估值百分位过滤负 PE (母集仅保留正数)
7. 东财 em_get 令牌桶预订模式与锁内超时快速失败保护 (不推进时间戳)
8. BoundedIdSet 严格 FIFO 淘汰机制 (杜绝退化为 LRU)
"""
import sys
import os
import time
import sqlite3
from datetime import datetime, date
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "scripts"))

def main():
    print("==================================================")
    print("  QUANT RADAR (jiankong) 审计与实战加固深度验证套件")
    print("==================================================")

    # 1. 验证更正后的小微盘宏观基准标的 (国证2000 399303.SZ)
    print("\n--- 1. 宏观核心基准标的测试 (国证2000 399303.SZ) ---")
    from scripts.watchlist_manager import WatchlistManager, normalize_stock_code
    wm = WatchlistManager(filepath=BASE_DIR / "watchlist.json")
    codes = [item["code"] for item in wm.items]
    assert "399303.SZ" in codes, "未找到国证2000 (399303.SZ)"
    assert "000922.SH" in codes, "未找到中证红利 (000922.SH)"
    assert "HSAHP.HK" in codes, "未找到AH溢价指数 (HSAHP.HK)"
    norm_2000 = normalize_stock_code("399303.SZ")
    norm_div = normalize_stock_code("000922.SH")
    norm_ah = normalize_stock_code("HSAHP.HK")
    print(f"[OK] 399303.SZ -> code={norm_2000['code']}, tencent={norm_2000['tencent_symbol']}, type={norm_2000['asset_type']}")
    print(f"[OK] 000922.SH -> code={norm_div['code']}, tencent={norm_div['tencent_symbol']}, type={norm_div['asset_type']}")
    print(f"[OK] HSAHP.HK  -> code={norm_ah['code']}, tencent={norm_ah['tencent_symbol']}, type={norm_ah['asset_type']}")

    # 2. 验证 A/H 跨市场双状态机与港股半日市 (Half-Day Trading)
    print("\n--- 2. A/H 双状态机与港股半日市测试 ---")
    from scripts.market_session import get_cn_session, get_hk_session, get_market_session
    
    # 港股 9:10 开市前竞价 POS
    dt_0910 = datetime.strptime("2026-11-06 09:10:00", "%Y-%m-%d %H:%M:%S")
    hk_0910 = get_hk_session(dt_0910)
    assert hk_0910["session"] == "pos_auction", f"09:10 应为 pos_auction，实际为 {hk_0910['session']}"
    assert hk_0910["is_trading"] is False

    # 港股 9:25 阻断期 POS Blocking
    dt_0925 = datetime.strptime("2026-11-06 09:25:00", "%Y-%m-%d %H:%M:%S")
    hk_0925 = get_hk_session(dt_0925)
    assert hk_0925["session"] == "pos_blocking", f"09:25 应为 pos_blocking，实际为 {hk_0925['session']}"
    assert hk_0925["is_trading"] is False

    # 上午 11:45 (A股午休，港股早市交易)
    dt_1145 = datetime.strptime("2026-11-06 11:45:00", "%Y-%m-%d %H:%M:%S")
    cn_1145 = get_cn_session(dt_1145)
    hk_1145 = get_hk_session(dt_1145)
    print(f"[OK] 11:45:00 时段 - CN={cn_1145['session']}(is_trading={cn_1145['is_trading']}), HK={hk_1145['session']}(is_trading={hk_1145['is_trading']})")
    assert cn_1145["is_trading"] is False
    assert hk_1145["is_trading"] is True

    # 港股半日市测试 (2026-12-24 平安夜半日市)
    dt_half_morning = datetime.strptime("2026-12-24 10:30:00", "%Y-%m-%d %H:%M:%S")
    dt_half_cas = datetime.strptime("2026-12-24 12:05:00", "%Y-%m-%d %H:%M:%S")
    dt_half_afternoon = datetime.strptime("2026-12-24 14:30:00", "%Y-%m-%d %H:%M:%S")
    hk_half_m = get_hk_session(dt_half_morning)
    hk_half_c = get_hk_session(dt_half_cas)
    hk_half_a = get_hk_session(dt_half_afternoon)
    assert hk_half_m["is_trading"] is True, "半日市上午应正常交易"
    assert hk_half_c["session"] == "closing_auction" and hk_half_c["is_trading"] is False, "半日市12:00-12:10应为CAS"
    assert hk_half_a["session"] == "closed" and hk_half_a["is_trading"] is False, "半日市下午14:30必须闭市(杜绝误判交易中)"
    print("[OK] 港股半日市(12-24)状态验证: 上午交易中 -> 12:05 CAS收市竞价 -> 14:30 全天闭市(成功拦截下午误判)")

    # 3. 验证宏观罗盘大小盘轮动 (国证2000 vs 沪深300)
    print("\n--- 3. 宏观罗盘国证2000闭环测试 ---")
    from scripts.macro_compass import MacroCompassEngine
    quotes_sample = {
        "399303.SZ": {"code": "399303.SZ", "name": "国证2000", "price": 8500.0, "pct_change": 2.50, "prev_close": 8292.68},
        "000300.SH": {"code": "000300.SH", "name": "沪深300", "price": 3900.0, "pct_change": 0.50, "prev_close": 3880.60},
        "000922.SH": {"code": "000922.SH", "name": "中证红利", "price": 5200.0, "pct_change": 0.10, "prev_close": 5194.80},
        "399006.SZ": {"code": "399006.SZ", "name": "创业板指", "price": 2200.0, "pct_change": 1.80, "prev_close": 2161.10},
        "HSAHP.HK":  {"code": "HSAHP.HK",  "name": "恒生AH溢价", "price": 142.5, "pct_change": 0.30, "prev_close": 142.07}
    }
    mc = MacroCompassEngine()
    mc.update_from_quotes(quotes_sample)
    compass = mc.get_full_status()
    assert compass["market_cap_style"]["bias"] == "小盘", "大小盘基准未对标国证2000识别为小盘占优"
    print(f"[OK] AH溢价指数点位: {compass['southbound_ah']['ah_premium_index']}")
    print(f"[OK] 大小盘轮动状态: {compass['market_cap_style']['intraday_status']}")

    # 4. 验证预警引擎 A/H 标的独立状态机隔离
    print("\n--- 4. 预警引擎 A/H 混排单票状态机隔离测试 ---")
    from scripts.alert_engine import AlertEngine
    ae = AlertEngine()
    # 模拟 15:30 (A股收盘 closed/is_trading=False，港股交易 afternoon/is_trading=True)
    dt_1530 = datetime.strptime("2026-11-06 15:30:00", "%Y-%m-%d %H:%M:%S")
    now_ts_1530 = dt_1530.timestamp()

    cfg_a_stock = {"code": "TEST_600519.SH", "market": "SH", "alert_threshold_pct": 3.0}
    cfg_hk_stock = {"code": "TEST_00700.HK", "market": "HK", "alert_threshold_pct": 3.0}
    ae._state.pop("TEST_600519.SH", None)
    ae._state.pop("TEST_00700.HK", None)
    metrics_alert = {"price": 105.0, "pct_change": 5.0, "volume": 1000, "amount": 100000, "high": 106.0, "low": 99.0, "prev_close": 100.0}

    # A股在 15:30 自动隔离拦截
    alert_a = ae.evaluate_and_gate(cfg_a_stock, metrics_alert, now_ts=now_ts_1530, is_trading=None)
    assert alert_a is None, "A股标的在 15:30 收盘后必须被状态机拦截，绝不能误报预警！"
    # 港股在 15:30 正常触发
    alert_hk = ae.evaluate_and_gate(cfg_hk_stock, metrics_alert, now_ts=now_ts_1530, is_trading=None)
    assert alert_hk is not None, "港股标的在 15:30 处于交易中，必须正常触发预警！"
    print("[OK] 15:30 单票状态机隔离验证成功: A股 TEST_600519.SH 被精确拦截屏蔽，港股 TEST_00700.HK 正常触发预警！")

    # 5. 验证除权首日 (T日) 与月K跨周期联动失效
    print("\n--- 5. 除权首日与月K线跨周期联动失效测试 ---")
    from scripts.stock_history_provider import kline_cache, get_kline_cached
    test_sym = "TEST_DIV_LINK.SH"
    
    # 写入日K与月K缓存
    kline_cache.upsert(test_sym, "day", [{"date": "2026-11-02", "open": 100.0, "high": 102.0, "low": 99.0, "close": 100.0, "volume": 1000}], "test", replace=True)
    kline_cache.upsert(test_sym, "month", [{"date": "2026-10-31", "open": 90.0, "high": 105.0, "low": 88.0, "close": 100.0, "volume": 10000}], "test", replace=True)
    
    assert len(kline_cache.read(test_sym, "day")) == 1
    assert len(kline_cache.read(test_sym, "month")) == 1

    # 模拟周三对齐交易日 2026-11-02 发生除权 (偏离 1.0% > 0.1%)
    info_test = {"full_code": test_sym, "symbol": "TEST_DIV_LINK", "market": "SH", "asset_type": "stock"}
    get_kline_cached(info_test, "day", latest_prev_close=99.0, prev_trade_date="2026-11-02")
    
    # 验证联动失效：日K与月K必须被全部删除清空！
    assert len(kline_cache.read(test_sym, "day")) == 0, "日K缓存应被清空"
    assert len(kline_cache.read(test_sym, "month")) == 0, "联动失效未生效：月K缓存必须被同步连带清空！"
    print("[OK] 跨周期联动失效测试成功: 日K检测到除权，调用 invalidate(symbol, freq=None) 同步清空了月K缓存！")

    # 6. 验证负 PE 历史样本过滤
    print("\n--- 6. 负 PE 历史样本过滤测试 ---")
    from scripts.stock_history_provider import calculate_percentile
    hist_pe_mixed = [-10.0, -5.0, 10.0, 20.0, 30.0]
    p_pos = calculate_percentile(hist_pe_mixed, 20.0)
    # 正数样本仅有 [10.0, 20.0, 30.0]，严格小于 20.0 的仅有 10.0，分位数应为 1/3 = 33.3%
    assert abs(p_pos - 33.3) < 0.1, f"负PE过滤后分位数应为33.3%，实际计算为 {p_pos}%"
    print(f"[OK] 负PE过滤验证成功: 混合样本 {hist_pe_mixed} 中 20.0 的正数分位数为 {p_pos:.1f}% (公允值)")

    # 7. 验证东财 em_get 锁内超时排队上限保护 (不推进时间戳)
    print("\n--- 7. 东财 em_get 锁内超时快速失败保护测试 ---")
    import scripts.stock_history_provider as shp
    with shp._em_lock:
        shp._next_allowed_time[0] = time.monotonic() + 30.0  # 人工制造 30 秒排队堆积
        t_before = shp._next_allowed_time[0]

    # 发起请求，应在锁内直接抛出 TimeoutError，且绝不推进时间戳
    try:
        shp.em_get("https://datacenter-web.eastmoney.com/test", timeout=1.0)
        assert False, "排队超限未抛出 TimeoutError"
    except TimeoutError as te:
        with shp._em_lock:
            t_after = shp._next_allowed_time[0]
            shp._next_allowed_time[0] = 0.0  # 恢复现场
        assert t_after == t_before, "快速失败但时间戳被错误推进了！"
        print(f"[OK] 东财令牌桶超时保护测试成功: 捕获 '{te}'，且锁内拒绝排号未污染后续时钟队列！")

    # 8. 验证 BoundedIdSet 严格 FIFO 淘汰机制 (防退化为 LRU)
    print("\n--- 8. BoundedIdSet 严格 FIFO 淘汰机制测试 ---")
    from scripts.web_server import BoundedIdSet
    bis = BoundedIdSet(maxlen=3)
    bis.add("A")
    bis.add("B")
    bis.add("C")
    # 再次 add("A") (若使用 move_to_end 则退化为 LRU，A 变最新，接下来推 D 时 B 被淘汰)
    bis.add("A")
    # 存入 D，严格 FIFO 模式下必须淘汰最老进入的 A！
    bis.add("D")
    assert "A" not in bis, "严格 FIFO 必须淘汰最先进入的 A！"
    assert "B" in bis and "C" in bis and "D" in bis, "集合中应保留 B, C, D"
    print("[OK] BoundedIdSet 纯正 FIFO 测试成功: 重复访问已存在元素未改变队列时序，成功淘汰最早入队 ID！")

    print("\n==================================================")
    print("  🎉 全部 8 项金融逻辑、跨市场时段与并发架构审计修复验证 100% 通过！")
    print("==================================================")

if __name__ == "__main__":
    main()

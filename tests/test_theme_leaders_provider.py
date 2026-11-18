# -*- coding: utf-8 -*-
"""
theme_leaders_provider (问财热点二级内容·板块头部代表公司) 单元测试
重点覆盖：
1. 成分股查询问句拼装规则 (「概念」后缀不重复追加)
2. 「纳入概念原因」简介提取优先级 (【板块名】条目优先, 无则首条, 缺失为空不编造)
3. 归属校验 (所属概念不含目标板块名的行必须剔除; 全部不符必须显式报错)
4. 头部代表公司按总市值降序取前 3, 走势线近7日窗口与区间涨跌幅
5. 当日缓存命中不再打问财接口; refresh=True 强制重算
6. 走势取数失败 → trend=None + trend_error 真实原因 (禁止静默补线)
"""
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "scripts"))

from scripts import theme_leaders_provider as tlp
from scripts.iwencai_provider import IwencaiProviderError


def _row(code, name, cap, concepts, reasons, price="10.00", pct=1.5):
    return {
        "股票代码": code,
        "股票简称": name,
        "最新价": price,
        "最新涨跌幅": pct,
        "所属概念": concepts,
        "纳入概念原因": reasons,
        "总市值[20261118]": cap,
        "所属同花顺行业": ["电子", "半导体"],
    }


def _fake_bars(n=7):
    return [
        {"date": f"2026-11-{10 + i:02d}", "open": 1.0, "close": 10.0 + i, "high": 1.0, "low": 1.0, "volume": 1.0}
        for i in range(n)
    ]


@pytest.fixture()
def cache_file(tmp_path, monkeypatch):
    f = tmp_path / "theme_board_leaders_cache.json"
    monkeypatch.setattr(tlp, "CACHE_FILE", f)
    return f


# ---------- 纯逻辑 ----------

def test_build_constituent_query_suffix_rule():
    assert tlp.build_constituent_query("算力租赁") == "算力租赁概念成分股按总市值排名"
    assert tlp.build_constituent_query("芯片概念") == "芯片概念成分股按总市值排名"
    assert tlp.build_constituent_query("共封装光学(CPO)") == "共封装光学(CPO)概念成分股按总市值排名"
    with pytest.raises(IwencaiProviderError):
        tlp.build_constituent_query("  ")


def test_pick_intro_prefers_board_prefix():
    row = {"纳入概念原因": ["【算力租赁】A公告内容", "【其他概念】B内容"]}
    assert tlp._pick_intro(row, "算力租赁") == "【算力租赁】A公告内容"
    row2 = {"纳入概念原因": ["【其他概念】B内容"]}
    assert tlp._pick_intro(row2, "算力租赁") == "【其他概念】B内容"
    assert tlp._pick_intro({}, "算力租赁") == ""  # 缺失为空, 绝不编造


def test_is_board_member_verification():
    ok = {"所属概念": ["算力租赁", "云计算"]}
    assert tlp._is_board_member(ok, "算力租赁") is True
    assert tlp._is_board_member(ok, "光刻胶") is False
    assert tlp._is_board_member({"所属概念": "['算力租赁', 'x']"}, "算力租赁") is True
    assert tlp._is_board_member({}, "算力租赁") is False


# ---------- 组装与缓存 ----------

def test_fetch_board_leaders_top3_by_market_cap(cache_file, monkeypatch):
    calls = {"q": 0, "k": 0}

    def fake_query2data(query, limit=400, page="1", timeout=30):
        calls["q"] += 1
        assert query == "算力租赁概念成分股按总市值排名"
        return {
            "trace_id": "abc123",
            "datas": [
                _row("300001.SZ", "小市值", 1e9, ["算力租赁"], ["【算力租赁】小市值原因"]),
                _row("600941.SH", "中国移动", 1.5e12, ["算力租赁", "5G"], ["【算力租赁】移动原因"]),
                _row("601138.SH", "工业富联", 7.5e11, ["算力租赁", "CPO"], ["【算力租赁】富联原因"]),
                _row("600050.SH", "中国联通", 1.4e11, ["算力租赁"], ["【算力租赁】联通原因"]),
                _row("600000.SH", "串板银行", 9e12, ["银行"], ["【银行】不该出现"]),
            ],
        }

    def fake_fetch_day_kline(tencent_sym, count=640):
        calls["k"] += 1
        return _fake_bars()

    monkeypatch.setattr(tlp, "query2data", fake_query2data)
    monkeypatch.setattr(tlp, "fetch_day_kline", fake_fetch_day_kline)

    res = tlp.fetch_board_leaders("886050.TI", "算力租赁")
    assert calls["q"] == 1
    assert res["board"] == {"code": "886050.TI", "name": "算力租赁"}
    leaders = res["leaders"]
    assert [l["code"] for l in leaders] == ["600941.SH", "601138.SH", "600050.SH"]  # 市值降序前3
    assert all(l["code"] != "600000.SH" for l in leaders)  # 归属校验剔除串板行
    assert all(l["code"] != "300001.SZ" for l in leaders)  # 第4名不入选

    top = leaders[0]
    assert top["intro"] == "【算力租赁】移动原因"
    assert top["industry"] == ["电子", "半导体"]
    assert top["trend"]["dates"][0] == "2026-11-10"
    assert len(top["trend"]["closes"]) == 7
    # close 10..16 → (16/10-1)*100 = 60.0
    assert top["trend"]["pct_7d"] == 60.0
    assert top["trend_error"] is None
    assert calls["k"] == 3

    # 当日缓存命中: 不再打问财/行情
    res2 = tlp.fetch_board_leaders("886050.TI", "算力租赁")
    assert calls["q"] == 1 and calls["k"] == 3
    assert res2["leaders"][0]["code"] == "600941.SH"

    # refresh=True 强制重算
    tlp.fetch_board_leaders("886050.TI", "算力租赁", refresh=True)
    assert calls["q"] == 2


def test_fetch_board_leaders_membership_rejected(cache_file, monkeypatch):
    monkeypatch.setattr(
        tlp, "query2data",
        lambda query, limit=400, page="1", timeout=30: {
            "trace_id": "t",
            "datas": [_row("600000.SH", "浦发银行", 1e12, ["银行"], ["【银行】x"])],
        },
    )
    with pytest.raises(IwencaiProviderError, match="归属校验失败"):
        tlp.fetch_board_leaders("886050.TI", "算力租赁")


def test_fetch_board_leaders_empty_dataset(cache_file, monkeypatch):
    monkeypatch.setattr(
        tlp, "query2data",
        lambda query, limit=400, page="1", timeout=30: {"trace_id": "t", "datas": []},
    )
    with pytest.raises(IwencaiProviderError, match="空数据集"):
        tlp.fetch_board_leaders("886050.TI", "算力租赁")


def test_trend_failure_is_explicit_not_fabricated(cache_file, monkeypatch):
    monkeypatch.setattr(
        tlp, "query2data",
        lambda query, limit=400, page="1", timeout=30: {
            "trace_id": "t",
            "datas": [_row("600941.SH", "中国移动", 1e12, ["算力租赁"], ["【算力租赁】x"])],
        },
    )

    def boom(tencent_sym, count=640):
        raise RuntimeError("腾讯行情超时")

    monkeypatch.setattr(tlp, "fetch_day_kline", boom)
    res = tlp.fetch_board_leaders("886050.TI", "算力租赁")
    ld = res["leaders"][0]
    assert ld["trend"] is None
    assert "腾讯日K获取失败" in ld["trend_error"]
    assert "腾讯行情超时" in ld["trend_error"]  # 真实原因透传


def test_trend_window_trims_to_7(cache_file, monkeypatch):
    monkeypatch.setattr(
        tlp, "query2data",
        lambda query, limit=400, page="1", timeout=30: {
            "trace_id": "t",
            "datas": [_row("600941.SH", "中国移动", 1e12, ["算力租赁"], ["【算力租赁】x"])],
        },
    )
    monkeypatch.setattr(tlp, "fetch_day_kline", lambda tencent_sym, count=640: _fake_bars(10))
    res = tlp.fetch_board_leaders("886050.TI", "算力租赁")
    trend = res["leaders"][0]["trend"]
    assert len(trend["dates"]) == 7
    assert trend["dates"][-1] == "2026-11-19"  # 只保留窗口末7个交易日

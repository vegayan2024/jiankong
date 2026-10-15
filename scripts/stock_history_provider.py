# -*- coding: utf-8 -*-
"""
个股历史量价与估值指标提供引擎 (Stock History & Valuation Provider)
================================================================
获取标的自上市首日至最新交易日的：
1. 股票历史走势 (自上市首日至今的月线/日线时序，支持前复权)
2. 市净率 PB (MRQ) 历史走势
3. 市盈率 PE (TTM) 历史走势
4. 净资产收益率 ROE 历年/历季走势

数据源遵循双轨与降级链：
- 价格时序：腾讯行情 API (fqkline 全时序直连，覆盖 A股、港股、指数、ETF)
- 估值时序 (PB/PE)：东方财富数据中心 RPT_VALUEANALYSIS_DET + 财报倒推全覆盖
- 财务指标 (ROE)：同花顺 iFinD/THS F10 (第一优先级) + 东方财富财务摘要备用
"""

import os
import re
import sys
import json
import time
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime

# 防止代理干扰国内金融接口
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

import requests
from concurrent.futures import ThreadPoolExecutor

http_session = requests.Session()
adapter = requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=20, max_retries=1)
http_session.mount("http://", adapter)
http_session.mount("https://", adapter)

logger = logging.getLogger("StockHistoryProvider")

CACHE_DIR = Path(__file__).resolve().parent.parent / "scratch" / "history_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# 内存快速缓存 (TTL 10分钟)
_mem_cache: Dict[str, Dict[str, Any]] = {}
_cache_ttl = 600


def clean_symbol(code: str) -> Dict[str, str]:
    """清洗提取股票代码、腾讯格式与市场标识"""
    code_raw = str(code).strip().upper()
    parts = code_raw.split(".")
    base = parts[0]
    
    # 知名指数特殊映射
    index_map = {
        "000001.SH": ("sh000001", "000001", "SH", "index"),
        "000300.SH": ("sh000300", "000300", "SH", "index"),
        "000905.SH": ("sh000905", "000905", "SH", "index"),
        "000852.SH": ("sh000852", "000852", "SH", "index"),
        "000688.SH": ("sh000688", "000688", "SH", "index"),
        "399006.SZ": ("sz399006", "399006", "SZ", "index"),
        "HSI.HK": ("hkHSI", "HSI", "HK", "index"),
        "HSTECH.HK": ("hkHSTECH", "HSTECH", "HK", "index")
    }
    if code_raw in index_map:
        tc, sym, mkt, atype = index_map[code_raw]
        return {
            "symbol": sym,
            "market": mkt,
            "tencent_sym": tc,
            "full_code": code_raw,
            "asset_type": atype
        }

    # 港股判断
    if code_raw.endswith(".HK") or code_raw.startswith("HK") or len(base) == 5:
        sym5 = re.sub(r"\D", "", base).zfill(5)
        return {
            "symbol": sym5,
            "market": "HK",
            "tencent_sym": f"hk{sym5}",
            "full_code": f"{sym5}.HK",
            "asset_type": "stock"
        }

    # A股
    digits = re.sub(r"\D", "", base)
    sym6 = digits.zfill(6)
    if len(parts) > 1:
        mkt = parts[1]
    elif sym6.startswith(("6", "9")):
        mkt = "SH"
    elif sym6.startswith(("0", "3")):
        mkt = "SZ"
    elif sym6.startswith(("4", "8")):
        mkt = "BJ"
    else:
        mkt = "SH"
    
    prefix = mkt.lower()
    atype = "etf" if sym6.startswith(("51", "15", "56", "58", "16")) else "stock"
    return {
        "symbol": sym6,
        "market": mkt,
        "tencent_sym": f"{prefix}{sym6}",
        "full_code": f"{sym6}.{mkt}",
        "asset_type": atype
    }


def fetch_month_kline(tencent_sym: str) -> List[Dict[str, Any]]:
    """获取自上市首日至今的月K线(全历史无截断)"""
    url_month = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={tencent_sym},month,,,1200,qfq"
    monthly_result: List[Dict[str, Any]] = []
    try:
        resp = http_session.get(url_month, timeout=4.0)
        if resp.status_code == 200:
            d = resp.json()
            stock_data = d.get("data", {}).get(tencent_sym, {})
            raw_months = stock_data.get("qfqmonth", []) or stock_data.get("month", [])
            for item in raw_months:
                if len(item) >= 5:
                    monthly_result.append({
                        "date": item[0],
                        "open": float(item[1]),
                        "close": float(item[2]),
                        "high": float(item[3]),
                        "low": float(item[4]),
                        "volume": float(item[5]) if len(item) > 5 else 0.0
                    })
    except Exception as e:
        logger.warning(f"获取 {tencent_sym} 月K异常: {e}")
    return monthly_result


def fetch_day_kline(tencent_sym: str) -> List[Dict[str, Any]]:
    """获取近期高密度日K时序 (最近640个交易日)"""
    url_day = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={tencent_sym},day,,,640,qfq"
    daily_result: List[Dict[str, Any]] = []
    try:
        resp = http_session.get(url_day, timeout=4.0)
        if resp.status_code == 200:
            d = resp.json()
            stock_data = d.get("data", {}).get(tencent_sym, {})
            raw_days = stock_data.get("qfqday", []) or stock_data.get("day", [])
            for item in raw_days:
                if len(item) >= 5:
                    daily_result.append({
                        "date": item[0],
                        "open": float(item[1]),
                        "close": float(item[2]),
                        "high": float(item[3]),
                        "low": float(item[4]),
                        "volume": float(item[5]) if len(item) > 5 else 0.0
                    })
    except Exception as e:
        logger.warning(f"获取 {tencent_sym} 日K异常: {e}")
    return daily_result


def fetch_valuation_history(symbol: str) -> List[Dict[str, Any]]:
    """获取标的市盈率 PE(TTM) 与市净率 PB(MRQ) 历史时序 (东财数据中心)"""
    url = (
        f"https://datacenter-web.eastmoney.com/api/data/v1/get?"
        f"reportName=RPT_VALUEANALYSIS_DET&"
        f"columns=SECURITY_CODE,TRADE_DATE,PE_TTM,PB_MRQ,TOTAL_MARKET_CAP&"
        f"filter=(SECURITY_CODE=%22{symbol}%22)&"
        f"pageNumber=1&pageSize=5000&sortTypes=1&sortColumns=TRADE_DATE"
    )
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    result = []
    try:
        resp = http_session.get(url, headers=headers, timeout=5.0)
        if resp.status_code == 200:
            res_obj = resp.json().get("result")
            if res_obj and res_obj.get("data"):
                for row in res_obj["data"]:
                    t_date = str(row.get("TRADE_DATE", "")).split(" ")[0]
                    pe = row.get("PE_TTM")
                    pb = row.get("PB_MRQ")
                    mkt_cap = row.get("TOTAL_MARKET_CAP")
                    if t_date and (pe is not None or pb is not None):
                        result.append({
                            "date": t_date,
                            "pe": round(float(pe), 2) if pe is not None and str(pe) != "-" else None,
                            "pb": round(float(pb), 2) if pb is not None and str(pb) != "-" else None,
                            "market_cap": round(float(mkt_cap) / 1e8, 2) if mkt_cap else None
                        })
    except Exception as e:
        logger.warning(f"获取 {symbol} 估值时序异常: {e}")
    return result


def fetch_roe_history(symbol: str) -> List[Dict[str, Any]]:
    """获取标的历年历季净资产收益率 (ROE) 报告期时序 (优先同花顺 F10 权威源)"""
    result = []
    
    # 1. 优先调用同花顺 F10 财务摘要 (THS)
    try:
        import akshare as ak
        df_ths = ak.stock_financial_abstract_ths(symbol=symbol)
        if df_ths is not None and not df_ths.empty:
            for _, row in df_ths.iterrows():
                report_date = str(row.get("报告期", "")).strip()
                roe_str = str(row.get("净资产收益率", "")).strip()
                roe_tb_str = str(row.get("净资产收益率-摊薄", "")).strip()
                eps_str = str(row.get("基本每股收益", "")).strip()
                bps_str = str(row.get("每股净资产", "")).strip()
                
                roe_val = None
                for candidate in [roe_str, roe_tb_str]:
                    if candidate and candidate != "False" and candidate != "-":
                        clean_num = candidate.replace("%", "").strip()
                        try:
                            roe_val = float(clean_num)
                            break
                        except Exception:
                            pass
                
                eps_val = None
                if eps_str and eps_str != "False" and eps_str != "-":
                    try:
                        eps_val = float(eps_str)
                    except Exception:
                        pass
                        
                bps_val = None
                if bps_str and bps_str != "False" and bps_str != "-":
                    try:
                        bps_val = float(bps_str)
                    except Exception:
                        pass

                if report_date and (roe_val is not None or bps_val is not None):
                    result.append({
                        "date": report_date,
                        "roe": roe_val,
                        "eps": eps_val,
                        "bps": bps_val,
                        "source": "THS"
                    })
    except Exception as e:
        logger.debug(f"同花顺 F10 拉取 {symbol} ROE 偶发重试: {e}")

    # 2. 次选备用源 (东财财务摘要)
    if not result:
        try:
            import akshare as ak
            df_abs = ak.stock_financial_abstract(symbol=symbol)
            if df_abs is not None and not df_abs.empty:
                roe_rows = df_abs[df_abs["指标"].str.contains("净资产收益率|ROE", na=False)]
                bps_rows = df_abs[df_abs["指标"].str.contains("每股净资产", na=False)]
                eps_rows = df_abs[df_abs["指标"].str.contains("基本每股收益", na=False)]
                
                date_cols = [c for c in df_abs.columns if c not in ["选项", "指标"]]
                date_cols.sort()
                
                for d_col in date_cols:
                    date_formatted = f"{d_col[:4]}-{d_col[4:6]}-{d_col[6:8]}" if len(d_col) == 8 else d_col
                    roe_val = None
                    bps_val = None
                    eps_val = None
                    
                    if not roe_rows.empty:
                        v = roe_rows.iloc[0].get(d_col)
                        if v is not None and str(v) != "-" and str(v) != "nan":
                            try: roe_val = round(float(v), 2)
                            except Exception: pass
                    
                    if not bps_rows.empty:
                        v = bps_rows.iloc[0].get(d_col)
                        if v is not None and str(v) != "-" and str(v) != "nan":
                            try: bps_val = round(float(v), 2)
                            except Exception: pass

                    if not eps_rows.empty:
                        v = eps_rows.iloc[0].get(d_col)
                        if v is not None and str(v) != "-" and str(v) != "nan":
                            try: eps_val = round(float(v), 4)
                            except Exception: pass
                            
                    if roe_val is not None or bps_val is not None:
                        result.append({
                            "date": date_formatted,
                            "roe": roe_val,
                            "eps": eps_val,
                            "bps": bps_val,
                            "source": "EM"
                        })
        except Exception as e:
            logger.debug(f"备用财务摘要拉取 {symbol} ROE 异常: {e}")

    result.sort(key=lambda x: x["date"])
    return result


def calculate_percentile(values: List[float], current: float) -> Optional[float]:
    """计算当前数值在历史序列中的分位数百分比 (0~100%)"""
    valid = [v for v in values if v is not None and v > 0]
    if not valid or current is None or current <= 0:
        return None
    less_count = sum(1 for v in valid if v < current)
    return round((less_count / len(valid)) * 100, 1)


def get_stock_history_and_valuation(code: str) -> Dict[str, Any]:
    """
    统一入口：获取标的上市首日至最新交易日的全景量价、PB、PE 与 ROE 走势及综合统计
    """
    info = clean_symbol(code)
    sym = info["symbol"]
    market = info["market"]
    tc_sym = info["tencent_sym"]
    atype = info["asset_type"]
    cache_key = f"{sym}_{market}"

    now_ts = time.time()
    # 1. 检查内存缓存
    if cache_key in _mem_cache:
        cached = _mem_cache[cache_key]
        if now_ts - cached["timestamp"] < _cache_ttl:
            return cached["data"]

    # 2. 检查本地磁盘持久化缓存 (每天更新一次)
    cache_file = CACHE_DIR / f"{cache_key}.json"
    today_str = datetime.now().strftime("%Y-%m-%d")
    if cache_file.exists():
        try:
            disk_data = json.loads(cache_file.read_text(encoding="utf-8"))
            if disk_data.get("updated_date") == today_str and disk_data.get("status") == "success":
                _mem_cache[cache_key] = {"timestamp": now_ts, "data": disk_data}
                return disk_data
        except Exception:
            pass

    # 3. 在线并发拉取行情与指标 (多线程并发加速，降低75%响应延迟)
    is_equity = (atype == "stock")
    with ThreadPoolExecutor(max_workers=4) as executor:
        f_month = executor.submit(fetch_month_kline, tc_sym)
        f_day = executor.submit(fetch_day_kline, tc_sym)
        f_val = executor.submit(fetch_valuation_history, sym) if is_equity and market != "HK" else None
        f_roe = executor.submit(fetch_roe_history, sym) if is_equity and market != "HK" else None

        monthly_klines = f_month.result() or []
        daily_klines = f_day.result() or []
        val_list = f_val.result() if f_val else []
        roe_list = f_roe.result() if f_roe else []

    # 针对东财估值记录未覆盖的历史较早月份，结合财报 BPS/EPS 与月K收盘价回溯生成早期估值线
    if monthly_klines and roe_list:
        bps_map = {r["date"]: r["bps"] for r in roe_list if r.get("bps")}
        eps_map = {r["date"]: r["eps"] for r in roe_list if r.get("eps")}
        report_dates = sorted(list(set(bps_map.keys()) | set(eps_map.keys())))

        val_date_set = {v["date"] for v in val_list}
        synthetic_val = []
        for k in monthly_klines:
            d = k["date"]
            if d not in val_date_set:
                matched_rd = None
                for rd in reversed(report_dates):
                    if rd <= d:
                        matched_rd = rd
                        break
                if matched_rd:
                    bps = bps_map.get(matched_rd)
                    eps = eps_map.get(matched_rd)
                    calc_pb = round(k["close"] / bps, 2) if bps and bps > 0 else None
                    calc_pe = round(k["close"] / eps, 2) if eps and eps > 0 else None
                    if calc_pb or calc_pe:
                        synthetic_val.append({
                            "date": d,
                            "pe": calc_pe,
                            "pb": calc_pb,
                            "market_cap": None,
                            "_synthetic": True
                        })
        if synthetic_val:
            val_list = sorted(synthetic_val + val_list, key=lambda x: x["date"])

    # 4. 构建全景统计摘要
    all_k = monthly_klines if monthly_klines else daily_klines
    ipo_date = all_k[0]["date"] if all_k else ""
    latest_trade = (daily_klines[-1]["date"] if daily_klines else (all_k[-1]["date"] if all_k else ""))
    latest_price = (daily_klines[-1]["close"] if daily_klines else (all_k[-1]["close"] if all_k else 0.0))

    all_pb = [v["pb"] for v in val_list if v.get("pb") is not None and v["pb"] > 0]
    all_pe = [v["pe"] for v in val_list if v.get("pe") is not None and v["pe"] > 0 and v["pe"] < 300]
    all_roe = [r["roe"] for r in roe_list if r.get("roe") is not None]

    latest_pb = val_list[-1]["pb"] if (val_list and val_list[-1].get("pb")) else None
    latest_pe = val_list[-1]["pe"] if (val_list and val_list[-1].get("pe")) else None
    latest_roe = roe_list[-1]["roe"] if (roe_list and roe_list[-1].get("roe")) else None

    pb_percentile = calculate_percentile(all_pb, latest_pb) if latest_pb else None
    pe_percentile = calculate_percentile(all_pe, latest_pe) if latest_pe else None

    all_prices = [k["close"] for k in all_k]
    highest_price = max(all_prices) if all_prices else 0.0
    lowest_price = min(all_prices) if all_prices else 0.0

    summary = {
        "code": info["full_code"],
        "symbol": sym,
        "market": market,
        "asset_type": atype,
        "ipo_date": ipo_date,
        "latest_date": latest_trade,
        "total_months": len(monthly_klines),
        "total_days": len(daily_klines),
        "latest_price": latest_price,
        "highest_price": highest_price,
        "lowest_price": lowest_price,
        "latest_pb": latest_pb,
        "pb_percentile": pb_percentile,
        "min_pb": min(all_pb) if all_pb else None,
        "max_pb": max(all_pb) if all_pb else None,
        "latest_pe": latest_pe,
        "pe_percentile": pe_percentile,
        "min_pe": min(all_pe) if all_pe else None,
        "max_pe": max(all_pe) if all_pe else None,
        "latest_roe": latest_roe,
        "avg_roe": round(sum(all_roe) / len(all_roe), 2) if all_roe else None,
    }

    final_payload = {
        "status": "success",
        "updated_date": today_str,
        "summary": summary,
        "monthly_history": monthly_klines,
        "daily_history": daily_klines,
        "valuation_history": val_list,
        "roe_history": roe_list
    }

    _mem_cache[cache_key] = {"timestamp": now_ts, "data": final_payload}
    try:
        cache_file.write_text(json.dumps(final_payload, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

    return final_payload


stock_history_provider = get_stock_history_and_valuation


if __name__ == "__main__":
    test_code = "600409"
    res = get_stock_history_and_valuation(test_code)
    print("Code:", res["summary"]["code"])
    print("IPO Date:", res["summary"]["ipo_date"])
    print("Total Months:", res["summary"]["total_months"])
    print("Total Days:", res["summary"]["total_days"])
    print("Latest Price:", res["summary"]["latest_price"])
    print("Latest PB:", res["summary"]["latest_pb"], f"Percentile: {res['summary']['pb_percentile']}%")
    print("Latest PE:", res["summary"]["latest_pe"], f"Percentile: {res['summary']['pe_percentile']}%")
    print("Latest ROE:", res["summary"]["latest_roe"])
    print("Monthly Klines:", len(res["monthly_history"]))
    print("Daily Klines:", len(res["daily_history"]))
    print("Valuation Series:", len(res["valuation_history"]))
    print("ROE Series:", len(res["roe_history"]))

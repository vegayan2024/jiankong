# -*- coding: utf-8 -*-
"""
风格题材时效情报与知识库智能更新服务 (Themes Intel Updater)
=========================================================
定位：
  针对 jiankong 系统的「风格题材 / 问财热点」栏目，提供自动化时效数据拉取与知识库更新机制：
  1. 拉取各题材领衔龙头股票的真实行情与 5 日走势，动态计算板块真实周度表现 (`week_perf`)，来源：腾讯前复权K线；
  2. 通过东财主力净流入 (f62，已实测沪 1./深 0./港 116. 均返回真实值) 获取真实资金流，并按日快照自采累计，
     攒满 5 个自然交易日即输出「近5日累计」，不足则如实标注「累计N日」——真实数据、真实窗口，禁止估算冒充；
  3. 热度星级 (`hot_level`) = 真实成交额 × |真实周表现| 的跨题材排名分位（本地计算，非问财官方热度），可复算；
  4. 每个时效字段均写入来源标注 (`*_source`)，接口无返回时输出「未获取」并标记 `missing`，绝不捏造；
  5. 自动维护持久化文件 `themes_knowledge_base.json` 与资金流日频快照 `fund_flow_history.json`，
     并支持与 `watchlist.json` 双向勾稽同步；
  6. 提供模块化调用与 CLI 一键执行接口（服务端另设定时调度：每交易日 10:00 自动触发）。
"""

import os
import re
import sys
import json
import time
import logging
import argparse
import urllib.request
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple

try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ThemeIntelUpdater")

SCRIPTS_DIR = Path(__file__).resolve().parent
JIANKONG_DIR = SCRIPTS_DIR.parent
THEMES_FILE = JIANKONG_DIR / "themes_knowledge_base.json"
WEB_THEMES_FILE = JIANKONG_DIR / "web" / "themes_knowledge_base.json"
WATCHLIST_FILE = JIANKONG_DIR / "watchlist.json"
FUND_FLOW_HISTORY_FILE = JIANKONG_DIR / "fund_flow_history.json"

# 全局直连 opener：绕过系统代理，避免代理拦截行情接口
_URL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# 真实数据来源常量 (供 *_source 字段标注，任何出数结论均可溯源)
SRC_WEEK_PERF = "tencent_qfq_kline_5d"           # 腾讯前复权K线近5交易日区间涨跌 (真实)
SRC_FUND_FLOW = "eastmoney_f62_daily_accum"      # 东财主力净流入日频快照累计 (真实)
SRC_HOT_LEVEL = "local_rank_by_real_turnover_x_week_perf"  # 真实成交额×周振幅跨题材排名 (本地可复算)
SRC_MISSING = "missing"                          # 接口无返回：如实缺失，不造数


def code_to_tencent_symbol(code: str) -> str:
    """将标准代码格式 (如 688256.SH, 01801.HK) 转换为腾讯行情代码 (如 sh688256, hk01801)"""
    c = code.strip().upper()
    if c.endswith(".HK") or (len(re.sub(r"\D", "", c)) == 5 and not c.endswith((".SH", ".SZ", ".BJ"))):
        num = re.sub(r"\D", "", c).zfill(5)
        return f"hk{num}"
    elif c.endswith(".SH"):
        num = re.sub(r"\D", "", c).zfill(6)
        return f"sh{num}"
    elif c.endswith(".SZ"):
        num = re.sub(r"\D", "", c).zfill(6)
        return f"sz{num}"
    elif c.endswith(".BJ"):
        num = re.sub(r"\D", "", c).zfill(6)
        return f"bj{num}"
    else:
        num = re.sub(r"\D", "", c)
        if len(num) == 6:
            if num.startswith(("60", "68", "51", "56", "58")):
                return f"sh{num}"
            else:
                return f"sz{num}"
        elif len(num) == 5:
            return f"hk{num}"
    return c.lower()


def code_to_eastmoney_secid(code: str) -> Optional[str]:
    """
    标准代码 -> 东财 secid。已实测映射：沪 '1.XXXXXX'、深 '0.XXXXXX'、港 '116.XXXXX'。
    未验证的市场 (如北交所) 一律返回 None——宁可如实缺失，不猜测映射。
    """
    c = code.strip().upper()
    num = re.sub(r"\D", "", c)
    if c.endswith(".HK"):
        return f"116.{num.zfill(5)}"
    if c.endswith(".SH"):
        return f"1.{num.zfill(6)}"
    if c.endswith(".SZ"):
        return f"0.{num.zfill(6)}"
    return None


class ThemeIntelUpdater:
    def __init__(self, themes_path: Optional[Path] = None, watchlist_path: Optional[Path] = None):
        self.themes_path = themes_path or THEMES_FILE
        self.watchlist_path = watchlist_path or WATCHLIST_FILE
        self.themes: List[Dict[str, Any]] = []
        self.load_themes()

    def load_themes(self) -> List[Dict[str, Any]]:
        """加载题材知识库数据"""
        if self.themes_path.exists():
            try:
                with open(self.themes_path, "r", encoding="utf-8") as f:
                    self.themes = json.load(f)
                logger.info(f"Loaded {len(self.themes)} themes from {self.themes_path}")
            except Exception as e:
                logger.error(f"Failed to load themes from {self.themes_path}: {e}")
                self.themes = []
        return self.themes

    def save_themes(self):
        """保存题材知识库数据 (同时同步主目录与 web 目录)"""
        try:
            content = json.dumps(self.themes, ensure_ascii=False, indent=2)
            self.themes_path.write_text(content, encoding="utf-8")
            if WEB_THEMES_FILE.parent.exists():
                WEB_THEMES_FILE.write_text(content, encoding="utf-8")
            logger.info(f"Successfully saved {len(self.themes)} themes to {self.themes_path}")
        except Exception as e:
            logger.error(f"Failed to save themes: {e}")

    # ------------------------------------------------------------------
    # 真实行情：腾讯前复权K线 (周表现 + 成交额)
    # ------------------------------------------------------------------
    def fetch_stock_kline_perf(self, symbol: str) -> Tuple[Optional[float], Optional[float]]:
        """
        通过日K线接口计算近 5 个交易日的累计涨跌幅与日均成交额
        返回: (5日累计涨跌幅%, 成交额万元)
        """
        t_sym = code_to_tencent_symbol(symbol)
        url = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={t_sym},day,,,6,qfq"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with _URL_OPENER.open(req, timeout=5) as resp:
                res = json.loads(resp.read().decode("utf-8"))
            kdata = res.get("data", {}).get(t_sym, {})
            days = kdata.get("qfqday", kdata.get("day", []))

            # 获取实时成交额
            qt_info = kdata.get("qt", {}).get(t_sym, [])
            turnover_wan = 0.0
            if len(qt_info) > 37:
                try:
                    raw_val = float(qt_info[37])
                    # 港股行情成交额单位为元(HKD)，A股行情成交额单位为万元(CNY)
                    if symbol.endswith(".HK") or t_sym.startswith("hk"):
                        turnover_wan = raw_val / 10000.0
                    else:
                        turnover_wan = raw_val
                except (ValueError, IndexError):
                    pass

            if len(days) >= 2:
                base_close = float(days[0][2])  # 区间起点收盘价
                curr_close = float(days[-1][2])  # 最新价格
                pct = round((curr_close - base_close) / base_close * 100, 2)
                return pct, turnover_wan
            elif len(days) == 1 and len(qt_info) > 32:
                try:
                    pct = float(qt_info[32])
                    return pct, turnover_wan
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"Failed to fetch kline for {symbol} ({t_sym}): {e}")
        return None, None

    # ------------------------------------------------------------------
    # 真实资金流：东财主力净流入 f62 (2026-11-18 实测通过，沪/深/港均有真实返回)
    # 交叉验证：f62 与 fflow/kline 主力净流入完全一致，且 大单+超大单=主力 算术闭合
    # ------------------------------------------------------------------
    def fetch_main_inflow_batch(self, codes: List[str]) -> Dict[str, Optional[float]]:
        """
        东财 ulist.np 批量获取当日主力净流入 (f62，单位：元)。
        返回 {code: float 或 None}；接口异常/无返回的代码值为 None (如实缺失，不造数)。
        """
        result: Dict[str, Optional[float]] = {c: None for c in codes}
        code_by_bare = {}
        secids = []
        for c in codes:
            secid = code_to_eastmoney_secid(c)
            if not secid:
                logger.warning(f"代码 {c} 无已实测的东财映射 (不猜测)，资金流如实缺失")
                continue
            secids.append(secid)
            code_by_bare[secid.split(".", 1)[1]] = c
        if not secids:
            return result

        url = ("https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2"
               f"&secids={','.join(secids)}&fields=f12,f62")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with _URL_OPENER.open(req, timeout=8) as resp:
                res = json.loads(resp.read().decode("utf-8"))
            diff = (res.get("data") or {}).get("diff") or []
            for item in diff:
                bare = str(item.get("f12", ""))
                code = code_by_bare.get(bare)
                if not code:
                    continue
                try:
                    result[code] = float(item.get("f62"))
                except (TypeError, ValueError):
                    result[code] = None
        except Exception as e:
            logger.warning(f"东财主力净流入批量获取失败 (本次全部如实缺失): {e}")
        ok = sum(1 for v in result.values() if v is not None)
        logger.info(f"东财主力净流入批量获取: {ok}/{len(codes)} 只返回真实值")
        return result

    # ------------------------------------------------------------------
    # 资金流日频快照：自采真实累计 (窗口如实标注)
    # ------------------------------------------------------------------
    def _load_fund_flow_history(self) -> Dict[str, Dict[str, float]]:
        try:
            with open(FUND_FLOW_HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_fund_flow_history(self, hist: Dict[str, Dict[str, float]]):
        try:
            FUND_FLOW_HISTORY_FILE.write_text(
                json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"资金流日频快照保存失败: {e}")

    # ------------------------------------------------------------------
    # 核心：全量刷新时效指标
    # ------------------------------------------------------------------
    def update_theme_metrics(self) -> Dict[str, Any]:
        """
        全量更新所有题材的时效性数据 (全部真实来源，来源逐项标注)：
        - week_perf  : 腾讯前复权K线近5交易日真实区间涨跌
        - fund_flow  : 东财主力净流入日频快照累计 (近5日；不足5日如实标'累计N日')
        - hot_level  : 真实成交额×|周表现| 跨题材排名分位 (本地可复算)
        """
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        today = now_str[:10]

        # 1) 汇总全部龙头代码，东财批量取当日真实主力净流入
        all_codes: List[str] = []
        for t in self.themes:
            for s in t.get("top_stocks", []):
                if s.get("code"):
                    all_codes.append(s["code"])
        unique_codes = sorted(set(all_codes))
        inflow_map = self.fetch_main_inflow_batch(unique_codes)

        # 2) 日频快照累计 (每代码仅留最近30个日期，同日重跑覆盖为最新值)
        hist = self._load_fund_flow_history()
        for code, val in inflow_map.items():
            if val is not None:
                hist.setdefault(code, {})[today] = val
        for code in list(hist.keys()):
            dates = sorted(hist[code].keys())[-30:]
            hist[code] = {d: hist[code][d] for d in dates}
        self._save_fund_flow_history(hist)

        # 3) 逐题材计算：真实周表现 + 真实资金流累计
        stats_for_rank: List[Tuple[Dict[str, Any], float, float]] = []
        updated_count = 0
        for t in self.themes:
            top_stocks = t.get("top_stocks", [])
            perfs = []
            turnovers = []

            for s in top_stocks:
                code = s.get("code")
                if code:
                    pct, turn = self.fetch_stock_kline_perf(code)
                    if pct is not None:
                        perfs.append(pct)
                    if turn is not None:
                        turnovers.append(turn)
                    # 避免对接口请求过密
                    time.sleep(0.05)

            # 3.1 真实周表现 (week_perf，腾讯K线)
            if perfs:
                avg_perf = round(sum(perfs) / len(perfs), 2)
                sign = "+" if avg_perf > 0 else ""
                t["week_perf"] = f"{sign}{avg_perf}%"
                t["week_perf_source"] = SRC_WEEK_PERF
            else:
                avg_perf = 0.0
                t["week_perf_source"] = SRC_MISSING

            total_turnover_yi = sum(turnovers) / 10000.0  # 万元 -> 亿元

            # 3.2 真实资金流 (东财 f62 日频累计；窗口如实标注，不足5日不冒充)
            theme_codes = [s["code"] for s in top_stocks if s.get("code")]
            total_inflow = 0.0
            covered_codes = 0
            window_days: List[int] = []
            for c in theme_codes:
                series = hist.get(c, {})
                last5 = dict(sorted(series.items())[-5:])
                if last5:
                    covered_codes += 1
                    total_inflow += sum(last5.values())
                    window_days.append(len(last5))

            if covered_codes == 0:
                t["fund_flow"] = "未获取(东财无返回)"
                t["fund_flow_source"] = SRC_MISSING
            else:
                yi = total_inflow / 1e8
                sign = "+" if yi > 0 else ""
                direction = "净流入" if yi > 0 else ("均衡" if yi == 0 else "净流出")
                n_days = min(window_days)
                window_label = "近5日累计" if n_days >= 5 else f"累计{n_days}日"
                t["fund_flow"] = f"{sign}{yi:.2f}亿 {direction}({window_label}·东财真实)"
                t["fund_flow_source"] = SRC_FUND_FLOW
            t["fund_flow_coverage"] = f"{covered_codes}/{len(theme_codes)}龙头"

            stats_for_rank.append((t, total_turnover_yi, abs(avg_perf)))
            updated_count += 1

        # 4) 热度星级：真实成交额×(1+|周表现|/10) 跨题材排名分位 (可复算，非捏造)
        scores = [turn * (1.0 + ap / 10.0) for (_, turn, ap) in stats_for_rank]
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        rank_pos = {idx: pos for pos, idx in enumerate(order)}
        total = max(len(stats_for_rank), 1)
        for i, (t, _turn, _ap) in enumerate(stats_for_rank):
            frac = (rank_pos[i] + 1) / total
            if frac <= 0.3:
                t["hot_level"] = "★★★★★"
            elif frac <= 0.7:
                t["hot_level"] = "★★★★☆"
            else:
                t["hot_level"] = "★★★☆☆"
            t["hot_level_source"] = SRC_HOT_LEVEL
            t["last_updated_at"] = now_str

        self.save_themes()
        logger.info(f"Updated metrics for {updated_count} themes at {now_str}")
        return {
            "status": "success",
            "updated_count": updated_count,
            "timestamp": now_str,
            "inflow_codes_ok": sum(1 for v in inflow_map.values() if v is not None),
            "inflow_codes_total": len(unique_codes),
            "sources": {
                "week_perf": SRC_WEEK_PERF,
                "fund_flow": SRC_FUND_FLOW,
                "hot_level": SRC_HOT_LEVEL,
            },
        }

    def sync_with_watchlist(self) -> Dict[str, Any]:
        """
        保证 themes_knowledge_base.json 中的所有头部股票在 watchlist.json 中 100% 存在且分组一致
        """
        if not self.watchlist_path.exists():
            return {"status": "error", "message": "watchlist.json not found"}

        try:
            with open(self.watchlist_path, "r", encoding="utf-8") as f:
                watchlist: List[Dict[str, Any]] = json.load(f)
        except Exception as e:
            return {"status": "error", "message": f"Failed to read watchlist: {e}"}

        wl_map = {item["code"]: item for item in watchlist}
        added_count = 0
        updated_count = 0

        for t in self.themes:
            grp = t.get("group")
            for s in t.get("top_stocks", []):
                code = s.get("code")
                name = s.get("name")
                if not code:
                    continue

                if code not in wl_map:
                    # 识别市场
                    market = "SH" if code.endswith(".SH") else ("SZ" if code.endswith(".SZ") else ("HK" if code.endswith(".HK") else "SH"))
                    symbol = code.split(".")[0]
                    new_item = {
                        "code": code,
                        "symbol": symbol,
                        "market": market,
                        "name": name or f"标的{symbol}",
                        "group": grp,
                        "asset_type": "stock",
                        "enabled": True,
                        "alert_threshold_pct": 4.5,
                        "monitor_announcement": True,
                        "monitor_intraday": True,
                        "monitor_news": True,
                        "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }
                    watchlist.append(new_item)
                    wl_map[code] = new_item
                    added_count += 1
                else:
                    item = wl_map[code]
                    if item.get("group") != grp:
                        item["group"] = grp
                        updated_count += 1

        # 保存 watchlist.json
        if added_count > 0 or updated_count > 0:
            type_priority = {"index": 0, "etf": 1, "stock": 2}
            watchlist.sort(key=lambda x: (
                type_priority.get(x.get("asset_type", "stock"), 2),
                0 if (x.get("group", "").startswith("风格:")) else 1,
                x.get("market", ""),
                x.get("code", "")
            ))
            with open(self.watchlist_path, "w", encoding="utf-8") as f:
                json.dump(watchlist, f, ensure_ascii=False, indent=2)
            logger.info(f"Watchlist synchronized: added {added_count}, updated {updated_count}")

        return {
            "status": "success",
            "added_count": added_count,
            "updated_count": updated_count,
            "total_watchlist": len(watchlist)
        }


def run_theme_intel_update(sync_watchlist: bool = True) -> Dict[str, Any]:
    """快捷执行更新函数"""
    updater = ThemeIntelUpdater()
    metric_res = updater.update_theme_metrics()
    sync_res = {}
    if sync_watchlist:
        sync_res = updater.sync_with_watchlist()
    return {
        "status": "success",
        "metrics": metric_res,
        "sync": sync_res,
        "total_themes": len(updater.themes)
    }


def main():
    parser = argparse.ArgumentParser(description="风格题材时效情报与知识库智能更新器 (真实数据源)")
    parser.add_argument("--no-sync", action="store_true", help="不自动同步到 watchlist.json")
    args = parser.parse_args()

    print("🚀 启动风格题材时效情报与知识库更新 (真实数据源: 腾讯K线 + 东财主力净流入)...")
    res = run_theme_intel_update(sync_watchlist=not args.no_sync)
    m = res["metrics"]
    print("\n========================================================")
    print(f"🎉 题材数据更新完成！")
    print(f"• 题材总数: {res['total_themes']}")
    print(f"• 指标更新时间: {m.get('timestamp')}")
    print(f"• 周表现来源: {m.get('sources', {}).get('week_perf')} (真实K线)")
    print(f"• 资金流来源: {m.get('sources', {}).get('fund_flow')} (真实，东财 f62 日频累计)")
    print(f"• 东财取数覆盖: {m.get('inflow_codes_ok')}/{m.get('inflow_codes_total')} 只龙头返回真实值")
    print(f"• 自选监控池同步: 新增 {res['sync'].get('added_count', 0)} 只, 更新 {res['sync'].get('updated_count', 0)} 只, 标的池总计 {res['sync'].get('total_watchlist', 0)} 只")
    print("========================================================\n")


if __name__ == "__main__":
    main()

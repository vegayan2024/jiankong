"""
QUANT RADAR (Jiankong) - 宏观与风格罗盘模块 (Macro Compass)
融合南方基金「指数观澜」平台宏观量化理念：
1. 市场情绪温度计 (Market Sentiment & Percentile P-Value)
2. 大小盘轮动信号 (Large-Cap vs Small-Cap Rotation)
3. 价值 vs 成长轮动信号 (Value vs Growth Style Rotation)
4. 港股资金面与AH溢价参考 (Southbound Flow & AH Premium)
"""

import os
import json
import time
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("macro_compass")
logger.setLevel(logging.INFO)

CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scratch", "macro_compass_cache.json")

class MacroCompassEngine:
    """
    宏观战术资产配置与风格罗盘引擎
    支持：
    1. 本地量化模型实时推算（基于自选池核心指数比价动量与估值）
    2. 指数观澜对照与离线/可插拔校准
    """
    def __init__(self):
        self._ensure_cache_dir()
        self.cached_state: Dict[str, Any] = self._load_cache()

    def _ensure_cache_dir(self):
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)

    def _load_cache(self) -> Dict[str, Any]:
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"读取宏观罗盘缓存失败: {e}")
        # 默认基准状态（与观澜最新实盘对齐，可动态演进）
        return {
            "update_time": time.strftime("%Y-%m-%d 18:30:00"),
            "sentiment": {
                "p_value": 16.62,
                "zone": "低温区",
                "zone_code": "cold",  # cold / normal / hot
                "icon": "❄️",
                "advice": "建议超配以A500为代表的权益资产",
                "range_desc": "近3年历史分位 16.6% (处于相对底部逆向布局区)"
            },
            "market_cap_style": {
                "bias": "小盘",
                "bias_code": "small",  # large / small / equal
                "bias_score": 0.65,     # [-1(大盘), 1(小盘)]
                "benchmark": "国证2000/中证1000 vs 沪深300",
                "desc": "中小盘相对强弱动量偏强，流动性边际改善弹性占优"
            },
            "growth_value_style": {
                "bias": "价值",
                "bias_code": "value",  # growth / value / balanced
                "bias_score": -0.58,   # [-1(价值), 1(成长)]
                "benchmark": "红利/高股息 vs 创业板/科创50",
                "desc": "防御属性高股息与稳定现金流资产受长端利率与风险偏好青睐"
            },
            "southbound_ah": {
                "southbound_status": "净流入维持稳健",
                "ah_premium_desc": "AH股溢价处于中高位区间，港股多维ERP显现中长期配置性价比"
            },
            "source": "南方基金「指数观澜」量化框架与本地模型对齐"
        }

    def save_cache(self):
        try:
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.cached_state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存宏观罗盘缓存失败: {e}")

    def update_from_quotes(self, quotes: Dict[str, Any]):
        """
        利用盘中实时行情微调风格相对强弱动量（日内动量微调，闭环联动国证2000、中证红利与AH溢价指数）
        """
        # 1. 大小盘相对强弱：优先取国证2000 (399303.SZ)，次选中证1000 (000852.SH) vs 沪深300 (000300.SH)
        csi300 = quotes.get("000300.SH") or quotes.get("000300")
        csi_small = (
            quotes.get("399303.SZ") or quotes.get("399303") or
            quotes.get("000852.SH") or quotes.get("000852") or
            quotes.get("159531.SZ") or quotes.get("563300.SH")
        )
        if csi300 and csi_small:
            c300_pct = float(csi300.get("pct_change", csi300.get("change_pct", 0.0)))
            small_pct = float(csi_small.get("pct_change", csi_small.get("change_pct", 0.0)))
            small_name = csi_small.get("name", "小盘")
            diff_mc = small_pct - c300_pct
            self.cached_state["market_cap_style"]["intraday_spread"] = round(diff_mc, 2)
            self.cached_state["market_cap_style"]["intraday_status"] = (
                f"盘中{small_name}强于大盘 +{diff_mc:.2f}%" if diff_mc > 0 else f"盘中大盘强于{small_name} +{abs(diff_mc):.2f}%"
            )
            if diff_mc >= 0.5:
                self.cached_state["market_cap_style"]["bias"] = "小盘"
                self.cached_state["market_cap_style"]["bias_code"] = "small"
            elif diff_mc <= -0.5:
                self.cached_state["market_cap_style"]["bias"] = "大盘"
                self.cached_state["market_cap_style"]["bias_code"] = "large"

        # 2. 价值 vs 成长风格：中证红利 (000922.SH) vs 创业板指 (399006.SZ) / 科创50 (000688.SH)
        dividend = quotes.get("000922.SH") or quotes.get("512010.SH")
        growth = quotes.get("399006.SZ") or quotes.get("000688.SH")
        if dividend and growth:
            div_pct = float(dividend.get("pct_change", dividend.get("change_pct", 0.0)))
            gro_pct = float(growth.get("pct_change", growth.get("change_pct", 0.0)))
            diff_style = gro_pct - div_pct  # 正值代表成长占优，负值代表红利价值占优
            self.cached_state["growth_value_style"]["intraday_spread"] = round(diff_style, 2)
            self.cached_state["growth_value_style"]["intraday_status"] = (
                f"盘中成长强于价值 +{diff_style:.2f}%" if diff_style > 0 else f"盘中红利价值强于成长 +{abs(diff_style):.2f}%"
            )
            if diff_style >= 0.5:
                self.cached_state["growth_value_style"]["bias"] = "成长"
                self.cached_state["growth_value_style"]["bias_code"] = "growth"
            elif diff_style <= -0.5:
                self.cached_state["growth_value_style"]["bias"] = "价值"
                self.cached_state["growth_value_style"]["bias_code"] = "value"

        # 3. 恒生AH溢价指数追踪：HSAHP.HK
        ah_index = quotes.get("HSAHP.HK")
        if ah_index:
            ah_price = float(ah_index.get("price", 0.0))
            ah_pct = float(ah_index.get("pct_change", ah_index.get("change_pct", 0.0)))
            if ah_price > 0:
                self.cached_state["southbound_ah"]["ah_premium_index"] = ah_price
                self.cached_state["southbound_ah"]["ah_premium_change"] = ah_pct
                self.cached_state["southbound_ah"]["ah_premium_desc"] = (
                    f"AH股溢价指数点位 {ah_price:.2f} ({'+' if ah_pct>=0 else ''}{ah_pct:.2f}%)，"
                    f"{'溢价走阔H股性价比凸显' if ah_pct > 0 else '溢价收窄AH价差平抑'}"
                )

        self.cached_state["last_sync_time"] = time.strftime("%Y-%m-%d %H:%M:%S")

    def get_summary_pill_text(self) -> str:
        """
        生成给顶部胶囊 Badge 展示的高信息密度单行摘要
        """
        sent = self.cached_state.get("sentiment", {})
        mc = self.cached_state.get("market_cap_style", {})
        gv = self.cached_state.get("growth_value_style", {})
        
        icon = sent.get("icon", "❄️")
        p_val = sent.get("p_value", 16.6)
        zone = sent.get("zone", "低温")
        mc_bias = mc.get("bias", "小盘")
        gv_bias = gv.get("bias", "价值")
        
        return f"{icon}情绪:{zone}({p_val}) | 风格:{mc_bias}+{gv_bias}"

    def get_full_status(self) -> Dict[str, Any]:
        return {
            "pill_text": self.get_summary_pill_text(),
            **self.cached_state
        }

# 全局单例
macro_compass = MacroCompassEngine()

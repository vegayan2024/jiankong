# -*- coding: utf-8 -*-
"""
QUANT RADAR (Jiankong) - 微观筹码结构与筹码峰 (CYQ) 向量化计算引擎
=============================================================
遵循 A 股经典成交量换手率衰减模型 (三角价格分布累积算法)：
1. 选取近 120 交易日日 K 线时序
2. 逐日按换手率 (Turnover Rate) 对历史沉淀筹码执行物理衰减：Chips_{t} = Chips_{t-1} * (1 - turnover_t)
3. 当日新增换手筹码按三角概率分布 (Triangular Distribution) 离散填充在 [Low, High] 价格区间
4. 产出核心微观量化指标：
   - 获利盘比例 (Profit Ratio %)：当前收盘价下方的筹码堆积占比
   - 90% 筹码集中度 (Concentration 90%)：(P95 - P5) / (P95 + P5)
   - 70% 筹码集中度 (Concentration 70%)：(P85 - P15) / (P85 + P15)
   - 加权平均筹码成本 (Average Cost)
   - 60 档离散筹码峰直方图 (Histogram Bins)，支持前端极速 Canvas 渲染
"""

import math
from typing import List, Dict, Any, Optional

def compute_cyq(klines: List[Dict[str, Any]], latest_price: Optional[float] = None, window: int = 120) -> Dict[str, Any]:
    """
    计算标的的微观筹码分布 (CYQ)
    """
    if not klines or len(klines) < 5:
        return {
            "status": "insufficient_data",
            "profit_ratio": 50.0,
            "avg_cost": latest_price or 0.0,
            "concentration_90": 15.0,
            "concentration_70": 10.0,
            "cost_range_90": [0.0, 0.0],
            "cost_range_70": [0.0, 0.0],
            "bins": []
        }

    # 取近 window 天 K 线
    recent_k = klines[-window:]
    
    # 提取最高最低价，构建设防价格边界
    highs = [float(k.get("high") or k.get("close") or 0.0) for k in recent_k]
    lows = [float(k.get("low") or k.get("close") or 0.0) for k in recent_k]
    closes = [float(k.get("close") or 0.0) for k in recent_k]
    
    min_p = min(p for p in lows if p > 0) if any(p > 0 for p in lows) else 1.0
    max_p = max(highs) if highs else 10.0
    
    if max_p <= min_p:
        max_p = min_p * 1.1

    cur_price = float(latest_price) if latest_price and latest_price > 0 else closes[-1]

    # 离散化区间：划分为 60 个价格箱体 (Bins)
    num_bins = 60
    bin_width = (max_p - min_p) / num_bins
    bin_prices = [min_p + (i + 0.5) * bin_width for i in range(num_bins)]
    chips = [0.0] * num_bins

    # 估算日换手率：若没有显式 turnover_rate，利用成交量相对于均量的比率估算基准 2.5%
    vols = [float(k.get("volume") or 0.0) for k in recent_k]
    avg_vol = sum(vols) / len(vols) if vols and sum(vols) > 0 else 1.0

    for k in recent_k:
        k_high = float(k.get("high") or k.get("close") or 0.0)
        k_low = float(k.get("low") or k.get("close") or 0.0)
        k_close = float(k.get("close") or 0.0)
        k_open = float(k.get("open") or k_close)
        k_vol = float(k.get("volume") or 0.0)

        if k_high <= k_low or k_high <= 0:
            continue

        # 估算当日换手率 (A股日均换手约 1.5%~6.0%，加权成交量波动)
        rel_vol = k_vol / avg_vol if avg_vol > 0 else 1.0
        turnover = min(max(0.02 * rel_vol, 0.01), 0.25)

        # 1. 历史筹码物理衰减
        decay = 1.0 - turnover
        for i in range(num_bins):
            chips[i] *= decay

        # 2. 当日新增筹码按三角分布分配
        # 顶点设为当日典型加权均价 (Open + High + Low + Close) / 4
        k_center = (k_open + k_high + k_low + k_close) / 4.0
        
        # 找到受影响的 bin 索引
        start_idx = max(0, min(num_bins - 1, int((k_low - min_p) / bin_width)))
        end_idx = max(0, min(num_bins - 1, int((k_high - min_p) / bin_width)))

        daily_weights = []
        for i in range(start_idx, end_idx + 1):
            p = bin_prices[i]
            # 简化的三角核函数 (Triangular Kernel)
            dist = abs(p - k_center)
            span = max(k_high - k_low, bin_width)
            w = max(0.0, 1.0 - (dist / (span * 0.75)))
            daily_weights.append((i, w))

        total_w = sum(w for _, w in daily_weights)
        if total_w > 0:
            for i, w in daily_weights:
                chips[i] += (w / total_w) * turnover

    # 归一化总筹码为 100%
    total_chips = sum(chips)
    if total_chips > 0:
        chips = [c / total_chips for c in chips]
    else:
        chips = [1.0 / num_bins] * num_bins

    # 计算关键量化指标
    # 1. 获利盘比例 (Profit Ratio)
    profit_chips = 0.0
    for i in range(num_bins):
        if bin_prices[i] <= cur_price:
            profit_chips += chips[i]
    profit_ratio = round(profit_chips * 100.0, 1)

    # 2. 加权平均成本 (Average Cost)
    avg_cost = sum(bin_prices[i] * chips[i] for i in range(num_bins))

    # 3. 分位数计算 (P5, P15, P50, P85, P95)
    cum = 0.0
    p5 = bin_prices[0]
    p15 = bin_prices[0]
    p50 = bin_prices[num_bins // 2]
    p85 = bin_prices[-1]
    p95 = bin_prices[-1]

    for i in range(num_bins):
        cum += chips[i]
        if cum >= 0.05 and p5 == bin_prices[0]:
            p5 = bin_prices[i]
        if cum >= 0.15 and p15 == bin_prices[0]:
            p15 = bin_prices[i]
        if cum >= 0.50 and p50 == bin_prices[num_bins // 2]:
            p50 = bin_prices[i]
        if cum >= 0.85 and p85 == bin_prices[-1]:
            p85 = bin_prices[i]
        if cum >= 0.95 and p95 == bin_prices[-1]:
            p95 = bin_prices[i]
            break

    conc_90 = round(((p95 - p5) / (p95 + p5) * 100.0), 2) if (p95 + p5) > 0 else 0.0
    conc_70 = round(((p85 - p15) / (p85 + p15) * 100.0), 2) if (p85 + p15) > 0 else 0.0

    # 组装 60 档直方图数据
    bins_data = []
    max_chip_density = max(chips) if chips else 1.0
    for i in range(num_bins):
        p = round(bin_prices[i], 2)
        c = chips[i]
        norm_width = round((c / max_chip_density) * 100.0, 1) if max_chip_density > 0 else 0.0
        bins_data.append({
            "price": p,
            "density": round(c * 100.0, 2),
            "width_pct": norm_width,
            "is_profit": p <= cur_price
        })

    return {
        "status": "success",
        "latest_price": round(cur_price, 2),
        "profit_ratio": profit_ratio,
        "avg_cost": round(avg_cost, 2),
        "concentration_90": conc_90,
        "concentration_70": conc_70,
        "cost_range_90": [round(p5, 2), round(p95, 2)],
        "cost_range_70": [round(p15, 2), round(p85, 2)],
        "bins": bins_data
    }

# -*- coding: utf-8 -*-
"""
QUANT RADAR (Jiankong) - Pydantic 数据模型契约
==============================================
遵循 pydantic-models-py 技能规范：
采用 Multi-Model 模式 (Base / Create / Update / Response)，
提供完整的字段默认值兜底与类型安全，防止前端处理空值白屏。
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


# ==============================================================================
# 1. 资产标的与行情快照模型 (Stock & Quote Models)
# ==============================================================================

class StockQuoteBase(BaseModel):
    """标的基础信息"""
    code: str = Field(..., description="规范化资产代码，如 600519.SH, 00700.HK, 000300.SH")
    name: str = Field("", description="标的名称")
    symbol: str = Field("", description="纯数字或字母代码")
    market: str = Field("SH", description="市场代码: SH, SZ, BJ, HK 等")
    asset_type: str = Field("stock", description="资产类型: stock, index, etf")
    group: str = Field("自选", description="所属板块或题材分组")
    enabled: bool = Field(True, description="是否启用监控与预警")
    alert_threshold_pct: float = Field(3.0, description="异动预警阈值百分比")


class StockQuoteResponse(StockQuoteBase):
    """实时行情快照响应模型 (带默认值兜底，杜绝 None 导致前端异常)"""
    price: float = Field(0.0, description="当前最新价格")
    change_pct: float = Field(0.0, description="当日涨跌幅百分比")
    change: float = Field(0.0, description="当日涨跌额")
    high: float = Field(0.0, description="最高价")
    low: float = Field(0.0, description="最低价")
    open: float = Field(0.0, description="开盘价")
    prev_close: float = Field(0.0, description="前收盘价")
    volume: float = Field(0.0, description="成交量 (手/股)")
    amount: float = Field(0.0, description="成交金额 (万元/元)")
    turnover_rate: float = Field(0.0, description="换手率百分比")
    pe_ttm: Optional[float] = Field(None, description="滚动市盈率 PE(TTM)")
    pb: Optional[float] = Field(None, description="市净率 PB")
    market_cap: Optional[float] = Field(None, description="总市值 (亿元)")
    date: str = Field("", description="行情日期 YYYY-MM-DD")
    time: str = Field("", description="行情时间 HH:MM:SS")
    datetime: str = Field("", description="完整时间戳 YYYY-MM-DD HH:MM:SS")
    is_anomaly: bool = Field(False, description="是否触发异动警报")
    anomaly_msg: str = Field("", description="异动提示文本")

    class Config:
        populate_by_name = True


# ==============================================================================
# 2. 监控池管理模型 (Watchlist Management Models)
# ==============================================================================

class WatchlistItemCreate(BaseModel):
    """添加监控标的请求体"""
    code: str = Field(..., min_length=1, description="标的代码，如 600519 或 600519.SH")
    name: Optional[str] = Field(None, description="标的名称（可选，留空将自动联网匹配）")
    group: Optional[str] = Field("自选", description="所属分组/题材分类")
    threshold: Optional[float] = Field(3.0, ge=0.1, le=50.0, description="异动报警阈值 (%)")
    asset_type: Optional[str] = Field(None, description="资产类型: stock, index, etf（可选）")


class WatchlistItemUpdate(BaseModel):
    """更新监控标的请求体 (PATCH 全部字段可选)"""
    enabled: Optional[bool] = Field(None, description="是否启用")
    threshold: Optional[float] = Field(None, ge=0.1, le=50.0, description="异动报警阈值 (%)")
    group: Optional[str] = Field(None, min_length=1, description="所属分组")


# ==============================================================================
# 3. 实时预警、信披与要闻模型 (Alerts, Announcements & News)
# ==============================================================================

class AlertItemResponse(BaseModel):
    """异动告警事件模型"""
    id: str = Field(..., description="警报唯一标识")
    code: str = Field(..., description="触发标的代码")
    name: str = Field("", description="触发标的名称")
    group: str = Field("", description="标的分组")
    price: float = Field(0.0, description="触发时价格")
    change_pct: float = Field(0.0, description="触发时涨跌幅 (%)")
    threshold: float = Field(3.0, description="设定阈值 (%)")
    msg: str = Field("", description="警报描述文本")
    timestamp: str = Field("", description="触发时间 HH:MM:SS")
    date: str = Field("", description="触发日期 YYYY-MM-DD")


class AnnouncementItemResponse(BaseModel):
    """巨潮官方信披公告模型"""
    id: str = Field(..., description="公告唯一ID")
    code: str = Field("", description="关联标的代码")
    sec_code: str = Field("", description="A股纯数字代码")
    sec_name: str = Field("", description="公司简称")
    title: str = Field(..., description="公告标题")
    time: str = Field("", description="披露时间")
    url: str = Field("", description="公告PDF或详情链接")
    is_major: bool = Field(False, description="是否为重大公告")


class NewsItemResponse(BaseModel):
    """7x24 全球财经快讯模型"""
    id: str = Field(..., description="快讯唯一ID")
    title: str = Field("", description="快讯标题")
    content: str = Field(..., description="快讯正文")
    time: str = Field("", description="发布时间")
    source: str = Field("全球财经", description="快讯信源")
    is_hit: bool = Field(False, description="是否命中自选池标的")
    matched_stocks: List[Dict[str, str]] = Field(default_factory=list, description="命中的自选池标的列表")


# ==============================================================================
# 4. WebSocket 推送与概览聚合响应 (Broadcast & Overview Models)
# ==============================================================================

class TickBroadcastMessage(BaseModel):
    """WebSocket 广播 TICK 心跳消息"""
    type: str = Field("TICK", description="消息类型: TICK, QUOTE_UPDATE, ALERT")
    timestamp: str = Field(..., description="时间戳 HH:MM:SS")
    datetime: str = Field(..., description="完整日期时间 YYYY-MM-DD HH:MM:SS")
    date: str = Field(..., description="日期 YYYY-MM-DD")
    quotes_count: int = Field(0, description="当前缓存行情数")
    alerts_count: int = Field(0, description="当前警报数")
    news_count: int = Field(0, description="当前资讯数")
    news_hit_count: int = Field(0, description="命中自选池资讯数")
    macro_compass: Optional[Dict[str, Any]] = Field(default=None, description="宏观与风格罗盘状态")


class StandardApiResponse(BaseModel):
    """统一 API 响应包装"""
    status: str = Field("success", description="执行状态: success 或 error")
    message: Optional[str] = Field(None, description="提示信息")
    data: Optional[Any] = Field(None, description="数据载荷")

# QUANT RADAR (Jiankong) 架构演进与全栈整合实施方案 (RFC-2026-001)

> **文档编号**：RFC-2026-001  
> **文档性质**：系统重构与全栈架构演进方案（供第三方 AI / 技术委员会同行评审专用）  
> **目标工程**：`QUANT RADAR (Jiankong)` 盘中全资产即时监控与智能情报终端  
> **基线版本**：v2.2.0 (FastAPI + Vanilla JS + ECharts 5 + SQLite WAL + 四级门禁预警)  
> **演进方向**：轻量级个人监控台账 $\longrightarrow$ 机构级高性能实战投研/盯盘终端  

---

## 📋 附录 A：给评审 AI (Reviewer Agent) 的评估指令

```text
【评审指令】
你是一位兼具高频量化交易系统工程、金融终端前端架构（Bloomberg/Wind/TradingView体系）以及大模型金融落地经验的资深系统架构师。
请阅读下方关于《QUANT RADAR (Jiankong) 架构演进与全栈整合实施方案》，对其展开深入、客观、技术硬核的同行评审（Peer Review）。

请重点围绕以下 6 个维度展开评估并给出具体见解：
1. 技术选型匹配度与合理性：引入 KLineChart、FINOS Perspective、Gridstack.js、InStock、OpenBB Provider、DuckDB、vn.py EventEngine 是否存在技术栈冲突或过度设计？
2. 性能收益与资源开销：WebAssembly 内存表、DuckDB 进程内列存与轻量 LLM 链对本地机器（个人/单机工作站）的 CPU/内存压测表现预估，能否真正解决掉帧与计算瓶颈？
3. A股实战交易价值：新增的筹码峰（CYQ）、形态识别、连板梯队情绪算子、产业链逻辑推演是否切中 A 股短线与波段交易核心痛点？
4. 实施难度与改造成本评估：方案对各阶段改造周期、技术难点（如 Perspective Schema 绑定、OpenBB Pydantic 改造）的预估是否客观？
5. 潜在系统风险与破坏性：评估方案对现有稳定功能（A/H双时钟、四级门禁冷却、东财令牌桶限流、前复权漂移校验）的破坏风险及回滚策略完备性。
6. 最终裁决与优化建议：给出【通过 / 有条件修改 / 重新设计】的明确结论，并列出 Top 3 必须补充或调整的技术细节。
```

---

## 一、 系统现状与核心改造动因 (Current Baseline & Motivation)

### 1.1 系统基线能力 (v2.2.0)
- **底层时钟与数据韧性**：具备 A/H 独立时钟引擎（涵盖港股半日市、A股集合竞价等状态机）、除权首日跨周期联动失效防护、东财估值微秒级令牌桶串行领号、多源降级链（TuShare $\rightarrow$ AkShare $\rightarrow$ BaoStock $\rightarrow$ 腾讯）与 SQLite WAL 本地缓存。
- **预警与情报链路**：基于四级门禁的单/多条件规则组合预警（防轰炸冷却与日触发上限持久化）；AIHOT 具备 25+ 行业信源、新浪 7x24 电报及巨潮信披实体匹配能力。

### 1.2 现有系统的三大演化瓶颈
1. **前台表现力与吞吐瓶颈**：当前基于 Vanilla JS DOM 全量重绘渲染 186+ 自选股，在高频行情刷新时存在微卡顿；抽屉内置 ECharts K 线体量大，缺乏盘中分笔闪烁跳动、十字光标极速联动、分时买卖挂单线及手绘趋势线工具。
2. **监控大脑缺乏微观交易结构算子**：`alert_engine.py` 停留于价格截面指标组合，缺乏 A 股短线核心微观结构维度（如筹码集中度/获利比例、分时形态突破、连板晋级率、炸板率）；AIHOT 缺乏对突发政策与公告的产业链传导推理。
3. **主进程高并发与数据分析耦合**：`web_server.py` 在单个 FastAPI 进程内同时承担 HTTP 路由、长连接推送、行情轮询与历史数据清洗计算，缺乏事件总线（Event Bus）做异步解耦；SQLite 在横截面历史分位数回溯分析时吞吐受限。

---

## 二、 目标总体架构拓扑 (Target Architecture)

```mermaid
flowchart TB
    subgraph Frontend [前端专业工作区 - Golden Layout / Gridstack.js]
        subgraph TopBar [顶部全局状态带]
            ClockBadge[A/H双市场状态徽章]
            MacroBar[宏观罗盘/大小盘轮动]
        end
        subgraph PanelA [动态行情透视矩阵]
            PerspectiveView["FINOS Perspective (WASM) 毫秒级闪烁/透视热力图"]
        end
        subgraph PanelB [专业图表工作台]
            KLineView["KLineChart 毫秒级K线 / 筹码峰 / 估值走廊 (PE/PB Band)"]
        end
        subgraph PanelC [盘中战意与预警情报]
            SentimentView[短线情绪周期/连板梯队]
            AlertStream[四级门禁预警队列]
            AIHOTStream[AIHOT 深度产业链推演流]
        end
    end

    subgraph EventMesh [进程内高性能异步事件总线 (vn.py EventEngine via asyncio.Queue)]
        EVENT_TICK[EVENT_TICK 行情跳动]
        EVENT_BAR[EVENT_BAR 分时/K线更新]
        EVENT_ALERT[EVENT_ALERT 触发告警]
        EVENT_INTEL[EVENT_INTEL 突发情报/信披]
    end

    subgraph CoreEngines [核心解耦算子与推理引擎]
        AlertOperator["预警算子库 (InStock 移植: CYQ筹码峰 / 分时放量 / 经典形态)"]
        SentimentEngine["情绪周期量化 (涨跌停梯队 / 炸板率 / 溢价率)"]
        LLMTaskWorker["突发情报推理 Worker (多Agent粗细二段过滤 + 产业链传导)"]
    end

    subgraph DataPlatform [数据层与分析加速 (OpenBB SPI + DuckDB)]
        DataRouter["数据源路由 SPI (Pydantic 标准契约 + 自动熔断降级)"]
        DuckDBEngine["DuckDB 进程内列式分析 (历史估值分位数 / 截面相关性)"]
        SQLiteCache[("SQLite WAL (日K/基本面缓存)") ]
    end

    %% 数据流连接
    DataRouter -->|推送新Tick| EVENT_TICK
    EVENT_TICK --> PerspectiveView
    EVENT_TICK --> AlertOperator
    AlertOperator -->|条件命中| EVENT_ALERT
    EVENT_ALERT --> AlertStream
    EVENT_ALERT --> PushNotifier[外部推送: 飞书/企业微信/PushPlus]
    
    EVENT_INTEL --> LLMTaskWorker
    LLMTaskWorker -->|推理结构化输出| AIHOTStream
    
    DuckDBEngine <--> SQLiteCache
    DuckDBEngine -->|秒级分位数计算| KLineView
```

---

## 三、 模块级技术设计与实施规范

### 模块 1：前台 UI 与专业金融交互重构

#### 1.1 KLineChart 专业级 K 线引擎集成
- **定位**：替换抽屉（UI_Drawer）中沉重的 ECharts K 线，实现专业 TradingView 级交互体验。
- **技术规范**：
  - 采用原生 ES Module 引入 `klinecharts`（50KB，零外部依赖，Canvas 渲染）。
  - 后端适配：`/api/stock/{code}/history` 数据字段映射为标准化 `Timestamp, Open, High, Low, Close, Volume, Turnover`。
  - **特色功能叠加**：
    1. **估值通道（PE/PB Band）**：主图自定义扩展指标层，利用 DuckDB 产出的 5 年 PE/PB 历史分位数（10%, 25%, 50%, 75%, 90%）反算价格曲线并作为动态走廊叠加在 K 线背景上。
    2. **除权缺口与复权标记**：直接在 K 线时间轴挂载 Dividends 标记点，提示分红送配与复权除权时点。
    3. **画线工具持久化**：开启内置画线插件（趋势线、水平支撑线、斐波那契回撤），通过 `klinecharts.executeListener` 序列化保存至本地 `localStorage`。

#### 1.2 FINOS Perspective 流式大数据矩阵
- **定位**：自选矩阵（186+ 标的）高频渲染引擎，彻底杜绝 DOM 重绘掉帧。
- **技术规范**：
  - 以 Web Components 原生标签 `<perspective-viewer>` 挂载，底层由 Rust/C++ 编译的 WebAssembly 驱动。
  - 数据契约：前端声明 Perspective Table Schema：
    ```javascript
    const schema = {
      code: "string", name: "string", market: "string", sector: "string",
      price: "float", change_pct: "float", volume: "integer", turnover_rate: "float",
      pe_ttm: "float", pb: "float", updated_time: "datetime"
    };
    ```
  - **增量更新机制**：WebSocket 接收到 `EVENT_TICK` 时，不再遍历 DOM，直接调用 `perspectiveTable.update(ticks)`，原生享受亚毫秒级闪烁脉冲（Flash on Tick）与自动色阶柱状图。
  - **多维动态透视（Pivot）**：允许用户在界面上任意将表格切换为 `按题材分组 -> 涨跌幅均值排序`，或一键切换为全市场 Treemap（矩形树图）。

#### 1.3 Gridstack.js 多工作区自由编排
- **定位**：将固定三段式页面升级为彭博终端/IDE 级可拖拽可堆叠工作区。
- **技术规范**：
  - 轻量级 Vanilla JS 方案（相比 Golden Layout 对现有 DOM 侵入更低，无需复杂 iframe 容器）。
  - 核心面板拆分：【自选透视矩阵】、【深度 K 线抽屉】、【宏观资产罗盘】、【短线情绪看板】、【AIHOT 电报流】。
  - 提供预设布局切换（“盘中盯盘布局”、“盘后复盘布局”、“极简宽屏模式”），布局序列化为 JSON 存入前端缓存。

---

### 模块 2：盘中监控大脑与 A 股微观结构量化算子

#### 2.1 移植 InStock 经典微观结构算子
- **定位**：对 `scripts/alert_engine.py` 进行算子升维，从单一价格截面拓展至量价结构与筹码博弈。
- **移植路径**：剥离 InStock 的 `indicators` 与 `strategy` 纯计算函数，嵌入到现有的监控计算流中：
  1. **筹码峰分布（CYQ - Chip Yield Quantity）**：
     - 基于每日换手率与成交价格区间的三角分布推算持仓筹码沉淀。
     - 产出指标：`profit_ratio`（当前获利盘比例）、`cost_50`（筹码集中中枢）、`chip_concentration_90`（90%筹码集中度）。
     - 预警触发算子：`profit_ratio >= 0.90 AND chip_concentration_90 <= 0.10`（高控盘底部单峰密集突破）。
  2. **分时形态与量价异动算子**：
     - 分时放量突破：`intraday_volume / avg_5d_volume >= 2.5 AND current_price >= high_20d`。
     - 盘中回踩年线/均线假摔确认：`low <= ma_250 AND close >= ma_250 * 1.01`。
  3. **短线情绪周期引擎（Sentiment Engine）**：
     - 盘中跟踪全市场真实涨停数、连板梯队高度（最高板标的）、炸板率（$炸板数 / (封板数 + 炸板数)$）、封单金额衰减速率。
     - 产出全景情绪温度计：`冰点期` / `主升初期` / `高潮分歧` / `退潮期`，动态调节预警阀门敏感度。

#### 2.2 AIHOT 深度产业链推理升级 (Multi-Agent Reasoning)
- **定位**：告别浅层关键词提取，实现“事件定性 $\rightarrow$ 产业链映射 $\rightarrow$ 自选标的穿透”的自动化推理。
- **技术规范**：
  - **二段式流控架构**（兼顾毫秒级低延迟与大模型推理成本）：
    ```
    [7x24快讯/巨潮信披] 
          ↓ (第一道: 本地 BoundedIdSet + 正则关键词黑白名单粗筛, 淘汰 90% 无关噪音)
    [核心事件候选池 (10%)]
          ↓ (异步放入 asyncio.Queue 消费队列)
    [轻量 LLM 链 (本地 vLLM / DeepSeek-V3 / Flash API)]
          ↓
    [输出标准化 JSON 契约]
    ```
  - **Prompt 契约与结构化输出**：
    ```json
    {
      "event_impact_level": 4,
      "sentiment": "BULLISH",
      "chain_path": "上游锂矿供应收缩 -> 碳酸锂期货挺价 -> 中游磷酸铁锂正极加工费修复",
      "impacted_tickers": [
        {"code": "002466.SZ", "name": "天齐锂业", "relevance": "直接收益原料库存升值", "priority": "HIGH"},
        {"code": "300750.SZ", "name": "宁德时代", "relevance": "成本端波动影响中性偏弱", "priority": "MEDIUM"}
      ],
      "brief_summary": "某核心主产国宣布出口管制，短期现货供需错配加剧。"
    }
    ```

---

### 模块 3：底层框架、事件流与时序分析加速

#### 3.1 引入 vn.py 架构的高性能事件总线 (EventEngine)
- **定位**：解决 `web_server.py` 行情拉取、预警判定、外部推送耦合在一个异步大循环中导致的排队延迟。
- **技术规范**：
  - 基于 Python `asyncio.Queue` 实现约 150 行的高效进程内 EventBus：
    ```python
    class EventBus:
        def __init__(self):
            self._queue = asyncio.Queue(maxsize=10000)
            self._handlers = collections.defaultdict(list)
            
        def register(self, event_type: str, handler: Callable):
            self._handlers[event_type].append(handler)
            
        async def publish(self, event: Event):
            await self._queue.put(event)
            
        async def start(self):
            while True:
                event = await self._queue.get()
                handlers = self._handlers.get(event.type, [])
                for handler in handlers:
                    asyncio.create_task(handler(event))
                self._queue.task_done()
    ```
  - **解耦逻辑**：行情轮询器仅发布 `EVENT_TICK`；`AlertEngine`、`MacroCompass`、`PerspectiveWS` 各自独立订阅，杜绝单一耗时任务拖慢全局推送。

#### 3.2 借鉴 OpenBB Platform：数据源路由协议 (SPI) 插件化
- **定位**：重构 `stock_history_provider.py` 中繁杂交织的 if-else 降级代码，解耦取数、限流、洗数与业务逻辑。
- **设计模式**：
  - **统一数据模型 (Pydantic)**：定义 `StandardQuote`, `StandardKline`, `StandardValuation`。
  - **Provider 抽象基类**：
    ```python
    class BaseDataProvider(ABC):
        @abstractmethod
        async def fetch_quote(self, symbol: str) -> StandardQuote: pass
        @abstractmethod
        async def fetch_kline(self, symbol: str, interval: str) -> List[StandardKline]: pass

    class TuShareProvider(BaseDataProvider): ...
    class AkShareProvider(BaseDataProvider): ...
    class TencentProvider(BaseDataProvider): ...
    ```
  - **降级编排器 (Router with Fallback)**：支持通过装饰器自动完成重试、限流门控（东财令牌桶）与跨源自动 Fallback，并保留原系统的 **0.1% 除权首日价格漂移仲裁逻辑**。

#### 3.3 DuckDB 嵌入式时序分析引擎
- **定位**：与现有的 SQLite WAL 并存分工。SQLite 负责元数据与配置；DuckDB 负责全市场日K/分时历史数据的列式极速 OLAP。
- **技术规范**：
  - 单文件进程内运行（零运维），支持直接挂载读取现有 SQLite 表或 Parquet 文件。
  - **典型加速场景**：
    1. **历史估值分位数秒级计算**：计算自选股过去 1200 个交易日 PE 分位数时，DuckDB 向量化 SQL 仅需 3ms（原 Python 循环解析需 200ms+）。
    2. **板块联动截面相关性矩阵**：盘中实时计算 13 大题材领袖标的日内分时相关性系数，秒级输出协同拉升信号。

---

## 四、 综合实施路线图与阶段规划 (Roadmap & Milestones)

| 阶段 | 核心改造模块 | 关键交付物 | 预计耗时 | 改造难度 | 核心价值 / ROI |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Phase 1**<br>交互质变 | 前台 UI 与图表重构 | 1. 集成 `KLineChart` 替换抽屉 ECharts，支持画线与 PE/PB Band<br>2. 接入 `Gridstack.js` 允许自由拖拽分屏工作区 | 3~5 个工作日 | ★★☆☆☆ | **极高**：立即呈现专业量化交易终端质感，解决看盘分析交互体验。 |
| **Phase 2**<br>大脑升维 | 预警算子与情绪模型 | 1. 移植 InStock 筹码峰（CYQ）与放量突破算子至 `alert_engine.py`<br>2. 建立短线连板梯队与炸板率情绪量化面板 | 4~6 个工作日 | ★★☆☆☆ | **高**：直接赋能盘中实战短线交易，捕获高胜率右侧突破。 |
| **Phase 3**<br>智能情报 | AIHOT 产业链推理链 | 1. 建立基于 `BoundedIdSet` 的二段式快讯粗筛队列<br>2. 接入轻量 LLM 链输出标准产业链传导与自选股受影响评估 | 5~7 个工作日 | ★★★☆☆ | **高**：盘中上千条快讯自动降噪，实现“消息一出、标的秒定”。 |
| **Phase 4**<br>底座工业化 | 架构事件化与分析加速 | 1. 引入轻量 `EventEngine` 异步解耦全局事件流<br>2. 引入 `DuckDB` 加速历史分位数分析<br>3. 引入 `FINOS Perspective` 升级高频矩阵 | 7~10 个工作日 | ★★★☆☆ | **中-高**：彻底消除多自选并发卡顿隐患，奠定机构级扩展基础。 |

---

## 五、 技术风险、架构兼容性与兜底回滚策略

### 5.1 核心算法与金融纪律保持机制 (Non-Negotiable)
在实施过程中，以下现有核心能力必须保持 100% 行为一致，严禁被重构破坏：
1. **A/H 双市场独立交易日历与时钟**：必须严格保留跨午间休市、港股半日市、集合竞价细分及节假日交易日判定逻辑。
2. **除权首日跨周期日K主裁判**：对前复权价格漂移 >0.1% 的异动拦截仲裁逻辑必须无条件迁移至数据路由层。
3. **东财估值微秒级令牌桶**：针对东方财富估值接口的串行门控、最小间隔与抖动限流必须原样封装在 `EastMoneyProvider` 中，防止 IP 封禁。
4. **预警防轰炸持久化状态机**：冷冻期与单日触发上限记录必须保持在重启后可恢复。

### 5.2 兼容性降级与回滚方案
- **双图表引擎灰度开关**：在抽屉面板顶部保留一个轻量 Toggle 按钮（`[切换引擎: 专业KLineChart / 经典ECharts]`），若 WebAssembly 或新 Canvas 库在特定老旧客户端异常，可 0 秒无缝降级回退。
- **数据源路由熔断兜底**：若 Provider 插件出现解析异常，自动回退至现有的腾讯行情及 TuShare 原生直连接口，并在控制台打印来源告警。
- **SQLite 数据保全**：DuckDB 仅作为只读 OLAP 计算副本挂载，基础数据写入仍优先提交至 SQLite WAL，确保底层持久化绝对安全。

---

## 六、 供评审 AI 输出的评估模板 (Review Output Format)

请评审专家按照以下规范结构输出评审报告：

```markdown
# 评审报告：QUANT RADAR (Jiankong) 架构演进方案评估

## 1. 总体评价与架构评分
- 综合推荐指数：【强烈推荐 / 建议修改后实施 / 暂缓实施】
- 六维架构雷达评分（1-10分）：
  * 业务价值匹配度：X分
  * 技术选型合理性：X分
  * 性能改善确定性：X分
  * 架构扩展性：X分
  * 实施风险可控性：X分
  * 工程 ROI（投入产出比）：X分

## 2. 各维度专项技术审查
- 前台选型（KLineChart / Perspective / Gridstack）：...
- 量化算子与微观结构（InStock CYQ / 情绪周期）：...
- 智能情报链（二段式 LLM 推理）：...
- 底层事件总线与列存加速（vn.py / OpenBB / DuckDB）：...

## 3. 潜在架构隐患与盲区提醒 (Gotchas & Blindspots)
- （例如：Perspective WebAssembly 与现有 CSS 浅色主题样式的融合难度）
- （例如：DuckDB 进程并发读写与 SQLite WAL 之间的数据同步延迟）
- （例如：大模型盘中推理延迟对快讯推送即时性的影响）

## 4. 落地实施优化建议 (Top Actionable Recommendations)
1. ...
2. ...
3. ...
```

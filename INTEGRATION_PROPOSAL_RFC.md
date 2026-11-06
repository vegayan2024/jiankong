# QUANT RADAR (Jiankong) 架构演进与全栈整合实施方案 (RFC-2026-001 Rev.B)
> **标准工程实施规范与同行评审终版**  
> **基线版本**：QUANT RADAR (Jiankong) v2.2.0 (Commit `2cf88c5`)  
> **修订说明**：本版本（Rev.B）已全量吸收外部架构专家与量化审计智能体的评审意见，修复了初稿中的阶段依赖倒置、过度设计、PE/PB走廊口径错误、全市场情绪数据源缺失、事件总线异步死锁隐患，并严格界定了不可变金融领域层边界。

---

## 📌 修订对照表：评审意见吸收与架构矫正明细

| 审查维度 | 初稿缺陷 (Rev.A) | 终版修正方案 (Rev.B) | 影响与改进目标 |
| :--- | :--- | :--- | :--- |
| **阶段依赖** | Phase 1 做 Gridstack，Phase 4 才做 EventEngine，但前端却期望直接消费事件流；依赖倒置。 | **新增 Phase 0（基准压测与契约定义）**；先统一 WebSocket 批量帧契约与轻量解耦，再推进视图层。 | 消除阶段脱节，避免前台重写时后端数据契约未决。 |
| **前台选型** | Perspective WASM 对 186+ 自选股属于严重过度设计，存在包体大、Shadow DOM 浅色主题冲突等问题。 | **首选 Canvas / 虚拟滚动表格（Virtual Table）**，Perspective 降级为 Phase 4 可选分析插件，保留纯 DOM 回退。 | 避免引入百KB级 WASM 负担，零改造风险实现 60FPS 流畅滚动。 |
| **估值走廊** | 简单用“分位数反算价格走廊”，易重蹈前复权与未复权交叉合成的错误（违背 CHECK-03）。 | **严格遵循 $\text{Band}_p(t) = \text{EPS}_{ttm}(t) \times \text{PE分位}_p$**，复权口径全链路一致；非官方数据直接置空。 | 根除金融估值计算硬伤，确保估值带与日K同频可信。 |
| **量化算子** | CYQ 缺乏流通股本与换手率主备源；情绪周期缺少全市场涨跌停数据；自动改写预警阈值。 | **明确 TuShare/AkShare 流通股本接口**；情绪引擎建立**独立全市场低频令牌桶**；情绪指标仅作为独立看板，**严禁自动篡改四级门禁**。 | 守住预警防轰炸与冷却持久化底线，防范东财/同花顺封IP。 |
| **智能情报** | 宣称“秒定”不切实际；BoundedIdSet 误用作语义粗筛；无超时降级；主进程内阻塞。 | **BoundedIdSet 专职 FIFO 去重**；组合粗筛（权重+公告类+词典+正则）；**2秒超时硬降级**（先推原文，异步补推推理）；独立 Worker 进程。 | 保障盘中毫秒级电报推送，杜绝 LLM 延迟拖垮主服务。 |
| **事件总线** | `asyncio.create_task` 伴随 `task_done` 导致异常丢失、无背压、无合并；拓扑中前端直连进程总线。 | **修复异步事件死锁**；引入 **Tick 聚合与防拥塞合并（Coalescing）**；明确拓扑中总线经由 WS 批量分发器向前端广播。 | 消除消息堆积引发的内存暴涨与事件丢失隐患。 |
| **数据与存储** | 试图将除权仲裁迁入 Provider；DuckDB 与 SQLite 并发读写冲突风险。 | **确立不可变领域层（Domain Layer）**，仲裁/时钟/令牌桶绝对不动；**SQLite 唯一写入，DuckDB 仅化物化只读副本**。 | 100% 确保 12 项技术审计核验标准（CHECK-01~12）零回归。 |

---

## 一、 系统现状与不可变金融领域层边界 (Immutable Domain Boundary)

为确保系统升级过程绝对不破坏既有的金融工程严谨性，架构确立**“领域层守正、外围层出奇”**原则。以下模块与算法作为**核心领域层资产（Domain Core）**，严禁下放至数据 Provider，亦严禁被重构破坏：

```
┌────────────────────────────────────────────────────────────────────────┐
│                   不可变领域层核心资产 (Immutable Domain Core)            │
├──────────────────────────────────┬─────────────────────────────────────┤
│ 1. [CHECK-02] 除权漂移同日对齐与   │ 0.1% 严格漂移检测；日K主裁判连带清空   │
│    跨周期联动失效 (日K/周K/月K)     │ 月K/周K；7天新鲜度前置 maxd>=target   │
├──────────────────────────────────┼─────────────────────────────────────┤
│ 2. [CHECK-03] 严禁交叉合成估值    │ 严禁用前复权收盘价除以未复权财报指标  │
├──────────────────────────────────┼─────────────────────────────────────┤
│ 3. [CHECK-05/06] A/H 独立时钟      │ 港股 POS申报/阻断/CAS竞价细分；      │
│    与港股法定半日市状态机         │ 平安夜/大除夕半日市；独立港股休市日历 │
├──────────────────────────────────┼─────────────────────────────────────┤
│ 4. [CHECK-07] 东财微秒级令牌桶     │ 锁内仅领号，>15s 超时快速失败绝不推进  │
├──────────────────────────────────┼─────────────────────────────────────┤
│ 5. [CHECK-09] 严格 FIFO 去重队列   │ BoundedIdSet 纯严格 FIFO，杜绝LRU挤压 │
├──────────────────────────────────┼─────────────────────────────────────┤
│ 6. [CHECK-10] 单票状态机动态隔离   │ A/H 混排自选股逐票派发交易状态        │
└──────────────────────────────────┴─────────────────────────────────────┘
```

---

## 二、 修正后的目标系统架构拓扑 (Corrected Target Architecture)

```mermaid
flowchart TB
    subgraph Frontend [前端专业工作区 - Gridstack.js 弹性分屏]
        subgraph TopBar [顶部全局状态带]
            ClockBadge[A/H双市场独立时钟徽章]
            MacroBar[宏观罗盘: 国证2000 vs 沪深300]
        end
        subgraph PanelMain [核心盘口矩阵 - 二选一灰度]
            VirtualTable["[默认] 高性能虚拟滚动表格 (Canvas/Virtual DOM 60FPS)"]
            PerspectiveView["[实验] FINOS Perspective (WASM 深度透视沙盒)"]
        end
        subgraph PanelDrawer [个股深度全景抽屉 - 双引擎开关]
            KLineView["[主力] KLineChart (专业K线 / 画线 / 筹码峰 / 估值走廊)"]
            EChartsView["[备用] 经典 ECharts 5 图表 (零成本无缝回退)"]
        end
        subgraph PanelSide [战意情报侧边栏]
            SentimentCard[A股全市场情绪温度计: 连板/炸板/溢价]
            AlertStream[四级门禁预警流 (单票隔离 / 防轰炸持久化)]
            AIHOTStream[AIHOT 深度产业链推演流 (带原文引用与置信度)]
        end
    end

    subgraph ServerProcess [FastAPI 异步服务主进程]
        WS_Server[WebSocket 广播服务 - 端口 8765]
        WS_Dispatcher[WS 批量分发器: 300ms 批量聚合/旧Tick合并]

        subgraph EventBus [轻量异步事件总线 - asyncio.Queue 带背压]
            EVENT_TICK[EVENT_TICK 最新行情跳动]
            EVENT_ALERT[EVENT_ALERT 预警事件]
            EVENT_INTEL[EVENT_INTEL 突发高价值情报]
        end

        subgraph DomainEngines [不可变金融领域层]
            MarketSession[A/H 独立时钟与半日市引擎]
            AlertEngine[四级门禁预警引擎 (静态门限确定性)]
            CacheArbiter[KLineCache 仲裁者: 除权漂移0.1% / 日K主裁判]
            EastMoneyBucket[东财微秒级令牌桶: 锁内15s快速失败]
        end

        subgraph StorageCore [存储与物化副本]
            SQLiteWAL[(SQLite WAL: 唯一可写持久化库)]
            DuckDBRO[(DuckDB: 只读 Parquet 物化副本 / 盘后更新)]
        end
    end

    subgraph ExternalWorker [独立异步推理 Worker 进程]
        IntelQueue[高价值电报/公告有界消费队列]
        LLM_Engine["轻量 LLM 链 (本地 vLLM / DeepSeek API / 2s 超时熔断)"]
    end

    subgraph DataRouterSPI [数据路由 SPI 抽象层 (OpenBB 架构模式)]
        TencentProvider[腾讯高频接口: 65只切片批量拉取]
        TuShareProvider[TuShare Pro: 历史日K/复权因子/流通股本]
        AkShareProvider[AkShare: 备用历史K线/东财行情网关]
        SentimentGateway[全市场涨停池网关: 独立限流令牌桶]
    end

    %% 数据链路连接
    DataRouterSPI -->|Raw Tick| DomainEngines
    DomainEngines -->|合规校验通过| EVENT_TICK
    EVENT_TICK --> WS_Dispatcher
    WS_Dispatcher ==>|WS 批量帧 (JSON Array)| VirtualTable
    WS_Dispatcher -.->|可选帧推送| PerspectiveView

    EVENT_TICK --> AlertEngine
    AlertEngine -->|触发预警| EVENT_ALERT
    EVENT_ALERT --> WS_Server
    EVENT_ALERT --> PushNotifier[外部推送: 飞书/企业微信/PushPlus]

    DataRouterSPI -->|快讯与公告| IntelQueue
    IntelQueue --> LLM_Engine
    LLM_Engine -->|结构化推理结果| EVENT_INTEL
    EVENT_INTEL --> WS_Server

    SQLiteWAL -.->|盘后/定时增量物化| DuckDBRO
    DuckDBRO -->|只读加速横截面计算| KLineView
```

---

## 三、 核心专项技术设计与重构实施细节

### 1. 前台图表与布局工程设计

#### 1.1 KLineChart 替换与估值走廊（PE/PB Band）口径修正
- **双引擎灰度切换**：在个股抽屉顶部提供无缝切换开关：`[图表模式: 专业 KLineChart (默认) | 经典 ECharts]`，配置持久化至 `localStorage`。
- **估值走廊（PE/PB Band）金融口径规范**：
  - 严禁直接使用价格与分位数进行无量纲反算。
  - **标准计算公式**：
    $$\text{PriceBand}_{P}(t) = \text{EPS}_{\text{ttm}}(t) \times \text{Percentile}_{\text{PE}, P}$$
    $$\text{PriceBandPB}_{P}(t) = \text{BPS}(t) \times \text{Percentile}_{\text{PB}, P}$$
    其中 $P \in \{10\%, 25\%, 50\%, 75\%, 90\%\}$。
  - **复权连续性约束**：$\text{EPS}_{\text{ttm}}(t)$ 与 $\text{BPS}(t)$ 必须经由除权因子做前复权同向调整；若底层缺失标准化时序，走廊线**显式置空**，杜绝违背 CHECK-03。
- **Gridstack.js 协同与防抖**：
  - 统一通过单例 `ResizeObserver` 监听各 Grid Item 容器尺寸变动。
  - 引入 200ms 防抖函数触发 `chart.resize()`，杜绝拖拽时连续触发重绘导致掉帧。

#### 1.2 自选高频矩阵升级路径（实事求是替代 Perspective）
- **第一顺位方案（主推荐）**：采用轻量级 **虚拟滚动表格（Virtual Scroll Table）**（如基于纯原生 JS 实现的 30 行虚拟列表容器，或借助 Canvas 渲染网格）。
  - 内存占用 $< 2\text{MB}$（相比 Perspective 动辄 50MB+ WebAssembly 实例更加轻巧）。
  - 完美适配现有 Slate 浅色极简金融终端设计，无需 hack Shadow DOM。
- **第二顺位方案（保留为实验性沙盒）**：在独立路由页面（如 `/perspective`）中作为深度分析组件引入，不污染首页核心看盘链路。

---

### 2. 量化算子与微观结构引擎重构

#### 2.1 筹码峰分布（CYQ）数据落地与除权对齐
- **数据源绑定**：
  - 流通股本时序：首选 TuShare `daily_basic.float_share`，次选 AkShare `stock_individual_info_em`。
  - 换手率时序：严格对齐当日成交量与流通股本，日 K 线必须取自 `CacheArbiter` 仲裁后的干净前复权序列。
- **除权除息跳变处理**：当标的发生除权除息（检测到 0.1% 漂移并触发全量刷新）后，CYQ 算法从除权基准日向后至少重新衰减回溯 60 个交易日，防止历史陈旧筹码分布误导当前获利盘统计。
- **规则上线机制**：
  - CYQ 算子（如单峰密集、获利比例超 90%）先在预警流中开启**影子模式（Shadow Mode）**运行，仅记录日志并标注 `[SHADOW_ALERT]`，待实盘验证 2 周无虚警后转正为正式预警规则。

#### 2.2 A股短线情绪周期引擎与独立性原则
- **数据源隔离与独立令牌桶**：
  - 新增全市场 A 股涨停池、连板梯队（最高板）、炸板率、跌停池抓取通道。
  - 独立配置 `SentimentTokenBucket`，限制拉取频率不得高于 15 秒/次，严禁使用个股高频接口轮询全市场，杜绝 IP 被封禁。
- **市场规则硬隔离**：
  - 情绪周期引擎**严格限定于 A 股普通标的**（主板 10% 涨跌幅、双创 20%、北交所 30% 需单独归一化加权）。
  - **港股标的与指数标的彻底剔除**出情绪池，杜绝跨市场规则混淆。
- **预警门限绝对不可侵入原则**：
  - 情绪温度计（冰点/主升/分歧/退潮）仅作为前台独立看盘卡片，输出辅助决策建议。
  - **严禁后台自动动态调节用户的预警阈值**，保障四级门禁四阶段判定的绝对确定性。

---

### 3. AIHOT 智能情报链工业化（低延迟与防幻觉）

#### 3.1 严格二段式粗细过滤链路
```
[7x24快讯 / 巨潮法定信披]
       │
       ▼
【第 1 关：严格 FIFO 去重】 ── 命中已有 ID ──> [丢弃]
       │ (未命中，记录入 BoundedIdSet)
       ▼
【第 2 关：规则与实体粗筛】
  - 信源权重分 ≥ 7.0 分
  - 公告类型分类（定期报告 / 重大资产重组 / 中标 / 减持 / 处罚）
  - 行业核心词典与监控池实体正则命中
       │ (未通过: 仅作为普通快讯推入前端，不触发大模型)
       ▼ [核心高价值候选事件 (约 5~10%)]
【第 3 关：独立异步 Worker 队列】
```

#### 3.2 2 秒超时降级与防幻觉保障 (SLA & Guardrails)
- **2 秒超时熔断**：
  - 突发快讯进入队列后，启动 `asyncio.wait_for(llm_task, timeout=2.0)`。
  - 若 2.0 秒内 LLM 未返回，**立即按规则模板向客户端广播原文情报**；当后台 LLM 推理完成后，通过 WebSocket 发送 `EVENT_INTEL_UPDATE` 进行异步补充高亮。
- **防幻觉与主数据绑定**：
  - LLM 输出必须经过 Pydantic 校验，且提及的标的代码必须存在于系统 `SecurityMaster`（主数据字典）中，非法代码强制置空。
  - 输出中必须包含 `quote_snippet`（原文引用摘要，至少精确匹配原文 8 个连续字符），杜绝无中生有。

---

### 4. 底层架构事件化与分析加速

#### 4.1 健壮的轻量异步事件总线 (Corrected EventBus)
修正初稿中 `task_done` 与未收集异常的严重缺陷，提供安全可控的单进程异步总线：

```python
class RobustEventBus:
    def __init__(self, maxsize: int = 5000):
        self._queue = asyncio.Queue(maxsize=maxsize)
        self._handlers = collections.defaultdict(list)
        self._running = False
        
    def register(self, event_type: str, handler: Callable):
        self._handlers[event_type].append(handler)
        
    async def publish(self, event: Event):
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            # 背压策略: 队列满时丢弃旧Tick，保障系统不OOM
            if event.type == "EVENT_TICK":
                try:
                    _ = self._queue.get_nowait()
                    self._queue.put_nowait(event)
                except Exception:
                    pass
                    
    async def run(self):
        self._running = True
        while self._running:
            event = await self._queue.get()
            handlers = self._handlers.get(event.type, [])
            if handlers:
                # 显式异常捕获与隔离，避免单个 Handler 崩溃拖垮总线
                results = await asyncio.gather(
                    *(self._safe_call(h, event) for h in handlers),
                    return_exceptions=True
                )
                for res in results:
                    if isinstance(res, Exception):
                        logger.error(f"[EventBus] Handler execution failed: {res}")
            self._queue.task_done()
            
    async def _safe_call(self, handler, event):
        try:
            await handler(event)
        except Exception as e:
            return e
```

#### 4.2 数据层：SQLite 为主，DuckDB 只读物化
- **唯一真理源（Single Source of Truth）**：SQLite WAL 依然是系统中唯一的交易记录与行情持久化存储。
- **DuckDB 定位**：仅作为盘后或每 15 分钟异步生成的 Parquet 只读副本加速引擎，执行多因子横截面分位数计算。严禁对 DuckDB 和 SQLite 建立双向并发读写，彻底隔离锁竞争。

---

## 四、 修正后的演进路线图与准入准出门禁 (Revised Roadmap)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Phase 0: 基础设施底座与测试基线 (1~2天)                                         │
│ 目标: 固化性能基准、定义 WS 批量帧契约、将 CHECK-01~12 纳入自动化回归门禁          │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼──────────────────────────────────────┐
│ Phase 1: 看盘交互质变与走廊修复 (3~4天)                                        │
│ 目标: KLineChart 双引擎集成、修正 PE/PB 估值走廊口径、Gridstack 弹性分屏、虚拟表格  │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼──────────────────────────────────────┐
│ Phase 2: 量化微观结构与情绪引擎 (4~5天)                                        │
│ 目标: 引入流通股本源、CYQ 筹码峰影子模式、独立全市场情绪看板 (不可侵入预警门限)      │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼──────────────────────────────────────┐
│ Phase 3: 异步解耦与情报链升级 (5~6天)                                          │
│ 目标: 轻量 RobustEventBus 引入、独立 LLM Worker 进程、2秒超时降级与防幻觉校验   │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼──────────────────────────────────────┐
│ Phase 4: 分析加速与实验特性 (探索性)                                          │
│ 目标: DuckDB 只读 Parquet 物化加速、Perspective WASM 沙盒页面验证              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 阶段详细任务与准出标准 (Gate Criteria)

| 阶段 | 核心任务与改造范围 | 准入门槛 | 准出验证标准 (Exit Criteria) |
| :--- | :--- | :--- | :--- |
| **Phase 0**<br>基线与契约 | 1. 压测记录当前 Vanilla JS 内存与帧率基准<br>2. 定义 `/ws/live` 批量数据契约与 Feature Flags<br>3. 将 12 项技术审计核验放入 CI/自动化验证脚本 | 现有系统 v2.2.0 全部功能完好 | `python tests/test_audit_verification.py` 必须 100% 绿色通过；基准日志已归档。 |
| **Phase 1**<br>图表与布局 | 1. 引入 `klinecharts` 实现抽屉专业 K 线并提供 ECharts 一键切换开关<br>2. 按严格公式重写 PE/PB Band<br>3. 引入 Gridstack.js 与虚拟滚动表格 | Phase 0 契约已冻结 | 1. 十字光标极速联动无卡顿；<br>2. PE/PB Band 价格带与复权日 K 绝对吻合；<br>3. 切换经典 ECharts 100% 正常。 |
| **Phase 2**<br>算子与情绪 | 1. 接入 TuShare 流通股本时序与 CYQ 算子<br>2. 建立独立 A 股情绪周期看板（独立限流）<br>3. CYQ 预警采用影子模式打印 | Phase 1 图表已平稳上线 | 1. 情绪看板与 A/H 自选隔离正常，东财接口零超频告警；<br>2. 原四级门禁预警状态机零改变；<br>3. 影子模式日志记录正常。 |
| **Phase 3**<br>事件与情报 | 1. 在 `web_server.py` 引入 RobustEventBus 解耦 TICK 派发<br>2. 拆分独立 LLM 产业链推理 Worker<br>3. 落地 2 秒超时降级与 Pydantic 校验 | Phase 2 算子平稳运行 | 1. 高频行情下 EventBus 零内存泄露；<br>2. 突发快讯推迟 $\le 2\text{s}$，超时降级生效；<br>3. LLM 标的映射准确率 100% 命中 SecurityMaster。 |
| **Phase 4**<br>分析加速 | 1. 建立盘后 SQLite 到 Parquet 的物化同步任务<br>2. DuckDB 只读加速分位数计算<br>3. 在独立路由试运行 Perspective WASM | 前 3 个阶段均通过实盘验证 | 1. DuckDB 零写锁冲突；<br>2. 1200 日历史估值分位数计算耗时 $< 5\text{ms}$；<br>3. WASM 模块崩溃不影响主系统。 |

---

## 五、 实施安全性：回归测试与熔断机制

### 5.1 自动化审查回归集绑定
任何阶段的代码提交，必须由自动化测试脚本确保以下关键项不发生回退：
```bash
python tests/test_audit_verification.py
```
- **CHECK-01**：小微盘风格基准为国证2000 (`399303.SZ`)。
- **CHECK-02**：`overlap_drift > 0.001` 触发全量失效并连带清空周K/月K。
- **CHECK-03**：严禁交叉相除合成估值。
- **CHECK-05**：港股 12:10 半日市强制闭市。
- **CHECK-07**：东财令牌桶领号预订超过 15 秒快速失败抛错。
- **CHECK-09**：BoundedIdSet 严格 FIFO 不退化为 LRU。
- **CHECK-10**：单票独立派发 `is_stock_trading` 交易状态。

### 5.2 零停机回滚开关设计
系统核心配置文件中内嵌特性标志位（Feature Flags）：
```json
{
  "FEATURE_FLAGS": {
    "USE_KLINECHARTS": true,
    "ENABLE_GRIDSTACK": true,
    "ENABLE_SHADOW_CYQ": true,
    "ENABLE_EVENT_BUS": false,
    "ENABLE_LLM_INFERENCE": false,
    "USE_PERSPECTIVE_WASM": false
  }
}
```
任一新功能若在实盘中发生未捕获异常或内存暴涨，只需在配置文件中将对应特性置为 `false` 并重启服务，系统瞬间回退至原版经过完整审计的 v2.2.0 运行模式。

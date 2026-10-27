# QUANT RADAR (Jiankong) - 项目执行约束与规范 (CLAUDE.md)

本项目是 A股/港股/指数/ETF 全资产实时监控终端与高频情报雷达（FastAPI + WebSocket + 纯前端浅色泳道终端）。

---

## 1. 语言与交互规范
- **默认中文**：一律使用中文进行说明、分析与代码注释。
- **直击要点**：优先提供清晰直接的结论与改动说明，不进行冗长寒暄。
- **精准代码链接**：提及文件与符号时，必须给出具体的文件路径和行号范围。

---

## 2. 核心架构与技术栈约束
- **后端服务**：Python 3.10+、FastAPI、Uvicorn、WebSocket（高频推流与断线保活）、Pydantic（严格数据模型序列化）。
- **前端架构**：原生 HTML5 + Vanilla CSS（专业浅色主题、分组泳道布局、题材洞察卡片）+ Vanilla JS（WebSocket 客户端、AIHOT 事件流渲染、标的穿透联动）。
- **数据源规范**：
  - 行情：AkShare / 东方财富网 / 新浪行情实时轮询。
  - 法定信披第一信任源：巨潮资讯 (CNINFO)，支持 PDF 原文解析。
  - 题材情报与主线：同花顺问财 (iwencai.com) 标准题材洞察与全景透视。
  - 行业要闻：AIHOT 驱动的 25+ 权威行业信源及微信公众号矩阵（化工、有色、黄金、民爆等置顶）。

---

## 3. 本地专属技能库 (Local Skills)
项目内 `.agents/skills` 与 `.claude/skills` 已配备以下专用工程技能：
- **`fastapi-templates`**：FastAPI 异步生命周期（Lifespan）、WebSocket 连接管理、多线程行情采集解耦与优雅重启。
- **`pydantic-models-py`**：规范 186+ 监控标的行情、AIHOT 行业要闻、巨潮公告与异动预警的严格数据契约。
- **`kpi-dashboard-design`**：实时大屏泳道陈列、板块均幅动态计算、阈值告警触发与前端渲染防抖优化。
- **`pdf-official`**：针对巨潮公告/研报 PDF 页面关键表格与核心数据的无头快速抽取。
- **`risk-metrics-calculation`**：单票与板块波动率、最大回撤、异动偏离度等风控指标计算。

---

## 4. 浏览器与网页交互自动化优先顺位规范 (Browser & Web Automation Priority)
- **第一顺位（首选）· ego(lite)**：凡涉及网页操控、浏览器自动化、已登录状态下的后台/社媒/电商/SaaS系统交互、数据抓取与表单自动化任务，**无条件首选 `ego(lite)` 方案**（官方文档：`https://lite.ego.app/document/en/docs/windows/product-introduce`，配套 `ego-browser` 技能与本地环境）。
  - **核心机制**：优先通过 `ego-browser` 运行在独立 Space 工作区中，复用 Chrome 本地登录态、Cookie 与书签，使用高压缩率语义快照（Snapshot / Accessibility Tree）执行交互操作；遇到登录验证码或支付等风控节点支持用户在 Space 中无缝介入处理。
- **第二顺位（次选/降级）· huashu-chrome**：当 `ego(lite)` 环境未启动、不可用或特定任务需要时，**次选使用 `huashu-chrome` 技能与 MCP 工具集**。严格遵循「站点经验先行（learnings）」与「数据层网络优先（network > dom > pixels）」原则。
- **透明纪律**：若第一顺位不可用需要降级到第二顺位时，必须显式说明原因，禁止静默处置。

---

## 5. 数据客观性与完整性铁律
- **100% 客观保真**：严禁凭空创造和臆想数据；扫描不到新信息时如实输出空状态，绝不捏造假新闻或伪造涨跌幅。
- **配置持久化**：监控标的变更必须持久化写入 `watchlist.json`，外部推送信道变更必须持久化写入 `push_config.json`。

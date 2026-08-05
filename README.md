# TripOps Agent

TripOps 是一个面向多人、多约束旅行规划和行程中断恢复的 Agent 工程项目。它不以“生成一段攻略文本”为目标，而是输出带证据、可校验、可局部重规划的结构化旅行方案。

## 项目目标

- 使用 LangGraph 显式编排 Supervisor、Planner、Researcher 和 Verifier。
- 使用 Skills 渐进加载领域能力，避免把全部规则塞进 system prompt。
- 通过 Tool Registry 管理本地工具和 MCP 工具的能力、权限、风险、成本及降级路径。
- 通过 Middleware 实现上下文构建、结构化校验、超时重试、熔断、审计和人工审批。
- 使用稠密向量 + BM25 + RRF + rerank 构建可引用的 Hybrid RAG。
- 使用确定性代码验证预算、时间窗、开放时间、路线和多人偏好约束。
- 使用持久化 checkpoint 支持暂停审批、异常恢复和局部重规划。
- 使用 TravelPlanner 风格数据集评测约束满足率、工具选择、引用质量、延迟和成本。

## 架构原则

1. **LLM 负责候选，代码负责约束。** 硬约束不能只依赖 Critic Agent。
2. **主图负责控制，叶子 Agent 负责推理。** 长流程状态和恢复路径必须显式可见。
3. **工具最小暴露。** 每个节点只获得当前任务所需且当前用户有权使用的工具。
4. **证据与结论分离。** 原始工具结果进入 Artifact Store，主上下文只保留摘要和引用。
5. **所有降级可观察。** 重试、缓存命中、熔断和 fallback 都进入统一事件流。

## 演示场景

四位用户从上海前往关西旅行，成员在预算、饮食、寺庙与亲子活动上存在冲突。系统生成满足硬约束并兼顾公平性的计划。随后注入航班取消或暴雨事件，系统从 checkpoint 恢复，只重算受影响的交通和日程片段，并在改签前请求人工确认。

## 当前状态

- `M1` 已完成：工程骨架、领域契约、范围和架构决策。
- `M2` 已完成：Skills 渐进加载、动态工具注册、三套 MCP Mock Server、四层上下文和可靠性 Middleware。
- `M3` 已完成：Supervisor/Planner/并行 Researcher/Verifier 显式 LangGraph 控制流和自动重规划。
- `M4` 已完成：Hybrid RAG、RRF、rerank、citation、确定性约束校验与局部重规划。
- `M5` 已完成：SQLite checkpoint、跨进程审批恢复、异常降级和统一 JSONL Trace。
- `M6` 已完成：80 个 TravelPlanner 风格案例、故障注入和可复现效果报告。
- `M7` 已完成：异步 Run API、SSE trace、disruption 注入、审批恢复和交付文档。
- `M8` 已完成：南半球假日风格交互台、实时 Agent 状态、行程/证据可视化和一键中断演练。
- `M9` 已完成：真实 Researcher 工具、结构化 Candidate Builder 与 `Planner → Research → Candidate Build → Schedule → Verify` 调度链路。

当前所有工具调用均可统一经过权限、风险、审批、预算、缓存、超时、重试、熔断、fallback、Artifact 外置与事件追踪。范围和验收指标见 [docs/scope.md](docs/scope.md)，总体架构见 [docs/architecture.md](docs/architecture.md)，API 使用见 [docs/api.md](docs/api.md)。

## 本地开发

```bash
uv sync --extra dev
uv run ruff check src tests mcp_servers
uv run mypy src
uv run pytest --cov=tripops
uv run tripops-eval
uv run tripops-api
```

API 启动后：

- 打开 `http://127.0.0.1:9900/` 使用交互页面；填写旅行简报并点击“让 Agent 开始规划”。
- 行程生成后点击“模拟热带风暴”，观察 revision、原计划保留率和局部重规划结果。
- 打开 `http://127.0.0.1:9900/docs` 调试原始 API。

离线模式不需要模型 API Key，也不需要前端构建步骤；HTML、CSS 和原生 JavaScript 由 FastAPI 直接提供。生产接入参数见 `.env.example`。

### 真实研究数据源

`TRIPOPS_LIVE_RESEARCH_ENABLED=true` 时，Researcher 会通过受治理的 Tool Registry 调用：

| 数据源 | 用途 | 密钥 | 边界 |
| --- | --- | --- | --- |
| [Open-Meteo](https://open-meteo.com/en/docs) | 地理编码、天气预报 | 无需 | 仅请求可用预报窗口；超出时显式返回“不可预报” |
| [MediaWiki Action API](https://www.mediawiki.org/wiki/API%3ASearch/en) | 目的地附近 POI 与可引用页面 | 无需 | 提供地点候选，不代替票价/营业时间核验 |
| [Tavily Search](https://docs.tavily.com/documentation/api-reference/endpoint/search) | 交通、政策、餐饮、住宿和当前网页搜索 | `TRIPOPS_TAVILY_API_KEY` | 可选；未配置时对应 capability 显式降级 |

Candidate Builder 会把带 citation 的 `CandidateFact` 规范化成可排程候选。候选不足时仅补齐缺失时段，并在 plan metadata、trace 和页面标记 `real / mixed / fallback`，不将演示目录伪装为实时搜索结果。

## 可验证结果

- 97 个自动化测试，语句/分支综合覆盖率 88.84%。
- 约 9.36k 行有效 Python（源码、测试与 MCP Mock），仓库首个提交共 12k+ 行。
- 50 个标准约束、20 个动态变更、10 个故障注入案例。
- 基线中 labelled violation F1、citation correctness/freshness、局部影响召回、原计划保留和 fallback 成功率均为 100%。
- 评测指标只证明确定性工程链路，不将离线 fixture 结果包装成线上 LLM 效果；详见 [评测报告](docs/evaluation-report.md)。

## 目录

```text
src/tripops/
├── agents/          # Supervisor/Planner/Researcher/Verifier 与 LangGraph
├── api/             # Run API、SSE、disruption、approval 与交互页面
├── constraints/     # 硬约束校验和局部影响分析
├── context/         # runtime/state/memory/artifact/checkpoint
├── evaluation/      # 80-case benchmark、指标和故障探针
├── middleware/      # hooks、权限、预算、重试、熔断、fallback
├── models/          # OpenAI-compatible 模型工厂
├── planning/        # 候选目录、多人公平评分和约束感知调度
├── rag/             # ingestion/BM25/dense/RRF/rerank/citation
├── skills/          # 渐进加载的 Skill Registry
└── tools/           # Tool Registry 与 MCP discovery
```

默认 `TRIPOPS_AGENT_MODE=offline`，用于无模型密钥复现；真实 Researcher 与 Agent 模式独立开关。切换为 `llm` 后，Supervisor 与 Planner 使用 LangChain structured output；LLM 只提出路由和研究 DAG，研究完成后才由 Candidate Builder 和确定性 Scheduler 构造 itinerary，最后由 Verifier 裁决。

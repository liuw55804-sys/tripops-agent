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

API 启动后访问 `http://127.0.0.1:9900/docs`。离线模式不需要模型 API Key；生产接入参数见 `.env.example`。

## 可验证结果

- 53 个自动化测试，语句/分支综合覆盖率 89.25%。
- 50 个标准约束、20 个动态变更、10 个故障注入案例。
- 基线中 labelled violation F1、citation correctness/freshness、局部影响召回、原计划保留和 fallback 成功率均为 100%。
- 评测指标只证明确定性工程链路，不将离线 fixture 结果包装成线上 LLM 效果；详见 [评测报告](docs/evaluation-report.md)。

## 目录

```text
src/tripops/
├── agents/          # Supervisor/Planner/Researcher/Verifier 与 LangGraph
├── api/             # Run API、SSE、disruption、approval
├── constraints/     # 硬约束校验和局部影响分析
├── context/         # runtime/state/memory/artifact/checkpoint
├── evaluation/      # 80-case benchmark、指标和故障探针
├── middleware/      # hooks、权限、预算、重试、熔断、fallback
├── rag/             # BM25/dense/RRF/rerank/citation
├── skills/          # 渐进加载的 Skill Registry
└── tools/           # Tool Registry 与 MCP discovery
```

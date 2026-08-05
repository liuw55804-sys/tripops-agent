# 工程范围与验收标准

## 规模预算

| 模块 | 目标有效代码量 |
|---|---:|
| Agent 图与领域 Agent | 1,800–2,400 |
| Skills、上下文和工具控制面 | 1,300–1,800 |
| MCP、Middleware 与可靠性 | 1,300–1,800 |
| RAG、证据和约束求解 | 1,500–2,000 |
| API、配置与可观测性 | 700–1,000 |
| 测试、Mock 与离线评测 | 3,000–4,000 |
| 总计 | 10,000–13,000 |

代码量是范围护栏，不是质量指标。生成文件、锁文件、Markdown 知识文档和前端依赖不计入。

## 必须交付

### Agent 控制面

- Supervisor 可区分 `clarify`、`plan`、`research`、`verify`、`replan` 和 `finish`。
- Planner 生成带依赖关系的结构化任务 DAG。
- 独立 Researcher 可并行运行并使用隔离上下文。
- Verifier 返回机器可执行的 violation，不只输出自然语言批评。

### Skills 与工具

- 至少五个领域 Skill，支持摘要发现和按需加载。
- Tool Registry 支持 capability、风险、权限、延迟、成本和 fallback 元数据。
- 至少三个 MCP Server 或等价 Mock Server。
- 所有副作用工具必须通过人工确认。

### 上下文与持久化

- 运行时上下文、Graph State、长期记忆和 Artifact Store 分离。
- 大型工具结果自动外置，只向模型提供摘要和引用。
- SQLite checkpoint 支持进程重启后恢复。

### RAG 与约束

- BM25 与稠密向量多路召回，经 RRF 融合和 rerank。
- 最终事实包含 source、retrieved_at 和 citation id。
- 预算、时间冲突、开放时间、通勤时间和必选偏好由确定性校验器执行。
- 中断事件触发影响分析和局部重规划，而不是全量重跑。

### 可靠性与评测

- 工具支持 timeout、retry、cache、circuit breaker 和 fallback。
- Trace 能关联 run、agent、plan step、tool call、citation 和 violation。
- 单元测试覆盖率不低于 80%。
- 离线评测至少包含 50 个常规案例、20 个动态变更案例和 10 个故障注入案例。

## 明确不做

- 真实支付、出票或酒店订单写入。
- 自研向量数据库或通用工作流平台。
- 大型 React/Vue 前端；首版使用 API、SSE 和简洁的调试页。
- 直接部署 RAGFlow 等完整 RAG 平台。
- 为展示“多 Agent”而创建没有独立职责的角色。

## 里程碑

1. `M1`：领域模型、配置、API 骨架和验收用例。
2. `M2`：Skills、Tool Registry、MCP 和上下文基础设施。
3. `M3`：Supervisor/Planner/Researcher/Verifier 主图。
4. `M4`：Hybrid RAG、证据链、硬约束和局部重规划。
5. `M5`：持久化、HITL、可靠性中间件和 Trace。
6. `M6`：自动化测试、故障注入、离线评测和简历材料。

## 最终验收（2026-08-04）

- 100 个测试全部通过，覆盖率 89.35%，Ruff 与 strict mypy 通过。
- 80 个离线案例按 `50 standard / 20 dynamic / 10 fault` 固化。
- FastAPI 支持异步 run、状态查询、SSE trace、disruption 注入和 approval 恢复。
- 有效 Python 代码约 9.36k 行，接近 10k–13k 规模预算下界；未通过复制案例或增加无职责 Agent 凑行数。

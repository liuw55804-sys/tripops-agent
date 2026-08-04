# 完成交付审计

本表将目标中的每项显式要求映射到实现与可重复证据。统计日期：2026-08-04。

| 要求 | 实现证据 | 验证证据 |
| --- | --- | --- |
| Python、LangChain v1、LangGraph | `pyproject.toml`；`agents/graph.py`；`agents/llm.py` | strict mypy；Agent graph 与 structured-output tests |
| Supervisor / Planner / Researcher / Verifier | Agent Protocol、显式节点、并行 `Send`、结构化边界 | `test_agent_graph.py` 并行、重规划、trace tests |
| 多人多约束实际行程 | `planning/catalog.py`、`scoring.py`、`scheduler.py` | 6 项 scheduler tests；API 首次生成 15 items |
| Skills 渐进加载 | frontmatter-only discovery、`SkillSelectionPolicy`、按命中 body loader | 4 项 selector tests；API 返回 selected skills |
| MCP 与动态工具治理 | 三套 stdio MCP server、发现隔离、Tool Registry、GovernedToolResearcher | MCP smoke；registry、tool researcher tests |
| Hooks / Middleware | Skills、tool selection、model budget、governed execution middleware | `test_langchain_hooks.py`、`test_tool_execution.py` |
| 分层上下文 | Runtime、Graph State、SQLite Memory、Artifact Store、ContextCompiler | context/memory/artifact/compiler tests |
| 持久化 checkpoint | `AsyncSqliteSaver`、interrupt/Command resume | 关闭并重开 SQLite 后恢复审批的 integration test |
| Hybrid RAG | ingestion、BM25、hash dense/Milvus-compatible boundary、RRF、rerank | retrieval、ingestion、citation、researcher tests |
| 确定性硬约束 | budget/date/overlap/transit/opening/evidence/required/excluded/diet/access | constraint tests与 50 labelled standard cases |
| 局部纠偏 | Impact Analyzer、RepairScope、slot-preserving scheduler、缺失证据定向研究 | API disruption 保留 12/15 items；dynamic 20 cases |
| 超时异常降级 | timeout、retry、TTL cache、circuit breaker、fallback、artifact externalization | tool tests与 10 fault-injection cases |
| 人工审批 | risk metadata、approval interrupt、checkpoint resume API | approval graph/integration/API conflict tests |
| 全链路追踪 | run/agent/step/tool/citation/violation/approval/degradation schema、JSONL/SSE | trace tests与 API trace/SSE test |
| 离线评测 | 50 standard + 20 dynamic + 10 fault，JSON/Markdown report | `uv run tripops-eval`，labelled F1 等基线指标 |
| 可运行演示 | FastAPI async run、status、SSE、trace、disruption、approval；offline/llm modes | TestClient E2E 与真实 Uvicorn `/health` smoke |
| 质量门禁 | Ruff、strict mypy、pytest-cov、GitHub Actions | 86 tests；89.94% coverage；CI workflow |
| 代码规模 | 源码、测试与 MCP mock 约 9.36k 行；首提交 12k+ 总行 | `find ... -name '*.py' ... wc -l` |

## 指标边界

80-case 报告验证的是带标签的确定性约束、引用、影响分析和降级逻辑。它不等同于开放世界目的地推荐质量，也不宣称模型在 TravelPlanner 官方数据集上达到 100%。真实模型与供应商接入需另行记录模型版本、prompt、采样参数、数据许可、网络延迟和费用。

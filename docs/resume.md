# 简历与面试口径

## 推荐项目名称

**TripOps：多约束旅行规划与行程中断恢复 Agent**

## 简历表述

> 基于 Python、LangChain v1 与 LangGraph 构建 Supervisor/Planner/Researcher/Verifier 多智能体旅行运维系统；设计 Skills 渐进加载、MCP 动态工具治理与四层上下文，接入 Open-Meteo、Wikipedia 与可选 Tavily 真实研究工具，将带引用的 CandidateFact 在研究后构建为可排程候选；以确定性规则校验预算、时间窗、通勤、饮食及无障碍硬约束。实现 checkpoint 断点恢复、局部重规划以及 timeout/retry/cache/circuit-breaker/fallback 全链路降级；建立 80-case 离线评测与故障注入集，97 项测试覆盖率 88.84%。

## 面试时应主动限定的结论

- 100% 指标来自带标签的确定性离线 fixture，用于验证规则、引用和降级链路，不代表开放世界路线推荐质量。
- Dense Retriever 默认使用可离线复现的 hash embedding；安装 `rag` extra 后可替换为 Milvus 与 CrossEncoder，融合接口不变。
- Mock MCP 用来稳定复现 discovery、隔离失败和工具路由；Open-Meteo/Wikipedia 为真实只读数据源，Tavily 需单独配置密钥，仍需在生产环境补充配额和服务条款治理。
- 当前不执行支付和出票。所有高影响或财务动作只生成 ApprovalRequest，并由 checkpoint 暂停。

## 可深入追问的设计点

1. 为什么主控制流选择显式 LangGraph，而不是让通用 ReAct 自主循环。
2. 为什么 Researcher 只返回 Evidence，Verifier 只返回 Violation。
3. reducer、并行 `Send` 和 checkpoint 在重复执行时如何保证状态可恢复。
4. 工具注册信息如何同时参与 capability 路由、权限过滤、风险审批和 fallback。
5. 为什么硬约束由代码裁决，而偏好公平性使用 coverage 与 Jain index 评估。
6. disruption 如何通过时间窗口、同行者和 DAG 依赖传播，又如何保护 locked item。

# ADR-0001：使用显式 LangGraph 控制面

## 状态

Accepted

## 决策

主工作流使用自定义 LangGraph `StateGraph`。Supervisor、Planner、并行 Researcher、Verifier、人工审批和局部重规划作为显式节点或子图。LangChain `create_agent` 只用于需要工具推理循环的叶子节点。

## 原因

- 旅行规划是长流程强约束任务，需要持久化、暂停和恢复。
- 失败重试、局部重规划与全局预算必须由确定性控制面管理。
- 显式图便于测试每个路由条件，也便于面试时解释系统行为。
- 通用 Agent Harness 的 Skills、Middleware 和子 Agent 思想仍可复用，但不隐藏核心业务图。

## 代价

- 需要维护更多状态模型和图路由代码。
- LangChain/LangGraph 升级时要验证自定义节点边界。
- 必须避免把所有细节都堆进一个巨大 State。


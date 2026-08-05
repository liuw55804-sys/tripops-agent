# API 与演示

## 启动

```bash
uv sync --extra dev
uv run tripops-api
```

默认启用无密钥的 Open-Meteo 和 Wikipedia 真实研究工具。如需交通、政策、餐饮和住宿网页搜索，在 `.env` 配置 `TRIPOPS_TAVILY_API_KEY`。不需外网的确定性实验可设置 `TRIPOPS_LIVE_RESEARCH_ENABLED=false`。

交互演示台位于 `http://127.0.0.1:9900/`，可完成创建行程、查看 Agent 实时进度、浏览结构化结果，以及注入夏季风暴并观察局部重规划。页面直接调用下述 API，无需 Node.js 或前端构建步骤。

Swagger UI 位于 `http://127.0.0.1:9900/docs`。

## 创建任务

```bash
curl -sS http://127.0.0.1:9900/v1/runs \
  -H 'content-type: application/json' \
  -d '{
    "request": {
      "id": "demo-kansai",
      "origin": "Shanghai",
      "destinations": ["Kyoto", "Osaka"],
      "start_date": "2030-10-01",
      "end_date": "2030-10-05",
      "budget": "12000",
      "travelers": [{"id": "alice", "display_name": "Alice"}],
      "raw_requirement": "生成可解释的关西行程"
    }
  }'
```

返回的 `run_id` 是 API、LangGraph checkpoint 和 trace 的统一关联键：

```bash
curl -sS http://127.0.0.1:9900/v1/runs/$RUN_ID
curl -N http://127.0.0.1:9900/v1/runs/$RUN_ID/events
curl -sS http://127.0.0.1:9900/v1/runs/$RUN_ID/trace
```

## 注入行程中断

```bash
curl -sS -X POST http://127.0.0.1:9900/v1/runs/$RUN_ID/disruptions \
  -H 'content-type: application/json' \
  -d '{"event": {
    "id": "storm-1",
    "event_type": "severe_weather",
    "description": "台风导致铁路停运",
    "locations": ["Kyoto"],
    "required_capabilities": ["weather_search", "transport_search"]
  }}'
```

Supervisor 会从同一 checkpoint 重新进入，Impact Analyzer 计算最小修复范围，Planner 增加 revision，并只向匹配 capability 的 Researcher 分发任务。

## 审批恢复

副作用工具在图中通过 `interrupt()` 暂停。客户端确认后调用：

```bash
curl -sS -X POST http://127.0.0.1:9900/v1/runs/$RUN_ID/approval \
  -H 'content-type: application/json' \
  -d '{"decision":{"approved":true,"decided_by":"alice"}}'
```

审批只恢复 checkpoint 中等待的动作，不会重新执行此前已经完成的研究节点。

"use strict";

const $ = (selector) => document.querySelector(selector);
const state = { runId: null, run: null, trace: [], priorItemIds: new Set(), eventSource: null };

const phaseMeta = {
  intake: ["01", "Supervisor", "理解目标并归一化旅行约束"],
  plan: ["02", "Planner", "拆解任务并生成可执行日程"],
  research: ["03", "Researcher", "路由工具并收集带来源的证据"],
  verify: ["04", "Verifier", "检查预算、时间窗与硬约束"],
  replan: ["05", "Recovery", "计算影响范围并局部重规划"],
  finish: ["✓", "Response", "汇总可解释结果"],
};
const phaseOrder = ["intake", "plan", "research", "verify", "finish"];
const phaseProgress = { intake: 12, clarify: 18, plan: 35, research: 58, verify: 80, approval: 88, replan: 64, finish: 100, failed: 100 };
const phaseMessages = {
  intake: "Supervisor 正在提取目的地、预算与旅行偏好…",
  clarify: "正在判断是否需要补充旅行边界…",
  plan: "Planner 正在创建依赖清晰的任务图…",
  research: "Researcher 正在按能力选择工具并登记证据…",
  verify: "Verifier 正在执行确定性约束校验…",
  approval: "发现需要人工确认的高风险操作。",
  replan: "Recovery 正在锁定未受影响行程并修复其余部分…",
  finish: "规划完成：结果、证据和决策链均可追溯。",
  failed: "工作流未完成，请检查服务日志后重试。",
};

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function pad(value) { return String(value).padStart(2, "0"); }

function setDemoDates() {
  const now = new Date();
  const year = now.getMonth() >= 1 ? now.getFullYear() + 1 : now.getFullYear();
  $("#start-date").value = `${year}-01-18`;
  $("#end-date").value = `${year}-01-24`;
}

function toast(message) {
  const target = $("#toast");
  target.textContent = message;
  target.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { target.hidden = true; }, 4200);
}

function apiError(payload, fallback) {
  if (typeof payload?.detail === "string") return payload.detail;
  if (Array.isArray(payload?.detail)) return payload.detail.map((item) => item.msg).join("；");
  return fallback;
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(apiError(payload, `请求失败（${response.status}）`));
  return payload;
}

function renderAgents(phase = "intake", isReplan = false) {
  const list = $("#agent-list");
  list.replaceChildren();
  const sequence = isReplan ? ["intake", "replan", "research", "verify", "finish"] : phaseOrder;
  const currentIndex = Math.max(0, sequence.indexOf(phase));
  sequence.forEach((key, index) => {
    const meta = phaseMeta[key];
    const row = node("div", "agent-row");
    if (index < currentIndex || phase === "finish") row.classList.add("done");
    if (index === currentIndex && phase !== "finish") row.classList.add("active");
    row.append(node("span", "agent-icon", meta[0]));
    const copy = node("div");
    copy.append(node("h4", "", meta[1]), node("p", "", meta[2]));
    row.append(copy, node("span", "agent-status", index < currentIndex || phase === "finish" ? "done" : index === currentIndex ? "working" : "queued"));
    list.append(row);
  });
  $("#progress-bar").style.width = `${phaseProgress[phase] || 8}%`;
  $("#live-message").textContent = phaseMessages[phase] || "工作流正在推进…";
}

function showRunState(runId, isReplan = false) {
  $("#idle-state").hidden = true;
  $("#run-state").hidden = false;
  $("#run-badge").textContent = isReplan ? "恢复中" : "运行中";
  $("#run-badge").classList.add("active");
  $("#run-id").textContent = runId;
  renderAgents(isReplan ? "replan" : "intake", isReplan);
}

function formatMoney(value, currency = "CNY") {
  return new Intl.NumberFormat("zh-CN", { style: "currency", currency, maximumFractionDigits: 0 }).format(Number(value || 0));
}

function dateLabel(iso, index) {
  const date = new Date(`${iso.slice(0, 10)}T00:00:00`);
  const weekdays = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
  return { day: `DAY ${pad(index + 1)}`, date: `${date.getMonth() + 1}月${date.getDate()}日`, weekday: weekdays[date.getDay()] };
}

function categoryName(category) {
  return ({ meal: "餐饮", restaurant: "餐饮", food: "餐饮", transport: "交通", activity: "体验", accommodation: "住宿" })[category] || "行程";
}

function renderItinerary(plan, changedIds = new Set()) {
  const target = $("#itinerary");
  target.replaceChildren();
  const grouped = new Map();
  (plan.itinerary || []).forEach((item) => {
    const key = item.starts_at.slice(0, 10);
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key).push(item);
  });
  [...grouped.entries()].forEach(([date, items], index) => {
    const group = node("section", "day-group");
    const labelData = dateLabel(date, index);
    const label = node("div", "day-label");
    label.append(node("span", "", labelData.day), node("strong", "", labelData.date), node("small", "", labelData.weekday));
    const itemList = node("div", "day-items");
    items.forEach((item) => {
      const row = node("article", `itinerary-item${changedIds.has(item.id) ? " changed" : ""}`);
      row.append(node("span", "item-time", item.starts_at.slice(11, 16)), node("i", "item-dot"));
      const copy = node("div");
      copy.append(node("h4", "", item.title), node("p", "", `${item.location} · ${categoryName(item.category)}${item.required_transit_minutes ? ` · 转场 ${item.required_transit_minutes} 分钟` : ""}`));
      const costStatus = item.metadata?.cost_status;
      const costLabel = costStatus === "unknown" ? "待核价" : `${costStatus === "estimated" ? "约 " : ""}${formatMoney(item.cost, plan.currency)}`;
      row.append(copy, node("span", `item-cost${costStatus === "unknown" ? " warning" : ""}`, costLabel));
      itemList.append(row);
    });
    group.append(label, itemList);
    target.append(group);
  });
}

function renderMetrics(run) {
  const itinerary = run.plan?.itinerary || [];
  const dayCount = new Set(itinerary.map((item) => item.starts_at.slice(0, 10))).size;
  const unknownCount = Number(run.plan?.metadata?.unknown_cost_item_count || 0);
  const unpricedCapabilities = run.plan?.metadata?.unpriced_capabilities || [];
  const ledger = run.plan?.budget_ledger;
  const costValue = ledger
    ? `${formatMoney(ledger.total_low, ledger.currency)}–${formatMoney(ledger.total_high, ledger.currency)}${ledger.unpriced_kinds?.length ? " + 未覆盖" : ""}`
    : `${formatMoney(run.plan?.estimated_total_cost, run.plan?.currency)}${unknownCount || unpricedCapabilities.length ? " + 待核价" : ""}`;
  const values = [
    [String(dayCount), "旅行天数"],
    [String(itinerary.length), "已安排体验"],
    [costValue, ledger ? (ledger.unpriced_kinds?.length ? "已核价小计（非总预算）" : "完整网页报价预算区间") : unknownCount || unpricedCapabilities.length ? "已估支出（预算未闭合）" : "预计行程内支出"],
    [`${run.evidence?.length || 0}/${run.evidence?.length || 0}`, "建议引用证据"],
  ];
  const target = $("#metrics");
  target.replaceChildren();
  values.forEach(([value, label]) => {
    const metric = node("div", "metric");
    metric.append(node("strong", "", value), node("span", "", label));
    target.append(metric);
  });
}

function renderBudgetLedger(plan) {
  const target = $("#budget-ledger");
  target.replaceChildren();
  const ledger = plan?.budget_ledger;
  if (!ledger) {
    target.append(node("p", "ledger-empty", "本次运行没有形成结构化网页报价。"));
    return;
  }
  const total = node("div", "ledger-total");
  total.append(node("span", "", ledger.unpriced_kinds?.length ? "已核价小计 · 非总预算" : "完整预算区间"), node("strong", "", `${formatMoney(ledger.total_low, ledger.currency)}–${formatMoney(ledger.total_high, ledger.currency)}`));
  target.append(total);
  (ledger.components || []).forEach((component) => {
    const row = node("div", "ledger-component");
    const copy = node("div");
    copy.append(node("strong", "", component.label), node("small", "", component.note));
    row.append(copy, node("b", "", `${formatMoney(component.amount_low, component.currency)}–${formatMoney(component.amount_high, component.currency)}`));
    target.append(row);
  });
  const usableQuotes = (ledger.quotes || []).filter((quote) => quote.status !== "rejected").slice(0, 6);
  if (usableQuotes.length) target.append(node("p", "ledger-caption", "网页报价来源 · 点击核对实时库存"));
  usableQuotes.forEach((quote) => {
    const link = node("a", "quote-link");
    link.href = quote.source_uri;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.append(
      node("span", "", quote.title),
      node("b", "", `${formatMoney(quote.amount_low, quote.currency)}${quote.amount_high !== quote.amount_low ? `–${formatMoney(quote.amount_high, quote.currency)}` : ""} ↗`),
    );
    target.append(link);
  });
  if (ledger.unpriced_kinds?.length) target.append(node("p", "ledger-warning", `仍待核价：${ledger.unpriced_kinds.join("、")}`));
}

function renderConstraints(run) {
  const target = $("#constraint-health");
  target.replaceChildren();
  const candidateMode = run.plan?.metadata?.candidate_source_mode || "fallback";
  const candidateStatus = {
    real: ["真实候选来源", false, "✓ 实时"],
    mixed: ["真实候选来源", true, "部分补位"],
    fallback: ["真实候选来源", true, "演示降级"],
  }[candidateMode] || ["真实候选来源", true, candidateMode];
  const budgetExceeded = run.violations?.some((v) => v.code === "budget_exceeded");
  const budgetUnverified = run.violations?.some((v) => v.code === "budget_unverified");
  const rows = [
    ["预算上限", budgetExceeded || budgetUnverified, budgetExceeded ? "已超支" : budgetUnverified ? "待核价" : "✓ 通过"],
    ["日程无冲突", run.violations?.some((v) => ["time_overlap", "date_out_of_range"].includes(v.code)), null],
    ["证据完整性", run.violations?.some((v) => ["evidence_missing", "stale_evidence"].includes(v.code)), null],
    ["硬约束校验", run.violations?.some((v) => v.severity === "error"), null],
  ];
  rows.forEach(([label, failed, explicitStatus]) => {
    const row = node("div", "health-row");
    const status = node("b", failed ? "warning" : "", explicitStatus || (failed ? "需关注" : "✓ 通过"));
    row.append(node("span", "", label), status);
    target.append(row);
  });
  const sourceRow = node("div", "health-row");
  sourceRow.append(
    node("span", "", candidateStatus[0]),
    node("b", candidateStatus[1] ? "warning" : "", candidateStatus[2]),
  );
  target.append(sourceRow);
  (run.violations || []).slice(0, 2).forEach((violation) => {
    const row = node("div", "health-row");
    row.append(node("span", "", violation.message), node("b", "warning", violation.severity));
    target.append(row);
  });
}

function renderEvidence(run) {
  const skills = $("#skills");
  skills.replaceChildren();
  const sourceLabels = { real: "候选: 真实来源", mixed: "候选: 真实 + 补位", fallback: "候选: 演示降级" };
  const sourceMode = run.plan?.metadata?.candidate_source_mode;
  if (sourceMode) skills.append(node("span", "skill-chip", sourceLabels[sourceMode] || `候选: ${sourceMode}`));
  (run.selected_skills || []).forEach((skill) => skills.append(node("span", "skill-chip", skill)));
  if (!run.selected_skills?.length) skills.append(node("span", "skill-chip", "deterministic-planning"));
  const target = $("#evidence");
  target.replaceChildren();
  (run.evidence || []).slice(0, 4).forEach((evidence) => {
    const row = node("div", "evidence-row");
    row.append(node("p", "", evidence.claim));
    const source = evidence.source_uri ? node("a", "evidence-source", evidence.source_name) : node("span", "", evidence.source_name);
    if (evidence.source_uri) {
      source.href = evidence.source_uri;
      source.target = "_blank";
      source.rel = "noreferrer";
    }
    const meta = node("small");
    meta.append(source, document.createTextNode(` · confidence ${Math.round(evidence.confidence * 100)}%`));
    row.append(meta);
    target.append(row);
  });
}

function renderTrace(events) {
  const target = $("#trace-list");
  target.replaceChildren();
  events.forEach((event) => {
    const item = node("li");
    const time = event.occurred_at ? new Date(event.occurred_at).toLocaleTimeString("zh-CN", { hour12: false }) : "--:--:--";
    item.append(node("strong", "", `${event.name} `), node("span", "", `${event.kind} · ${event.status} · ${event.duration_ms ? `${event.duration_ms.toFixed(1)}ms` : time}`));
    target.append(item);
  });
  $("#trace-count").textContent = `${events.length} events`;
}

function renderResults(run, changedIds = new Set(), preserved = null) {
  state.run = run;
  $("#results").hidden = false;
  $("#revision").textContent = `R${run.plan.revision}`;
  $("#updated-time").textContent = `${new Date(run.updated_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })} 更新`;
  const resultDays = new Set(run.plan.itinerary.map((item) => item.starts_at.slice(0, 10))).size;
  $("#results-summary").textContent = `${resultDays} 天行程已生成 · ${run.plan.itinerary.length} 项安排 · ${run.violations?.length ? `${run.violations.length} 项约束提醒` : "所有硬约束通过"}。`;
  $("#preservation-note").textContent = preserved === null ? "确定性约束已验证" : `局部重规划 · 保留 ${preserved}%`;
  renderMetrics(run);
  renderItinerary(run.plan, changedIds);
  renderBudgetLedger(run.plan);
  renderConstraints(run);
  renderEvidence(run);
  $("#run-badge").textContent = "已完成";
  $("#run-badge").classList.remove("active");
  renderAgents("finish", run.plan.revision > 1);
}

async function loadTrace() {
  if (!state.runId) return;
  try {
    const trace = await requestJson(`/v1/runs/${state.runId}/trace`);
    state.trace = trace.events || [];
    renderTrace(state.trace);
  } catch (error) {
    console.warn("Trace unavailable", error);
  }
}

async function processRun(run) {
  state.run = run;
  const phase = run.phase || (run.status === "completed" ? "finish" : "intake");
  renderAgents(phase, (run.plan?.revision || 1) > 1);
  $("#trace-count").textContent = `${run.trace_event_count || 0} events`;
  if (run.status === "completed" && run.plan) {
    renderResults(run);
    await loadTrace();
    return true;
  }
  if (run.status === "completed") throw new Error("工作流异常结束：后端未返回可用计划");
  if (run.status === "failed") throw new Error(run.error || "Agent 工作流执行失败");
  return run.status === "waiting_approval";
}

async function pollRun(runId) {
  for (let attempt = 0; attempt < 80; attempt += 1) {
    const run = await requestJson(`/v1/runs/${runId}`);
    if (await processRun(run)) return run;
    await new Promise((resolve) => setTimeout(resolve, 180));
  }
  throw new Error("等待 Agent 响应超时");
}

function subscribeToEvents(runId) {
  state.eventSource?.close();
  if (!("EventSource" in window)) return;
  const source = new EventSource(`/v1/runs/${runId}/events`);
  state.eventSource = source;
  ["graph_node", "agent", "plan_step", "tool_call", "citation", "violation", "degradation"].forEach((kind) => {
    source.addEventListener(kind, (event) => {
      try {
        const traceEvent = JSON.parse(event.data);
        if (!state.trace.some((item) => item.event_id === traceEvent.event_id)) state.trace.push(traceEvent);
        renderTrace(state.trace);
        const phase = traceEvent.attributes?.next_phase || phaseFromTraceName(traceEvent.name);
        if (phaseMeta[phase]) renderAgents(phase);
        else $("#live-message").textContent = `${traceEvent.name} · ${traceEvent.status}`;
      } catch (error) { console.warn("Invalid SSE event", error); }
    });
  });
  source.addEventListener("run_status", async (event) => {
    try {
      await processRun(JSON.parse(event.data));
    } catch (error) {
      console.warn("Terminal run status", error);
    } finally {
      source.close();
    }
  });
  source.onerror = () => source.close();
}

function phaseFromTraceName(name) {
  if (name === "planner") return "plan";
  if (name.startsWith("researcher:")) return "research";
  if (name === "verifier") return "verify";
  if (name === "impact_analyzer") return "replan";
  if (name === "finalizer") return "finish";
  return null;
}

function formPayload() {
  const form = new FormData($("#trip-form"));
  const preferences = form.getAll("preference");
  const rawRequirement = String(form.get("requirement") || "").trim();
  const destinations = String(form.get("destination") || "").split(/\s*(?:、|，|;|→|->|\n)\s*/).filter(Boolean);
  const travelerCount = Math.max(1, Number(form.get("traveler_count") || 1));
  const id = `southbound-${Date.now()}`;
  return {
    request: {
      id,
      origin: String(form.get("origin")),
      destinations,
      start_date: String(form.get("start_date")),
      end_date: String(form.get("end_date")),
      currency: "CNY",
      budget: String(form.get("budget")),
      travelers: Array.from({ length: travelerCount }, (_, index) => ({ id: `traveler-${index + 1}`, display_name: `旅行者 ${index + 1}`, preferences })),
      constraints: [],
      raw_requirement: rawRequirement,
    },
    user_message: `${preferences.join("、")}。${rawRequirement}`,
  };
}

async function startPlanning(event) {
  event.preventDefault();
  const button = $(".submit-button");
  button.disabled = true;
  button.querySelector("span").textContent = "Agent 正在接管旅程…";
  $("#results").hidden = true;
  state.trace = [];
  try {
    const started = await requestJson("/v1/runs", { method: "POST", body: JSON.stringify(formPayload()) });
    state.runId = started.run_id;
    showRunState(started.run_id);
    subscribeToEvents(started.run_id);
    await pollRun(started.run_id);
    toast("行程已生成，请查看预算待核价项与候选来源。");
    $("#results").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    $("#run-badge").textContent = "执行失败";
    $("#run-badge").classList.remove("active");
    toast(error.message);
  } finally {
    button.disabled = false;
    button.querySelector("span").textContent = "让 Agent 开始规划";
  }
}

async function simulateStorm() {
  if (!state.runId || !state.run?.plan) return;
  const button = $("#storm-button");
  const feedback = $("#storm-feedback");
  const oldIds = new Set(state.run.plan.itinerary.map((item) => item.id));
  const first = state.run.plan.itinerary[0];
  const start = new Date(first.starts_at);
  const end = new Date(start.getTime() + 3 * 60 * 60 * 1000);
  button.disabled = true;
  feedback.textContent = "已收到气象预警，正在计算影响半径…";
  showRunState(state.runId, true);
  try {
    await requestJson(`/v1/runs/${state.runId}/disruptions`, {
      method: "POST",
      body: JSON.stringify({ event: {
        id: `summer-storm-${Date.now()}`,
        event_type: "severe_weather",
        description: "夏季强对流天气影响户外活动与区域交通",
        starts_at: start.toISOString(),
        ends_at: end.toISOString(),
        locations: [first.location],
        affected_item_ids: [first.id],
        required_capabilities: ["weather_search", "transport_search"],
      } }),
    });
    subscribeToEvents(state.runId);
    const revised = await pollRun(state.runId);
    const newIds = new Set(revised.plan.itinerary.map((item) => item.id));
    const preserved = [...oldIds].filter((id) => newIds.has(id)).length;
    const percentage = Math.round((preserved / Math.max(1, oldIds.size)) * 100);
    const changed = new Set([...newIds].filter((id) => !oldIds.has(id)));
    renderResults(revised, changed, percentage);
    feedback.textContent = `恢复完成：保留 ${preserved}/${oldIds.size} 项，仅重排受影响时段。`;
    toast(`局部重规划完成，原行程保留率 ${percentage}%。`);
  } catch (error) {
    feedback.textContent = error.message;
    toast(error.message);
  } finally {
    button.disabled = false;
  }
}

function loadDemo() {
  $("#origin").value = "上海";
  $("#destination").value = "新西兰皇后镇";
  $("#budget").value = "18000";
  $("#traveler-count").value = "1";
  $("#requirement").value = "每天不要安排太满，保留看日落的时间；优先公共交通。";
  setDemoDates();
  $("#planner").scrollIntoView({ behavior: "smooth" });
  toast("皇后镇盛夏示例已载入。 ");
}

setDemoDates();
$("#trip-form").addEventListener("submit", startPlanning);
$("#load-demo").addEventListener("click", loadDemo);
$("#storm-button").addEventListener("click", simulateStorm);

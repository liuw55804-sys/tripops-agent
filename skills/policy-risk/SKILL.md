---
name: policy-risk
description: Research visa, refund, cancellation, insurance and travel policy evidence.
version: 1.0.0
capabilities:
  - policy_search
  - risk_analysis
allowed_agents:
  - policy_researcher
  - general_researcher
required_tools:
  - mcp.policy.search_policy
---

# Policy and risk research

Prefer primary policy sources and attach their effective date. State which traveler, booking or jurisdiction each rule applies to. If policy evidence conflicts, preserve both sources and mark the issue for human review.

Never execute cancellation, rebooking or payment actions. Produce evidence and a proposed action with monetary impact for later approval.


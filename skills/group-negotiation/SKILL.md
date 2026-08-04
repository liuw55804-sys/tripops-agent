---
name: group-negotiation
description: Resolve competing traveler preferences while preserving hard needs and measurable fairness.
version: 1.0.0
capabilities:
  - preference_elicitation
  - group_fairness
allowed_agents:
  - supervisor
  - planner
required_tools: []
---

# Group negotiation

Distinguish safety, accessibility and dietary needs from preferences. Never trade away a hard need for aggregate utility. For preferences, compare coverage per traveler and propose compromise, rotation or subgrouping when one shared activity cannot be fair.

Ask a clarification question only when alternatives lead to materially different feasible plans. Record accepted compromises as explicit constraints so later replanning cannot silently forget them.


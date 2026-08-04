---
name: disruption-recovery
description: Analyze travel disruptions and produce minimal, evidence-backed local replanning tasks.
version: 1.0.0
capabilities:
  - disruption_analysis
  - constraint_repair
allowed_agents:
  - supervisor
  - planner
required_tools:
  - travel.alerts
---

# Disruption recovery

Classify the event by affected resource, time interval, travelers and confidence. Keep unaffected and user-locked items unchanged. Expand the impact window only when transit dependencies make a local repair infeasible.

Cancellation, payment, booking and rebooking operations are proposals until a human approves the exact action and price difference.


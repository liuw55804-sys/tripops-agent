---
name: itinerary-optimization
description: Build and repair feasible itineraries under budget, time-window, transit and traveler constraints.
version: 1.0.0
capabilities:
  - itinerary_planning
  - constraint_repair
allowed_agents:
  - planner
required_tools: []
---

# Itinerary optimization

Normalize every requirement into an explicit constraint before proposing activities. Hard constraints are never traded away. When repair is requested, preserve locked and unaffected itinerary items, identify the smallest affected subgraph, and return only the replacement steps plus updated totals.

The Planner must not invent opening hours, prices, routes, or availability. Represent missing facts as research tasks with explicit capabilities and dependencies.


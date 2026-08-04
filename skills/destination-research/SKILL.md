---
name: destination-research
description: Collect fresh, attributable facts about destinations, stays, attractions and local transit.
version: 1.0.0
capabilities:
  - poi_search
  - accommodation_search
  - local_transport_search
allowed_agents:
  - general_researcher
  - poi_researcher
required_tools: []
---

# Destination research

Turn each missing fact into the narrowest possible query. Preserve source URI, retrieval time, validity time, price currency and any conditions attached to availability. Separate observations from derived conclusions.

Do not treat snippets or stale cached pages as sufficient evidence for consequential decisions. Return `Evidence` records and unresolved questions, never a finished itinerary.


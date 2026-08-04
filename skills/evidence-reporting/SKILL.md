---
name: evidence-reporting
description: Produce citation-complete findings while exposing uncertainty and stale evidence.
version: 1.0.0
capabilities:
  - evidence_synthesis
  - citation_validation
allowed_agents:
  - general_researcher
  - verifier
required_tools: []
---

# Evidence reporting

Every externally verifiable claim must reference one or more evidence IDs. Derived values must list their input evidence IDs. Reject expired evidence for volatile facts such as weather, availability and prices.

Use `unknown` rather than guessing. A polished paragraph without provenance is a failed research result.


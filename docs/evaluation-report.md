# TripOps Offline Evaluation

- Suite: `tripops-travelplanner-v1`
- Generated at: `2026-08-04T14:57:35.086269+00:00`
- Cases: **80**

## Headline metrics

| Metric | Result |
| --- | ---: |
| Labelled violation precision | 100.0% |
| Labelled violation recall | 100.0% |
| Labelled violation F1 | 100.0% |
| Preference coverage | 100.0% |
| Group fairness (Jain index) | 100.0% |
| Citation correctness | 100.0% |
| Citation freshness | 100.0% |
| Dynamic affected-item recall | 100.0% |
| Original-plan preservation | 100.0% |
| Fault degradation success | 100.0% |
| Mean local evaluation latency | 0.35 ms |

## Breakdown

| Category | Cases | Violation F1 | Citation correctness | Affected recall | Preservation | Degradation |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| standard | 50 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| dynamic | 20 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| fault | 10 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |

## Interpretation

The suite validates deterministic constraint detection against labelled failures, citation integrity and freshness, multi-traveler preference fairness, local impact analysis, and middleware fallback under injected provider faults. The plan feasibility rate is intentionally not a model-quality claim: labelled cases include invalid plans so the verifier's precision and recall can be measured.

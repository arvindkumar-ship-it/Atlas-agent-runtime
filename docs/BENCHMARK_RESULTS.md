# Included measurements

84 actual fixture workflows; 12 per mode. See [method and limitations](BENCHMARKS.md).

| Mode | Success | Median ms | P95 ms | Input tokens, estimated per workflow | Context reduction, estimated |
| --- | ---: | ---: | ---: | ---: | ---: |
| `atlas_context` | 100% | 1088.2 | 1423.2 | 14174.5 | 4.37% |
| `atlas_full` | 100% | 1062.5 | 1481.8 | 14174.5 | 4.37% |
| `atlas_runtime` | 100% | 1060.2 | 1385.8 | 14174.5 | 4.37% |
| `full_history` | 100% | 1145.6 | 1602.6 | 14822.0 | 0.00% |
| `keyword_memory` | 100% | 1087.5 | 1428.0 | 14822.0 | 0.00% |
| `recent_window` | 100% | 1073.1 | 1576.5 | 14323.5 | 3.36% |
| `summary_memory` | 100% | 1123.1 | 1722.1 | 13890.5 | 6.28% |

All modes passed these deterministic fixtures. Context savings here are small; longer model-backed tasks remain an independent evaluation. Model usage and GPU memory were unavailable, so no GPU speedup or real-model accuracy is claimed.

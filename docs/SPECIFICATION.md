# PDF implementation decisions

The supplied 355-page PDF develops several earlier concepts before settling on **Atlas Agent Runtime**. This repository implements the final coding-agent deliverable and its educational inference/reference components. Earlier proposals for unrelated infrastructure products are design history rather than additional apps.

The PDF's code is incremental and later corrects several interfaces. This delivery consolidates those steps into one consistent package rather than retaining conflicting versions.

| Final requirement | Implementation | Boundary |
| --- | --- | --- |
| Repository + task intake | API ZIP import, configured repository root, CLI | Python/pytest repositories; bounded archive size |
| Durable run/event store | SQLAlchemy SQLite/PostgreSQL, hash chain, pure reducer | Hash chain detects accidental mutation, not a database administrator rewriting history |
| Checkpoints and restart | Materialized projection, pending actions, lease generation | Requires durable workspace/artifact storage |
| Plans and parallel tools | DAG validation; parallel read-only inspection | Side effects remain sequential |
| Safe repository operations | Typed registry, path guards, secret exclusions | No generic shell or dependency installation |
| Patch approval | Exact run/action digest, durable pending approval | Shared operator identity |
| Tests and verification | Restricted Docker pytest; preserved tests; regression test; AST checks | AST checks are bounded rules, not a complete security proof |
| Structured memory | Source-linked typed observations and contradictions | Run-scoped, lexical retrieval; no vector DB |
| Context compiler | Pinned policy/goal/plan, seven comparable modes | Token counts are explicitly estimated |
| Local/cloud inference | Compatible streaming `/v1/chat/completions` adapter | Requires external model weights/server or credentials |
| Runtime scheduling | Bounded priority queue, cancellation, concurrency, KV estimates | In-process admission, not GPU continuous batching |
| Prefix reuse | Stable policy identity and hit/miss telemetry | Does not claim server cached-token hits |
| KV accounting | Model geometry estimate and budget admission | GPU allocations remain backend-owned |
| KV paging/offload | CPU reference pages, fork/COW, spill/restore | Educational arrays; not vLLM GPU page tables |
| Session park/resume | Durable state while tools/approval wait | No unsupported slot save/restore API calls |
| Speculative decoding | Explicit policy hook | Server implementation/configuration is external |
| Model routing | Optional small served model on the same endpoint | No invented quality estimator or trained router |
| Transformer from scratch | NumPy + optional trainable PyTorch reference | Random weights and toy training, not a production planner |
| Telemetry | Prometheus histograms/counters and optional OTLP spans | Actual unavailable metrics remain null |
| Console and benchmarks | React console, four real fixtures, seven modes, measured JSONL/CSV | Fixture results do not establish LLM quality gains |
| Deployment | Docker, PostgreSQL compose, host worker services, Render demo blueprint, CI | Hosting/model credentials supplied by the owner |

## Changes needed to make the tutorial operational

Event sequence allocation and projection changes are transactional. Queue jobs are actually leased and fenced rather than an in-memory pending list. Approval is tied to the exact action. Workspace locks and patch journals cover repeated/crashed writes. Patch application refuses fuzzy hunks and modified original tests. Uploaded ZIPs cannot write outside the repository or introduce symlinks. Process execution uses an argv list with fixed pytest flags, bounded output and process-group cleanup.

Completion requires independent checks. The agent's narrative is evidence-bearing output, not authority to declare success. Native tool-call stream fragments are reassembled and then validated through the same tool contracts as JSON decisions.

The historical `v0.1`–`v1.0` sequence in the PDF is a learning roadmap. This archive does not fabricate old commits or tags. The implementation map above identifies the final code to study in that order.

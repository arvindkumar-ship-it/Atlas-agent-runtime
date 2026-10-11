# Execution and recovery

The database is the source of truth for run state. Filesystem artifacts hold the isolated repository and immutable baseline. The inference server owns its model and GPU cache.

## A run

1. `Store.create` atomically inserts the run, `run.created` event and queue job. A client idempotency key is bound to the task body.
2. A worker claims a job, increments its generation and renews its lease. Every worker event/effect transaction checks the current owner, generation and expiry.
3. A process lock serializes workspace access across workers on the same host. The original repository is copied with file/count/size limits; symlinks and secret paths are excluded. Its text baseline is stored outside the workspace.
4. The compiler pins system policy, the goal, constraints, plan and contradictions. It selects source-linked observations within an explicit estimated input budget.
5. The model returns a strict `Decision`. A validated plan creates a dependency graph. Read-only actions may run together; side effects run serially.
6. Proposed actions become durable events before execution. An exact patch either has prior workspace authorization or waits for a patch-specific approval.
7. A tool result and its idempotency effect record commit together. Observations keep the event ID as their evidence source.
8. A final answer must cite existing tool evidence. The runtime independently reruns tests, checks Python AST rules, preserves original tests, requires an added regression test for code changes and captures a diff/fingerprint.
9. Only a passed verification report allows `run.completed`.

## Crash boundaries

| Boundary | Recovery |
| --- | --- |
| Before job claim | Another worker claims the queued job |
| After claim, before effects | Expired lease is reclaimed with a new generation |
| Model request before result commit | The call may be repeated; external billing is not exactly once |
| Proposed action before execution | Pending action is reconstructed from events |
| During multi-file patch writes | Journal accepts only known before/after versions and finishes the same patch |
| After patch, before result commit | Journal makes repeating the same patch safe |
| After effect/result commit | The effect record prevents duplicate logical completion |
| Waiting for approval | Exact action and approval remain in the database; approving queues the job |
| Worker process dies while holding file lock | The operating system releases the lock |
| Workspace disk lost | Restore the workspace/artifact backup; event replay alone cannot recreate external state |

Retries are bounded. Transient transport, 429 and server errors retry up to three times. Tools retry only when their contract and result allow it. Jobs reclaimed after crashes have a five-attempt limit. Invalid model JSON and failed final verification consume the step/token ledger and terminate after repeated failures.

SQLite uses `BEGIN IMMEDIATE`, WAL and a busy timeout. It is suitable for one host with modest write concurrency. PostgreSQL uses row locking, `SKIP LOCKED` claims and a transaction advisory lock for schema initialization. API decisions and worker writes lock queue rows before run projections to keep lock ordering consistent.

## Context and memory

The compiler estimates tokens as `ceil(UTF-8 bytes / 3)` and reserves 1,024 tokens for output/framing. This is an approximation. Server usage, when available, is recorded separately and charged to the run ledger. Missing usage remains unknown in telemetry; the ledger uses the local estimate for admission.

Memory is run-scoped and typed: observations, facts, hypotheses, failures and procedures. Tool outputs become observations, not automatic causal conclusions. Contradictions use explicit subject/value pairs. Retrieval uses lexical overlap, confidence, importance and recency; there is no hidden embedding service.

The system prefix hash describes identical policy/tool/schema text. It does not describe which backend GPU pages are present. `session.parked` means durable context is preserved while asynchronous tools execute; `gpu_cache_released` remains `null` because Atlas cannot infer server cache release.

## Inference

An in-process bounded priority queue schedules model calls with FIFO ties, cancellation propagation and configured concurrency. Admission estimates KV storage as:

`2 × layers × KV heads × head dimension × bytes per element × (input + reserved output tokens)`

Those estimates do not include weights, fragmentation, attention workspaces or backend scheduling. Separate worker processes each have their own admission budget; they are not a distributed GPU allocator.

The HTTP adapter reads SSE deltas and server usage. TTFT is client-observed time to first content chunk. TPOT is an average derived from first/last content chunk timestamps and server completion-token count, not a per-token profiler. HTTP proxies are not inherited from the process environment. The model endpoint and credentials are operator configuration.

Small-model routing changes the requested served model name. Both names must exist on the configured endpoint. The speculation policy is a tested decision hook; enabling a server's draft decoder remains an operator task.

## Reference code

The educational stack is separate from agent execution. NumPy implements byte BPE, stable attention, GQA, RMSNorm, RoPE, SwiGLU, a decoder, prefill/decode cache parity, sampling and row-wise int8 quantization. Its cache experiment uses actual CPU arrays, reference counts, partial-page copy on write, LRU spill and exact restore.

The optional PyTorch decoder has numerical parity tests against the NumPy weights and a real autograd training step. `scripts/train_reference.py` trains a small byte model on local text. The supplied training loss trace is a toy sanity check without a held-out evaluation or pretrained language ability.

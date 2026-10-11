# Measured fixture benchmarks

`benchmarks/results/results.jsonl` records 84 runs: four bug fixtures × seven variants × three repetitions. `summary.csv` is computed from those rows. Mode order rotates between repetitions. Every run uses a fresh source copy, database, scheduler and workspace; the backend is `demo`.

Each run actually reproduces a failure, patches the workspace, adds a regression test and runs pytest again. Success also requires the repaired `logic.py` to match the independent fixture oracle and the regression file to exist. Agent-declared success alone does not pass this benchmark.

## Variants

| Mode | Context | Scheduling |
| --- | --- | --- |
| `full_history` | Entire observation transcript; fails explicitly if it exceeds the budget | Direct model call |
| `recent_window` | Last four observations within the same budget | Direct model call |
| `keyword_memory` | Observations ordered by lexical overlap | Direct model call |
| `summary_memory` | Bounded deterministic extractive summaries | Direct model call |
| `atlas_context` | Pinned state plus prioritized condensed observations | Direct model call |
| `atlas_runtime` | Atlas compiler | Bounded runtime scheduler |
| `atlas_full` | Atlas compiler and source-linked structured memory | Bounded runtime scheduler |

The last three modes share much of the compiler. On these short fixtures, source-linked memories often duplicate observations and are intentionally omitted. The benchmark exposes that limitation instead of creating artificial differences between modes.

## Reading the measurements

- `workflow_latency_ms` is wall time for the actual fixture workflow, including tool subprocesses and database work.
- Model/tool call counts and latency come from committed events. Final verifier calls are separate from agent-issued `tool.completed` calls.
- Context figures use a UTF-8 byte-based token estimate. Actual model input/output tokens, TTFT, TPOT and GPU memory are `null` with the fixture driver.
- `peak_kv_bytes_estimate` applies the declared model geometry to the largest compiled request. No GPU allocation occurred.
- Success rates are fixture-driver/runtime checks. The deterministic driver sees durable run metadata and has known patches; it cannot establish that an LLM retains reasoning quality after compaction.
- The fixtures are short and CPU test time dominates. Small latency differences are noise, not evidence of GPU speedup or better intelligence. Included figures are not performance promises.

The complete measured table is generated from CSV during packaging in `docs/BENCHMARK_RESULTS.md`. Its values should be interpreted using these notes.

## Reproduce fixture results

```bash
python -m benchmarks.run_all --trusted-local --repetitions 3
```

Use the same dependency locks, machine load, task list and mode budgets. The trusted local option runs only the supplied fixture code in this benchmark. No model or GPU is required.

## Evaluate a real model

Configure `ATLAS_BACKEND`, URL, served model name and optional credentials. Build the Docker sandbox and run:

```bash
python -m benchmarks.run_all --repetitions 3 --output benchmarks/results/live-model.jsonl
```

The same exact fixture oracles score the result. Model-backed `demo_id` is absent, so the server receives the compiled messages and produces its own actions. Server usage fields become actual measurements only when the server reports them.

For research claims, expand to unseen repositories and externally owned tests, repeat enough runs, record the model checkpoint/hash, hardware, server configuration, warm/cold state and failure categories. Compare equivalent admission/queue policies and separately measure backend cache hits and GPU memory. The supplied runtime benchmark is a starting harness, not a completed LLM quality study.

## Reference training trace

`reference-training.json` records 30 real CPU optimizer steps on a repeated toy byte corpus, seed 42. The training loss decreased during the check. There is no held-out score. The checkpoint is intentionally outside the delivery archive; rerun `python -m scripts.train_reference` to generate your own local checkpoint.

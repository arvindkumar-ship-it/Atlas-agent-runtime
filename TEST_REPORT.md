# Delivery verification

Verified on Linux with Python 3.12.14 and Node 24.19.0. Commands used the supplied dependency locks in a newly created virtual environment. The fixture backend does not need an external model or GPU.

| Check | Actual result |
| --- | --- |
| Clean dependency install | `requirements-dev.lock` installed successfully |
| Editable package install | Built and installed `atlas-agent-runtime` 1.0.0 |
| Dependency consistency | `pip check`: no broken requirements |
| Python suite with optional CPU PyTorch | **108 passed, 3 skipped** |
| Reference numerical suite | **18 passed**, including NumPy/PyTorch forward/cache parity and autograd |
| Python lint | Ruff check passed |
| Python formatting | Ruff format check passed |
| Frontend formatting | Prettier check passed |
| Frontend production build | TypeScript validation and Vite build passed |
| Browser workflow | **2 passed**: desktop and mobile verification, patch preview and download |
| Browser layout inspection | Desktop/mobile screenshots reviewed; no horizontal page overflow or JavaScript page errors |
| CLI | Installed `atlas demo sql --trusted-local` completed with independent checks and a patch artifact |
| Standalone worker process | Claimed/completed a real fixture job and exported its actual Prometheus counters on loopback |
| Fixture benchmark | **84/84 passed** across 7 variants, 4 tasks, 3 repetitions |
| Toy reference training | 30 CPU optimizer steps; training loss 5.5437 → 1.7563; no held-out score |
| Python package build | Source distribution and wheel built successfully |
| Deployment files | Compose, model compose, Render blueprint and CI YAML parsed successfully |

## What the Python checks cover

Transactional event/projection consistency, deterministic replay after reopening the database, tamper detection, idempotency-key conflicts, concurrent appends, exclusive claims, stale-generation rejection, retry/dead-letter bounds, exact approval binding, cancellation, atomic effect commits and crash recovery.

The runtime checks execute all four fixture workflows, preserve their original source, reproduce failed tests, apply real patches, add regression coverage, rerun tests and compare repaired source to an independent fixture oracle. Manual approval resumes on a fresh engine. An interrupted leased worker resumes its pending patch without duplicating logical completion. Concurrent engine handles serialize filesystem effects.

Security checks cover traversal, absolute/protected paths, symlinks, secret exclusion, ZIP validation/alias duplicates, exact patch mismatches, multi-file write interruption, partial deletion, test-preservation aliases, runner/config override refusal, installed-pytest shadow prevention, fixed command arguments, process timeout, nonzero exit and bounded output.

Model checks use HTTP mock transports to verify actual request/stream handling, usage and cached-token propagation, missing usage as unknown, native tool-call fragments, HTTP failures and malformed decision rejection. Context checks cover pinned goal/policy/contradictions, budget limits, baseline overflow and stable prefix identity. Telemetry checks verify cumulative histograms and invalid labels/values.

Reference checks cover causality, GQA attention parity, RMSNorm/RoPE, incremental/full decoder parity, bounded int8 error, sampling filters, Unicode BPE round trips and exact paged-cache fork/spill/restore behavior.

## Skipped live integrations

Three opt-in checks were skipped with explicit reasons:

- PostgreSQL: `ATLAS_TEST_DATABASE_URL` was not configured; no live PostgreSQL service was available.
- Docker sandbox: a Docker daemon was unavailable in the build environment.
- Real model server: `ATLAS_TEST_MODEL_URL` was not configured; no model weights, model server or GPU was supplied.

Their tests and CI setup are included. PostgreSQL/Docker behavior was not claimed as live-tested here. CI provisions PostgreSQL and builds/runs the sandbox when you push the repository. The prepared hosted deployment was not published because deployment is your manual step.

The base clean environment also skips the three PyTorch checks until the optional reference dependency is installed; they were separately executed successfully with CPU PyTorch 2.14.1.

## Warnings and interpretation

The current Starlette test client emits one deprecation warning about its HTTPX compatibility path. It did not affect the checks; dependency versions are locked. The browser checks used a working headless Chromium executable available during verification; normal setup uses Playwright's Chromium installer.

Fixture results are runtime/recovery checks with a deterministic known-patch driver. They do not establish general LLM correctness, compaction-quality gains, server cache savings or GPU speedup. Actual unavailable model/GPU measurements are null. See [benchmark methodology](docs/BENCHMARKS.md) and [security boundaries](SECURITY.md).

## Archive integrity

`RELEASE_MANIFEST.json` lists byte counts and SHA-256 checksums for every delivered file except the manifest itself. The packaging command verifies each archived checksum, ZIP CRCs and unique paths. Local databases, credentials, dependency directories, caches and model weights are excluded.

The candidate archive was also extracted into a separate directory and its Python suite executed against the extracted source successfully. All local documentation links were checked.

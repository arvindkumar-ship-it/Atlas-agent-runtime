# Execution boundaries

Atlas runs repository tests, which are executable code. The supported boundary for model-authored or uploaded repositories is a Docker test container on a dedicated operator-controlled worker. The local runner requires an explicit opt-in and is intended for the included trusted fixtures or your own trusted code.

## Tool restrictions

- Relative paths only; traversal, absolute paths, Windows drive syntax, secret paths and symlinks are rejected.
- ZIP import is limited to 8 MiB compressed, 32 MiB extracted, 5,000 members and 2 MiB per file, with a compression-ratio limit.
- Source repositories are copied. Original Python tests are preserved against the immutable external baseline.
- The model has no shell tool and cannot select arbitrary command flags, install packages, alter approvals or reconfigure the sandbox.
- Test runner/collection configuration and conftest hooks cannot be patched. All discovered test files are explicitly selected by the default runner; Python safe-path mode prevents a repository's `pytest.py` from shadowing the installed runner at startup.
- Patches use exact hunks, reject unsupported binary/mode/rename changes and cannot modify protected paths.
- The Docker runner disables networking, drops capabilities, sets no-new-privileges, runs as UID 65534, mounts the workspace read-only and limits CPU, memory, PIDs, output and elapsed time.
- Python test output and repository text are marked as untrusted in the policy. Typed contracts and approvals still enforce the boundary if a model follows injected instructions.

The sandbox shares the host kernel. Docker access gives the worker account substantial host authority; isolate that worker and keep the API account outside the Docker group. A hardened multi-tenant service would require separate tenant storage, per-user authorization, stronger isolation and additional controls.

## Authentication and privacy

Production configuration refuses an API key shorter than 32 characters. API and metrics routes require the operator bearer key when configured. Use HTTPS for public deployment. Health/readiness and static console assets are public; run data is behind the API guard. CORS defaults to same-origin.

The standalone worker's separate Prometheus endpoint binds only to loopback and carries no bearer middleware. Keep it private to the host/monitoring agent; it exposes bounded process metrics without repository text.

The console uses text rendering for model output and diff content, tab-scoped session storage for its key and a restrictive content security policy. Run records, uploaded source and patch artifacts can still contain private repository data. Keep data directories and backups private; do not commit them to GitHub.

The subprocess environment omits model/API/cloud credentials and disables automatic pytest plugin loading. Docker receives no host credentials. Model credentials are sent only to the configured model endpoint. Repository content in compiled context is sent to that endpoint; choose it according to your data policy.

## Verification limits

The AST scanner checks syntax and a small set of dangerous constructs. Passing it does not establish that arbitrary Python is safe. Added regression tests are required, but their semantic coverage needs review. A malicious test can forge output inside its sandbox; a production review should use externally owned tests as well. Included fixture benchmarks additionally compare repaired source with an independent exact oracle.

The event hash chain is an integrity check without an external signature anchor. A database administrator can rewrite the chain. Patch journals allow bounded crash recovery rather than a fully atomic transaction across the database and filesystem. Model calls may repeat after crashes and external billing cannot be made exactly once.

## Reporting a problem

For your deployment, report security issues privately to its repository owner. Include the affected version, a minimal reproduction and the failing boundary. Never attach API keys, production repository archives or database dumps to a public issue.

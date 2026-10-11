# API contract

The console and API are served from the same origin. When an operator key is configured, all `/api/*` and `/metrics` requests need `Authorization: Bearer <key>`. Public health endpoints carry no run data. The OpenAPI JSON is available at `/openapi.json`; the restrictive CSP may block the default CDN-based Swagger UI.

| Method / route | Result |
| --- | --- |
| `GET /health` | Process version/status |
| `GET /readyz` | Database readiness; 503 on failure |
| `GET /api/config` | Non-secret backend/sandbox settings |
| `GET /api/demos` | Bundled fixture descriptions |
| `POST /api/demos/{id}/run` | Create an isolated demo run; 202 |
| `GET /api/repositories` | Available top-level repository folders |
| `POST /api/repositories` | Raw ZIP body with `Content-Type: application/zip`; 201 |
| `POST /api/runs` | Validated repository task; optional `Idempotency-Key`; 202 |
| `GET /api/runs?limit=50&offset=0` | Paginated recent summaries; maximum 100 per page |
| `GET /api/runs/{id}` | Full durable run projection |
| `GET /api/runs/{id}/events?after=0` | Ordered events after a sequence |
| `GET /api/runs/{id}/stream?after=0` | SSE with sequence IDs and heartbeat comments |
| `GET /api/runs/{id}/replay?through=1` | Hash-checked reconstructed state; through is optional |
| `POST /api/runs/{id}/cancel` | Terminal cancellation; conflicting terminal state gives 409 |
| `GET /api/runs/{id}/approvals` | Exact action-bound approval records |
| `POST /api/runs/{id}/approvals/{approval}` | `{ "approved": true }` or false |
| `GET /api/runs/{id}/patch` | Final verification diff; 409 before a report exists |
| `GET /api/dashboard` | Aggregates from latest 100 runs; estimated token fields labeled |
| `GET /api/metrics` | Prometheus samples as JSON |
| `GET /metrics` | Standard Prometheus exposition |

General task request:

```json
{
  "repository_path": "example",
  "instruction": "Repair the expired-token bug and add regression coverage",
  "approval_policy": "manual",
  "context_tokens": 8192,
  "total_tokens": 180000,
  "priority": 100
}
```

The repository must resolve inside `ATLAS_REPO_ROOT`. An upload may return a nested path when it unwraps a GitHub ZIP folder; use that exact returned path. Lower priority numbers run first. Allowed approval policies are `manual` and `workspace`; the latter is prior authorization to edit the isolated copy.

Unknown fields and invalid shapes are rejected. UUID run/approval IDs are validated. A repeated idempotency key with the same normalized task returns the existing run; a different task gives 409. The API uses a best-effort active-run limit of 32 from its recent-run window, not a distributed per-tenant quota.

SSE clients should reconnect using the last sequence in the `after` query. The endpoint finishes when the run becomes terminal. The dashboard polls through the same guarded API, so it does not put credentials in URL parameters.

Model decisions use the schema in `atlas/domain/models.py`. Tools are exported from `atlas/tools/registry.py`; a model cannot add a new tool name or approval capability by returning it in JSON.

# API health checks

The public `/health` contract continues to return HTTP 200 so existing clients
can read its dependency status. Docker applies stricter readiness semantics by
running `python -m api.readiness_check` against that response.

| Signal | Meaning | Failure behavior |
| --- | --- | --- |
| `/health` HTTP response | The FastAPI process is live and can serve requests. | Connection or HTTP failure means the process is not live. |
| `api.readiness_check` | PostgreSQL is reachable and product search can run. | Exits 1 unless `status=ok` and `database=connected`. |
| `/health` response body | Backward-compatible dependency details for operators and clients. | Keeps HTTP 200; inspect `status`, `database`, and `llm`. |

The Dockerfile and `docker-compose.yml` use the readiness checker. After three
consecutive failed checks the API container becomes `unhealthy`; once PostgreSQL
recovers, a successful check changes it back to `healthy`.

`restart: unless-stopped` restarts the API when its process exits or the Docker
daemon restarts. Docker does not restart a running container solely because its
health status is `unhealthy`. If automatic restart on readiness failure is
required, configure that policy in the deployment orchestrator or an external
monitor instead of making the liveness endpoint depend on PostgreSQL.

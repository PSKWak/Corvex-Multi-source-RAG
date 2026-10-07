# Observability

*Applies to: Corvex 2.6.*

## Metrics

Corvex exposes Prometheus metrics at `telemetry.metrics_path` (default `/metrics`) on the
`http.addr` listener. The 1.x JSON endpoint `/stats` was removed in 2.0.

Core series:

| Metric | Type | Meaning |
|---|---|---|
| `corvex_jobs_enqueued_total{queue}` | counter | jobs accepted |
| `corvex_jobs_completed_total{queue,class}` | counter | successful runs |
| `corvex_jobs_failed_total{queue,class}` | counter | failed attempts (pre-DLQ) |
| `corvex_enqueue_rejected_total{queue,reason}` | counter | `reason="max_depth"` = `CVX-4210` |
| `corvex_dlq_size{queue}` | gauge | dead-letter backlog |
| `corvex_queue_depth{queue}` | gauge | pending jobs |
| `corvex_job_duration_seconds{class}` | histogram | run time |
| `corvex_worker_slot_utilization` | gauge | 0–1, busy slots / total slots |
| `corvex_storage_pool_wait_seconds` | histogram | time slots wait for a connection |

## Logs

Structured logs to stdout. `telemetry.log_format`: `json` (default) or `text`. Every job
log line carries `job_id`, `class`, `queue`, `attempt`, and `trace_id`.

## Tracing

Set `telemetry.otlp.endpoint` to export spans (OpenTelemetry) for enqueue → schedule →
run. The `trace_id` in logs matches the span.

## Dashboard

`http://<addr>/dashboard` shows live queue depths, worker pools, the DLQ, throughput
graphs, and a job search. `/dashboard/settings` requires the `admin` scope.

## Health checks

- `GET /healthz` — process is up (no auth)
- `GET /readyz` — storage reachable and migrations current (no auth)

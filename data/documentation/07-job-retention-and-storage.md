# Job Retention and Storage

*Applies to: Corvex 2.6.*

## What Corvex keeps

| Job state | Retention key | Default |
|---|---|---|
| Completed | `retention.completed` | `24h` |
| Failed / dead-lettered | `retention.failed` | `168h` (7 days) |
| Pending / running | not applicable | kept until terminal |

After the retention window, a job's record and payload are deleted by the pruner, which
runs every `retention.sweep_interval` (default `10m`). `corvex prune` runs it immediately.

> **Default changed in 2.0.** In 1.x, completed-job retention was controlled by
> `keep_completed_days` with a **default of 7 days**. Corvex 2.0 replaced it with
> `retention.completed`, a duration string, and lowered the default to **24 hours**. If you
> upgraded and expected a week of history, set `retention.completed: 168h` explicitly.

## Why 24h

Completed-job records are the largest contributor to storage growth on busy deployments.
The 24-hour default keeps recent history for debugging and dashboards while bounding
storage. Teams that need long-lived job history should ship completion events to an
external store (see `telemetry` webhooks) rather than raising `retention.completed` into
weeks.

## Storage sizing

Rough estimate: `bytes ≈ completed_jobs_per_day × avg_payload_bytes × (retention_hours/24)`.
The pruner is incremental and never deletes more than `retention.sweep_batch` (default
`5000`) records per pass, so a large retention reduction is applied gradually.

## Redis vs Postgres

| | Redis | Postgres |
|---|---|---|
| Throughput | higher | lower |
| Durability | depends on RDB/AOF config | strong |
| Payload size | keep < 64 KB | up to 1 MB |
| Best for | high-volume, short-lived jobs | audit-heavy, larger payloads |

`storage.redis` and `storage.postgres` are mutually exclusive. Switching backends requires
draining all queues first; there is no online migration between storage engines.

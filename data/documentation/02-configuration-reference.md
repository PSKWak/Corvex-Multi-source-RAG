# Configuration Reference

*Applies to: Corvex 2.6. For the 1.x keys and their 2.0 replacements, see
[Upgrading from 1.x](/guides/upgrade-2.0).*

Corvex is configured with a single YAML file (`corvex.yaml`). Every key can also be set
with an environment variable using the `CORVEX_` prefix and `__` as the nesting separator,
e.g. `CORVEX_WORKERS__CONCURRENCY=16`.

## `http`

| Key | Type | Default | Notes |
|---|---|---|---|
| `http.addr` | string | `0.0.0.0:8577` | API + dashboard listener. **The default port changed from 9000 to 8577 in 2.0.** |
| `http.external_url` | string | derived | Used in dashboard links and webhook callbacks. |

## `storage`

| Key | Type | Default | Notes |
|---|---|---|---|
| `storage.redis.url` | string | — | Redis connection URL. In 1.x this key was `redis_url`. |
| `storage.postgres.url` | string | — | Postgres connection URL. Mutually exclusive with `storage.redis`. |
| `storage.redis.pool_size` | int | `10` | Max connections per process (workers **and** server). |

## `workers`

| Key | Type | Default | Notes |
|---|---|---|---|
| `workers.concurrency` | int | number of CPU cores | Jobs run in parallel per worker process. **In 1.x this key was the top-level `worker_threads`.** |
| `workers.queues` | list | `[default]` | Queues this worker pulls from, in priority order. |
| `workers.shutdown_grace` | duration | `30s` | How long `corvex drain` waits for in-flight jobs. Also settable via `CORVEX_DRAIN_TIMEOUT`. |

## `queue`

| Key | Type | Default | Notes |
|---|---|---|---|
| `queue.max_depth` | int | `10000` | Max pending jobs per queue. Enqueue beyond this returns `CVX-4210`. Set to `-1` for unbounded (not recommended). **Setting it to `0` rejects all enqueues.** |
| `queue.max_retries` | int | `5` | Attempts before a job moves to the dead-letter queue (`CVX-4301`). |
| `queue.retry_backoff` | string | `exponential` | `exponential` or `linear`. |

## `retention`

| Key | Type | Default | Notes |
|---|---|---|---|
| `retention.completed` | duration | `24h` | How long completed jobs are kept before pruning. **The 1.x key `keep_completed_days` (default 7) was replaced in 2.0.** |
| `retention.failed` | duration | `168h` | Retention for failed / dead-lettered jobs. |
| `retention.sweep_interval` | duration | `10m` | How often the pruner runs. `corvex prune` triggers it immediately. |

## `auth`

| Key | Type | Default | Notes |
|---|---|---|---|
| `auth.enabled` | bool | `true` | When true, all API routes require `Authorization: Bearer <token>`. |
| `auth.tokens` | list | — | Static tokens with scopes. See [Authentication](/guides/authentication). |

## `telemetry`

| Key | Type | Default | Notes |
|---|---|---|---|
| `telemetry.metrics_path` | string | `/metrics` | Prometheus exposition. The 1.x JSON endpoint `/stats` was removed in 2.0. |
| `telemetry.log_format` | string | `json` | `json` or `text`. |

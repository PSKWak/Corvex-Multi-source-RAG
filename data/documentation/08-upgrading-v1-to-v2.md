# Upgrading from Corvex 1.x to 2.x

*Applies to: Corvex 2.0 and later. Current stable is 2.6.*

Corvex 2.0 (2023-11) is a breaking release. Read this in full before upgrading a
production deployment.

## 1. Renamed configuration keys

| 1.x key | 2.x key | Notes |
|---|---|---|
| `worker_threads: N` | `workers.concurrency: N` | now nested under `workers` |
| `redis_url: ...` | `storage.redis.url: ...` | |
| `keep_completed_days: 7` | `retention.completed: 168h` | duration string; **new default is `24h`** |
| `http_port: 9000` | `http.addr: 0.0.0.0:8577` | **default port is now 8577** |
| `stats_enabled: true` | `telemetry.metrics_path: /metrics` | JSON `/stats` removed; Prometheus only |

Loading a 1.x config unchanged fails fast with `CVX-1002` naming the first unknown key.
Run `corvex config check` to get the full list.

## 2. API authentication

The query-parameter token (`?token=<t>`) is removed. All API clients must send
`Authorization: Bearer <t>`. See [Authentication](/guides/authentication).

## 3. CLI changes

- `corvex purge` → `corvex prune`
- `corvex stats` → removed; use `/metrics` or `corvex status`

## 4. Upgrade procedure

```bash
corvex drain                 # on every worker host
systemctl stop corvex        # stop servers
# install 2.6
corvex config check          # fix renamed keys until this passes
corvex migrate               # apply storage schema changes (one-way)
corvex serve                 # start servers
corvex worker --queues ...   # start workers
```

`corvex migrate` rewrites job records to the 2.x schema. It is **not reversible** —
snapshot Redis/Postgres first. Mixed 1.x/2.x workers against the same storage are not
supported; upgrade all processes together.

## 5. After upgrading

- Confirm `retention.completed` is what you want (default dropped from 7 days to 24h).
- Update monitoring to scrape `/metrics` instead of `/stats`.
- Update any enqueue scripts that passed `?token=`.

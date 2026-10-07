# Error Code Reference

*Applies to: Corvex 2.6.*

Corvex error codes have the form `CVX-<class><number>`. Class `1xxx` = configuration and
startup, `2xxx` = authentication, `4xxx` = runtime and queueing, `5xxx` = storage.

## CVX-1001 — configuration file not found or unreadable

Corvex could not find `corvex.yaml` in the working directory, `/etc/corvex/corvex.yaml`,
or `$CORVEX_CONFIG`, or the file exists but could not be read (permissions, invalid UTF-8).
Pass `--config <path>` explicitly or fix file permissions.

## CVX-1002 — invalid configuration: unknown key

A key in `corvex.yaml` is not recognised. The message names the offending path. The most
common cause after upgrading from 1.x is a renamed key — see
[Upgrading from 1.x](/guides/upgrade-2.0). Run `corvex config check` to list all unknown
keys at once.

## CVX-1010 — storage backend unreachable at startup

Corvex could not connect to Redis/Postgres during boot. Check `storage.*.url`, network
reachability, and that the backend is accepting connections. Corvex retries for
`storage.connect_timeout` (default `15s`) before exiting.

## CVX-2001 — missing or invalid authentication token

The request had no `Authorization: Bearer <token>` header, or the token is not in
`auth.tokens`. Note that the 1.x `?token=` query parameter is **not** accepted in 2.x.

## CVX-2003 — token lacks required scope

The token authenticated but does not carry the scope the route needs (e.g. `jobs:write`
for enqueue, `admin` for `/dashboard/settings`).

## CVX-4210 — enqueue rejected: queue depth limit reached

The target queue already holds `queue.max_depth` pending jobs (default `10000`). This is
backpressure working as designed. Options, in order of preference:

1. Add worker capacity so the queue drains faster.
2. Raise `queue.max_depth` if the spike is legitimate and you have memory headroom.
3. Route overflow to a separate lower-priority queue.

Do **not** set `queue.max_depth: 0` to "remove the limit" — in 2.x that value rejects
*every* enqueue. Use `-1` for unbounded, and only with monitoring.

## CVX-4301 — job exceeded max_retries, moved to dead-letter queue

The job failed `queue.max_retries` times (default `5`). It is now in the DLQ and will not
run again automatically. Inspect with `corvex status` / the dashboard; requeue with
`corvex dlq requeue <job_id>` after fixing the cause.

## CVX-4404 — job class not registered on any worker

`corvex enqueue <JobClass>` (or an API enqueue) named a class that no currently-running
worker has registered. Start a worker that imports the handler, or check for a typo in the
class name.

## CVX-5002 — storage write failed

A write to Redis/Postgres failed mid-operation — typically Redis `OOM`, Postgres disk
full, or a failover. The job is retried. Check backend health and `maxmemory` / disk.

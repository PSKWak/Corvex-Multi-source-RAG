# CLI Reference

*Applies to: Corvex 2.6.*

Global flags: `--config <path>`, `--log-level <debug|info|warn|error>`, `--json`.

## `corvex serve`

Starts the API server, the scheduler, and the dashboard on `http.addr` (default
`0.0.0.0:8577`).

```bash
corvex serve --config /etc/corvex/corvex.yaml
```

## `corvex worker`

Starts a worker process.

| Flag | Default | Notes |
|---|---|---|
| `--queues` | `default` | Comma-separated, priority order. |
| `--concurrency` | `workers.concurrency` | Override per process. |

## `corvex enqueue <JobClass>`

Enqueues one job. `--arg k=v` repeatable. Returns the job id, or `CVX-4404` if no running
worker has registered `<JobClass>`.

## `corvex drain`

Gracefully stops the local workers: stops pulling new jobs, waits up to
`workers.shutdown_grace` (default `30s`, or `$CORVEX_DRAIN_TIMEOUT`) for in-flight jobs,
then exits. Send it before deploys.

## `corvex prune`

Immediately runs the retention sweep (delete completed jobs older than
`retention.completed`, failed jobs older than `retention.failed`).

> **Renamed:** this command was `corvex purge` in 1.x. `corvex purge` now prints a
> deprecation error and exits non-zero.

## `corvex status`

Prints queue depths, worker counts, DLQ size, and the oldest pending job age. `--json` for
machine output.

## `corvex migrate`

Applies pending storage schema migrations. Run after every upgrade. Safe to run twice.

## `corvex config check`

Validates `corvex.yaml` and reports unknown keys (`CVX-1002`) without starting anything.

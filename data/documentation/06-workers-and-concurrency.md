# Workers and Concurrency

*Applies to: Corvex 2.6.*

## The concurrency model

Each `corvex worker` process runs a pool of `workers.concurrency` job slots. Jobs in a
slot run on their own OS thread; blocking I/O in a job does not stall the other slots.
Scale out by running more worker processes (on more hosts), scale up by raising
`workers.concurrency` per process.

```yaml
workers:
  concurrency: 16          # default: number of CPU cores
  queues: [critical, default, low]
  shutdown_grace: 30s
```

> **Config key history:** this setting is `workers.concurrency` in 2.x. In 1.x it was a
> top-level key named `worker_threads`. A 1.x config loaded into 2.x fails with `CVX-1002`
> naming `worker_threads` as unknown.

## Choosing a value

Start with `concurrency = CPU cores` for CPU-bound jobs, or `2–4 × cores` for I/O-bound
jobs. Watch the `corvex_worker_slot_utilization` metric: sustained utilisation above 0.9
with a growing queue means add capacity; utilisation below 0.3 means you have over-provisioned.

Every slot holds a storage connection while running. Ensure
`storage.redis.pool_size ≥ workers.concurrency`, or slots will block waiting for a
connection (a common cause of "workers look idle but the queue isn't draining").

## Queue priority

`workers.queues` is a strict priority list. A worker only pulls from `default` when
`critical` is empty. For weighted fairness instead of strict priority, run separate worker
pools per queue.

## Graceful shutdown

`corvex drain` (or `SIGTERM`) stops the worker pulling new jobs and waits up to
`workers.shutdown_grace` for in-flight jobs to finish. Override the wait with the
`CORVEX_DRAIN_TIMEOUT` environment variable. Jobs still running when the grace period
expires are returned to the queue and retried elsewhere (at-least-once delivery).

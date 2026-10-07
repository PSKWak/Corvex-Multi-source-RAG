# High-Throughput Tuning

*Applies to: Corvex 2.6.*

This guide covers deployments pushing tens of thousands of jobs per second. Apply changes
one at a time and watch the metrics named below.

## 1. Right-size the connection pool

Every running job slot holds one storage connection. Set:

```
storage.redis.pool_size ≥ (workers.concurrency × worker_processes_per_host) + 4
```

Symptoms of an undersized pool: `corvex_storage_pool_wait_seconds` p99 climbing, workers
showing low CPU while the queue grows. This is the single most common throughput
bottleneck.

## 2. Batch enqueues

Use `POST /api/v1/jobs:batch` (up to 1000 jobs per request) instead of one request per
job. Batching cuts enqueue overhead by ~20× at high rates.

## 3. Tune the queue depth limit

`queue.max_depth` (default `10000`) is a safety valve, not a tuning knob. If you hit
`CVX-4210` during legitimate spikes:

- first, add worker capacity;
- if the spike is genuinely burst-then-drain, raise `queue.max_depth` to
  `expected_peak_backlog × 1.5` and ensure the storage backend has the memory for it
  (`≈ max_depth × avg_payload_bytes`);
- never set it to `0` (rejects all) — use `-1` for unbounded only with alerting on
  `corvex_queue_depth`.

## 4. Separate scheduler and API load

At high rates, run `corvex serve --role api` and `corvex serve --role scheduler` as
separate deployments so a slow API client cannot delay scheduling.

## 5. Storage engine

Above ~20k jobs/s, use Redis, keep payloads under 8 KB (put large inputs in object
storage and pass a reference), and enable Redis pipelining with
`storage.redis.pipeline: true`.

## Key metrics

| Metric | Watch for |
|---|---|
| `corvex_queue_depth` | sustained growth = under-capacity |
| `corvex_storage_pool_wait_seconds` | > 0 at p50 = pool too small |
| `corvex_worker_slot_utilization` | > 0.9 sustained = add workers |
| `corvex_enqueue_rejected_total` | any increase = `CVX-4210` firing |

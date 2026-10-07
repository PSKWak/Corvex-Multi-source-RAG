---
title: "How We Load-Tested Corvex to 50,000 Jobs per Second"
author: "Marcus Bello"
author_role: staff
publish_date: "2023-06-27"
product_version: "1.9"
tags: [performance, benchmark, engineering, load-testing]
url: https://blog.corvex.dev/2023/06/scaling-to-50k
---

# How We Load-Tested Corvex to 50,000 Jobs per Second

Before the 2.0 release we wanted a defensible throughput number. This post is the
methodology and the raw results — not a tuning guide (that comes later), just how the
benchmark was actually run so you can reproduce or challenge it.

## The hardware

All on AWS, single region, same placement group:

| Role | Instance | Count |
|---|---|---|
| Corvex server (`corvex serve`) | c6i.4xlarge (16 vCPU, 32 GiB) | 1 |
| Workers (`corvex worker`) | c6i.2xlarge (8 vCPU, 16 GiB) | 8 |
| Redis (storage) | r6g.2xlarge (8 vCPU, 64 GiB), single node, AOF off | 1 |
| Load generator | c6i.8xlarge | 2 |

## The workload

- Job body: a 220-byte JSON payload, deserialize, sleep 1 ms (simulated work), return.
- Enqueue: batched, 500 jobs per API call, from the two load generators.
- Duration: 20-minute steady-state after a 5-minute ramp.
- `worker_threads: 24` per worker (3× the 8 vCPUs, since the job is I/O-shaped).

## Results

| Metric | Value |
|---|---|
| Sustained enqueue rate | 51,300 jobs/s |
| Sustained completion rate | 50,900 jobs/s |
| p50 end-to-end latency | 38 ms |
| p99 end-to-end latency | 210 ms |
| Redis CPU | 71% |
| Peak `corvex_queue_depth` | ~44,000 |

## What was the bottleneck

Redis single-core write throughput. At ~51k/s the Redis node's main thread was the wall;
worker CPU never went above 45%. Sharding the queue across two Redis nodes in a follow-up
test got us to ~92k/s but that configuration wasn't supported for general use at the time.

## Caveats

This is a synthetic 1 ms job. Real jobs that make network calls or touch a database will
be gated by *those* systems long before Corvex or Redis. Treat 50k/s as a ceiling for the
transport, not a promise for your workload.

---
title: "Tuning Corvex Worker Pools: A Practical Checklist"
author: "Sarah Nguyen"
author_role: community
publish_date: "2025-07-22"
product_version: "2.6"
tags: [performance, tuning, workers, redis, checklist]
url: https://blog.corvex.dev/2025/07/tuning-worker-pools
---

# Tuning Corvex Worker Pools: A Practical Checklist

We spent a quarter making Corvex throughput predictable. Here's the checklist we now run
whenever a queue starts backing up, in the order that pays off fastest.

## 1. Is it the connection pool? (usually yes)

`storage.redis.pool_size` defaults to 10. Every running job slot holds one connection, so
if `workers.concurrency` is 32 you have 22 slots permanently starved. The tell: worker CPU
low, `corvex_queue_depth` rising, `corvex_storage_pool_wait_seconds` p50 > 0.

Set `pool_size` to `workers.concurrency + 4` per process, and check that Redis
`maxclients` and your OS file-descriptor limit can absorb `hosts × processes × pool_size`
connections.

## 2. Match concurrency to job shape

- CPU-bound jobs (image resize, compression): `concurrency ≈ CPU cores`.
- I/O-bound jobs (HTTP calls, DB queries): `concurrency ≈ 2–4 × cores`, but now the
  downstream is your limit — don't set 200 and DDoS your own API.

Watch `corvex_worker_slot_utilization`. Sustained > 0.9 with a growing queue → add
processes/hosts. Sustained < 0.3 → you're over-provisioned; scale down.

## 3. Batch your enqueues

One `POST /api/v1/jobs:batch` (up to 1000 jobs) instead of 1000 requests. At high rates
this alone can 10–20× your effective enqueue throughput.

## 4. Separate queues by SLA

Strict-priority `workers.queues: [critical, default, low]` means `low` never delays
`critical`. For big batch jobs, give them a dedicated queue and dedicated workers so a
backlog there can't cause `CVX-4210` on your real-time queue.

## 5. Only then touch queue.max_depth

`queue.max_depth` (default 10,000) is a safety valve. Raise it only for known
burst-then-drain patterns, size it to ~1.5× the expected burst, confirm Redis has the
memory, and alert on `corvex_queue_depth`. Never `0` (rejects everything); `-1` only with
paging alerts.

## Our before/after

30k jobs/s workload, 6 hosts: queue depth was climbing all day at `pool_size: 10`. After
`pool_size: 40` + batch enqueue, steady-state depth sits near zero and p99 latency dropped
from 1.4s to 180ms. We changed nothing about the job code.

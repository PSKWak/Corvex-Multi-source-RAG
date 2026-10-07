---
title: "Designing Around CVX-4210: Backpressure and Dead-Letter Queues"
author: "Dana Whitfield"
author_role: community
publish_date: "2024-09-30"
product_version: "2.3"
tags: [patterns, backpressure, dead-letter-queue, reliability]
url: https://blog.corvex.dev/2024/09/backpressure
---

# Designing Around CVX-4210: Backpressure and Dead-Letter Queues

`CVX-4210 enqueue rejected: queue depth limit reached` is not a bug. It's Corvex telling
you the producers are outrunning the consumers. This post is the pattern we landed on
after fighting it the wrong way for a month.

## What we did wrong first

We raised `queue.max_depth` from 10,000 to 500,000. That just moved the failure: instead
of fast rejects we got a 40-minute backlog, jobs running against stale data, and one
incident where a stuck consumer let the queue grow until Redis hit `maxmemory` and
everything fell over. Making the buffer huge converts a visible problem into an invisible
one.

> Do **not** try to "turn off" the limit with `queue.max_depth: 0`. On 2.x that value
> rejects every enqueue. `-1` is unbounded, and unbounded without alerting is how you OOM
> your storage.

## The pattern that worked

**1. Tiered queues.** Real-time work goes on `default`. Bulk/batch work goes on `bulk`
with its own workers. `bulk` hitting `CVX-4210` never touches user-facing latency.

**2. A spillover dead-letter path for overflow, not just failures.** When an enqueue to
`default` returns `CVX-4210`, the producer writes the job to a `spillover` list (plain
Redis, outside Corvex) and a small cron drains `spillover` back into `default` whenever
`corvex status` shows depth below a threshold. Overflowing jobs are delayed, never
dropped.

**3. Right-size workers to the p95 arrival rate, not the average.** We were provisioned
for mean throughput; the spikes were 3× mean. Adding two workers sized for p95 removed
90% of the 4210s on its own.

**4. Alert on `corvex_enqueue_rejected_total`, page on `corvex_dlq_size`.** A few rejects
is backpressure doing its job. A growing DLQ (from `CVX-4301`, retries exhausted) is a
real dependency problem.

## When raising max_depth *is* right

If your load is genuinely burst-then-idle — a nightly import that dumps 200k jobs and then
nothing for hours — then sizing `queue.max_depth` to ~1.5× that burst and letting workers
chew through it is fine. Just make sure `max_depth × avg_payload_bytes` fits in Redis with
headroom, and alert on depth so a *non*-draining burst wakes someone.

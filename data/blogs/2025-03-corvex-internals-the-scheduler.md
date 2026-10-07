---
title: "Corvex Internals: How the Scheduler Actually Works"
author: "Marcus Bello"
author_role: staff
publish_date: "2025-03-11"
product_version: "2.3"
tags: [internals, architecture, scheduler, deep-dive]
url: https://blog.corvex.dev/2025/03/scheduler-internals
---

# Corvex Internals: How the Scheduler Actually Works

People ask how a job goes from `corvex enqueue` to running on a worker. This is the whole
path, as of 2.3. Nothing here is configurable — it's background for debugging and capacity
planning.

## The data structures

Per queue, Corvex keeps three Redis structures:

- **`pending`** — a sorted set scored by `run_at` (epoch ms). Delayed jobs (`--in`,
  `--at`) sit here with a future score; immediate jobs get `now`.
- **`ready`** — a list. Jobs whose `run_at` has passed, waiting to be claimed.
- **`running`** — a hash of `job_id → {worker_id, claimed_at, lease_expires}`.

## The scheduler loop

`corvex serve` runs one scheduler goroutine. Every 250 ms it:

1. `ZRANGEBYSCORE pending -inf now LIMIT 0 500` — pull up to 500 now-due jobs.
2. Atomically move them to `ready` (Lua script, so a crash mid-move can't lose them).
3. Re-check `pending` for the next-due score and sleep `min(250ms, next_due - now)`.

Batch size 500 and the 250 ms tick are why the practical scheduling granularity is
"within a second", not millisecond-exact.

## Claiming

Workers `BLPOP ready` with a short timeout. On claim, the worker writes `running[job_id]`
with `lease_expires = now + max(job_timeout, 30s)`. A separate reaper goroutine scans
`running` every 5 s; any lease that's expired (worker died, GC pause, network partition)
goes back to `ready`. This is the mechanism behind at-least-once delivery — and why job
handlers must be idempotent.

## Retries

A failed job is re-inserted into `pending` with `run_at = now + backoff(attempt)`.
`exponential` backoff is `min(2^attempt seconds, 1h)` with ±20% jitter. After
`queue.max_retries` the job is moved to the dead-letter set instead (`CVX-4301`).

## Why there's no cron

Recurring schedules would need a durable "next fire time" per rule and timezone-aware
recurrence math. The sorted-set model handles one-shot future jobs cleanly but has no
place for "every weekday at 09:00" — that's why 2.x has delayed enqueue but no periodic
scheduler.

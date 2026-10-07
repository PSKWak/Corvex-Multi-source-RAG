---
title: "Migrating Our Production Fleet from Corvex 1.9 to 2.0"
author: "Priya Raman"
author_role: staff
publish_date: "2024-02-09"
product_version: "2.0"
tags: [migration, upgrade, operations, breaking-changes]
url: https://blog.corvex.dev/2024/02/migrating-to-v2
---

# Migrating Our Production Fleet from Corvex 1.9 to 2.0

We run Corvex across ~40 worker hosts. Here's exactly what the 1.9 → 2.0 migration looked
like, including the two things that bit us.

## The config rewrite

Every key we used got renamed. Side by side:

```yaml
# 1.9
redis_url: redis://redis.internal:6379/0
worker_threads: 24
http_port: 9000
keep_completed_days: 7
stats_enabled: true
```

```yaml
# 2.0
storage:
  redis:
    url: redis://redis.internal:6379/0
workers:
  concurrency: 24
http:
  addr: 0.0.0.0:8577
retention:
  completed: 168h      # 2.0 default is 24h — we set this to keep our old 7-day behaviour
telemetry:
  metrics_path: /metrics
```

`corvex config check` catches every unknown key, so we iterated on that until it was clean
before touching anything else.

### Gotcha 1: the retention default dropped to 24h

We didn't set `retention.completed` at first and assumed history would carry over. Two
days later a dashboard that looked back a week was empty. The 2.0 default is **24 hours**,
not 7 days. If you relied on the old default, set `retention.completed: 168h` explicitly.

### Gotcha 2: the port changed to 8577

Our health checks, our Grafana datasource, and three internal scripts all hardcoded
`:9000`. 2.0 listens on `:8577` by default. We could have kept 9000 by setting
`http.addr: 0.0.0.0:9000`, but we chose to move to the new default and fix the callers.

## The API auth change

`?token=` is gone in 2.0. Everything moves to `Authorization: Bearer`. We grepped the
whole org for `token=` in URLs — found 11 call sites — and switched them to headers before
cutting over.

## The actual cutover

```bash
# per worker host
corvex drain
# stop servers, install 2.0, then:
corvex config check     # must be clean
corvex migrate          # one-way storage schema change — snapshot Redis first
corvex serve
corvex worker --queues critical,default,low
```

`corvex migrate` took about 90 seconds for ~3M job records. It's not reversible, so we
took an RDB snapshot immediately before. Total downtime was under 4 minutes.

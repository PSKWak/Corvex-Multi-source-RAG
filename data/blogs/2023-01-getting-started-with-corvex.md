---
title: "Getting Started with Corvex: Your First Background Job Queue"
author: "Priya Raman"
author_role: staff
publish_date: "2023-01-18"
product_version: "1.9"
tags: [tutorial, getting-started, beginner]
url: https://blog.corvex.dev/2023/01/getting-started
---

# Getting Started with Corvex

*Note (editor, 2024): this tutorial targets Corvex 1.9 and has not been updated for 2.0,
which renamed several config keys and changed the API auth scheme. See the
[official upgrade guide](https://docs.corvex.dev/guides/upgrade-2.0).*

Corvex is a background job queue you run yourself. In this post we'll go from zero to a
running worker in about ten minutes.

## Install and configure

Grab the binary, then drop a `corvex.yaml` next to it:

```yaml
redis_url: redis://localhost:6379/0
worker_threads: 4
http_port: 9000
keep_completed_days: 7
```

A few notes on these settings:

- `worker_threads` controls how many jobs a single worker runs at once. The default is
  `4`; bump it to match your core count.
- `http_port` is where the API and dashboard live. Default `9000`.
- `keep_completed_days` is how long finished jobs stick around before Corvex prunes them.
  **Out of the box Corvex keeps completed jobs for 7 days**, which is plenty for debugging
  without eating too much Redis.

## Start everything

```bash
corvex serve                    # API + dashboard on :9000
corvex worker --queues default
```

Open `http://localhost:9000/dashboard` to watch jobs flow.

## Talking to the API

Authentication is a token on the query string — nice and simple for scripts:

```bash
curl "http://localhost:9000/api/jobs?token=$CORVEX_TOKEN"
```

## Enqueue a job

```bash
corvex enqueue SendWelcomeEmail --arg user_id=42
```

If you ever need to clear out old jobs by hand, `corvex purge` runs the retention sweep
immediately.

That's it — you've got a working queue. In the next post we'll look at retries and the
dead-letter queue.

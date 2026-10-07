# Getting Started with Corvex

*Applies to: Corvex 2.6 (current stable)*

Corvex is a self-hosted distributed job queue. It runs background jobs across a pool of
workers with at-least-once delivery, automatic retries, a dead-letter queue, and a web
dashboard.

## Install

```bash
curl -sSL https://get.corvex.dev | sh
corvex version
# corvex 2.6.1 (build 2025-05-14)
```

Corvex ships as a single static binary. It needs a storage backend: Redis 6.2+ or
PostgreSQL 13+.

## Minimal configuration

Corvex reads `corvex.yaml` from the working directory, then `/etc/corvex/corvex.yaml`,
then the path in `$CORVEX_CONFIG`. If no config file is found, Corvex exits with
`CVX-1001`.

```yaml
# corvex.yaml
storage:
  redis:
    url: redis://localhost:6379/0

http:
  # Default bind address. The API and dashboard share this listener in 2.x.
  addr: 0.0.0.0:8577

workers:
  concurrency: 8        # default: number of CPU cores
```

## Run the server and a worker

```bash
corvex serve                     # API + scheduler + dashboard on :8577
corvex worker --queues default   # a worker process
```

The dashboard is served at `http://localhost:8577/dashboard`. The Prometheus metrics
endpoint is `http://localhost:8577/metrics`.

## Enqueue your first job

```bash
corvex enqueue SendWelcomeEmail --arg user_id=42
```

A job class must be registered by at least one running worker or the enqueue is rejected
with `CVX-4404`.

## Where to go next

- [Configuration reference](/reference/configuration)
- [Authentication](/guides/authentication) — the API requires a bearer token
- [Workers and concurrency](/guides/workers)
- [Upgrading from 1.x](/guides/upgrade-2.0) — 2.0 renamed several config keys

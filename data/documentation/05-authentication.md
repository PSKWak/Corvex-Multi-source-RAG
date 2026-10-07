# Authentication

*Applies to: Corvex 2.6. This changed significantly in 2.0.*

## API authentication (2.x)

When `auth.enabled` is `true` (the default), **every** API request must send a bearer
token in the `Authorization` header:

```bash
curl -H "Authorization: Bearer $CORVEX_TOKEN" \
     https://corvex.internal:8577/api/v1/jobs
```

A request without the header, or with an unknown token, is rejected with `CVX-2001`.

> **Removed in 2.0:** the 1.x query-parameter form `?token=<token>` is no longer accepted.
> Requests using it fail with `CVX-2001`. Update any scripts, dashboards, or monitoring
> checks that still pass the token in the URL.

## Configuring tokens

```yaml
auth:
  enabled: true
  tokens:
    - value: ${CORVEX_ADMIN_TOKEN}
      scopes: [admin, jobs:read, jobs:write]
    - value: ${CORVEX_CI_TOKEN}
      scopes: [jobs:write]
```

Scopes: `jobs:read`, `jobs:write`, `dlq:manage`, `admin`. A route that needs a scope the
token lacks returns `CVX-2003`.

## Scopes by route (selection)

| Route | Scope |
|---|---|
| `GET /api/v1/jobs` | `jobs:read` |
| `POST /api/v1/jobs` (enqueue) | `jobs:write` |
| `POST /api/v1/dlq/{id}/requeue` | `dlq:manage` |
| `GET /dashboard/settings` | `admin` |

## Rotating tokens

Add the new token to `auth.tokens`, reload (`corvex reload` or `SIGHUP`), roll clients
over, then remove the old token and reload again. Corvex accepts all listed tokens
simultaneously, so rotation needs no downtime.

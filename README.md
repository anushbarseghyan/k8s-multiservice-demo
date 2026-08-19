# k8s-multiservice-demo

A small three-service app whose real purpose is to teach Kubernetes
**health checks** — specifically, the gap between a pod being `Running` and the
app inside it actually *working*.

## Why this exists

A container can be `Running` — the process started, the port is open — while the
application is still useless: the database isn't connected yet, a dependency is
down, or a request hangs forever. Kubernetes only knows the difference if you
give it **probes**:

- a **liveness** probe (is the process wedged? if so, restart it), and
- a **readiness** probe (should this pod receive traffic *right now*?).

Get these wrong and you get user-visible failures that look like "it's Running,
why is it broken?" This project is built to *reproduce* those failures on
purpose, so the fix is memorable rather than theoretical.

The API is deliberately shaped for three later experiments:

- **no readiness probe** — traffic hits a pod that isn't ready yet.
- **a too-shallow readiness probe** — using `/healthz` (which never touches the
  DB) as readiness, so the pod is marked ready before the DB is.
- **a too-aggressive liveness probe** — a timeout short enough that the `/slow`
  endpoint trips it and the pod gets killed mid-request.

To make the not-ready window easy to observe, the API **sleeps 15s on startup
before it even tries to connect to Postgres** (`STARTUP_DELAY`). That delay is
intentional — don't "fix" it.

## Eventual architecture

```
  browser ──▶ nginx frontend ──/api──▶ FastAPI ──▶ Postgres
              (static page +           (asyncpg
               reverse proxy)           pool)
```

Three services on a local [kind](https://kind.sigs.k8s.io/) cluster. nginx serves
the static page and reverse-proxies `/api` to the FastAPI service; FastAPI owns
an `items` table in Postgres.

## Status

**Day 1 — application layer only.** The two service images build and run under
plain Docker. There are **no Kubernetes manifests yet** (no kind cluster, no
Deployments, no probes, no nginx `/api` proxy config) — those come in a later
session. Because the nginx proxy config isn't wired up yet, the frontend's
button won't reach the API when run under Docker alone; that connection is part
of the Kubernetes work.

## API endpoints

| Endpoint        | Purpose                                                            |
| --------------- | ----------------------------------------------------------------- |
| `GET /healthz`  | Liveness. 200 if the process is alive. **Never touches the DB.**  |
| `GET /readyz`   | Readiness. 200 only if the DB pool exists and `SELECT 1` works; otherwise 503 with a reason. |
| `GET /slow`     | Sleeps `SLOW_DELAY` seconds (default 5), then 200.                |
| `GET /items`    | List rows from the `items` table.                                 |
| `POST /items`   | Insert a row: `{"name": "..."}`.                                  |

### Configuration (environment variables)

| Var             | Default     | Meaning                                   |
| --------------- | ----------- | ----------------------------------------- |
| `DB_HOST`       | `localhost` | Postgres host                             |
| `DB_PORT`       | `5432`      | Postgres port                             |
| `DB_NAME`       | `demo`      | Database name                             |
| `DB_USER`       | `demo`      | Database user                             |
| `DB_PASSWORD`   | `demo`      | Database password                         |
| `STARTUP_DELAY` | `15`        | Seconds to wait before connecting to DB   |
| `SLOW_DELAY`    | `5`         | Seconds `/slow` sleeps                     |

## Day-1 run instructions (Docker only)

```bash
# 1. Network + Postgres
docker network create demo
docker run -d --name db --network demo \
  -e POSTGRES_DB=demo -e POSTGRES_USER=demo -e POSTGRES_PASSWORD=demo \
  postgres:16-alpine

# 2. Build and run the API
docker build -t demo/api:local app/api
docker run -d --name api --network demo -p 8000:8000 \
  -e DB_HOST=db -e DB_PASSWORD=demo demo/api:local
```

Watch the readiness window (the API sleeps 15s, then connects):

```bash
# 503 in the first ~15s, then 200
curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/readyz

# 200 immediately, even while readyz is still 503 — it never touches the DB
curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/healthz
```

Exercise the real endpoints:

```bash
curl -s -X POST localhost:8000/items \
  -H 'Content-Type: application/json' -d '{"name":"widget"}'
curl -s localhost:8000/items
```

Build and serve the frontend page:

```bash
docker build -t demo/frontend:local app/frontend
docker run -d --name frontend --network demo -p 8080:80 demo/frontend:local
# open http://localhost:8080
```

Clean up:

```bash
docker rm -f db api frontend
docker network rm demo
```

## Repo layout

```
app/
  api/          FastAPI service (asyncpg pool, health endpoints)
    main.py
    requirements.txt
    Dockerfile
    .dockerignore
  frontend/     nginx static page (proxy config comes later, from a ConfigMap)
    index.html
    Dockerfile
```

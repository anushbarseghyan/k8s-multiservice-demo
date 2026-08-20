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

**Day 2 — running on a kind cluster.** All three services run on a single-node
[kind](https://kind.sigs.k8s.io/) cluster with a one-command bring-up
(`make up`). The nginx `/api` reverse-proxy is now wired up from a ConfigMap, so
the frontend button reaches the API end-to-end. The probes are set **correctly**
in this baseline:

- **liveness** → `/healthz` (never touches the DB, so a slow DB won't get the
  pod killed), and
- **readiness** → `/readyz` (503 until the DB pool works, so the ~15s
  `STARTUP_DELAY` keeps the pod out of the Service until it can actually serve).

Still to come: the three intentional **broken-probe experiments** (no readiness,
too-shallow readiness, too-aggressive liveness) that reproduce the failures this
project exists to teach.

See [`k8s/`](k8s/), the [`Makefile`](Makefile), and the design/plan docs under
[`docs/superpowers/`](docs/superpowers/).

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

## Run on kind (Day 2)

Prerequisites: Docker running, `kubectl`, and `kind` (`brew install kind`).

```bash
make up      # create cluster, build + load images, apply manifests, wait for Ready
make status  # kubectl get pods,svc
# open http://localhost:8080 and click the button
make down     # delete the cluster
```

Watch the "Running but not Ready" window — the api pod stays **out of the
Service** for ~15s after it starts, until `/readyz` goes green:

```bash
# empty for ~15s after the api pod (re)starts, then populated with one endpoint
kubectl get endpoints api

# reach the API through the nginx proxy
curl -s localhost:8080/api/items -w '\nHTTP %{http_code}\n'
curl -s -X POST localhost:8080/api/items \
  -H 'Content-Type: application/json' -d '{"name":"widget"}'
```

## Alternative: plain Docker (no Kubernetes)

Runs the two images directly under Docker. Note the frontend's button won't
reach the API this way — the nginx `/api` proxy only exists in the Kubernetes
ConfigMap, so use `curl` against the API port directly.

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
  frontend/     nginx static page (the /api proxy comes from the ConfigMap below)
    index.html
    Dockerfile
k8s/            Kubernetes manifests (applied to a kind cluster)
  kind-config.yaml   1-node cluster, maps localhost:8080 -> NodePort 30080
  postgres.yaml      Postgres Deployment (emptyDir) + db Service
  api.yaml           API Deployment + Service, liveness /healthz + readiness /readyz
  frontend.yaml      nginx /api-proxy ConfigMap + Deployment + NodePort Service
Makefile        up / down / status / logs-api helpers
docs/
  superpowers/  design spec and implementation plan for the k8s baseline
```

# Day 2 — Kubernetes baseline (working end-to-end on kind)

## Goal

Take the Day-1 application layer (FastAPI `api`, nginx `frontend`, and their
Dockerfiles) and stand it up on a local [kind](https://kind.sigs.k8s.io/)
cluster so the whole thing is green end-to-end: all pods `Ready`, and the
frontend's "GET /api/items" button reaches the API through the nginx `/api`
proxy.

This session builds the **correct** baseline only. The three intentional
"broken probe" experiments (no readiness, too-shallow readiness, too-aggressive
liveness) described in the README come in a **later** session — but the baseline
is deliberately shaped so each experiment is a small, isolated change from here.

Non-goals for this session:

- No experiments / failure-mode manifests yet.
- No Ingress, no Helm, no Kustomize overlays. Plain manifests, kept readable.
- No data durability for Postgres (ephemeral is fine — the teaching focus is
  probes, not storage).

## Prerequisite

`kind` is not currently installed. It will be installed with
`brew install kind` as the first step. `docker` is running and `kubectl` is
already present. `helm` is not needed.

## Architecture

```
  browser ──▶ localhost:8080 ──▶ kind node :30080 (NodePort)
                                   │
                                   ▼
                             frontend Service ──▶ nginx pod
                                                   │  (ConfigMap default.conf)
                                                   │  location /api/ ──▶ api:8000
                                                   ▼
                                              api Service (ClusterIP) ──▶ api pod
                                                                          │ asyncpg
                                                                          ▼
                                                                    db Service ──▶ postgres pod
```

All services live in the `default` namespace (single-purpose demo cluster; a
dedicated namespace would be ceremony without payoff here).

## Repo layout (new files)

```
k8s/
  kind-config.yaml      # 1-node cluster, extraPortMappings host 8080 -> 30080
  postgres.yaml         # Deployment (emptyDir) + Service  (ClusterIP "db")
  api.yaml              # Deployment + Service (ClusterIP "api"), correct probes
  frontend.yaml         # ConfigMap (nginx conf) + Deployment + Service (NodePort 30080)
Makefile                # up / down / status helpers
```

## Components

### kind-config.yaml
Single control-plane node. `extraPortMappings` binds host `127.0.0.1:8080` to
node port `30080` (the frontend NodePort), so `http://localhost:8080` reaches
the frontend directly — matching the Day-1 README story with no `port-forward`.

### postgres.yaml
- `Deployment` `postgres`, image `postgres:16-alpine`, 1 replica.
- Env: `POSTGRES_DB=demo`, `POSTGRES_USER=demo`, `POSTGRES_PASSWORD=demo`
  (matches the API defaults / Day-1 instructions).
- Storage: `emptyDir` mounted at `/var/lib/postgresql/data` — ephemeral by
  design.
- `Service` `db` (ClusterIP, port 5432) — the name the API resolves via
  `DB_HOST=db`.
- Probes: a simple `exec` `pg_isready -U demo` readiness probe so the API's own
  readiness only depends on the DB actually accepting connections. (Optional but
  cheap; keeps ordering honest.)

### api.yaml
- `Deployment` `api`, image `demo/api:local` (built from `app/api`, loaded into
  kind), `imagePullPolicy: IfNotPresent` so kind uses the loaded image and never
  reaches out to a registry.
- 1 replica for the baseline.
- Env: `DB_HOST=db`, `DB_PASSWORD=demo` (other DB_* use image defaults).
  `STARTUP_DELAY` left at its default 15 — the delay is intentional.
- Container port 8000.
- **Probes (the point of the whole project — set CORRECTLY here):**
  - `livenessProbe`  → `httpGet /healthz` : liveness never touches the DB, so a
    slow or briefly-unreachable DB will NOT get the pod killed.
  - `readinessProbe` → `httpGet /readyz` : returns 503 until the asyncpg pool is
    usable, so the ~15s `STARTUP_DELAY` window keeps the pod OUT of the `api`
    Service endpoints until it can actually serve DB-backed requests.
  - `readinessProbe` timings tuned so the pod is not marked ready before ~15s
    but does become ready shortly after the DB connects (e.g.
    `periodSeconds: 5`, `failureThreshold` high enough to cover the startup
    window; a `startupProbe` may be used instead to keep liveness/readiness
    thresholds tight — decided at plan time).
- `Service` `api` (ClusterIP, port 8000).

### frontend.yaml
- `ConfigMap` `frontend-nginx` holding `default.conf`:
  - `location /api/ { proxy_pass http://api:8000/; }` (this is the piece the
    Day-1 image intentionally omitted).
  - serves the static `index.html` for `/`.
- `Deployment` `frontend`, image `demo/frontend:local`, `IfNotPresent`. Mounts
  the ConfigMap at `/etc/nginx/conf.d/default.conf` (subPath).
- `Service` `frontend` (NodePort, `nodePort: 30080`, port 80).

### Makefile
- `make up` : `kind create cluster --config k8s/kind-config.yaml` → build both
  images → `kind load docker-image` both → `kubectl apply -f k8s/` →
  `kubectl wait --for=condition=Available` on the deployments (and/or
  `--for=condition=Ready` pods).
- `make down` : `kind delete cluster`.
- `make status` : `kubectl get pods,svc` for a quick health view.

## Data flow (happy path)

1. `make up` creates the cluster, loads images, applies manifests.
2. `postgres` becomes Ready (pg_isready).
3. `api` pod starts, serves HTTP immediately; `/healthz` → 200, `/readyz` → 503
   during the ~15s startup delay, so it stays out of the `api` Service.
4. After the delay the asyncpg pool connects, `CREATE TABLE items` runs,
   `/readyz` → 200, and the pod joins the `api` Service endpoints.
5. Browser hits `http://localhost:8080`; nginx serves the page; the button
   fetches `/api/items`, nginx proxies to `api:8000/items`, API returns rows.

## Error handling / observable behaviour

- The intentional not-ready window is *expected* and visible via
  `kubectl get pods` (READY 0/1 then 1/1) and `kubectl describe`.
- If Postgres is slow, the API retries (existing `CONNECT_ATTEMPTS` logic) and
  readiness stays 503 rather than the pod flapping.
- Because liveness is `/healthz`, none of the above restarts the api container —
  which is exactly the correct behaviour the later "aggressive liveness"
  experiment will violate on purpose.

## Verification (definition of done)

- `make up` completes and `kubectl get pods` shows `postgres`, `api`, and
  `frontend` all `1/1 Ready`.
- `kubectl get endpoints api` is empty for the first ~15s after the api pod
  starts, then populated (proves readiness gating works).
- `curl -s -o /dev/null -w '%{http_code}' localhost:8080/` → 200 (page).
- Clicking the button (or `curl localhost:8080/api/items`) returns 200 with a
  JSON list; a POST then GET round-trips an item through Postgres.
- `make down` removes the cluster cleanly.
- README updated: Status moves to "Day 2 — running on kind", with the new
  `k8s/` layout and `make up` / `make down` instructions.

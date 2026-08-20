# Day 2 — Kubernetes baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the Day-1 app (api + frontend + Postgres) on a local kind cluster with correct liveness/readiness probes and an nginx `/api` proxy, driven by a Makefile, green end-to-end.

**Architecture:** Plain Kubernetes manifests in `k8s/` applied to a single-node kind cluster. Images are built from the existing Dockerfiles and side-loaded with `kind load docker-image` (no registry). The frontend is reachable at `localhost:8080` via a kind `extraPortMappings` → NodePort. nginx reverse-proxies `/api/` to the `api` ClusterIP Service.

**Tech Stack:** kind, kubectl, Docker, Kubernetes manifests (Deployment/Service/ConfigMap), GNU make.

**Note on "tests":** This is infra, so verification = `kubectl`/`curl` observations, not unit tests. Each task defines an explicit expected observation before it's considered done.

---

### Task 0: Install kind

**Files:** none.

- [ ] **Step 1: Install kind**

Run: `brew install kind`
Expected: kind installed.

- [ ] **Step 2: Verify**

Run: `kind version`
Expected: prints a version (e.g. `kind v0.x`). `kubectl version --client` and `docker info` already work.

---

### Task 1: kind cluster config + cluster up

**Files:**
- Create: `k8s/kind-config.yaml`

- [ ] **Step 1: Write kind config**

`k8s/kind-config.yaml`:

```yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
name: k8s-demo
nodes:
  - role: control-plane
    extraPortMappings:
      - containerPort: 30080   # frontend NodePort
        hostPort: 8080
        listenAddress: "127.0.0.1"
        protocol: TCP
```

- [ ] **Step 2: Create the cluster**

Run: `kind create cluster --config k8s/kind-config.yaml`
Expected: ends with "You can now use your cluster with kubectl".

- [ ] **Step 3: Verify node Ready**

Run: `kubectl get nodes`
Expected: one node `k8s-demo-control-plane` in `Ready` state (may take ~20s).

- [ ] **Step 4: Commit**

```bash
git add k8s/kind-config.yaml
git commit -m "feat(k8s): kind cluster config with 8080->30080 port mapping"
```

---

### Task 2: Postgres Deployment + Service

**Files:**
- Create: `k8s/postgres.yaml`

- [ ] **Step 1: Write the manifest**

`k8s/postgres.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: postgres
  labels: { app: postgres }
spec:
  replicas: 1
  selector:
    matchLabels: { app: postgres }
  template:
    metadata:
      labels: { app: postgres }
    spec:
      containers:
        - name: postgres
          image: postgres:16-alpine
          ports:
            - containerPort: 5432
          env:
            - { name: POSTGRES_DB, value: demo }
            - { name: POSTGRES_USER, value: demo }
            - { name: POSTGRES_PASSWORD, value: demo }
            - { name: PGDATA, value: /var/lib/postgresql/data/pgdata }
          readinessProbe:
            exec:
              command: ["pg_isready", "-U", "demo", "-d", "demo"]
            initialDelaySeconds: 3
            periodSeconds: 5
          volumeMounts:
            - name: data
              mountPath: /var/lib/postgresql/data
      volumes:
        - name: data
          emptyDir: {}
---
apiVersion: v1
kind: Service
metadata:
  name: db
  labels: { app: postgres }
spec:
  selector: { app: postgres }
  ports:
    - port: 5432
      targetPort: 5432
```

Note: `PGDATA` points to a subdir of the mount because postgres:alpine refuses a non-empty (lost+found-free but sometimes non-empty) mount root; a subdir is the standard fix.

- [ ] **Step 2: Apply and verify (deferred to Task 5 full bring-up)**

This task's manifest is validated statically now; live verification happens in Task 5. Static check:
Run: `kubectl apply --dry-run=client -f k8s/postgres.yaml`
Expected: `deployment.apps/postgres created (dry run)` and `service/db created (dry run)`.

- [ ] **Step 3: Commit**

```bash
git add k8s/postgres.yaml
git commit -m "feat(k8s): postgres Deployment (emptyDir) + db Service"
```

---

### Task 3: API Deployment + Service with correct probes

**Files:**
- Create: `k8s/api.yaml`

- [ ] **Step 1: Write the manifest**

`k8s/api.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api
  labels: { app: api }
spec:
  replicas: 1
  selector:
    matchLabels: { app: api }
  template:
    metadata:
      labels: { app: api }
    spec:
      # Kubernetes injects legacy Docker-link env vars for every Service (the db
      # Service -> DB_PORT=tcp://<clusterIP>:5432), which shadows the app's own
      # DB_PORT and crashes it at startup. Disable the injection; the app reaches
      # Postgres via DNS (DB_HOST=db), not these vars.
      enableServiceLinks: false
      containers:
        - name: api
          image: demo/api:local
          imagePullPolicy: IfNotPresent
          ports:
            - containerPort: 8000
          env:
            - { name: DB_HOST, value: db }
            - { name: DB_PORT, value: "5432" }   # explicit; overrides any injected link var
            - { name: DB_PASSWORD, value: demo }
          # Liveness: shallow, never touches the DB. A slow/unready DB must NOT
          # get this container restarted.
          livenessProbe:
            httpGet: { path: /healthz, port: 8000 }
            initialDelaySeconds: 5
            periodSeconds: 10
            timeoutSeconds: 2
            failureThreshold: 3
          # Readiness: true readiness. 503 until the asyncpg pool works, so the
          # ~15s STARTUP_DELAY window keeps this pod OUT of the api endpoints.
          # A failing readiness probe never restarts the pod, so a generous
          # window here is safe.
          readinessProbe:
            httpGet: { path: /readyz, port: 8000 }
            initialDelaySeconds: 3
            periodSeconds: 3
            timeoutSeconds: 2
            failureThreshold: 20
---
apiVersion: v1
kind: Service
metadata:
  name: api
  labels: { app: api }
spec:
  selector: { app: api }
  ports:
    - port: 8000
      targetPort: 8000
```

- [ ] **Step 2: Static verify**

Run: `kubectl apply --dry-run=client -f k8s/api.yaml`
Expected: `deployment.apps/api created (dry run)` and `service/api created (dry run)`.

- [ ] **Step 3: Commit**

```bash
git add k8s/api.yaml
git commit -m "feat(k8s): api Deployment+Service, liveness /healthz readiness /readyz"
```

---

### Task 4: Frontend ConfigMap (nginx /api proxy) + Deployment + NodePort Service

**Files:**
- Create: `k8s/frontend.yaml`

- [ ] **Step 1: Write the manifest**

`k8s/frontend.yaml`:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: frontend-nginx
data:
  default.conf: |
    server {
        listen 80;
        server_name _;

        location / {
            root /usr/share/nginx/html;
            index index.html;
        }

        # The piece intentionally omitted from the Day-1 image: reverse-proxy
        # /api/ to the api Service. Trailing slashes strip the /api prefix, so
        # /api/items -> http://api:8000/items.
        location /api/ {
            proxy_pass http://api:8000/;
            proxy_set_header Host $host;
        }
    }
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: frontend
  labels: { app: frontend }
spec:
  replicas: 1
  selector:
    matchLabels: { app: frontend }
  template:
    metadata:
      labels: { app: frontend }
    spec:
      containers:
        - name: frontend
          image: demo/frontend:local
          imagePullPolicy: IfNotPresent
          ports:
            - containerPort: 80
          readinessProbe:
            httpGet: { path: /, port: 80 }
            initialDelaySeconds: 2
            periodSeconds: 5
          volumeMounts:
            - name: nginx-conf
              mountPath: /etc/nginx/conf.d/default.conf
              subPath: default.conf
      volumes:
        - name: nginx-conf
          configMap:
            name: frontend-nginx
---
apiVersion: v1
kind: Service
metadata:
  name: frontend
  labels: { app: frontend }
spec:
  type: NodePort
  selector: { app: frontend }
  ports:
    - port: 80
      targetPort: 80
      nodePort: 30080
```

- [ ] **Step 2: Static verify**

Run: `kubectl apply --dry-run=client -f k8s/frontend.yaml`
Expected: configmap, deployment, and service all print `(dry run)`.

- [ ] **Step 3: Commit**

```bash
git add k8s/frontend.yaml
git commit -m "feat(k8s): frontend nginx ConfigMap (/api proxy) + Deployment + NodePort"
```

---

### Task 5: Makefile driver + full bring-up

**Files:**
- Create: `Makefile`

- [ ] **Step 1: Write the Makefile**

`Makefile` (tabs, not spaces, for recipe lines):

```makefile
CLUSTER := k8s-demo

.PHONY: up down status images cluster apply wait logs-api

up: cluster images apply wait status

cluster:
	kind get clusters | grep -qx $(CLUSTER) || kind create cluster --config k8s/kind-config.yaml

images:
	docker build -t demo/api:local app/api
	docker build -t demo/frontend:local app/frontend
	kind load docker-image demo/api:local --name $(CLUSTER)
	kind load docker-image demo/frontend:local --name $(CLUSTER)

apply:
	kubectl apply -f k8s/postgres.yaml -f k8s/api.yaml -f k8s/frontend.yaml

wait:
	kubectl rollout status deploy/postgres --timeout=120s
	kubectl rollout status deploy/api --timeout=120s
	kubectl rollout status deploy/frontend --timeout=120s

status:
	kubectl get pods,svc

logs-api:
	kubectl logs -l app=api --tail=50

down:
	kind delete cluster --name $(CLUSTER)
```

Note: `apply` lists the three app manifests explicitly and omits `kind-config.yaml` (which is not a cluster resource). The `wait` on `deploy/api` covers the ~15s readiness window because rollout status waits for the pod to become Ready.

- [ ] **Step 2: Bring it all up**

Run: `make up`
Expected: images build + load, manifests apply, all three rollouts report "successfully rolled out", then `kubectl get pods` shows postgres/api/frontend `1/1 Running`.

- [ ] **Step 3: Verify readiness gating actually happened**

Immediately after the api pod starts (re-run if you missed the window), run:
`kubectl get endpoints api`
Expected: NOT ENDPOINTS for the first ~15s (readiness 503), then a single `10.x.x.x:8000` entry once `/readyz` goes 200. This proves the readiness probe gates traffic during STARTUP_DELAY.

- [ ] **Step 4: Verify end-to-end through the proxy**

Run: `curl -s -o /dev/null -w '%{http_code}\n' localhost:8080/`
Expected: `200` (nginx serves the page).

Run: `curl -s localhost:8080/api/items -w '\nHTTP %{http_code}\n'`
Expected: `[]` then `HTTP 200` (proxied to api:8000/items).

Run:
```bash
curl -s -X POST localhost:8080/api/items -H 'Content-Type: application/json' -d '{"name":"widget"}' -w '\nHTTP %{http_code}\n'
curl -s localhost:8080/api/items -w '\nHTTP %{http_code}\n'
```
Expected: POST returns the created row `HTTP 201`; GET now lists `widget` with `HTTP 200` — a full round-trip through nginx → api → Postgres.

- [ ] **Step 5: Commit**

```bash
git add Makefile
git commit -m "feat(k8s): Makefile driver (up/down/status) for the kind baseline"
```

---

### Task 6: Update README to Day-2 status

**Files:**
- Modify: `README.md` (Status section ~lines 45-52; add k8s layout + make instructions)

- [ ] **Step 1: Update the Status section**

Replace the "Day 1 — application layer only" Status paragraph with a "Day 2 — running on kind" description: the app now runs on a single-node kind cluster; the nginx `/api` proxy is wired via ConfigMap; probes are set correctly (liveness `/healthz`, readiness `/readyz`); the three broken-probe experiments are still future work.

- [ ] **Step 2: Add a "Run on kind" section**

Document:
```bash
brew install kind        # one-time
make up                  # cluster + build + load + apply + wait
# open http://localhost:8080  and click the button
make status
make down                 # tear down
```
And show how to watch the readiness window:
```bash
kubectl get endpoints api   # empty for ~15s after the api pod starts, then populated
```

- [ ] **Step 3: Update the Repo layout block**

Add the `k8s/` directory (kind-config.yaml, postgres.yaml, api.yaml, frontend.yaml) and the `Makefile` to the layout tree.

- [ ] **Step 4: Verify**

Run: `kubectl get pods` after `make up` to confirm the documented commands match reality.
Expected: all three pods `1/1 Running`.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: README to Day-2 (running on kind) with make up/down instructions"
```

---

## Self-Review

- **Spec coverage:** kind-config (Task 1) ✓; postgres Deployment+Service (Task 2) ✓; api Deployment+Service with correct liveness `/healthz`/readiness `/readyz` (Task 3) ✓; frontend ConfigMap `/api` proxy + NodePort (Task 4) ✓; Makefile up/down/status + image load via `kind load` (Task 5) ✓; browser at localhost:8080 via extraPortMappings→NodePort ✓; readiness-gating verification ✓; README update (Task 6) ✓. All spec "definition of done" items are covered by Task 5 Steps 2-4 and Task 6.
- **Placeholder scan:** No TBD/TODO; all manifests and the Makefile are complete literal content.
- **Type/name consistency:** Service names `db`/`api`/`frontend`, labels `app: <name>`, ports 5432/8000/80, nodePort 30080, image tags `demo/api:local` & `demo/frontend:local`, cluster name `k8s-demo`, and env `DB_HOST=db`/`DB_PASSWORD=demo` are consistent across kind-config, manifests, Makefile, and the api source defaults.

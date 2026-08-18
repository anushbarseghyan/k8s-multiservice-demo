# k8s-multiservice-demo

A three-service application containerized with Docker and deployed to a local Kubernetes cluster.

The deployment itself is deliberately ordinary. The point of this repo is the second half: a set of documented experiments in breaking health checks, to show the difference between a pod that is *running* and an application that *works*.

> **Status:** work in progress. Manifests and failure notes are being added as I build.

---

## Why this exists

A pod reporting `Running` tells you that a container process started. It tells you nothing about whether the application inside it can serve a request.

That gap is well documented and easy to read about. It is much less easy to recognise at three in the morning when a service is up, healthy by every dashboard, and returning errors. This repo is my attempt to cause each of those failures on purpose, in a controlled environment, and write down what they actually look like.

## Architecture

```
          ┌──────────────┐
          │   frontend   │   nginx, serves static page, proxies /api
          │  (NodePort)  │
          └──────┬───────┘
                 │
          ┌──────▼───────┐
          │     api      │   FastAPI, reads/writes the database
          │ (ClusterIP)  │   liveness + readiness probes live here
          └──────┬───────┘
                 │
          ┌──────▼───────┐
          │      db      │   PostgreSQL, backed by a PVC
          │ (ClusterIP)  │
          └──────────────┘
```

**Kubernetes objects used:** Deployment, Service, ConfigMap, Secret, PersistentVolumeClaim, and liveness/readiness/startup probes.

## Repository layout

```
.
├── app/
│   ├── api/            FastAPI service + Dockerfile
│   └── frontend/       nginx config, static page + Dockerfile
├── k8s/                manifests, applied in numeric order
├── docs/
│   └── probes.md       the failure experiments
└── Makefile            up / down / reset
```

## Running it locally

Requires [kind](https://kind.sigs.k8s.io/) (or minikube), `kubectl`, and Docker.

```bash
# create the cluster and load images
make up

# check what came up
kubectl get pods -n demo

# open the frontend
kubectl port-forward -n demo svc/frontend 8080:80
```

Then visit `http://localhost:8080`.

```bash
# tear everything down
make down
```

## The probe experiments

Full write-ups with output and screenshots are in [`docs/probes.md`](docs/probes.md). Summary:

### 1. No readiness probe

The API pod reports `Running` as soon as its process starts, before it has connected to the database. The Service adds it to the endpoint list immediately, and traffic is routed to a pod that cannot serve it.

**What it looks like:** healthy pod list, failing requests.

### 2. A readiness probe that checks too little

The probe hits an endpoint that returns `200` if the HTTP server is up, without touching the database. The pod is marked ready. The database connection is dead. Requests still fail.

**What it looks like:** identical to a healthy cluster, from the outside.

### 3. A liveness probe that is too aggressive

`timeoutSeconds` set below the API's real response time under load. Kubernetes concludes the container is hung and restarts it. Under sustained load this becomes a restart loop.

**What it looks like:** an outage caused by the mechanism intended to prevent one.

## Notes

This is a learning project, not a production reference. In particular: secrets are committed as plain manifests for reproducibility, there is no TLS, no resource limits tuning, and no Ingress controller. Do not copy the manifests into anything real without addressing those.

## Background

I taught biology for a long time before moving into infrastructure work. The habit I brought with me is that a result you have not tested is a hypothesis, not a finding — which is roughly how I have come to treat health checks.

---

**Author:** Anush Barseghyan · [LinkedIn](https://www.linkedin.com/in/anush-barseghyan/)

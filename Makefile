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

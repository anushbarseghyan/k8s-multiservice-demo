FROM nginx:1.27-alpine

# Only the static page is baked in. The nginx config (with the /api proxy to the
# FastAPI service) is intentionally NOT included here — it will come from a
# Kubernetes ConfigMap in a later session.
COPY index.html /usr/share/nginx/html/index.html

EXPOSE 80

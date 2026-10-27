# Pin the nginx:alpine release by its multi-platform manifest digest.
FROM nginx:alpine@sha256:a3554c0d0b48177b1413c26e01f695334428020d9df01243fbae3e11cfd17417

ENV GARDEN_SERVICE_HOST=garden-service-svc \
    GARDEN_SERVICE_PORT=80 \
    NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1

COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY index.html app.js style.css /usr/share/nginx/html/
COPY vendor/ /usr/share/nginx/html/vendor/

EXPOSE 80

# Build and push containers

Run from the repository root. All four images match the `v2` manifests.

```bash
docker build -t kdkanishka/garden-auth:v2 ./auth-service
docker build -t kdkanishka/garden-frontend:v2 ./frontend
docker build -t kdkanishka/garden-service:v2 ./garden-service
docker build -t kdkanishka/weather-analyzer:v2 ./weather-service

docker push kdkanishka/garden-auth:v2
docker push kdkanishka/garden-frontend:v2
docker push kdkanishka/garden-service:v2
docker push kdkanishka/weather-analyzer:v2
```

Use a new tag for each later release and update all application image references
in `k8s/`, including the migration Job. See [README.md](README.md) for deployment.

#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -gt 0 ]]; then
    if [[ $# -eq 1 && ( $1 == --help || $1 == -h ) ]]; then
        printf 'Usage: %s\nRemove Smart Garden services, workloads and pods from Minikube; retain postgres-data.\n' "$0"
        exit 0
    fi
    printf 'Usage: %s\n' "$0" >&2
    exit 1
fi

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
K8S_DIR="$ROOT_DIR/k8s"
KUBECTL=(kubectl --context=minikube --namespace=default)

if ! command -v kubectl >/dev/null 2>&1; then
    printf 'Required command not found: kubectl\n' >&2
    exit 1
fi

printf 'Removing Smart Garden services and workloads...\n'
"${KUBECTL[@]}" delete \
    -f "$K8S_DIR/frontend.yaml" \
    -f "$K8S_DIR/auth-service.yaml" \
    -f "$K8S_DIR/garden-service.yaml" \
    -f "$K8S_DIR/weather-service.yaml" \
    -f "$K8S_DIR/migration-job.yaml" \
    --ignore-not-found=true --wait=true --timeout=180s
# Do not delete postgres.yaml: it also defines the persistent database volume.
"${KUBECTL[@]}" delete deployment postgres --ignore-not-found=true --wait=true --timeout=180s
"${KUBECTL[@]}" delete service postgres-svc --ignore-not-found=true --wait=true --timeout=180s
"${KUBECTL[@]}" delete configmap garden-config --ignore-not-found=true --wait=true --timeout=180s
"${KUBECTL[@]}" delete secret garden-db-secret --ignore-not-found=true --wait=true --timeout=180s
"${KUBECTL[@]}" delete secret garden-auth-keys --ignore-not-found=true --wait=true --timeout=180s

printf 'Waiting for Smart Garden pods to terminate...\n'
"${KUBECTL[@]}" wait --for=delete pod \
    -l app.kubernetes.io/part-of=smart-garden --timeout=180s

printf '\nSmart Garden stopped. Database volume postgres-data is retained.\n'
printf 'Run ./deploy.sh to launch again.\n'

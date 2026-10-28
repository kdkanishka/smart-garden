#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -gt 0 ]]; then
    if [[ $# -eq 1 && ( $1 == --help || $1 == -h ) ]]; then
        printf 'Usage: %s\nDeploy the manifest image tags to Minikube and open the frontend.\nPrepare k8s/secret.yaml first.\n' "$0"
        exit 0
    fi
    printf 'Usage: %s\n' "$0" >&2
    exit 1
fi

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
K8S_DIR="$ROOT_DIR/k8s"
KUBECTL=(kubectl --context=minikube --namespace=default)

for command in minikube kubectl openssl; do
    if ! command -v "$command" >/dev/null 2>&1; then
        printf 'Required command not found: %s\n' "$command" >&2
        exit 1
    fi
done

if [[ ! -f "$K8S_DIR/secret.yaml" ]]; then
    printf 'Create %s/secret.yaml from secret.example.yaml and fill in matching database credentials first.\n' "$K8S_DIR" >&2
    exit 1
fi

on_error() {
    local status=$?
    printf '\nDeployment failed. Inspect pods and migration logs with:\n' >&2
    printf '  kubectl --context=minikube --namespace=default get pods\n' >&2
    printf '  kubectl --context=minikube --namespace=default logs job/garden-migrate\n' >&2
    exit "$status"
}
trap on_error ERR

printf 'Starting Minikube...\n'
minikube start --profile=minikube --cpus=4 --memory=4096

printf 'Applying configuration and starting PostgreSQL...\n'
"${KUBECTL[@]}" apply -f "$K8S_DIR/configmap.yaml"
"${KUBECTL[@]}" apply -f "$K8S_DIR/secret.yaml"
if [[ -z "$("${KUBECTL[@]}" get secret garden-auth-keys --ignore-not-found=true -o name)" ]]; then
    printf 'Generating JWT signing keys...\n'
    private_key="$(openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 2>/dev/null)"
    public_key="$(openssl pkey -pubout <<< "$private_key")"
    "${KUBECTL[@]}" create secret generic garden-auth-keys \
        --from-file=private.pem=<(printf '%s\n' "$private_key") \
        --from-file=public.pem=<(printf '%s\n' "$public_key")
    unset private_key public_key
fi
"${KUBECTL[@]}" apply -f "$K8S_DIR/postgres.yaml"
"${KUBECTL[@]}" rollout status deployment/postgres --timeout=180s

printf 'Running database migrations...\n'
"${KUBECTL[@]}" delete job garden-migrate --ignore-not-found=true --wait=true --timeout=180s
"${KUBECTL[@]}" apply -f "$K8S_DIR/migration-job.yaml"
"${KUBECTL[@]}" wait --for=condition=complete job/garden-migrate --timeout=180s

printf 'Rolling out application services...\n'
"${KUBECTL[@]}" apply -f "$K8S_DIR/auth-service.yaml"
"${KUBECTL[@]}" apply -f "$K8S_DIR/weather-service.yaml"
"${KUBECTL[@]}" apply -f "$K8S_DIR/garden-service.yaml"
# Refresh environment variables even when only the ConfigMap or Secret changed.
"${KUBECTL[@]}" rollout restart deployment/auth-service deployment/weather-analyzer deployment/garden-service
"${KUBECTL[@]}" rollout status deployment/auth-service --timeout=180s
"${KUBECTL[@]}" rollout status deployment/weather-analyzer --timeout=180s
"${KUBECTL[@]}" rollout status deployment/garden-service --timeout=180s
"${KUBECTL[@]}" apply -f "$K8S_DIR/frontend.yaml"
"${KUBECTL[@]}" rollout status deployment/frontend --timeout=180s

printf '\nDeployment ready. Opening the frontend.\n'
printf 'If Minikube keeps a tunnel running, leave this terminal open while using the app.\n'
minikube service frontend-svc --profile=minikube --namespace=default

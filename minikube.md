# Manual verification
kubectl config current-context
kubectl get deployments
kubectl get pods
kubectl get svc
kubectl get pvc
kubectl get jobs
kubectl get endpointslices

# Open the frontend
minikube service frontend-svc

# Print its current URL
minikube service frontend-svc --url
Fallback access:
kubectl port-forward service/frontend-svc 8080:80
# Open http://localhost:8080

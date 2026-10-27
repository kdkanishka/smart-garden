## Build containers
docker build -t kdkanishka/garden-frontend:v1 ./frontend
docker build -t kdkanishka/garden-service:v1 ./garden-service
docker build -t kdkanishka/weather-analyzer:v1 ./weather-service

## Push containers
docker push kdkanishka/garden-frontend:v1
docker push kdkanishka/garden-service:v1
docker push kdkanishka/weather-analyzer:v1

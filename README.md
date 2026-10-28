# Smart Garden

Manage your own plants and get watering advice from SMHI forecasts.
See [ARCHITECTURE.md](ARCHITECTURE.md) for the component map and Mermaid diagram.

## Login

| Username | Password | Stable user ID |
|---|---|---|
| `demo1` | `demo1` | `demo-user-1` |
| `demo2` | `demo2` | `demo-user-2` |

Each account sees only its own plants. There is no registration or users table.
Auth stores password hashes and issues RS256 JWTs valid for 30 minutes. Garden
validates tokens locally and stores their subject as each plant's `owner_id`.
Every plant query checks ownership. Other users' plants return `404`.

The login page and static files are public. The garden UI appears after login,
and all plant APIs require a Bearer token. Tokens stay in JavaScript memory;
refreshing, logging out, or expiry requires another login. Logout clears local
state but does not revoke a token before its expiry. No cookies or browser
storage are used. Weather and Kubernetes health checks need no user token.

## Build and deploy

Install Docker, Minikube, kubectl, and OpenSSL. Minikube needs 4 CPUs and 4096 MiB.
Build and push these public Docker Hub images from the repository root:

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

Copy `k8s/secret.example.yaml` to `k8s/secret.yaml` and replace the placeholders.
`POSTGRES_USER`, `POSTGRES_PASSWORD`, and `DATABASE_URL` must match. Use
`postgresql+psycopg://USER:PASSWORD@postgres-svc:5432/smart_garden` and URL-encode
the user/password in the URL. The real Secret file is Git-ignored.

```bash
./deploy.sh
```

This starts Minikube, applies configuration, creates JWT keys if missing,
starts PostgreSQL, runs migrations, rolls out all services, and opens the app.
Keep the terminal open if Minikube uses a frontend tunnel. Both scripts target
the `minikube` context and `default` namespace. The original plant migration now
includes ownership and must run on a fresh database, as agreed for this release.

The `garden-auth-keys` Secret is generated with OpenSSL. Auth receives only its
private key; Garden receives only its public key. Repeated deployments reuse
the existing keys. Configuration is in `k8s/configmap.yaml` (`JWT_ISSUER`,
`JWT_AUDIENCE`, and weather settings). Backends restart to reload configuration.

For new releases, use a fresh image tag and update all application references
in `k8s/`, including the migration Job. The script deploys those tags; it does
not build or push images. Never overwrite deployed tags (`IfNotPresent`).

```bash
./stop.sh
```

Shutdown removes services, workloads, configuration, and JWT keys. It retains
the cluster and `postgres-data` PVC. The next deployment creates fresh keys;
old tokens will no longer work. Stable demo user IDs preserve plant ownership.
Deleting the PVC or Minikube cluster discards the stored garden.

## API

All browser requests use the frontend's origin:

| Route | Purpose | Login required |
|---|---|---|
| `POST /api/auth/login` | JSON `username` and `password`; returns `access_token`, `token_type`, `expires_in`, `username` | No |
| `GET /api/plants` | List your plants with weather | Yes |
| `POST /api/plants` | Create your plant with `name`, `lat`, `lon` | Yes |
| `GET /api/plants/{id}` | Read your plant with weather | Yes |
| `PATCH /api/plants/{id}` | Update name or coordinates | Yes |
| `DELETE /api/plants/{id}` | Delete your plant | Yes |

Send `Authorization: Bearer TOKEN` for plant requests. Missing, invalid, or
expired tokens return `401`. Clients cannot submit `owner_id`. Weather failure
returns plants with `weather_status: "unavailable"`; mutations still work.

## Tests and local development

Use Python 3.12+ in a virtual environment:

```bash
python -m pip install -r auth-service/requirements-dev.txt -r garden-service/requirements-dev.txt
python -m pytest -c auth-service/pytest.ini auth-service/tests
python -m pytest -c garden-service/pytest.ini garden-service/tests
python -m pip install -r weather-service/requirements-dev.txt
python -m pytest -c weather-service/pytest.ini weather-service/tests
```

Garden's database integration tests run only when `TEST_DATABASE_URL` names a
disposable PostgreSQL database ending in `_test`. They migrate and clear that
database and verify isolation between users.

For local services, generate keys outside the repository:

```bash
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out /tmp/garden-private.pem
openssl pkey -in /tmp/garden-private.pem -pubout -out /tmp/garden-public.pem
export JWT_PRIVATE_KEY="$(< /tmp/garden-private.pem)"
export JWT_PUBLIC_KEY="$(< /tmp/garden-public.pem)"
export DATABASE_URL='postgresql+psycopg://USER:PASSWORD@localhost:5432/smart_garden'
python -m alembic -c garden-service/alembic.ini upgrade head
```

In separate terminals with those environment variables, launch Auth on port
8002, Garden on 8001, and Weather on 8000:

```bash
python -m uvicorn main:app --app-dir auth-service --port 8002
WEATHER_SERVICE_URL=http://localhost:8000 python -m uvicorn main:app --app-dir garden-service --port 8001
python -m uvicorn main:app --app-dir weather-service --port 8000
```

For the frontend container, set `GARDEN_SERVICE_HOST`/`PORT` and
`AUTH_SERVICE_HOST`/`PORT` to addresses reachable from that container.

## Acceptance checks

1. Log in as `demo1`, create a plant, log out, and log in as `demo2`. The plant
   must be absent. Its direct GET/PATCH/DELETE requests must return `404`.
2. Create a different plant as `demo2`; log back in as `demo1` and verify that
   only the first plant appears. Refresh and verify that login is required.
3. Check invalid credentials, token expiry, and a missing/modified token.
   Logout must clear the list, form, and selected marker, including during requests.
4. Confirm same-origin API requests carry Bearer tokens but map tile requests do
   not. Verify create/edit/delete and weather-unavailable behavior.
5. Check `kubectl get deployments,pods,services,pvc`: Frontend/Auth/Garden/Weather/
   PostgreSQL use `2/2/2/3/1` replicas. Only the frontend uses a NodePort.

This demo uses HTTP and intentionally simple demo passwords. PostgreSQL has one
replica and a 1 GiB PVC; persistence is not high availability or backup.

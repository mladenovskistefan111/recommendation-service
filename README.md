# recommendation-service

A gRPC service that returns product recommendations for the platform-demo e-commerce platform. For each request it fetches the **entire** product list from `product-catalog-service`, removes the products the user already has, and returns a random sample of up to 5 product IDs. It is stateless and is called only by `frontend`.

Part of the [platform-demo](https://github.com/platform-boutique) platform — microservices, observability (LGTM stack), GitOps (Argo CD), policy enforcement (Kyverno), infrastructure provisioning (Crossplane) and an internal developer portal (Backstage). See the master `README.md` in the platform-demo root for the whole-system view.

## At a glance

| | |
|---|---|
| **Language / runtime** | Python 3.12 in the image (3.13 pinned for local dev), `grpcio` |
| **Image** | `mladenovskistefan/recommendation-service` |
| **Protocol / port** | gRPC (HTTP/2, plaintext) on `8080` |
| **Metrics** | Pushed over OTLP/gRPC to Alloy → Mimir every 15 s (no scrape endpoint). Dashboard names: `rpc_server_duration_seconds_{bucket,count,sum}`, `rpc_server_requests_total`, `rpc_server_active_requests` |
| **Health** | `grpc.health.v1.Health/Check` on `8080` — static `SERVING`, does **not** reflect catalog availability |
| **State** | None |
| **Depends on** | `product-catalog-service:3550` (`ListProducts`) |
| **Called by** | `frontend` (`ListRecommendations`) |
| **gRPC reflection** | Not enabled — pass `-proto` to `grpcurl` |

## API

Package `hipstershop`, service `RecommendationService` ([`proto/recommendation.proto`](proto/recommendation.proto)); health in [`proto/health.proto`](proto/health.proto). The proto also embeds the client definition of `ProductCatalogService.ListProducts`.

| Method | Request | Response | Behaviour |
|---|---|---|---|
| `ListRecommendations` | `{ string user_id, repeated string product_ids }` | `{ repeated string product_ids }` | `candidates = all catalog IDs − request.product_ids`; returns `min(5, len(candidates))` random picks. `user_id` is only logged. If the catalog call fails → gRPC `INTERNAL` ("Failed to fetch product catalog.") |

The frontend then keeps at most 4 of the returned IDs and fetches each product from the catalog.

## Configuration

| Variable | Required | Default | Description |
|---|---|---|---|
| `PRODUCT_CATALOG_SERVICE_ADDR` | **Yes** | — | `host:port` (no scheme) of product-catalog gRPC. **The process exits at startup with `RuntimeError` if unset** |
| `PORT` | No | `8080` | gRPC listen port |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | No | `http://localhost:4317` | OTLP/**gRPC** endpoint for **traces, metrics and logs** (scheme required), e.g. `http://alloy.monitoring.svc.cluster.local:4317`. Legacy fallback: `COLLECTOR_SERVICE_ADDR` (`host:port`) |
| `OTEL_METRIC_EXPORT_INTERVAL` | No | `15000` | Milliseconds between metric pushes |
| `OTEL_SERVICE_NAME` | No | `recommendation-service` | `service.name` — becomes the Prometheus `job` and the Loki `service_name`; keep the default, dashboards depend on it |
| `SERVICE_VERSION` | No | `1.0.0` | `service.version` resource attribute |
| `PYROSCOPE_ADDR` | No | `http://localhost:4040` | Profiles use the Pyroscope SDK push protocol (**not** OTLP). Point it at Alloy (`http://alloy.monitoring.svc.cluster.local:4040`), which forwards to Pyroscope, or directly at Pyroscope |

Notes:

- The catalog channel is created with `grpc.insecure_channel`, which connects lazily — the service starts fine even if the catalog is down; individual RPCs then fail. No per-call deadline is set.
- Telemetry is **pushed**, not scraped: traces, metrics and logs share one OTLP/gRPC endpoint (Alloy); profiles go to Pyroscope (via Alloy). `service.instance.id` is the pod hostname, so replicas stay distinct in Mimir.
- The former Prometheus endpoint (`METRICS_PORT`) no longer exists. Logs keep going to stdout and are additionally exported over OTLP with trace ids.
- Telemetry and profiling cannot be switched off with a flag; with no reachable collector the exporters just log errors and the service keeps serving.

## Container image

Multi-stage [`Dockerfile`](Dockerfile):

| Stage | Base | What happens |
|---|---|---|
| builder | `python:3.12-alpine` | installs `g++`/`linux-headers`, `pip install -r requirements.in`, **compiles the protos** with `grpc_tools.protoc` into `generated/` and patches the relative import in `recommendation_pb2_grpc.py` |
| runtime | `python:3.12-alpine` | copies site-packages, generated stubs (`/app/src/generated`), `src/`; runs as non-root |

- **Entrypoint:** `python src/server.py` (working dir `/app`).
- **User:** `appuser` — UID `100`, GID `101` (verified). Set `runAsUser: 100` for `runAsNonRoot`.
- **EXPOSE:** `8080` only.
- **Docker `HEALTHCHECK`:** Python `grpc.channel_ready_future` against `localhost:${PORT:-8080}`.
- **Image size:** ~41 MB.
- **Reproducibility:** the image installs from `requirements.in` (direct deps pinned, transitive floating), not the fully pinned `requirements.txt`.
- The generated stubs are not in git; they only exist after the Docker build.

## Kubernetes deployment notes

**Workload:** stateless `Deployment`; scale freely.

**Ports to declare**

| Name | Port | Protocol | Expose via Service? |
|---|---|---|---|
| `grpc` | `8080` | TCP (HTTP/2) | Yes — `frontend` dials `recommendation-service:8080` |

**Service DNS name expected by callers:** `recommendation-service` (`frontend`: `RECOMMENDATION_SERVICE_ADDR=recommendation-service:8080`). `email-service` also listens on `8080` — different Service, no clash.

**Startup dependency:** `product-catalog-service` is *not* needed to start, but is needed to serve. No init container required; readiness stays green even when the catalog is down, so consider an app-level readiness check if you want the pod pulled from endpoints during catalog outages (not currently implemented).

**Probes**

```yaml
readinessProbe:
  grpc: { port: 8080 }
  initialDelaySeconds: 3
  periodSeconds: 5
livenessProbe:
  grpc: { port: 8080 }
  periodSeconds: 10
  failureThreshold: 3
```

**Graceful shutdown:** the process runs as PID 1 and installs no `SIGTERM` handler, so the kernel drops `SIGTERM` and each termination waits out the grace period. Set `terminationGracePeriodSeconds: 5`.

**Security context**

```yaml
securityContext:
  runAsNonRoot: true
  runAsUser: 100
  runAsGroup: 101
  allowPrivilegeEscalation: false
  capabilities: { drop: ["ALL"] }
  readOnlyRootFilesystem: true   # verified in-cluster; no writable /tmp was needed
```

**Resources** — requests sized from measured idle usage in the test stack (see the chart's `values.yaml` for limits):

```yaml
resources:
  requests: { cpu: 100m, memory: 64Mi }
  limits:   { cpu: "500m", memory: 192Mi }
```

**Config / secrets:** none.

**Telemetry:** pushed, not scraped — OTLP/gRPC to Alloy (`global.monitoring.otlpEndpoint`), profiles to Alloy `:4040`. Metrics carry `job="recommendation-service"` and `instance=<pod>`; no scrape annotations or `ServiceMonitor` are needed.

**Helm chart:** [`platform-demo-helm/recommendation-service`](https://github.com/platform-boutique/platform-demo-helm/tree/main/recommendation-service) (built on the shared `platform-common` library chart). It ships with HA defaults — 2 replicas,
HPA, PodDisruptionBudget, topology spread, NetworkPolicy, restricted pod security context, probes and the full env contract (see Configuration) —
and was verified on a 3-node kind cluster under the `restricted` Pod Security Standard.

**Deployment and admission policies:** Argo CD deploys the chart from `platform-demo-helm` (`envs/<env>/values/recommendation-service.yaml` pins the image digest; promotion is a pull request). In the cluster Kyverno enforces, for this service: pods named `app.kubernetes.io/name: recommendation-service` that run under the ServiceAccount of the same name (Cilium derives the network identity from that label), an image from this repository pinned by digest and signed by our release pipeline, a read-only root filesystem, resource requests and a memory limit, readiness and liveness probes and no API token. The Helm CI evaluates the rendered chart against these policies before merge; see [`platform-demo-helm/policies`](https://github.com/platform-boutique/platform-demo-helm/tree/main/policies).

## Observability

| Signal | Implementation | Destination |
|---|---|---|
| **Traces** | OpenTelemetry SDK, `GrpcInstrumentorServer` and `GrpcInstrumentorClient` (so the catalog call is a child span), OTLP/gRPC exporter (`BatchSpanProcessor`) | Alloy `:4317` → Tempo |
| **Metrics** | Hand-rolled RPC instruments, `PeriodicExportingMetricReader` over OTLP/gRPC every 15 s (replaces the Prometheus reader) | Alloy → Mimir |
| **Logs** | Python `logging` with a hand-built JSON format to stdout **and** an OTel `LoggingHandler` (OTLP/gRPC, with trace ids) | Alloy → Loki |
| **Profiles** | `pyroscope-io`, tagged with `version` | Alloy `:4040` → Pyroscope |

Metric names: `rpc_server_duration_seconds_{bucket,count,sum}` (buckets 1 ms … 10 s; the unit `s` yields `_seconds`), `rpc_server_requests_total`, `rpc_server_active_requests` (labels `rpc_system`, `rpc_service`, `rpc_method`, `rpc_grpc_status_code`). The JSON stdout line is built with a plain format string, so messages containing `"` can yield invalid JSON (the OTLP copy is unaffected).

Every series carries `job="recommendation-service"` (from `service.name`) and `instance=<pod name>` (`service.instance.id`), which is what the Grafana dashboards select on. Logs land in Loki with `service_name="recommendation-service"` and trace/span ids, so a log line links to its trace.

## Running locally

Requirements: Python 3.13 (see `.python-version`; 3.12 also works), Docker, a running `product-catalog-service` (or any gRPC stub on the configured address), [`grpcurl`](https://github.com/fullstorydev/grpcurl).

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

# generate the gRPC stubs (what the Dockerfile does) — required before running from source
mkdir -p src/generated && python -m grpc_tools.protoc -I proto \
  --python_out=src/generated --grpc_python_out=src/generated proto/recommendation.proto
touch src/generated/__init__.py
sed -i 's/^import recommendation_pb2 as recommendation__pb2/from . import recommendation_pb2 as recommendation__pb2/' src/generated/recommendation_pb2_grpc.py

PRODUCT_CATALOG_SERVICE_ADDR=localhost:3550 python src/server.py
pytest                        # tests (pytest.ini sets pythonpath=src)

docker build -t recommendation-service .
docker run --rm -p 8081:8080 \
  -e PRODUCT_CATALOG_SERVICE_ADDR=host.docker.internal:3550 recommendation-service
```

### Manual gRPC testing (no reflection — pass the proto)

```bash
grpcurl -plaintext -proto proto/recommendation.proto \
  -d '{"user_id": "u1", "product_ids": ["OLJCESPC7Z", "66VCHSJNUP"]}' \
  localhost:8080 hipstershop.RecommendationService/ListRecommendations

grpcurl -plaintext -proto proto/health.proto localhost:8080 grpc.health.v1.Health/Check
```

## Project structure

```
├── src/
│   ├── server.py        # gRPC server (10-thread pool), RecommendationService + Health servicers, catalog client stub
│   ├── telemetry.py     # OTel traces/metrics (server + client instrumentation), Prometheus server, Pyroscope; imported first
│   ├── generated/       # protoc output (git: only __init__.py; real stubs are built in Docker)
│   └── __init__.py
├── proto/               # recommendation.proto, health.proto
├── requirements.in / requirements.txt / requirements-dev.txt
├── pyproject.toml / pytest.ini / .python-version / .trivyignore
├── Dockerfile
└── docs/                # empty placeholder
```

## Known limitations

- Fetches the whole catalog on every request (fine for 9 products; will not scale to large catalogs).
- Recommendations are random — no ranking, no use of `user_id`.
- Health ignores catalog availability; no deadline on the catalog call.
- `SIGTERM` is ignored when running as PID 1 (see Graceful shutdown).

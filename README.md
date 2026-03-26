# recommendation-service

A gRPC service that returns product recommendations for users on the platform-demo e-commerce platform. It fetches the full product catalog from `product-catalog-service`, filters out products the user already has, and returns a random sample. Part of a broader microservices platform built with full observability, GitOps, and internal developer platform tooling.

## Overview

The service exposes one gRPC method:

| Method | Description |
|---|---|
| `ListRecommendations` | Returns up to 5 recommended product IDs for a given user, excluding products they already have |

**Port:** `8080` (gRPC)  
**Metrics Port:** `9464` (Prometheus)  
**Protocol:** gRPC  
**Language:** Python  
**Upstream dependency:** `product-catalog-service:3550`

## Requirements

- Python 3.12+
- Docker
- A running `product-catalog-service` instance
- `grpcurl` for manual testing

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `PRODUCT_CATALOG_SERVICE_ADDR` | Yes | Address of product catalog e.g. `product-catalog-service:3550` |
| `PORT` | No | gRPC server port (default: `8080`) |
| `METRICS_PORT` | No | Prometheus metrics port (default: `9464`) |
| `OTEL_SERVICE_NAME` | No | Service name reported to OTel (default: `recommendation-service`) |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | No | OTLP HTTP endpoint (default: `http://localhost:4318`) |
| `PYROSCOPE_ADDR` | No | Pyroscope profiling endpoint (default: `http://localhost:4040`) |
| `SERVICE_VERSION` | No | Service version tag (default: `1.0.0`) |

## Running Locally

### 1. Install dependencies

```bash
pip install pip-tools
pip-compile requirements.in
pip install -r requirements.txt
```

### 2. Run the service

```bash
PRODUCT_CATALOG_SERVICE_ADDR="localhost:3550" python src/server.py
```

### 3. Run with Docker

```bash
docker build -t recommendation-service .

docker run -p 8081:8080 -p 9096:9464 \
  -e PRODUCT_CATALOG_SERVICE_ADDR="product-catalog-service:3550" \
  recommendation-service
```

## Testing

### Manual gRPC testing

Install `grpcurl` then, from the service root:

```bash
# list recommendations for a user
grpcurl -plaintext \
  -proto proto/recommendation.proto \
  -d '{"user_id": "test-user", "product_ids": ["OLJCESPC7Z"]}' \
  localhost:8081 \
  hipstershop.RecommendationService/ListRecommendations

# health check
grpcurl -plaintext \
  -proto proto/health.proto \
  localhost:8081 \
  grpc.health.v1.Health/Check
```

### Generate traffic

```bash
while true; do
  grpcurl -plaintext \
    -proto proto/recommendation.proto \
    -d '{"user_id": "test-user", "product_ids": ["OLJCESPC7Z"]}' \
    localhost:8081 \
    hipstershop.RecommendationService/ListRecommendations
  sleep 1
done
```

## Project Structure

```
├── proto/
│   ├── recommendation.proto   # Service definition + ProductCatalog client stub
│   └── health.proto           # gRPC health check
├── src/
│   ├── server.py              # gRPC server, service implementation
│   ├── telemetry.py           # OpenTelemetry traces, Prometheus metrics, Pyroscope profiling
│   ├── generated/             # Proto-generated stubs (built in Dockerfile)
│   └── __init__.py
├── requirements.in            # Direct dependencies
├── requirements.txt           # Pinned lockfile
└── Dockerfile                 # Two-stage build with proto compilation
```

## Observability

- **Traces** — OTLP HTTP → Alloy → Tempo. Both inbound server spans and outbound productcatalog client calls are instrumented automatically via `GrpcInstrumentorServer` and `GrpcInstrumentorClient`.
- **Metrics** — Prometheus endpoint on `:9464/metrics`, scraped by Alloy → Mimir. Exposes `rpc_server_duration`, `rpc_server_requests_total`, `rpc_server_active_requests`.
- **Logs** — JSON structured logs to stdout, collected by Alloy via Docker socket → Loki.
- **Profiles** — Continuous CPU and heap profiling via Pyroscope SDK → Pyroscope.

## Part Of

This service is part of [platform-demo](https://github.com/mladenovskistefan111) — a full platform engineering project featuring microservices, observability (LGTM stack), GitOps (Argo CD), policy enforcement (Kyverno), infrastructure provisioning (Crossplane), and an internal developer portal (Backstage).
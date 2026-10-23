# Kubernetes

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
and was verified in the earlier local phase on a 3-node kind cluster (since deleted) under the `restricted` Pod Security Standard; it has not been verified on EKS yet.

**Deployment and admission policies:** Argo CD deploys the chart from `platform-demo-helm` (`envs/<env>/values/recommendation-service.yaml` pins the image digest; promotion is a pull request). In the cluster Kyverno enforces, for this service: pods named `app.kubernetes.io/name: recommendation-service` that run under the ServiceAccount of the same name (Cilium derives the network identity from that label), an image from this repository pinned by digest and signed by our release pipeline, a read-only root filesystem, resource requests and a memory limit, readiness and liveness probes and no API token. The Helm CI evaluates the rendered chart against these policies before merge; see [`platform-demo-helm/policies`](https://github.com/platform-boutique/platform-demo-helm/tree/main/policies).

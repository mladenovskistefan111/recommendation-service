# Observability

| Signal | Implementation | Destination |
|---|---|---|
| **Traces** | OpenTelemetry SDK, `GrpcInstrumentorServer` and `GrpcInstrumentorClient` (so the catalog call is a child span), OTLP/gRPC exporter (`BatchSpanProcessor`) | Alloy `:4317` → Tempo |
| **Metrics** | Hand-rolled RPC instruments, `PeriodicExportingMetricReader` over OTLP/gRPC every 15 s (replaces the Prometheus reader) | Alloy → Mimir |
| **Logs** | Python `logging` with a hand-built JSON format to stdout **and** an OTel `LoggingHandler` (OTLP/gRPC, with trace ids) | Alloy → Loki |
| **Profiles** | `pyroscope-io`, tagged with `version` | Alloy `:4040` → Pyroscope |

Metric names: `rpc_server_duration_seconds_{bucket,count,sum}` (buckets 1 ms … 10 s; the unit `s` yields `_seconds`), `rpc_server_requests_total`, `rpc_server_active_requests` (labels `rpc_system`, `rpc_service`, `rpc_method`, `rpc_grpc_status_code`). The JSON stdout line is built with a plain format string, so messages containing `"` can yield invalid JSON (the OTLP copy is unaffected).

Every series carries `job="recommendation-service"` (from `service.name`) and `instance=<pod name>` (`service.instance.id`), which is what the Grafana dashboards select on. Logs land in Loki with `service_name="recommendation-service"` and trace/span ids, so a log line links to its trace.

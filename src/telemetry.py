import os
import time

import pyroscope
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.instrumentation.grpc import GrpcInstrumentorClient, GrpcInstrumentorServer
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics._internal.aggregation import ExplicitBucketHistogramAggregation
from opentelemetry.sdk.metrics.view import View
from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import start_http_server

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_SERVICE_NAME = os.environ.get("OTEL_SERVICE_NAME", "recommendation-service")
_SERVICE_VERSION = os.environ.get("SERVICE_VERSION", "1.0.0")
_OTEL_ENDPOINT = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
_PYROSCOPE_ENDPOINT = os.environ.get("PYROSCOPE_ADDR", "http://localhost:4040")
_METRICS_PORT = int(os.environ.get("METRICS_PORT", "9464"))

# ---------------------------------------------------------------------------
# Resource
# ---------------------------------------------------------------------------

resource = Resource.create(
    {
        SERVICE_NAME: _SERVICE_NAME,
        SERVICE_VERSION: _SERVICE_VERSION,
    }
)

# ---------------------------------------------------------------------------
# Traces (OTLP HTTP → Alloy → Tempo)
# ---------------------------------------------------------------------------

trace_exporter = OTLPSpanExporter(
    endpoint=f"{_OTEL_ENDPOINT}/v1/traces",
)

tracer_provider = TracerProvider(resource=resource)
tracer_provider.add_span_processor(BatchSpanProcessor(trace_exporter))
trace.set_tracer_provider(tracer_provider)

# ---------------------------------------------------------------------------
# Metrics (Prometheus endpoint → Alloy → Mimir)
# ---------------------------------------------------------------------------

prometheus_reader = PrometheusMetricReader()

duration_view = View(
    instrument_name="rpc_server_duration",
    aggregation=ExplicitBucketHistogramAggregation(
        boundaries=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10],
    ),
)

meter_provider = MeterProvider(
    resource=resource,
    metric_readers=[prometheus_reader],
    views=[duration_view],
)
metrics.set_meter_provider(meter_provider)

# Start Prometheus HTTP server in a background thread
start_http_server(port=_METRICS_PORT, addr="0.0.0.0")  # nosec B104
print(f"Prometheus metrics server listening on :{_METRICS_PORT}/metrics")

# ---------------------------------------------------------------------------
# gRPC auto-instrumentation (patches both server and client before start)
# ---------------------------------------------------------------------------

GrpcInstrumentorServer().instrument()
GrpcInstrumentorClient().instrument()

# ---------------------------------------------------------------------------
# Custom gRPC server metrics
# ---------------------------------------------------------------------------

meter = metrics.get_meter("recommendation-service-grpc")

rpc_server_duration = meter.create_histogram(
    name="rpc_server_duration",
    description="Duration of inbound gRPC calls in seconds",
    unit="s",
)

rpc_server_requests_total = meter.create_counter(
    name="rpc_server_requests_total",
    description="Total number of inbound gRPC calls",
)

rpc_server_active_requests = meter.create_up_down_counter(
    name="rpc_server_active_requests",
    description="Number of in-flight gRPC calls",
)


def start_rpc_metrics(method: str):
    """
    Call at the start of each gRPC handler.
    Returns a callable: end(grpc_status_code) to record metrics.
    """
    start_time = time.monotonic()
    attrs = {
        "rpc_system": "grpc",
        "rpc_service": "hipstershop.RecommendationService",
        "rpc_method": method,
    }
    rpc_server_active_requests.add(1, attrs)

    def end(grpc_status_code: str):
        elapsed = time.monotonic() - start_time
        final_attrs = {**attrs, "rpc_grpc_status_code": grpc_status_code}
        rpc_server_duration.record(elapsed, final_attrs)
        rpc_server_requests_total.add(1, final_attrs)
        rpc_server_active_requests.add(-1, attrs)

    return end


# ---------------------------------------------------------------------------
# Pyroscope continuous profiling
# ---------------------------------------------------------------------------

pyroscope.configure(
    application_name=_SERVICE_NAME,
    server_address=_PYROSCOPE_ENDPOINT,
    tags={"version": _SERVICE_VERSION},
)

import logging
import os
import socket
import time

import pyroscope
from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.grpc import GrpcInstrumentorClient, GrpcInstrumentorServer
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics._internal.aggregation import ExplicitBucketHistogramAggregation
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import View
from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_SERVICE_NAME = os.environ.get("OTEL_SERVICE_NAME", "recommendation-service")
_SERVICE_VERSION = os.environ.get("SERVICE_VERSION", "1.0.0")
# OTLP/gRPC endpoint (scheme required, e.g. http://alloy:4317). COLLECTOR_SERVICE_ADDR (host:port)
# is kept as a legacy fallback.
_OTEL_ENDPOINT = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or (
    f"http://{os.environ['COLLECTOR_SERVICE_ADDR']}"
    if os.environ.get("COLLECTOR_SERVICE_ADDR")
    else "http://localhost:4317"
)
_OTEL_INSECURE = _OTEL_ENDPOINT.startswith("http://")
_PYROSCOPE_ENDPOINT = os.environ.get("PYROSCOPE_ADDR", "http://localhost:4040")
_METRIC_EXPORT_INTERVAL_MS = int(os.environ.get("OTEL_METRIC_EXPORT_INTERVAL", "15000"))

# ---------------------------------------------------------------------------
# Resource
# ---------------------------------------------------------------------------

resource = Resource.create(
    {
        SERVICE_NAME: _SERVICE_NAME,
        SERVICE_VERSION: _SERVICE_VERSION,
        # Distinguishes replicas: in Kubernetes the hostname is the pod name.
        "service.instance.id": os.environ.get("HOSTNAME") or socket.gethostname(),
    }
)

# ---------------------------------------------------------------------------
# Traces (OTLP gRPC → Alloy → Tempo)
# ---------------------------------------------------------------------------

tracer_provider = TracerProvider(resource=resource)
tracer_provider.add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint=_OTEL_ENDPOINT, insecure=_OTEL_INSECURE))
)
trace.set_tracer_provider(tracer_provider)

# ---------------------------------------------------------------------------
# Metrics (OTLP gRPC push → Alloy → Mimir)
# ---------------------------------------------------------------------------

duration_view = View(
    instrument_name="rpc_server_duration",
    aggregation=ExplicitBucketHistogramAggregation(
        boundaries=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10],
    ),
)

meter_provider = MeterProvider(
    resource=resource,
    metric_readers=[
        PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=_OTEL_ENDPOINT, insecure=_OTEL_INSECURE),
            export_interval_millis=_METRIC_EXPORT_INTERVAL_MS,
        )
    ],
    views=[duration_view],
)
metrics.set_meter_provider(meter_provider)

# ---------------------------------------------------------------------------
# Logs (OTLP gRPC → Alloy → Loki). The service logger keeps writing JSON to stdout;
# this handler additionally ships records (with trace/span ids) over OTLP.
# ---------------------------------------------------------------------------

logger_provider = LoggerProvider(resource=resource)
logger_provider.add_log_record_processor(
    BatchLogRecordProcessor(OTLPLogExporter(endpoint=_OTEL_ENDPOINT, insecure=_OTEL_INSECURE))
)
set_logger_provider(logger_provider)
logging.getLogger(_SERVICE_NAME).addHandler(LoggingHandler(level=logging.INFO, logger_provider=logger_provider))

# ---------------------------------------------------------------------------
# gRPC auto-instrumentation (patches grpc before server starts)
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
    Returns a callable: end(grpc_status_code_name) to record metrics.
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
# Pyroscope continuous profiling (SDK push protocol; PYROSCOPE_ADDR may point at Alloy)
# ---------------------------------------------------------------------------

pyroscope.configure(
    application_name=_SERVICE_NAME,
    server_address=_PYROSCOPE_ENDPOINT,
    tags={"version": _SERVICE_VERSION},
)

"""
Unit tests for telemetry.py — start_rpc_metrics helper.

All OTel SDK / Pyroscope / Prometheus side-effects are stubbed before import.
Instrument references are grabbed from the module after import so we hold
the exact MagicMock instances the module bound at init time.
"""

import sys
import types
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Install stubs before importing telemetry
# ---------------------------------------------------------------------------


def _install_otel_stubs():
    def _mod(name):
        m = types.ModuleType(name)
        sys.modules[name] = m
        return m

    _mod("opentelemetry")
    _mod("opentelemetry.sdk")

    trace_mod = _mod("opentelemetry.trace")
    trace_mod.set_tracer_provider = MagicMock()

    # Build a stable meter whose create_* methods each return a fixed mock
    fake_histogram = MagicMock()
    fake_counter = MagicMock()
    fake_updown = MagicMock()

    fake_meter = MagicMock()
    fake_meter.create_histogram.return_value = fake_histogram
    fake_meter.create_counter.return_value = fake_counter
    fake_meter.create_up_down_counter.return_value = fake_updown

    metrics_mod = _mod("opentelemetry.metrics")
    metrics_mod.set_meter_provider = MagicMock()
    metrics_mod.get_meter = MagicMock(return_value=fake_meter)

    for name in [
        "opentelemetry.sdk.trace",
        "opentelemetry.sdk.trace.export",
        "opentelemetry.exporter.otlp.proto.http",
        "opentelemetry.exporter.otlp.proto.http.trace_exporter",
        "opentelemetry.sdk.metrics",
        "opentelemetry.sdk.metrics.view",
        "opentelemetry.sdk.metrics._internal",
        "opentelemetry.sdk.metrics._internal.aggregation",
        "opentelemetry.exporter.prometheus",
        "opentelemetry.sdk.resources",
        "opentelemetry.instrumentation.grpc",
    ]:
        sys.modules.setdefault(name, types.ModuleType(name))

    sys.modules["opentelemetry.sdk.trace"].TracerProvider = MagicMock()
    sys.modules["opentelemetry.sdk.trace.export"].BatchSpanProcessor = MagicMock()
    sys.modules[
        "opentelemetry.exporter.otlp.proto.http.trace_exporter"
    ].OTLPSpanExporter = MagicMock()
    sys.modules["opentelemetry.sdk.metrics"].MeterProvider = MagicMock()
    sys.modules["opentelemetry.sdk.metrics.view"].View = MagicMock()
    sys.modules[
        "opentelemetry.sdk.metrics._internal.aggregation"
    ].ExplicitBucketHistogramAggregation = MagicMock()
    sys.modules["opentelemetry.exporter.prometheus"].PrometheusMetricReader = MagicMock()

    resource_mod = sys.modules["opentelemetry.sdk.resources"]
    resource_mod.Resource = MagicMock()
    resource_mod.SERVICE_NAME = "service.name"
    resource_mod.SERVICE_VERSION = "service.version"

    grpc_instr = sys.modules["opentelemetry.instrumentation.grpc"]
    grpc_instr.GrpcInstrumentorServer = MagicMock()
    grpc_instr.GrpcInstrumentorClient = MagicMock()


def _install_misc_stubs():
    prometheus_client = types.ModuleType("prometheus_client")
    prometheus_client.start_http_server = MagicMock()
    sys.modules["prometheus_client"] = prometheus_client

    pyroscope_mod = types.ModuleType("pyroscope")
    pyroscope_mod.configure = MagicMock()
    sys.modules["pyroscope"] = pyroscope_mod


_install_otel_stubs()
_install_misc_stubs()

import telemetry  # noqa: E402

# Grab instrument references bound at import time
_duration = telemetry.rpc_server_duration
_requests_total = telemetry.rpc_server_requests_total
_active_requests = telemetry.rpc_server_active_requests


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStartRpcMetrics:
    def setup_method(self):
        _duration.record.reset_mock()
        _requests_total.add.reset_mock()
        _active_requests.add.reset_mock()

    def test_returns_callable(self):
        end = telemetry.start_rpc_metrics("ListRecommendations")
        assert callable(end)

    def test_end_accepts_ok_status(self):
        end = telemetry.start_rpc_metrics("ListRecommendations")
        end("0")

    def test_end_accepts_error_status(self):
        end = telemetry.start_rpc_metrics("ListRecommendations")
        end("13")

    def test_active_requests_incremented_on_start(self):
        telemetry.start_rpc_metrics("ListRecommendations")
        assert _active_requests.add.call_args_list[0][0][0] == 1

    def test_active_requests_decremented_on_end(self):
        end = telemetry.start_rpc_metrics("ListRecommendations")
        end("0")
        calls = _active_requests.add.call_args_list
        assert len(calls) == 2
        assert calls[0][0][0] == 1
        assert calls[1][0][0] == -1

    def test_duration_recorded_on_end(self):
        end = telemetry.start_rpc_metrics("ListRecommendations")
        end("0")
        _duration.record.assert_called_once()
        assert _duration.record.call_args[0][0] >= 0

    def test_request_counter_incremented_on_end(self):
        end = telemetry.start_rpc_metrics("ListRecommendations")
        end("0")
        _requests_total.add.assert_called_once()
        assert _requests_total.add.call_args[0][0] == 1

    def test_attributes_contain_method_name(self):
        telemetry.start_rpc_metrics("ListRecommendations")
        attrs = _active_requests.add.call_args[0][1]
        assert attrs["rpc_method"] == "ListRecommendations"

    def test_attributes_contain_service_name(self):
        telemetry.start_rpc_metrics("ListRecommendations")
        attrs = _active_requests.add.call_args[0][1]
        assert "hipstershop.RecommendationService" in attrs["rpc_service"]

    def test_end_attributes_contain_grpc_status_code(self):
        end = telemetry.start_rpc_metrics("ListRecommendations")
        end("13")
        final_attrs = _duration.record.call_args[0][1]
        assert final_attrs["rpc_grpc_status_code"] == "13"

    def test_independent_calls_track_separately(self):
        end1 = telemetry.start_rpc_metrics("MethodA")
        end2 = telemetry.start_rpc_metrics("MethodB")
        end1("0")
        end2("0")
        assert _duration.record.call_count == 2
        assert _duration.record.call_args_list[0][0][1]["rpc_method"] == "MethodA"
        assert _duration.record.call_args_list[1][0][1]["rpc_method"] == "MethodB"
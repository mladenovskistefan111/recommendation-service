"""
Unit tests for RecommendationServiceServicer (src/server.py).

Strategy
--------
- telemetry, gRPC health stubs, and generated proto stubs are all replaced
  with lightweight fakes installed into sys.modules before server.py imports.
- The ProductCatalogServiceStub is injected via __init__ — no patching needed,
  just pass a mock stub directly.
- random.sample is patched where determinism matters.
"""

import logging
import sys
import types
from unittest.mock import MagicMock, patch

import grpc
import pytest

# ---------------------------------------------------------------------------
# Stub heavy imports before server.py is loaded
# ---------------------------------------------------------------------------


def _make_telemetry_stub():
    mod = types.ModuleType("telemetry")

    def start_rpc_metrics(method: str):
        def end(code: str):
            pass

        return end

    mod.start_rpc_metrics = start_rpc_metrics
    return mod


def _make_grpc_health_stubs():
    grpc_health = types.ModuleType("grpc_health")
    grpc_health_v1 = types.ModuleType("grpc_health.v1")

    class _HealthCheckResponse:
        SERVING = 1

        def __init__(self, status=1):
            self.status = status

    health_pb2 = types.ModuleType("grpc_health.v1.health_pb2")
    health_pb2.HealthCheckResponse = _HealthCheckResponse

    health_pb2_grpc = types.ModuleType("grpc_health.v1.health_pb2_grpc")
    health_pb2_grpc.HealthServicer = object
    health_pb2_grpc.add_HealthServicer_to_server = MagicMock()

    grpc_health.v1 = grpc_health_v1
    grpc_health_v1.health_pb2 = health_pb2
    grpc_health_v1.health_pb2_grpc = health_pb2_grpc

    sys.modules.setdefault("grpc_health", grpc_health)
    sys.modules.setdefault("grpc_health.v1", grpc_health_v1)
    sys.modules.setdefault("grpc_health.v1.health_pb2", health_pb2)
    sys.modules.setdefault("grpc_health.v1.health_pb2_grpc", health_pb2_grpc)

    return health_pb2, health_pb2_grpc


def _make_recommendation_pb2_stubs():
    class _Empty:
        pass

    class _ListRecommendationsResponse:
        def __init__(self):
            self.product_ids = []

        def extend(self, ids):
            self.product_ids.extend(ids)

    class _ListRecommendationsRequest:
        def __init__(self, user_id="", product_ids=None):
            self.user_id = user_id
            self.product_ids = product_ids or []

    recommendation_pb2 = types.ModuleType("generated.recommendation_pb2")
    recommendation_pb2.Empty = _Empty
    recommendation_pb2.ListRecommendationsResponse = _ListRecommendationsResponse
    recommendation_pb2.ListRecommendationsRequest = _ListRecommendationsRequest

    recommendation_pb2_grpc = types.ModuleType("generated.recommendation_pb2_grpc")
    recommendation_pb2_grpc.RecommendationServiceServicer = object
    recommendation_pb2_grpc.add_RecommendationServiceServicer_to_server = MagicMock()
    recommendation_pb2_grpc.ProductCatalogServiceStub = MagicMock

    generated = types.ModuleType("generated")
    generated.recommendation_pb2 = recommendation_pb2
    generated.recommendation_pb2_grpc = recommendation_pb2_grpc

    sys.modules.setdefault("generated", generated)
    sys.modules.setdefault("generated.recommendation_pb2", recommendation_pb2)
    sys.modules.setdefault("generated.recommendation_pb2_grpc", recommendation_pb2_grpc)

    return recommendation_pb2, recommendation_pb2_grpc


sys.modules["telemetry"] = _make_telemetry_stub()
_health_pb2, _health_pb2_grpc = _make_grpc_health_stubs()
_rec_pb2, _rec_pb2_grpc = _make_recommendation_pb2_stubs()

import server  # noqa: E402

# ---------------------------------------------------------------------------
# Helper — build a servicer with an injected catalog stub
# ---------------------------------------------------------------------------


def _make_servicer(catalog_stub):
    return server.RecommendationServiceServicer(product_catalog_stub=catalog_stub)


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


class TestListRecommendations_HappyPath:
    def test_returns_response_object(self, catalog_stub, valid_request, mock_grpc_context):
        servicer = _make_servicer(catalog_stub)
        result = servicer.ListRecommendations(valid_request, mock_grpc_context)
        assert hasattr(result, "product_ids")

    def test_no_grpc_error_on_success(self, catalog_stub, valid_request, mock_grpc_context):
        servicer = _make_servicer(catalog_stub)
        servicer.ListRecommendations(valid_request, mock_grpc_context)
        mock_grpc_context.set_code.assert_not_called()
        mock_grpc_context.set_details.assert_not_called()

    def test_calls_catalog_list_products(self, catalog_stub, valid_request, mock_grpc_context):
        servicer = _make_servicer(catalog_stub)
        servicer.ListRecommendations(valid_request, mock_grpc_context)
        catalog_stub.ListProducts.assert_called_once()

    def test_max_responses_capped_at_five(self, mock_grpc_context, make_catalog, make_request):
        stub = make_catalog(product_ids=["P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8"])
        servicer = _make_servicer(stub)
        result = servicer.ListRecommendations(make_request(product_ids=[]), mock_grpc_context)
        assert len(result.product_ids) <= server.RecommendationServiceServicer.MAX_RESPONSES

    def test_owned_products_excluded(self, mock_grpc_context, make_catalog, make_request):
        stub = make_catalog(product_ids=["P1", "P2", "P3"])
        servicer = _make_servicer(stub)
        result = servicer.ListRecommendations(make_request(product_ids=["P1", "P2"]), mock_grpc_context)
        assert "P1" not in result.product_ids
        assert "P2" not in result.product_ids

    def test_all_owned_returns_empty(self, mock_grpc_context, make_catalog, make_request):
        stub = make_catalog(product_ids=["P1", "P2"])
        servicer = _make_servicer(stub)
        result = servicer.ListRecommendations(make_request(product_ids=["P1", "P2"]), mock_grpc_context)
        assert result.product_ids == []

    def test_empty_catalog_returns_empty(self, empty_catalog_stub, valid_request, mock_grpc_context):
        servicer = _make_servicer(empty_catalog_stub)
        result = servicer.ListRecommendations(valid_request, mock_grpc_context)
        assert result.product_ids == []

    def test_recommended_ids_are_subset_of_catalog(self, mock_grpc_context, make_catalog, make_request):
        catalog_ids = {"P1", "P2", "P3", "P4", "P5"}
        stub = make_catalog(product_ids=list(catalog_ids))
        servicer = _make_servicer(stub)
        result = servicer.ListRecommendations(make_request(product_ids=[]), mock_grpc_context)
        assert set(result.product_ids).issubset(catalog_ids)

    def test_no_duplicate_recommendations(self, mock_grpc_context, make_catalog, make_request):
        stub = make_catalog(product_ids=["P1", "P2", "P3", "P4", "P5"])
        servicer = _make_servicer(stub)
        result = servicer.ListRecommendations(make_request(product_ids=[]), mock_grpc_context)
        assert len(result.product_ids) == len(set(result.product_ids))

    def test_randomness_respected(self, mock_grpc_context, make_catalog, make_request):
        """Patch random.sample to verify it is called with the candidate set."""
        stub = make_catalog(product_ids=["P1", "P2", "P3"])
        servicer = _make_servicer(stub)
        with patch("server.random.sample", return_value=["P3"]) as mock_sample:
            result = servicer.ListRecommendations(make_request(product_ids=[]), mock_grpc_context)
        mock_sample.assert_called_once()
        assert result.product_ids == ["P3"]


# ---------------------------------------------------------------------------
# Catalog RPC error handling
# ---------------------------------------------------------------------------


class TestListRecommendations_CatalogErrors:
    def _rpc_error(self, code=grpc.StatusCode.UNAVAILABLE, details="down"):
        err = MagicMock()
        err.__class__ = grpc.RpcError
        err.code.return_value = code
        err.details.return_value = details
        return err

    def test_catalog_rpc_error_sets_internal(self, mock_grpc_context, make_catalog, make_request):
        stub = make_catalog()
        stub.ListProducts.side_effect = self._rpc_error()
        servicer = _make_servicer(stub)
        result = servicer.ListRecommendations(make_request(), mock_grpc_context)
        assert hasattr(result, "product_ids")
        mock_grpc_context.set_code.assert_called_once_with(grpc.StatusCode.INTERNAL)

    def test_catalog_rpc_error_sets_details(self, mock_grpc_context, make_catalog, make_request):
        stub = make_catalog()
        stub.ListProducts.side_effect = self._rpc_error()
        servicer = _make_servicer(stub)
        servicer.ListRecommendations(make_request(), mock_grpc_context)
        detail = mock_grpc_context.set_details.call_args[0][0]
        assert "catalog" in detail.lower() or "fetch" in detail.lower()

    def test_catalog_rpc_error_returns_empty_response(self, mock_grpc_context, make_catalog, make_request):
        stub = make_catalog()
        stub.ListProducts.side_effect = self._rpc_error()
        servicer = _make_servicer(stub)
        result = servicer.ListRecommendations(make_request(), mock_grpc_context)
        assert result.product_ids == []


# ---------------------------------------------------------------------------
# Unexpected exception handling
# ---------------------------------------------------------------------------


class TestListRecommendations_UnexpectedErrors:
    def test_unexpected_exception_sets_internal(self, mock_grpc_context, make_catalog, make_request):
        stub = make_catalog()
        stub.ListProducts.side_effect = RuntimeError("boom")
        servicer = _make_servicer(stub)
        result = servicer.ListRecommendations(make_request(), mock_grpc_context)
        assert hasattr(result, "product_ids")
        mock_grpc_context.set_code.assert_called_once_with(grpc.StatusCode.INTERNAL)

    def test_unexpected_exception_returns_empty_response(self, mock_grpc_context, make_catalog, make_request):
        stub = make_catalog()
        stub.ListProducts.side_effect = RuntimeError("boom")
        servicer = _make_servicer(stub)
        result = servicer.ListRecommendations(make_request(), mock_grpc_context)
        assert result.product_ids == []

    def test_unexpected_exception_sets_details(self, mock_grpc_context, make_catalog, make_request):
        stub = make_catalog()
        stub.ListProducts.side_effect = RuntimeError("boom")
        servicer = _make_servicer(stub)
        servicer.ListRecommendations(make_request(), mock_grpc_context)
        mock_grpc_context.set_details.assert_called_once()


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


@pytest.fixture()
def rec_logger():
    """
    recommendation-service logger sets propagate=False — attach a capture
    handler directly so pytest caplog is bypassed.
    """
    log = logging.getLogger("recommendation-service")
    records = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Capture()
    log.addHandler(handler)
    yield records
    log.removeHandler(handler)


class TestListRecommendations_Logging:
    def test_logs_user_id_on_success(self, catalog_stub, mock_grpc_context, rec_logger, make_request):
        servicer = _make_servicer(catalog_stub)
        req = make_request(user_id="user-logged-999")
        servicer.ListRecommendations(req, mock_grpc_context)
        messages = [r.getMessage() for r in rec_logger]
        assert any("user-logged-999" in m for m in messages)

    def test_logs_error_on_catalog_failure(self, mock_grpc_context, make_catalog, make_request, rec_logger):
        stub = make_catalog()
        rpc_err = MagicMock()
        rpc_err.__class__ = grpc.RpcError
        stub.ListProducts.side_effect = rpc_err
        servicer = _make_servicer(stub)
        servicer.ListRecommendations(make_request(), mock_grpc_context)
        error_messages = [r.getMessage() for r in rec_logger if r.levelno == logging.ERROR]
        assert len(error_messages) >= 1


# ---------------------------------------------------------------------------
# HealthServicer
# ---------------------------------------------------------------------------


class TestHealthServicer:
    def test_check_returns_serving(self, mock_grpc_context):
        servicer = server.HealthServicer()
        response = servicer.Check(MagicMock(), mock_grpc_context)
        assert response.status == _health_pb2.HealthCheckResponse.SERVING

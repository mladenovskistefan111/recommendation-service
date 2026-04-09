"""
Shared pytest fixtures for the recommendation-service test suite.
"""

from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Lightweight proto message stand-ins
# ---------------------------------------------------------------------------


class _Product:
    def __init__(self, product_id):
        self.id = product_id


class _ListProductsResponse:
    def __init__(self, product_ids):
        self.products = [_Product(pid) for pid in product_ids]


class _ListRecommendationsRequest:
    def __init__(self, user_id="user-123", product_ids=None):
        self.user_id = user_id
        self.product_ids = product_ids or []


class _ListRecommendationsResponse:
    def __init__(self):
        self.product_ids = []

    def extend(self, ids):
        self.product_ids.extend(ids)


# ---------------------------------------------------------------------------
# Catalog stub factory
# ---------------------------------------------------------------------------


def make_catalog_stub(product_ids=None):
    """Return a mock ProductCatalogServiceStub pre-loaded with products."""
    stub = MagicMock()
    ids = product_ids if product_ids is not None else ["P1", "P2", "P3", "P4", "P5"]
    stub.ListProducts.return_value = _ListProductsResponse(ids)
    return stub


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def catalog_stub():
    """Default catalog stub with 5 products."""
    return make_catalog_stub()


@pytest.fixture()
def empty_catalog_stub():
    """Catalog stub that returns no products."""
    return make_catalog_stub(product_ids=[])


@pytest.fixture()
def valid_request():
    """Request with no products already owned — all catalog items are candidates."""
    return _ListRecommendationsRequest(user_id="user-abc", product_ids=[])


@pytest.fixture()
def request_with_owned():
    """Request where user already owns some products."""
    return _ListRecommendationsRequest(user_id="user-abc", product_ids=["P1", "P2"])


@pytest.fixture()
def mock_grpc_context():
    ctx = MagicMock()
    ctx.set_code = MagicMock()
    ctx.set_details = MagicMock()
    return ctx


@pytest.fixture()
def make_request():
    return _ListRecommendationsRequest


@pytest.fixture()
def make_catalog():
    return make_catalog_stub


@pytest.fixture()
def response_class():
    return _ListRecommendationsResponse

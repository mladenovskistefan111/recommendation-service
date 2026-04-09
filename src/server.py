# telemetry must be imported first to patch grpc before anything else
import logging
import os
import random
import time
from concurrent import futures

import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc

from generated import recommendation_pb2, recommendation_pb2_grpc
from telemetry import start_rpc_metrics  # noqa: F401, E402

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------

logger = logging.getLogger("recommendation-service")
handler = logging.StreamHandler()
handler.setFormatter(
    logging.Formatter(
        '{"timestamp": %(created)f, "severity": "%(levelname)s", "name": "%(name)s", "message": "%(message)s"}'
    )
)
logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False

# ---------------------------------------------------------------------------
# ProductCatalog gRPC client (upstream dependency)
# ---------------------------------------------------------------------------


def _build_product_catalog_stub() -> recommendation_pb2_grpc.ProductCatalogServiceStub:
    addr = os.environ.get("PRODUCT_CATALOG_SERVICE_ADDR", "")
    if not addr:
        raise RuntimeError("PRODUCT_CATALOG_SERVICE_ADDR environment variable not set")
    logger.info(f"Connecting to productcatalog at {addr}")
    channel = grpc.insecure_channel(addr)
    return recommendation_pb2_grpc.ProductCatalogServiceStub(channel)


# ---------------------------------------------------------------------------
# gRPC service implementation
# ---------------------------------------------------------------------------


class RecommendationServiceServicer(recommendation_pb2_grpc.RecommendationServiceServicer):
    MAX_RESPONSES = 5

    def __init__(self, product_catalog_stub):
        self._catalog = product_catalog_stub

    def ListRecommendations(self, request, context):
        end_metrics = start_rpc_metrics("ListRecommendations")
        try:
            # Fetch all products from the catalog
            cat_response = self._catalog.ListProducts(recommendation_pb2.Empty())
            all_ids = [p.id for p in cat_response.products]

            # Filter out products the user already has, then sample randomly
            candidates = list(set(all_ids) - set(request.product_ids))
            num_return = min(self.MAX_RESPONSES, len(candidates))
            recommended = random.sample(candidates, num_return)

            logger.info(f"ListRecommendations user_id={request.user_id} returning product_ids={recommended}")

            response = recommendation_pb2.ListRecommendationsResponse()
            response.product_ids.extend(recommended)
            end_metrics("0")  # OK
            return response

        except grpc.RpcError as err:
            logger.error(f"ProductCatalog RPC failed: {err}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Failed to fetch product catalog.")
            end_metrics("13")  # INTERNAL
            return recommendation_pb2.ListRecommendationsResponse()

        except Exception as err:
            logger.error(f"ListRecommendations failed: {err}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("An unexpected error occurred.")
            end_metrics("13")  # INTERNAL
            return recommendation_pb2.ListRecommendationsResponse()


class HealthServicer(health_pb2_grpc.HealthServicer):
    def Check(self, request, context):
        return health_pb2.HealthCheckResponse(
            status=health_pb2.HealthCheckResponse.SERVING,
        )


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------


def main():
    port = os.environ.get("PORT", "8080")

    product_catalog_stub = _build_product_catalog_stub()

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    recommendation_pb2_grpc.add_RecommendationServiceServicer_to_server(
        RecommendationServiceServicer(product_catalog_stub), server
    )
    health_pb2_grpc.add_HealthServicer_to_server(HealthServicer(), server)

    server.add_insecure_port(f"[::]:{port}")
    server.start()
    logger.info(f"RecommendationService gRPC server started on port {port}")

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.stop(0)


if __name__ == "__main__":
    main()

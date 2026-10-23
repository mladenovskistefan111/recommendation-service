# API

Package `hipstershop`, service `RecommendationService` ([`proto/recommendation.proto`](https://github.com/platform-boutique/app-recommendation-service/blob/main/proto/recommendation.proto)); health in [`proto/health.proto`](https://github.com/platform-boutique/app-recommendation-service/blob/main/proto/health.proto). The proto also embeds the client definition of `ProductCatalogService.ListProducts`.

| Method | Request | Response | Behaviour |
|---|---|---|---|
| `ListRecommendations` | `{ string user_id, repeated string product_ids }` | `{ repeated string product_ids }` | `candidates = all catalog IDs − request.product_ids`; returns `min(5, len(candidates))` random picks. `user_id` is only logged. If the catalog call fails → gRPC `INTERNAL` ("Failed to fetch product catalog.") |

The frontend then keeps at most 4 of the returned IDs and fetches each product from the catalog.

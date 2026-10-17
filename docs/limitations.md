# Known limitations

- Fetches the whole catalog on every request (fine for 9 products; will not scale to large catalogs).
- Recommendations are random — no ranking, no use of `user_id`.
- Health ignores catalog availability; no deadline on the catalog call.
- `SIGTERM` is ignored when running as PID 1 (see Graceful shutdown).

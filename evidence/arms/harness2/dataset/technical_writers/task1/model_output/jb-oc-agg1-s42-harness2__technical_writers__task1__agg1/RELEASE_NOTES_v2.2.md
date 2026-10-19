# Release Notes — DataSync API v2.2.0

## [2.2.0] (2024-12-15)

> DataSync API v2.2 introduces webhooks for event-driven integrations, resolves a sync-job reliability defect, and delivers a major bulk-insert performance improvement. No breaking changes and no required upgrade actions — the release is a drop-in update for existing v2.0/v2.1 integrations.

### Upgrade Steps

* No action required — existing integrations continue to work against v2.2 without changes.
* Recommended (optional):
  * Subscribe to webhooks (`POST /webhooks`) to replace polling patterns; provide a `secret` at creation time and verify the `X-Webhook-Signature` header (HMAC-SHA256 of the raw body) on every delivery. See `API_REFERENCE.md` → Webhooks.
  * Review bulk record inserts: `POST /datasets/{id}/records` accepts up to 1,000 records per request and is now ~3x faster — batching pays off double (see `MIGRATION_GUIDE_v2.0_to_v2.2.md` §6).
  * If you trigger dataset syncs, prefer `"mode": "incremental"` for recurring jobs (introduced in v2.1) and confirm your job-polling or `sync.completed`/`sync.failed` handling works with the fixed job lifecycle.

### Breaking Changes

* None in v2.2.0.
* Reminder — ongoing deprecation from v2.1: API key authentication is deprecated and will be removed in v3. JWT authentication (`POST /auth/login`, `Authorization: Bearer <token>`) is the supported method. API keys and the legacy `GET`/`POST /me/api-keys` endpoints remain functional during the deprecation window. New integrations must not use API keys. Migration guidance: `MIGRATION_GUIDE_v2.0_to_v2.2.md` §3.

### New Features

* **Webhooks** — subscribe an HTTPS endpoint to platform events instead of polling:
  * Endpoints: `GET /webhooks`, `POST /webhooks`, `GET /webhooks/{id}`, `DELETE /webhooks/{id}`.
  * Supported events: `dataset.created`, `dataset.updated`, `sync.completed`, `sync.failed`.
  * Note: there is no `dataset.deleted` event — `DELETE /datasets/{id}` is a soft delete (archive) that emits `dataset.updated`. Detect archival via the dataset's archived flag in the `dataset.updated` payload.
  * Optional signing secret: deliveries include an `X-Webhook-Signature` header with the HMAC-SHA256 signature of the raw request body for verification.
  * Full documentation, including signature verification steps and reference code: `API_REFERENCE.md` → Webhooks.

### Bug Fixes

* **Sync jobs occasionally stuck in `running`** — async sync jobs no longer remain in the `running` state indefinitely; `GET /jobs/{job_id}` now reliably transitions to `completed` or `failed`.

### Performance Improvements

* **Bulk record inserts are ~3x faster.** `POST /datasets/{id}/records` (single object or array, max 1,000 records per request) processes large batches roughly three times faster than in v2.1.

### Other Changes

* None.

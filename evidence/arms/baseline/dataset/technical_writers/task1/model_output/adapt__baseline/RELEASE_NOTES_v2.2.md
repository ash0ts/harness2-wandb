# Release Notes — DataSync API v2.2.0

## [2.2.0] (2024-12-15)

> DataSync API v2.2.0 introduces **webhooks**, fixes sync jobs that could get stuck in `running`, and makes bulk record inserts up to **3x faster**. There are **no breaking changes** in this release. Partners migrating from v2.0 should also review the [Migration Guide](MIGRATION_GUIDE_v2.0_to_v2.2.md) (covering the v2.1 JWT authentication change).

### Upgrade Steps

* No action is required to upgrade from v2.1.0 — existing integrations continue to work unchanged.
* [ACTION REQUIRED for v2.0 users] If you are still on **API-key authentication** (deprecated since v2.1, removal in v3), complete the JWT migration — see the [Migration Guide §2](MIGRATION_GUIDE_v2.0_to_v2.2.md#2-authentication-migration-api-keys--jwt).
* *Optional:* register a webhook to receive push notifications instead of polling:

  ```json
  POST /webhooks
  {
    "url": "https://hooks.example.com/datasync",
    "events": ["dataset.created", "dataset.updated", "sync.completed", "sync.failed"],
    "secret": "whsec_..."
  }
  ```

  Provide `secret` to receive `X-Webhook-Signature` (HMAC-SHA256) headers and verify deliveries — see the [API Reference → Webhooks](API_REFERENCE.md#webhooks).
* *Recommended for scheduled syncs:* switch `POST /datasets/{id}/sync` calls to `"mode": "incremental"` (added in v2.1) to transfer only changes since the last successful sync.

### Breaking Changes

* **None in v2.2.0.**
* Reminder of prior and upcoming changes:
  * v2.1.0 introduced the breaking switch to **JWT authentication**; API keys are deprecated and will be **removed in v3**.
  * `/me/api-keys` (list/create) remain functional during the deprecation period but should not be used in new code.

### New Features

* **Webhooks** — subscribe to platform events and receive `POST` deliveries at your own HTTPS endpoint:
  * Available events: `dataset.created`, `dataset.updated`, `sync.completed`, `sync.failed`.
  * Optional `secret` enables HMAC-SHA256 signing via the `X-Webhook-Signature` header; verification guidance is in the [API Reference](API_REFERENCE.md#verifying-webhook-signatures).
  * Manage subscriptions with `GET /webhooks`, `POST /webhooks`, `GET /webhooks/{id}`, `DELETE /webhooks/{id}` (all new in v2.2.0).
  * ⚠️ **Caveat:** there is **no `dataset.deleted` event**. `DELETE /datasets/{id}` performs a **soft delete** (sets the `archived` flag) and therefore fires `dataset.updated` with `archived: true`. Do not subscribe to `dataset.deleted` — it will never fire.
  * Webhook payload format: `{ "event": "...", "timestamp": "ISO8601", "data": { ... } }`.

### Bug Fixes

* **Sync jobs occasionally stuck in `running`** now transition correctly to `completed` or `failed`. Job status remains observable via `GET /jobs/{job_id}` (`status`: `pending|running|completed|failed`, `progress`: 0–100, `error`).

### Performance Improvements

* **Bulk record inserts** (`POST /datasets/{id}/records`) are up to **3x faster**. Batch up to 1,000 records per request. Note that bulk inserts draw from the separate `bulk` rate-limit category — see the [Troubleshooting Guide §2](TROUBLESHOOTING_GUIDE.md#2-rate-limiting-reference).

### Other Changes

* None.

# DataSync API — Troubleshooting Guide (v2.2)

Diagnostics for the issues integration partners report most often, including the three scenarios escalated by Customer Success after the v2.2 release. Pair this with `API_REFERENCE.md` (endpoint details) and `MIGRATION_GUIDE_v2.0_to_v2.2.md` (upgrade path).

---

## 0. Before you start: collect diagnostics

Every API response includes a `meta` object:

```json
{
  "success": false,
  "data": null,
  "error": { "code": "ERR_CODE", "message": "Human readable", "details": {} },
  "meta": { "request_id": "b2c1...", "timestamp": "2024-12-15T10:00:00Z" }
}
```

- Always capture **`meta.request_id`** — support uses it to locate the exact request in server logs.
- `400` validation failures include an `errors` array with field-specific messages — read it before guessing.
- `500` responses are logged server-side; retry with backoff and provide the `request_id` if the problem persists.

---

## 1. Authentication error diagnostics

### 1.1 `401 Unauthorized` — decision tree

Work down the list in order:

1. **Is the `Authorization` header present and well-formed?**
   Correct format: `Authorization: Bearer <access_token>`.
   - Missing header → `401`.
   - Wrong scheme (e.g., sending the token without the `Bearer` prefix, or a legacy API key credential in a bearer-style header) → `401`. This is the single most common cause of 401s reported after upgrading from v2.0.
2. **Has the access token expired?** Access tokens live **~1 hour**. If your process caches a token across long runs, refresh it (`POST /auth/refresh`) proactively — don't wait for the 401.
3. **Was the token invalidated?** `POST /auth/logout` invalidates tokens — and if called **without** a `refresh_token` in the body, it invalidates **all** tokens for the user. If another team member or job ran a full logout, every outstanding token stops working. Re-login.
4. **Is the refresh token the problem?** Distinguish the two refresh failures:
   - `400` from `POST /auth/refresh` → the refresh token is **invalid** — re-login with credentials.
   - `401` from `POST /auth/refresh` → the refresh token has **expired** (they last **7 days**) — re-login with credentials.
5. **Still authenticating with a legacy API key?** Keys remain functional during the deprecation window (removal planned for v3), but they are deprecated. If a request with a key returns 401, verify the key is still listed via `GET /me/api-keys` and that your client is sending the credential exactly as configured in your v2.0 integration. Plan the JWT migration regardless — see `MIGRATION_GUIDE_v2.0_to_v2.2.md`.

### 1.2 `401` vs `403` — which one means what

| Code | Meaning | Typical fix |
|---|---|---|
| `401` | Not authenticated — the server cannot verify who you are | Fix the token (sections 1.1) |
| `403` | Forbidden — authenticated, but not authorized for this resource | Use credentials/resource your user actually owns |

**Known behavior:** in some cases an authorization failure (valid token, insufficient permissions) is reported as `401` instead of `403`. If your token decodes as valid and unexpired but you still receive `401` on a resource you expect to access, treat it as a potential permissions issue: verify ownership of the resource, and if the problem persists, contact support with the `request_id` from `meta`.

### 1.3 Throttling on the login endpoint

`POST /auth/login` can return `429` (too many attempts) independently of other rate limits. Back off and retry later; do not loop login attempts in a retry storm. Batch services behind a shared token service so 40+ processes are not each performing logins.

### 1.4 Auth error quick reference

| Where | Response | Meaning | Action |
|---|---|---|---|
| Any endpoint | `401` | Not authenticated / token expired / token invalidated | Check header, refresh or re-login |
| Any endpoint | `403` | Authenticated but not authorized | Check resource ownership/permissions |
| `POST /auth/login` | `400` | Invalid credentials | Check email/password |
| `POST /auth/login` | `429` | Too many attempts | Back off; centralize token acquisition |
| `POST /auth/refresh` | `400` | Invalid refresh token | Re-login |
| `POST /auth/refresh` | `401` | Refresh token expired (7 days) | Re-login |

---

## 2. Rate limiting

### 2.1 Categories — the part most integrations miss

Rate limits are enforced **per category**, not only per plan. Every endpoint belongs to exactly one category:

| Category | Endpoints counted | Notes |
|---|---|---|
| `standard` | All endpoints **except** the two below (22 of 24 operations) | The default budget |
| `bulk` | `POST /datasets/{id}/records` only | Separate budget, **lower** than standard |
| `sync` | `POST /datasets/{id}/sync` only | Separate budget, independent of standard |

So on the Free tier, 60 standard `GET` requests plus 25 bulk inserts in the same minute does **not** fit: the 60 are fine, the 25 bulk requests exceed the 20/min bulk budget and the 21st onward get `429` — even though you are far under 100 req/min overall.

### 2.2 Limits by tier

| Tier | Standard | Bulk | Sync | Daily cap |
|---|---|---|---|---|
| Free | 100 req/min | **20 req/min** | Separate budget — current value returned in `X-RateLimit-Limit` | 10,000 req/day |
| Pro | 1,000 req/min | Separate budget — current value returned in `X-RateLimit-Limit` | Separate budget — current value returned in `X-RateLimit-Limit` | None |
| Enterprise | Custom | Custom | Custom | Custom |

The authoritative limit for your plan and category is always exposed in the `X-RateLimit-Limit` response header — read it once from a live call rather than hardcoding assumptions.

### 2.3 Rate limit response headers

Every response (success or `429`) carries:

| Header | Meaning |
|---|---|
| `X-RateLimit-Limit` | Maximum requests per window for that category |
| `X-RateLimit-Remaining` | Requests remaining in the current window |
| `X-RateLimit-Reset` | Unix timestamp when the window resets |

> Headers on `429` responses have been reliable since v2.1 (a v2.0 defect omitted them). If you built retry logic against v2.0 that couldn't see headers on 429s, it will work correctly now.

### 2.4 `429` handling and retry strategy

1. **Stop, don't hammer.** On `429`, wait until `X-RateLimit-Reset` before retrying — do not retry in a tight loop, which only prolongs the lockout.
2. **Use `X-RateLimit-Remaining` proactively.** Throttle or defer work when remaining budget approaches zero, instead of reacting to 429s.
3. **Batch bulk work.** `POST /datasets/{id}/records` accepts up to **1,000 records per request** — one large request consumes one bulk-budget slot and moves 50x the data of 50 small ones.
4. **Separate your workers.** Standard traffic and bulk/sync traffic draw from different budgets; schedule them on independent schedules so a sync job cannot starve interactive traffic.
5. **Space sync triggers.** Sync requests have their own category; trigger dataset syncs sequentially or on a jittered schedule rather than firing many at once.

---

## 3. Webhook issues

### 3.1 Not receiving callbacks — checklist

1. **List your actual subscriptions** with `GET /webhooks` and compare the `events` array to what you expect.
2. **Are you subscribed to an event that doesn't exist?** The valid events are exactly: `dataset.created`, `dataset.updated`, `sync.completed`, `sync.failed`. There is **no `dataset.deleted` event** — a subscription entry for it never fires.
3. **Looking for deletion notifications?** `DELETE /datasets/{id}` is a **soft delete** (archive): the dataset's archived flag is set and a **`dataset.updated`** event is delivered. Detect archival by inspecting the archived/status state in the `dataset.updated` payload. (Record-level `DELETE /datasets/{id}/records/{record_id}` is a hard delete; it does not emit a dataset-level event.)
4. **Waiting for job results?** Sync jobs emit `sync.completed` / `sync.failed`; you can replace `GET /jobs/{job_id}` polling with these events.
5. **Is your receiver reachable?** Confirm the `url` registered on the webhook is publicly reachable via HTTPS.

### 3.2 Signature verification failures

When a webhook is created with a `secret`, deliveries carry `X-Webhook-Signature` (HMAC-SHA256 of the raw body). If verification fails:

| Symptom | Cause | Fix |
|---|---|---|
| Every delivery fails verification | Signature computed over re-serialized JSON instead of the **raw body bytes** | Hash the raw body exactly as received |
| Fails only behind a proxy/framework | Body was consumed/re-encoded before your handler | Access the raw request body in your handler |
| Fails on some webhooks only | Multiple subscriptions with different/rotated secrets | Confirm you use the secret that belongs to each webhook; delete stale subscriptions |
| Works locally, fails in production | Secret mismatch between environments | Keep per-environment secrets in your secret store |

Full verification steps and reference code: `API_REFERENCE.md` → Webhooks → *Webhook signature verification*.

### 3.3 Payload shape

Every delivery uses the same envelope — parse defensively and route on the `event` field:

```json
{
  "event": "dataset.updated",
  "timestamp": "ISO8601",
  "data": { "..." : "event-specific fields" }
}
```

---

## 4. Common migration pitfalls (v2.0 → v2.2)

Compiled from recurring support patterns. Each row states the mistake, the observable symptom, and the correct behavior.

| # | Pitfall | Symptom | Correct behavior |
|---|---|---|---|
| 1 | Assuming old auth still works unchanged | `401` after upgrade | JWT is the standard since v2.1; API keys are deprecated but still work during the window — verify header format first (`MIGRATION_GUIDE` §3) |
| 2 | Caching access tokens beyond their ~1 h lifetime | Sudden `401`s after an hour of operation | Refresh proactively or retry once on 401 after refreshing |
| 3 | Subscribing to `dataset.deleted` | No callbacks when datasets are archived | Event doesn't exist; `DELETE /datasets/{id}` is a soft delete that emits `dataset.updated` |
| 4 | Treating dataset deletion as permanent | Dataset "gone" but still in storage | Soft delete sets the archived flag; retrieve with `GET /datasets?status=archived`. Record deletion (`.../records/{record_id}`) **is** a hard delete |
| 5 | Hammering bulk inserts | `429` after ~20 requests on Free tier | Bulk is its own rate category (20 req/min on Free); batch up to 1,000 records per request |
| 6 | Assuming one global rate limit | Unexpected `429` while "well under the limit" | Limits are per category: standard / bulk / sync |
| 7 | Requesting `limit` > 100 on list endpoints | Truncated results or validation error | `limit` defaults to 20, **max 100**; page through with `page` |
| 8 | Invalid dataset names | `400` on create | Name must be 3–100 characters |
| 9 | Duplicate dataset names | `409` on create | Names must be unique — catch 409 and reuse/update the existing dataset |
| 10 | Missing `Content-Type: application/json` | `400` on POST/PUT/PATCH | The header is required for all body-sending requests |
| 11 | Sending `filter` as an unencoded object | Filter ignored or `400` | The `filter` parameter is **JSON-encoded** (URL-encode it in query strings) |
| 12 | Assuming `sort` descends by default | "Wrong" ordering | `order` defaults to `asc` — set `order=desc` explicitly |
| 13 | Running syncs in `full` mode out of habit | Slow, heavy sync jobs | `mode` defaults to `full`; pass `incremental` for recurring syncs (v2.1 feature) |
| 14 | Treating the `202` from `/sync` as completion | Missing data; no job tracking | `202` = job accepted. Track via `GET /jobs/{job_id}` or `sync.completed` / `sync.failed` webhooks |
| 15 | Missing rate limit headers on 429s (v2.0 behavior) | Retry logic without backoff timing | Fixed in v2.1 — headers are present on all responses, including `429` |
| 16 | Ignoring `422` | Confusing business-rule failures with validation bugs | `400` = field validation (see `errors` array); `422` = business rule violation |
| 17 | Retrying `500` immediately and repeatedly | Extended disruption | Back off; include `meta.request_id` in the support ticket |

---

## 5. Escalated scenario walkthroughs

The three Customer Success escalations that triggered this documentation, anonymized, with the resolution paths.

### Scenario A — "JWT migration breaking our integration" (Enterprise, dozens of microservices)

- **Symptom:** `401` errors after upgrading; API keys still visible in the dashboard; legacy key endpoints still return 200; no published transition guidance.
- **Diagnosis:** v2.1 switched to JWT and deprecated API keys. Keys remain valid during the deprecation window (removal in v3), and parallel authentication is supported — so a working integration can move service by service.
- **Resolution:** staged migration — keep keys for un-migrated services, stand up a shared token service for JWT issuance/refresh, migrate waves of services, retire the key when the last wave completes. Decision criteria and before/after code: `MIGRATION_GUIDE_v2.0_to_v2.2.md` §3.
- **Prevention:** never create new API keys; treat `GET`/`POST /me/api-keys` as transition-only.

### Scenario B — "Webhook not firing for deleted datasets" (Pro tier)

- **Symptom:** webhook subscribed to five event names; callbacks received for `created`/`updated` but never for `dataset.deleted`.
- **Diagnosis:** `dataset.deleted` is not an implemented event. `DELETE /datasets/{id}` performs a soft delete (archive) that triggers `dataset.updated`.
- **Resolution:** remove `dataset.deleted` from the subscription; subscribe to `dataset.updated` and branch on the archived flag in the payload (`MIGRATION_GUIDE_v2.0_to_v2.2.md` §5.3). Valid events: `dataset.created`, `dataset.updated`, `sync.completed`, `sync.failed`.
- **Prevention:** validate event names against the table in §3.1 when configuring webhooks.

### Scenario C — "Rate limiting inconsistent between endpoints" (Free tier)

- **Symptom:** ~20 bulk inserts trigger `429` while standard GET traffic at 100 req/min works fine; docs appeared to promise a single 100 req/min limit.
- **Diagnosis:** limits are enforced per category. Bulk inserts belong to the `bulk` category (20 req/min on Free), independent of the standard budget.
- **Resolution:** batch up to 1,000 records per request, read `X-RateLimit-Remaining`/`X-RateLimit-Reset` and pace jobs accordingly (`MIGRATION_GUIDE_v2.0_to_v2.2.md` §6; §2 of this guide).
- **Prevention:** design sync/insert jobs against the per-category matrix in §2.2, not the single-plan number.

---

## 6. Error code reference

| HTTP code | Meaning | What to do |
|---|---|---|
| `400` | Bad request — validation failed | Read the field-level `errors` array in the body; fix the payload. (On `/auth/refresh`: invalid refresh token → re-login) |
| `401` | Not authenticated | Follow the decision tree in §1.1 |
| `403` | Forbidden — authenticated but not authorized | Check resource ownership/permissions |
| `404` | Not found — including resources owned by another user | Verify the ID; note `404` is also returned for datasets you don't own |
| `409` | Conflict | E.g., duplicate dataset name — reuse or rename |
| `422` | Unprocessable entity — business rule violation | The request is syntactically valid but violates a rule; adjust the operation |
| `429` | Rate limited | Wait until `X-RateLimit-Reset`; check which category you exhausted (§2) |
| `500` | Server error | Retry with backoff; if persistent, contact support with `meta.request_id` |

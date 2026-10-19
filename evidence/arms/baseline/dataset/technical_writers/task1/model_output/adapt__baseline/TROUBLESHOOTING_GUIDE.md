# DataSync API Troubleshooting Guide (v2.2)

Practical diagnostics for the issues support sees most often — drawn from recently escalated customer cases — plus reference material on rate limits, migration pitfalls, and authentication errors.

**Applies to:** `https://api.datasync.io/v2` · Test changes against `https://sandbox.api.datasync.io/v2` (resets daily).

**Before you dig in:** every API response includes `meta.request_id`. If you contact support, always include it (mandatory for `500` errors) — it lets us locate your exact request.

---

## 1. Escalated issues — symptoms, causes, fixes

### 1.1 "401 errors after upgrading from v2.0"

**Symptom**

- Integration worked on v2.0, returns `401` after the upgrade.
- API keys still appear valid in the dashboard.
- Common with large deployments (e.g., dozens of microservices sharing one credential).

**Cause**

API keys were **deprecated in v2.1** in favor of JWT authentication. Keys still work during the transition period (removal is scheduled for **v3**), but services that lost valid credentials — or that never implemented token refresh — fail with `401`.

**Fix**

1. Confirm which credential each service sends. Key-based requests must use your existing v2.0 auth header format, unchanged.
2. If a service sends `Authorization: Bearer <token>` with an API key, it will fail — Bearer is for **JWT access tokens** from `POST /auth/login`.
3. For JWT: on `401`, refresh the access token (`POST /auth/refresh`) and retry once. Access tokens expire after **1 hour**.
4. For large fleets, migrate in phases with a central token broker — full walkthrough in the [Migration Guide §2.5](MIGRATION_GUIDE_v2.0_to_v2.2.md#25-escalated-scenario--jwt-migration-breaking-our-integration-47-microservices).
5. `/me/api-keys` still working **does** mean keys are still valid — but it's deprecated; plan removal before v3.

**Diagnostic shortcut**

```bash
# If this succeeds, your credential is valid and the problem is in how a
# specific service is sending it (or the token expired mid-flight):
curl -H "Authorization: Bearer <access_token>" https://api.datasync.io/v2/me
```

### 1.2 "Webhook never fires when a dataset is deleted"

**Symptom**

You subscribed to `dataset.deleted` and never receive callbacks when datasets are deleted/archived — while `dataset.created` and `dataset.updated` work fine.

**Cause — this is expected behavior, not a bug**

- **`dataset.deleted` is not a real event.** The complete event list is:
  - `dataset.created`
  - `dataset.updated`
  - `sync.completed`
  - `sync.failed`
- `DELETE /datasets/{id}` is a **soft delete** (it sets the dataset's `archived` flag). Because the dataset still exists, archiving fires **`dataset.updated`** — with `archived: true` in the payload.

**Fix**

1. Remove `dataset.deleted` from your subscription (it silently never matches).
2. Subscribe to `dataset.updated` and branch on the `archived` flag:

```python
def handle_webhook(payload):
    if payload["event"] == "dataset.updated" and payload["data"].get("archived"):
        handle_archive(payload["data"]["id"])     # your "deletion" logic
```

3. If you genuinely need permanent dataset destruction today, there is no API for it — archive is the deletion model.

**Verify your subscription**

```bash
curl -H "Authorization: Bearer <access_token>" https://api.datasync.io/v2/webhooks
# Check that "events" contains only valid events, and that your secret is set
# so deliveries carry X-Webhook-Signature.
```

### 1.3 "Rate limits hit much faster on bulk inserts"

**Symptom**

On the Free tier (100 requests/min documented), you can only complete ~20 bulk record inserts per minute before receiving `429` — while regular `GET` requests comfortably reach 100/min.

**Cause**

Rate limits are enforced **per endpoint category**, not just per account:

- Regular reads/writes → **`standard`** category (the documented 100/min on Free).
- `POST /datasets/{id}/records` (bulk inserts) → **`bulk`** category — a **separate, lower limit** (Free tier: **20 bulk requests/min**).
- `POST /datasets/{id}/sync` → **`sync`** category — also separate.

**Fix — design your sync jobs around the categories**

```python
import time

def bulk_insert_throttled(dataset_id, records, max_per_minute=18):
    for chunk in chunks(records, 1000):              # hard cap: 1000 records/request
        resp = post(f"{BASE}/datasets/{dataset_id}/records", json={"records": chunk})
        if resp.status_code == 429:
            reset = int(resp.headers["X-RateLimit-Reset"])
            time.sleep(max(reset - time.time(), 1))  # wait for the window to reset
            continue
        remaining = resp.headers.get("X-RateLimit-Remaining")
        if remaining is not None and int(remaining) <= 1:
            time.sleep(60)                           # avoid hitting the wall mid-batch
```

Full per-tier, per-category limits: [§2 Rate limiting reference](#2-rate-limiting-reference).

---

## 2. Rate limiting reference

### 2.1 How it works

- Limits are enforced **per account (tier)** **and per category**. A `bulk` request and a `standard` request draw from **separate budgets** — they don't exhaust each other.
- Categories are assigned per endpoint: most endpoints are `standard`; bulk record inserts are `bulk`; sync triggers are `sync` (see the [endpoint index](API_REFERENCE.md#endpoint-index)).
- Rate-limit headers are returned on **every** response — including `429` (this was fixed in v2.1; older versions omitted them on `429`).

### 2.2 Limits by tier and category

| Category | Free tier | Pro tier | Enterprise |
|----------|-----------|----------|------------|
| **standard** | 100 requests/min · 10,000/day | 1,000 requests/min · unlimited daily | custom |
| **bulk** (`POST /datasets/{id}/records`) | **20 requests/min** | not published — read `X-RateLimit-Limit` | custom |
| **sync** (`POST /datasets/{id}/sync`) | not published — read `X-RateLimit-Limit` | not published — read `X-RateLimit-Limit` | custom |

> Some category/tier combinations don't have published numbers. Make one request in that category and read `X-RateLimit-Limit` from the response — that is your authoritative limit for the current window. Enterprise customers can request custom limits via support.

### 2.3 Response headers

| Header | Meaning |
|--------|---------|
| `X-RateLimit-Limit` | Max requests allowed in the current window (per category) |
| `X-RateLimit-Remaining` | Requests left in the current window |
| `X-RateLimit-Reset` | Unix timestamp when the window resets |

### 2.4 Handling `429` correctly

1. **Honor `X-RateLimit-Reset`** — sleep until that Unix timestamp, don't hammer.
2. **Back off with exponential backoff + jitter** for repeated `429`s.
3. **Watch `X-RateLimit-Remaining`** proactively and throttle before you hit zero — don't treat the limit as something to "discover".
4. **Don't retry immediately** — an instant retry consumes another request and extends your lockout.
5. **Separate your workers by category**: a bulk-insert worker and a read worker have independent budgets; don't serialize them behind one global semaphore based on the `standard` limit.
6. `POST /auth/login` itself returns `429` on too many attempts — implement token refresh rather than re-logging in every request.

---

## 3. Common migration pitfalls (v2.0 → v2.2)

Drawn from recurring support patterns:

| # | Pitfall | What happens | Fix |
|---|---------|--------------|-----|
| 1 | Keeping API-key auth past v2.1 | `401` after upgrade | Migrate to JWT ([Migration Guide §2](MIGRATION_GUIDE_v2.0_to_v2.2.md#2-authentication-migration-api-keys--jwt)) |
| 2 | No token-refresh logic | Works for 1 hour, then everything 401s | Refresh on 401 and retry once; access tokens live 1h, refresh tokens 7d |
| 3 | Subscribing to `dataset.deleted` | Webhook never fires | Event doesn't exist; archive fires `dataset.updated` with `archived: true` |
| 4 | Assuming `DELETE /datasets/{id}` destroys data | Data "still there" | It's a soft delete — the dataset is archived, not removed |
| 5 | Assuming `DELETE .../records/{record_id}` is soft | Data unexpectedly gone | Record deletes **are** hard deletes |
| 6 | `limit` > 100 on list endpoints | Fewer results than expected, or validation error | Max page size is 100; paginate with `page` |
| 7 | Sending `filter` unencoded | `400` or ignored filter | `filter` must be **JSON-encoded** |
| 8 | Missing `Content-Type: application/json` | `400` on POST/PUT/PATCH | Set the header on all body-carrying requests |
| 9 | Dataset names outside 3–100 chars, or duplicates | `400` / `409` | Validate name length client-side; handle `409` (conflict) explicitly |
| 10 | > 1000 records per bulk request | `400` | Chunk inserts to ≤ 1000 records |
| 11 | Single-record insert loops | Slow, burns through the `bulk` budget | Batch arrays (now 3x faster in v2.2) |
| 12 | Treating all endpoints as one rate budget | Unexpected `429` on bulk/sync endpoints | Categories are separate — see §2 |
| 13 | Polling `GET /jobs/{job_id}` in a tight loop | Wasted quota; possible rate limit | Poll with backoff, or use `sync.completed`/`sync.failed` webhooks |
| 14 | Syncing with `mode: full` on a schedule | Massive redundant transfers | Use `mode: incremental` (v2.1+); reserve `full` for first run/repairs |
| 15 | Parsing the response as a bare payload | Code breaks on error responses | All responses use the `{success, data, error, meta}` envelope |
| 16 | Ignoring `error.details` on `400` | Hard-to-debug validation failures | Field-specific messages are in the error details |
| 17 | Reporting errors without `request_id` | Slow support turnaround | Always send `meta.request_id` (required for `500`s) |
| 18 | Testing against production | Polluted data, quota burn | Sandbox resets daily and is built for experimentation |

---

## 4. Authentication error diagnostics

### 4.1 Quick diagnostic flow

```
401 received
 ├─ Is an Authorization header present at all?
 │   └─ No → add it: Authorization: Bearer <access_token>
 ├─ Is the header well-formed ("Bearer <token>", no stray quotes/spaces)?
 │   └─ No → fix the header format
 ├─ Is the token a JWT access token (not an API key)?
 │   └─ No → obtain one via POST /auth/login
 ├─ Was the access token issued more than 1 hour ago?
 │   └─ Yes → POST /auth/refresh with the refresh_token, then retry
 ├─ Was the refresh token issued more than 7 days ago?
 │   └─ Yes → refresh token expired: POST /auth/login again
 ├─ Did you call /auth/logout without a refresh_token body?
 │   └─ Yes → that invalidated ALL tokens: log in again
 └─ Still failing with a token you believe is valid?
     └─ Call GET /me with the same token (see 4.2), then contact support
        with meta.request_id
```

### 4.2 Symptom → cause → fix

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `401` on every request, no header sent | Missing `Authorization` header | Add `Authorization: Bearer <access_token>` |
| `401` with `Bearer <api-key>` | API key sent where a JWT is expected | Login via `POST /auth/login`, use the returned access token |
| `401` after ~1 hour of uptime | Access token expired | `POST /auth/refresh`; auto-refresh on 401 + single retry |
| `401` from `POST /auth/refresh` ("expired token") | Refresh token older than 7 days (or invalid/revoked) | Re-authenticate with `POST /auth/login` |
| `401` after a logout call | `POST /auth/logout` without a `refresh_token` body invalidates **all** tokens | Log in again; always pass `refresh_token` if you only want that session invalidated |
| `401` on `POST /auth/login` itself | Invalid credentials — or too many attempts (`429` also possible) | Check credentials; back off before retrying |
| `403` (not `401`) | Authenticated, but not allowed to do that | Request access/permissions — the token itself is fine |
| `401` although `GET /me` succeeds with the same token | Possible platform-side issue | Capture `meta.request_id` and contact support |

### 4.3 `401` vs `403` — which is which

- **`401 Unauthorized`** — *not authenticated*: the request didn't carry a valid token. Fix credentials/tokens.
- **`403 Forbidden`** — *authenticated but not authorized*: the token is valid; the account lacks permission for that resource or action. Fix permissions, not auth.

> Note: a permissions problem should surface as `403`. If you receive `401` while the same token successfully calls `GET /me`, treat it as suspect — verify your token with `GET /me` first, then report the behavior to support with the `meta.request_id` so we can investigate.

### 4.4 What to include when contacting support

- `meta.request_id` from the failing response (always; required for `500`s)
- Timestamp of the request and the endpoint + method
- Your tier (Free / Pro / Enterprise)
- Which auth method the failing client uses (JWT vs legacy API key) and whether the token was freshly issued or long-lived
- For webhook issues: your webhook configuration (`url` + subscribed `events`) and one raw delivery (headers + body) if you received any

---

## 5. Error-code quick reference

| Code | Meaning | Typical trigger | What to do |
|------|---------|-----------------|------------|
| `400` | Validation failed | Bad body/params; > 1000 records; name length; unencoded filter | Read field-specific messages in the error details |
| `401` | Not authenticated | Missing/expired/invalid token | See §4 |
| `403` | Forbidden | Valid token, insufficient permission | Request access; don't retry blindly |
| `404` | Not found | Wrong ID — or resource owned by another user | Verify IDs and ownership |
| `409` | Conflict | Duplicate dataset name | Rename or adopt the existing resource |
| `422` | Business rule violation | Semantically valid but not allowed | Read the error message for the rule |
| `429` | Rate limited | Category/tier budget exhausted | Honor `X-RateLimit-Reset`; see §2 |
| `500` | Server error | Platform fault | Retry with backoff; report `meta.request_id` |

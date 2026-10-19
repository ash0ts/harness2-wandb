# DataSync API — Migration Guide: v2.0 → v2.2

This guide is for teams whose integrations were built against **DataSync API v2.0** and are moving to **v2.2**. The path crosses two releases — v2.1.0 (2024-11-01) and v2.2.0 (2024-12-15) — and the most impactful change on that path is the switch from API key authentication to JWT.

**Companion documents:**

- `API_REFERENCE.md` — full endpoint documentation for v2.2
- `TROUBLESHOOTING_GUIDE.md` — diagnostics for auth, rate limit, and webhook issues
- `RELEASE_NOTES_v2.2.md` — what shipped in v2.2.0

**Environments**

| Environment | Base URL | Notes |
|---|---|---|
| Production | `https://api.datasync.io/v2` | |
| Sandbox | `https://sandbox.api.datasync.io/v2` | Resets daily; safe for migration testing |

---

## 1. What changed between v2.0 and v2.2

| Release | Date | Change | Impact on v2.0 integrations |
|---|---|---|---|
| v2.1.0 | 2024-11-01 | **JWT authentication** replaces API keys (keys deprecated; removal planned for v3) | **Action required** — plan and execute an auth migration |
| v2.1.0 | 2024-11-01 | **Incremental sync mode** added to `POST /datasets/{id}/sync` | Optional — significant efficiency gain, easy to miss |
| v2.1.0 | 2024-11-01 | Rate limit headers are now returned on `429` responses | Enables proper backoff handling |
| v2.2.0 | 2024-12-15 | **Webhooks** (event subscriptions with optional HMAC signatures) | Optional — replaces polling patterns |
| v2.2.0 | 2024-12-15 | Fixed: sync jobs occasionally stuck in `running` status | Reliability improvement |
| v2.2.0 | 2024-12-15 | Bulk record inserts are ~3x faster | Performance improvement |

**No new breaking changes were introduced in v2.2.0 itself.** The one breaking change on the v2.0 → v2.2 path is the authentication switch in v2.1. The response envelope, error codes, and all v2.0 data endpoints behave the same in v2.2.

---

## 2. Upgrade path overview

Work through these steps in order. Each maps to a section below.

1. **Test in the sandbox first.** The sandbox resets daily, so you can iterate freely against fake data.
2. **Plan the authentication migration** (Section 3). This is the only mandatory work: legacy API keys are deprecated and will be removed in v3. Both auth methods work side by side during the deprecation window, so you can migrate in phases.
3. **Adopt incremental sync** where you trigger syncs (Section 4). This capability shipped in v2.1 and is opt-in per request — many v2.0 integrators never noticed it.
4. **Evaluate webhooks** for event-driven patterns (Section 5) — new in v2.2.
5. **Revisit bulk insert batching** (Section 6) — inserts are faster in v2.2, and bulk requests draw from their own rate limit category.
6. **Verify rate limit handling** — make sure your client reads `X-RateLimit-*` headers and backs off correctly on `429` (see `TROUBLESHOOTING_GUIDE.md`).

### Pre-migration checklist

- [ ] Integration tested against `https://sandbox.api.datasync.io/v2`
- [ ] Inventory of every service/cron job/script that calls the API with a legacy API key
- [ ] JWT login + refresh flow implemented and tested (Section 3.5–3.6)
- [ ] Decision recorded for each service: migrate now or during the deprecation window (Section 3.4)
- [ ] Sync calls reviewed for `mode` usage (Section 4)
- [ ] Bulk insert chunking reviewed against the 1,000-record-per-request limit (Section 6)
- [ ] `429` handler uses `X-RateLimit-Reset` for backoff
- [ ] Webhook signature verification implemented if you subscribe to events (Section 5)

---

## 3. Authentication transition: API keys → JWT

### 3.1 What changed

- **v2.0** authenticated requests with a long-lived API key sent with every request.
- **v2.1** switched to **JWT bearer tokens** obtained from `POST /auth/login`. API keys were deprecated at the same time.
- **Deprecation window:** both methods are accepted during the transition period. API keys will be **removed in v3**.
- The legacy key management endpoints — `GET /me/api-keys` and `POST /me/api-keys` — still operate, but they are **deprecated** and exist only to support the transition.

### 3.2 What still works during the deprecation period

A frequent source of confusion: *"the key endpoints still return 200 — are my keys still valid?"*

**Yes.** During the deprecation window:

- Existing API keys continue to authenticate requests.
- `GET /me/api-keys` still lists your keys, and `POST /me/api-keys` still creates new ones (both marked deprecated in the API reference).
- Do **not** create new API keys for new services or new deployments. New development should use JWT exclusively.

The continued operation of these endpoints is deliberate: it is what makes a phased migration possible. It is **not** a signal that API keys are a supported long-term option.

### 3.3 JWT quick start

```
POST /auth/login
{ "email": "you@example.com", "password": "..." }
```

Response:

```json
{
  "success": true,
  "data": {
    "access_token": "eyJhbGciOi...",
    "refresh_token": "d9f0c1..."
  },
  "meta": { "request_id": "b2c1...", "timestamp": "2024-12-15T10:00:00Z" }
}
```

Token facts you must design around:

| Token | Lifetime | Used for |
|---|---|---|
| `access_token` | ~1 hour | `Authorization: Bearer <access_token>` header on every API call |
| `refresh_token` | 7 days | `POST /auth/refresh` to obtain a new access token (and new tokens) |

Notes:

- `POST /auth/refresh` takes the `refresh_token` in the request body — no credentials required.
- `POST /auth/logout` invalidates tokens. If you omit the `refresh_token` in the body, **all** tokens for the user are invalidated.
- `429` can be returned by `POST /auth/login` after too many attempts — back off and retry later.

### 3.4 Decision criteria for the deprecation period

Because both methods work during the window, the right question is not *“can we defer?”* but *“which services should move first?”* Use these criteria:

| Factor | Migrate now | Defer (staged) |
|---|---|---|
| Number of services using the key | A handful | Dozens of microservices — migrate in phases |
| Refresh handling in your stack | Token storage/refresh already implemented | Needs infra work — start now, finish before v3 |
| Security posture requirements | High — short-lived JWTs reduce the blast radius of leaked credentials | Low-sensitivity internal tooling can move last |
| New services or rewrite in progress | Always JWT — never build on keys | — |
| Creating new API keys | Stop immediately (`POST /me/api-keys` is deprecated) | — |
| Deadline | — | **All services must be off API keys before v3**, when key support is removed |

**Recommended staged approach for large fleets** (for example, an enterprise with dozens of microservices sharing one key):

1. Keep the existing API key running — nothing breaks during the window.
2. Implement a shared token service (or use your platform's secret manager) that performs login + refresh once and hands short-lived access tokens to services. This avoids every service implementing its own refresh logic.
3. Migrate services in waves: start with low-risk read-only consumers, finish with write-heavy services.
4. After each wave, confirm no 401 spikes, then stop using the key in that wave's services.
5. When the last wave is done, remove the key from all configuration — do not wait for the v3 removal date.

### 3.5 Before/after: authenticating a request

**Before — v2.0 pattern (API key sent with every request; header name as configured in your v2.0 integration):**

```bash
curl -H "X-API-Key: ak_live_xxxx" \
     https://api.datasync.io/v2/datasets
```

**After — v2.2 pattern (JWT bearer token):**

```bash
# 1. Exchange credentials for tokens (once per hour)
curl -X POST -H "Content-Type: application/json" \
     -d '{"email":"you@example.com","password":"..."}' \
     https://api.datasync.io/v2/auth/login

# 2. Call the API with the access token
curl -H "Authorization: Bearer eyJhbGciOi..." \
     https://api.datasync.io/v2/datasets
```

### 3.6 Before/after: token lifecycle handling

Long-running processes must refresh proactively or recover from expiry. A minimal Python pattern:

**Before (v2.0):** static credential, no lifecycle code.

```python
API_KEY = "ak_live_xxxx"  # set once, never changes

def call(path, **kw):
    return requests.get(f"{BASE}/datasets", headers={"X-API-Key": API_KEY})
```

**After (v2.2):** login, proactive refresh, and 401 retry.

```python
import time, requests

class Auth:
    def __init__(self, email, password):
        self.email, self.password = email, password
        self.access = self.refresh_tok = None
        self.expires_at = 0

    def token(self):
        if time.time() > self.expires_at - 60:   # refresh 60s before expiry
            self._login_or_refresh()
        return self.access

    def _login_or_refresh(self):
        if self.refresh_tok:
            r = requests.post(f"{BASE}/auth/refresh",
                              json={"refresh_token": self.refresh_tok})
        else:
            r = requests.post(f"{BASE}/auth/login",
                              json={"email": self.email, "password": self.password})
        r.raise_for_status()
        d = r.json()["data"]
        self.access, self.refresh_tok = d["access_token"], d["refresh_token"]
        self.expires_at = time.time() + 55 * 60  # access tokens last ~1 hour

AUTH = Auth("you@example.com", "...")

def call(method, path, **kw):
    headers = kw.pop("headers", {})
    headers["Authorization"] = f"Bearer {AUTH.token()}"
    r = requests.request(method, f"{BASE}{path}", headers=headers, **kw)
    if r.status_code == 401:                 # token expired mid-flight: refresh once, retry
        AUTH._login_or_refresh()
        headers["Authorization"] = f"Bearer {AUTH.token()}"
        r = requests.request(method, f"{BASE}{path}", headers=headers, **kw)
    return r
```

Handle these refresh-path responses explicitly:

| Response from `POST /auth/refresh` | Meaning | Action |
|---|---|---|
| `200` | New tokens issued | Swap tokens in storage |
| `400` | Refresh token invalid | Re-login with credentials |
| `401` | Refresh token expired (older than 7 days) | Re-login with credentials |

---

## 4. Incremental sync (added in v2.1 — commonly missed)

`POST /datasets/{id}/sync` existed in v2.0, so many integrators never re-read its docs. What changed in v2.1: the endpoint gained an **`incremental` mode** that transfers only what changed since the last successful sync, instead of re-syncing the entire dataset.

**Key facts:**

- `mode` accepts `full` or `incremental`; **the default is `full`** — you must opt in explicitly on every request.
- The call is asynchronous: a `202` response means the job was accepted. The response body identifies the job; poll `GET /jobs/{job_id}` for `{ "status": "pending" | "running" | "completed" | "failed", "progress": 0-100, "error": string | null }`.
- Sync requests draw from the dedicated **sync rate limit category**, separate from standard request limits (see `TROUBLESHOOTING_GUIDE.md`).
- v2.2 also fixed a defect where sync jobs could occasionally remain stuck in `running`.

**Before — v2.0 pattern (full sync every run):**

```python
r = requests.post(f"{BASE}/datasets/{ds_id}/sync",
                  headers=auth_headers,
                  json={"source_id": "src_123", "mode": "full"})
job_id = r.json()["data"]["job_id"]
```

**After — v2.2 pattern (incremental, with job tracking):**

```python
r = requests.post(f"{BASE}/datasets/{ds_id}/sync",
                  headers=auth_headers,
                  json={"source_id": "src_123", "mode": "incremental"})
job_id = r.json()["data"]["job_id"]

# Poll until the job settles
while True:
    job = requests.get(f"{BASE}/jobs/{job_id}", headers=auth_headers).json()["data"]
    if job["status"] in ("completed", "failed"):
        break
    time.sleep(5)
```

With v2.2 you can replace the polling loop entirely by subscribing to the `sync.completed` and `sync.failed` webhook events (Section 5) — poll only as a fallback.

**When to use `incremental`:** any recurring or scheduled sync against a source you already synced before. Use `full` for initial loads, source reconfigurations, or after suspected drift.

---

## 5. Webhooks (new in v2.2)

Webhooks let the API notify you when things happen, instead of you polling.

### 5.1 The event list — and the one event that does *not* exist

| Event | Fired when |
|---|---|
| `dataset.created` | A dataset is created |
| `dataset.updated` | A dataset is updated **or archived** (see below) |
| `sync.completed` | A sync job finishes successfully |
| `sync.failed` | A sync job fails |

> **Important — there is no `dataset.deleted` event.** `DELETE /datasets/{id}` is a **soft delete**: it sets the dataset's archived flag and emits a **`dataset.updated`** event. If your webhook filter includes `dataset.deleted`, it will simply never fire. To detect archival, subscribe to `dataset.updated` and check the dataset's archived/status state in the payload. (Deleting an individual *record* via `DELETE /datasets/{id}/records/{record_id}` is a hard delete and is not accompanied by a dataset-level event.)

### 5.2 Getting started

```
POST /webhooks
{ "url": "https://hooks.example.com/datasync",
  "events": ["dataset.created", "dataset.updated", "sync.completed", "sync.failed"],
  "secret": "whsec_..." }
```

- `secret` is optional but **strongly recommended**: when set, every delivery includes an `X-Webhook-Signature` header containing the HMAC-SHA256 signature of the raw request body, keyed with your secret. Verification steps and reference code are in `API_REFERENCE.md` (Webhooks → Signature verification).
- Payload shape for every event:

```json
{
  "event": "dataset.updated",
  "timestamp": "2024-12-15T10:00:00Z",
  "data": { "...": "event-specific fields" }
}
```

### 5.3 Before/after: change detection

**Before (v2.0):** poll the API on a timer to notice changes.

```python
while True:
    datasets = requests.get(f"{BASE}/datasets", headers=auth_headers).json()["data"]
    diff_against_last_snapshot(datasets)
    time.sleep(300)
```

**After (v2.2):** react to events; poll only as a fallback.

```python
@app.post("/datasync")
def handle(request):
    if not signature_valid(request):          # HMAC-SHA256, see API_REFERENCE.md
        return "", 400
    body = request.get_json()
    if body["event"] == "dataset.updated" and body["data"].get("archived"):
        handle_archival(body["data"])          # replaces a "dataset.deleted" handler
    elif body["event"] == "sync.completed":
        refresh_local_copy(body["data"])
    return "", 200
```

---

## 6. Bulk record inserts

`POST /datasets/{id}/records` accepts either a single record object or an array of records (**max 1,000 records per request**). In v2.2 bulk inserts are approximately **3x faster**.

One behavior change to be aware of: bulk record inserts draw from a dedicated **bulk rate limit category** with its own — lower — limits, separate from the standard request budget. On the Free tier that is **20 bulk requests per minute**. If your sync jobs post many small batches, they will hit `429` long before the standard limit is reached.

**Before (v2.0):** many small requests.

```python
for record in records:                       # N requests, N chances to be rate limited
    requests.post(f"{BASE}/datasets/{ds_id}/records",
                  headers=auth_headers, json=record)
```

**After (v2.2):** batch up to 1,000 records per request.

```python
for chunk in chunks(records, 1000):          # ceil(N/1000) requests
    requests.post(f"{BASE}/datasets/{ds_id}/records",
                  headers=auth_headers, json=chunk)
```

Batching reduces request count (helping you stay inside the bulk rate category) *and* benefits from the v2.2 performance improvement. See `TROUBLESHOOTING_GUIDE.md` for the full rate limit matrix and retry strategy.

---

## 7. Escalated support scenarios, resolved

The three scenarios below came through Customer Success as documentation escalations. They are reproduced here (anonymized) because each one represents a migration pattern other integrators are likely to hit.

### Scenario 1 — 401 errors after upgrading, with API keys still listed in the dashboard

**Symptom.** After upgrading, requests that worked in v2.0 return `401`. The legacy key management endpoints still respond successfully, which makes it look like nothing changed.

**Root cause.** v2.1 switched the platform to JWT authentication. API keys are deprecated but still accepted during the transition window — so a `401` at this stage almost always means the *request* is malformed for the new auth flow (missing/misnamed header, cached expired credentials), not that the key was revoked.

**Concrete solution.**

1. Confirm the key still works by listing it via `GET /me/api-keys` (deprecated, but functional).
2. Implement the JWT flow (Section 3.3 / 3.6). Exchange credentials once, store both tokens, refresh proactively before the ~1 hour access-token expiry.
3. For large fleets, use the staged rollout in Section 3.4 — parallel auth support means API-key services keep working while JWT services come online one by one.
4. Stop creating new API keys today; complete the switchover before v3 removes key support.

**Before/after code:** Section 3.5 and 3.6.

### Scenario 2 — Webhook subscribed to `dataset.deleted` never fires

**Symptom.** A webhook registered for `dataset.created`, `dataset.updated`, `dataset.deleted`, `sync.completed`, and `sync.failed` receives callbacks for everything except deletions. Datasets disappear from the active list, but no deletion event arrives.

**Root cause.** `dataset.deleted` is **not an implemented event**. `DELETE /datasets/{id}` performs a **soft delete** (archive): the dataset's archived flag is set and a **`dataset.updated`** event is emitted. The callback *is* being sent — just under a different event name.

**Concrete solution.**

1. Remove `dataset.deleted` from your event subscription; it is not a valid event (valid events: `dataset.created`, `dataset.updated`, `sync.completed`, `sync.failed`).
2. Subscribe to `dataset.updated` and inspect the dataset's archived flag in the payload to distinguish archival from ordinary updates (Section 5.3).
3. Remember the asymmetry: datasets archive (soft), individual records delete permanently (hard) — and record deletion emits no dataset-level event.

### Scenario 3 — Rate limited after ~20 bulk inserts despite a 100 req/min plan limit

**Symptom.** On the Free tier, standard `GET` traffic sails along at up to 100 requests/minute, but a bulk insert job starts receiving `429` after roughly 20 requests.

**Root cause.** Rate limits are enforced **per category**, not just per plan. Bulk record inserts belong to the `bulk` category, which on the Free tier allows **20 requests/minute** — independent of the 100 req/min standard budget.

**Concrete solution.**

1. Batch up to 1,000 records per request (Section 6) — fewer, larger requests fit the bulk budget.
2. Read `X-RateLimit-Remaining` and `X-RateLimit-Reset` (Unix timestamp) from responses and pace the job accordingly; on `429`, wait until `X-RateLimit-Reset` before retrying.
3. Design scheduled sync jobs around the per-category budgets in `TROUBLESHOOTING_GUIDE.md`.

---

## 8. Compatibility notes and deprecations

| Item | Status | Details |
|---|---|---|
| API key authentication | **Deprecated (v2.1)** | Still functional during the transition window; removal planned for v3. Migrate to JWT. |
| `GET /me/api-keys` | **Deprecated (v2.1)** | Legacy key listing. Do not build new integrations on it. |
| `POST /me/api-keys` | **Deprecated (v2.1)** | Legacy key creation. Do not create new keys. |
| Response envelope | Unchanged | `{ success, data, error, meta }` since v2.0 |
| Error semantics | Unchanged | `400` validation (with field-level `errors` array), `401`, `403`, `404`, `409`, `422`, `429`, `500` |
| v2.0 data endpoints | Unchanged | All 16 operations added in v2.0 behave the same in v2.2 |

New since v2.0: 4 auth/account operations (v2.1) and 4 webhook operations (v2.2) — 24 operations in total across the API.

---

## 9. Quick reference: v2.0 → v2.2 cheat sheet

| v2.0 pattern | v2.2 replacement |
|---|---|
| API key in every request header | `POST /auth/login` → `Authorization: Bearer <access_token>`, refresh before ~1 h expiry |
| Full sync on every run | `POST /datasets/{id}/sync` with `"mode": "incremental"` |
| Polling `GET /jobs/{job_id}` in a loop | Subscribe to `sync.completed` / `sync.failed` webhooks (poll as fallback) |
| Polling datasets for changes | Subscribe to `dataset.created` / `dataset.updated` |
| Expecting a `dataset.deleted` event | Handle `dataset.updated` + archived flag (soft delete) |
| One request per record insert | Batch arrays up to 1,000 records per `POST /datasets/{id}/records` |
| Ignoring `429` (retry immediately) | Back off using `X-RateLimit-Reset`; respect per-category budgets |

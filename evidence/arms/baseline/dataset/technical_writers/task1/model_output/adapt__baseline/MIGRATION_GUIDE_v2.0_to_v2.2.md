# DataSync API Migration Guide: v2.0 → v2.2

**Audience:** Developers with integrations built against DataSync API v2.0 who are upgrading to v2.2.
**Applies to:** `https://api.datasync.io/v2` (production) and `https://sandbox.api.datasync.io/v2` (sandbox).

> **Sandbox tip:** The sandbox resets daily and is pre-loaded with fake data — use it to validate your migration before touching production.

---

## 1. What changed between v2.0 and v2.2

| Version | Released | Summary of changes |
|---------|----------|--------------------|
| v2.0.0  | 2024-09-15 | Baseline: new endpoint structure, job queue for async operations |
| v2.1.0  | 2024-11-01 | **Breaking:** switched to JWT authentication (API keys deprecated, removal in v3); added **incremental sync mode**; fixed rate-limit headers missing on `429` responses; added `/auth/*`, `/me/usage` |
| v2.2.0  | 2024-12-15 | Added **webhooks**; fixed sync jobs occasionally stuck in `running`; bulk record inserts up to **3x faster** |

The three changes that require action or attention on your side are:

1. **Authentication** — API keys → JWT (breaking in v2.1, still in a deprecation window).
2. **Webhooks** (new in v2.2) — including important behavior around dataset deletion.
3. **Incremental sync** (added in v2.1) — a capability many v2.0-era integrations never adopted.

---

## 2. Authentication migration: API keys → JWT

### 2.1 Background

v2.1 replaced static API keys with JWT-based authentication. API keys were **deprecated in v2.1**, but **not removed** — they continue to work during the transition period and will be **removed in v3**. This means:

- Existing API keys (including keys still listed in your dashboard) **remain valid** during the deprecation window.
- `/me/api-keys` endpoints still function, but they are **deprecated** — do not build new automation on them.
- During the transition window, **both authentication methods are accepted in parallel**, so you can migrate service-by-service without a big-bang cutover.

### 2.2 How JWT authentication works

| Aspect | Detail |
|--------|--------|
| Token endpoint | `POST /auth/login` with `email` + `password` → returns `access_token` and `refresh_token` |
| Access token lifetime | **1 hour** |
| Refresh token lifetime | **7 days** |
| Request header | `Authorization: Bearer <access_token>` |
| Refresh flow | `POST /auth/refresh` with `refresh_token` in the body → returns new tokens |
| Logout | `POST /auth/logout` (optional `refresh_token` in body). **If `refresh_token` is omitted, all tokens are invalidated.** |

> The Python SDK handles token refresh automatically. If you are on raw HTTP, implement the refresh flow yourself (pattern below).

### 2.3 Decision criteria: migrate now or stay on API keys during the deprecation period?

Use the table below to decide your migration timing. Either way, **all API-key usage must end before v3** — the question is only *when* you move.

| Your situation | Recommendation |
|----------------|----------------|
| New integration starting today | **JWT only.** Never build on deprecated auth. |
| Single service, credentials in central config/secret store | **Migrate now.** Effort is small: add login + refresh logic, swap the header. |
| Services cannot yet handle a 1-hour token expiry (no refresh logic) | **Stay on API keys short-term**, add refresh handling on a staging branch, then cut over. |
| Large fleet (dozens of microservices) sharing one credential | **Phased migration** (see §2.5): introduce a central token broker, migrate services incrementally, keep API keys only for stragglers until v3. |
| Security/compliance requires short-lived credentials | **Migrate now.** 1-hour access tokens are strictly better than never-expiring keys. |
| Automation calls `GET/POST /me/api-keys` to manage keys | **Plan replacement now.** These endpoints are deprecated and will disappear in v3; key management will not be the auth path going forward. |

**Rule of thumb:** if your code treats its credential as *permanent*, you will break when v3 removes API keys. Migrating early means one deliberate change now instead of an emergency later.

### 2.4 Before/after: authentication code

**Before (v2.0 — static API key, sent on every request, never expires):**

```python
# v2.0 pattern (pseudocode). Your existing API key header format is unchanged
# during the transition window — this code keeps working until v3.
API_KEY = load_secret("DATASYNC_API_KEY")     # configured once, never rotated

def call_api(path):
    return http.get(f"{BASE_URL}{path}", headers=auth_header(API_KEY))

datasets = call_api("/datasets")              # works, but is on the v3 removal path
```

**After (v2.2 — JWT with automatic refresh):**

```python
# v2.2 pattern (pseudocode)
tokens = None

def login():
    global tokens
    r = http.post(f"{BASE_URL}/auth/login",
                  json={"email": EMAIL, "password": PASSWORD})
    tokens = r.json()["data"]                 # access_token (1h) + refresh_token (7d)

def get_access_token():
    global tokens
    if tokens is None:
        login()
    return tokens["access_token"]

def refresh():
    global tokens
    r = http.post(f"{BASE_URL}/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    if r.status_code == 200:
        tokens = r.json()["data"]
    else:
        login()                               # refresh expired (7d) → full re-login

def call_api(method, path, **kw):
    headers = {"Authorization": f"Bearer {get_access_token()}", **kw.pop("headers", {})}
    resp = http.request(method, f"{BASE_URL}{path}", headers=headers, **kw)
    if resp.status_code == 401:               # access token expired mid-flight
        refresh()
        return call_api(method, path, **kw)   # retry once with fresh token
    return resp
```

**Checklist for the swap:**

- [ ] Call `POST /auth/login` once at startup (and after 7 days when the refresh token expires).
- [ ] Send `Authorization: Bearer <access_token>` on every request.
- [ ] On `401`, refresh the access token and retry the request once.
- [ ] Never hardcode passwords in code — store them in your secret manager, same as the old API key.
- [ ] If a service only ever runs unattended, make sure refresh happens *before* the access token's 1-hour expiry, not only reactively.

### 2.5 Escalated scenario — "JWT migration breaking our integration" (47 microservices)

**Reported symptom (real-world escalation):** after upgrading, a customer with 47 microservices on API keys received `401` errors; dashboard keys still looked valid; docs did not explain the transition.

**What actually happened:** API keys were deprecated in v2.1 but are still valid during the transition period. The `401`s indicate those services were sending credentials in a way v2.1+ no longer accepts, or tokens had expired without a refresh path. The fix is a **phased migration**, not an emergency rewrite:

**Recommended phased approach for large fleets:**

1. **Inventory** — list every service that sends DataSync requests and which credential it uses.
2. **Create one service account** — a single email/password whose tokens all services share (or one account per environment: dev/staging/prod).
3. **Introduce a token broker** — a small internal service (or sidecar/library) that performs `POST /auth/login` + `POST /auth/refresh` and hands out fresh access tokens to your services. Services then fetch a token from the broker instead of holding long-lived secrets.
4. **Migrate in waves** — start with low-risk read-only services (e.g., ones that only call `GET /datasets`). Keep API keys for the rest — parallel auth is supported during the deprecation window.
5. **Monitor** — watch for `401` responses per service; a spike after a wave means that wave's refresh logic is wrong.
6. **Retire keys** — once all services are on JWT, delete remaining API keys via the dashboard and remove `/me/api-keys` calls from any automation. Do this *before* v3 so removal is a non-event.

**Answer to "does `/me/api-keys` still working mean keys are still valid?"** — Yes, during the transition period. Deprecation ≠ removal. But it is a signal to *start* migrating: those endpoints and key-based auth are scheduled for removal in v3.

---

## 3. Webhooks (new in v2.2)

### 3.1 The complete event list — read this before subscribing

There are **exactly four** webhook event types:

| Event | Fired when |
|-------|------------|
| `dataset.created` | A dataset is created |
| `dataset.updated` | A dataset is updated **or archived** (see 3.2) |
| `sync.completed` | A sync job finishes successfully |
| `sync.failed` | A sync job fails |

> ⚠️ **`dataset.deleted` does not exist.** Do not subscribe to it — you will never receive it.

### 3.2 Dataset "deletion" is a soft delete

`DELETE /datasets/{id}` performs a **soft delete**: it sets the dataset's `archived` flag and returns `204`. Because the dataset still exists (archived, not destroyed), this operation fires **`dataset.updated`**, not a deletion event.

**Escalated scenario — "webhook not firing for deleted datasets":** a partner subscribed to `dataset.deleted` (assuming it existed by naming convention) and never received callbacks when datasets were archived. This is expected behavior, not a bug.

**Before (broken subscription):**

```json
POST /webhooks
{
  "url": "https://hooks.example.com/datasync",
  "events": ["dataset.created", "dataset.updated", "dataset.deleted", "sync.completed", "sync.failed"]
}
```

Archiving a dataset produces **no `dataset.deleted` callback** — the subscription silently never matches.

**After (correct subscription):**

```json
POST /webhooks
{
  "url": "https://hooks.example.com/datasync",
  "events": ["dataset.created", "dataset.updated", "sync.completed", "sync.failed"]
}
```

Then detect archival from the `dataset.updated` payload:

```python
def handle_webhook(payload):
    if payload["event"] == "dataset.updated":
        dataset = payload["data"]
        if dataset.get("archived"):
            handle_archive(dataset["id"])      # your "deletion" logic
        else:
            handle_update(dataset)
```

### 3.3 Payload and signature

Every delivery is a JSON body:

```json
{
  "event": "sync.completed",
  "timestamp": "2024-12-15T10:30:00Z",
  "data": { "job_id": "..." }
}
```

If you supply a `secret` when creating the webhook, each delivery includes an `X-Webhook-Signature` header: the **HMAC-SHA256 of the raw request body**, keyed with your secret. **Always verify it** before trusting a delivery — see the [API Reference → Webhooks](API_REFERENCE.md#webhooks) for verification code.

> Register `dataset.created`, `dataset.updated`, `sync.completed`, and `sync.failed` (with a secret) via `POST /webhooks`. Manage subscriptions with `GET /webhooks` and `DELETE /webhooks/{id}`.

---

## 4. Incremental sync (added in v2.1 — commonly missed)

Many v2.0-era integrations never noticed that `POST /datasets/{id}/sync` accepts a **`mode`** parameter:

| Mode | Behavior |
|------|----------|
| `full` (default) | Re-syncs the entire external source into the dataset |
| `incremental` | Transfers only changes since the last successful sync |

Sync is asynchronous: the call returns `202` with a `job_id`; track it via `GET /jobs/{job_id}` (`status`: `pending|running|completed|failed`, `progress`: 0–100, `error`). In v2.2, sync jobs that previously could get stuck in `running` now complete reliably — another reason to adopt the new mode now.

**Before (v2.0 habit — always full sync):**

```python
# Every run re-transfers the whole source — slow and wasteful for large datasets
job = post(f"{BASE_URL}/datasets/{ds_id}/sync",
           json={"source_id": "src_123"}).json()["data"]
poll_until_done(job["job_id"])
```

**After (v2.2 — scheduled incremental syncs, full sync only on first run or repair):**

```python
def start_sync(mode="incremental"):
    job = post(f"{BASE_URL}/datasets/{ds_id}/sync",
               json={"source_id": "src_123", "mode": mode}).json()["data"]
    return job["job_id"]

# First run / drift repair:
job_id = start_sync(mode="full")

# Scheduled runs (cron, etc.):
job_id = start_sync(mode="incremental")
```

Or skip polling entirely by subscribing to the `sync.completed` / `sync.failed` webhooks (§3).

---

## 5. Bulk inserts (3x faster in v2.2)

`POST /datasets/{id}/records` accepts a single object or an array (max **1000 records per request**) and is up to **3x faster** in v2.2. If you looped single-record inserts in v2.0, batch them:

**Before:**

```python
for record in records:
    post(f"{BASE_URL}/datasets/{ds_id}/records", json=record)
```

**After:**

```python
for chunk in chunks(records, 1000):                 # respect the 1000-record cap
    post(f"{BASE_URL}/datasets/{ds_id}/records", json=chunk)
```

> ⚠️ Bulk inserts run in the **separate `bulk` rate-limit category** with lower limits than regular requests (e.g., 20 bulk requests/min on the Free tier). Throttle accordingly — see the [Troubleshooting Guide → Rate limiting](TROUBLESHOOTING_GUIDE.md#2-rate-limiting-reference).

---

## 6. Other behavior to know when moving to v2.2

- **Response envelope:** every response is wrapped as `{ "success", "data", "error", "meta" }`. `meta.request_id` is what support needs when you report a `500`.
- **Validation errors (`400`)** include field-specific messages — surface them in your logs.
- **Pagination:** `limit` defaults to 20, **max 100**. Values above 100 will not get you more data.
- **Content-Type:** `application/json` is required for all `POST`/`PUT`/`PATCH`.
- **Error semantics:** `401` = not authenticated, `403` = authenticated but not allowed, `409` = conflict (e.g., duplicate dataset name), `429` = rate limited (now includes rate-limit headers, fixed in v2.1).
- **Record deletes are hard deletes** (`DELETE .../records/{record_id}`), while **dataset deletes are soft deletes** (archive). Don't conflate them.

---

## 7. Migration checklist

```
[ ] 1. Confirm current version and inventory all services + credentials
[ ] 2. Test against https://sandbox.api.datasync.io/v2 (resets daily)
[ ] 3. Authentication
      [ ] Create service account credentials
      [ ] Implement login + refresh + retry-on-401
      [ ] Decide API-key timeline using §2.3 criteria; plan removal before v3
[ ] 4. Webhooks (optional but recommended)
      [ ] Register webhook with the four valid events + secret
      [ ] Verify X-Webhook-Signature (HMAC-SHA256) on every delivery
      [ ] Remove any dataset.deleted subscriptions (does not exist)
      [ ] Treat dataset.updated with archived=true as a deletion
[ ] 5. Sync
      [ ] Switch scheduled syncs to mode: "incremental" (keep full for first run)
      [ ] Track jobs via GET /jobs/{job_id} or sync.* webhooks
[ ] 6. Bulk inserts
      [ ] Batch up to 1000 records per request
      [ ] Throttle for the bulk rate-limit category
[ ] 7. Error handling
      [ ] Parse the response envelope; log meta.request_id on failures
      [ ] Handle 401/403/409/429 explicitly
```

---

## 8. Getting help

- Reproduce issues against the **sandbox** first: `https://sandbox.api.datasync.io/v2`.
- For any `500`, include the `meta.request_id` from the response body when contacting support — it lets us find your exact request in our logs.
- See also: [API Reference](API_REFERENCE.md) and [Troubleshooting Guide](TROUBLESHOOTING_GUIDE.md).

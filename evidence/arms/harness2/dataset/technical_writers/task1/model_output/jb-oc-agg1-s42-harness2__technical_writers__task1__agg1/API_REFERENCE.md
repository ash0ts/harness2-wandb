# DataSync API Reference — v2.2

REST API documentation for DataSync API v2.2, organized by resource. Formatting follows the Swagger-inspired Markdown style (collapsible endpoint sections, parameter tables, response tables, cURL examples).

**Base URL:** `https://api.datasync.io/v2`
**Sandbox:** `https://sandbox.api.datasync.io/v2` (resets daily)

---

## Conventions

### Authentication

All endpoints require authentication unless marked *public*. Send a JWT access token in the `Authorization` header:

```
Authorization: Bearer <access_token>
```

Obtain tokens via `POST /auth/login`; refresh via `POST /auth/refresh`. Access tokens live ~1 hour, refresh tokens 7 days. Legacy API key authentication is **deprecated** (removal planned for v3) — see `MIGRATION_GUIDE_v2.0_to_v2.2.md`.

### Request format

`Content-Type: application/json` is required for all `POST` / `PUT` / `PATCH` requests.

### Response envelope

Every response is wrapped in a standard envelope:

```json
{
  "success": true,
  "data": {},
  "error": null,
  "meta": { "request_id": "uuid", "timestamp": "ISO8601" }
}
```

On failure, `success` is `false`, `data` is `null`, and `error` is an object: `{ "code": "ERR_CODE", "message": "Human readable", "details": {} }`. Validation failures (`400`) include a field-level `errors` array. Server errors (`500`) include a `request_id` in `meta` — include it when contacting support.

### Error codes

| HTTP code | Meaning |
|---|---|
| `400` | Bad request — validation failed; body contains field-specific messages |
| `401` | Not authenticated |
| `403` | Forbidden — authenticated but not authorized |
| `404` | Not found |
| `409` | Conflict (e.g., duplicate dataset name) |
| `422` | Unprocessable entity — business rule violation |
| `429` | Rate limited |
| `500` | Server error |

### Rate limits

Requests are counted in three categories: **standard**, **bulk**, and **sync**. Every response includes `X-RateLimit-Limit`, `X-RateLimit-Remaining`, and `X-RateLimit-Reset` (Unix timestamp) headers; on `429`, wait until the reset time before retrying. See `TROUBLESHOOTING_GUIDE.md` for the full per-tier matrix.

### Pagination

List endpoints accept `page` (default `1`) and `limit` (default `20`, maximum `100`).

---

## Endpoint inventory

| # | Method | Endpoint | Rate category | Added | Status |
|---|---|---|---|---|---|
| 1 | `POST` | `/auth/login` | standard | v2.1.0 | active (public) |
| 2 | `POST` | `/auth/refresh` | standard | v2.1.0 | active (public) |
| 3 | `POST` | `/auth/logout` | standard | v2.1.0 | active |
| 4 | `GET` | `/datasets` | standard | v2.0.0 | active |
| 5 | `POST` | `/datasets` | standard | v2.0.0 | active |
| 6 | `GET` | `/datasets/{id}` | standard | v2.0.0 | active |
| 7 | `PUT` | `/datasets/{id}` | standard | v2.0.0 | active |
| 8 | `DELETE` | `/datasets/{id}` | standard | v2.0.0 | active (soft delete) |
| 9 | `GET` | `/datasets/{id}/records` | standard | v2.0.0 | active |
| 10 | `POST` | `/datasets/{id}/records` | **bulk** | v2.0.0 | active |
| 11 | `GET` | `/datasets/{id}/records/{record_id}` | standard | v2.0.0 | active |
| 12 | `PATCH` | `/datasets/{id}/records/{record_id}` | standard | v2.0.0 | active |
| 13 | `DELETE` | `/datasets/{id}/records/{record_id}` | standard | v2.0.0 | active (hard delete) |
| 14 | `POST` | `/datasets/{id}/sync` | **sync** | v2.0.0 | active |
| 15 | `GET` | `/jobs/{job_id}` | standard | v2.0.0 | active |
| 16 | `GET` | `/webhooks` | standard | v2.2.0 | active |
| 17 | `POST` | `/webhooks` | standard | v2.2.0 | active |
| 18 | `GET` | `/webhooks/{id}` | standard | v2.2.0 | active |
| 19 | `DELETE` | `/webhooks/{id}` | standard | v2.2.0 | active |
| 20 | `GET` | `/me` | standard | v2.0.0 | active |
| 21 | `PATCH` | `/me` | standard | v2.0.0 | active |
| 22 | `GET` | `/me/api-keys` | standard | v2.0.0 | **DEPRECATED (v2.1)** |
| 23 | `POST` | `/me/api-keys` | standard | v2.0.0 | **DEPRECATED (v2.1)** |
| 24 | `GET` | `/me/usage` | standard | v2.1.0 | active |

------------------------------------------------------------------------------------------

## Authentication

#### Authenticate and obtain tokens

<details>
 <summary><code>POST</code> <code><b>/auth/login</b></code> <code>(authenticate user and get tokens — public)</code></summary>

##### Parameters

> | name       |  type     | data type | description        |
> |------------|-----------|-----------|--------------------|
> | `email`    |  required | string    | Account email      |
> | `password` |  required | string    | Account password   |

*(Request body — JSON)*

##### Responses

> | http code | content-type     | response                                   |
> |-----------|------------------|--------------------------------------------|
> | `200`     | `application/json` | Tokens returned (`access_token`, `refresh_token`) |
> | `400`     | `application/json` | Invalid credentials                        |
> | `429`     | `application/json` | Too many attempts — back off and retry later |

##### Example cURL

> ```javascript
>  curl -X POST -H "Content-Type: application/json" \
>       -d '{"email":"you@example.com","password":"secret"}' \
>       https://api.datasync.io/v2/auth/login
> ```

</details>

------------------------------------------------------------------------------------------

#### Refresh an access token

<details>
 <summary><code>POST</code> <code><b>/auth/refresh</b></code> <code>(exchange a refresh token for new tokens — public)</code></summary>

##### Parameters

> | name            |  type     | data type | description            |
> |-----------------|-----------|-----------|------------------------|
> | `refresh_token` |  required | string    | Current refresh token  |

*(Request body — JSON)*

##### Responses

> | http code | content-type     | response              |
> |-----------|------------------|-----------------------|
> | `200`     | `application/json` | New tokens issued   |
> | `400`     | `application/json` | Invalid token       |
> | `401`     | `application/json` | Expired token — log in again |

##### Example cURL

> ```javascript
>  curl -X POST -H "Content-Type: application/json" \
>       -d '{"refresh_token":"d9f0c1..."}' \
>       https://api.datasync.io/v2/auth/refresh
> ```

</details>

------------------------------------------------------------------------------------------

#### Invalidate tokens

<details>
 <summary><code>POST</code> <code><b>/auth/logout</b></code> <code>(invalidate tokens — requires auth)</code></summary>

##### Parameters

> | name            |  type     | data type | description                                                     |
> |-----------------|-----------|-----------|-----------------------------------------------------------------|
> | `refresh_token` |  optional | string    | Specific token to invalidate; if omitted, **all** tokens for the user are invalidated |

*(Request body — JSON, optional)*

##### Responses

> | http code | content-type     | response   |
> |-----------|------------------|------------|
> | `204`     | None             | Success    |

##### Example cURL

> ```javascript
>  curl -X POST -H "Content-Type: application/json" \
>       -H "Authorization: Bearer <access_token>" \
>       -d '{"refresh_token":"d9f0c1..."}' \
>       https://api.datasync.io/v2/auth/logout
> ```

</details>

------------------------------------------------------------------------------------------

## Datasets

#### List datasets

<details>
 <summary><code>GET</code> <code><b>/datasets</b></code> <code>(list all datasets for the authenticated user)</code></summary>

##### Parameters

> | name     |  type     | data type | description                              |
> |----------|-----------|-----------|------------------------------------------|
> | `page`   |  optional | int       | Page number (default `1`)                |
> | `limit`  |  optional | int       | Items per page (default `20`, max `100`) |
> | `status` |  optional | string    | Filter: `active` \| `archived` \| `all` (default `all`) |

##### Responses

> | http code | content-type     | response      |
> |-----------|------------------|---------------|
> | `200`     | `application/json` | Dataset list |
> | `401`     | `application/json` | Unauthorized |

##### Example cURL

> ```javascript
>  curl -H "Authorization: Bearer <access_token>" \
>       "https://api.datasync.io/v2/datasets?page=1&limit=20&status=active"
> ```

</details>

------------------------------------------------------------------------------------------

#### Create a dataset

<details>
 <summary><code>POST</code> <code><b>/datasets</b></code> <code>(create a new dataset)</code></summary>

##### Parameters

> | name          |  type     | data type | description                          |
> |---------------|-----------|-----------|--------------------------------------|
> | `name`        |  required | string    | Dataset name — must be 3–100 characters and unique |
> | `description` |  optional | string    | Human-readable description           |
> | `tags`        |  optional | array     | List of tags                         |

*(Request body — JSON)*

##### Responses

> | http code | content-type     | response         |
> |-----------|------------------|------------------|
> | `201`     | `application/json` | Created        |
> | `400`     | `application/json` | Validation error |
> | `409`     | `application/json` | Duplicate name  |

##### Example cURL

> ```javascript
>  curl -X POST -H "Content-Type: application/json" \
>       -H "Authorization: Bearer <access_token>" \
>       -d '{"name":"Quarterly metrics","description":"Q4 data","tags":["finance"]}' \
>       https://api.datasync.io/v2/datasets
> ```

</details>

------------------------------------------------------------------------------------------

#### Get a dataset

<details>
 <summary><code>GET</code> <code><b>/datasets/{id}</b></code> <code>(get a single dataset by ID)</code></summary>

##### Parameters

> | name |  type     | data type | description     |
> |------|-----------|-----------|-----------------|
> | `id` |  required | string    | Dataset ID      |

Returns `404` if the dataset does not exist or is not owned by the authenticated user.

##### Responses

> | http code | content-type     | response    |
> |-----------|------------------|-------------|
> | `200`     | `application/json` | Dataset   |
> | `401`     | `application/json` | Unauthorized |
> | `404`     | `application/json` | Not found |

##### Example cURL

> ```javascript
>  curl -H "Authorization: Bearer <access_token>" \
>       https://api.datasync.io/v2/datasets/ds_123
> ```

</details>

------------------------------------------------------------------------------------------

#### Update a dataset

<details>
 <summary><code>PUT</code> <code><b>/datasets/{id}</b></code> <code>(update a dataset)</code></summary>

##### Parameters

> | name          |  type     | data type | description        |
> |---------------|-----------|-----------|--------------------|
> | `id`          |  required | string    | Dataset ID (path)  |
> | `name`        |  optional | string    | New name (3–100 chars, unique) |
> | `description` |  optional | string    | New description    |
> | `tags`        |  optional | array     | New tag list       |

*(Request body — JSON; same fields as create, all optional)*

##### Responses

> | http code | content-type     | response         |
> |-----------|------------------|------------------|
> | `200`     | `application/json` | Updated        |
> | `400`     | `application/json` | Validation error |
> | `404`     | `application/json` | Not found      |

##### Example cURL

> ```javascript
>  curl -X PUT -H "Content-Type: application/json" \
>       -H "Authorization: Bearer <access_token>" \
>       -d '{"description":"Updated description"}' \
>       https://api.datasync.io/v2/datasets/ds_123
> ```

</details>

------------------------------------------------------------------------------------------

#### Archive (soft-delete) a dataset

<details>
 <summary><code>DELETE</code> <code><b>/datasets/{id}</b></code> <code>(archive a dataset — soft delete)</code></summary>

##### Parameters

> | name |  type     | data type | description    |
> |------|-----------|-----------|----------------|
> | `id` |  required | string    | Dataset ID     |

> **This is a soft delete.** The dataset's archived flag is set; it is not permanently removed and remains retrievable via `GET /datasets?status=archived`. Archiving emits a **`dataset.updated`** webhook event — there is no `dataset.deleted` event.

##### Responses

> | http code | content-type     | response   |
> |-----------|------------------|------------|
> | `204`     | None             | Success    |
> | `404`     | `application/json` | Not found |

##### Example cURL

> ```javascript
>  curl -X DELETE -H "Authorization: Bearer <access_token>" \
>       https://api.datasync.io/v2/datasets/ds_123
> ```

</details>

------------------------------------------------------------------------------------------

#### List records in a dataset

<details>
 <summary><code>GET</code> <code><b>/datasets/{id}/records</b></code> <code>(list records in a dataset)</code></summary>

##### Parameters

> | name     |  type     | data type | description                                        |
> |----------|-----------|-----------|----------------------------------------------------|
> | `id`     |  required | string    | Dataset ID (path)                                  |
> | `page`   |  optional | int       | Page number (default `1`)                          |
> | `limit`  |  optional | int       | Items per page (default `20`, max `100`)           |
> | `sort`   |  optional | string    | Field name to sort by                              |
> | `order`  |  optional | string    | `asc` (default) \| `desc`                          |
> | `filter` |  optional | string    | JSON-encoded filter query                          |

##### Responses

> | http code | content-type     | response             |
> |-----------|------------------|----------------------|
> | `200`     | `application/json` | Record list        |
> | `404`     | `application/json` | Dataset not found  |

##### Example cURL

> ```javascript
>  curl -H "Authorization: Bearer <access_token>" \
>       "https://api.datasync.io/v2/datasets/ds_123/records?page=1&limit=20&sort=created_at&order=desc&filter=%7B%22status%22%3A%22active%22%7D"
> ```

</details>

------------------------------------------------------------------------------------------

#### Add records to a dataset (bulk-capable)

<details>
 <summary><code>POST</code> <code><b>/datasets/{id}/records</b></code> <code>(add records — single object or array, max 1,000 per request)</code></summary>

##### Parameters

> | name      |  type     | data type | description                                      |
> |-----------|-----------|-----------|--------------------------------------------------|
> | `id`      |  required | string    | Dataset ID (path)                                |
> | `records` |  required | array     | Records to add; a single record object is also accepted. **Max 1,000 records per request.** |

*(Request body — JSON)*

> **Rate limit category: `bulk`.** This endpoint is counted against the bulk rate limit category, which is separate from (and lower than) the standard budget — e.g., 20 requests/minute on the Free tier. Batch records up to the 1,000-per-request maximum.

##### Responses

> | http code | content-type     | response             |
> |-----------|------------------|----------------------|
> | `201`     | `application/json` | Created            |
> | `400`     | `application/json` | Validation error   |
> | `404`     | `application/json` | Dataset not found  |

##### Example cURL

> ```javascript
>  curl -X POST -H "Content-Type: application/json" \
>       -H "Authorization: Bearer <access_token>" \
>       -d '{"records":[{"field1":"a"},{"field1":"b"}]}' \
>       https://api.datasync.io/v2/datasets/ds_123/records
> ```

</details>

------------------------------------------------------------------------------------------

#### Get a single record

<details>
 <summary><code>GET</code> <code><b>/datasets/{id}/records/{record_id}</b></code> <code>(get a single record)</code></summary>

##### Parameters

> | name        |  type     | data type | description   |
> |-------------|-----------|-----------|---------------|
> | `id`        |  required | string    | Dataset ID    |
> | `record_id` |  required | string    | Record ID     |

##### Responses

> | http code | content-type     | response   |
> |-----------|------------------|------------|
> | `200`     | `application/json` | Record   |
> | `404`     | `application/json` | Not found |

##### Example cURL

> ```javascript
>  curl -H "Authorization: Bearer <access_token>" \
>       https://api.datasync.io/v2/datasets/ds_123/records/rec_456
> ```

</details>

------------------------------------------------------------------------------------------

#### Update a single record

<details>
 <summary><code>PATCH</code> <code><b>/datasets/{id}/records/{record_id}</b></code> <code>(partial update of a single record)</code></summary>

##### Parameters

> | name        |  type     | data type | description                     |
> |-------------|-----------|-----------|---------------------------------|
> | `id`        |  required | string    | Dataset ID (path)               |
> | `record_id` |  required | string    | Record ID (path)                |
> | *field:value pairs* |  required | object | Partial record object — only the fields to change |

*(Request body — JSON; partial update only)*

##### Responses

> | http code | content-type     | response         |
> |-----------|------------------|------------------|
> | `200`     | `application/json` | Updated        |
> | `400`     | `application/json` | Validation error |
> | `404`     | `application/json` | Not found      |

##### Example cURL

> ```javascript
>  curl -X PATCH -H "Content-Type: application/json" \
>       -H "Authorization: Bearer <access_token>" \
>       -d '{"status":"reviewed"}' \
>       https://api.datasync.io/v2/datasets/ds_123/records/rec_456
> ```

</details>

------------------------------------------------------------------------------------------

#### Delete a single record

<details>
 <summary><code>DELETE</code> <code><b>/datasets/{id}/records/{record_id}</b></code> <code>(delete a single record — hard delete)</code></summary>

##### Parameters

> | name        |  type     | data type | description   |
> |-------------|-----------|-----------|---------------|
> | `id`        |  required | string    | Dataset ID    |
> | `record_id` |  required | string    | Record ID     |

> Unlike dataset deletion (archive/soft delete), **record deletion is a hard delete** and cannot be undone.

##### Responses

> | http code | content-type     | response   |
> |-----------|------------------|------------|
> | `204`     | None             | Success    |
> | `404`     | `application/json` | Not found |

##### Example cURL

> ```javascript
>  curl -X DELETE -H "Authorization: Bearer <access_token>" \
>       https://api.datasync.io/v2/datasets/ds_123/records/rec_456
> ```

</details>

------------------------------------------------------------------------------------------

#### Trigger a sync

<details>
 <summary><code>POST</code> <code><b>/datasets/{id}/sync</b></code> <code>(trigger an async sync job with an external source)</code></summary>

##### Parameters

> | name        |  type     | data type | description                                                     |
> |-------------|-----------|-----------|-----------------------------------------------------------------|
> | `id`        |  required | string    | Dataset ID (path)                                               |
> | `source_id` |  required | string    | External source identifier                                      |
> | `mode`      |  optional | string    | `full` (default) \| `incremental` — incremental transfers only changes since the last successful sync |

*(Request body — JSON)*

> The call is asynchronous: a `202` response means the job was accepted; track it via `GET /jobs/{job_id}` or the `sync.completed` / `sync.failed` webhook events.
>
> **Rate limit category: `sync`.** Sync requests are counted in their own category, separate from the standard budget.

##### Responses

> | http code | content-type     | response           |
> |-----------|------------------|--------------------|
> | `202`     | `application/json` | Job started (returns job identifier) |
> | `400`     | `application/json` | Invalid source     |
> | `404`     | `application/json` | Dataset not found  |

##### Example cURL

> ```javascript
>  curl -X POST -H "Content-Type: application/json" \
>       -H "Authorization: Bearer <access_token>" \
>       -d '{"source_id":"src_123","mode":"incremental"}' \
>       https://api.datasync.io/v2/datasets/ds_123/sync
> ```

</details>

------------------------------------------------------------------------------------------

## Jobs

#### Check async job status

<details>
 <summary><code>GET</code> <code><b>/jobs/{job_id}</b></code> <code>(check the status of an async job, e.g. a sync)</code></summary>

##### Parameters

> | name     |  type     | data type | description |
> |----------|-----------|-----------|-------------|
> | `job_id` |  required | string    | Job identifier returned by the operation that started it |

##### Response payload

`data` contains: `status` (`pending` | `running` | `completed` | `failed`), `progress` (0–100), `error` (string or `null`).

##### Responses

> | http code | content-type     | response   |
> |-----------|------------------|------------|
> | `200`     | `application/json` | Job status |
> | `404`     | `application/json` | Job not found |

##### Example cURL

> ```javascript
>  curl -H "Authorization: Bearer <access_token>" \
>       https://api.datasync.io/v2/jobs/job_789
> ```

</details>

------------------------------------------------------------------------------------------

## Webhooks

Webhook subscriptions push notifications to your HTTPS endpoint when events occur. Supported events:

| Event | Fired when |
|---|---|
| `dataset.created` | A dataset is created |
| `dataset.updated` | A dataset is updated **or archived** (soft delete) |
| `sync.completed` | A sync job completes successfully |
| `sync.failed` | A sync job fails |

> **There is no `dataset.deleted` event.** `DELETE /datasets/{id}` performs a soft delete (archive) that emits `dataset.updated`.

Payload shape for every delivery:

```json
{
  "event": "dataset.updated",
  "timestamp": "ISO8601",
  "data": { "..." : "event-specific fields" }
}
```

#### List webhooks

<details>
 <summary><code>GET</code> <code><b>/webhooks</b></code> <code>(list configured webhooks)</code></summary>

##### Parameters

> None

##### Responses

> | http code | content-type     | response     |
> |-----------|------------------|--------------|
> | `200`     | `application/json` | Webhook list |
> | `401`     | `application/json` | Unauthorized |

##### Example cURL

> ```javascript
>  curl -H "Authorization: Bearer <access_token>" \
>       https://api.datasync.io/v2/webhooks
> ```

</details>

------------------------------------------------------------------------------------------

#### Create a webhook

<details>
 <summary><code>POST</code> <code><b>/webhooks</b></code> <code>(create a webhook subscription)</code></summary>

##### Parameters

> | name     |  type     | data type | description                                                            |
> |----------|-----------|-----------|------------------------------------------------------------------------|
> | `url`    |  required | string    | HTTPS endpoint that will receive deliveries                            |
> | `events` |  required | array     | Events to subscribe to: `dataset.created`, `dataset.updated`, `sync.completed`, `sync.failed` |
> | `secret` |  optional | string    | Signing secret — when set, deliveries include an `X-Webhook-Signature` header (HMAC-SHA256 of the body) |

*(Request body — JSON)*

##### Responses

> | http code | content-type     | response         |
> |-----------|------------------|------------------|
> | `201`     | `application/json` | Created        |
> | `400`     | `application/json` | Validation error |

##### Example cURL

> ```javascript
>  curl -X POST -H "Content-Type: application/json" \
>       -H "Authorization: Bearer <access_token>" \
>       -d '{"url":"https://hooks.example.com/datasync","events":["dataset.created","dataset.updated","sync.completed","sync.failed"],"secret":"whsec_..."}' \
>       https://api.datasync.io/v2/webhooks
> ```

</details>

------------------------------------------------------------------------------------------

#### Get webhook details

<details>
 <summary><code>GET</code> <code><b>/webhooks/{id}</b></code> <code>(get webhook details)</code></summary>

##### Parameters

> | name |  type     | data type | description |
> |------|-----------|-----------|-------------|
> | `id` |  required | string    | Webhook ID  |

##### Responses

> | http code | content-type     | response   |
> |-----------|------------------|------------|
> | `200`     | `application/json` | Webhook  |
> | `404`     | `application/json` | Not found |

##### Example cURL

> ```javascript
>  curl -H "Authorization: Bearer <access_token>" \
>       https://api.datasync.io/v2/webhooks/wh_001
> ```

</details>

------------------------------------------------------------------------------------------

#### Delete a webhook

<details>
 <summary><code>DELETE</code> <code><b>/webhooks/{id}</b></code> <code>(remove a webhook subscription)</code></summary>

##### Parameters

> | name |  type     | data type | description |
> |------|-----------|-----------|-------------|
> | `id` |  required | string    | Webhook ID  |

##### Responses

> | http code | content-type     | response   |
> |-----------|------------------|------------|
> | `204`     | None             | Success    |
> | `404`     | `application/json` | Not found |

##### Example cURL

> ```javascript
>  curl -X DELETE -H "Authorization: Bearer <access_token>" \
>       https://api.datasync.io/v2/webhooks/wh_001
> ```

</details>

------------------------------------------------------------------------------------------

### Webhook signature verification

When a webhook is created **with a `secret`**, every delivery includes an `X-Webhook-Signature` header containing the **HMAC-SHA256 signature of the raw request body**, keyed with that secret. Verify it before processing any delivery:

1. **Read the raw request body** — the exact bytes as received. Do *not* re-serialize a parsed JSON object; key ordering and whitespace differences will change the signature.
2. **Compute the expected signature** — HMAC-SHA256 over the raw body using your webhook secret as the key.
3. **Compare in constant time** — use a timing-safe comparison (e.g., `hmac.compare_digest` in Python). Never compare with `==`.
4. **Reject mismatches** — return an error status (e.g., `400`) and do not process the payload.

Reference implementation (Python):

```python
import hmac, hashlib

def verify_webhook(request_body: bytes, received_signature: str, secret: str) -> bool:
    expected = hmac.new(secret.encode(), request_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received_signature)

# In your handler:
# sig = request.headers.get("X-Webhook-Signature")
# if not verify_webhook(request.get_data(), sig, WEBHOOK_SECRET):
#     abort(400)
```

Common verification failures:

| Symptom | Cause |
|---|---|
| Every signature check fails | Verifying against the parsed/re-serialized JSON instead of the raw body |
| Signature valid locally, missing at the receiver | A proxy or framework consumed the body before your handler read the raw bytes |
| Intermittent failures after rotation | An old webhook (old secret) still active alongside the new one — delete the stale subscription |

------------------------------------------------------------------------------------------

## Account

#### Get current user profile

<details>
 <summary><code>GET</code> <code><b>/me</b></code> <code>(get the current user profile)</code></summary>

##### Parameters

> None

##### Responses

> | http code | content-type     | response    |
> |-----------|------------------|-------------|
> | `200`     | `application/json` | User profile |
> | `401`     | `application/json` | Unauthorized |

##### Example cURL

> ```javascript
>  curl -H "Authorization: Bearer <access_token>" \
>       https://api.datasync.io/v2/me
> ```

</details>

------------------------------------------------------------------------------------------

#### Update current user profile

<details>
 <summary><code>PATCH</code> <code><b>/me</b></code> <code>(update the current user profile)</code></summary>

##### Parameters

> | name       |  type     | data type | description   |
> |------------|-----------|-----------|---------------|
> | `name`     |  optional | string    | Display name  |
> | `email`    |  optional | string    | Email address |
> | `timezone` |  optional | string    | Timezone      |

*(Request body — JSON; partial update)*

##### Responses

> | http code | content-type     | response         |
> |-----------|------------------|------------------|
> | `200`     | `application/json` | Updated        |
> | `400`     | `application/json` | Validation error |

##### Example cURL

> ```javascript
>  curl -X PATCH -H "Content-Type: application/json" \
>       -H "Authorization: Bearer <access_token>" \
>       -d '{"timezone":"Europe/Berlin"}' \
>       https://api.datasync.io/v2/me
> ```

</details>

------------------------------------------------------------------------------------------

#### List API keys *(legacy)*

<details>
 <summary><code>GET</code> <code><b>/me/api-keys</b></code> <code>(<b>DEPRECATED</b> — list API keys)</code></summary>

> ![Deprecated](https://img.shields.io/badge/status-deprecated-red) **Deprecated since v2.1.** DataSync uses JWT authentication (`POST /auth/login`). API keys remain functional during the deprecation window only and will be removed in v3. See `MIGRATION_GUIDE_v2.0_to_v2.2.md`.

##### Parameters

> None

##### Responses

> | http code | content-type     | response    |
> |-----------|------------------|-------------|
> | `200`     | `application/json` | Key list   |
> | `401`     | `application/json` | Unauthorized |

##### Example cURL

> ```javascript
>  curl -H "Authorization: Bearer <access_token>" \
>       https://api.datasync.io/v2/me/api-keys
> ```

</details>

------------------------------------------------------------------------------------------

#### Create an API key *(legacy)*

<details>
 <summary><code>POST</code> <code><b>/me/api-keys</b></code> <code>(<b>DEPRECATED</b> — create an API key)</code></summary>

> ![Deprecated](https://img.shields.io/badge/status-deprecated-red) **Deprecated since v2.1.** Do not create new API keys — new integrations must use JWT authentication. Keys will be removed in v3. See `MIGRATION_GUIDE_v2.0_to_v2.2.md`.

##### Parameters

> | name   |  type     | data type | description  |
> |--------|-----------|-----------|--------------|
> | `name` |  required | string    | Key name     |

*(Request body — JSON)*

##### Responses

> | http code | content-type     | response   |
> |-----------|------------------|------------|
> | `201`     | `application/json` | Created   |

##### Example cURL

> ```javascript
>  curl -X POST -H "Content-Type: application/json" \
>       -H "Authorization: Bearer <access_token>" \
>       -d '{"name":"legacy-reporting-job"}' \
>       https://api.datasync.io/v2/me/api-keys
> ```

</details>

------------------------------------------------------------------------------------------

#### Get usage statistics

<details>
 <summary><code>GET</code> <code><b>/me/usage</b></code> <code>(get usage statistics for the authenticated user)</code></summary>

##### Parameters

> | name     |  type     | data type | description                                  |
> |----------|-----------|-----------|----------------------------------------------|
> | `period` |  optional | string    | `day` \| `week` \| `month` (default) \| `year` |

##### Responses

> | http code | content-type     | response    |
> |-----------|------------------|-------------|
> | `200`     | `application/json` | Usage stats |
> | `401`     | `application/json` | Unauthorized |

##### Example cURL

> ```javascript
>  curl -H "Authorization: Bearer <access_token>" \
>       "https://api.datasync.io/v2/me/usage?period=month"
> ```

</details>

------------------------------------------------------------------------------------------

# DataSync API Reference — v2.2

**Base URL (production):** `https://api.datasync.io/v2`
**Base URL (sandbox):** `https://sandbox.api.datasync.io/v2` — resets daily, pre-loaded with fake data.

> Formatting of this document follows the Swagger-inspired Markdown style from azagniotov's public GitHub Gist, ["Example to create beautiful REST API docs in Markdown, inspired by Swagger API docs"](https://gist.github.com/azagniotov/).

------------------------------------------------------------------------------------------

## General information

### Authentication

All endpoints except `/auth/login` and `/auth/refresh` require authentication:

```
Authorization: Bearer <access_token>
```

Obtain tokens via `POST /auth/login`. Access tokens expire after **1 hour**; refresh tokens after **7 days**. API-key authentication is **deprecated** (see [Deprecated endpoints](#deprecated-endpoints)).

### Content type

`Content-Type: application/json` is required for all `POST` / `PUT` / `PATCH` requests.

### CORS

CORS is pre-enabled for `*.datasync.io` domains. Other origins can be whitelisted on request — contact support.

### Response envelope

Every response is wrapped in a common envelope:

```json
{
  "success": true,
  "data": { "..." : "..." },
  "error": null,
  "meta": { "request_id": "b2c9...", "timestamp": "2024-12-15T10:30:00Z" }
}
```

On failure, `success` is `false`, `data` is `null`, and `error` is populated:

```json
{
  "success": false,
  "data": null,
  "error": { "code": "ERR_CODE", "message": "Human readable message", "details": {} },
  "meta": { "request_id": "b2c9...", "timestamp": "2024-12-15T10:30:00Z" }
}
```

Validation failures (`400`) include field-specific error messages in the error details. For `500` responses, always report the `meta.request_id` to support.

### Error codes

> | http code | meaning |
> |-----------|---------|
> | `400`     | Bad request — validation failed (field-specific messages included) |
> | `401`     | Not authenticated |
> | `403`     | Forbidden — authenticated but not authorized |
> | `404`     | Not found |
> | `409`     | Conflict (e.g., duplicate dataset name) |
> | `422`     | Unprocessable entity — business rule violation |
> | `429`     | Rate limited — see [Troubleshooting Guide](TROUBLESHOOTING_GUIDE.md) |
> | `500`     | Server error — include `meta.request_id` in your support request |

### Rate limiting

Requests are counted in per-endpoint **categories** (`standard`, `bulk`, `sync`). Every response includes:

> | header                 | description                          |
> |------------------------|--------------------------------------|
> | `X-RateLimit-Limit`    | Maximum requests in the current window |
> | `X-RateLimit-Remaining`| Requests remaining in the current window |
> | `X-RateLimit-Reset`    | Unix timestamp when the window resets |

See the [Troubleshooting Guide](TROUBLESHOOTING_GUIDE.md#2-rate-limiting-reference) for per-tier and per-category limits.

------------------------------------------------------------------------------------------

## Authentication

#### Obtain tokens

<details>
 <summary><code>POST</code> <code><b>/auth/login</b></code> <code>(authenticate user and get tokens)</code></summary>

##### Parameters

> | name      |  type     | data type | description | required |
> |-----------|-----------|-----------|-------------|----------|
> | `email`   |  body     | string    | Account email address | ✅ required |
> | `password`|  body     | string    | Account password      | ✅ required |

##### Responses

> | http code | content-type       | response                                             |
> |-----------|--------------------|------------------------------------------------------|
> | `200`     | `application/json` | `access_token` and `refresh_token` returned          |
> | `400`     | `application/json` | invalid credentials                                  |
> | `429`     | `application/json` | too many attempts                                    |

##### Example cURL

> ```bash
> curl -X POST https://api.datasync.io/v2/auth/login \
>   -H "Content-Type: application/json" \
>   -d '{"email": "user@example.com", "password": "s3cret"}'
> ```

</details>

<details>
 <summary><code>POST</code> <code><b>/auth/refresh</b></code> <code>(refresh access token)</code></summary>

##### Parameters

> | name            |  type     | data type | description                     | required |
> |-----------------|-----------|-----------|---------------------------------|----------|
> | `refresh_token` |  body     | string    | The refresh token from login    | ✅ required |

##### Responses

> | http code | content-type       | response                    |
> |-----------|--------------------|-----------------------------|
> | `200`     | `application/json` | New tokens returned         |
> | `400`     | `application/json` | Invalid token               |
> | `401`     | `application/json` | Expired token               |

##### Example cURL

> ```bash
> curl -X POST https://api.datasync.io/v2/auth/refresh \
>   -H "Content-Type: application/json" \
>   -d '{"refresh_token": "<refresh_token>"}'
> ```

</details>

<details>
 <summary><code>POST</code> <code><b>/auth/logout</b></code> <code>(invalidate tokens)</code></summary>

##### Parameters

> | name            |  type     | data type | description                                                                                   | required |
> |-----------------|-----------|-----------|-----------------------------------------------------------------------------------------------|----------|
> | `refresh_token` |  body     | string    | Optional. If omitted, **all** tokens for the user are invalidated                             | optional |

##### Responses

> | http code | content-type       | response   |
> |-----------|--------------------|------------|
> | `204`     | none               | Success    |

##### Example cURL

> ```bash
> curl -X POST https://api.datasync.io/v2/auth/logout \
>   -H "Authorization: Bearer <access_token>" \
>   -H "Content-Type: application/json" \
>   -d '{"refresh_token": "<refresh_token>"}'
> ```

</details>

------------------------------------------------------------------------------------------

## Datasets

#### List datasets

<details>
 <summary><code>GET</code> <code><b>/datasets</b></code> <code>(list all datasets for the authenticated user)</code></summary>

##### Parameters

> | name     |  type     | data type | description                                    | default | required |
> |----------|-----------|-----------|------------------------------------------------|---------|----------|
> | `page`   |  query    | int       | Page number                                    | `1`     | optional |
> | `limit`  |  query    | int       | Items per page — **maximum 100**               | `20`    | optional |
> | `status` |  query    | string    | Filter by status: `active` \| `archived` \| `all` | `all` | optional |

##### Responses

> | http code | content-type       | response        |
> |-----------|--------------------|-----------------|
> | `200`     | `application/json` | Dataset list    |
> | `401`     | `application/json` | Unauthorized    |

##### Example cURL

> ```bash
> curl -H "Authorization: Bearer <access_token>" \
>   "https://api.datasync.io/v2/datasets?page=1&limit=20&status=active"
> ```

</details>

#### Create dataset

<details>
 <summary><code>POST</code> <code><b>/datasets</b></code> <code>(create new dataset)</code></summary>

##### Parameters

> | name          |  type     | data type | description                                   | required |
> |---------------|-----------|-----------|-----------------------------------------------|----------|
> | `name`        |  body     | string    | Dataset name — must be **3–100 characters**   | ✅ required |
> | `description` |  body     | string    | Human-readable description                    | optional |
> | `tags`        |  body     | array     | List of tags                                  | optional |

##### Responses

> | http code | content-type       | response          |
> |-----------|--------------------|-------------------|
> | `201`     | `application/json` | Created dataset   |
> | `400`     | `application/json` | Validation error  |
> | `409`     | `application/json` | Duplicate name    |

##### Example cURL

> ```bash
> curl -X POST https://api.datasync.io/v2/datasets \
>   -H "Authorization: Bearer <access_token>" \
>   -H "Content-Type: application/json" \
>   -d '{"name": "customer-metrics", "description": "Q4 metrics", "tags": ["finance"]}'
> ```

</details>

#### Get / update / archive a dataset

<details>
 <summary><code>GET</code> <code><b>/datasets/{id}</b></code> <code>(get single dataset by ID)</code></summary>

##### Parameters

> | name |  type     | data type | description               | required |
> |------|-----------|-----------|---------------------------|----------|
> | `id` |  path     | string    | The dataset ID            | ✅ required |

##### Responses

> | http code | content-type       | response     |
> |-----------|--------------------|--------------|
> | `200`     | `application/json` | Dataset      |
> | `401`     | `application/json` | Unauthorized |
> | `404`     | `application/json` | Not found (or not owned by you) |

##### Example cURL

> ```bash
> curl -H "Authorization: Bearer <access_token>" \
>   https://api.datasync.io/v2/datasets/ds_123
> ```

</details>

<details>
 <summary><code>PUT</code> <code><b>/datasets/{id}</b></code> <code>(update dataset)</code></summary>

##### Parameters

> | name          |  type     | data type | description                        | required |
> |---------------|-----------|-----------|------------------------------------|----------|
> | `id`          |  path     | string    | The dataset ID                     | ✅ required |
> | `name`        |  body     | string    | New name (3–100 characters)        | optional |
> | `description` |  body     | string    | New description                    | optional |
> | `tags`        |  body     | array     | New tag list                       | optional |

##### Responses

> | http code | content-type       | response         |
> |-----------|--------------------|------------------|
> | `200`     | `application/json` | Updated dataset  |
> | `400`     | `application/json` | Validation error |
> | `404`     | `application/json` | Not found        |

##### Example cURL

> ```bash
> curl -X PUT https://api.datasync.io/v2/datasets/ds_123 \
>   -H "Authorization: Bearer <access_token>" \
>   -H "Content-Type: application/json" \
>   -d '{"name": "customer-metrics-2025"}'
> ```

</details>

<details>
 <summary><code>DELETE</code> <code><b>/datasets/{id}</b></code> <code>(archive dataset — <b>soft delete</b>)</code></summary>

##### Parameters

> | name |  type     | data type | description    | required |
> |------|-----------|-----------|----------------|----------|
> | `id` |  path     | string    | The dataset ID | ✅ required |

##### Responses

> | http code | content-type       | response   |
> |-----------|--------------------|------------|
> | `204`     | none               | Success    |
> | `404`     | `application/json` | Not found  |

##### Notes

> This is a **soft delete**: it sets the dataset's `archived` flag. The dataset is not destroyed. Archiving fires the **`dataset.updated`** webhook event — there is **no `dataset.deleted` event** (see [Webhooks](#webhooks)).

##### Example cURL

> ```bash
> curl -X DELETE -H "Authorization: Bearer <access_token>" \
>   https://api.datasync.io/v2/datasets/ds_123
> ```

</details>

------------------------------------------------------------------------------------------

## Records

#### List records

<details>
 <summary><code>GET</code> <code><b>/datasets/{id}/records</b></code> <code>(list records in dataset)</code></summary>

##### Parameters

> | name     |  type     | data type | description                                            | default | required |
> |----------|-----------|-----------|--------------------------------------------------------|---------|----------|
> | `id`     |  path     | string    | The dataset ID                                         |         | ✅ required |
> | `page`   |  query    | int       | Page number                                            | `1`     | optional |
> | `limit`  |  query    | int       | Items per page (max 100)                               | `20`    | optional |
> | `sort`   |  query    | string    | Field name to sort by                                  |         | optional |
> | `order`  |  query    | string    | Sort order: `asc` \| `desc`                            | `asc`   | optional |
> | `filter` |  query    | string    | **JSON-encoded** filter query                          |         | optional |

##### Responses

> | http code | content-type       | response             |
> |-----------|--------------------|----------------------|
> | `200`     | `application/json` | Record list          |
> | `404`     | `application/json` | Dataset not found    |

##### Example cURL

> ```bash
> curl -G -H "Authorization: Bearer <access_token>" \
>   "https://api.datasync.io/v2/datasets/ds_123/records" \
>   --data-urlencode 'sort=created_at' \
>   --data-urlencode 'order=desc' \
>   --data-urlencode 'filter={"region":"EU"}'
> ```

</details>

#### Add records (single or bulk)

<details>
 <summary><code>POST</code> <code><b>/datasets/{id}/records</b></code> <code>(add records to dataset)</code></summary>

##### Parameters

> | name       |  type     | data type      | description                                              | required |
> |------------|-----------|----------------|----------------------------------------------------------|----------|
> | `id`       |  path     | string         | The dataset ID                                           | ✅ required |
> | `records`  |  body     | array (object) | A single record object or an array — **max 1000 records per request** | ✅ required |

##### Responses

> | http code | content-type       | response          |
> |-----------|--------------------|-------------------|
> | `201`     | `application/json` | Created           |
> | `400`     | `application/json` | Validation error  |
> | `404`     | `application/json` | Dataset not found |

##### Notes

> This endpoint is in the **`bulk` rate-limit category** — separate, lower limits apply. Bulk inserts are up to **3x faster** as of v2.2.

##### Example cURL

> ```bash
> curl -X POST https://api.datasync.io/v2/datasets/ds_123/records \
>   -H "Authorization: Bearer <access_token>" \
>   -H "Content-Type: application/json" \
>   -d '{"records": [{"sku": "A-1", "qty": 4}, {"sku": "A-2", "qty": 9}]}'
> ```

</details>

#### Single record operations

<details>
 <summary><code>GET</code> <code><b>/datasets/{id}/records/{record_id}</b></code> <code>(get single record)</code></summary>

##### Parameters

> | name        |  type     | data type | description     | required |
> |-------------|-----------|-----------|-----------------|----------|
> | `id`        |  path     | string    | The dataset ID  | ✅ required |
> | `record_id` |  path     | string    | The record ID   | ✅ required |

##### Responses

> | http code | content-type       | response   |
> |-----------|--------------------|------------|
> | `200`     | `application/json` | Record     |
> | `404`     | `application/json` | Not found  |

##### Example cURL

> ```bash
> curl -H "Authorization: Bearer <access_token>" \
>   https://api.datasync.io/v2/datasets/ds_123/records/rec_456
> ```

</details>

<details>
 <summary><code>PATCH</code> <code><b>/datasets/{id}/records/{record_id}</b></code> <code>(update single record — partial update)</code></summary>

##### Parameters

> | name        |  type     | data type          | description                       | required |
> |-------------|-----------|--------------------|-----------------------------------|----------|
> | `id`        |  path     | string             | The dataset ID                    | ✅ required |
> | `record_id` |  path     | string             | The record ID                     | ✅ required |
> | *(body)*    |  body     | object             | Field/value pairs to update — only supplied fields change | ✅ required |

##### Responses

> | http code | content-type       | response         |
> |-----------|--------------------|------------------|
> | `200`     | `application/json` | Updated          |
> | `400`     | `application/json` | Validation error |
> | `404`     | `application/json` | Not found        |

##### Example cURL

> ```bash
> curl -X PATCH https://api.datasync.io/v2/datasets/ds_123/records/rec_456 \
>   -H "Authorization: Bearer <access_token>" \
>   -H "Content-Type: application/json" \
>   -d '{"qty": 12}'
> ```

</details>

<details>
 <summary><code>DELETE</code> <code><b>/datasets/{id}/records/{record_id}</b></code> <code>(delete single record — <b>hard delete</b>)</code></summary>

##### Parameters

> | name        |  type     | data type | description     | required |
> |-------------|-----------|-----------|-----------------|----------|
> | `id`        |  path     | string    | The dataset ID  | ✅ required |
> | `record_id` |  path     | string    | The record ID   | ✅ required |

##### Responses

> | http code | content-type       | response   |
> |-----------|--------------------|------------|
> | `204`     | none               | Success    |
> | `404`     | `application/json` | Not found  |

##### Notes

> Unlike dataset deletion (soft delete/archive), record deletion is permanent.

##### Example cURL

> ```bash
> curl -X DELETE -H "Authorization: Bearer <access_token>" \
>   https://api.datasync.io/v2/datasets/ds_123/records/rec_456
> ```

</details>

------------------------------------------------------------------------------------------

## Sync & jobs

#### Trigger sync

<details>
 <summary><code>POST</code> <code><b>/datasets/{id}/sync</b></code> <code>(trigger sync with external source — async)</code></summary>

##### Parameters

> | name        |  type     | data type | description                                                            | required |
> |-------------|-----------|-----------|------------------------------------------------------------------------|----------|
> | `id`        |  path     | string    | The dataset ID                                                         | ✅ required |
> | `source_id` |  body     | string    | The external source ID to sync from                                    | ✅ required |
> | `mode`      |  body     | string    | `full` (default) — re-sync everything; `incremental` — only changes since the last successful sync | optional (default `full`) |

##### Responses

> | http code | content-type       | response                                   |
> |-----------|--------------------|--------------------------------------------|
> | `202`     | `application/json` | Job started — returns `job_id`             |
> | `400`     | `application/json` | Invalid source                             |
> | `404`     | `application/json` | Dataset not found                          |

##### Notes

> Sync runs asynchronously. Track progress via `GET /jobs/{job_id}` or subscribe to the `sync.completed` / `sync.failed` webhook events. This endpoint is in the **`sync` rate-limit category**.

##### Example cURL

> ```bash
> curl -X POST https://api.datasync.io/v2/datasets/ds_123/sync \
>   -H "Authorization: Bearer <access_token>" \
>   -H "Content-Type: application/json" \
>   -d '{"source_id": "src_789", "mode": "incremental"}'
> ```

</details>

#### Check job status

<details>
 <summary><code>GET</code> <code><b>/jobs/{job_id}</b></code> <code>(check async job status)</code></summary>

##### Parameters

> | name      |  type     | data type | description   | required |
> |-----------|-----------|-----------|---------------|----------|
> | `job_id`  |  path     | string    | The job ID    | ✅ required |

##### Responses

> | http code | content-type       | response                            |
> |-----------|--------------------|-------------------------------------|
> | `200`     | `application/json` | Job status object (see below)       |
> | `404`     | `application/json` | Job not found                       |

##### Job status object

> ```json
> {
>   "status": "pending" | "running" | "completed" | "failed",
>   "progress": 0,
>   "error": null
> }
> ```
>
> `progress` ranges from 0–100; `error` is a string or `null`.

##### Example cURL

> ```bash
> curl -H "Authorization: Bearer <access_token>" \
>   https://api.datasync.io/v2/jobs/job_abc
> ```

</details>

------------------------------------------------------------------------------------------

## Webhooks

> **Available events (complete list):**
>
> | event             | fired when                                                        |
> |-------------------|-------------------------------------------------------------------|
> | `dataset.created` | A dataset is created                                              |
> | `dataset.updated` | A dataset is updated **or archived** (soft delete)                |
> | `sync.completed`  | A sync job completes successfully                                 |
> | `sync.failed`     | A sync job fails                                                  |
>
> ⚠️ There is **no `dataset.deleted` event**. `DELETE /datasets/{id}` is a soft delete and fires `dataset.updated` with the dataset's `archived` flag set.

#### List webhooks

<details>
 <summary><code>GET</code> <code><b>/webhooks</b></code> <code>(list configured webhooks)</code></summary>

##### Parameters

> None

##### Responses

> | http code | content-type       | response        |
> |-----------|--------------------|-----------------|
> | `200`     | `application/json` | Webhook list    |
> | `401`     | `application/json` | Unauthorized    |

##### Example cURL

> ```bash
> curl -H "Authorization: Bearer <access_token>" \
>   https://api.datasync.io/v2/webhooks
> ```

</details>

#### Create webhook

<details>
 <summary><code>POST</code> <code><b>/webhooks</b></code> <code>(create webhook)</code></summary>

##### Parameters

> | name     |  type     | data type | description                                                                                          | required |
> |----------|-----------|-----------|------------------------------------------------------------------------------------------------------|----------|
> | `url`    |  body     | string    | HTTPS endpoint that will receive deliveries                                                          | ✅ required |
> | `events` |  body     | array     | Event types to subscribe to: `dataset.created`, `dataset.updated`, `sync.completed`, `sync.failed`   | ✅ required |
> | `secret` |  body     | string    | Optional. If provided, deliveries include an `X-Webhook-Signature` header (HMAC-SHA256)              | optional |

##### Responses

> | http code | content-type       | response          |
> |-----------|--------------------|-------------------|
> | `201`     | `application/json` | Created webhook   |
> | `400`     | `application/json` | Validation error  |

##### Example cURL

> ```bash
> curl -X POST https://api.datasync.io/v2/webhooks \
>   -H "Authorization: Bearer <access_token>" \
>   -H "Content-Type: application/json" \
>   -d '{"url": "https://hooks.example.com/datasync", "events": ["dataset.updated", "sync.completed"], "secret": "whsec_..."}'
> ```

</details>

#### Get webhook details

<details>
 <summary><code>GET</code> <code><b>/webhooks/{id}</b></code> <code>(get webhook details)</code></summary>

##### Parameters

> | name |  type     | data type | description     | required |
> |------|-----------|-----------|-----------------|----------|
> | `id` |  path     | string    | The webhook ID  | ✅ required |

##### Responses

> | http code | content-type       | response    |
> |-----------|--------------------|-------------|
> | `200`     | `application/json` | Webhook     |
> | `404`     | `application/json` | Not found   |

##### Example cURL

> ```bash
> curl -H "Authorization: Bearer <access_token>" \
>   https://api.datasync.io/v2/webhooks/wh_123
> ```

</details>

#### Delete webhook

<details>
 <summary><code>DELETE</code> <code><b>/webhooks/{id}</b></code> <code>(delete webhook)</code></summary>

##### Parameters

> | name |  type     | data type | description     | required |
> |------|-----------|-----------|-----------------|----------|
> | `id` |  path     | string    | The webhook ID  | ✅ required |

##### Responses

> | http code | content-type       | response   |
> |-----------|--------------------|------------|
> | `204`     | none               | Success    |
> | `404`     | `application/json` | Not found  |

##### Example cURL

> ```bash
> curl -X DELETE -H "Authorization: Bearer <access_token>" \
>   https://api.datasync.io/v2/webhooks/wh_123
> ```

</details>

### Webhook deliveries

Every delivery is a `POST` to your configured `url` with a JSON body:

```json
{
  "event": "sync.completed",
  "timestamp": "2024-12-15T10:30:00Z",
  "data": { "job_id": "job_abc" }
}
```

### Verifying webhook signatures

If you supplied a `secret` when creating the webhook, every delivery includes:

```
X-Webhook-Signature: <hex digest>
```

The signature is the **HMAC-SHA256 of the raw request body**, keyed with your secret. Verify every delivery before processing it:

1. Read the **raw, unparsed** body bytes (do not re-serialize parsed JSON — key order and whitespace will differ).
2. Compute `HMAC-SHA256(secret, raw_body)` and hex-encode the digest.
3. Compare it to the `X-Webhook-Signature` header using a **constant-time comparison**.
4. Reject the delivery if the signature is missing or mismatched.

```python
import hmac, hashlib

def verify_webhook(secret: str, raw_body: bytes, signature_header: str) -> bool:
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)

# Flask example
@app.route("/datasync/webhook", methods=["POST"])
def datasync_webhook():
    raw = request.get_data()                      # raw bytes — do not parse first
    if not verify_webhook(WEBHOOK_SECRET, raw, request.headers.get("X-Webhook-Signature", "")):
        return "", 400
    payload = json.loads(raw)
    # handle payload["event"], payload["data"] ...
    return "", 204
```

------------------------------------------------------------------------------------------

## User

#### Get current user

<details>
 <summary><code>GET</code> <code><b>/me</b></code> <code>(get current user profile)</code></summary>

##### Parameters

> None

##### Responses

> | http code | content-type       | response      |
> |-----------|--------------------|---------------|
> | `200`     | `application/json` | User profile  |
> | `401`     | `application/json` | Unauthorized  |

##### Example cURL

> ```bash
> curl -H "Authorization: Bearer <access_token>" https://api.datasync.io/v2/me
> ```

</details>

<details>
 <summary><code>PATCH</code> <code><b>/me</b></code> <code>(update current user profile)</code></summary>

##### Parameters

> | name       |  type     | data type | description          | required |
> |------------|-----------|-----------|----------------------|----------|
> | `name`     |  body     | string    | Display name         | optional |
> | `email`    |  body     | string    | Account email        | optional |
> | `timezone` |  body     | string    | IANA timezone name   | optional |

##### Responses

> | http code | content-type       | response         |
> |-----------|--------------------|------------------|
> | `200`     | `application/json` | Updated profile  |
> | `400`     | `application/json` | Validation error |

##### Example cURL

> ```bash
> curl -X PATCH https://api.datasync.io/v2/me \
>   -H "Authorization: Bearer <access_token>" \
>   -H "Content-Type: application/json" \
>   -d '{"timezone": "Europe/Berlin"}'
> ```

</details>

<details>
 <summary><code>GET</code> <code><b>/me/usage</b></code> <code>(get usage statistics)</code></summary>

##### Parameters

> | name     |  type     | data type | description                                | default  | required |
> |----------|-----------|-----------|--------------------------------------------|----------|----------|
> | `period` |  query    | string    | One of `day` \| `week` \| `month` \| `year` | `month`  | optional |

##### Responses

> | http code | content-type       | response      |
> |-----------|--------------------|---------------|
> | `200`     | `application/json` | Usage stats   |
> | `401`     | `application/json` | Unauthorized  |

##### Example cURL

> ```bash
> curl -H "Authorization: Bearer <access_token>" \
>   "https://api.datasync.io/v2/me/usage?period=week"
> ```

</details>

------------------------------------------------------------------------------------------

## Deprecated endpoints

> ⚠️ **The endpoints below are deprecated (since v2.1) and will be removed in v3.** They continue to function during the transition period, but do not build new integrations on them. Migrate to JWT authentication — see the [Migration Guide](MIGRATION_GUIDE_v2.0_to_v2.2.md).

<details>
 <summary>⚠️ <code>GET</code> <code><b>/me/api-keys</b></code> <code>(list API keys — <b>DEPRECATED</b>, use JWT)</code></summary>

##### Parameters

> None

##### Responses

> | http code | content-type       | response      |
> |-----------|--------------------|---------------|
> | `200`     | `application/json` | Key list      |
> | `401`     | `application/json` | Unauthorized  |

##### Example cURL

> ```bash
> curl -H "Authorization: <API key credentials>" https://api.datasync.io/v2/me/api-keys
> ```

</details>

<details>
 <summary>⚠️ <code>POST</code> <code><b>/me/api-keys</b></code> <code>(create API key — <b>DEPRECATED</b>, use JWT)</code></summary>

##### Parameters

> | name   |  type     | data type | description      | required |
> |--------|-----------|-----------|------------------|----------|
> | `name` |  body     | string    | Key display name | ✅ required |

##### Responses

> | http code | content-type       | response       |
> |-----------|--------------------|----------------|
> | `201`     | `application/json` | Created key    |

##### Example cURL

> ```bash
> curl -X POST https://api.datasync.io/v2/me/api-keys \
>   -H "Authorization: <API key credentials>" \
>   -H "Content-Type: application/json" \
>   -d '{"name": "legacy-pipeline"}'
> ```

</details>

------------------------------------------------------------------------------------------

## Endpoint index

> | method   | path                                  | auth required | rate-limit category | added in | deprecated |
> |----------|---------------------------------------|---------------|---------------------|----------|------------|
> | `POST`   | `/auth/login`                         | no            | standard            | v2.1.0   | —          |
> | `POST`   | `/auth/refresh`                       | no            | standard            | v2.1.0   | —          |
> | `POST`   | `/auth/logout`                        | yes           | standard            | v2.1.0   | —          |
> | `GET`    | `/datasets`                           | yes           | standard            | v2.0.0   | —          |
> | `POST`   | `/datasets`                           | yes           | standard            | v2.0.0   | —          |
> | `GET`    | `/datasets/{id}`                      | yes           | standard            | v2.0.0   | —          |
> | `PUT`    | `/datasets/{id}`                      | yes           | standard            | v2.0.0   | —          |
> | `DELETE` | `/datasets/{id}`                      | yes           | standard            | v2.0.0   | —          |
> | `GET`    | `/datasets/{id}/records`              | yes           | standard            | v2.0.0   | —          |
> | `POST`   | `/datasets/{id}/records`              | yes           | **bulk**            | v2.0.0   | —          |
> | `GET`    | `/datasets/{id}/records/{record_id}`  | yes           | standard            | v2.0.0   | —          |
> | `PATCH`  | `/datasets/{id}/records/{record_id}`  | yes           | standard            | v2.0.0   | —          |
> | `DELETE` | `/datasets/{id}/records/{record_id}`  | yes           | standard            | v2.0.0   | —          |
> | `POST`   | `/datasets/{id}/sync`                 | yes           | **sync**            | v2.0.0   | —          |
> | `GET`    | `/jobs/{job_id}`                      | yes           | standard            | v2.0.0   | —          |
> | `GET`    | `/webhooks`                           | yes           | standard            | v2.2.0   | —          |
> | `POST`   | `/webhooks`                           | yes           | standard            | v2.2.0   | —          |
> | `GET`    | `/webhooks/{id}`                      | yes           | standard            | v2.2.0   | —          |
> | `DELETE` | `/webhooks/{id}`                      | yes           | standard            | v2.2.0   | —          |
> | `GET`    | `/me`                                 | yes           | standard            | v2.0.0   | —          |
> | `PATCH`  | `/me`                                 | yes           | standard            | v2.0.0   | —          |
> | `GET`    | `/me/api-keys`                        | yes           | standard            | v2.0.0   | ⚠️ **yes** |
> | `POST`   | `/me/api-keys`                        | yes           | standard            | v2.0.0   | ⚠️ **yes** |
> | `GET`    | `/me/usage`                           | yes           | standard            | v2.1.0   | —          |

# Migrating from TaskFlow API v1 to v2

**Audience:** developers with integrations built against TaskFlow API v1 (`https://api.taskflow.io/api`).
**Target version:** TaskFlow API v2.0.1 (`https://api.taskflow.io/api/v2`).
**Related reading:** [TaskFlow API v2 Reference](taskflow_api_v2.md) · [Webhook Event Catalog](webhook_event_catalog.html)

A v1 deprecation timeline has not yet been announced. Migrate at your convenience, but do not build new integrations on v1: v1 receives maintenance fixes only.

---

## 1. Breaking changes at a glance

| Area | v1 | v2 | Action required |
|---|---|---|---|
| Base URL | `https://api.taskflow.io/api` | `https://api.taskflow.io/api/v2` | Update every request |
| Authentication | `X-API-Key: <key>` header | `Authorization: Bearer <token>` | Change the header on every call |
| Timestamps | Unix integers everywhere | ISO 8601 UTC (`2024-03-01T14:22:00Z`) | Change parsing/formatting; two legacy exceptions remain (§6) |
| Pagination | `limit` / `offset` | `page` / `per_page` (default 25, max 200) | Rewrite paging logic |
| List response shape | Bare JSON array | `data` array + `meta` object (tasks) | Update response parsing |
| Task create success | `200 OK` | `201 Created` | Accept 201 |
| Task assignment | `PUT /api/tasks/:id/assign` | `POST /api/v2/tasks/{taskId}/assign` | Change method and body field |
| Task comments | `GET/POST /api/tasks/:id/comments` | `GET /tasks/{taskId}?include=comments` (read); no standalone write endpoint in v2 spec (§8) | Rework comment flows |
| Task history | `GET /api/tasks/:id/history` | `GET /tasks/{taskId}?include=history` | Move to `include` |
| Current user | `GET /api/users/current` | `GET /api/v2/users/me` | Update path; field renames |
| Search | `GET /api/search` (tasks + projects) | `GET /api/v2/search/tasks` (tasks only) | Drop `type`; re-scope expectations |
| Project delete | Always cascades | `cascade` query parameter, default `true` | Behavior-compatible; opt out with `cascade=false` |
| Errors | `{"error": "message"}` | Structured `{"error": {"code", "message", "details"}}` (legacy format still appears on some endpoints) | Parse both during migration |
| Rate limit | 60 requests/minute | 100/min (free), 1000/min (paid), `429` + `X-RateLimit-Remaining` | Handle 429; limits improved |
| Webhook events | 4 event types | 10 declared event types (+2 observed in production) | Re-subscribe and verify handlers |

## 2. Update the base URL and authentication

Replace:

```plaintext
X-API-Key: <your_key>
GET https://api.taskflow.io/api/tasks
```

with:

```bash
curl --header "Authorization: Bearer <your_api_token>" \
  --url "https://api.taskflow.io/api/v2/tasks"
```

Your existing dashboard API key continues to work as a Bearer token. If your integration acts on behalf of individual users, switch to the new token flow:

1. `POST /api/v2/auth/login` with `email` and `password` (plus `mfa_code` if the account has MFA enabled) → returns `access_token` and `refresh_token`.
2. Send `Authorization: Bearer <access_token>` on subsequent calls.
3. When the access token expires, call `POST /api/v2/auth/refresh` with the `refresh_token` to obtain a new pair. A `401` from refresh means the token expired or was revoked — re-authenticate.

## 3. Rename request fields

v2 normalized several v1 field names. These renames apply to request and response bodies:

| Object | v1 field | v2 field |
|---|---|---|
| Task | `desc` | `description` |
| Task | `assigned_to` | `assignee_id` |
| Task | `project` | `project_id` |
| Task | `deadline` (Unix timestamp) | `due_date` (ISO 8601 date-time) |
| Task assignment | `user_id` | `assignee_id` |
| Project | `desc` | `description` |
| User | `org` | `organization_id` |

New optional fields you can adopt immediately: `tags` and `watchers` on task create/update, `template_id` and `visibility` on project create, `notify` on task assignment, `secret` and `active` on webhook create.

## 4. Convert timestamps

v1 used Unix timestamps for all dates. v2 uses ISO 8601 UTC strings with a trailing `Z`.

```text
v1: "deadline": 1711929600        →  v2: "due_date": "2024-04-01T00:00:00Z"
v1: history entry "when": 1709312400 →  v2: "created_at": "2024-03-01T14:22:00Z"
```

Two documented v2 fields are still Unix timestamps — handle them explicitly:

- Project object: `created` (Unix) — scheduled to migrate.
- User object (`GET /users/me`): `last_login` (Unix) — `created_at` in the same object is already ISO 8601.

## 5. Migrate pagination and list responses

| v1 | v2 |
|---|---|
| `?limit=25&offset=50` | `?page=3&per_page=25` |
| Response: `[ {...}, {...} ]` | Response: `{ "data": [ {...} ], "meta": { "current_page": 3, "total_pages": 5, "total_count": 98 } }` |

Notes:

- `per_page` defaults to 25 and caps at 200 on the tasks endpoint; the v1 default of 20 no longer applies.
- Task list filters gain `blocked` as a status value, a `label` filter, and `due_date` as a sort field.
- The projects list is now paginated in v2 (it accepts `page`/`per_page`). The projects response still uses a `projects` array rather than the `data` envelope — parse per endpoint.

## 6. Endpoint-by-endpoint mapping

| v1 endpoint | v2 endpoint | Notes |
|---|---|---|
| `GET /api/tasks` | `GET /api/v2/tasks` | Params renamed (`project`→`project_id`, `limit`/`offset`→`page`/`per_page`); envelope changed |
| `POST /api/tasks` | `POST /api/v2/tasks` | Returns `201`, field renames (§3), `due_date` is ISO 8601 |
| `GET /api/tasks/:id` | `GET /api/v2/tasks/{taskId}` | Add `include=comments,history,attachments` to expand related resources |
| `PUT /api/tasks/:id` | `PUT /api/v2/tasks/{taskId}` | Still partial updates; new `409 Conflict` on concurrent modification — add retry logic |
| `DELETE /api/tasks/:id` | `DELETE /api/v2/tasks/{taskId}` | Returns `204 No Content`; permission model is now documented (admin/owner on the project, or the task's creator) |
| `PUT /api/tasks/:id/assign` | `POST /api/v2/tasks/{taskId}/assign` | Method changed PUT→POST; body `user_id`→`assignee_id`; optional `notify` (default `true`) |
| `GET /api/tasks/:id/comments` | `GET /api/v2/tasks/{taskId}?include=comments` | See §8 |
| `POST /api/tasks/:id/comments` | *No v2 equivalent in the current spec* | See §8 |
| `GET /api/tasks/:id/history` | `GET /api/v2/tasks/{taskId}?include=history` | History timestamps are ISO 8601 in v2 |
| `GET /api/projects` | `GET /api/v2/projects` | Adds `archived`, `role` (now including `viewer`), pagination |
| `POST /api/projects` | `POST /api/v2/projects` | `desc`→`description`; adds `template_id`, `visibility` |
| `DELETE /api/projects/:id` | `DELETE /api/v2/projects/{projectId}` | Same cascade behavior by default; `cascade=false` preserves tasks |
| `GET /api/users/current` | `GET /api/v2/users/me` | `org`→`organization_id`; adds `timezone`; supports `fields` filter |
| `GET /api/search` | `GET /api/v2/search/tasks` | Tasks only; `type` parameter removed; adds `project_id`, `status`, `date_from`, `date_to`; `q` is required |
| `POST /api/webhooks` | `POST /api/v2/webhooks` | Event list expanded 4→10; adds `secret`, `active` (§7) |
| `GET /api/webhooks` | *No v2 equivalent in the current spec* | Webhook listing is not exposed in v2; store webhook IDs at creation time |
| `DELETE /api/webhooks/:id` | `DELETE /api/v2/webhooks/{webhookId}` | Returns `204 No Content` |

## 7. Re-subscribe your webhooks

v1 events were `task.created`, `task.updated`, `task.deleted`, `project.created`. All four still exist in v2 with the same names. The declared v2 enum adds six subscribable types:

`task.assigned`, `task.commented`, `project.archived`, `project.deleted`, `member.added`, `member.removed`

Production also emits two scheduled event types that are **not** yet declared in the OpenAPI spec and therefore cannot be subscribed to via `POST /webhooks`: `task.due_date_approaching` and `task.overdue`. See the [Webhook Event Catalog](webhook_event_catalog.html) for descriptions, trigger conditions, payloads, and provenance for all twelve.

Other webhook changes:

- Provide a `secret` to receive HMAC-verifiable payloads; verify the signature against the raw body.
- Delivery retry policy: up to 3 retries, then the delivery is marked `failed`.
- Set `active: false` to pause a webhook without deleting it.

## 8. Capabilities with no direct v2 equivalent

Plan around these gaps:

1. **Posting task comments** (`POST /api/tasks/:id/comments`) has no standalone v2 endpoint in the current OpenAPI spec. Reading comments is covered by `include=comments`. If you post comments via the API, keep the v1 call working until a v2 endpoint ships, or move comment creation into the UI. (A `task.commented` webhook event exists, confirming comment support in the platform.)
2. **Listing webhooks** (`GET /api/webhooks`) is not exposed in v2. Persist your `webhook_id` values when you create webhooks so you can delete them later.
3. **Project search** is gone: `GET /api/search?type=project` has no v2 counterpart because search is task-scoped. Track project membership client-side or via the `member.*` webhook events.

## 9. Parse the new error format

v2's canonical error body is structured:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Title is required",
    "details": []
  }
}
```

Some endpoints still return the legacy v1-style shape:

```json
{
  "error": "not_found",
  "message": "Task not found"
}
```

Write a parser that accepts both: if `error` is an object, read `error.code` and `error.message`; if it is a string, read the sibling `message`. The legacy format is transitional and will be removed.

## 10. Migration checklist

1. Update base URL to `https://api.taskflow.io/api/v2` everywhere.
2. Replace the `X-API-Key` header with `Authorization: Bearer <token>`; if you adopt the login flow, implement refresh-on-401 and MFA handling.
3. Rename fields: `desc`, `assigned_to`, `project`, `deadline`, `user_id`, `org` (§3).
4. Convert Unix timestamps to ISO 8601 on send; parse ISO 8601 on receive, with explicit handling for project `created` and user `last_login` (§4).
5. Switch pagination to `page`/`per_page` and unwrap the `data`/`meta` envelope on the tasks list.
6. Expect `201 Created` (not `200 OK`) from task and project creation, and `204 No Content` from deletions.
7. Change task assignment from `PUT .../assign` to `POST .../assign` with `assignee_id`.
8. Move comment and history reads to `?include=`; plan for the missing comment-write endpoint (§8).
9. Handle `409 Conflict` on task updates with a re-fetch-and-retry loop.
10. Make `DELETE /projects/{id}` explicit about `cascade` for your safety posture.
11. Update search calls to `GET /search/tasks` and remove the `type` parameter.
12. Re-register webhooks with the expanded event list and add HMAC verification; subscribe only to declared events (§7).
13. Parse both error formats (§9).
14. Handle `429` with backoff using `X-RateLimit-Remaining`.

## 11. What you gain in v2

- Token-based auth with MFA support and refresh flow (`/auth/login`, `/auth/refresh`).
- Related-resource expansion (`include=comments,history,attachments`) replacing three separate endpoints.
- Label filtering, due-date sorting, and the `blocked` task status.
- Watchers, task templates (`template_id`), and project visibility levels.
- Concurrency safety via `409 Conflict`.
- A richer webhook platform: ten subscribable event types, HMAC secrets, pause/resume via `active`, and documented retry behavior.
- Higher rate limits (100/min free tier, 1000/min paid) with an explicit `429` + `X-RateLimit-Remaining` contract.

---
title: TaskFlow API v1 to v2 Migration Guide
description: How to migrate integrations from the TaskFlow API v1 to the v2 REST API
---

# TaskFlow API v1 → v2 Migration Guide

This guide helps you migrate integrations from the TaskFlow API **v1** to the **v2** REST API. It covers every breaking change, maps each v1 endpoint to its v2 replacement, and provides before/after examples you can adapt directly.

**Sources.** The v2 material in this guide reflects the reconciled TaskFlow API v2 documentation (see `taskflow_api_v2_docs.md`), the OpenAPI 3.0 specification (version 2.0.1), and the documented discrepancy report. Where the sources disagreed, the specification was treated as the source of truth.

## Overview of what changed

v2 is a substantial revision. The changes fall into four groups:

1. **Breaking changes** — things that will make v1 integrations fail (authentication headers, endpoint paths, field names, pagination, timestamps, error format, the assign verb).
2. **Changed behavior** — things that still work but behave differently (deleting projects, search scope, validation strictness).
3. **New capabilities** — things v1 could not do at all (label filters, watchers, MFA login flow, webhook signing, richer user roles).
4. **Known inconsistencies** — residual issues in the v2 platform that you should code defensively around.

## Deprecation timeline

No formal v1 end-of-life date is published in the available materials. Plan your migration without assuming a hard cutoff, and contact `support@taskflow.io` for your tenant's v1 retirement schedule if you need one.

## Quick reference: breaking changes

| Area | v1 | v2 |
|------|----|----|
| Base URL | `https://api.taskflow.io/api` | `https://api.taskflow.io/api/v2` |
| Authentication | `X-API-Key: <key>` header | `Authorization: Bearer <token>` |
| List pagination | `limit`, `offset` | `page`, `per_page` (+ `meta` object) |
| Timestamps | Unix seconds | ISO 8601 (two exceptions, see below) |
| Error body | `{"error": "message"}` | Structured error envelope |
| Assign a task | `PUT /api/tasks/:id/assign` | `POST /tasks/{taskId}/assign` |
| Task description field | `desc` | `description` |
| Task due date field | `deadline` (Unix timestamp) | `due_date` (ISO 8601 date-time) |
| Task assignee field | `assigned_to` | `assignee_id` |
| Task project field | `project` | `project_id` |
| Comments & history | Dedicated endpoints | `include` query parameter on task retrieval |
| Search | `GET /api/search` (tasks and projects) | `GET /search/tasks` (tasks only) |
| Organization field on user | `org` | `organization_id` |
| Webhook events | 4 events | 10 declared events (+2 observed, see below) |
| Rate limit | 60 requests/minute | 100/minute (free), 1000/minute (paid) |

## Base URL

All v2 requests go to a versioned base URL. Update the base URL in your client first — nothing from v1 resolves under v2 paths.

```
v1: https://api.taskflow.io/api/tasks
v2: https://api.taskflow.io/api/v2/tasks
```

## Authentication

v1 used a static key sent in an `X-API-Key` header. v2 uses Bearer authentication:

```
Authorization: Bearer <token>
```

There are two paths to a valid v2 credential:

- **API keys** — generate a key from the TaskFlow dashboard under *Settings > API Keys* and send it as a Bearer token. This is the closest equivalent to the v1 flow.
- **User tokens** — v2 adds first-class authentication endpoints that v1 never had:
  - `POST /auth/login` with `email` and `password` obtains a token. If the account has multi-factor authentication enabled, the API returns `403` with an indication that MFA is required; retry with an `mfa_code` included.
  - `POST /auth/refresh` exchanges a refresh token for a new access token. Build token refresh into your client if you use this flow.

Note that `/auth/login` is intentionally open (no Bearer header required) — this is a change from v1, where "all API calls require an API key."

## Endpoint mapping

| v1 endpoint | v2 endpoint | Notes |
|-------------|-------------|-------|
| `GET /api/tasks` | `GET /tasks` | Parameter changes, see [pagination](#pagination) and [filters](#new-filter-and-sort-options). |
| `POST /api/tasks` | `POST /tasks` | Body field renames; returns `201 Created` (v1 returned `200 OK`). |
| `GET /api/tasks/:id` | `GET /tasks/{taskId}` | Supports `include=comments,history,attachments`. |
| `PUT /api/tasks/:id` | `PUT /tasks/{taskId}` | Same verb; body field renames; may return `409`. |
| `DELETE /api/tasks/:id` | `DELETE /tasks/{taskId}` | Permission check returns `403` when insufficient. |
| `PUT /api/tasks/:id/assign` | `POST /tasks/{taskId}/assign` | **Verb changed.** Body field renamed `user_id` → `assignee_id`. |
| `GET /api/tasks/:id/comments` | `GET /tasks/{taskId}?include=comments` | No longer a separate endpoint. |
| `POST /api/tasks/:id/comments` | *No direct v2 equivalent in the specification* | Reading comments moved to `include`; there is no documented v2 comment-creation endpoint — see [removed and folded endpoints](#removed-and-folded-endpoints). |
| `GET /api/tasks/:id/history` | `GET /tasks/{taskId}?include=history` | No longer a separate endpoint. |
| `GET /api/projects` | `GET /projects` | Now paginated; supports `archived` and `role` filters. |
| `POST /api/projects` | `POST /projects` | `desc` → `description`; new `template_id` and `visibility` fields; returns `201`. |
| `DELETE /api/projects/:id` | `DELETE /projects/{projectId}` | New `cascade` parameter; see [project deletion](#project-deletion). |
| `GET /api/users/current` | `GET /users/me` | `org` → `organization_id`; supports a `fields` parameter. |
| `GET /api/search` | `GET /search/tasks` | Tasks only; the `type` parameter is gone; filter by `project_id`. |
| `POST /api/webhooks` | `POST /webhooks` | New `secret` and `active` fields; expanded event list; returns `201`. |
| `GET /api/webhooks` | *No v2 equivalent in the specification* | No list-webhooks operation is defined in the v2 spec — see [removed and folded endpoints](#removed-and-folded-endpoints). |
| `DELETE /api/webhooks/:id` | `DELETE /webhooks/{webhookId}` | Path parameter renamed; returns `204 No Content`. |
| — | `GET /projects/{projectId}`, `PUT /projects/{projectId}` | **New in v2**: retrieve and update a single project (v1 only supported create, list, and delete). |
| — | `POST /auth/login`, `POST /auth/refresh` | **New in v2**: token-based authentication. |

## Field renames

Rename these fields in every request body and every place you read responses:

| Object | v1 field | v2 field |
|--------|----------|----------|
| Task | `desc` | `description` |
| Task | `deadline` (Unix timestamp) | `due_date` (ISO 8601) |
| Task | `assigned_to` | `assignee_id` |
| Task | `project` | `project_id` |
| User | `org` | `organization_id` |
| Assign request body | `user_id` | `assignee_id` |
| List filters | `project` (query parameter) | `project_id` |

## Timestamps

v1 documented "all timestamps are Unix." v2 standardizes on **ISO 8601** date-time strings (for example, `2024-03-15T00:00:00Z`). This affects `deadline` → `due_date` above, task history entries, and project timestamps.

**Known exceptions.** Two response fields still return Unix timestamps in v2: `projects.created` and `users.last_login`. This inconsistency is recorded in the discrepancy report. Parse these two fields defensively (numeric epoch versus ISO string) until the platform standardizes them.

When sending `due_date`, supply a full ISO 8601 value with a UTC offset; values without an offset are interpreted as UTC.

## Pagination

v1 list endpoints used `limit`/`offset`. v2 uses `page`/`per_page` and returns a `meta` object with pagination information:

```
v1: GET /api/tasks?limit=50&offset=100
v2: GET /api/v2/tasks?page=3&per_page=50
```

- Defaults per the specification: `page` starts at 1, `per_page` defaults to **25** with a **maximum of 200**. If you requested large pages in v1, split them into multiple v2 requests.
- **Projects are now paginated.** v1 returned all projects in a single response, and the current v2 documentation still claims the projects endpoint is unpaginated — the specification defines `page`/`per_page` for it, and the specification wins. If your integration assumed "one response contains every project," rework it to follow the `meta` object.

## Errors

v1 returned a flat body:

```json
{ "error": "message" }
```

v2 returns a structured envelope:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "..."
  }
}
```

Migrate error handling to read `error.code` and `error.message`. **Caution:** some older v2 endpoints may still return the flat legacy format; the platform is standardizing across routes. Handle both shapes during your migration window:

```python
def error_code(body):
    err = body.get("error", {})
    return err["code"] if isinstance(err, dict) else err
```

New status codes to handle that v1 did not document: `409 Conflict` (stale concurrent task update — re-fetch and retry), `422 Unprocessable Entity` (validation failures, such as a title longer than 200 characters), and `429 Too Many Requests` (rate limiting).

## Assigning a task

The assign endpoint changed both verb and body:

```
v1: PUT /api/tasks/:id/assign    body: {"user_id": 93}
v2: POST /api/v2/tasks/{taskId}/assign    body: {"assignee_id": 93}
```

Behavior notes:

- Assigning a new assignee **replaces** any existing assignment.
- By default the assignee is notified; pass `"notify": false` to suppress the notification.
- To unassign, send `"assignee_id": null` via the task update endpoint (`PUT /tasks/{taskId}`), which v2 documents as the unassignment path.

## Removed and folded endpoints

Two v1 endpoints have **no v2 equivalent in the specification**, and two were folded into other operations:

- **`POST /api/tasks/:id/comments`** — v2 reads comments via `GET /tasks/{taskId}?include=comments`, but the specification defines no operation to create a comment. If your integration posts comments, contact TaskFlow support for the supported v2 path before migrating; do not assume the v1 endpoint keeps working under the v2 base URL.
- **`GET /api/webhooks`** — v2 lets you create (`POST /webhooks`) and delete (`DELETE /webhooks/{webhookId}`) webhooks, but defines no list operation. Track your webhook IDs on your side.
- **`GET /api/tasks/:id/comments`** and **`GET /api/tasks/:id/history`** — replaced by the `include` query parameter on `GET /tasks/{taskId}`. Multiple expansions can be combined: `?include=comments,history`.

## Search

v1 search spanned tasks and projects:

```
v1: GET /api/search?q=launch&type=task
v2: GET /api/v2/search/tasks?q=launch&project_id=12
```

- The `type` parameter is gone — v2 search is task-scoped. If you searched projects, use the project list endpoint with its filters instead.
- `project_id` narrows search to a single project.
- Update the path (`/search/tasks` under the v2 base URL).

## Project deletion

v1 deleted all tasks in a project implicitly. v2 keeps that default but makes it explicit and controllable:

- `DELETE /projects/{projectId}` cascades by default (`cascade=true`): all tasks in the project are deleted. Observable behavior matches v1, but now each deleted task also emits a `task.deleted` webhook event.
- Pass `?cascade=false` to require an empty project; deleting a project that still contains tasks then returns `409 Conflict`.

## Filters, sorting, and expanded fields

New capabilities with no v1 counterpart — optional to adopt, useful to know about:

- **Task filters**: filter by `label`; the `status` filter now also accepts `blocked`; sort with `sort=created_at|updated_at|priority|due_date` and `order=asc|desc`.
- **Task creation**: pass `watchers` (array of user IDs), `tags` (array of strings), and `priority: urgent` (new value; the enum is `low`, `medium`, `high`, `critical`, `urgent`).
- **Project creation**: pass `template_id` to create from a template and `visibility` to control project visibility.
- **Project filters**: `archived=true` includes archived projects; `role` filters by member role, which now includes a `viewer` role.
- **User profile**: `GET /users/me?fields=id,email,name` trims the response to selected fields.
- **Task retrieval**: `include=comments,history,attachments` expands related resources in one request, replacing v1's separate round-trips.

## Webhooks

The event catalog grew from 4 events in v1 to **10 subscribable events** in v2:

`task.created`, `task.updated`, `task.deleted`, `task.assigned`, `task.commented`, `project.created`, `project.archived`, `project.deleted`, `member.added`, `member.removed`

Additional v2 webhook changes:

- Registration (`POST /webhooks`) accepts an optional `secret` for HMAC payload verification — verify signatures server-side before trusting payloads.
- An `active` flag (default `true`) lets you pause a subscription without deleting it.
- **Undocumented events exist**: production emits `task.due_date_approaching` and `task.overdue`, which are not in the `events` enum. Do not build critical logic on them until they are added to the specification. See the webhook event catalog for full details, including delivery-retry semantics (failed deliveries are retried 3 times, then marked permanently failed).

## Rate limits

| Tier | v1 | v2 |
|------|----|----|
| Free | 60 requests/minute | 100 requests/minute |
| Paid | 60 requests/minute | 1000 requests/minute |

On limit, v2 returns `429 Too Many Requests`; read the `X-RateLimit-Remaining` response header to throttle proactively. Most integrations migrated from 60/min to 100/min need no changes, but paid-tier integrations that previously self-throttled can safely raise their throughput.

## Validation strictness

v2 enforces limits that v1 left undefined. If your integration creates tasks, check:

- `title` is required and limited to **200 characters** (a 201-character title returns `422`).
- Enum values are validated — an invalid `status`, `priority`, or unknown `assignee_id` returns `422`.
- Concurrent update conflicts return `409`; re-fetch the task and retry rather than blindly resending the update.

## Before/after examples

### List tasks (pagination and filters)

```bash
# v1
curl -s -H "X-API-Key: $TASKFLOW_KEY" \
  "https://api.taskflow.io/api/tasks?status=open&project=12&limit=50&offset=0"

# v2
curl -s --fail \
  --header "Authorization: Bearer $TASKFLOW_TOKEN" \
  --url "https://api.taskflow.io/api/v2/tasks?status=open&project_id=12&page=1&per_page=50"
```

### Create a task

```bash
# v1
curl -s -X POST -H "X-API-Key: $TASKFLOW_KEY" -H "Content-Type: application/json" \
  -d '{"title":"Ship release","desc":"Final checks","project":12,"assigned_to":93,"deadline":1710460800}' \
  "https://api.taskflow.io/api/tasks"

# v2
curl -s --fail -X POST \
  --header "Authorization: Bearer $TASKFLOW_TOKEN" \
  --header "Content-Type: application/json" \
  --data '{"title":"Ship release","description":"Final checks","project_id":12,"assignee_id":93,"due_date":"2024-03-15T00:00:00Z","watchers":[57]}' \
  --url "https://api.taskflow.io/api/v2/tasks"
```

### Assign a task

```bash
# v1
curl -s -X PUT -H "X-API-Key: $TASKFLOW_KEY" -H "Content-Type: application/json" \
  -d '{"user_id":93}' \
  "https://api.taskflow.io/api/tasks/1050/assign"

# v2
curl -s --fail -X POST \
  --header "Authorization: Bearer $TASKFLOW_TOKEN" \
  --header "Content-Type: application/json" \
  --data '{"assignee_id":93,"notify":true}' \
  --url "https://api.taskflow.io/api/v2/tasks/1050/assign"
```

### Read comments and history in one call

```bash
# v1 (two round-trips)
curl -s -H "X-API-Key: $TASKFLOW_KEY" "https://api.taskflow.io/api/tasks/1042/comments"
curl -s -H "X-API-Key: $TASKFLOW_KEY" "https://api.taskflow.io/api/tasks/1042/history"

# v2 (one round-trip)
curl -s --fail \
  --header "Authorization: Bearer $TASKFLOW_TOKEN" \
  --url "https://api.taskflow.io/api/v2/tasks/1042?include=comments,history"
```

### Register a webhook

```bash
# v1
curl -s -X POST -H "X-API-Key: $TASKFLOW_KEY" -H "Content-Type: application/json" \
  -d '{"url":"https://example.com/hook","events":["task.created","task.updated"]}' \
  "https://api.taskflow.io/api/webhooks"

# v2
curl -s --fail -X POST \
  --header "Authorization: Bearer $TASKFLOW_TOKEN" \
  --header "Content-Type: application/json" \
  --data '{"url":"https://example.com/hook","events":["task.created","task.updated","project.deleted"],"secret":"<hmac-secret>","active":true}' \
  --url "https://api.taskflow.io/api/v2/webhooks"
```

## Migration checklist

1. Point your client at `https://api.taskflow.io/api/v2` and switch to `Authorization: Bearer`.
2. Rename fields in request bodies and response handling (`desc`, `deadline`, `assigned_to`, `project`, `org`, `user_id`).
3. Convert all outgoing timestamps to ISO 8601 and switch timestamp parsing to accept ISO strings — with a numeric fallback for `projects.created` and `users.last_login`.
4. Replace `limit`/`offset` with `page`/`per_page` and honor the `meta` object; rework any "projects come back in one response" assumptions.
5. Update error handling to the structured envelope, tolerating the legacy flat format; handle `409`, `422`, and `429`.
6. Change the assign call from `PUT` to `POST` and rename `user_id` to `assignee_id`.
7. Replace comment/history list calls with `?include=`; find an alternative for posting comments (no documented v2 endpoint).
8. Migrate search calls to `/search/tasks` and remove `type=project` usage.
9. Update webhook registration: expand the event list, add HMAC verification if you adopt a `secret`, and stop listing webhooks via `GET /api/webhooks` (track IDs yourself).
10. Load-test against the new rate limits if you are on a paid tier.
11. Add strict-input handling for the 200-character title limit and enum validation (`422` responses).
12. Verify project-deletion behavior against your expectations, including the extra `task.deleted` events emitted by the default cascade.

## Support

For migration questions, v1 retirement schedules for your tenant, or the undocumented comment-creation endpoint, contact `support@taskflow.io`.

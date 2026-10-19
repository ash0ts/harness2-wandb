# TaskFlow API v2 Reference

**API version:** 2.0.1
**Last reviewed:** 2026-10-19
**Base URL:** `https://api.taskflow.io/api/v2`

Use this documentation to integrate with the TaskFlow REST API. Every operation below conforms to the [TaskFlow API Documentation Style Guide](taskflow_api_style_guide.md); parameters, enums, defaults, and status codes are reconciled against the OpenAPI 3.0 specification (v2.0.1) extracted from the codebase.

## Endpoint index

### Open endpoints

* [Authenticate a user](#authenticate-a-user) : `POST /auth/login`
* [Refresh an access token](#refresh-an-access-token) : `POST /auth/refresh`

### Endpoints that require authentication

#### Task related

* [List tasks](#list-tasks) : `GET /tasks`
* [Create a task](#create-a-task) : `POST /tasks`
* [Retrieve a task](#retrieve-a-task) : `GET /tasks/{taskId}`
* [Update a task](#update-a-task) : `PUT /tasks/{taskId}`
* [Delete a task](#delete-a-task) : `DELETE /tasks/{taskId}`
* [Assign a task](#assign-a-task) : `POST /tasks/{taskId}/assign`

#### Project related

* [List projects](#list-projects) : `GET /projects`
* [Create a project](#create-a-project) : `POST /projects`
* [Retrieve a project](#retrieve-a-project) : `GET /projects/{projectId}`
* [Update a project](#update-a-project) : `PUT /projects/{projectId}`
* [Delete a project](#delete-a-project) : `DELETE /projects/{projectId}`

#### User related

* [Retrieve the current user](#retrieve-the-current-user) : `GET /users/me`

#### Webhook related

* [Create a webhook](#create-a-webhook) : `POST /webhooks`
* [Delete a webhook](#delete-a-webhook) : `DELETE /webhooks/{webhookId}`

#### Search related

* [Search tasks](#search-tasks) : `GET /search/tasks`

---

## Authentication

TaskFlow v2 supports two credential types. Include either one in the `Authorization` header of every request to a protected endpoint:

```plaintext
Authorization: Bearer <your_api_token>
```

1. **Static API tokens.** Generate a long-lived API token from the TaskFlow dashboard under **Settings > API Keys**. Use these for server-to-server integrations that do not act on behalf of an individual user.
2. **Login token pairs.** Exchange a user's credentials for an access token and a refresh token with [`POST /auth/login`](#authenticate-a-user). Access tokens are short-lived; renew them with [`POST /auth/refresh`](#refresh-an-access-token). Use these for integrations that act on behalf of a user.

If credentials are missing, malformed, or expired, the API returns `401 Unauthorized` in the [standard error format](#errors).

### Authenticate a user

Exchanges an email address and password for an access token and refresh token.

```plaintext
POST /api/v2/auth/login
```

Supported attributes:

| Attribute    | Type   | Required | Description                                                                 |
|--------------|--------|----------|-----------------------------------------------------------------------------|
| `email`      | string | Yes      | The user's email address.                                                   |
| `password`   | string | Yes      | The user's password.                                                        |
| `mfa_code`   | string | Conditional | Time-based one-time code. Required if MFA is enabled on the account.     |

If successful, returns `200 OK` with a token pair:

```json
{
  "access_token": "at_9f2c...",
  "refresh_token": "rt_1a8e...",
  "token_type": "Bearer",
  "expires_in": 3600
}
```

**Errors**

| Status code | Meaning              | Common causes                                                        |
|-------------|----------------------|----------------------------------------------------------------------|
| `401`       | Invalid credentials  | Unknown email, wrong password.                                       |
| `403`       | MFA required         | MFA is enabled on the account and `mfa_code` was absent or invalid.  |

### Refresh an access token

Exchanges a valid refresh token for a new token pair.

```plaintext
POST /api/v2/auth/refresh
```

Supported attributes:

| Attribute       | Type   | Required | Description                          |
|-----------------|--------|----------|--------------------------------------|
| `refresh_token` | string | Yes      | The refresh token issued by login.   |

If successful, returns `200 OK` and a new token pair (same shape as the login response).

**Errors**

| Status code | Meaning                             | Common causes                                    |
|-------------|-------------------------------------|--------------------------------------------------|
| `401`       | Refresh token expired or revoked    | Token older than its validity window, or rotated/revoked by logout. |

---

## Tasks

Use this API to create, retrieve, update, and delete tasks, the primary work units in TaskFlow.

### List tasks

Lists all tasks the authenticated user can access, with filtering, sorting, and pagination.

```plaintext
GET /api/v2/tasks
```

Supported attributes:

| Attribute    | Type    | Required | Description                                                                        |
|--------------|---------|----------|------------------------------------------------------------------------------------|
| `status`     | string  | No       | Filter by task status. One of `open`, `in_progress`, `done`, `archived`, `blocked`.|
| `project_id` | integer | No       | Only return tasks belonging to this project.                                       |
| `page`       | integer | No       | Page number to return. Default is `1`.                                             |
| `per_page`   | integer | No       | Number of results per page. Default is `25`. Maximum is `200`.                     |
| `sort`       | string  | No       | Sort field. One of `created_at`, `updated_at`, `priority`, `due_date`.             |
| `order`      | string  | No       | Sort direction. One of `asc`, `desc`.                                              |
| `label`      | string  | No       | Filter tasks by label name.                                                        |

If successful, returns `200 OK` and a paginated envelope with the following response attributes:

| Attribute           | Type          | Description                              |
|---------------------|---------------|------------------------------------------|
| `data`              | object array  | The page of task objects.                |
| `data[].id`         | integer       | Task ID.                                 |
| `data[].title`      | string        | Task title.                              |
| `data[].status`     | string        | Task status.                             |
| `data[].priority`   | string        | Task priority.                           |
| `meta.current_page` | integer       | Page number returned.                    |
| `meta.total_pages`  | integer       | Total number of pages available.         |
| `meta.total_count`  | integer       | Total number of matching tasks.          |

Example request:

```bash
curl --header "Authorization: Bearer <your_api_token>" \
  --url "https://api.taskflow.io/api/v2/tasks?status=open&page=1"
```

Example response:

```json
{
  "data": [
    {
      "id": 1042,
      "title": "Fix login bug",
      "description": "Users are unable to login with SSO",
      "status": "open",
      "priority": "high",
      "assignee_id": 57,
      "project_id": 12,
      "created_at": "2024-03-01T14:22:00Z",
      "updated_at": "2024-03-01T14:22:00Z",
      "due_date": "2024-03-15T00:00:00Z"
    }
  ],
  "meta": {
    "current_page": 1,
    "total_pages": 5,
    "total_count": 98
  }
}
```

**Errors**

| Status code | Meaning              | Common causes                          |
|-------------|----------------------|----------------------------------------|
| `401`       | Unauthorized         | Missing or invalid token.              |
| `429`       | Too Many Requests    | Rate limit exceeded. See [Rate limiting](#rate-limiting). |

### Create a task

Creates a task. Only `title` is required; all other attributes are optional.

```plaintext
POST /api/v2/tasks
```

Supported attributes:

| Attribute      | Type          | Required | Description                                                                     |
|----------------|---------------|----------|---------------------------------------------------------------------------------|
| `title`        | string        | Yes      | Title of the task. Maximum 200 characters.                                      |
| `description`  | string        | No       | Longer description. Supports Markdown formatting.                               |
| `status`       | string        | No       | Initial status. Default is `open`.                                              |
| `priority`     | string        | No       | One of `low`, `medium`, `high`, `critical`, `urgent`.                           |
| `assignee_id`  | integer       | No       | ID of the user to assign the task to.                                           |
| `project_id`   | integer       | No       | Project to create the task in. If omitted, the task is placed in the user's default project *(behavior pending engineering verification)*. |
| `due_date`     | string        | No       | Due date. ISO 8601 date-time (`2024-04-01T00:00:00Z`).                          |
| `tags`         | string array  | No       | Tags to apply to the task.                                                      |
| `watchers`     | integer array | No       | User IDs to add as watchers.                                                    |

If successful, returns `201 Created` with the full task object.

Example request:

```bash
curl --request POST \
  --header "Authorization: Bearer <your_api_token>" \
  --header "Content-Type: application/json" \
  --data '{
    "title": "Design new landing page",
    "description": "Create mockups for the Q2 marketing campaign",
    "priority": "high",
    "project_id": 12,
    "due_date": "2024-04-01T00:00:00Z",
    "tags": ["design", "marketing"],
    "watchers": [57, 82]
  }' \
  --url "https://api.taskflow.io/api/v2/tasks"
```

**Errors**

| Status code | Meaning            | Common causes                                             |
|-------------|--------------------|-----------------------------------------------------------|
| `400`       | Bad request        | Malformed JSON body.                                      |
| `401`       | Unauthorized       | Missing or invalid token.                                 |
| `422`       | Validation error   | `title` missing or longer than 200 characters; invalid enum value. |

### Retrieve a task

Retrieves a single task by its ID.

```plaintext
GET /api/v2/tasks/{taskId}
```

Supported attributes:

| Attribute  | Type    | Required | Description                                                                                      |
|------------|---------|----------|--------------------------------------------------------------------------------------------------|
| `taskId`   | integer | Yes      | ID of the task to retrieve.                                                                      |
| `include`  | string  | No       | Expand related resources in the response. One of `comments`, `history`, `attachments`.           |

If successful, returns `200 OK` with the full task object (same schema as the list endpoint), plus the requested related resources when `include` is supplied.

Example request:

```bash
curl --header "Authorization: Bearer <your_api_token>" \
  --url "https://api.taskflow.io/api/v2/tasks/1042?include=comments"
```

**Errors**

| Status code | Meaning       | Common causes                                        |
|-------------|---------------|------------------------------------------------------|
| `401`       | Unauthorized  | Missing or invalid token.                            |
| `404`       | Task not found| Task does not exist or you do not have access to it. |

### Update a task

Updates a task. Partial updates are supported: send only the fields you want to change; omitted fields remain unchanged. `id` and `created_at` cannot be modified.

```plaintext
PUT /api/v2/tasks/{taskId}
```

Supported attributes:

| Attribute      | Type          | Required | Description                                                        |
|----------------|---------------|----------|--------------------------------------------------------------------|
| `taskId`       | integer       | Yes      | ID of the task to update.                                          |
| `title`        | string        | No       | New title.                                                         |
| `description`  | string        | No       | New description.                                                   |
| `status`       | string        | No       | Must be a valid status string.                                     |
| `priority`     | string        | No       | One of `low`, `medium`, `high`, `critical`, `urgent`.              |
| `assignee_id`  | integer       | No       | Assignee's user ID. Set to `null` to unassign.                     |
| `project_id`   | integer       | No       | Move the task to this project.                                     |
| `due_date`     | string        | No       | ISO 8601 date-time. Set to `null` to clear.                        |
| `tags`         | string array  | No       | Replace the task's tags.                                           |
| `watchers`     | integer array | No       | Replace the task's watcher user IDs.                               |

If successful, returns `200 OK` and the updated task object.

Example request:

```bash
curl --request PUT \
  --header "Authorization: Bearer <your_api_token>" \
  --header "Content-Type: application/json" \
  --data '{"status": "in_progress", "assignee_id": 57}' \
  --url "https://api.taskflow.io/api/v2/tasks/1042"
```

Example response:

```json
{
  "id": 1042,
  "title": "Fix login bug",
  "status": "in_progress",
  "priority": "high",
  "assignee_id": 57,
  "project_id": 12,
  "updated_at": "2024-03-02T10:15:00Z"
}
```

**Errors**

| Status code | Meaning      | Common causes                                                            |
|-------------|--------------|--------------------------------------------------------------------------|
| `401`       | Unauthorized | Missing or invalid token.                                                |
| `404`       | Task not found | Task does not exist or you do not have access to it.                   |
| `409`       | Conflict     | Concurrent modification detected: the task changed since you last read it. Re-fetch the task, re-apply your change, and retry. |

### Delete a task

Deletes a task permanently. This action cannot be undone.

```plaintext
DELETE /api/v2/tasks/{taskId}
```

Supported attributes:

| Attribute | Type    | Required | Description               |
|-----------|---------|----------|---------------------------|
| `taskId`  | integer | Yes      | ID of the task to delete. |

Permissions: you must be an admin or owner on the project that contains the task. Regular members can delete only tasks they created.

If successful, returns `204 No Content`. The response body is empty.

Example request:

```bash
curl --request DELETE \
  --header "Authorization: Bearer <your_api_token>" \
  --url "https://api.taskflow.io/api/v2/tasks/1042"
```

**Errors**

| Status code | Meaning         | Common causes                                   |
|-------------|-----------------|-------------------------------------------------|
| `403`       | Forbidden       | Insufficient permissions on the project.        |
| `404`       | Task not found  | Task does not exist or you do not have access.  |

### Assign a task

Assigns a task to an assignee. Use this operation instead of `PUT /tasks/{taskId}` when the only change is assignment, because it additionally triggers assignment notifications.

Reassignment behavior: assigning a task that already has an assignee replaces the existing assignee. The previous assignee loses assignment notifications; watchers are unaffected.

```plaintext
POST /api/v2/tasks/{taskId}/assign
```

Supported attributes:

| Attribute     | Type    | Required | Description                                                      |
|---------------|---------|----------|------------------------------------------------------------------|
| `taskId`      | integer | Yes      | ID of the task to assign.                                        |
| `assignee_id` | integer | Yes      | ID of the user to assign the task to.                            |
| `notify`      | boolean | No       | Whether to send a notification to the assignee. Default is `true`. |

If successful, returns `200 OK` with the assigned task object.

Example request:

```bash
curl --request POST \
  --header "Authorization: Bearer <your_api_token>" \
  --header "Content-Type: application/json" \
  --data '{"assignee_id": 93, "notify": true}' \
  --url "https://api.taskflow.io/api/v2/tasks/1050/assign"
```

**Errors**

| Status code | Meaning              | Common causes                                       |
|-------------|----------------------|-----------------------------------------------------|
| `401`       | Unauthorized         | Missing or invalid token.                           |
| `404`       | Task or user not found | `taskId` or `assignee_id` does not exist, or you lack access. |

---

## Projects

Use this API to manage projects, the containers that organize tasks and members.

### List projects

Lists all projects the authenticated user has access to. Projects are returned in alphabetical order by name *(ordering pending engineering verification)*.

```plaintext
GET /api/v2/projects
```

Supported attributes:

| Attribute  | Type    | Required | Description                                                            |
|------------|---------|----------|------------------------------------------------------------------------|
| `archived` | boolean | No       | If `true`, include archived projects. Default is `false`.              |
| `role`     | string  | No       | Filter by your role in the project. One of `owner`, `admin`, `member`, `viewer`. |
| `page`     | integer | No       | Page number to return.                                                 |
| `per_page` | integer | No       | Number of results per page.                                            |

If successful, returns `200 OK` with a `projects` array.

Example request:

```bash
curl --header "Authorization: Bearer <your_api_token>" \
  --url "https://api.taskflow.io/api/v2/projects?archived=false"
```

Example response:

```json
{
  "projects": [
    {
      "id": 12,
      "name": "Website Redesign",
      "description": "Q2 2024 website overhaul",
      "owner_id": 57,
      "created": 1709312400,
      "member_count": 8,
      "task_count": 45,
      "archived": false
    }
  ]
}
```

> [!warning]
> The `created` field is a Unix timestamp, not ISO 8601. This is a known legacy deviation; the TaskFlow timestamp standard is ISO 8601 UTC (see [Timestamps](#timestamps-and-formats)) and this field is scheduled to migrate in a future release.

**Errors**

| Status code | Meaning      | Common causes             |
|-------------|--------------|---------------------------|
| `401`       | Unauthorized | Missing or invalid token. |

### Create a project

Creates a project. `name` is required and must be unique within your organization, with a maximum of 100 characters.

```plaintext
POST /api/v2/projects
```

Supported attributes:

| Attribute      | Type   | Required | Description                                                                       |
|----------------|--------|----------|-----------------------------------------------------------------------------------|
| `name`         | string | Yes      | Project name. Maximum 100 characters; unique within the organization.             |
| `description`  | string | No       | Optional project description.                                                     |
| `template_id`  | integer| No       | Create from an existing project template.                                         |
| `visibility`   | string | No       | One of `private`, `internal`, `public`. Default is `private`.                     |

If successful, returns `201 Created` with the new project object (same fields as the list endpoint; `member_count` is `1`, `task_count` is `0`, and `archived` is `false` for new projects).

Example request:

```bash
curl --request POST \
  --header "Authorization: Bearer <your_api_token>" \
  --header "Content-Type: application/json" \
  --data '{"name": "My New Project", "visibility": "internal"}' \
  --url "https://api.taskflow.io/api/v2/projects"
```

**Errors**

| Status code | Meaning            | Common causes                                        |
|-------------|--------------------|------------------------------------------------------|
| `400`       | Bad request        | Malformed JSON body.                                 |
| `401`       | Unauthorized       | Missing or invalid token.                            |
| `422`       | Validation error   | `name` missing, longer than 100 characters, or not unique. |

### Retrieve a project

Retrieves a single project by its ID.

```plaintext
GET /api/v2/projects/{projectId}
```

Supported attributes:

| Attribute   | Type    | Required | Description                    |
|-------------|---------|----------|--------------------------------|
| `projectId` | integer | Yes      | ID of the project to retrieve. |

If successful, returns `200 OK` with the project object.

Example request:

```bash
curl --header "Authorization: Bearer <your_api_token>" \
  --url "https://api.taskflow.io/api/v2/projects/12"
```

**Errors**

| Status code | Meaning          | Common causes                                      |
|-------------|------------------|----------------------------------------------------|
| `401`       | Unauthorized     | Missing or invalid token.                          |
| `404`       | Project not found| Project does not exist or you do not have access.  |

### Update a project

Updates a project. Partial updates are supported: send only the fields you want to change.

```plaintext
PUT /api/v2/projects/{projectId}
```

Supported attributes:

| Attribute     | Type    | Required | Description                                        |
|---------------|---------|----------|----------------------------------------------------|
| `projectId`   | integer | Yes      | ID of the project to update.                       |
| `name`        | string  | No       | New project name.                                  |
| `description` | string  | No       | New project description.                           |
| `archived`    | boolean | No       | Set to `true` to archive the project.              |
| `visibility`  | string  | No       | One of `private`, `internal`, `public`.            |

If successful, returns `200 OK` and the updated project object.

Example request:

```bash
curl --request PUT \
  --header "Authorization: Bearer <your_api_token>" \
  --header "Content-Type: application/json" \
  --data '{"description": "Q2 2024 website overhaul - updated scope"}' \
  --url "https://api.taskflow.io/api/v2/projects/12"
```

Example response:

```json
{
  "id": 12,
  "name": "Website Redesign",
  "description": "Q2 2024 website overhaul - updated scope",
  "owner_id": 57,
  "archived": false
}
```

**Errors**

| Status code | Meaning           | Common causes                                     |
|-------------|-------------------|---------------------------------------------------|
| `401`       | Unauthorized      | Missing or invalid token.                         |
| `404`       | Project not found | Project does not exist or you do not have access. |

### Delete a project

Deletes a project permanently. This action cannot be undone.

```plaintext
DELETE /api/v2/projects/{projectId}
```

Supported attributes:

| Attribute   | Type    | Required | Description                                                        |
|-------------|---------|----------|--------------------------------------------------------------------|
| `projectId` | integer | Yes      | ID of the project to delete.                                       |
| `cascade`   | boolean | No       | If `true`, deletes all tasks within the project. Default is `true`.|

> [!warning]
> By default, deleting a project also deletes every task in it. Pass `cascade=false` to keep the tasks.

If successful, returns `204 No Content`. The response body is empty.

Example request:

```bash
curl --request DELETE \
  --header "Authorization: Bearer <your_api_token>" \
  --url "https://api.taskflow.io/api/v2/projects/12?cascade=false"
```

**Errors**

| Status code | Meaning           | Common causes                                     |
|-------------|-------------------|---------------------------------------------------|
| `401`       | Unauthorized      | Missing or invalid token.                         |
| `403`       | Forbidden         | Insufficient permissions on the project.          |
| `404`       | Project not found | Project does not exist or you do not have access. |

---

## Users

Use this API to retrieve information about the authenticated user.

### Retrieve the current user

Retrieves the profile of the currently authenticated user.

```plaintext
GET /api/v2/users/me
```

Supported attributes:

| Attribute | Type   | Required | Description                                                        |
|-----------|--------|----------|--------------------------------------------------------------------|
| `fields`  | string | No       | Comma-separated list of fields to include in the response.         |

If successful, returns `200 OK` with the user object.

Example request:

```bash
curl --header "Authorization: Bearer <your_api_token>" \
  --url "https://api.taskflow.io/api/v2/users/me?fields=id,email,name,timezone"
```

Example response:

```json
{
  "user": {
    "id": 57,
    "email": "jane@example.com",
    "name": "Jane Smith",
    "role": "admin",
    "organization_id": 5,
    "timezone": "America/New_York",
    "created_at": "2023-01-15T09:00:00Z",
    "last_login": 1709398800
  }
}
```

> [!warning]
> `created_at` is ISO 8601 but `last_login` is a Unix timestamp. This legacy deviation will be addressed in API v3.

**Errors**

| Status code | Meaning      | Common causes             |
|-------------|--------------|---------------------------|
| `401`       | Unauthorized | Missing or invalid token. |

---

## Webhooks

Use this API to manage webhooks, which deliver real-time event notifications to your integration endpoints. The complete list of event types, trigger conditions, payload examples, and provenance is maintained in the [Webhook Event Catalog](webhook_event_catalog.html).

### Create a webhook

Creates a webhook that delivers the selected event types to your webhook URL.

```plaintext
POST /api/v2/webhooks
```

Supported attributes:

| Attribute | Type          | Required | Description                                                                 |
|-----------|---------------|----------|-----------------------------------------------------------------------------|
| `url`     | string (URI)  | Yes      | The webhook URL that receives event deliveries.                             |
| `events`  | string array  | Yes      | Event types to subscribe to. One or more of: `task.created`, `task.updated`, `task.deleted`, `task.assigned`, `task.commented`, `project.created`, `project.archived`, `project.deleted`, `member.added`, `member.removed`. |
| `secret`  | string        | No       | HMAC secret for payload verification.                                       |
| `active`  | boolean       | No       | Whether the webhook delivers events. Default is `true`.                     |

If successful, returns `201 Created` with the created webhook object.

Example request:

```bash
curl --request POST \
  --header "Authorization: Bearer <your_api_token>" \
  --header "Content-Type: application/json" \
  --data '{
    "url": "https://integrations.acme.corp/taskflow/events",
    "events": ["task.created", "task.updated", "task.assigned"],
    "secret": "<your_hmac_secret>",
    "active": true
  }' \
  --url "https://api.taskflow.io/api/v2/webhooks"
```

**Delivery guarantees.** Failed deliveries are retried up to 3 times; if every attempt fails, the delivery is marked `failed` (evidence: production delivery log, March 2024 export — delivery `d-90008`, HTTP 502, `retry_attempts: 3`, `final_status: failed`). Verify payload authenticity by computing an HMAC with your `secret` over the raw request body.

**Errors**

| Status code | Meaning          | Common causes                              |
|-------------|------------------|--------------------------------------------|
| `400`       | Bad request      | Malformed JSON body.                       |
| `401`       | Unauthorized     | Missing or invalid token.                  |
| `422`       | Validation error | `url` is not a valid URI, or `events` contains an unknown event type. |

### Delete a webhook

Deletes a webhook permanently. The webhook URL stops receiving deliveries immediately; this action cannot be undone.

```plaintext
DELETE /api/v2/webhooks/{webhookId}
```

Supported attributes:

| Attribute   | Type    | Required | Description                    |
|-------------|---------|----------|--------------------------------|
| `webhookId` | integer | Yes      | ID of the webhook to delete.   |

If successful, returns `204 No Content`. The response body is empty.

Example request:

```bash
curl --request DELETE \
  --header "Authorization: Bearer <your_api_token>" \
  --url "https://api.taskflow.io/api/v2/webhooks/301"
```

**Errors**

| Status code | Meaning           | Common causes                                     |
|-------------|-------------------|---------------------------------------------------|
| `401`       | Unauthorized      | Missing or invalid token.                         |
| `404`       | Webhook not found | Webhook does not exist or you do not own it.      |

---

## Search

Use this API to find tasks by text and attribute filters.

### Search tasks

Searches tasks with a full-text query, optionally scoped by project, status, and date range.

```plaintext
GET /api/v2/search/tasks
```

Supported attributes:

| Attribute    | Type    | Required | Description                                     |
|--------------|---------|----------|-------------------------------------------------|
| `q`          | string  | Yes      | Full-text search query.                         |
| `project_id` | integer | No       | Restrict results to this project.               |
| `status`     | string  | No       | Restrict results to this task status.           |
| `date_from`  | string  | No       | Lower bound (inclusive) on task dates. `YYYY-MM-DD`. |
| `date_to`    | string  | No       | Upper bound (inclusive) on task dates. `YYYY-MM-DD`. |

If successful, returns `200 OK` with matching task objects.

Example request:

```bash
curl --header "Authorization: Bearer <your_api_token>" \
  --url "https://api.taskflow.io/api/v2/search/tasks?q=landing%20page&project_id=12"
```

> [!note]
> Advanced query syntax (operators, field-scoped terms) is supported only partially and is not yet fully specified. Contact API support before relying on operator behavior.

**Errors**

| Status code | Meaning      | Common causes                                        |
|-------------|--------------|------------------------------------------------------|
| `400`       | Bad request  | `q` missing or invalid filter combination.           |
| `401`       | Unauthorized | Missing or invalid token.                            |

---

## Rate limiting

The API enforces rate limits to ensure fair usage:

- **Free tier:** 100 requests per minute.
- **Paid plans:** 1000 requests per minute.

When you exceed the limit, the API returns `429 Too Many Requests`. Check the `X-RateLimit-Remaining` response header to see how many requests you have left, and back off before retrying.

## Pagination

List endpoints support pagination via `page` and `per_page` query parameters. The response `meta` object contains pagination info (`current_page`, `total_pages`, `total_count`). The projects endpoint also accepts `page` and `per_page` per the OpenAPI specification; the earlier statement that it does not paginate was an error and has been removed.

## Errors

The API uses standard HTTP status codes. The canonical v2 error body is:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Title is required",
    "details": []
  }
}
```

Some older endpoints may still return the legacy format:

```json
{
  "error": "not_found",
  "message": "Task not found"
}
```

Treat the canonical envelope as the contract for new integrations. The legacy format is transitional and will be removed.

## Timestamps and formats

- All request and response timestamps are ISO 8601 UTC with a trailing `Z` (for example, `2024-03-01T14:22:00Z`), except the known legacy Unix-timestamp fields: project `created` and user `last_login`. Both are flagged at their endpoints and are scheduled to migrate.
- IDs are integers. Enum values are lowercase snake_case.

## Changelog summary (docs reconciliation)

This reference was reconciled against the OpenAPI v2.0.1 spec and the documentation audit tracker. Highlights of what changed versus the previous ad-hoc docs:

- Added the missing auth flow (`POST /auth/login`, `POST /auth/refresh`) with MFA handling.
- Corrected task `title` maximum length (255 → 200), `per_page` defaults (20/100 → 25/200), and the `status`/`priority`/`sort` enums (`blocked`, `urgent`, `due_date` added).
- Documented `label` filtering and the `include` expansion parameter on task retrieval.
- Documented `watchers`, `409 Conflict` on task update, and the assign operation with `notify`.
- Documented project pagination (previously wrongly denied), `template_id`, `visibility`, project update and delete operations, and the `cascade` delete behavior.
- Documented the `fields` parameter on `GET /users/me`.
- Added full webhook and search reference sections, retry guarantees, and HMAC verification guidance.

*For questions or support, email api-support@taskflow.io.*

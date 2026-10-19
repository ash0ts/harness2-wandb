---
title: TaskFlow API v2 Reference
description: REST API to manage TaskFlow tasks, projects, users, search, and webhooks.
version: 2.0.1
last_updated: 2026-10-19
---

# TaskFlow API v2 Reference

Use this API to manage [tasks](#tasks), [projects](#projects), [users](#users), [search](#search), and [webhooks](#webhooks) in your TaskFlow workspace.

- **Base URL:** `https://api.taskflow.io/api/v2`
- **API version:** 2.0.1
- **Specification:** `taskflow_openapi_v2_extract.yaml` (OpenAPI 3.0.3) is the source of truth for paths, parameters, enums, and response codes. This page is generated from and reconciled against it.

## Authentication

Two credential styles are supported. Unless stated otherwise, every endpoint in this reference requires authentication; the only open endpoint is `POST /auth/login`.

**1. API keys.** Generate a key from the TaskFlow dashboard under **Settings > API Keys** and send it in the `Authorization` header:

```plaintext
Authorization: Bearer <your_api_key>
```

**2. Access tokens.** Exchange an email and password for an access token and refresh token pair with `POST /auth/login`, then send the access token the same way:

```plaintext
Authorization: Bearer <your_access_token>
```

Access tokens expire. When they do, call `POST /auth/refresh` with the refresh token to obtain a new pair. Do not call it "token rotation" in your own notes — the supported flow is: refresh the access token with the refresh token.

## Pagination

Paginated list endpoints accept `page` and `per_page` and return pagination facts in a `meta` object:

```json
{
  "data": [],
  "meta": { "current_page": 1, "total_pages": 5, "total_count": 98 }
}
```

Default `per_page` is `25`; maximum is `200` unless a lower maximum is stated on the endpoint.

## Rate limiting

- Free tier: 100 requests per minute.
- Paid plans: 1000 requests per minute.

When you exceed the limit the API returns `429 Too Many Requests`. Check the `X-RateLimit-Remaining` response header to see how many requests you have left before the window resets.

## Errors

The canonical error body is:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Title is required",
    "details": []
  }
}
```

A few older endpoints still return the deprecated flat shape below. Treat it as legacy; it will be removed in API v3.

```json
{
  "error": "not_found",
  "message": "Task not found"
}
```

## Timestamps

All date-time values are ISO 8601 with a UTC offset (for example, `2024-03-01T14:22:00Z`). Two legacy fields still return Unix timestamps — `projects[].created` and `users[].last_login` — and both migrate to ISO 8601 in API v3. Do not build new integrations that assume Unix timestamps for these fields.

---

## Open endpoints

Open endpoints require no authentication.

- [Authenticate a user](#authenticate-a-user): `POST /api/v2/auth/login`

## Endpoints that require authentication

All other endpoints require a valid API key or access token in the `Authorization` header.

### Tasks

- [List all tasks](#list-all-tasks): `GET /api/v2/tasks`
- [Create a task](#create-a-task): `POST /api/v2/tasks`
- [Retrieve a task](#retrieve-a-task): `GET /api/v2/tasks/{taskId}`
- [Update a task](#update-a-task): `PUT /api/v2/tasks/{taskId}`
- [Delete a task](#delete-a-task): `DELETE /api/v2/tasks/{taskId}`
- [Assign a task](#assign-a-task): `POST /api/v2/tasks/{taskId}/assign`

### Projects

- [List all projects](#list-all-projects): `GET /api/v2/projects`
- [Create a project](#create-a-project): `POST /api/v2/projects`
- [Retrieve a project](#retrieve-a-project): `GET /api/v2/projects/{projectId}`
- [Update a project](#update-a-project): `PUT /api/v2/projects/{projectId}`
- [Delete a project](#delete-a-project): `DELETE /api/v2/projects/{projectId}`

### Users

- [Retrieve the current user](#retrieve-the-current-user): `GET /api/v2/users/me`

### Search

- [Search for tasks](#search-for-tasks): `GET /api/v2/search/tasks`

### Webhooks

- [Create a webhook](#create-a-webhook): `POST /api/v2/webhooks`
- [Delete a webhook](#delete-a-webhook): `DELETE /api/v2/webhooks/{webhookId}`

---

## Authentication

### Authenticate a user

Exchanges an email address and password for an access token and refresh token pair. If multi-factor authentication (MFA) is enabled on the account, the first request returns `403 MFA required` and you must retry with an `mfa_code`.

```plaintext
POST /api/v2/auth/login
```

**URL**: `/api/v2/auth/login`

**Method**: `POST`

**Auth required**: NO

**Permissions required**: None

**Request attributes**

| Attribute    | Type   | Required    | Description                                        |
|--------------|--------|-------------|-----------------------------------------------------|
| `email`      | string | Yes         | The account email address.                          |
| `password`   | string | Yes         | The account password.                               |
| `mfa_code`   | string | Conditional | The current MFA code. Required if MFA is enabled on the account. |

**Example request**

```shell
curl --request POST \
  --url "https://api.taskflow.io/api/v2/auth/login" \
  --header "Content-Type: application/json" \
  --data '{
    "email": "jane@example.com",
    "password": "<your_password>",
    "mfa_code": "123456"
  }'
```

**Success response**

**Code**: `200 OK`

| Attribute       | Type    | Description                                  |
|-----------------|---------|----------------------------------------------|
| `access_token`  | string  | Bearer token for API requests.                |
| `refresh_token` | string  | Token used to refresh the access token.       |
| `token_type`    | string  | Always `Bearer`.                              |
| `expires_in`    | integer | Access token lifetime in seconds.             |

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "rt_9f2c7e6a1b",
  "token_type": "Bearer",
  "expires_in": 3600
}
```

**Error responses**

| Status code | Meaning                                             |
|-------------|-----------------------------------------------------|
| 400         | Bad request - malformed body or missing attributes.  |
| 401         | Invalid credentials.                                 |
| 403         | MFA required. Retry with `mfa_code`.                 |

---

### Refresh an access token

Exchanges a valid refresh token for a new access token and refresh token pair. Use this when an access token expires; do not re-send credentials.

```plaintext
POST /api/v2/auth/refresh
```

**URL**: `/api/v2/auth/refresh`

**Method**: `POST`

**Auth required**: NO (the refresh token itself authenticates the call)

**Permissions required**: None

**Request attributes**

| Attribute       | Type   | Required | Description                          |
|-----------------|--------|----------|--------------------------------------|
| `refresh_token` | string | Yes      | The refresh token from `POST /auth/login` or a previous refresh. |

**Example request**

```shell
curl --request POST \
  --url "https://api.taskflow.io/api/v2/auth/refresh" \
  --header "Content-Type: application/json" \
  --data '{
    "refresh_token": "rt_9f2c7e6a1b"
  }'
```

**Success response**

**Code**: `200 OK`

Returns a new token pair with the same attributes as [Authenticate a user](#authenticate-a-user):

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "rt_5d81ba4c02",
  "token_type": "Bearer",
  "expires_in": 3600
}
```

**Error responses**

| Status code | Meaning                                                        |
|-------------|----------------------------------------------------------------|
| 400         | Bad request - `refresh_token` missing or malformed.             |
| 401         | Refresh token expired or revoked. Authenticate again with `POST /auth/login`. |

---

## Tasks

Use this API to create, retrieve, update, and delete tasks, and to manage task assignment.

### List all tasks

Lists all tasks the authenticated user can access. Filter, sort, and paginate the results with query parameters. Rate limits apply; see [Rate limiting](#rate-limiting).

```plaintext
GET /api/v2/tasks
```

**URL**: `/api/v2/tasks`

**Method**: `GET`

**Auth required**: YES

**Permissions required**: None (results are scoped to projects you can access)

**Query parameters**

| Attribute   | Type    | Required | Description                                                                        |
|-------------|---------|----------|-------------------------------------------------------------------------------------|
| `label`     | string  | No       | Filter tasks by label name.                                                         |
| `order`     | string  | No       | Sort direction. One of `asc`, `desc`.                                               |
| `page`      | integer | No       | Page number. Default is `1`.                                                        |
| `per_page`  | integer | No       | Results per page. Default is `25`. Maximum is `200`.                                 |
| `project_id`| integer | No       | Only return tasks belonging to this project.                                        |
| `sort`      | string  | No       | Sort field. One of `created_at`, `updated_at`, `priority`, `due_date`.               |
| `status`    | string  | No       | Filter by task status. One of `open`, `in_progress`, `done`, `archived`, `blocked`.  |

**Example request**

```shell
curl --header "Authorization: Bearer <your_access_token>" \
  --url "https://api.taskflow.io/api/v2/tasks?status=open&page=1&per_page=25"
```

**Success response**

**Code**: `200 OK`

| Attribute               | Type          | Description                        |
|-------------------------|---------------|------------------------------------|
| `data[]`                | object array  | Task objects.                      |
| `data[].id`             | integer       | Task ID.                           |
| `data[].title`          | string        | Task title.                        |
| `data[].description`    | string        | Task description.                  |
| `data[].status`         | string        | Task status.                       |
| `data[].priority`       | string        | Task priority.                     |
| `data[].assignee_id`    | integer       | Assigned user ID, or `null`.       |
| `data[].project_id`     | integer       | Containing project ID.             |
| `data[].created_at`     | string        | ISO 8601 creation timestamp.       |
| `data[].updated_at`     | string        | ISO 8601 last-update timestamp.    |
| `data[].due_date`       | string        | ISO 8601 due date, or `null`.      |
| `meta.current_page`     | integer       | Current page number.               |
| `meta.total_pages`      | integer       | Total number of pages.             |
| `meta.total_count`      | integer       | Total number of matching tasks.    |

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

**Error responses**

| Status code | Meaning                                       |
|-------------|-----------------------------------------------|
| 400         | Bad request - invalid filter or sort value.    |
| 401         | Authentication failed.                         |
| 429         | Too Many Requests - rate limit exceeded.       |

---

### Create a task

Creates a new task. You must provide a `title`. If `project_id` is omitted, the task is created in the user's default project.

```plaintext
POST /api/v2/tasks
```

**URL**: `/api/v2/tasks`

**Method**: `POST`

**Auth required**: YES

**Permissions required**: Member (or higher) on the target project

**Request attributes**

| Attribute     | Type            | Required | Description                                                              |
|---------------|-----------------|----------|---------------------------------------------------------------------------|
| `title`       | string          | Yes      | Task title. Maximum 200 characters.                                       |
| `assignee_id` | integer         | No       | User ID to assign the task to.                                            |
| `description` | string          | No       | Longer description. Supports Markdown formatting.                         |
| `due_date`    | string          | No       | Due date in ISO 8601 date-time format.                                    |
| `priority`    | string          | No       | One of `low`, `medium`, `high`, `critical`, `urgent`. Default is `medium`. |
| `project_id`  | integer         | No       | Project to create the task in.                                            |
| `status`      | string          | No       | Initial status. Default is `open`.                                        |
| `tags`        | string array    | No       | Tags to apply to the task.                                                |
| `watchers`    | integer array   | No       | User IDs to add as watchers.                                              |

**Example request**

```shell
curl --request POST \
  --url "https://api.taskflow.io/api/v2/tasks" \
  --header "Authorization: Bearer <your_access_token>" \
  --header "Content-Type: application/json" \
  --data '{
    "title": "Design new landing page",
    "description": "Create mockups for the Q2 marketing campaign",
    "priority": "high",
    "project_id": 12,
    "due_date": "2024-04-01T00:00:00Z",
    "tags": ["design", "marketing"],
    "watchers": [57, 82]
  }'
```

**Success response**

**Code**: `201 Created`

Returns the full task object with the same attributes as the task objects in [List all tasks](#list-all-tasks).

```json
{
  "id": 1107,
  "title": "Design new landing page",
  "description": "Create mockups for the Q2 marketing campaign",
  "status": "open",
  "priority": "high",
  "assignee_id": null,
  "project_id": 12,
  "created_at": "2024-03-05T10:00:00Z",
  "updated_at": "2024-03-05T10:00:00Z",
  "due_date": "2024-04-01T00:00:00Z"
}
```

**Error responses**

| Status code | Meaning                                              |
|-------------|------------------------------------------------------|
| 400         | Bad request - missing required fields or invalid values. |
| 401         | Authentication failed.                                |
| 422         | Validation error (for example, title too long).       |

**Notes**

- Edge cases worth testing in your integration: a title of exactly 200 characters is accepted; 201 characters returns `422`; an unknown `assignee_id` returns `422`; `due_date` values without a UTC offset are interpreted as UTC.

---

### Retrieve a task

Retrieves a single task by ID. Pass the task ID as the `taskId` path parameter. Optionally expand related resources with the `include` query parameter.

```plaintext
GET /api/v2/tasks/{taskId}
```

**URL**: `/api/v2/tasks/{taskId}`

**Method**: `GET`

**Auth required**: YES

**Permissions required**: Read access to the project containing the task

**Request attributes**

| Attribute | Type    | Required | Description                                                                     |
|-----------|---------|----------|----------------------------------------------------------------------------------|
| `taskId`  | integer | Yes      | The ID of the task to retrieve.                                                   |
| `include` | string  | No       | Comma-separated list of related resources to expand. One or more of `comments`, `history`, `attachments`. |

**Example request**

```shell
curl --header "Authorization: Bearer <your_access_token>" \
  --url "https://api.taskflow.io/api/v2/tasks/1042?include=comments,history"
```

**Success response**

**Code**: `200 OK`

Returns the task object with the same attributes as the task objects in [List all tasks](#list-all-tasks). When `include` is supplied, each requested collection is added as a top-level key (`comments`, `history`, `attachments`).

```json
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
  "due_date": "2024-03-15T00:00:00Z",
  "comments": [
    {
      "id": 5567,
      "author_id": 82,
      "body": "Reproduced on staging.",
      "created_at": "2024-03-02T09:17:00Z"
    }
  ]
}
```

**Error responses**

| Status code | Meaning                                            |
|-------------|-----------------------------------------------------|
| 401         | Authentication failed.                              |
| 404         | Task not found, or you do not have access to it.    |

---

### Update a task

Updates a task. Partial updates are supported: send only the fields you want to change. Fields not included in the request body remain unchanged.

```plaintext
PUT /api/v2/tasks/{taskId}
```

**URL**: `/api/v2/tasks/{taskId}`

**Method**: `PUT`

**Auth required**: YES

**Permissions required**: Write access to the project containing the task

**Request attributes**

| Attribute     | Type          | Required | Description                                                       |
|---------------|---------------|----------|--------------------------------------------------------------------|
| `taskId`      | integer       | Yes      | The ID of the task to update.                                      |
| `assignee_id` | integer       | No       | Assigned user ID. Set to `null` to unassign.                       |
| `description` | string        | No       | Task description.                                                  |
| `due_date`    | string        | No       | Due date in ISO 8601 format, or `null` to clear.                   |
| `priority`    | string        | No       | One of `low`, `medium`, `high`, `critical`, `urgent`.               |
| `project_id`  | integer       | No       | Project to move the task to.                                       |
| `status`      | string        | No       | Must be a valid status string.                                     |
| `tags`        | string array  | No       | Replaces the tag list.                                             |
| `title`       | string        | No       | Task title. Maximum 200 characters.                                |
| `watchers`    | integer array | No       | Replaces the watcher list.                                         |

The `id` and `created_at` fields cannot be modified.

**Example request**

```shell
curl --request PUT \
  --url "https://api.taskflow.io/api/v2/tasks/1042" \
  --header "Authorization: Bearer <your_access_token>" \
  --header "Content-Type: application/json" \
  --data '{
    "status": "in_progress",
    "assignee_id": 57
  }'
```

**Success response**

**Code**: `200 OK`

Returns the updated task object:

```json
{
  "id": 1042,
  "title": "Fix login bug",
  "description": "Users are unable to login with SSO",
  "status": "in_progress",
  "priority": "high",
  "assignee_id": 57,
  "project_id": 12,
  "created_at": "2024-03-01T14:22:00Z",
  "updated_at": "2024-03-05T09:41:12Z",
  "due_date": "2024-03-15T00:00:00Z"
}
```

**Error responses**

| Status code | Meaning                                          |
|-------------|---------------------------------------------------|
| 400         | Bad request - malformed body or invalid values.   |
| 401         | Authentication failed.                            |
| 403         | You do not have write access to this task.        |
| 404         | Task not found.                                   |
| 409         | Conflict - concurrent modification detected. Re-fetch the task and retry. |

---

### Delete a task

Deletes a task permanently. This action cannot be undone.

```plaintext
DELETE /api/v2/tasks/{taskId}
```

**URL**: `/api/v2/tasks/{taskId}`

**Method**: `DELETE`

**Auth required**: YES

**Permissions required**: Admin or owner on the project containing the task. Regular members can delete only tasks they created.

**Request attributes**

| Attribute | Type    | Required | Description                  |
|-----------|---------|----------|-------------------------------|
| `taskId`  | integer | Yes      | The ID of the task to delete. |

**Example request**

```shell
curl --request DELETE \
  --header "Authorization: Bearer <your_access_token>" \
  --url "https://api.taskflow.io/api/v2/tasks/1042"
```

**Success response**

**Code**: `204 No Content`

The response body is empty.

**Error responses**

| Status code | Meaning                                                     |
|-------------|--------------------------------------------------------------|
| 401         | Authentication failed.                                       |
| 403         | Forbidden - insufficient permissions.                        |
| 404         | Task not found.                                              |

---

### Assign a task

Assigns a task to a user. Assigning a new assignee replaces any existing assignment. By default the assignee receives a notification; set `notify` to `false` to suppress it. This endpoint also works as an unassignment helper: pass `assignee_id: null` with `notify` to control notification behavior on unassignment.

```plaintext
POST /api/v2/tasks/{taskId}/assign
```

**URL**: `/api/v2/tasks/{taskId}/assign`

**Method**: `POST`

**Auth required**: YES

**Permissions required**: Write access to the project containing the task

**Request attributes**

| Attribute     | Type    | Required | Description                                          |
|---------------|---------|----------|-------------------------------------------------------|
| `taskId`      | integer | Yes      | The ID of the task to assign.                         |
| `assignee_id` | integer | Yes      | The user ID of the assignee.                          |
| `notify`      | boolean | No       | Whether to send a notification to the assignee. Default is `true`. |

**Example request**

```shell
curl --request POST \
  --url "https://api.taskflow.io/api/v2/tasks/1050/assign" \
  --header "Authorization: Bearer <your_access_token>" \
  --header "Content-Type: application/json" \
  --data '{
    "assignee_id": 93,
    "notify": true
  }'
```

**Success response**

**Code**: `200 OK`

Returns the updated task object:

```json
{
  "id": 1050,
  "title": "Prepare Q2 report",
  "status": "open",
  "priority": "medium",
  "assignee_id": 93,
  "project_id": 12,
  "created_at": "2024-03-04T09:00:00Z",
  "updated_at": "2024-03-05T10:02:00Z",
  "due_date": null
}
```

**Error responses**

| Status code | Meaning                                                     |
|-------------|--------------------------------------------------------------|
| 400         | Bad request - `assignee_id` missing or malformed.            |
| 401         | Authentication failed.                                       |
| 404         | Task or user not found. An invalid assignee is reported here. |

---

## Projects

Use this API to create, retrieve, update, and delete projects.

### List all projects

Lists all projects the authenticated user has access to. Results are paginated; pass `page` and `per_page` to control paging. Projects are returned in alphabetical order by name.

```plaintext
GET /api/v2/projects
```

**URL**: `/api/v2/projects`

**Method**: `GET`

**Auth required**: YES

**Permissions required**: None (results are scoped to your organization and memberships)

**Query parameters**

| Attribute  | Type    | Required | Description                                                        |
|------------|---------|----------|---------------------------------------------------------------------|
| `archived` | boolean | No       | If `true`, include archived projects. Default is `false`.           |
| `page`     | integer | No       | Page number. Default is `1`.                                        |
| `per_page` | integer | No       | Results per page. Default is `25`. Maximum is `200`.                |
| `role`     | string  | No       | Filter by your role in the project. One of `owner`, `admin`, `member`, `viewer`. |

**Example request**

```shell
curl --header "Authorization: Bearer <your_access_token>" \
  --url "https://api.taskflow.io/api/v2/projects?role=owner&page=1"
```

**Success response**

**Code**: `200 OK`

| Attribute               | Type         | Description                                          |
|-------------------------|--------------|-------------------------------------------------------|
| `data[]`                | object array | Project objects.                                       |
| `data[].id`             | integer      | Project ID.                                            |
| `data[].name`           | string       | Project name.                                          |
| `data[].description`    | string       | Project description, or `null`.                        |
| `data[].owner_id`       | integer      | User ID of the project owner.                          |
| `data[].created`        | integer      | Legacy Unix timestamp. Migrates to ISO 8601 `created_at` in API v3. |
| `data[].member_count`   | integer      | Number of project members.                             |
| `data[].task_count`     | integer      | Number of tasks in the project.                        |
| `data[].archived`       | boolean      | Whether the project is archived.                       |
| `meta.current_page`     | integer      | Current page number.                                   |
| `meta.total_pages`      | integer      | Total number of pages.                                 |
| `meta.total_count`      | integer      | Total number of matching projects.                     |

```json
{
  "data": [
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
  ],
  "meta": {
    "current_page": 1,
    "total_pages": 1,
    "total_count": 3
  }
}
```

**Notes**

- This endpoint previously returned a bare `projects` array with no pagination. It now follows the standard `data` + `meta` list envelope. If you parsed the bare array, update your client.
- `created` is a legacy Unix timestamp; see [Timestamps](#timestamps).

**Error responses**

| Status code | Meaning                                    |
|-------------|---------------------------------------------|
| 400         | Bad request - invalid filter value.         |
| 401         | Authentication failed.                      |

---

### Create a project

Creates a new project. The `name` is required and must be unique within your organization.

```plaintext
POST /api/v2/projects
```

**URL**: `/api/v2/projects`

**Method**: `POST`

**Auth required**: YES

**Permissions required**: None (the creating user becomes the owner)

**Request attributes**

| Attribute     | Type    | Required | Description                                                            |
|---------------|---------|----------|-------------------------------------------------------------------------|
| `name`        | string  | Yes      | Project name. Maximum 100 characters. Unique within the organization.   |
| `description` | string  | No       | Optional project description.                                           |
| `template_id` | integer | No       | Create the project from an existing project template.                   |
| `visibility`  | string  | No       | One of `private`, `internal`, `public`. Default is `private`.           |

**Example request**

```shell
curl --request POST \
  --url "https://api.taskflow.io/api/v2/projects" \
  --header "Authorization: Bearer <your_access_token>" \
  --header "Content-Type: application/json" \
  --data '{
    "name": "My New Project",
    "description": "Optional project description",
    "visibility": "internal"
  }'
```

**Success response**

**Code**: `201 Created`

| Attribute     | Type    | Description                            |
|---------------|---------|-----------------------------------------|
| `id`          | integer | Auto-generated project ID.              |
| `name`        | string  | The project name.                       |
| `description` | string  | Project description, or `null`.         |
| `owner_id`    | integer | ID of the creating user.                |
| `created`     | integer | Legacy Unix timestamp.                  |
| `member_count`| integer | Always `1` for new projects.            |
| `task_count`  | integer | Always `0` for new projects.            |
| `archived`    | boolean | Always `false` for new projects.        |
| `visibility`  | string  | The project visibility setting.         |

```json
{
  "id": 16,
  "name": "My New Project",
  "description": "Optional project description",
  "owner_id": 57,
  "created": 1709654400,
  "member_count": 1,
  "task_count": 0,
  "archived": false,
  "visibility": "internal"
}
```

**Error responses**

| Status code | Meaning                                                     |
|-------------|--------------------------------------------------------------|
| 400         | Bad request - malformed body.                                 |
| 401         | Authentication failed.                                        |
| 422         | Validation error - name missing, too long, or not unique.     |

---

### Retrieve a project

Retrieves a single project by ID.

```plaintext
GET /api/v2/projects/{projectId}
```

**URL**: `/api/v2/projects/{projectId}`

**Method**: `GET`

**Auth required**: YES

**Permissions required**: Read access to the project

**Request attributes**

| Attribute   | Type    | Required | Description                     |
|-------------|---------|----------|----------------------------------|
| `projectId` | integer | Yes      | The ID of the project to retrieve. |

**Example request**

```shell
curl --header "Authorization: Bearer <your_access_token>" \
  --url "https://api.taskflow.io/api/v2/projects/12"
```

**Success response**

**Code**: `200 OK`

Returns a project object with the same attributes as the project objects in [List all projects](#list-all-projects), plus `visibility`:

```json
{
  "id": 12,
  "name": "Website Redesign",
  "description": "Q2 2024 website overhaul",
  "owner_id": 57,
  "created": 1709312400,
  "member_count": 8,
  "task_count": 45,
  "archived": false,
  "visibility": "internal"
}
```

**Error responses**

| Status code | Meaning                                         |
|-------------|--------------------------------------------------|
| 401         | Authentication failed.                           |
| 404         | Project not found, or you do not have access to it. |

---

### Update a project

Updates a project. Partial updates are supported: send only the fields you want to change.

```plaintext
PUT /api/v2/projects/{projectId}
```

**URL**: `/api/v2/projects/{projectId}`

**Method**: `PUT`

**Auth required**: YES

**Permissions required**: Admin or owner on the project

**Request attributes**

| Attribute     | Type    | Required | Description                                              |
|---------------|---------|----------|-----------------------------------------------------------|
| `projectId`   | integer | Yes      | The ID of the project to update.                           |
| `archived`    | boolean | No       | Set to `true` to archive the project, `false` to restore.  |
| `description` | string  | No       | Project description.                                       |
| `name`        | string  | No       | Project name. Maximum 100 characters.                      |
| `visibility`  | string  | No       | One of `private`, `internal`, `public`.                    |

**Example request**

```shell
curl --request PUT \
  --url "https://api.taskflow.io/api/v2/projects/12" \
  --header "Authorization: Bearer <your_access_token>" \
  --header "Content-Type: application/json" \
  --data '{
    "name": "Website Redesign - Phase 2",
    "archived": false
  }'
```

**Success response**

**Code**: `200 OK`

Returns the updated project object:

```json
{
  "id": 12,
  "name": "Website Redesign - Phase 2",
  "description": "Q2 2024 website overhaul",
  "owner_id": 57,
  "created": 1709312400,
  "member_count": 8,
  "task_count": 45,
  "archived": false,
  "visibility": "internal"
}
```

**Error responses**

| Status code | Meaning                                                     |
|-------------|--------------------------------------------------------------|
| 400         | Bad request - malformed body or invalid values.               |
| 401         | Authentication failed.                                        |
| 403         | Forbidden - requires admin or owner on the project.           |
| 404         | Project not found.                                            |

---

### Delete a project

Deletes a project. By default the deletion cascades: all tasks within the project are deleted as well. This action cannot be undone. Pass `cascade=false` to delete the project only when it contains no tasks.

```plaintext
DELETE /api/v2/projects/{projectId}
```

**URL**: `/api/v2/projects/{projectId}`

**Method**: `DELETE`

**Auth required**: YES

**Permissions required**: Owner on the project

**Request attributes**

| Attribute   | Type    | Required | Description                                                            |
|-------------|---------|----------|-------------------------------------------------------------------------|
| `projectId` | integer | Yes      | The ID of the project to delete.                                         |
| `cascade`   | boolean | No       | If `true`, deletes all tasks within the project. Default is `true`.      |

**Example request**

```shell
curl --request DELETE \
  --header "Authorization: Bearer <your_access_token>" \
  --url "https://api.taskflow.io/api/v2/projects/3?cascade=false"
```

**Success response**

**Code**: `204 No Content`

The response body is empty.

**Error responses**

| Status code | Meaning                                                                        |
|-------------|---------------------------------------------------------------------------------|
| 401         | Authentication failed.                                                          |
| 403         | Forbidden - requires project owner.                                             |
| 404         | Project not found.                                                              |
| 409         | Conflict - project still contains tasks and `cascade` was `false`.               |

---

## Users

Use this API to retrieve information about the authenticated user.

### Retrieve the current user

Retrieves the profile of the currently authenticated user, including basic subscription information. Use the `fields` query parameter to request only the attributes you need.

```plaintext
GET /api/v2/users/me
```

**URL**: `/api/v2/users/me`

**Method**: `GET`

**Auth required**: YES

**Permissions required**: None

**Query parameters**

| Attribute | Type   | Required | Description                                                          |
|-----------|--------|----------|-----------------------------------------------------------------------|
| `fields`  | string | No       | Comma-separated list of fields to include in the response. For example, `fields=id,email,name`. |

**Example request**

```shell
curl --header "Authorization: Bearer <your_access_token>" \
  --url "https://api.taskflow.io/api/v2/users/me?fields=id,email,name,role"
```

**Success response**

**Code**: `200 OK`

| Attribute          | Type    | Description                                                  |
|--------------------|---------|---------------------------------------------------------------|
| `user.id`          | integer | User ID.                                                       |
| `user.email`       | string  | Account email address.                                         |
| `user.name`        | string  | Display name.                                                  |
| `user.role`        | string  | Organization role.                                             |
| `user.organization_id` | integer | Organization ID.                                            |
| `user.timezone`    | string  | IANA time zone of the user profile.                            |
| `user.created_at`  | string  | ISO 8601 account creation timestamp.                           |
| `user.last_login`  | integer | Legacy Unix timestamp of the last login. Migrates to ISO 8601 in API v3. |

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

**Notes**

- When `fields` is supplied, only the requested attributes are returned. `id` is always returned.
- `last_login` is a legacy Unix timestamp; see [Timestamps](#timestamps).

**Error responses**

| Status code | Meaning               |
|-------------|------------------------|
| 401         | Authentication failed. |

---

## Search

Use this API to search for tasks.

### Search for tasks

Searches tasks by full-text query, optionally narrowed by project, status, and due-date range.

```plaintext
GET /api/v2/search/tasks
```

**URL**: `/api/v2/search/tasks`

**Method**: `GET`

**Auth required**: YES

**Permissions required**: Read access to the projects searched

**Query parameters**

| Attribute    | Type    | Required | Description                                                       |
|--------------|---------|----------|--------------------------------------------------------------------|
| `q`          | string  | Yes      | Full-text search query. Matches task titles and descriptions.      |
| `date_from`  | string  | No       | Only return tasks due on or after this date (`YYYY-MM-DD`).        |
| `date_to`    | string  | No       | Only return tasks due on or before this date (`YYYY-MM-DD`).       |
| `project_id` | integer | No       | Restrict the search to this project.                               |
| `status`     | string  | No       | Restrict the search to this task status.                           |

URL-encode the query value; use `--data-urlencode` in cURL or percent-encoding such as `%20` for spaces.

**Example request**

```shell
curl --request GET \
  --header "Authorization: Bearer <your_access_token>" \
  --data-urlencode "q=landing page" \
  --url "https://api.taskflow.io/api/v2/search/tasks?project_id=12&status=open"
```

**Success response**

**Code**: `200 OK`

Returns task objects with the same attributes as [List all tasks](#list-all-tasks), ordered by relevance:

```json
{
  "data": [
    {
      "id": 1107,
      "title": "Design new landing page",
      "description": "Create mockups for the Q2 marketing campaign",
      "status": "open",
      "priority": "high",
      "assignee_id": null,
      "project_id": 12,
      "created_at": "2024-03-05T10:00:00Z",
      "updated_at": "2024-03-05T10:00:00Z",
      "due_date": "2024-04-01T00:00:00Z"
    }
  ],
  "meta": {
    "current_page": 1,
    "total_pages": 1,
    "total_count": 1
  }
}
```

**Error responses**

| Status code | Meaning                                            |
|-------------|-----------------------------------------------------|
| 400         | Bad request - `q` missing or a date range is invalid. |
| 401         | Authentication failed.                               |

---

## Webhooks

Use this API to register and remove webhooks that push TaskFlow events to your integration endpoints. For the full list of event types, payload examples, and delivery semantics, see the [webhook event catalog](webhook_event_catalog.html).

### Create a webhook

Registers a webhook that receives the selected event types at your webhook URL. Provide an HMAC `secret` so you can verify payload signatures; `active` controls whether the webhook fires immediately after creation.

```plaintext
POST /api/v2/webhooks
```

**URL**: `/api/v2/webhooks`

**Method**: `POST`

**Auth required**: YES

**Permissions required**: Admin or owner on the workspace

**Request attributes**

| Attribute | Type         | Required | Description                                                                                          |
|-----------|--------------|----------|-------------------------------------------------------------------------------------------------------|
| `url`     | string (URI) | Yes      | The webhook URL that receives event payloads. Must be publicly reachable over HTTPS.                   |
| `events`  | string array | Yes      | Event types to subscribe to. One or more of `task.created`, `task.updated`, `task.deleted`, `task.assigned`, `task.commented`, `project.created`, `project.archived`, `project.deleted`, `member.added`, `member.removed`. |
| `secret`  | string       | No       | HMAC secret used to sign payloads for verification.                                                     |
| `active`  | boolean      | No       | Whether the webhook is active. Default is `true`.                                                       |

**Example request**

```shell
curl --request POST \
  --url "https://api.taskflow.io/api/v2/webhooks" \
  --header "Authorization: Bearer <your_access_token>" \
  --header "Content-Type: application/json" \
  --data '{
    "url": "https://integrations.acme.corp/taskflow/events",
    "events": ["task.created", "task.updated", "project.archived"],
    "secret": "<your_hmac_secret>",
    "active": true
  }'
```

**Success response**

**Code**: `201 Created`

| Attribute    | Type         | Description                                    |
|--------------|--------------|-------------------------------------------------|
| `id`         | integer      | Webhook ID.                                     |
| `url`        | string       | The webhook URL.                                |
| `events`     | string array | Subscribed event types.                         |
| `active`     | boolean      | Whether the webhook is active.                  |
| `created_at` | string       | ISO 8601 creation timestamp.                    |

```json
{
  "id": 301,
  "url": "https://integrations.acme.corp/taskflow/events",
  "events": ["task.created", "task.updated", "project.archived"],
  "active": true,
  "created_at": "2024-02-20T11:30:00Z"
}
```

**Error responses**

| Status code | Meaning                                                        |
|-------------|-----------------------------------------------------------------|
| 400         | Bad request - malformed body.                                    |
| 401         | Authentication failed.                                           |
| 422         | Validation error - `url` not reachable/valid, or unknown event type. |

**Notes**

- **Payload verification.** When a `secret` is configured, each delivery is signed with HMAC-SHA256 and the signature is sent in the `X-TaskFlow-Signature` header. Verify it before trusting a payload.
- **Retry policy.** A delivery that does not return a `2xx` status is retried up to 3 times with exponential backoff. If all attempts fail, the delivery is marked `failed` and is not retried again. Production logs confirm this behavior (a `project.deleted` delivery returned `502` and was retired after 3 attempts).
- **Event types.** The declared subscription list is the ten event types above. Two additional event types observed in production, `task.due_date_approaching` and `task.overdue`, are scheduled events not yet declared in the OpenAPI specification; see the event catalog for details.

---

### Delete a webhook

Deletes a webhook. The webhook URL stops receiving events immediately; queued deliveries for the deleted webhook are dropped.

```plaintext
DELETE /api/v2/webhooks/{webhookId}
```

**URL**: `/api/v2/webhooks/{webhookId}`

**Method**: `DELETE`

**Auth required**: YES

**Permissions required**: Admin or owner on the workspace

**Request attributes**

| Attribute   | Type    | Required | Description                    |
|-------------|---------|----------|---------------------------------|
| `webhookId` | integer | Yes      | The ID of the webhook to delete. |

**Example request**

```shell
curl --request DELETE \
  --header "Authorization: Bearer <your_access_token>" \
  --url "https://api.taskflow.io/api/v2/webhooks/301"
```

**Success response**

**Code**: `204 No Content`

The response body is empty.

**Error responses**

| Status code | Meaning                        |
|-------------|---------------------------------|
| 401         | Authentication failed.          |
| 403         | Forbidden - insufficient permissions. |
| 404         | Webhook not found.              |

---

## Support

For questions or support, email api-support@taskflow.io.

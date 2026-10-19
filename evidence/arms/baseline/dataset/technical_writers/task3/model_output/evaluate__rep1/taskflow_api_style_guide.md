# TaskFlow API Documentation Style Guide

**Version:** 1.0
**Applies to:** All TaskFlow REST API documentation (v2 and later)
**Maintained by:** Technical Writing, Developer Experience
**Based on:** The TaskFlow RESTAPIDocs endpoint template, the per-endpoint documentation audit rubric, the OpenAPI 3.0 extract for the platform API, and the REST API documentation conventions used by mature open-source projects such as GitLab.

---

## 1. Purpose and scope

This guide governs how TaskFlow writes, structures, and maintains REST API documentation. It applies to every Markdown topic published under the TaskFlow API documentation set: endpoint references, authentication guides, webhook catalogs, migration guides, and any future API version. Its goal is to make every endpoint page predictable: a developer who has read one TaskFlow endpoint should be able to read any other one without relearning the layout, the vocabulary, or the tone.

Two truths drive the rules below. First, our documentation has historically been written ad hoc by multiple engineers, and the audit trail shows the costs: inconsistent terminology, missing authentication notes, empty error tables, and endpoints that exist in the codebase but not on the page. Second, we maintain an OpenAPI 3.0 specification extracted from the codebase, and that specification is machine-checkable in a way prose is not. The specification therefore anchors the facts; the prose adds the judgment, context, and examples that a schema cannot carry.

## 2. Source-of-truth hierarchy

When sources disagree, resolve them in this order and record the decision in the discrepancy report:

1. **The OpenAPI specification.** For anything a machine can verify — paths, methods, parameter names, parameter types, enums, defaults, maximums, required flags, declared response codes — the spec wins. Example: the spec enforces `maxLength: 200` on task titles, so any document that says "max 255 characters" is wrong regardless of how long it has said so.
2. **The current endpoint documentation.** For behavioral statements the spec does not express — sort order, cascade behavior descriptions, Markdown support in a field, permission rules — the documentation is the source of truth *unless* it is contradicted by the spec or flagged in the audit tracker. If we keep a documentation-only claim, we file a ticket to backfill it into the spec.
3. **The audit tracker.** The tracker records findings a mechanical diff cannot see: description quality, terminology drift, missing authentication notes, and stale sections. Any endpoint marked `needs_rewrite` is rewritten in full, not patched. Any endpoint marked `needs_update` must resolve every note in its row.
4. **Production evidence.** For behavior observable only at runtime — webhook retry counts, observed event types — the production delivery log is admissible evidence. Document observed behavior and mark it as observed, then reconcile it with the declared spec.

Never silently reconcile. Every disagreement between sources gets a row in the discrepancy report with a resolution, even when the resolution is "docs are right, spec must change."

## 3. Page structure

Every endpoint reference follows the structure of the RESTAPIDocs template, extended with the conventions below. A complete operation entry contains, in this order:

1. **Operation title** (see §4).
2. **Method and path block** — the HTTP method followed by the full path, in a fenced `plaintext` block, with the path starting with `/`:

   ```plaintext
   DELETE /api/v2/projects/{projectId}
   ```

3. **Description** (see §5).
4. **Metadata line set** from the template: `URL`, `Method`, `Auth required`, `Permissions required`.
5. **Request attributes table** (see §6).
6. **Example request** (see §7).
7. **Success response** — the status code, a response attributes table when the body has structure, and one or more example response bodies (see §8).
8. **Error responses table** (see §9).
9. **Notes** — anything a developer must know that does not fit above: defaults with caveats, deprecation warnings, retry behavior, rate-limit implications.

Resource-level introductions (for example, the Tasks chapter) start with one sentence in this pattern: **"Use this API to {verb} {feature}."** This mirrors the introductions used by GitLab's REST API docs and gives readers an immediate purpose statement before the operations begin.

## 4. Operation titles

Start every operation title with a verb. Use the verb that matches the HTTP method, per the GitLab convention table, and use the same verb in the title and the first sentence of the description:

| HTTP method | Title verb | Example title |
|-------------|-----------|---------------|
| `GET` (collection) | List all | `List all tasks` |
| `GET` (single item) | Retrieve | `Retrieve a task` |
| `POST` | Create | `Create a task` |
| `PUT` | Update | `Update a task` |
| `DELETE` | Delete | `Delete a task` |

Do not mix verbs across a page: an endpoint titled "Update a task" must not describe itself as "modifying" the task in the body, and a project section must not drift from "create" to "add" to "register" mid-paragraph. The audit tracker flagged this exact drift repeatedly; it is the single most common quality defect in our legacy docs.

## 5. Descriptions

Write a short description for every operation. The first sentence broadly restates the title, in the present tense, third person:

- Title: `List all tasks` → "Lists all tasks the authenticated user can access."
- Title: `Delete a task` → "Deletes a task permanently."

After the first sentence, add only what changes a developer's implementation: permission requirements, defaults, side effects (such as cascade deletion), partial-update semantics, and rate-limit implications. Do not narrate the obvious ("this endpoint takes an ID and returns a task"). Do not promise roadmap behavior in operative sentences; if a change is planned (for example, Unix-to-ISO 8601 migration), state it once in a Note with the target version.

## 6. Attribute tables

Document every parameter and every structured response field in a Markdown table. Never describe parameters in loose bullets in a reference page; bullets are permitted in conceptual guides only.

**Request attribute tables** use these columns: `Attribute`, `Type`, `Required`, `Description`.

- Place path attributes first, then required attributes, then the remaining attributes alphabetically.
- Put every attribute name in backticks.
- State enums inline with backticked values: One of `open`, `in_progress`, `done`, `archived`, `blocked`.
- State defaults explicitly: "Default is `25`."
- State maximums explicitly: "Maximum is `200`."
- For conditionally required attributes, use `Conditional` in the Required column and name the condition: "Required if `include_service_accounts` is `false`."
- Use the parameter names from the OpenAPI specification exactly. If the spec says `taskId`, the docs say `taskId` — never `task_id`, `id`, or "task ID" in a parameter context.

**Response attribute tables** use these columns: `Attribute`, `Type`, `Description`, sorted alphabetically. For nested fields use dot notation: `meta.current_page`, `data[].assignee_id`.

## 7. Request examples

Every operation ships at least one request example. Use cURL as the canonical example language; supplement — never replace — with a language-specific example (such as Python) only when the endpoint has unusual semantics worth demonstrating in a richer client.

cURL conventions, adapted from GitLab's guidelines:

- Use the production host with a placeholder token: `https://api.taskflow.io/api/v2/...` and `Authorization: Bearer <your_access_token>`.
- Use long option names: `--header`, `--request`, `--data`, `--url`.
- Put the URL in `--url`, wrapped in double quotes.
- `GET` is implied; write `--request POST`, `--request PUT`, `--request DELETE` for everything else.
- Break long commands with a trailing backslash and two-space indentation.
- For JSON bodies, include `--header "Content-Type: application/json"` and pass the body in single quotes so double-quoted JSON keys survive.
- Use `--data-urlencode` when a value contains spaces, ampersands, or other reserved characters, and URL-encode reserved characters inside URLs (`%20` for spaces).
- Never use real user data, real tokens, or real customer URLs in examples.

## 8. Response examples

Every operation ships at least one example response body, even when the body is empty — in that case state it: "The response body is empty." Show the actual envelope shape. TaskFlow list responses use a `data` array plus a `meta` object; single-resource responses return the resource object directly (with the documented exception of `GET /users/me`, which wraps the payload in a `user` object). When multiple realistically different responses exist (for example, a user with and without optional profile data), show more than one example, as the RESTAPIDocs template does.

If an operation returns a non-obvious status code or empty body, say why in a Note. For every documented success response, enumerate the body fields in a response attributes table when the body is structured.

## 9. Status codes and errors

Every operation documents its error responses in a two-column table: `Status code` and `Meaning`. Include at minimum the codes the OpenAPI spec declares for the operation, plus `401 Unauthorized` when the operation requires authentication. Describe the failure from the developer's perspective ("Task not found, or you do not have access to it") rather than from the server's.

TaskFlow's canonical error body is:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Title is required",
    "details": []
  }
}
```

A small set of legacy endpoints still returns the flat shape `{"error": "not_found", "message": "..."}`. Documents must identify those endpoints and mark the flat shape as deprecated. Never present both shapes as equally valid; present the canonical envelope first and the legacy shape only as a marked exception.

## 10. Cross-cutting conventions

**Authentication.** State `Auth required: YES` or `NO` in the metadata block for every operation. Open endpoints (currently `POST /auth/login`) are listed in their own section, as the RESTAPIDocs template does. Never write "all API calls require authentication" in a guide if any open endpoint exists.

**Permissions.** Where authorization is finer-grained than authentication (for example, deleting a task requires admin or owner rights on the project, or members may delete only tasks they created), document the rule in the `Permissions required` line and elaborate in Notes.

**Pagination.** Paginated list endpoints accept `page` and `per_page`, and return pagination facts in `meta`. If an endpoint does not yet support pagination but the spec declares `page` and `per_page`, the spec is followed; if neither supports it, say so explicitly rather than leaving the reader to guess.

**Rate limiting.** Every reference set carries one global rate-limit section stating the limits per plan, the `429 Too Many Requests` response, and the `X-RateLimit-Remaining` header. Endpoint pages link to it when rate limits materially affect the operation.

**Timestamps.** ISO 8601 with a UTC offset (`2024-03-01T14:22:00Z`) is the documentation standard for all date-time values. Known Unix-timestamp holdovers (`projects.created`, `users.last_login`) must be called out inline as legacy fields with a note that they migrate to ISO 8601 in API v3.

**Webhooks.** Webhook reference pages must document: the full declared event type list, the payload verification secret (HMAC), delivery retry behavior, and a link to the webhook event catalog. Use the term **webhook URL** for the destination field; do not alternate with "callback URL" or "destination." Event names are lowercase dotted identifiers (`task.created`) and appear in backticks.

## 11. Terminology and voice

Use one term for one concept, everywhere:

| Use | Do not use |
|-----|------------|
| endpoint | route, URL (when referring to the API surface) |
| retrieve (single item), list (collection) | fetch, get, show |
| parameter | argument, arg, field (for request inputs) |
| request body | payload |
| access token | JWT (in prose) |
| refresh the access token | rotate the token |
| assignee | user (when naming the assignee specifically) |
| delete | remove (for DELETE operations) |
| query parameter | query arg, filter (when naming parameters) |

Write in the second person ("you can filter results"), active voice, present tense. Avoid first person except in Notes that explicitly speak for the TaskFlow team. Avoid hedging ("might possibly", "should generally") — verify the behavior and state it.

## 12. History, deprecation, and change management

When an attribute or operation is deprecated, do three things: add a history note at the top of the section naming the version and replacement; mark the attribute inline in its table row ("Deprecated in v2.1. Use `widget_id` instead."); and list the operation on the deprecations page. Never delete a documented v2 operation from the reference without a deprecation trail — integrations in production depend on the page's stability as much as the API's.

## 13. Review checklist

Before publishing or merging an endpoint page, verify:

- [ ] Title starts with the method-appropriate verb; description's first sentence restates it.
- [ ] Method/path block present, path starts with `/`, placeholders match the OpenAPI parameter names.
- [ ] `URL`, `Method`, `Auth required`, `Permissions required` all present.
- [ ] Every spec parameter documented, including enums, defaults, and maximums; table order: path, required, alphabetical.
- [ ] At least one cURL request example and one JSON response example.
- [ ] Error table covers every spec-declared error code plus `401` where applicable.
- [ ] No terminology from the prohibited list; audit-tracker notes from the previous review all resolved.
- [ ] OpenAPI spec updated in the same change set whenever parameters, enums, or responses change, so the docs and spec cannot drift again.

A page that passes this checklist satisfies every column of the documentation audit tracker: description quality, auth info, parameter completeness, examples on both sides, error codes, voice consistency, terminology, and formatting standard.

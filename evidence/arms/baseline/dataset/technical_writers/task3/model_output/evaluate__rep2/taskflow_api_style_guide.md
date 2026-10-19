# TaskFlow API Documentation Style Guide

**Applies to:** all TaskFlow API documentation (v2 and later), including REST reference pages, the webhook event catalog, and migration guides.
**Status:** Approved for publication. Governs every endpoint page produced by the Technical Writing team.
**Sources of grounding:** The in-house `restapidocs_combined_template.md` endpoint template, the [GitLab REST API documentation guidelines](../task_folder/references/gitlab_api_doc_styleguide.md) snapshot, and the findings recorded in `doc_audit_tracker.csv`.

---

## 1. Purpose and scope

This guide exists because TaskFlow's API documentation was historically written ad-hoc by individual developers. The 2025 documentation audit (`doc_audit_tracker.csv`) found that of 17 audited endpoint rows, 6 required full rewrites, 7 required updates, and only 4 were acceptable as-is. The most common failure modes were not factual errors but *inconsistency*: two reviewers described the same object with different words ("user" vs. "assignee"), three different formatting styles were used for the same kind of information (tables, bullets, prose), and six pages drifted between active and passive voice.

This guide eliminates those failures by fixing a single canonical pattern for every endpoint page. If a rule here conflicts with an individual author's preference, this guide wins. If a rule here conflicts with verified product behavior, product behavior wins and this guide must be amended.

## 2. Source-of-truth hierarchy

TaskFlow documents one API from several sources. When sources disagree, resolve them in this order:

1. **The OpenAPI specification** (`taskflow_openapi_v2_extract.yaml`) is the machine-readable contract extracted from the codebase. Parameter names, types, enums, defaults, requiredness, and declared status codes come from the spec.
2. **Verified production behavior** (delivery logs, trace captures) is authoritative for behavior the spec does not model, such as webhook retry policy. Never publish production-derived behavior without labeling its provenance.
3. **Prose documentation** is the presentation layer. It may add context, examples, and warnings, but it must never contradict the spec. Any docs-only claim (for example, "tasks are created in the user's default project when `project_id` is omitted") that is not backed by the spec must be tagged for engineering verification before publication.
4. **The audit tracker** is a quality-control instrument, not a source of truth. Its notes reveal gaps that a plain diff of spec versus docs cannot (terminology drift, missing examples, inconsistent formatting). Every row must reach `acceptable` status before the docs it describes may ship.

Rule of engagement: **docs may say more than the spec, never less and never differently.** If the spec knows something the docs do not, that is a documentation bug. If the docs know something the spec does not, file a spec-backfill ticket (see §15).

## 3. Page structure

Every endpoint page follows the combined template's skeleton, hardened with GitLab-style attribute tables. The mandatory order is:

1. **H2 resource section** (for example, `## Tasks`) opening with the sentence pattern `Use this API to <verb> <feature>…`, per the GitLab introduction convention. Example: *"Use this API to create, read, update, and delete tasks, the primary work units in TaskFlow."*
2. **H3 operation section** per HTTP method. Each operation contains, in order:
   - Operation title (see §4).
   - A method-and-path block in a fenced `plaintext` code block, path always beginning with `/`:
     ```plaintext
     POST /api/v2/tasks
     ```
   - One-sentence description whose first sentence broadly restates the title (GitLab convention: "List all tasks" → "Lists all tasks.").
   - Request attributes table (path parameters first, then query/body; required attributes first, then alphabetical). See §5.
   - Success statement and response attributes, if the response body carries meaningful fields.
   - Example request (cURL).
   - Example response (JSON).
   - Error table.
3. **Cross-cutting H2 sections** collected once, not repeated per endpoint: Authentication, Rate limiting, Pagination, Errors, Timestamps and formats, Webhook delivery guarantees.

The page index must list every operation in the form used by the combined template — `* [Operation title](anchor) : `METHOD /path`` — grouped into *Open endpoints* (no auth) and *Endpoints that require authentication*, so readers can orient before diving in.

## 4. Operation titles

Start every operation title with the canonical verb for its HTTP method (adapted from GitLab):

| HTTP method | Verb | Alternatives (use sparingly) | Example |
|---|---|---|---|
| `GET` (multiple) | List | | **List tasks** |
| `GET` (single) | Retrieve | Get | **Retrieve a task** |
| `POST` | Create | Add | **Create a task** |
| `PUT` | Update | Replace | **Update a task** |
| `DELETE` | Delete | | **Delete a task** |

Forbidden constructions: headings phrased as sentences ("Updating a task — PUT …"), gerunds ("Getting your profile"), and synonyms that drift by page. The audit tracker flagged exactly this drift: *modify* in one body, *update* in a title; *remove* in a description, *delete* everywhere else. One verb per method, always.

## 5. Attribute tables

All request and response parameters are documented in Markdown tables, never in bullet lists. The audit tracker shows bullet-list parameters correlate with the lowest completeness scores (40–55%).

**Request columns:** `Attribute | Type | Required | Description`.
**Response columns:** `Attribute | Type | Description`.

Rules:

- Attribute names in backticks. Types use the OpenAPI vocabulary: `string`, `integer`, `boolean`, `string array`, `integer array`, `object`.
- Path attributes first, then required attributes, then the rest alphabetically.
- `Required` takes `Yes`, `No`, or `Conditional`. For conditional attributes, define the attribute first, then state the dependency in the canonical form: *"Required if `mfa_enabled` is `true`."*
- Every attribute gets a meaningful description. Vague stubs ("The ID", "The name") are audit failures. State defaults as *"Default is `25`."* State enums exhaustively with backticked values, even when a companion table exists.
- Nullable attributes: state what `null` means — *"Set to `null` to unassign the task."*
- Nested objects use dot notation in response tables: `meta.current_page`, `data[].assignee_id`.

## 6. Examples

Every operation carries **at least one cURL example and one JSON response example.** The audit tracker's six `needs_rewrite` rows all lacked one or both.

cURL conventions (GitLab-derived, adapted to TaskFlow):

- Base URL is always `https://api.taskflow.io/api/v2/`.
- Credentials are always the placeholder `<your_api_token>`; never real-looking tokens, emails, or URLs.
- Use long option names: `--header`, `--request`, `--data`, `--url`. Never `-H`, `-X`, `-d` in reference docs.
- Always put the URL in `--url "…"`. For `GET`, omit `--request` (GET is the default); include `--request` for POST/PUT/DELETE.
- Break long commands with ` \` and two-space indentation.
- Send JSON with an explicit `--header "Content-Type: application/json"` and single-quoted `--data '{"…"}'`.
- URL-encode spaces and reserved characters (`%20`, `%2F`).

Response examples must be realistic, minimal, and consistent with each other: the task with ID `1042`, project `12`, user `57` recur across TaskFlow examples so readers can follow one fixture through the whole document. Show at most two objects in list responses.

Language-specific SDK examples (Python, JavaScript) are welcome as *additions* but never as replacements for cURL + JSON.

## 7. Status codes and errors

- Begin the success statement with the GitLab formula: *"If successful, returns `200 OK` and the following response attributes:"* (adjust the code: `201 Created`, `204 No Content`).
- Every operation ends with an error table: `Status code | Meaning | Common causes`. "The API follows standard REST conventions" is not documentation; enumerate the codes the spec declares, plus `401` and `429` where relevant.
- `409 Conflict` must be documented wherever concurrent modification can occur, with recovery guidance (re-`GET`, re-apply, retry).
- Error bodies: the canonical v2 envelope is
  ```json
  { "error": { "code": "VALIDATION_ERROR", "message": "Title is required", "details": [] } }
  ```
  Document the legacy flat format (`{"error": "not_found", "message": "…"}`) only where it still occurs, and always label it as transitional.

## 8. Voice, tense, and grammar

- Present tense, indicative mood, active voice. The audit tracker's `voice_consistency` column flagged six rows; the fix is mechanical: *"Retrieves a task"* not *"This endpoint can be used to retrieve a task"* or *"You can fetch a task."*
- Address the reader as *you*. Never *we* for the API's actions ("we delete the tasks") — the API *deletes*.
- Sentence case for all headings. American spelling. Serial (Oxford) comma.
- Second person for instructions; third person for system behavior.
- Do not personify the API. It *returns*, *rejects*, *requires* — it does not *want* or *allow politely*.

## 9. Terminology glossary (canonical terms)

The audit tracker recorded twelve terminology conflicts. These are now settled:

| ✅ Canonical | ❌ Banned variants | Rationale |
|---|---|---|
| retrieve | fetch, get (in prose) | Matches operation titles |
| endpoint | route, URL (as a noun for an operation) | One noun per concept |
| task ID | taskId, task_id (in prose) | `taskId` only as a literal path parameter name, in backticks |
| update | modify, change (as the operation verb) | Verb table in §4 |
| assignee | user (when discussing assignment) | Distinguishes from actors/watchers |
| delete | remove | Verb table in §4 |
| webhook URL | callback URL, destination | One name for the subscriber's endpoint |
| access token | JWT (in prose) | Implementation detail, not a contract term |
| token refresh | token rotation | Rotation implies a different algorithm |
| filter (list endpoints) | query (for list parameters) | Reserve *query* for the search endpoint |
| request body | payload, body (interchangeably) | Pick one; use *payload* only for webhooks |
| watcher | follower, observer | Matches `watchers` field name |

When a spec field name exists (`assignee_id`, `due_date`), the prose must use it verbatim, in backticks.

## 10. Timestamps and data formats

- The TaskFlow v2 standard is **ISO 8601 UTC**, rendered with a trailing `Z`: `2024-03-01T14:22:00Z`.
- Known legacy deviations are documented where they occur, with a warning, not silently: project `created` and user `last_login` are Unix timestamps pending normalization (tracked for v3). Never describe a deviation as "fine" — describe it, flag it, and give the conversion hint.
- IDs are integers. Enums are lowercase snake_case (`in_progress`, `task.created`). Dates in *filters* use `YYYY-MM-DD`.

## 11. Webhooks documentation

Webhook events are documented in the published **Webhook Event Catalog** (HTML), which is the single authoritative list. Rules:

- Event names use dot notation `resource.verb` (`task.created`, `member.added`). Never invent synonyms in prose.
- Every event entry documents: name, description, trigger condition, example payload, and provenance (declared in the OpenAPI spec, observed in the delivery log, or both). Provenance labels are mandatory because the catalog synthesizes contract and production reality; events observed in production but absent from the spec must be marked so integrators do not assume they are subscribable.
- Document retry behavior with its evidence source. As of the March 2024 export: failed deliveries are retried up to 3 times, then marked `failed`.
- Document HMAC payload verification whenever `secret` exists on the resource.

## 12. Versioning, history, and deprecation

- Every page carries a version line (`**API version:** 2.0.1`) and a last-reviewed date, mirroring the audit tracker's `last_reviewed` column so the two artifacts stay in lockstep.
- Additions and behavior changes get a short **History** note at the top of the affected section, in the GitLab pattern: *- `watchers` introduced in TaskFlow API 2.0.*
- Deprecations are announced in three places: inline in the attribute table (*"[Deprecated](link) in 2.1. Use `due_date` instead."*), in the History note, and on the global deprecations list. Never delete a documented attribute without a replacement pointer.

## 13. Rate limiting and pagination

- Cross-cutting sections state the contract once: tier limits (100 requests/minute free, 1000/minute paid), the `429 Too Many Requests` response, and the `X-RateLimit-Remaining` response header.
- Pagination parameters are `page` (default `1`) and `per_page` (default `25`, maximum `200`). List responses carry a `meta` object (`current_page`, `total_pages`, `total_count`). Any list endpoint that deviates must say so explicitly — the current project list is the known case to watch.

## 14. Accessibility and rendering

- One idea per paragraph; tables over prose for enumerations; fenced code blocks always declare a language (`bash`, `json`, `plaintext`).
- Warn before destructive operations with a blockquote warning: `> [!warning]` followed by the consequence and any escape hatch (`cascade=false`).
- Links resolve within the docs set; anchors are GitHub-compatible (lowercase, hyphenated).

## 15. OpenAPI sync policy

Modeled on GitLab's `openapi-doc-check` CI job: any change to API code or the OpenAPI spec requires a matching docs change in the same merge request, and any docs change that touches parameters, enums, defaults, or status codes requires a spec review. The discrepancy report (`discrepancy_report.csv`) is regenerated whenever either source changes; unexplained diffs block publication. Docs-only claims awaiting verification (see §2.3) live in the report with status `Flagged for engineering`.

## 16. Pre-publication checklist

A page may ship only when every item holds — these mirror the audit tracker's columns so the tracker can be retired into a boolean gate:

1. Description present and first sentence restates the title.
2. Auth requirement stated.
3. All path/query/body attributes in tables; spec-identical names, types, enums, defaults, requiredness.
4. cURL example and JSON response example present.
5. Error table complete against the spec, plus 401/429 where applicable.
6. Voice, terminology, and formatting conform to §§4–10.
7. Any docs-only claim is tagged verified or flagged.
8. Audit tracker row updated: `status = acceptable`, reviewer, date.

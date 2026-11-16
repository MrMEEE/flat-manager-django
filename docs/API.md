# API Reference

This document describes the HTTP APIs provided by flat-manager-django. Unless noted otherwise, paths are relative to the server root. The REST API is mounted at `/api/`.

## Authentication and conventions

The REST API accepts Django REST Framework token authentication and session authentication. Send a user token as `Authorization: Token <token>`; repository tokens and the legacy `APIToken` model are not substitutes for a DRF token. Session-authenticated unsafe requests require CSRF protection.

Most resource endpoints require authentication and apply resource/action permissions and organization scope. A scoped user can only access records in permitted organizations; an out-of-scope object may return `404`. The `/api/v2/` catalog includes public endpoints and privileged mutation endpoints, described below. The machine check-in endpoint is separate and unauthenticated.

DRF list endpoints use page-number pagination by default, with 50 records per page. A paginated response contains `count`, `next`, `previous`, and `results`; follow `next` to retrieve all results. Use `?page=2` for subsequent pages. Common DRF responses include `400` for invalid input, `401` for missing or invalid token authentication, `403` for insufficient permission, and `404` for inaccessible or missing records. Workflow adapters may return JSON errors in an `errors`, `error`, or `detail` field depending on the underlying handler.

For supported creation endpoints, scoped users without a permitted default organization must supply `organisation_id`; globally authorized users may create unassigned resources. The Global organization is a protected system organization. Build and promotion operations are asynchronous: a successful submission means a task was queued, not that it completed. Poll the appropriate resource or status endpoint for completion and errors.

Example:

```sh
curl -H "Authorization: Token $FLATMAN_TOKEN" \
  "https://flat-manager.example/api/repositories/"
```

## Getting a token

An administrator or user with access to their profile can create a DRF token in the web profile (`/profile/`). The API uses the token displayed there, not a repository upload/download token. Keep it secret; use HTTPS and a secret manager in automation. The browsable API login is `/api/auth/login/` and uses a session; it does not issue a DRF token.

## Resource endpoints

For `CRUD`, the collection URL supports `GET` and `POST`; the detail URL supports `GET`, `PUT`, `PATCH`, and `DELETE`. `Read` means list and detail `GET`. Workflow-backed resources can register other methods but return `405 Method Not Allowed` when the corresponding operation has no handler. A collection `POST` or detail mutation may return a workflow acknowledgement such as `{"status":"ok","id":1,"messages":[]}` instead of a serialized resource; retrieve it separately. The `workflows` endpoint and singleton site configuration have special response shapes.

| Resource under `/api/` | Effective operations | Fields or purpose |
| --- | --- | --- |
| `users/` | CRUD; admin writes | User accounts used to authenticate and receive permissions. `GET users/me/` returns the current user. |
| `profiles/` | CRUD; admin writes | Contact and biography information associated with a user; `user_id` is supplied on write. |
| `gpg-keys/` | CRUD | Signing keys used to sign repositories and RPMs. Reads expose public key details, never private keys. |
| `repositories/` | CRUD | Managed Flatpak repositories, including signing configuration and parent/child relationships used by promotion chains. |
| `repository-subsets/` | CRUD | Named configurations for partial mirrors of a repository, with their own collection ID and optional base URL. |
| `packages/` | CRUD | Flatpak configuration: `repository_id`, `package_id`, `package_name`, `version`, `git_repo_url`, `git_branch`, `branch`, `arch`, `status`; reads include builds and latest build. |
| `builds/` | Read | Build history, state, logs and artifacts; supports package and BuildStream builds. |
| `artifacts/` | CRUD | Uploaded file records associated with a build, including filename, size, path and checksum. |
| `tokens/` | CRUD | Repository token configuration: `repository_id`, `name`, `token_type`, `expires_at`, `is_active`. These tokens are not DRF login tokens and are not currently enforced by the server. |
| `external-refs/` | CRUD | Upstream Flatpak refs mirrored from a remote into a managed repository; reads include state, logs, commits and version snapshots. |
| `promotions/` | Read | Flatpak build promotion records; filters: `build`, `target_repo`, `status`. |
| `external-ref-promotions/` | Read | External version promotion records; filters: `external_ref`, `external_ref_version`, `target_repo`, `status`. |
| `flatpak-remotes/` | CRUD; admin | Upstream Flatpak repositories used as sources when importing External refs. |
| `organisations/` | CRUD; admin | Ownership and permission boundaries for resources and users; Global is system-managed. |
| `bst-sources/` | CRUD | BuildStream projects and elements that produce Flatpak refs for a managed repository. |
| `bst-promotions/` | Read | BuildStream promotion history. |
| `clients/` | Read, DELETE | Machine inventory, organization assignments and lockdown state. |
| `permission-groups/` | CRUD; admin | Reusable sets of resource permissions that can be assigned to users, optionally scoped to an organization. |
| `ldap-sources/` | CRUD; admin | Directory connection and search settings; encrypted bind passwords are not returned. |
| `ldap-group-mappings/` | CRUD; admin | Maps a directory group from an LDAP source to an application role and optional organization. |
| `rpm-packages/` | CRUD | RPM source configuration, build distributions, destinations, status and organizations. |
| `rpm-builds/` | Read, DELETE | RPM build history, selected repositories, status, files and errors. |
| `rpm-distributions/` | CRUD; admin | Distribution name, display name, mock configuration, architecture, RHEL version and active state. |
| `rpm-repositories/` | Read | Distribution repository metadata. |
| `satellite-servers/` | Read, POST, DELETE; admin | Satellite connection metadata; secret credentials are not returned. |
| `satellite-repositories/` | Read, POST, DELETE; admin | Satellite server, organization, product, repository and numeric repository ID. Creating requires `server_pk`. |
| `rpm-destinations/` | Read, DELETE; admin | Package/distribution/Satellite repository mappings. |
| `rpm-signing-keys/` | Read; admin | Package/distribution/GPG key assignments. |
| `user-roles/` | CRUD; admin | Legacy user-to-role assignments, optionally scoped to an organization. Current authorization uses explicit and group permission grants. |
| `permission-grants/` | CRUD; admin | Individual user's resource/action grants, optionally scoped to an organization. |
| `group-permissions/` | CRUD; admin | Resource/action grants owned by a permission group, optionally scoped to an organization. |
| `site-config/` | GET collection/detail; PUT/PATCH detail; admin | Singleton site configuration. The collection GET returns a single object, not a paginated array. |
| `workflows/` | GET collection | Runtime catalog of resource actions, paths, methods, descriptions and declared permission metadata. |

Numeric relationship fields refer to database IDs. For fields and choices not listed above, use the browsable API `OPTIONS` response or the corresponding serializer/form. Workflow-backed creates and updates accept the existing web form's field names; sending a list represents a multiple-choice form field. API workflow calls use a JSON object, including `{}` where an action takes no fields.

Lists may support `search`, `ordering`, and resource-specific filters. For example, `packages/` supports `status`, `repository`, `arch`, `branch`, `search`, and `ordering`; `builds/` supports `package`, `status`, `build_number`, `search`, and `ordering`; `external-refs/` supports `repository`, `remote`, `status`, `ref`, and `search`. Sorting and filtering apply only where configured by that view.

### Serialized fields

The following are field names exposed by the resource serializers. Read-only fields cannot be set by a write; related IDs must be valid and in scope. `id`, timestamps, state, logs and generated results are normally read-only. Workflow-backed writes use forms, not these read-only serializers.

| Resource | Serialized fields |
| --- | --- |
| `users` | `id`, `username`, `email`, `first_name`, `last_name`, `is_local`, `created_at`. This serializer does not expose a password field. |
| `profiles` | `id`, `user`, `user_id` (write-only), `bio`, `phone`, `organization`. |
| `gpg-keys` | `id`, `name`, `email`, `key_id`, `fingerprint`, `public_key`, `passphrase_hint`, `created_by`, `created_at`, `updated_at`, `is_active`. List responses use a lighter serializer with `created_by_username` and omit `public_key`. |
| `repositories` | `id`, `name`, `collection_id`, `description`, `gpg_key`, `gpg_key_id` (write-only), `parent_repos`, `parent_repo_names`, `child_repo_names`, `subsets`, `repo_path`, `public_key_path`, `created_by`, `created_at`, `updated_at`, `is_active`, `build_count`. |
| `repository-subsets` | `id`, `name`, `collection_id`, `base_url`, `repository_id` (write-only). |
| `packages` | `id`, `package_id`, `package_name`, `version`, `git_repo_url`, `git_branch`, `source_commit`, `branch`, `arch`, `status`, `commit_hash`, `dependencies`, `repository`, `repository_id` (write-only), `created_by`, `created_at`, `build_number`, `error_message`, `builds`, `latest_build`. |
| `builds` | `id`, `package_id`, `package_package_id`, `package_name`, `build_number`, `version`, `source_commit`, `commit_hash`, `dependencies`, `status`, `error_message`, `repository_name`, `started_at`, `completed_at`, `artifacts`, `logs`. |
| `artifacts` | `id`, `filename`, `file_path`, `file_size`, `checksum`, `uploaded_at`, `build_id` (write-only). |
| `tokens` | `id`, `name`, `token`, `token_type`, `repository`, `repository_id` (write-only), `created_at`, `expires_at`, `is_active`. Treat `token` as a secret even though repository tokens are not currently enforced. |
| `external-refs` | `id`, `repository`, `remote`, `ref`, `display_name`, `commit_hash`, `status`, `error_message`, `log`, `last_pulled_at`, `versions`. A version exposes `id`, `ref`, `commit_hash`, `status`, `pulled_at`, `source_published_at`, `error_message`. |
| `promotions` | `id`, `build`, `package`, `target_repo`, `status`, `error_message`, `promoted_by`, `created_at`, `completed_at`. |
| `external-ref-promotions` | `id`, `external_ref`, `external_ref_version`, `target_repo`, `status`, `error_message`, `promoted_by`, `created_at`, `completed_at`. |
| `flatpak-remotes` | `id`, `name`, `url`, `is_active`, `priority`. |
| `organisations` | `id`, `name`, `description`, `responsible_name`, `responsible_email`. |
| `bst-sources` | `id`, `name`, `repository`, `git_repo_url`, `git_branch`, `bst_element`, `bst_version`, `status`, `error_message`, `build_number`, `organisations`. |
| `bst-promotions` | `id`, `build`, `bst_source`, `target_repo`, `status`, `error_message`, `promoted_by`, `created_at`, `completed_at`. |
| `clients` | `id`, `hostname`, `serial_number`, `machine_type`, `last_checkin`, `organisations`, `lockdown`, `installed_flatpaks`, `foreign_flatpaks`, `outdated_flatpaks`, `user_flatpaks`, `remotes`, `managed_remotes`, `updated_at`. |
| `permission-groups` | `id`, `name`, `description`, `organisation`, `is_predefined`, `users`. |
| `ldap-sources` | `id`, `name`, `hostname`, `port`, `protocol`, `verify_certs`, `server_type`, `bind_dn`, `base_dn`, `group_base_dn`, `group_membership`, `ldap_filter`, `attr_username`, `attr_first_name`, `attr_last_name`, `attr_email`, `is_active`. |
| `ldap-group-mappings` | `id`, `source`, `ldap_group_dn`, `role`, `organisation`. |
| `rpm-packages` | `id`, `name`, `git_repo_url`, `git_branch`, `spec_file`, `distributions`, `default_repos`, `organisations`, `status`, `available_version`, `upstream_version`, `description`, `allow_internet_access`, `cleanup_on_success`, `upstream_url`. |
| `rpm-builds` | `id`, `package`, `distribution`, `build_number`, `status`, `error_message`, `version`, `selected_repos`, `rpm_files`, `started_at`, `completed_at`. |
| `rpm-distributions` | `id`, `name`, `display_name`, `mock_config`, `arch`, `rhel_version`, `is_active`. |
| `rpm-repositories` | `id`, `distribution`, `name`, `enabled`, `source`. |
| `satellite-servers` | `id`, `name`, `url`, `login`, `ssl_verify`. |
| `satellite-repositories` | `id`, `server`, `name`, `organization`, `product`, `repository_id`. |
| `rpm-destinations` | `id`, `package`, `distribution`, `repository`. |
| `rpm-signing-keys` | `id`, `package`, `distribution`, `signing_key`. |
| `user-roles` | `id`, `user`, `role`, `organisation`. |
| `permission-grants` | `id`, `user`, `resource`, `action`, `granted`, `organisation`. |
| `group-permissions` | `id`, `group`, `resource`, `action`, `granted`, `organisation`. |
| `site-config` | `id`, `failed_builds_to_keep`, `upstream_version_check_interval_hours`, `available_version_check_interval_hours`, `build_timeout_minutes`, `stale_build_check_interval_seconds`, `stale_build_timeout_minutes`, `bst_checkout_timeout_minutes`, `promotion_retry_interval_minutes`, `promotion_stale_timeout_minutes`, `client_stale_hours`, `external_ref_check_interval_hours`, `dependency_ref_check_interval_hours`, `bst1_venv_path`, `timezone`. |

### Creation and update payloads

Some examples of write fields that differ from the resource read shape:

| Endpoint | JSON body |
| --- | --- |
| `POST /api/repositories/` | `name`, optional `collection_id`, `description`, `gpg_key_id`, `parent_repos` (ID list), `is_active`, `organisation_id`. |
| `POST /api/packages/` | `repository_id`, `package_id`, `package_name`; optional `version`, `git_repo_url`, `git_branch`, `branch`, `arch`, `organisation_id`. |
| `POST /api/bst-sources/` | `repository`, `name`, `git_repo_url`, `git_branch`, `bst_element`, `bst_version`, `organisations` (ID list). |
| `POST /api/rpm-packages/` | `name`, `git_repo_url`, `git_branch`, `spec_file`, `distributions` (ID list); optional `description`, `allow_internet_access`, `cleanup_on_success`, `upstream_url`, `upstream_version_script`, `organisations` (ID list). |
| `POST /api/organisations/` | `name`, `responsible_name`, `responsible_email`, optional `description`. |
| `POST /api/satellite-servers/` | `name`, `url`, `admin_user`, `admin_pass`, optional `svc_login`, `ssl_verify`. This workflow tests connectivity and provisions a service account. |
| `POST /api/satellite-repositories/` | `server_pk`, `organization`, `product`, `name`, `repository_id`. |
| `POST /api/permission-grants/` | `user`, `resource`, `action`, `granted`, optional `organisation` (`null` for global scope). |
| `POST /api/ldap-group-mappings/` | `source`, `ldap_group_dn`, `role`, optional `organisation`. |

```json
{"count": 1, "next": null, "previous": null, "results": [{"id": 1, "name": "stable"}]}
```

This illustrates the pagination envelope only; actual results contain the fields documented above. A successful asynchronous action commonly returns `202 Accepted` and a record ID or status; workflow-backed actions can return `200` with a `status`, `message`, or `messages` acknowledgement instead.

## External refs and promotions

Create an External with `POST /api/external-refs/`:

```json
{"repository": 1, "remote": 2, "ref": "app/org.example.App/x86_64/stable", "organisation_id": 3}
```

The repository must be an accessible, active top-level repository, and the remote must be active. A new record returns `201` and queues a pull; an existing `(repository, ref)` returns `200` and associates the selected/default organization without queuing a duplicate pull. If an existing External has no organizations, Global is retained as an owner when the new organization is added. An API `DELETE /api/external-refs/{id}/` is organization-aware: it detaches organizations the caller can delete; physical deletion and repository cleanup occur only when the caller can delete all owners. `204` therefore does not necessarily mean the shared record was removed for other owners.

`POST /api/external-refs/bulk-import/` is an admin workflow. Its body contains `repository_id`, `remote_id`, `refs` (1-1000 full `app/...` or `runtime/...` refs), and optionally `organisation_id`. The response includes `created`, `associated`, `skipped`, and `redirect_url`. The browser endpoint `/externals/bulk-import/` uses the same payload but is a session-authenticated web route, not a DRF-token route.

| Method and route | Input and result |
| --- | --- |
| `POST /api/external-refs/{id}/pull/` | `{}`; queues a pull and subsequent publication; returns `202` with `id` and `status`. Requires `externals:sync`. |
| `POST /api/external-refs/{id}/publish/` | `{}`; queues publication of a pulled ref; returns `202`. Requires published-builds External publication or legacy External publish permission. |
| `POST /api/external-refs/{id}/unpublish/` | `external_ref_version_id`; removes the specified published version from its source repository subject to current-commit checks. |
| `POST /api/external-refs/{id}/promote/` | `target_repo_id`, optional `external_ref_version_id`; returns `202` with `promotion_id`, `external_ref_version_id`, `status`. If omitted, the latest published version is used. |
| `POST /api/builds/{id}/promote/` | `target_repo_id`; returns `202` with `promotion_id` and `status`. The build must be published. |
| `POST /api/builds/{id}/bst-promote/` | `target_repo_id`; queues a BuildStream build promotion. |
| `GET /api/promotions/available/` | Paginated eligible build/target pairs with `kind`, build, package, source repository and target repository IDs/names. |
| `GET /api/external-ref-promotions/available/` | Paginated eligible External-version/target pairs with `kind`, External, ref, version, commit, source repository and target repository IDs/names. |
| `POST /api/{promotions,external-ref-promotions,bst-promotions}/{id}/{retry,delete}/` | Retry a failed promotion or delete a promotion through its workflow. |

Promotion availability is a snapshot calculated from the repository parent/child chain and promotion status. A child becomes available only when its required parent promotions have completed. Existing active or successful promotions are not offered again; failures may be retried. External promotion eligibility is version-specific. Re-fetch readiness after queued work completes; concurrent changes can invalidate a previously returned target.

```sh
curl -X POST -H "Authorization: Token $FLATMAN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"target_repo_id": 3}' \
  'https://flat-manager.example/api/builds/42/promote/'
```

Poll `GET /api/promotions/{promotion_id}/` or `GET /api/external-ref-promotions/{promotion_id}/` for `pending`, `promoting`, `promoted`, or `failed`, plus `error_message`. Supplying an `organisation_id` does not bypass permission checks.

## Actions

Unless stated otherwise, action names below use `POST /api/{resource}/{id}/{action}/` for detail actions and `POST /api/{resource}/{action}/` for collection actions. Dashes and underscores are significant. The server may require additional action-specific permissions beyond list/read permission.

| Resource | Detail actions | Collection actions |
| --- | --- | --- |
| `gpg-keys` | `public_key` (GET), `renew` | `generate`, `import_key` |
| `repositories` | `builds` (GET), `subsets` (GET), `update-metadata` | |
| `packages` | `start`, `cancel`, `commit`, `publish`, `upload`, `missing_objects` (GET/POST), `build_ref`, `retry`, `republish`, `check-upstream`, `check-available`, `logs` (GET) | |
| `builds` | `promote`, `bst-promote`, `unpublish`, `recommit`, `delete`, `cancel`, `logs` (GET) | |
| `external-refs` | `pull`, `publish`, `promote`, `unpublish` | `bulk-import` |
| `promotions`, `external-ref-promotions` | `retry`, `delete` | `available` (GET) |
| `bst-promotions` | `retry`, `delete` | |
| `bst-sources` | `build`, `retry`, `force-rebuild` | `integrity-check` |
| `flatpak-remotes` | `toggle` | |
| `clients` | `assign-organisations`, `lockdown` | |
| `permission-groups` | `memberships` | |
| `ldap-sources` | | `test-connection`, `test-search` |
| `site-config` | | `cleanup`, `sync-repositories`, `check-external-updates`, `scan-available-versions`, `scan-upstream-versions`, `repair-permissions`, `scan-orphaned-refs`, `prune-orphaned-ref`, `prune-orphaned-refs` |
| `rpm-packages` | `build`, `build-with-number`, `check-upstream`, `check-available`, `toggle-repository`, `signing-key`, `assign-destination` | `scan-spec-files`, `scan-branches` |
| `rpm-builds` | `retry`, `repush`, `cancel`, `logs` | |
| `rpm-distributions` | `toggle`, `sync-repos` | `sync` |
| `rpm-repositories` | `toggle` | |
| `satellite-servers` | | `fetch-organisations`, `fetch-products`, `fetch-repositories` |

The workflow routes for RPM `scan-branches`, `scan-spec-files`, RPM build `logs`, BuildStream `integrity-check`, and Satellite fetches are **POST** endpoints even though the underlying web handler is invoked with GET. `GET /api/workflows/` lists actual action paths, HTTP methods and declared resource permissions for the running version.

Action payloads include:

- Key `generate`: `name`, `email`, optional `comment`, `key_length`, `organisation_id`; `import_key`: `name`, `email`, `public_key`, optional `private_key`, `passphrase`, `organisation_id`; `renew`: `key_lifetime` (`0`, `1y`, `2y`, `5y`, `10y`). Never put private keys or passwords in source control or logs.
- RPM `scan-branches`: `url`; `scan-spec-files`: `url`, optional `branch`; `build-with-number`: `build_number`; `toggle-repository`: `repo_pk`; `signing-key`: `dist_pk`, optional `signing_key_id`; `assign-destination`: `distribution_id`, `repository_id`.
- Client `assign-organisations`: `organisations`, a replacement list of IDs. LDAP `test-connection` and `test-search` accept connection fields including `hostname`, `port`, `protocol`, `verify_certs`, `bind_dn`, and `bind_password`; search additionally uses `base_dn` and `search_username`. `source_pk` can reuse a saved source's bind password.
- Maintenance `prune-orphaned-ref`: `repo`, `ref`; `prune-orphaned-refs`: `items` containing `{repo, ref}` objects. Check action-specific responses before treating a queued operation as completed.
- Permission group `memberships`: `submit_action` (`add_permission`, `remove_permission`, `add_user`, or `remove_user`). Adding a user uses `user_pk`; removing one also uses `user_pk`. Removing a permission uses `permission_pk`; adding one uses the permission form's `resource`, `action`, `granted`, and optional `organisation` fields.
- Repository `update-metadata`, package `retry`/`republish`/version checks, BuildStream `build`/`retry`/`force-rebuild`, and RPM build `retry`/`repush`/`cancel` use an empty JSON object. BuildStream integrity and RPM/Satellite discovery are report-style workflows even though their public API method is POST.

For example, to associate an RPM package with a Satellite destination:

```sh
curl -X POST -H "Authorization: Token $FLATMAN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"distribution_id": 2, "repository_id": 7}' \
  'https://flat-manager.example/api/rpm-packages/12/assign-destination/'
```

Package `upload`, `missing_objects`, and `build_ref` currently provide flat-manager compatibility responses but do **not** implement artifact upload, OSTree object checking, or build-ref storage respectively. Do not rely on them as fully implemented operations.

`GET /api/packages/{id}/logs/` and `GET /api/builds/{id}/logs/` are intentionally unauthenticated; they expose build log contents. `GET /api/version/` is public and returns `version` and `build_date`. `GET /api/git-branches/?repo_url=...` is public; it runs `git ls-remote` and can return fallback `main`/`master` branches on failure.

## Flathub-compatible API

The following paths are under `/api/v2/`. They do not all use DRF pagination. The public catalog generally includes published packages only and does not apply the authenticated user's organization scope. A catalog application object includes `id`, `name`, `summary`, `description`, `homepage`, `icon_url`, `screenshots`, `developer`, `categories`, `keywords`, `verified`, `mobile`, `version`, `branch`, `arch`, `repository`, and `updated_at`.

### Public routes

| Method and path | Description |
| --- | --- |
| `GET status` | Returns `{"status":"ok"}`. |
| `GET appstream` | Published application objects; `sort=recent` or `recently-updated` orders by update time. |
| `GET appstream/{app_id}` | One published application object, or `404`. |
| `GET summary/{app_id}` | Application ID, name, version, branch, architecture and status. |
| `GET stats/`, `GET stats/{app_id}` | Aggregate app/repository counts, or app build and usage counts. |
| `GET usage/popular` | Usage ranking; `days` defaults to 30 and is clamped to 1-365. |
| `GET addon/{app_id}` | Currently returns an empty list for an existing published app. |
| `GET platforms`, `GET runtimes` | Distinct published package architectures or declared runtimes. |
| `GET is-fullscreen-app/{app_id}` | Currently returns `false` for an existing published app. |
| `POST search` | JSON `query` or `search`; returns matching published application objects. |
| `GET feed/{feed}` | `new` or `recently-updated`. |
| `GET collection/{collection}` | `popular`, `trending`, `recently-added`, `new`, `recently-updated`, `verified`, `mobile`, or `alphabetical`. |
| `GET collection/category`, `GET collection/category/{value}` | Category names or matching application objects. |
| `GET collection/category/{category}/subcategories` | Currently returns an empty list. |
| `GET collection/developer`, `GET collection/developer/{value}` | Developer names or matching application objects. |
| `GET collection/keyword`, `GET collection/keywords` | Distinct keyword names. |
| `GET quality-moderation/status`, `GET quality-moderation/stats-by-category` | Aggregate quality counts or package counts by branch. |
| `GET quality-moderation/passing-apps`, `GET quality-moderation/failed-by-guideline`, `GET quality-moderation/app-pick-recommendations` | Passing apps, failed guideline details, or currently empty recommendations. |
| `GET quality-moderation/{app_id}`, `GET quality-moderation/{app_id}/status` | App quality status, fullscreen flag, guidelines and review comment. |
| `GET year-in-review/{year}` | Counts of apps added/updated and usage observations for a year. |
| `GET app-picks/app-of-the-day`, `GET app-picks/app-of-the-week` | Published selections for today. |
| `GET app-picks/curated-app-selections/{date}`, `GET app-picks/app-of-the-day/{date}`, `GET app-picks/apps-of-the-week/{date}` | Published selections for an ISO date (`YYYY-MM-DD`). |

### Administrative routes

These routes require admin access (`IsAdmin`); authentication alone is insufficient.

| Method and path | Body or behavior |
| --- | --- |
| `PUT appstream/{app_id}/metadata` | Any of `summary`, `description`, `homepage`, `icon_url`, `screenshots`, `developer_name`, `categories`, `keywords`, `is_verified`, `is_mobile`. |
| `GET app-picks/admin/curated-app-selections` | List selections, including unpublished packages. |
| `POST app-picks/admin/curated-app-selections` | `app_id`, `kind`, `date`; optional `title`, `description`, `rank`. |
| `PUT app-picks/admin/curated-app-selections/{selection_id}` | Update `title`, `description`, `rank`, `kind`, or ISO `date`. |
| `DELETE app-picks/admin/curated-app-selections/{selection_id}` | Delete selection; `204`. |
| `GET app-picks/admin/curated-app-selection-themes` | List themes. |
| `GET app-picks/admin/apps-of-the-week/{date}` | List selections for the ISO date. |
| `POST app-picks/app-of-the-day`, `POST app-picks/app-of-the-week` | `app_id`; optional `date`, `title`, `description`, `rank`. |
| `POST quality-moderation/{app_id}/fullscreen` | Set fullscreen flag true. |
| `POST quality-moderation/{app_id}/request-review` | Optional `reason`; creates a pending review request. |
| `DELETE quality-moderation/{app_id}/request-review` | Reject pending requests for the application; `204`. |
| `GET moderation/apps`, `GET moderation/apps/{app_id}` | List review requests, optionally filtered to an app. |
| `POST moderation/submit_review_request` | `app_id`, optional `reason`; returns a new request ID and status. |
| `POST moderation/requests/{request_id}/review` | `status` (`approved` or `rejected`), optional `comment`. |

Paths shown without a trailing slash above match the explicit URL definitions; `stats/` is an exception and does include one. Some endpoints intentionally return empty compatibility results as noted; they are not full Flathub implementations.

## Machine check-in

`POST /api/client-checkin/` accepts an unauthenticated JSON machine report. `hostname` is required. Optional fields include `serial_number`, `machine_type`, `bios_version`, `server_url`, `remotes`, `managed_remotes`, `installed`, `user_flatpaks`, `tuxmigrate_facts`, and `virtual_machines`. The server creates or updates the client identified by hostname and returns `{"status":"ok","hostname":"...","lockdown":false}`. A missing hostname or invalid JSON returns `400`. See the bundled [check-in client](../packaging/flat-manager-django-client/flat-manager-checkin) for the complete payload format. Because this endpoint is unauthenticated, expose it only to trusted networks or protect it at the reverse proxy.

## Related documentation

- [Ansible collection expanded API guide](../ansible/collections/ansible_collections/mrmeee/flatman/docs/expanded-api.md) and [playbook examples](../ansible/collections/ansible_collections/mrmeee/flatman/playbooks/README.md).
- [Build system usage](BUILD_SYSTEM_USAGE.md), [GPG key management](GPG_KEYS.md), and [repository subset management](SUBSET_MANAGEMENT.md).
- The running server's browsable API and `GET /api/workflows/` reflect its installed version and permissions.
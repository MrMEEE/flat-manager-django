# Flat Manager — Django

A modern reimplementation of [flat-manager](https://github.com/flatpak/flat-manager) in Python/Django, with flatpak lifecycle management, extended with RPM package management, LDAP authentication, and a Bootstrap 5 web UI.

**Current version:** 0.8.11

## Features

- **Flatpak repository management** — Create and manage OSTree-backed Flatpak repositories with GPG signing
- **Flatpak build pipeline** — Build packages from Git or BuildStream sources, commit and promote between repositories
- **RPM package management** — Build RPMs with Mock, manage distributions, sync to Satellite/Katello
- **External refs** — Track and import upstream Flatpak refs from remote repositories
- **Client management** — Register clients, track installed/outdated/foreign packages, real-time check-ins
- **Organisation scoping** — Group repositories, GPG keys, and RPM packages by organisation
- **LDAP authentication** — Authenticate against Active Directory or FreeIPA/POSIX LDAP; auto-provision users and sync roles from LDAP groups
- **Role-based access control** — Five built-in roles (Admin, Superuser, Build Admin, Repo Admin, View) enforced on all views and API endpoints
- **REST API** — Full DRF-based API with token and session authentication
- **Real-time updates** — WebSocket push via Django Channels for build status and client check-ins
- **Background tasks** — Celery + Redis for async builds, scans, and promotions

## Architecture

```
┌──────────────────────────────────────────────┐
│  Bootstrap 5 Web UI  (Django Templates)      │
└───────────────────────┬──────────────────────┘
                        │ HTTP
┌───────────────────────▼──────────────────────┐
│  Django (Daphne / ASGI)                       │
│  ├─ apps/users/    — auth, users, LDAP, roles │
│  ├─ apps/flatpak/  — repos, builds, clients   │
│  ├─ apps/rpm/      — RPM packages & builds    │
│  └─ apps/api/      — DRF REST API             │
└──────┬──────────────────────┬─────────────────┘
       │ ORM                  │ WebSockets
┌──────▼──────┐      ┌────────▼────────┐
│  Database   │      │ Django Channels │
│ SQLite /    │      │  (Daphne/ASGI)  │
│ MariaDB     │      └────────┬────────┘
└─────────────┘               │
┌─────────────┐      ┌────────▼────────┐
│   Celery    │◄─────│ Redis / Valkey  │
│   Workers   │      │ (broker + layer)│
└─────────────┘      └─────────────────┘
```

## Role-Based Access Control

Every view and API endpoint enforces one of five roles. Roles can be scoped globally or per-organisation.

| Role | Description | Grants |
|---|---|---|
| `admin` | Platform administrator | All actions including user management, config, and LDAP |
| `superuser` | Elevated operator | All write actions except user/LDAP management |
| `build_admin` | Build operator | Package/build CRUD and promotion |
| `repo_admin` | Repository operator | Repository, GPG key, and distribution management |
| `view` | Read-only | View access only (all authenticated users also have read access) |

Local users authenticate with Django's password backend. LDAP-provisioned users (flagged `is_local=False`) are authenticated against LDAP and have roles synced from group mappings on every login.

## Installation

### Prerequisites

- Python 3.10+
- Redis or Valkey
- MariaDB/MySQL (production) or SQLite (development)

### Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure environment
cp packaging/conf/flat-manager.env.example .env
# Edit .env — at minimum set SECRET_KEY and DATABASE_URL

# 3. Apply migrations
python manage.py migrate

# 4. Create an initial admin user
python manage.py createsuperuser

# 5. Start all services
./manage-services.sh start
```

### Production (RPM)

See [docs/INSTALL_RPM.md](docs/INSTALL_RPM.md) for the full RPM-based installation guide covering Nginx, systemd units, and SELinux.

## Service Management

```bash
./manage-services.sh start    # Start all services
./manage-services.sh stop     # Stop all services
./manage-services.sh restart  # Restart all services
./manage-services.sh status   # Check service status
```

Systemd units are provided under `packaging/systemd/`:

| Unit | Description |
|---|---|
| `flat-manager-web.service` | Daphne ASGI server |
| `flat-manager-celery.service` | Celery worker (general queue) |
| `flat-manager-celery-ops.service` | Celery worker (long-running operations) |
| `flat-manager-celery-beat.service` | Celery periodic task scheduler |
| `flat-manager.target` | Umbrella target for all units |

## Project Structure

```
flat-manager-django/
├── apps/
│   ├── users/          # Authentication, users, roles, LDAP
│   │   ├── models.py   # User, UserRole, LDAPSource, LDAPGroupMapping
│   │   ├── backends.py # LDAPBackend, LocalModelBackend
│   │   └── mixins.py   # Permission mixins for CBVs
│   ├── flatpak/        # Flatpak repos, builds, clients, organisations
│   ├── rpm/            # RPM packages, builds, distributions, Satellite
│   └── api/            # DRF REST API + permissions.py
├── config/             # Django settings, ASGI/WSGI, Celery, URL routing
├── templates/          # Bootstrap 5 HTML templates
├── packaging/          # RPM spec, systemd units, Nginx config, SELinux
├── tools/              # Release tooling
├── docs/               # Extended documentation
├── manage.py
├── requirements.txt
├── release.sh          # Release script (bumps version + tags + pushes)
└── version.py          # Single source of truth for VERSION
```

## Web UI Endpoints

### Users & Administration (prefix: `/`)

| URL | Permission | Description |
|---|---|---|
| `/` | Public | Landing / login redirect |
| `/login/` | Public | Login form |
| `/logout/` | Authenticated | Logout |
| `/dashboard/` | Authenticated | Main dashboard with stats |
| `/dashboard/stats/` | Authenticated | JSON stats for dashboard polling |
| `/profile/` | Authenticated | Own profile |
| `/users/` | Admin | User list |
| `/users/create/` | Admin | Create user |
| `/users/<pk>/` | Admin | User detail |
| `/users/<pk>/edit/` | Admin | Edit user |
| `/users/<pk>/password/` | Admin | Set password |
| `/users/<pk>/roles/` | Admin | Manage roles |
| `/ldap/` | Admin | LDAP source list |
| `/ldap/create/` | Admin | Add LDAP source |
| `/ldap/<pk>/` | Admin | LDAP source detail + group mappings |
| `/ldap/<pk>/edit/` | Admin | Edit LDAP source |
| `/ldap/<pk>/delete/` | Admin | Delete LDAP source |
| `/ldap/<source_pk>/mappings/add/` | Admin | Add group mapping |
| `/ldap/<source_pk>/mappings/<pk>/delete/` | Admin | Delete group mapping |

### Flatpak (prefix: `/`)

#### GPG Keys

| URL | Permission | Description |
|---|---|---|
| `/gpg-keys/` | Authenticated | List GPG keys |
| `/gpg-keys/generate/` | Repo Admin | Generate new key pair |
| `/gpg-keys/import/` | Repo Admin | Import existing key |
| `/gpg-keys/<pk>/` | Authenticated | Key detail |
| `/gpg-keys/<pk>/delete/` | Repo Admin | Delete key |
| `/gpg-keys/<pk>/download/` | Authenticated | Download public key |
| `/gpg-keys/<pk>/renew/` | Repo Admin | Extend key expiry |

#### Repositories

| URL | Permission | Description |
|---|---|---|
| `/repos/` | Authenticated | Repository list |
| `/repos/create/` | Repo Admin | Create repository + OSTree init |
| `/repos/<pk>/` | Authenticated | Repository detail |
| `/repos/<pk>/edit/` | Repo Admin | Edit repository |
| `/repos/<pk>/delete/` | Repo Admin | Delete repository |
| `/repos/<pk>/update-metadata/` | Repo Admin | Re-sign + regenerate metadata |
| `/repos/<repo_pk>/subsets/create/` | Repo Admin | Create repository subset |
| `/subsets/<pk>/edit/` | Repo Admin | Edit subset |
| `/subsets/<pk>/delete/` | Repo Admin | Delete subset |

#### Packages & Builds

| URL | Permission | Description |
|---|---|---|
| `/packages/` | Authenticated | Package list |
| `/packages/create/` | Build Admin | Create package |
| `/packages/retry-all-failed/` | Build Admin | Retry all failed builds |
| `/packages/bulk-action/` | Build Admin | Bulk action on packages |
| `/packages/<pk>/` | Authenticated | Package detail |
| `/packages/<pk>/edit/` | Build Admin | Edit package |
| `/packages/<pk>/delete/` | Build Admin | Delete package |
| `/packages/<pk>/retry/` | Build Admin | Retry build |
| `/packages/<pk>/check-upstream/` | Authenticated | Check upstream version |
| `/packages/<pk>/check-available/` | Authenticated | Check available version |
| `/packages/<pk>/status/` | Authenticated | Build status (JSON) |
| `/packages/<pk>/commit/` | Build Admin | Commit build |
| `/packages/<pk>/publish/` | Build Admin | Publish build |
| `/packages/<pk>/republish/` | Build Admin | Re-publish build |
| `/packages/<pk>/builds/` | Authenticated | Build history (JSON) |
| `/builds/` | Authenticated | Build list |
| `/builds/<pk>/` | Authenticated | Build detail |
| `/builds/<pk>/promote/` | Build Admin | Promote build |
| `/builds/<pk>/promotions/` | Authenticated | Promotions for build (JSON) |
| `/builds/<pk>/unpublish/` | Build Admin | Unpublish build |
| `/builds/<pk>/delete/` | Build Admin | Delete build |
| `/builds/<pk>/cancel/` | Build Admin | Cancel build |

#### Promotions

| URL | Permission | Description |
|---|---|---|
| `/promotions/` | Authenticated | Promotion list |
| `/promotions/status/` | Authenticated | Bulk promotion status (JSON) |
| `/promotions/sync/` | Admin | Sync all repos |
| `/promotions/<pk>/delete/` | Build Admin | Delete promotion |
| `/promotions/<pk>/retry/` | Build Admin | Retry promotion |

#### BuildStream Sources

| URL | Permission | Description |
|---|---|---|
| `/bst-sources/` | Authenticated | BST source list |
| `/bst-sources/create/` | Build Admin | Add BST source |
| `/bst-sources/check-integrity/` | Authenticated | Integrity check |
| `/bst-sources/bulk-action/` | Authenticated | Bulk actions |
| `/bst-sources/<pk>/` | Authenticated | BST source detail |
| `/bst-sources/<pk>/edit/` | Build Admin | Edit BST source |
| `/bst-sources/<pk>/delete/` | Build Admin | Delete BST source |
| `/bst-sources/<pk>/retry/` | Build Admin | Retry |
| `/bst-sources/<pk>/force-rebuild/` | Build Admin | Force rebuild |
| `/builds/<build_pk>/bst-promote/` | Build Admin | Promote BST build |
| `/bst-promotions/<pk>/retry/` | Build Admin | Retry BST promotion |
| `/bst-promotions/<pk>/delete/` | Build Admin | Delete BST promotion |

#### External Refs

| URL | Permission | Description |
|---|---|---|
| `/externals/` | Authenticated | External ref list |
| `/externals/create/` | Build Admin | Add external ref |
| `/externals/bulk-action/` | Build Admin | Bulk action |
| `/externals/bulk-import/` | Build Admin | Bulk import |
| `/externals/<pk>/` | Authenticated | External ref detail |
| `/externals/<pk>/edit/` | Build Admin | Edit |
| `/externals/<pk>/delete/` | Build Admin | Delete |
| `/externals/<pk>/pull/` | Build Admin | Pull from upstream |
| `/externals/<pk>/publish/` | Build Admin | Publish |
| `/externals/<pk>/unpublish/` | Build Admin | Unpublish |
| `/externals/<pk>/promote/` | Build Admin | Promote |
| `/externals/<pk>/status/` | Authenticated | Status (JSON) |
| `/external-promotions/<pk>/retry/` | Build Admin | Retry promotion |
| `/external-promotions/<pk>/delete/` | Build Admin | Delete promotion |
| `/external-promotions/status/` | Authenticated | Bulk status (JSON) |

#### Clients

| URL | Permission | Description |
|---|---|---|
| `/clients/` | Authenticated | Client list |
| `/clients/<pk>/` | Authenticated | Client detail (triggers installed/outdated check) |
| `/clients/<pk>/assign-orgs/` | Authenticated | Assign organisations to client |
| `/clients/bulk-action/` | Authenticated | Bulk actions |
| `/api/client-checkin/` | Public (token) | Client check-in endpoint |

#### Organisations & Config

| URL | Permission | Description |
|---|---|---|
| `/organisations/` | Authenticated | Organisation list |
| `/organisations/create/` | Admin | Create organisation |
| `/organisations/<pk>/edit/` | Admin | Edit organisation |
| `/organisations/<pk>/delete/` | Admin | Delete organisation |
| `/dependencies/` | Authenticated | Dependency tracking list |
| `/config/` | Admin | Site configuration |
| `/config/run-cleanup/` | Admin | Run cleanup task |
| `/config/check-external-ref-updates/` | Admin | Trigger update scan |
| `/config/scan-available-versions/` | Admin | Scan available versions |
| `/config/scan-upstream-versions/` | Admin | Scan upstream versions |
| `/config/scan-repair-repo-tmp-perms/` | Admin | Repair repo tmp permissions |
| `/config/scan-orphaned-refs/` | Admin | Scan orphaned OSTree refs |
| `/config/prune-orphaned-ref/` | Admin | Prune one orphaned ref |
| `/config/prune-orphaned-refs/bulk/` | Admin | Prune all orphaned refs |
| `/config/remotes/add/` | Admin | Add Flatpak remote |
| `/config/remotes/<pk>/delete/` | Admin | Remove Flatpak remote |
| `/config/remotes/<pk>/toggle/` | Admin | Toggle remote active state |

### RPM (prefix: `/`)

#### Packages

| URL | Permission | Description |
|---|---|---|
| `/rpms/` | Authenticated | RPM package list |
| `/rpms/create/` | Build Admin | Create RPM package |
| `/rpms/<pk>/` | Authenticated | Package detail |
| `/rpms/<pk>/edit/` | Build Admin | Edit package |
| `/rpms/<pk>/delete/` | Build Admin | Delete package |
| `/rpms/<pk>/build/` | Build Admin | Trigger build |
| `/rpms/<pk>/build-with-number/` | Build Admin | Trigger build with specific number |
| `/rpms/<pk>/check-upstream/` | Build Admin | Check upstream version |
| `/rpms/<pk>/check-available/` | Build Admin | Check available version |
| `/rpms/<pk>/repositories/` | Repo Admin | Package → repository mapping |
| `/rpms/<pk>/repositories/<repo_pk>/toggle/` | Repo Admin | Toggle package/repo assignment |
| `/rpms/<pkg_pk>/signing-key/<dist_pk>/` | Repo Admin | Assign signing key per distribution |
| `/rpms/<pk>/assign-destination/` | Repo Admin | Assign Satellite destination |
| `/rpms/destinations/<pk>/remove/` | Repo Admin | Remove destination |

#### Builds

| URL | Permission | Description |
|---|---|---|
| `/rpms/builds/<pk>/` | Authenticated | RPM build detail |
| `/rpms/builds/<pk>/logs/` | Authenticated | Build logs (JSON) |
| `/rpms/builds/<pk>/mock-log/` | Authenticated | Raw mock log |
| `/rpms/builds/<pk>/retry/` | Build Admin | Retry build |
| `/rpms/builds/<pk>/cancel/` | Build Admin | Cancel build |
| `/rpms/builds/<pk>/delete/` | Build Admin | Delete build |
| `/rpms/scan-spec-files/` | Build Admin | Scan git repo for spec files |
| `/rpms/scan-branches/` | Build Admin | Scan git repo branches |

#### Distributions & Destinations

| URL | Permission | Description |
|---|---|---|
| `/rpms/distributions/` | Repo Admin | Distribution list |
| `/rpms/distributions/sync/` | Repo Admin | Sync distributions from Mock configs |
| `/rpms/distributions/<pk>/toggle/` | Repo Admin | Toggle distribution active state |
| `/rpms/distributions/<pk>/sync-repos/` | Repo Admin | Sync repos for distribution |
| `/rpms/distributions/repos/<repo_pk>/toggle/` | Repo Admin | Toggle individual repo |
| `/rpms/destinations/` | Repo Admin | Satellite/Katello destination list |
| `/rpms/destinations/add-server/` | Admin | Add Satellite server |
| `/rpms/destinations/servers/<pk>/delete/` | Admin | Delete Satellite server |
| `/rpms/destinations/servers/<server_pk>/add-repo/` | Admin | Add Satellite repository |
| `/rpms/destinations/repos/<pk>/delete/` | Admin | Delete Satellite repository |
| `/rpms/destinations/api/orgs/` | Admin | Fetch orgs from Satellite (JSON) |
| `/rpms/destinations/api/products/` | Admin | Fetch products from Satellite (JSON) |
| `/rpms/destinations/api/repos/` | Admin | Fetch repos from Satellite (JSON) |

## REST API Endpoints

Base URL: `/api/`

All API endpoints require session or token authentication. Write operations require an appropriate role (see Permission column). Unauthenticated access to build logs is explicitly allowed.

### Resources

| Resource | Base URL | Read | Write |
|---|---|---|---|
| Users | `/api/users/` | Authenticated | Admin |
| User profiles | `/api/profiles/` | Authenticated | Admin |
| GPG keys | `/api/gpg-keys/` | Authenticated | Repo Admin |
| Repositories | `/api/repositories/` | Authenticated | Repo Admin |
| Repository subsets | `/api/repository-subsets/` | Authenticated | Repo Admin |
| Packages | `/api/packages/` | Authenticated | Build Admin |
| Builds (history) | `/api/builds/` | Authenticated | Read-only viewset |
| Build artifacts | `/api/artifacts/` | Authenticated | Build Admin |
| Tokens | `/api/tokens/` | Authenticated | Repo Admin |

### Notable Actions

| Endpoint | Method | Auth | Description |
|---|---|---|---|
| `/api/users/me/` | GET | Authenticated | Current user |
| `/api/gpg-keys/<pk>/public_key/` | GET | Authenticated | Download public key |
| `/api/gpg-keys/generate/` | POST | Repo Admin | Generate key pair |
| `/api/gpg-keys/import_key/` | POST | Repo Admin | Import key |
| `/api/repositories/<pk>/builds/` | GET | Authenticated | Repository builds |
| `/api/repositories/<pk>/subsets/` | GET | Authenticated | Repository subsets |
| `/api/packages/<pk>/start/` | POST | Build Admin | Start build |
| `/api/packages/<pk>/cancel/` | POST | Build Admin | Cancel build |
| `/api/packages/<pk>/commit/` | POST | Build Admin | Commit build |
| `/api/packages/<pk>/publish/` | POST | Build Admin | Publish build |
| `/api/packages/<pk>/logs/` | GET | **Public** | Build logs |
| `/api/builds/<pk>/logs/` | GET | **Public** | Build logs |
| `/api/auth/login/` | POST | — | DRF session login |
| `/api/auth/logout/` | POST | Authenticated | DRF session logout |
| `/api/git-branches/` | GET | Authenticated | List branches of a git URL |

## WebSocket Events

Connect to `ws://<host>/ws/notifications/` (authenticated session required).

### `client_updated`

Sent when a client checks in. Payload:

```json
{
  "type": "notification_message",
  "notification_type": "client_updated",
  "pk": 42,
  "hostname": "workstation.example.com",
  "serial_number": "ABC123",
  "installed_count": 18,
  "foreign_count": 0,
  "outdated_count": 2,
  "commit_outdated_count": 1,
  "installed_flatpaks": [...],
  "foreign_flatpaks": [...],
  "outdated_flatpaks": [...],
  "last_checkin": "May 13, 09:41"
}
```

## Client Check-in Protocol

Managed clients call `POST /api/client-checkin/` with a JSON body:

```json
{
  "hostname": "workstation.example.com",
  "serial_number": "ABC123",
  "flatpaks": ["org.example.App/x86_64/stable", "..."]
}
```

No authentication is required on this endpoint. The server compares the installed list against all active repositories and responds with outdated/foreign counts, then pushes a WebSocket update to all connected UI sessions.

## Releasing

Always use the release script — never do manual `git tag` + `git push`:

```bash
./release.sh              # patch bump (default)
./release.sh --minor      # minor bump
./release.sh --version X.Y.Z  # explicit version
./release.sh --dry-run    # preview only
```

The script bumps `version.py`, adds a `%changelog` entry to the RPM spec, commits, tags, and pushes, triggering the GitHub Actions RPM build workflow.



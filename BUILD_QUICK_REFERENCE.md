# Build System Quick Reference

## Build States Flow

```
pending → building → built → committing → committed → publishing → published
                                                                    
   ↓          ↓        ↓          ↓            ↓           ↓           ↓
[Create]  [Building] [Done]   [Validate]   [Ready]    [Deploy]   [Available]
```

## fmdc Commands

| Command | Description | Example |
|---------|-------------|---------|
| `create` | Create new build | `./fmdc create http://localhost:8000 beta org.app.Name` |
| `list` | List builds | `./fmdc list http://localhost:8000 --status built` |
| `commit` | Commit build | `./fmdc commit http://localhost:8000/api/v1/builds/1/` |
| `publish` | Publish build | `./fmdc publish http://localhost:8000/api/v1/builds/1/` |
| `cancel` | Cancel build | `./fmdc cancel http://localhost:8000/api/v1/builds/1/` |

## Environment Variables

```bash
export FMDC_TOKEN="your-api-token"
```

Or use `--token` flag with each command.

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/builds/` | POST | Create build |
| `/api/v1/builds/` | GET | List builds |
| `/api/v1/builds/{id}/` | GET | Get build details |
| `/api/v1/builds/{id}/commit/` | POST | Commit build |
| `/api/v1/builds/{id}/publish/` | POST | Publish build |
| `/api/v1/builds/{id}/cancel/` | POST | Cancel build |
| `/api/v1/builds/{id}/logs/` | GET | Get build logs |

## Build Types

### Git-based Build
```json
{
  "repository_id": 1,
  "app_id": "org.example.App",
  "git_repo_url": "https://github.com/user/repo.git",
  "git_branch": "main",
  "branch": "stable",
  "arch": "x86_64"
}
```
→ Builds automatically after creation

### Upload-based Build
```json
{
  "repository_id": 1,
  "app_id": "org.example.App",
  "branch": "stable",
  "arch": "x86_64"
}
```
→ Requires manual upload of OSTree objects

## Status Meanings

| Status | Can Commit? | Can Publish? | Description |
|--------|-------------|--------------|-------------|
| `pending` | No | No | Awaiting start |
| `building` | No | No | Build in progress |
| `built` | ✅ Yes | No | Build complete |
| `committing` | No | No | Validating |
| `committed` | No | ✅ Yes | Ready to publish |
| `publishing` | No | No | Publishing |
| `published` | No | No | Available |
| `failed` | No | No | Build failed |
| `cancelled` | No | No | Cancelled |

## Common Tasks

### Create and publish git build
```bash
# 1. Create
BUILD_URL=$(./fmdc create http://localhost:8000 beta org.app.Name \
  --git-url https://github.com/user/repo.git | grep "Build URL" | cut -d' ' -f3)

# 2. Wait for built status (monitor in UI)

# 3. Commit
./fmdc commit $BUILD_URL

# 4. Publish
./fmdc publish $BUILD_URL
```

### Check build status via API
```bash
curl http://localhost:8000/api/v1/builds/1/ \
  -H "Authorization: Token YOUR_TOKEN" | jq .status
```

### Get build logs
```bash
curl http://localhost:8000/api/v1/builds/1/logs/ \
  -H "Authorization: Token YOUR_TOKEN" | jq
```

## Directory Structure

```
repos/
├── build-repo/         # Shared build repository
├── beta/              # Target repository (beta)
│   └── config
├── beta.gpg          # GPG public key
├── stable/           # Target repository (stable)
│   └── config
└── stable.gpg       # GPG public key
```

## Requirements

- **Git builds**: flatpak-builder, git
- **Upload builds**: ostree
- **Signing**: gpg with configured keys
- **Services**: celery, redis, django, daphne

## Service Status Check

```bash
pgrep -f celery && echo "✓ Celery running" || echo "✗ Celery not running"
pgrep -f redis && echo "✓ Redis running" || echo "✗ Redis not running"
pgrep -f "manage.py runserver" && echo "✓ Django running" || echo "✗ Django not running"
pgrep -f daphne && echo "✓ Daphne running" || echo "✗ Daphne not running"
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "flatpak-builder not found" | `sudo apt install flatpak-builder` |
| "Build failed" | Check logs: `/api/v1/builds/{id}/logs/` |
| "Cannot commit build" | Build must be in `built` status |
| "Cannot publish build" | Build must be in `committed` status |
| Git clone timeout | Check network, increase timeout in tasks.py |

## Web UI Navigation

```
Dashboard
├── GPG Keys
│   └── [List, Create, Generate, Import, Delete]
├── Repositories
│   ├── [List, Create, Detail, Edit, Delete]
│   └── Repository Detail
│       ├── Subsets [Create, Edit, Delete]
│       └── Parent Repositories [Assign]
└── Builds
    ├── [List, Create]
    └── Build Detail
        ├── Logs (real-time)
        ├── Status
        └── Actions [Commit, Publish, Cancel]
```

## Key Concepts

- **build-repo**: Shared OSTree repo for all builds
- **Commit**: Validates build and extracts refs
- **Publish**: Moves from build-repo to target repo
- **Parent repos**: Child repos inherit from parents (no direct builds)
- **Real-time updates**: WebSocket connection on build detail page

## Example: Complete Workflow

```bash
# Terminal 1: Monitor logs
tail -f logs/celery.log

# Terminal 2: Create and manage build
./fmdc create http://localhost:8000 beta org.example.App \
  --git-url https://github.com/flathub/org.example.App.git \
  --print-output

# Wait for status: "built" (check web UI)

./fmdc commit http://localhost:8000/api/v1/builds/1/

# Wait for status: "committed"

./fmdc publish http://localhost:8000/api/v1/builds/1/

# Status should be: "published"
# App is now in beta repository
```

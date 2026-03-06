# Build System Usage Guide

## Overview

The flat-manager-django build system supports two modes of operation:
1. **Git-based builds**: Automatically build flatpaks from source using flatpak-builder
2. **Upload-based builds**: Upload pre-built flatpak bundles or OSTree commits

## Prerequisites

### For Git-based Builds
- `flatpak-builder` must be installed
- Git must be installed
- The git repository must contain a valid flatpak manifest file (`.yml`, `.yaml`, or `.json`)

### For Upload-based Builds
- `ostree` must be installed
- The `fmdc` client tool or direct API access

## Creating a Build (Web UI)

1. Navigate to **Builds** → **New Build**
2. Fill in the form:
   - **Repository**: Select target repository (only repos without parent repos)
   - **App ID**: Reverse DNS format (e.g., `org.example.MyApp`)
   - **Git Repository URL**: (Optional) For git-based builds
   - **Git Branch**: (Optional) Branch/tag to build (default: `master`)
   - **Branch**: Flatpak branch (e.g., `stable`, `beta`)
   - **Architecture**: Target architecture (e.g., `x86_64`)
3. Click **Create Build**

### Git-based Build Example
```
Repository: beta
App ID: org.example.Calculator
Git Repository URL: https://github.com/example/calculator-flatpak.git
Git Branch: main
Branch: stable
Architecture: x86_64
```

The build will automatically:
1. Clone the repository
2. Run flatpak-builder
3. Export to build-repo
4. Update status to 'built'

### Upload-based Build Example
```
Repository: beta
App ID: org.example.TextEditor
Git Repository URL: (leave empty)
Branch: stable
Architecture: x86_64
```

Then use fmdc to upload the package (see below).

## Build Workflow

### Git-based Build Flow
```
Create Build → Building (automatic) → Built → Commit → Committed → Publish → Published
```

### Upload-based Build Flow
```
Create Build → Upload Objects (manual) → Commit → Committed → Publish → Published
```

## Using the fmdc Client

### Installation
The fmdc client is located at `/home/mj/Ansible/flat-manager-django/fmdc` and requires Python 3.7+ with aiohttp:

```bash
pip install aiohttp
export FMDC_TOKEN="your-api-token-here"
# Or use --token flag
```

### Create a Build
```bash
./fmdc create http://localhost:8000 beta org.example.MyApp
# Returns: Build URL: http://localhost:8000/api/v1/builds/1/
```

With git repository:
```bash
./fmdc create http://localhost:8000 beta org.example.MyApp \
    --git-url https://github.com/example/myapp-flatpak.git \
    --git-branch main \
    --branch stable \
    --arch x86_64
```

### List Builds
```bash
# List all builds
./fmdc list http://localhost:8000

# Filter by repository
./fmdc list http://localhost:8000 --repo beta

# Filter by status
./fmdc list http://localhost:8000 --status built

# JSON output
./fmdc list http://localhost:8000 --print-output
```

### Commit a Build
After a build is in 'built' state (git builds) or after uploading all objects (upload builds):

```bash
./fmdc commit http://localhost:8000/api/v1/builds/1/
```

This validates the build and marks it ready for publishing.

### Publish a Build
After a build is in 'committed' state:

```bash
./fmdc publish http://localhost:8000/api/v1/builds/1/
```

This publishes the build from build-repo to the target repository.

### Cancel a Build
```bash
./fmdc cancel http://localhost:8000/api/v1/builds/1/
```

## API Usage

### Create Build (POST /api/v1/builds/)
```bash
curl -X POST http://localhost:8000/api/v1/builds/ \
  -H "Authorization: Token YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "repository_id": 1,
    "app_id": "org.example.MyApp",
    "git_repo_url": "https://github.com/example/myapp-flatpak.git",
    "git_branch": "main",
    "branch": "stable",
    "arch": "x86_64"
  }'
```

### Get Build Status (GET /api/v1/builds/{id}/)
```bash
curl http://localhost:8000/api/v1/builds/1/ \
  -H "Authorization: Token YOUR_TOKEN"
```

### Commit Build (POST /api/v1/builds/{id}/commit/)
```bash
curl -X POST http://localhost:8000/api/v1/builds/1/commit/ \
  -H "Authorization: Token YOUR_TOKEN"
```

### Publish Build (POST /api/v1/builds/{id}/publish/)
```bash
curl -X POST http://localhost:8000/api/v1/builds/1/publish/ \
  -H "Authorization: Token YOUR_TOKEN"
```

### Get Build Logs (GET /api/v1/builds/{id}/logs/)
```bash
curl http://localhost:8000/api/v1/builds/1/logs/ \
  -H "Authorization: Token YOUR_TOKEN"
```

## Build Status Reference

| Status | Description |
|--------|-------------|
| `pending` | Build created, awaiting start |
| `building` | Build in progress (git clone, flatpak-builder) |
| `built` | Build completed, ready to commit |
| `committing` | Validating build and creating refs |
| `committed` | Build validated, ready to publish |
| `publishing` | Publishing to target repository |
| `published` | Build published and available |
| `failed` | Build failed (check logs) |
| `cancelled` | Build cancelled by user |

## Troubleshooting

### Git Build Fails
- Check that flatpak-builder is installed: `which flatpak-builder`
- Verify the git repository is accessible
- Ensure the manifest file exists in the repository root
- Check build logs in the web UI or via API

### Publish Fails
- Verify the target repository exists and is initialized
- Check OSTree repository permissions
- Ensure GPG key is properly configured if using signing

### No flatpak-builder
Install flatpak-builder:
```bash
# Ubuntu/Debian
sudo apt install flatpak-builder

# Fedora
sudo dnf install flatpak-builder

# Arch
sudo pacman -S flatpak-builder
```

### Build Logs
Access detailed logs via:
1. Web UI: Build Detail page → Logs tab
2. API: GET `/api/v1/builds/{id}/logs/`
3. Database: `BuildLog` table

## Example: Complete Git Build Workflow

```bash
# 1. Create a git-based build via web UI or API
# (Automatically starts building)

# 2. Wait for build to complete (monitor via web UI)
# Status will change: pending → building → built

# 3. Commit the build
./fmdc commit http://localhost:8000/api/v1/builds/1/

# 4. Wait for commit to complete
# Status will change: committing → committed

# 5. Publish the build
./fmdc publish http://localhost:8000/api/v1/builds/1/

# 6. Wait for publish to complete
# Status will change: publishing → published

# 7. Build is now available in the repository
```

## Real-time Updates

Build status updates are available via WebSockets:

```javascript
const ws = new WebSocket('ws://localhost:8001/ws/build/<build_id>/');

ws.onmessage = function(event) {
    const data = JSON.parse(event.data);
    console.log('Build status:', data.status);
    console.log('Message:', data.message);
};
```

The build detail page automatically connects to WebSocket for real-time updates.

## Notes

- Git-based builds automatically start building after creation
- Upload-based builds require manual object upload via flat-manager API
- Only repositories without parent repos can have builds
- Build artifacts are stored in `repos/build-repo/`
- Published builds are available in their target repository
- Failed builds can be inspected via logs before deletion

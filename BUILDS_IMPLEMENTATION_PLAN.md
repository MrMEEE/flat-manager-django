# Builds Implementation Plan

This document outlines the complete implementation plan for the build system, modeled after flat-manager.

## Overview

The build system supports two modes:
1. **Git-based builds**: Build from source using flatpak-builder
2. **Upload-based builds**: Push pre-built packages (like flat-manager)

## Phase 1: Core Build Model & Database (CURRENT)

### Changes to Build Model
- ✅ Added `git_repo_url`, `git_branch`, `source_commit` fields
- ✅ Added validation to prevent builds on repos with parents
- ✅ Added auto-generation of `build_id`
- ⏳ Need to run migration

### Migration Required
```bash
python manage.py makemigrations
python manage.py migrate
```

## Phase 2: Build Management UI

### Views Needed
1. `BuildListView` - List all builds with filtering
2. `BuildCreateView` - Create new build (validates no parent repos)
3. `BuildDetailView` - Show build details, logs, status
4. `BuildDeleteView` - Cancel/delete builds

### Templates
1. `templates/flatpak/build_list.html`
2. `templates/flatpak/build_form.html`
3. `templates/flatpak/build_detail.html`
4. `templates/flatpak/build_confirm_delete.html`

### URLs
```python
path('builds/', BuildListView.as_view(), name='build_list'),
path('builds/create/', BuildCreateView.as_view(), name='build_create'),
path('builds/<pk>/', BuildDetailView.as_view(), name='build_detail'),
path('builds/<pk>/delete/', BuildDeleteView.as_view(), name='build_delete'),
```

## Phase 3: API Endpoints (flat-manager compatible)

### API Endpoints
```
POST   /api/v1/build                    - Create build
GET    /api/v1/build                    - List builds
GET    /api/v1/build/{id}               - Get build details
POST   /api/v1/build/{id}/build_ref     - Add ref to build
POST   /api/v1/build/{id}/commit        - Commit build
POST   /api/v1/build/{id}/publish       - Publish build
GET    /api/v1/build/{id}/commit        - Get commit job status
GET    /api/v1/build/{id}/publish       - Get publish job status
POST   /api/v1/build/{id}/upload        - Upload objects (multipart)
GET    /api/v1/build/{id}/missing_objects - Check missing objects
POST   /api/v1/build/{id}/purge         - Delete build
```

### Serializers Needed
- `BuildSerializer`
- `BuildRefSerializer`
- `JobSerializer`

## Phase 4: Celery Background Tasks

### Tasks
1. `build_from_git_task` - Clone repo, run flatpak-builder, export to OSTree
2. `commit_build_task` - Validate build, create refs
3. `publish_build_task` - Publish from build repo to main repo
4. `cleanup_build_task` - Clean up temporary build artifacts

### Build Directories Structure
```
build-repo/
├── {build_id}/
│   ├── upload/       # OSTree repo for uploads
│   ├── git-clone/    # Git checkout (for git builds)
│   ├── build-dir/    # flatpak-builder work directory
│   └── logs/         # Build logs
```

## Phase 5: Client Tool (fmdc)

### File: `fmdc` (Python script)
Commands:
- `fmdc create <manager_url> <repo> [app_id]` - Create new build
- `fmdc push <build_url> <local_repo> [branches]` - Upload pre-built packages
- `fmdc commit <build_url>` - Commit build
- `fmdc publish <build_url>` - Publish to repository
- `fmdc create-token <manager_url> <name>` - Create subset token
- `fmdc follow-job <job_url>` - Watch job progress

### Dependencies
```
aiohttp
gi (GObject Introspection)
OSTree Python bindings
```

## Phase 6: Build Workflow Implementation

### Git-Based Build Flow
1. User creates build with `git_repo_url`
2. Celery task clones repository
3. Runs flatpak-builder to build app
4. Exports to OSTree repository
5. Auto-commits and optionally publishes
6. Updates build status throughout

### Upload-Based Build Flow (flat-manager style)
1. Client calls `/api/v1/build` (creates build in "uploading" state)
2. Client checks `/api/v1/build/{id}/missing_objects` for needed objects
3. Client uploads objects via `/api/v1/build/{id}/upload` (multipart)
4. Client creates refs via `/api/v1/build/{id}/build_ref`
5. Client calls `/api/v1/build/{id}/commit` (validates, moves to "committing")
6. Commit job runs (flatpak build-commit-from)
7. Client calls `/api/v1/build/{id}/publish` (publishes to main repo)
8. Publish job runs (flatpak build-commit-from to main repo)

## Phase 7: Advanced Features

### Repository Lifecycle
- Builds in parent repos auto-flow to children
- Example: Development → Beta → Stable
- Each promotion can be manual or automatic

### Build Checks/Hooks
- Pre-commit validation hooks
- Post-publish hooks
- Integration with CI/CD

### Token Management
- Per-build tokens with limited scope
- Upload-only tokens
- Publish tokens

## Implementation Priority

1. **High Priority** (Core functionality):
   - Phase 1: Model updates ✅
   - Phase 2: Basic UI
   - Phase 3: API endpoints
   - Phase 5: Client tool (basic commands)

2. **Medium Priority**:
   - Phase 4: Celery tasks for git builds
   - Phase 6: Complete workflow
   
3. **Low Priority** (Advanced):
   - Phase 7: Lifecycle, hooks, advanced token management

## Dependencies to Install

```bash
# For flatpak-builder
sudo apt install flatpak-builder ostree

# Python packages (already have most)
pip install aiohttp tenacity

# For Python OSTree bindings
sudo apt install python3-gi gir1.2-ostree-1.0
```

## Security Considerations

1. **Validation**: Always validate repository permissions
2. **Isolation**: Build in isolated containers/namespaces
3. **Token Scoping**: Limit token capabilities
4. **Upload Limits**: Rate limiting and size limits
5. **Parent Repo Rule**: Enforce at model and API level

## Testing Strategy

1. Unit tests for model validation
2. Integration tests for API endpoints
3. End-to-end tests with fmdc client
4. Test both git and upload workflows
5. Test repository hierarchy

## Documentation Needed

1. Build system architecture diagram
2. API documentation
3. fmdc client documentation
4. Git build configuration guide
5. Token management guide

## Next Steps

1. Run migrations to apply Build model changes
2. Restart Django server
3. Create build list/create/detail views
4. Create basic API endpoints
5. Start fmdc client tool development

---

**Status**: Phase 1 complete (model updates), ready for Phase 2 (UI) and Phase 3 (API)

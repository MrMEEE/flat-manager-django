# Build System Implementation - Phase 6 Complete

## Summary

Successfully implemented **Phase 6: Celery Background Tasks** for the flat-manager-django build system.

## What Was Completed

### 1. Core Celery Tasks (apps/flatpak/tasks.py)

#### build_from_git_task
- Clones git repository
- Runs flatpak-builder to build from source
- Exports to build-repo OSTree repository
- Tracks source commit hash
- Comprehensive error handling and logging
- Automatic temp directory cleanup

Key features:
- Validates git repository URL
- Finds manifest file automatically (`.yml`, `.yaml`, `.json`)
- 30-minute timeout for builds
- Real-time status updates via WebSockets
- Detailed build logs

#### commit_build_task
- Validates build completion
- Checks OSTree refs in build-repo
- Extracts commit hash
- Marks build as committed and ready for publish
- Compatible with both git and upload workflows

#### publish_build_task
- Pulls commits from build-repo to target repository
- Updates repository summary
- Signs repository with GPG if configured
- Marks build as published
- Full error handling and rollback

### 2. API Integration (apps/api/views.py)

Updated BuildViewSet actions:
- **perform_create**: Auto-starts git builds after creation
- **start**: Triggers git-based builds manually
- **commit**: Calls commit_build_task
- **publish**: Calls publish_build_task with proper status validation

### 3. Web UI Integration (apps/flatpak/views.py)

Updated BuildCreateView:
- Automatically triggers build_from_git_task for git-based builds
- Displays appropriate success message for git vs upload builds

### 4. Database Updates

Added migrations:
- **0007**: Updated Build.STATUS_CHOICES with new workflow states
  - `built`: Build completed, ready to commit
  - `committing`: Validating and committing
  - `committed`: Ready to publish
  - `published`: Available in repository
- **0008**: Added `published_at` timestamp field

### 5. Model Updates (apps/flatpak/models.py)

- Expanded STATUS_CHOICES to support full workflow
- Added `published_at` field for tracking
- Updated BuildSerializer with all new fields

### 6. Documentation

Created comprehensive guides:
- **BUILD_SYSTEM_USAGE.md**: Complete user guide with examples
- **BUILDS_IMPLEMENTATION_PLAN.md**: Technical implementation roadmap

### 7. Build Workflow

#### Git-based Build Flow
```
Create Build → Building (automatic) → Built → Commit → Committed → Publish → Published
```

Process:
1. User creates build with git URL via UI or API
2. System automatically clones repo and runs flatpak-builder
3. Build exports to build-repo
4. User commits build (validates refs)
5. User publishes build (pulls to target repo)

#### Upload-based Build Flow
```
Create Build → Upload Objects → Commit → Committed → Publish → Published
```

Process:
1. User creates build without git URL
2. User uploads OSTree objects via API
3. User creates build refs
4. User commits build
5. User publishes build

## Files Modified

1. `/home/mj/Ansible/flat-manager-django/apps/flatpak/tasks.py` - Complete rewrite with production tasks
2. `/home/mj/Ansible/flat-manager-django/apps/api/views.py` - Updated BuildViewSet actions
3. `/home/mj/Ansible/flat-manager-django/apps/flatpak/views.py` - Updated BuildCreateView
4. `/home/mj/Ansible/flat-manager-django/apps/flatpak/models.py` - Added published_at, updated STATUS_CHOICES
5. `/home/mj/Ansible/flat-manager-django/apps/api/serializers.py` - Updated BuildSerializer fields
6. `/home/mj/Ansible/flat-manager-django/fmdc` - Made executable (chmod +x)

## Files Created

1. `/home/mj/Ansible/flat-manager-django/BUILD_SYSTEM_USAGE.md` - User documentation
2. Migration `0007_alter_build_status.py` - Status choices update
3. Migration `0008_build_published_at.py` - Published timestamp field

## Technical Details

### OSTree Integration
- Builds export to shared `build-repo` repository
- `ostree pull-local` transfers commits to target repos
- Summary signing with GPG for verified repositories
- Automatic ref validation

### Celery Integration
- All tasks use shared_task decorator
- WebSocket notifications via Channels
- Detailed logging to BuildLog model
- Proper exception handling and status updates

### Security
- Validates repository permissions
- Prevents builds on child repositories
- Temp directory cleanup
- GPG key integration for signing

## Current System Status

✅ All migrations applied (0001-0008)  
✅ Celery worker running (14 processes)  
✅ Redis running  
✅ Django dev server running  
✅ Daphne WebSocket server running  
✅ fmdc client tool executable  

## Next Steps (Phase 7 - Future Enhancements)

1. **Upload-based Builds**: Implement OSTree object upload endpoints
2. **Build Artifacts**: Store and serve built packages
3. **Repository Lifecycle**: Auto-flow builds from parent to child repos
4. **Build Checks**: Pre-publish validation hooks
5. **Advanced Features**:
   - Build dependencies
   - Multi-arch builds
   - Build caching
   - Incremental builds

## Testing Requirements

To test the build system, you'll need:

1. **flatpak-builder** installed:
   ```bash
   sudo apt install flatpak-builder  # Ubuntu/Debian
   ```

2. **Test repository with flatpak manifest**:
   - Any git repo with a `.yml`/`.yaml`/`.json` manifest
   - Example: https://github.com/flathub/org.example.App

3. **GPG key configured** (optional):
   - For signed repositories
   - Key must be in system GPG keyring

## Example Usage

### Create a Git-based Build

Via Web UI:
1. Go to Builds → New Build
2. Select repository (e.g., "beta")
3. Enter app ID (e.g., "org.example.App")
4. Enter git URL
5. Click "Create Build"
6. Build starts automatically

Via fmdc:
```bash
./fmdc create http://localhost:8000 beta org.example.App \
  --git-url https://github.com/example/app.git \
  --git-branch main
```

Via API:
```bash
curl -X POST http://localhost:8000/api/v1/builds/ \
  -H "Authorization: Token YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "repository_id": 1,
    "app_id": "org.example.App",
    "git_repo_url": "https://github.com/example/app.git",
    "git_branch": "main",
    "branch": "stable",
    "arch": "x86_64"
  }'
```

### Commit and Publish

```bash
# Wait for build to complete (status: built)
./fmdc commit http://localhost:8000/api/v1/builds/1/

# Wait for commit (status: committed)
./fmdc publish http://localhost:8000/api/v1/builds/1/

# Build is now published (status: published)
```

## Notes

- Git builds are fully automated from creation to built status
- Upload builds require manual object upload (TODO in Phase 7)
- All operations are async via Celery for responsiveness
- WebSocket updates provide real-time status in web UI
- Build logs are stored in database for debugging
- Temp directories are automatically cleaned up
- Failed builds preserve error messages and logs

## Limitations

- flatpak-builder required for git builds (not checked at build creation)
- No pre-build validation of manifest files
- Upload-based builds not fully implemented (API stubs in place)
- No build artifact storage/retrieval yet
- No multi-arch build coordination

## Performance Considerations

- Git clones are depth=1 for speed
- flatpak-builder has 30-minute timeout
- Build-repo is shared to save disk space
- Temp directories use system tmp for cleanup
- Celery workers can be scaled for concurrency

---

**Status**: Phase 6 Complete ✅  
**Next Phase**: Phase 7 - Advanced Features (Optional)  
**System**: Fully functional for git-based builds  

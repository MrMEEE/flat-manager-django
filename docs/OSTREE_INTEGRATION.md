# OSTree Repository Integration

## Overview
flat-manager-django now automatically creates and manages OSTree repositories on the filesystem when repositories are created through the web UI or API. This matches the behavior of the original flat-manager.

## Features Implemented

### Automatic OSTree Initialization
When a repository is created:
1. **OSTree repository created** in `repos/<repository-name>/` 
2. **Collection ID configured** if provided
3. **GPG signing enabled** if a GPG key is selected
4. **Public key exported** to the repository folder for client use
5. **Repository summary signed** with the GPG key

### Repository Path Management
- Base path configurable via `REPOS_BASE_PATH` setting
- Default: `<project_root>/repos/`
- Each repository gets its own directory: `repos/<repo_name>/`
- Path displayed in UI and API

### GPG Signing
If a GPG key is assigned to the repository:
- Private key imported to system GPG keyring for signing
- OSTree configured with: `core.gpg-sign=<key_id>`
- Repository summary is signed automatically
- Public key exported to: `repos/<repo_name>/<repo_name>.gpg`

## Configuration

### Settings (`config/settings.py`)
```python
# Flatpak Repository Configuration
REPOS_BASE_PATH = os.path.join(BASE_DIR, 'repos')
os.makedirs(REPOS_BASE_PATH, exist_ok=True)
```

Can be overridden with environment variable or custom settings.

### Repository Model
New properties added:
```python
@property
def repo_path(self):
    """Get the filesystem path for this repository."""
    return os.path.join(settings.REPOS_BASE_PATH, self.name)

def get_public_key_path(self):
    """Get the path where the public GPG key should be stored."""
    if self.gpg_key:
        return os.path.join(self.repo_path, f"{self.name}.gpg")
    return None
```

## Usage

### Creating a Repository (Web UI)
1. Navigate to **Repositories** → **New Repository**
2. Enter repository details:
   - **Name**: Unique identifier (will be directory name)
   - **Collection ID**: Reverse DNS format (e.g., `org.example.MyApp`)
   - **Description**: Optional
   - **GPG Key**: Select a key for signing (optional)
3. Click **Create Repository**
4. OSTree repository is automatically initialized at `repos/<name>/`

### Checking Repository Details
Repository detail page now shows:
- **Repository Path**: Full filesystem path
- **Public Key Path**: Path to exported GPG public key (if key assigned)
- All other repository metadata

### Repository List View
Shows repository path under each repository name for quick reference.

## OSTree Utility Functions

Located in `apps/flatpak/utils/ostree.py`:

### `init_ostree_repo(repo_path, collection_id=None, gpg_key=None)`
Initialize a new OSTree repository with optional signing.

### `sign_repo_summary(repo_path, gpg_key_id)`
Sign the repository summary file.

### `delete_ostree_repo(repo_path)`
Remove repository from disk.

### `get_repo_info(repo_path)`
Get repository configuration and statistics.

### `check_ostree_available()`
Verify OSTree is installed.

## API Integration

### Repository API Endpoints
Now include additional fields:
```json
{
  "id": 1,
  "name": "my-repo",
  "collection_id": "org.example.MyApp",
  "repo_path": "/path/to/flat-manager-django/repos/my-repo",
  "public_key_path": "/path/to/flat-manager-django/repos/my-repo/my-repo.gpg",
  "gpg_key": {...},
  "subsets": [...],
  ...
}
```

## OSTree Repository Structure

Each repository follows standard OSTree structure:
```
repos/
└── my-repo/
    ├── config              # OSTree configuration
    ├── extensions/         # Repository extensions
    ├── objects/           # Content objects
    ├── refs/              # Named references
    │   ├── heads/
    │   ├── mirrors/
    │   └── remotes/
    ├── state/             # Repository state
    ├── tmp/               # Temporary files
    └── my-repo.gpg        # Public GPG key (if signing enabled)
```

### Repository Config Example
```ini
[core]
repo_version=1
mode=archive-z2
collection-id=org.example.MyApp
sign-verify=true
gpg-sign=5BC307B02C178C23
```

## Requirements

### System Dependencies
- **ostree**: OSTree command-line tool
  ```bash
  # Ubuntu/Debian
  sudo apt-get install ostree
  
  # Fedora
  sudo dnf install ostree
  ```

- **GPG**: GnuPG for signing (usually pre-installed)

### Python Dependencies
Already included in `requirements.txt`:
- Django 5.0+
- All existing dependencies

## File Permissions

The Django process needs:
- **Write access** to `REPOS_BASE_PATH`
- **Read/Write access** to GPG keyring (~/.gnupg)
- **Execute permissions** for `ostree` command

## Security Considerations

1. **GPG Private Keys**: Stored in database, imported to system keyring for signing
2. **Repository Access**: No authentication on OSTree repos (add nginx/apache layer)
3. **File Permissions**: Ensure proper permissions on `repos/` directory
4. **Backup**: Include `repos/` directory in backup strategy

## Testing

### Manual Test
```bash
# Create a repository through the UI or API
# Then verify:
ls -la repos/<repo-name>/
ostree config --repo repos/<repo-name>/ get core.collection-id
ostree refs --repo repos/<repo-name>/
```

### Verify GPG Signing
```bash
# Check if GPG key is configured
ostree config --repo repos/<repo-name>/ get core.gpg-sign

# Verify public key exists
ls -lh repos/<repo-name>/*.gpg
```

## Troubleshooting

### "ostree: command not found"
Install OSTree:
```bash
sudo apt-get install ostree
```

### GPG Signing Fails
1. Check if GPG key exists: `gpg --list-keys`
2. Verify private key is available: `gpg --list-secret-keys`
3. Check Django logs for detailed error messages

### Permission Denied
```bash
# Ensure proper permissions
chmod 755 repos/
chown -R <django-user>:<django-group> repos/
```

## Future Enhancements

Planned features:
- [ ] Auto-cleanup of old repository data
- [ ] Repository size tracking
- [ ] Integrity verification checks
- [ ] Support for repository mirroring
- [ ] HTTP serving of repositories (via nginx)
- [ ] Delta generation for updates
- [ ] Repository replication

## Compatibility

This implementation follows flat-manager's architecture:
- Same OSTree repository structure
- Compatible with flatpak clients
- Same collection-id mechanism
- Same GPG signing approach

Clients can use these repositories with standard flatpak commands:
```bash
# Add repository
flatpak remote-add --user my-repo <url> --gpg-import=<path-to-gpg-key>

# Install from repository
flatpak install my-repo org.example.MyApp
```

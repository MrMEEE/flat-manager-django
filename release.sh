#!/usr/bin/env bash
#
# flat-manager-django release wrapper
#
# Bumps the version, updates version.py + the RPM spec changelog,
# then commits, tags, and pushes — which triggers the GitHub Actions
# RPM build workflow.
#
# Usage:
#   ./release.sh                    # patch bump (0.1.0 → 0.1.1)
#   ./release.sh --minor            # minor bump (0.1.1 → 0.2.0)
#   ./release.sh --major            # major bump (0.2.0 → 1.0.0)
#   ./release.sh --version 2.0.0    # explicit version
#   ./release.sh --dry-run          # preview only, no changes
#   ./release.sh --help             # show all options
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON=""

# Prefer the project venv if it exists
if [[ -x "$SCRIPT_DIR/venv/bin/python" ]]; then
    PYTHON="$SCRIPT_DIR/venv/bin/python"
elif command -v python3 &>/dev/null; then
    PYTHON="python3"
elif command -v python &>/dev/null; then
    PYTHON="python"
else
    echo "ERROR: No Python interpreter found." >&2
    exit 1
fi

exec "$PYTHON" "$SCRIPT_DIR/tools/release.py" "$@"

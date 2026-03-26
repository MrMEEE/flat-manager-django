#!/usr/bin/env bash
# setup-bst1-venv.sh — Create a Python 3.11 virtualenv with BuildStream 1
#
# BuildStream 1.x requires Python <= 3.11.  configparser.SafeConfigParser was
# removed in Python 3.12, which breaks the BST1 build/install machinery.
#
# Usage:
#   ./setup-bst1-venv.sh [VENV_PATH]
#
# VENV_PATH defaults to /opt/flat-manager/bst1-venv (RPM install location).
# For development you can pass a local path, e.g.:
#   ./setup-bst1-venv.sh ./venv-bst1

set -euo pipefail

VENV_PATH="${1:-/opt/flat-manager/bst1-venv}"

# ── Find Python 3.11 ──────────────────────────────────────────────────────────
PY311=""
for candidate in python3.11 python3.11-debug; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PY311="$candidate"
        break
    fi
done

if [ -z "$PY311" ]; then
    echo "Python 3.11 not found. Attempting to install it..."
    if command -v apt-get >/dev/null 2>&1; then
        # Debian / Ubuntu — python3.11 is in universe on 22.04+ and 24.04
        if ! sudo apt-get install -y python3.11 python3.11-venv python3.11-dev; then
            echo ""
            echo "apt install failed. Try adding the deadsnakes PPA first:"
            echo "  sudo add-apt-repository ppa:deadsnakes/ppa"
            echo "  sudo apt-get update"
            echo "  sudo apt-get install python3.11 python3.11-venv python3.11-dev"
            exit 1
        fi
        PY311="python3.11"
    elif command -v dnf >/dev/null 2>&1; then
        # RHEL / Fedora / CentOS Stream — python3.11 is in AppStream on RHEL9+
        sudo dnf install -y python3.11
        PY311="python3.11"
    else
        echo "ERROR: Cannot auto-install Python 3.11 — unknown package manager."
        echo "Please install Python 3.11 manually, then re-run this script."
        exit 1
    fi
fi

PY_VER=$("$PY311" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
echo "Using $PY311 ($PY_VER)"

if python3 -c "import sys; exit(0 if sys.version_info < (3,12) else 1)" 2>/dev/null; then
    :
else
    # Verify it's actually <= 3.11
    MAJOR=$("$PY311" -c 'import sys; print(sys.version_info.major)')
    MINOR=$("$PY311" -c 'import sys; print(sys.version_info.minor)')
    if [ "$MAJOR" -gt 3 ] || { [ "$MAJOR" -eq 3 ] && [ "$MINOR" -gt 11 ]; }; then
        echo "ERROR: $PY311 is Python $PY_VER which is > 3.11."
        echo "BuildStream 1 requires Python <= 3.11."
        exit 1
    fi
fi

# ── Create venv ───────────────────────────────────────────────────────────────
echo "Creating venv at: $VENV_PATH"
"$PY311" -m venv "$VENV_PATH"
"$VENV_PATH/bin/python" -m ensurepip --upgrade 2>/dev/null || true
"$VENV_PATH/bin/pip" install --upgrade pip --quiet

# ── Install BuildStream 1 ─────────────────────────────────────────────────────
# Pin to the 1.6.x stable series.  1.9x.dev builds are early BST 2 pre-releases
# and are NOT compatible with BST1 project.conf files.
echo "Installing BuildStream 1.x (stable) ..."
"$VENV_PATH/bin/pip" install 'BuildStream>=1.0,<1.7'

BST_VER=$("$VENV_PATH/bin/bst" --version 2>/dev/null || echo "unknown")
echo ""
echo "Done! BuildStream $BST_VER installed at $VENV_PATH"
echo ""
echo "Set this path in flat-manager-django:"
echo "  • Config page: Settings → BuildStream 1 → Venv path"
echo "  • Or in config/settings.py: BST1_VENV_PATH = '$VENV_PATH'"

"""
OSTree repository utilities for flat-manager.
"""
import os
import stat
import subprocess
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def temp_gpg_homedir(gpg_key):
    """
    Context manager that creates a temporary GPG homedir with the key imported.

    Yields the path to the temp dir.  Cleans up automatically on exit.

    Usage::

        with temp_gpg_homedir(repo.gpg_key) as homedir:
            sign_repo_summary(repo_path, repo.gpg_key.key_id, gpg_homedir=homedir)
    """
    tmpdir = tempfile.mkdtemp(prefix='flatmgr_gpg_')
    try:
        os.chmod(tmpdir, stat.S_IRWXU)  # 700 — required by GnuPG
        if gpg_key and gpg_key.private_key:
            private_key_data = gpg_key.private_key
            if isinstance(private_key_data, bytes):
                private_key_data = private_key_data.decode('utf-8')
            subprocess.run(
                ['gpg', '--homedir', tmpdir, '--batch', '--import'],
                input=private_key_data,
                capture_output=True,
                text=True,
            )
        yield tmpdir
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def init_ostree_repo(repo_path, collection_id=None, gpg_key=None):
    """
    Initialize an OSTree repository.
    
    Args:
        repo_path: Path where the repository should be created
        collection_id: Optional collection ID for the repository
        gpg_key: Optional GPGKey model instance for signing
    
    Returns:
        dict with 'success' (bool), 'message' (str), and optional 'error' (str)
    """
    try:
        # Create directory if it doesn't exist
        os.makedirs(repo_path, exist_ok=True)
        
        # Initialize OSTree repository
        cmd = ['ostree', 'init', '--repo', repo_path, '--mode=archive-z2']
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        
        # Set collection ID if provided
        if collection_id:
            cmd = ['ostree', 'config', 'set', '--repo', repo_path, 
                   'core.collection-id', collection_id]
            subprocess.run(cmd, capture_output=True, text=True, check=True)
        
        # Configure GPG signing if key is provided
        if gpg_key:
            # Import GPG keys to system keyring for OSTree
            gpg_homedir = os.path.expanduser('~/.gnupg')
            
            # Import the private key (needed for signing)
            if gpg_key.private_key:
                import_cmd = ['gpg', '--batch', '--import']
                # Ensure private_key is string, not bytes
                private_key_data = gpg_key.private_key
                if isinstance(private_key_data, bytes):
                    private_key_data = private_key_data.decode('utf-8')
                
                import_result = subprocess.run(
                    import_cmd,
                    input=private_key_data,
                    capture_output=True,
                    text=True
                )
                # Ignore errors if key already exists
            
            # Configure OSTree to use this key for signing
            cmd = ['ostree', 'config', 'set', '--repo', repo_path,
                   'core.sign-verify', 'true']
            subprocess.run(cmd, capture_output=True, text=True, check=True)
            
            # Set the GPG key ID for signing
            cmd = ['ostree', 'config', 'set', '--repo', repo_path,
                   'core.gpg-sign', gpg_key.key_id]
            subprocess.run(cmd, capture_output=True, text=True, check=True)
            
            # Export public key to repos/ root folder
            repos_base = os.path.dirname(repo_path)
            public_key_path = os.path.join(repos_base, f"{os.path.basename(repo_path)}.gpg")
            with open(public_key_path, 'w') as f:
                f.write(gpg_key.public_key)
            
            # Create and sign initial summary
            with temp_gpg_homedir(gpg_key) as homedir:
                sign_repo_summary(repo_path, gpg_key.key_id, gpg_homedir=homedir)
        else:
            # Create initial summary without signing
            cmd = ['ostree', 'summary', '--update', '--repo', repo_path]
            subprocess.run(cmd, capture_output=True, text=True, check=True)
        
        return {
            'success': True,
            'message': f'OSTree repository initialized at {repo_path}'
        }
    
    except subprocess.CalledProcessError as e:
        return {
            'success': False,
            'message': 'Failed to initialize OSTree repository',
            'error': f'{e.stderr}'
        }
    except Exception as e:
        return {
            'success': False,
            'message': 'Failed to initialize OSTree repository',
            'error': str(e)
        }


def update_repo_metadata(repo_path, gpg_key=None):
    """
    Regenerate OSTree repository metadata (appstream, summary, static deltas)
    and GPG-sign everything in the correct order.

    The order matters:
    1. Remove stale unsigned static deltas so flatpak build-update-repo
       is forced to regenerate them WITH embedded GPG signatures.
    2. Run ``flatpak build-update-repo --generate-static-deltas --gpg-sign``
       which creates new GPG-signed delta superblocks and signs the summary.
    3. Sign every commit individually with ``ostree gpg-sign`` so that
       non-delta pulls (e.g. first-install fallback) can also verify.

    Args:
        repo_path: Path to the OSTree repository
        gpg_key:   Optional GPGKey model instance. When None the repo is
                   updated without signing.

    Returns:
        dict with 'success' (bool), 'message' (str), and optional 'detail'
        / 'error' strings.
    """
    import logging
    logger = logging.getLogger(__name__)

    try:
        # Step 1: Purge stale static deltas.  build-update-repo skips existing
        # delta superblocks, so any unsigned ones would survive and break GPG
        # verification for static-delta pulls.
        for subdir in ('deltas', 'delta-indexes'):
            stale = os.path.join(repo_path, subdir)
            if os.path.isdir(stale):
                shutil.rmtree(stale)
                logger.debug("Removed stale delta dir: %s", stale)

        # Step 2: Regenerate appstream metadata, deltas, and summary.
        if gpg_key:
            with temp_gpg_homedir(gpg_key) as homedir:
                cmd = [
                    'flatpak', 'build-update-repo',
                    '--generate-static-deltas',
                    f'--gpg-sign={gpg_key.key_id}',
                    f'--gpg-homedir={homedir}',
                    repo_path,
                ]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        else:
            result = subprocess.run(
                ['flatpak', 'build-update-repo', '--generate-static-deltas', repo_path],
                capture_output=True, text=True, timeout=300
            )

        if result.returncode != 0:
            logger.warning("flatpak build-update-repo failed: %s", result.stderr)
            # Fallback: at least keep the summary signed
            if gpg_key:
                with temp_gpg_homedir(gpg_key) as homedir:
                    sign_repo_summary(repo_path, gpg_key.key_id, gpg_homedir=homedir)
            else:
                subprocess.run(
                    ['ostree', 'summary', '-u', f'--repo={repo_path}'],
                    capture_output=True, text=True
                )
            return {
                'success': False,
                'message': 'flatpak build-update-repo failed; summary refreshed via fallback',
                'detail': result.stderr,
            }

        # Step 3: Sign every individual commit so non-delta pulls verify OK.
        if gpg_key:
            refs_result = subprocess.run(
                ['ostree', '--repo=' + repo_path, 'refs', '--list'],
                capture_output=True, text=True
            )
            for ref in refs_result.stdout.splitlines():
                ref = ref.strip()
                if not ref:
                    continue
                rev_result = subprocess.run(
                    ['ostree', '--repo=' + repo_path, 'rev-parse', ref],
                    capture_output=True, text=True
                )
                commit = rev_result.stdout.strip()
                if commit:
                    with temp_gpg_homedir(gpg_key) as homedir:
                        subprocess.run(
                            ['ostree', '--repo=' + repo_path, 'gpg-sign',
                             f'--gpg-homedir={homedir}', commit, gpg_key.key_id],
                            capture_output=True, text=True
                        )

        return {
            'success': True,
            'message': 'Repository metadata updated and signed successfully',
        }

    except Exception as e:
        logger.exception("update_repo_metadata failed for %s", repo_path)
        return {
            'success': False,
            'message': 'update_repo_metadata failed',
            'error': str(e),
        }


def sign_repo_summary(repo_path, gpg_key_id, gpg_homedir=None):
    """
    Sign the repository summary file.

    Args:
        repo_path: Path to the OSTree repository
        gpg_key_id: GPG key ID to use for signing
        gpg_homedir: Optional path to GPG homedir.  When omitted the
            system default (~/.gnupg) is used.  Pass the value yielded
            by ``temp_gpg_homedir`` to use a private temporary dir with
            the key imported from the database.

    Returns:
        dict with 'success' (bool) and optional 'error' (str)
    """
    try:
        homedir = gpg_homedir if gpg_homedir else os.path.expanduser('~/.gnupg')
        # Update and sign the summary
        cmd = ['ostree', 'summary', '--update', '--repo', repo_path,
               '--gpg-sign', gpg_key_id, '--gpg-homedir', homedir]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        
        return {
            'success': True,
            'message': 'Repository summary signed successfully'
        }
    
    except subprocess.CalledProcessError as e:
        return {
            'success': False,
            'message': 'Failed to sign repository summary',
            'error': f'{e.stderr}'
        }
    except Exception as e:
        return {
            'success': False,
            'message': 'Failed to sign repository summary',
            'error': str(e)
        }


def delete_ostree_repo(repo_path):
    """
    Delete an OSTree repository from disk.
    
    Args:
        repo_path: Path to the OSTree repository
    
    Returns:
        dict with 'success' (bool) and optional 'error' (str)
    """
    try:
        if os.path.exists(repo_path):
            shutil.rmtree(repo_path)
        
        return {
            'success': True,
            'message': f'Repository deleted from {repo_path}'
        }
    
    except Exception as e:
        return {
            'success': False,
            'message': 'Failed to delete repository',
            'error': str(e)
        }


def check_ostree_available():
    """
    Check if ostree command is available.
    
    Returns:
        bool: True if ostree is available, False otherwise
    """
    try:
        subprocess.run(['ostree', '--version'], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def get_repo_info(repo_path):
    """
    Get information about an OSTree repository.
    
    Args:
        repo_path: Path to the OSTree repository
    
    Returns:
        dict with repository information or error
    """
    try:
        if not os.path.exists(repo_path):
            return {'success': False, 'error': 'Repository does not exist'}
        
        info = {}
        
        # Get collection ID
        cmd = ['ostree', 'config', 'get', '--repo', repo_path, 'core.collection-id']
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            info['collection_id'] = result.stdout.strip()
        
        # Get GPG signing key
        cmd = ['ostree', 'config', 'get', '--repo', repo_path, 'core.gpg-sign']
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            info['gpg_key_id'] = result.stdout.strip()
        
        # Check if repository exists and is valid
        cmd = ['ostree', 'refs', '--repo', repo_path]
        result = subprocess.run(cmd, capture_output=True, text=True)
        info['ref_count'] = len(result.stdout.strip().split('\n')) if result.stdout.strip() else 0
        
        return {
            'success': True,
            'info': info
        }
    
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }

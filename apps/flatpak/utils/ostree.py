"""
OSTree repository utilities for flat-manager.
"""
import os
import subprocess
import shutil
from pathlib import Path


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
            sign_repo_summary(repo_path, gpg_key.key_id)
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


def sign_repo_summary(repo_path, gpg_key_id):
    """
    Sign the repository summary file.
    
    Args:
        repo_path: Path to the OSTree repository
        gpg_key_id: GPG key ID to use for signing
    
    Returns:
        dict with 'success' (bool) and optional 'error' (str)
    """
    try:
        # Update and sign the summary
        cmd = ['ostree', 'summary', '--update', '--repo', repo_path,
               '--gpg-sign', gpg_key_id, '--gpg-homedir', os.path.expanduser('~/.gnupg')]
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

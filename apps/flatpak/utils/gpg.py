"""
GPG key generation utilities.
"""
import gnupg
import os
from django.conf import settings


def generate_gpg_key(name, email, passphrase=None, key_type='RSA', key_length=4096, comment=''):
    """
    Generate a new GPG key pair.
    
    Args:
        name: Name for the key
        email: Email for the key
        passphrase: Passphrase to protect the private key (optional)
        key_type: Key type (default: RSA)
        key_length: Key length in bits (default: 4096)
        comment: Optional comment
    
    Returns:
        dict with:
            - key_id: Short key ID
            - fingerprint: Full fingerprint
            - public_key: ASCII armored public key
            - private_key: ASCII armored private key
    """
    import tempfile
    import shutil
    import os
    import subprocess
    
    temp_dir = tempfile.mkdtemp(prefix='gpg_')
    
    try:
        # Create a batch file for unattended key generation
        batch_file = os.path.join(temp_dir, 'keygen.batch')
        with open(batch_file, 'w') as f:
            f.write(f"Key-Type: {key_type}\n")
            f.write(f"Key-Length: {key_length}\n")
            f.write(f"Name-Real: {name}\n")
            f.write(f"Name-Email: {email}\n")
            if comment:
                f.write(f"Name-Comment: {comment}\n")
            f.write("Expire-Date: 0\n")
            f.write("%no-protection\n")  # No passphrase
            f.write("%commit\n")
        
        # Run gpg directly with batch mode
        env = os.environ.copy()
        env['GNUPGHOME'] = temp_dir
        
        result = subprocess.run(
            ['gpg', '--batch', '--gen-key', batch_file],
            env=env,
            capture_output=True,
            text=True,
            timeout=120
        )
        
        if result.returncode != 0:
            raise Exception(f"GPG key generation failed: {result.stderr}")
        
        # Export keys using subprocess to avoid python-gnupg passphrase issues
        # List keys to get fingerprint
        list_result = subprocess.run(
            ['gpg', '--list-keys', '--with-colons'],
            env=env,
            capture_output=True,
            text=True
        )
        
        # Parse fingerprint from output
        fingerprint = None
        key_id = None
        for line in list_result.stdout.split('\n'):
            if line.startswith('fpr:'):
                fingerprint = line.split(':')[9]
            elif line.startswith('pub:'):
                key_id = line.split(':')[4][-16:]
        
        if not fingerprint:
            raise Exception("No keys found after generation")
        
        # Export public key
        pub_result = subprocess.run(
            ['gpg', '--armor', '--export', fingerprint],
            env=env,
            capture_output=True,
            text=True
        )
        public_key = pub_result.stdout
        
        # Export private key with loopback pinentry and empty passphrase
        priv_result = subprocess.run(
            ['gpg', '--armor', '--export-secret-keys', '--pinentry-mode', 'loopback', '--passphrase', '', fingerprint],
            env=env,
            capture_output=True,
            text=True
        )
        private_key = priv_result.stdout
        
        if not public_key or not private_key:
            raise Exception(f"Failed to export generated keys. Errors: {pub_result.stderr} {priv_result.stderr}")
        
        return {
            'key_id': key_id,
            'fingerprint': fingerprint,
            'public_key': public_key,
            'private_key': private_key
        }
    finally:
        # Clean up temporary directory
        shutil.rmtree(temp_dir, ignore_errors=True)


def import_gpg_key(public_key, private_key=None, passphrase=None):
    """
    Import and validate a GPG key.
    
    Args:
        public_key: ASCII armored public key
        private_key: ASCII armored private key (optional)
        passphrase: Passphrase for encrypted private key (optional)
    
    Returns:
        dict with key information
    """
    import tempfile
    import shutil
    
    temp_dir = tempfile.mkdtemp(prefix='gpg_')
    
    try:
        # Initialize GPG with custom home directory to avoid system keyring
        gpg = gnupg.GPG(gnupghome=temp_dir)
        
        # Import public key
        import_result = gpg.import_keys(public_key)
        
        if not import_result.fingerprints:
            raise Exception("Failed to import public key")
        
        fingerprint = import_result.fingerprints[0]
        
        # Import private key if provided
        if private_key:
            private_result = gpg.import_keys(private_key, passphrase=passphrase)
            if not private_result.fingerprints:
                raise Exception("Failed to import private key. Check passphrase if key is encrypted.")
        
        # Get key info
        keys = gpg.list_keys(keys=fingerprint)
        if not keys:
            raise Exception("Failed to retrieve imported key information")
        
        key_info = keys[0]
        
        return {
            'key_id': key_info['keyid'][-16:],
            'fingerprint': fingerprint,
            'uids': key_info.get('uids', []),
            'created': key_info.get('date', '')
        }
    finally:
        # Clean up temporary directory
        shutil.rmtree(temp_dir, ignore_errors=True)

"""
GPG key generation utilities.
"""
import gnupg
import os
from django.conf import settings


def _get_writable_temp_base() -> str:
    """Return a temp base directory that is writable by the current process."""
    import tempfile

    candidates = []
    configured = (getattr(settings, 'TEMP_DIR', '') or '').strip()
    if configured:
        candidates.append(configured)
    candidates.append(os.path.join(tempfile.gettempdir(), 'flat-manager'))

    errors = []
    for base in candidates:
        try:
            os.makedirs(base, exist_ok=True)
            probe = tempfile.mkdtemp(prefix='probe-', dir=base)
            os.chmod(probe, 0o700)
            os.rmdir(probe)
            return base
        except OSError as exc:
            errors.append(f"{base}: {exc}")

    details = '; '.join(errors) if errors else 'no candidate directories available'
    raise OSError(f"No writable temp directory for GPG operations ({details})")


def _mk_secure_tempdir(prefix: str) -> str:
    """Create a temp dir with explicit 0700 permissions (systemd UMask safe)."""
    import tempfile

    base = _get_writable_temp_base()
    temp_dir = tempfile.mkdtemp(prefix=prefix, dir=base)
    os.chmod(temp_dir, 0o700)
    return temp_dir


def _duration_to_date(duration):
    """
    Convert a GPG duration string ('0', '1y', '2y', '5y', '10y') to a
    datetime.date or None (when duration is '0' / falsy).
    """
    import datetime
    if not duration or duration == '0':
        return None
    if duration.endswith('y'):
        years = int(duration[:-1])
        today = datetime.date.today()
        try:
            return today.replace(year=today.year + years)
        except ValueError:
            # Feb 29 edge case → Feb 28
            return today.replace(year=today.year + years, day=28)
    return None


def generate_gpg_key(name, email, passphrase=None, key_type='RSA', key_length=4096,
                     comment='', expires='0'):
    """
    Generate a new GPG key pair.

    Args:
        name:       Name for the key
        email:      Email for the key
        passphrase: Passphrase to protect the private key (optional)
        key_type:   Key type (default: RSA)
        key_length: Key length in bits (default: 4096)
        comment:    Optional comment
        expires:    GPG Expire-Date value: '0' = never, '1y', '2y', '5y', '10y'

    Returns:
        dict with:
            - key_id: Short key ID
            - fingerprint: Full fingerprint
            - public_key: ASCII armored public key
            - private_key: ASCII armored private key
            - expires_at: datetime.date or None
    """
    import shutil
    import os
    import subprocess

    temp_dir = _mk_secure_tempdir('gpg_')
    
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
            f.write(f"Expire-Date: {expires or '0'}\n")
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
            'private_key': private_key,
            'expires_at': _duration_to_date(expires),
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
    import shutil

    temp_dir = _mk_secure_tempdir('gpg_')
    
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


def renew_gpg_key(gpg_key, duration):
    """
    Extend the expiry date of an existing GPG key.

    The expiry is embedded in the key material, so the public key is
    re-exported after the change and returned so the caller can persist it.

    Args:
        gpg_key:  GPGKey model instance (needs .private_key and .fingerprint)
        duration: GPG duration string — '0' = never, '1y', '2y', '5y', '10y'

    Returns:
        dict with:
            - public_key:  Updated ASCII armored public key
            - expires_at:  datetime.date or None
    """
    import shutil
    import subprocess as _sp

    temp_dir = _mk_secure_tempdir('gpg_renew_')
    try:
        # Import the private key so we can edit it
        private_key_data = gpg_key.private_key
        if isinstance(private_key_data, bytes):
            private_key_data = private_key_data.decode('utf-8')

        import_result = _sp.run(
            ['gpg', '--homedir', temp_dir, '--batch', '--import'],
            input=private_key_data,
            capture_output=True,
            text=True,
        )
        if import_result.returncode != 0:
            raise Exception(f"Failed to import private key: {import_result.stderr}")

        # Change the expiry date.
        # GPG --edit-key interactive commands fed via --command-fd:
        #   expire  → trigger expire sub-command
        #   <dur>   → new validity period ('0' = no expiry, '1y', etc.)
        #   y       → confirm when GPG asks "Is this correct?"
        #   save    → write and exit
        #
        # NOTE: newer GPG versions do not echo a "Is this correct?" prompt
        # when given a non-interactive input — but supplying the extra 'y'
        # is harmless and keeps compatibility with older versions.
        commands = f'expire\n{duration}\ny\nsave\n'
        edit_result = _sp.run(
            [
                'gpg', '--homedir', temp_dir,
                '--batch', '--yes',
                '--pinentry-mode', 'loopback',
                '--passphrase', '',
                '--command-fd', '0',
                '--edit-key', gpg_key.fingerprint,
            ],
            input=commands,
            capture_output=True,
            text=True,
        )
        if edit_result.returncode != 0:
            raise Exception(f"Failed to change key expiry: {edit_result.stderr}")

        # Re-export the updated public key
        pub_result = _sp.run(
            ['gpg', '--homedir', temp_dir, '--armor', '--export', gpg_key.fingerprint],
            capture_output=True,
            text=True,
        )
        if not pub_result.stdout:
            raise Exception(f"Failed to re-export public key after renewal: {pub_result.stderr}")

        return {
            'public_key': pub_result.stdout,
            'expires_at': _duration_to_date(duration),
        }
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

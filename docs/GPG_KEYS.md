# GPG Key Management Guide

## Overview

The Flat Manager Django application provides comprehensive GPG key management with two options:
- **Generate Keys**: Let the application create new GPG key pairs automatically
- **Import Keys**: Upload existing GPG keys from your keyring

Both public and private keys are stored securely in the database. Only public keys can be downloaded.

## Web UI

### Generate a New GPG Key

1. Navigate to **GPG Keys** in the sidebar
2. Click **Generate Key** button
3. Fill in the form:
   - **Name**: Real name for the key (e.g., "Flatpak Builder")
   - **Email**: Email address for the key
   - **Comment**: Optional comment
   - **Key Length**: 2048 or 4096 bits (4096 recommended)
   - **Passphrase**: Optional passphrase to protect the private key
   - **Passphrase Hint**: Reminder for the passphrase (stored in database)
4. Click **Generate Key**

The application will use GPG to generate a new RSA key pair. This may take up to a minute.

### Import an Existing GPG Key

1. Navigate to **GPG Keys** in the sidebar
2. Click **Import Key** button
3. Fill in the form:
   - **Name**: Descriptive name for this key
   - **Email**: Email address
   - **Public Key**: ASCII armored public key
   - **Private Key**: ASCII armored private key
   - **Passphrase Hint**: Optional hint if key is password-protected
4. Click **Import Key**

To export keys from your GPG keyring:
```bash
# Export public key
gpg --armor --export KEY_ID > public.asc

# Export private key
gpg --armor --export-secret-key KEY_ID > private.asc
```

### View GPG Keys

The GPG Keys list shows:
- Name and email
- Key ID (short form)
- Fingerprint (full)
- Status (Active/Inactive)
- Creation date

Click on a key to view full details.

### Download Public Key

From the key detail page or list, click **Download Public Key** to get the ASCII armored public key file (.asc). This can be distributed to users for signature verification.

**Security Note**: Private keys are NEVER exposed through the download function.

### Delete a GPG Key

From the key detail page, click **Delete** and confirm. This removes the key from the database.

**Warning**: Deleting a key assigned to repositories will break signature verification.

## REST API

### Generate a GPG Key

```bash
POST /api/gpg-keys/generate/

{
  "name": "Flatpak Builder",
  "email": "builder@example.com",
  "comment": "Production signing key",
  "key_length": 4096,
  "passphrase": "optional-passphrase",
  "passphrase_hint": "Your hint here"
}
```

Response: Full GPGKey object with key_id and fingerprint.

### Import a GPG Key

```bash
POST /api/gpg-keys/import_key/

{
  "name": "Imported Key",
  "email": "user@example.com",
  "public_key": "-----BEGIN PGP PUBLIC KEY BLOCK-----\n...",
  "private_key": "-----BEGIN PGP PRIVATE KEY BLOCK-----\n...",
  "passphrase_hint": "Optional hint"
}
```

Response: Full GPGKey object with validated key information.

### List GPG Keys

```bash
GET /api/gpg-keys/
```

Returns paginated list of keys (without private_key field).

### Get GPG Key Details

```bash
GET /api/gpg-keys/{id}/
```

Returns full key details (private_key is hidden in serializer).

### Get Public Key Only

```bash
GET /api/gpg-keys/{id}/public_key/
```

Returns only the public key data (safe for distribution).

### Delete GPG Key

```bash
DELETE /api/gpg-keys/{id}/
```

Removes the key from the database.

## Security Considerations

1. **Private Key Storage**: Private keys are stored in the database. In production, consider:
   - Database encryption at rest
   - Restricting database access
   - Using passphrase-protected keys
   - Hardware security modules (HSM) for highly sensitive keys

2. **Passphrase Storage**: Passphrases are NOT stored. Only hints are saved.

3. **Key Distribution**: Only public keys should be distributed. The download function ensures private keys are never exposed.

4. **Access Control**: All GPG key operations require authentication.

## Assigning Keys to Repositories

When creating or editing a repository:
1. Select a GPG key from the dropdown
2. The key will be used to sign commits and tags for that repository

Only active GPG keys appear in the repository form.

## Troubleshooting

### Key Generation Fails

**Error**: "Failed to generate GPG key"

**Solution**: Ensure GPG is installed on the system:
```bash
# Ubuntu/Debian
sudo apt-get install gnupg

# CentOS/RHEL
sudo yum install gnupg2
```

### Import Validation Errors

**Error**: "Invalid public key format"

**Solution**: Ensure the key is in ASCII armored format (starts with `-----BEGIN PGP PUBLIC KEY BLOCK-----`). Use `gpg --armor` when exporting.

### Insufficient Entropy

If key generation is slow or hangs, the system may lack entropy. Install rng-tools:
```bash
sudo apt-get install rng-tools
sudo systemctl start rng-tools
```

## Command Line Examples

### Generate via API
```bash
curl -X POST http://localhost:8000/api/gpg-keys/generate/ \
  -H "Authorization: Token YOUR_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Test Key",
    "email": "test@example.com",
    "key_length": 4096
  }'
```

### Import via API
```bash
curl -X POST http://localhost:8000/api/gpg-keys/import_key/ \
  -H "Authorization: Token YOUR_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d @key_data.json
```

### Download Public Key
```bash
curl -X GET http://localhost:8000/api/gpg-keys/1/public_key/ \
  -H "Authorization: Token YOUR_API_TOKEN" \
  -o public_key.json

# Or via web UI
wget http://localhost:8000/gpg-keys/1/download/ \
  --header="Cookie: sessionid=YOUR_SESSION" \
  -O public_key.asc
```

## Next Steps

After setting up GPG keys:
1. Create repositories and assign keys
2. Upload flatpak builds
3. Sign builds with the repository's GPG key
4. Publish signed repositories

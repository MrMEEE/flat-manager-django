"""
Satellite/Katello integration helpers.

Covers:
- Fernet token encryption (keyed from Django SECRET_KEY)
- HTTP primitives for the Foreman/Katello REST API
- One-time server setup: create service account, role, PAT
- Repository discovery (orgs → products → repos)
- RPM upload (create upload request → PUT file → import & publish)
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Encryption
# ---------------------------------------------------------------------------

def _fernet():
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        raise RuntimeError(
            "The 'cryptography' package is required for token storage. "
            "Install it with: pip install cryptography"
        )
    # Derive a fixed 32-byte key from SECRET_KEY so tokens survive service restarts.
    key_bytes = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key_bytes))


def encrypt_token(token: str) -> str:
    """Encrypt and return a base-64 Fernet ciphertext string."""
    return _fernet().encrypt(token.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    """Decrypt a Fernet ciphertext string to the original token."""
    return _fernet().decrypt(ciphertext.encode()).decode()


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _ssl_ctx(verify_ssl: bool) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if not verify_ssl:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _basic_auth(login: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{login}:{password}".encode()).decode()


def _request(
    url: str,
    auth: str,
    method: str,
    path: str,
    payload: dict | None = None,
    verify_ssl: bool = True,
    timeout: int = 30,
) -> tuple[dict | None, str]:
    """Generic Satellite/Katello API call. Returns (response_dict | None, error_str)."""
    full_url = url.rstrip("/") + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(full_url, data=data, method=method)
    req.add_header("Authorization", auth)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx(verify_ssl)) as resp:
            body = resp.read().decode()
            return (json.loads(body) if body.strip() else {}), ""
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode()
        except Exception:
            detail = ""
        return None, f"HTTP {exc.code}: {detail}"
    except Exception as exc:
        return None, str(exc)


def get(url, auth, path, verify_ssl=True) -> tuple[dict | None, str]:
    return _request(url, auth, "GET", path, verify_ssl=verify_ssl)


def post(url, auth, path, payload, verify_ssl=True) -> tuple[dict | None, str]:
    return _request(url, auth, "POST", path, payload, verify_ssl=verify_ssl)


def api_list(url, auth, path, verify_ssl=True) -> list[dict]:
    """Fetch a paginated Katello API list. Returns [] on failure."""
    result, err = get(url, auth, path + "?enabled=true", verify_ssl)
    if err or result is None:
        return []
    return result.get("results", result) if isinstance(result, dict) else result


def test_connection(url: str, auth: str, verify_ssl: bool = True) -> str:
    """Ping the Satellite API. Returns '' on success or an error string."""
    _, err = get(url, auth, "/api/v2/ping", verify_ssl)
    return err


# ---------------------------------------------------------------------------
# Server bootstrap (one-time admin operation)
# ---------------------------------------------------------------------------

_REQUIRED_PERMISSIONS = [
    "view_organizations",
    "view_products",
    "edit_products",
    "view_repositories",
    "upload_to_repositories",
]


def provision_service_account(
    url: str,
    admin_auth: str,
    login: str,
    verify_ssl: bool = True,
) -> tuple[str | None, str]:
    """
    Create (or reuse) a Foreman service account + role + PAT.
    Returns (pat_token_plaintext, error).
    """
    # --- Create user ---
    import secrets
    import string
    password = "".join(
        secrets.choice(string.ascii_letters + string.digits + "!@#$%^&*")
        for _ in range(32)
    )
    user_payload = {
        "user": {
            "login":          login,
            "firstname":      "flat-manager",
            "lastname":       "Service",
            "mail":           f"{login}@localhost.invalid",
            "password":       password,
            "auth_source_id": 1,
            "admin":          False,
        }
    }
    result, err = post(url, admin_auth, "/api/v2/users", user_payload, verify_ssl)
    if err:
        if "already" in err.lower() or "taken" in err.lower():
            search = urllib.parse.quote(f"login = {login}")
            found, ferr = get(url, admin_auth, f"/api/v2/users?search={search}&per_page=2", verify_ssl)
            if not ferr and found and found.get("results"):
                user_id = found["results"][0]["id"]
            else:
                return None, f"User '{login}' already exists but couldn't look it up: {ferr or err}"
        else:
            return None, f"Failed to create service account: {err}"
    else:
        user_id = result["id"]

    # --- Create role ---
    role_name = f"flat-manager-{login}"
    result, err = post(url, admin_auth, "/api/v2/roles",
                       {"role": {"name": role_name, "description": "Auto-created by flat-manager-django"}},
                       verify_ssl)
    if err:
        if "already" in err.lower() or "taken" in err.lower():
            search = urllib.parse.quote(f"name = {role_name}")
            found, _ = get(url, admin_auth, f"/api/v2/roles?search={search}&per_page=2", verify_ssl)
            role_id = found["results"][0]["id"] if found and found.get("results") else None
            if role_id is None:
                return None, f"Role '{role_name}' exists but couldn't look it up"
        else:
            return None, f"Failed to create role: {err}"
    else:
        role_id = result["id"]

    # --- Assign permissions to role ---
    perms: list[dict] = []
    for perm_name in _REQUIRED_PERMISSIONS:
        path = "/api/v2/permissions?search=" + urllib.parse.quote(f"name = {perm_name}") + "&per_page=5"
        result, _ = get(url, admin_auth, path, verify_ssl)
        if result and result.get("results"):
            p = result["results"][0]
            perms.append({"id": p["id"], "resource_type": p.get("resource_type") or ""})

    # Group by resource_type (Foreman requirement)
    groups: dict[str, list[int]] = {}
    for p in perms:
        groups.setdefault(p["resource_type"], []).append(p["id"])
    for rtype, ids in groups.items():
        post(url, admin_auth, "/api/v2/filters",
             {"filter": {"role_id": role_id, "permission_ids": ids, "unlimited": True}},
             verify_ssl)

    # --- Assign role to user ---
    _request(url, admin_auth, "PATCH", f"/api/v2/users/{user_id}",
             {"user": {"role_ids": [role_id]}}, verify_ssl)

    # --- Create PAT ---
    pat_path = f"/api/v2/users/{user_id}/personal_access_tokens"
    import time
    token_name = "flat-manager"
    result, err = post(url, admin_auth, pat_path, {"personal_access_token": {"name": token_name}}, verify_ssl)
    if err and ("already" in err.lower() or "taken" in err.lower()):
        # Delete the existing PAT and recreate
        list_result, _ = get(url, admin_auth, pat_path + "?per_page=200", verify_ssl)
        pats = (list_result.get("results", []) if isinstance(list_result, dict) else list_result or []) if list_result else []
        pat_id = next((p["id"] for p in pats if p.get("name") == token_name), None)
        if pat_id:
            _request(url, admin_auth, "DELETE", f"{pat_path}/{pat_id}", verify_ssl=verify_ssl)
            time.sleep(1)
        result, err = post(url, admin_auth, pat_path, {"personal_access_token": {"name": token_name}}, verify_ssl)

    if err or not result:
        return None, f"Failed to create Personal Access Token: {err}"

    token_val = result.get("token_value") or result.get("value") or result.get("token", "")
    if not token_val:
        return None, "PAT was created but Satellite did not return the token value"

    return token_val, ""


# ---------------------------------------------------------------------------
# Repository discovery
# ---------------------------------------------------------------------------

def fetch_organizations(url: str, login: str, token: str, verify_ssl: bool = True) -> list[dict]:
    auth = _basic_auth(login, token)
    return api_list(url, auth, "/api/v2/organizations", verify_ssl)


def fetch_products(url: str, login: str, token: str, org_id: int, verify_ssl: bool = True) -> list[dict]:
    auth = _basic_auth(login, token)
    return api_list(url, auth, f"/katello/api/organizations/{org_id}/products", verify_ssl)


def fetch_repositories(url: str, login: str, token: str, product_id: int, verify_ssl: bool = True) -> list[dict]:
    auth = _basic_auth(login, token)
    return api_list(url, auth, f"/katello/api/products/{product_id}/repositories", verify_ssl)


# ---------------------------------------------------------------------------
# RPM upload
# ---------------------------------------------------------------------------

def push_rpm(rpm_path: str, url: str, login: str, token: str, repository_id: int, verify_ssl: bool = True) -> str:
    """
    Upload an RPM file to a Katello repository.
    Returns '' on success or an error string.
    """
    from pathlib import Path

    path = Path(rpm_path)
    if not path.exists():
        return f"RPM file not found: {rpm_path}"

    auth = _basic_auth(login, token)
    file_data = path.read_bytes()
    file_size = len(file_data)
    checksum = hashlib.sha256(file_data).hexdigest()
    filename = path.name

    def _version_tuple(version: str) -> tuple[int, ...]:
        numbers = [int(part) for part in re.split(r"[^0-9]+", str(version or "")) if part]
        return tuple(numbers[:3]) if numbers else (0,)

    def _artifacts_upload_url(upload_id: str) -> str:
        return url.rstrip("/") + f"/katello/api/v2/repositories/{repository_id}/content_uploads/{upload_id}"

    def _create_upload_request() -> tuple[str, str]:
        result, err = post(
            url, auth,
            f"/katello/api/v2/repositories/{repository_id}/content_uploads",
            {"size": file_size, "checksum": checksum, "content_type": "rpm"},
            verify_ssl,
        )
        if err or not result:
            return "", f"Could not create upload request: {err}"
        upload_id = result.get("upload_id")
        if not upload_id:
            return "", f"No upload_id in response: {result}"
        return upload_id, ""

    def _put_raw_upload(put_url: str) -> str:
        req = urllib.request.Request(put_url, data=file_data, method="PUT")
        req.add_header("Authorization", auth)
        req.add_header("Content-Type", "application/octet-stream")
        req.add_header("Content-Range", f"bytes 0-{file_size - 1}/{file_size}")
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=120, context=_ssl_ctx(verify_ssl)) as resp:
                resp.read()
            return ""
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode()
            except Exception:
                pass
            return f"HTTP {exc.code}: {detail}"
        except Exception as exc:
            return str(exc)

    def _put_multipart_upload(put_url: str) -> str:
        boundary = "--------fmd_boundary"
        body = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"size\"\r\n\r\n{file_size}\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"offset\"\r\n\r\n0\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"content\"; filename=\"{filename}\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode() + file_data + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(put_url, data=body, method="PUT")
        req.add_header("Authorization", auth)
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=120, context=_ssl_ctx(verify_ssl)) as resp:
                resp.read()
            return ""
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode()
            except Exception:
                pass
            return f"HTTP {exc.code}: {detail}"
        except Exception as exc:
            return str(exc)

    def _import_upload(upload_id: str):
        import_payload = {
            "uploads": [{"id": upload_id, "name": filename, "checksum": checksum}],
            "publish_repository": True,
            "content_type": "rpm",
        }
        _, err = _request(
            url, auth, "PUT",
            f"/katello/api/v2/repositories/{repository_id}/import_uploads",
            import_payload, verify_ssl,
        )
        return err

    version_info, version_err = get(url, auth, "/api/v2/version", verify_ssl)
    version_str = ""
    if isinstance(version_info, dict):
        version_str = str(version_info.get("version") or version_info.get("version") or "")
    if version_err == "" and version_str and _version_tuple(version_str) < (4, 15):
        put_url = url.rstrip("/") + f"/katello/api/v2/repositories/{repository_id}/upload_content"
        req = urllib.request.Request(put_url, data=file_data, method="PUT")
        req.add_header("Authorization", auth)
        req.add_header("Content-Type", "application/octet-stream")
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=120, context=_ssl_ctx(verify_ssl)) as resp:
                resp.read()
            return ""
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode()
            except Exception:
                pass
            return f"HTTP {exc.code}: {detail}"
        except Exception as exc:
            return str(exc)

    while True:
        upload_id, err = _create_upload_request()
        if err:
            return err

        put_url = _artifacts_upload_url(upload_id)
        upload_error = _put_raw_upload(put_url)
        if upload_error:
            logger.warning(
                "push_rpm: raw RPM upload failed for repo %s upload %s, trying multipart fallback: %s",
                repository_id, upload_id, upload_error,
            )
            upload_error = _put_multipart_upload(put_url)
            if upload_error:
                return f"File upload failed: {upload_error}"

        err = _import_upload(upload_id)
        if err and "checksum" in err.lower() and "did not match" in err.lower():
            logger.warning(
                "push_rpm: checksum mismatch for repo %s upload %s, retrying with a fresh upload: %s",
                repository_id, upload_id, err,
            )
            continue
        if err:
            return f"Failed to import upload: {err}"
        return ""

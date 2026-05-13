"""
Authentication backends for flat-manager.

LocalModelBackend  – standard Django password auth, but ONLY for users whose
                     is_local flag is set.  Prevents LDAP-provisioned accounts
                     from logging in with a local password.

LDAPBackend        – iterates all active LDAPSource entries, attempts to bind
                     as the user to verify the password, then auto-provisions
                     (or updates) the matching Django User and syncs group →
                     role mappings.
"""
from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

User = get_user_model()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LocalModelBackend
# ---------------------------------------------------------------------------

class LocalModelBackend(ModelBackend):
    """
    Standard Django ModelBackend that only authenticates users whose
    ``is_local`` flag is True.  This prevents LDAP-provisioned accounts
    from using a locally-set password.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        user = super().authenticate(request, username=username, password=password, **kwargs)
        if user is None:
            return None
        if not getattr(user, 'is_local', True):
            return None
        return user


# ---------------------------------------------------------------------------
# LDAPBackend
# ---------------------------------------------------------------------------

class LDAPBackend:
    """
    LDAP authentication backend.

    On success it:
    1. Creates the Django User if it doesn't exist yet (with is_local=False).
    2. Updates first_name / last_name / email from LDAP attributes.
    3. Replaces the user's UserRole entries (for this source) based on
       LDAPGroupMapping entries configured for the source.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        if not username or not password:
            return None

        # Import here to avoid circular imports at startup
        from .models import LDAPSource

        for source in LDAPSource.objects.filter(is_active=True):
            user = self._try_source(source, username, password)
            if user is not None:
                return user
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _try_source(self, source, username: str, password: str):
        """Try to authenticate *username* against a single LDAPSource."""
        try:
            import ldap3
        except ImportError:
            logger.error("ldap3 is not installed — LDAP authentication is unavailable")
            return None

        try:
            server = ldap3.Server(
                source.hostname,
                port=source.port,
                use_ssl=(source.protocol == 'ldaps'),
                get_info=ldap3.ALL,
                tls=ldap3.Tls(validate=2 if source.verify_certs else 0),
            )
        except Exception:
            logger.exception("LDAPSource %r: failed to create server object", source.name)
            return None

        # ── Step 1: service-account bind to find the user ────────────────────
        user_dn, user_attrs = self._find_user(source, server, username, ldap3)
        if user_dn is None:
            return None

        # ── Step 2: bind as the user to verify the password ──────────────────
        try:
            conn = ldap3.Connection(
                server,
                user=user_dn,
                password=password,
                authentication=ldap3.SIMPLE,
                auto_bind=ldap3.AUTO_BIND_NO_TLS,
            )
            if not conn.bind():
                logger.debug("LDAPSource %r: bind as %r failed", source.name, user_dn)
                return None
        except Exception:
            logger.debug("LDAPSource %r: bind as %r raised exception", source.name, user_dn,
                         exc_info=True)
            return None

        # ── Step 3: provision / update the Django User ───────────────────────
        django_user = self._provision_user(source, username, user_dn, user_attrs)

        # ── Step 4: sync group-mapped roles ──────────────────────────────────
        if django_user is not None:
            self._sync_roles(source, server, source.get_bind_password(), django_user, user_dn, user_attrs, ldap3)

        return django_user

    def _find_user(self, source, server, username: str, ldap3):
        """Bind with the service account and search for *username*.  Returns (dn, attrs) or (None, None)."""
        bind_password = source.get_bind_password()
        try:
            conn = ldap3.Connection(
                server,
                user=source.bind_dn,
                password=bind_password,
                authentication=ldap3.SIMPLE,
                auto_bind=ldap3.AUTO_BIND_NO_TLS,
            )
            if not conn.bind():
                logger.warning("LDAPSource %r: service account bind failed: %s", source.name, conn.result)
                return None, None
        except Exception:
            logger.exception("LDAPSource %r: service account bind raised exception", source.name)
            return None, None

        # Build search filter
        uid_attr = self._uid_attr(source)
        base_filter = source.ldap_filter or '(objectClass=person)'
        search_filter = f'(&{base_filter}({uid_attr}={ldap3.utils.conv.escape_filter_chars(username)}))'

        attrs = ['dn', 'cn', 'givenName', 'sn', 'mail', 'sAMAccountName', 'uid',
                 'memberOf', 'memberUid', 'objectClass']

        if not conn.search(
            search_base=source.base_dn,
            search_filter=search_filter,
            search_scope=ldap3.SUBTREE,
            attributes=attrs,
        ):
            logger.debug("LDAPSource %r: search for %r returned no results", source.name, username)
            return None, None

        entries = [e for e in conn.entries if e.entry_dn]
        if not entries:
            return None, None
        if len(entries) > 1:
            logger.warning("LDAPSource %r: multiple entries for %r — using first", source.name, username)

        entry = entries[0]
        user_attrs = {attr: entry[attr].values if attr in entry else [] for attr in attrs}
        return entry.entry_dn, user_attrs

    def _provision_user(self, source, username: str, user_dn: str, user_attrs: dict):
        """Get or create the Django User; update name / email from LDAP."""
        email = self._first(user_attrs.get('mail', []))
        first_name = self._first(user_attrs.get('givenName', []))
        last_name = self._first(user_attrs.get('sn', []))

        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            user = User(username=username, is_local=False)
            user.set_unusable_password()

        user.is_local = False
        if email:
            user.email = email
        if first_name:
            user.first_name = first_name
        if last_name:
            user.last_name = last_name
        user.save()
        return user

    def _sync_roles(self, source, server, bind_password: str, user, user_dn: str,
                    user_attrs: dict, ldap3):
        """
        Replace the user's roles that originate from this source's group
        mappings.  Other (manually assigned) roles are left untouched.
        """
        from .models import LDAPGroupMapping, UserRole

        mappings = list(source.group_mappings.select_related('organisation').all())
        if not mappings:
            return

        # Collect the user's group memberships
        user_groups = self._get_user_groups(source, server, bind_password, user_dn, user_attrs, ldap3)

        # Determine which roles should be granted
        granted = []
        for mapping in mappings:
            if mapping.ldap_group_dn.lower() in user_groups:
                granted.append((mapping.role, mapping.organisation))

        # The set of (role, org) combinations this source manages
        managed_combos = {(m.role, m.organisation_id) for m in mappings}

        # Remove roles from this source that are no longer in granted
        for role in UserRole.objects.filter(user=user):
            if (role.role, role.organisation_id) in managed_combos:
                if (role.role, role.organisation) not in [(r, o) for r, o in granted]:
                    role.delete()

        # Add newly granted roles
        for role_name, org in granted:
            UserRole.objects.get_or_create(user=user, role=role_name, organisation=org)

    def _get_user_groups(self, source, server, bind_password: str, user_dn: str,
                         user_attrs: dict, ldap3) -> set:
        """Return a set of lowercase group DNs the user belongs to."""
        if source.group_membership == 'ad':
            # memberOf attribute on the user entry (AD-style)
            raw = user_attrs.get('memberOf', [])
            return {dn.lower() for dn in raw if dn}

        # POSIX-style: search groups where memberUid = username
        uid_val = self._first(user_attrs.get('uid', []) or user_attrs.get('sAMAccountName', []))
        if not uid_val:
            return set()

        try:
            conn = ldap3.Connection(
                server,
                user=source.bind_dn,
                password=bind_password,
                authentication=ldap3.SIMPLE,
                auto_bind=ldap3.AUTO_BIND_NO_TLS,
            )
            if not conn.bind():
                return set()
            group_base = source.group_base_dn or source.base_dn
            conn.search(
                search_base=group_base,
                search_filter=f'(memberUid={ldap3.utils.conv.escape_filter_chars(uid_val)})',
                search_scope=ldap3.SUBTREE,
                attributes=['dn'],
            )
            return {e.entry_dn.lower() for e in conn.entries if e.entry_dn}
        except Exception:
            logger.exception("LDAPSource %r: POSIX group search failed", source.name)
            return set()

    # ------------------------------------------------------------------
    # Small utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _uid_attr(source) -> str:
        if source.server_type == 'ad':
            return 'sAMAccountName'
        return 'uid'

    @staticmethod
    def _first(seq):
        for item in seq:
            if item:
                return item
        return ''

    # Required by Django's auth machinery
    def get_user(self, user_id):
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None

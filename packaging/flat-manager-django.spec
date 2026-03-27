%global app_name      flat-manager
%global app_user      flat-manager
%global app_group     flat-manager
%global install_dir   /opt/%{app_name}
%global conf_dir      /etc/%{app_name}
%global data_dir      /var/lib/%{app_name}
%global log_dir       /var/log/%{app_name}

# ── Python interpreter selection ──────────────────────────────────────────────
# RHEL9 / CentOS Stream 9: default python3 is 3.9 — too old for Django 5.
#   Use python3.11 from AppStream.
# RHEL10 / CentOS Stream 10: default python3 is 3.12 — fine.
%if 0%{?rhel} == 9
%global pybin        python3.11
%global pypkg_prefix python3.11
%else
%global pybin        python3
%global pypkg_prefix python3
%endif

# ── BuildStream 1 always needs Python ≤ 3.11 ─────────────────────────────────
# BuildStream 1.x uses versioneer which calls configparser.SafeConfigParser,
# removed in Python 3.12.  Force python3.11 for the BST1 venv on all targets.
%global bst1pybin    python3.11

# ── Exclude the bundled venv from RPM's shebang-mangling check ────────────────
# The venv contains upstream pip packages with shebangs we cannot control
# (e.g. #!/usr/bin/env python in Django's project template).
%global __brp_mangle_shebangs_exclude_from ^%{install_dir}/venv/|^%{install_dir}/bst1-venv/

# ── Suppress debuginfo generation ────────────────────────────────────────────
# Pre-built pip wheels (Pillow, mysqlclient, hiredis, …) are stripped upstream
# and do not carry ELF build-IDs.  eu-strip cannot process them and
# --strict-build-id (default on RHEL10) would abort the build.
# There is nothing useful to extract into a debuginfo package here.
%global debug_package %{nil}
%undefine _missing_build_ids_terminate_build

# ── Exclude the venv from automatic dependency scanning ───────────────────────
# Pillow manylinux wheels bundle private copies of libjpeg, libpng, libtiff,
# liblzma, … with hashed SONAMEs (e.g. libjpeg-32d42e18.so.62.4.0).
# RPM's ELF scanner would generate Requires: for those hashed names which do
# not exist in any distro package — causing dnf to refuse installation.
# Excluding the entire venv from both Requires and Provides scanning is the
# standard approach for bundled/private dependency trees.
%global __requires_exclude_from ^%{install_dir}/venv/|^%{install_dir}/bst1-venv/
%global __provides_exclude_from ^%{install_dir}/venv/|^%{install_dir}/bst1-venv/

# ─────────────────────────────────────────────────────────────────────────────
#  Main package  (noarch — pure Python + config files)
# ─────────────────────────────────────────────────────────────────────────────
Name:           flat-manager-django
Version:        %{version_string}
Release:        1%{?dist}
Summary:        Flatpak repository manager — Django/Channels web application
License:        MIT
URL:            https://github.com/MrMEEE/flat-manager-django
Source0:        %{name}-%{version}.tar.gz
# python-libs subpackage contains compiled C extensions so the whole spec
# must be built for the target arch.  The main package contents are
# architecture-independent but will carry an arch suffix (e.g. .x86_64).

BuildRequires:  %{pypkg_prefix}
BuildRequires:  systemd-rpm-macros
BuildRequires:  checkpolicy
BuildRequires:  policycoreutils
BuildRequires:  rust
BuildRequires:  cargo

# Python runtime interpreter
Requires:       %{pypkg_prefix}

# Python virtualenv companion package (arch-specific, built from same SRPM)
Requires:       flat-manager-django-python-libs = %{version}-%{release}

# System services
Requires:       nginx
Requires:       (redis >= 7 or valkey >= 7)
Requires:       mariadb
Requires:       flatpak
Requires:       flatpak-builder
Requires:       ostree
Requires:       breezy
Requires:       openssl
Requires:       policycoreutils-python-utils

Requires(pre):  shadow-utils
%{?systemd_requires}

%description
flat-manager-django is a web-based Flatpak repository manager built with Django,
Celery, and Django Channels. It provides:

 * Flatpak build scheduling and real-time log streaming via WebSockets
 * OSTree repository management and GPG signing
 * Multi-stage promotion pipelines (build-repo → beta → stable …)
 * REST API compatible with flat-manager-client workflows

This package contains the Django application source, systemd service units,
nginx reverse-proxy configuration, and helper scripts.

Python dependencies are shipped in the companion flat-manager-django-python-libs
package so that the dependency set can be updated (e.g. security patches)
independently of the application code.

# ─────────────────────────────────────────────────────────────────────────────
#  Sub-package: Python virtualenv  (arch-specific — contains C extensions)
# ─────────────────────────────────────────────────────────────────────────────
%package        python-libs
Summary:        Python virtualenv for flat-manager-django
# NOT noarch: compiled C extensions (mysqlclient, Pillow, hiredis, …)

# python3.11-pip does not exist as an RPM on CS9 — pip is bootstrapped via
# ensurepip in %build instead.  On CS10, python3-pip is available normally.
%if 0%{?rhel} != 9
BuildRequires:  %{pypkg_prefix}-pip
BuildRequires:  cargo
%endif
BuildRequires:  %{pypkg_prefix}-devel
BuildRequires:  gcc
BuildRequires:  mariadb-connector-c-devel

Requires:       %{pypkg_prefix}

%description    python-libs
Pre-built Python virtualenv containing all pip dependencies for
flat-manager-django, installed under %{install_dir}/venv/.

Keeping the virtualenv in a separate package allows the Python dependency set
to be updated independently of the application code.

# ─────────────────────────────────────────────────────────────────────────────
#  Sub-package: client agent  (noarch — pure Python)
# ─────────────────────────────────────────────────────────────────────────────
%package        client
Summary:        Client check-in agent for flat-manager-django
BuildArch:      noarch

Requires:       python3
Requires:       flatpak

%description    client
Lightweight check-in agent for machines that consume flatpak repositories
managed by flat-manager-django.

Runs hourly via a systemd timer.  On each run it collects the local flatpak
state (installed apps, available updates, configured remotes) and POSTs it to
the flat-manager-django server so the "Clients" dashboard can track which
machines are up to date.

Configuration: /etc/flat-manager-django-client/config

# ─────────────────────────────────────────────────────────────────────────────
%prep
%autosetup -n %{name}-%{version}

%build
# ── Bootstrap pip on RHEL9 where python3.11-pip is not an RPM ────────────────
%if 0%{?rhel} == 9
%{pybin} -m ensurepip --upgrade 2>/dev/null || \
    curl -sS https://bootstrap.pypa.io/get-pip.py | %{pybin}
%endif

# ── Build virtualenv with all Python dependencies ─────────────────────────────
%{pybin} -m venv %{_builddir}/fmvenv
%{_builddir}/fmvenv/bin/pip install --upgrade pip --quiet
%{_builddir}/fmvenv/bin/pip install -r requirements.txt --quiet

# ── Build separate virtualenv for BuildStream 1 ───────────────────────────────
# BST 1 and BST 2 use incompatible project.conf formats; each needs its own
# isolated venv.  BST 1.x requires Python ≤ 3.11 — configparser.SafeConfigParser
# was removed in 3.12.  Always use python3.11 here regardless of the main pybin.
# Pin to the 1.6.x stable series; 1.9x.dev builds are actually early BST 2.
%{bst1pybin} -m venv %{_builddir}/bst1venv
%{_builddir}/bst1venv/bin/python -m ensurepip --upgrade 2>/dev/null || true
%{_builddir}/bst1venv/bin/pip install --upgrade pip --quiet
%{_builddir}/bst1venv/bin/pip install 'BuildStream>=1.0,<1.7' --quiet

# ── Collect static files (needs venv + Django importable) ────────────────────
mkdir -p %{_builddir}/tmp-static \
         %{_builddir}/tmp-repos \
         %{_builddir}/tmp-builds \
         %{_builddir}/tmp-logs

DJANGO_SETTINGS_MODULE=config.settings \
SECRET_KEY=build-phase-dummy-key \
DEBUG=False \
ALLOWED_HOSTS=localhost \
REPOS_BASE_PATH=%{_builddir}/tmp-repos \
FLATPAK_BUILD_PATH=%{_builddir}/tmp-builds \
STATIC_ROOT=%{_builddir}/tmp-static \
LOG_DIR=%{_builddir}/tmp-logs \
  %{_builddir}/fmvenv/bin/python manage.py collectstatic \
      --noinput --verbosity 0 2>/dev/null || :

# ── Compile SELinux policy module ────────────────────────────────────────────
cd packaging/selinux
checkmodule -M -m -o flat-manager-nginx.mod flat-manager-nginx.te
semodule_package -o flat-manager-nginx.pp -m flat-manager-nginx.mod
cd %{_builddir}/%{name}-%{version}

%install
# ── Application source → flat-manager-django (noarch) ────────────────────────
mkdir -p %{buildroot}%{install_dir}/app
cp -a . %{buildroot}%{install_dir}/app/

# Strip artefacts that must not ship
for d in venv .git repos builds media staticfiles logs __pycache__; do
    rm -rf %{buildroot}%{install_dir}/app/$d
done
find %{buildroot}%{install_dir}/app \
    \( -name '*.pyc' -o -name '__pycache__' -type d \) -delete
rm -f %{buildroot}%{install_dir}/app/db.sqlite3 \
      %{buildroot}%{install_dir}/app/dump.rdb

# ── Virtualenv → flat-manager-django-python-libs (arch-specific) ─────────────
cp -a %{_builddir}/fmvenv %{buildroot}%{install_dir}/venv

# Rewrite hard-coded build-dir paths to the final install prefix
find %{buildroot}%{install_dir}/venv \
    \( -type f -o -type l \) \
    -exec grep -IlF '%{_builddir}/fmvenv' {} \; \
  | xargs --no-run-if-empty \
    sed -i "s|%{_builddir}/fmvenv|%{install_dir}/venv|g"

# ── BST 1 virtualenv ──────────────────────────────────────────────────────────
cp -a %{_builddir}/bst1venv %{buildroot}%{install_dir}/bst1-venv

find %{buildroot}%{install_dir}/bst1-venv \
    \( -type f -o -type l \) \
    -exec grep -IlF '%{_builddir}/bst1venv' {} \; \
  | xargs --no-run-if-empty \
    sed -i "s|%{_builddir}/bst1venv|%{install_dir}/bst1-venv|g"

# ── Collected static files ────────────────────────────────────────────────────
install -d -m 0755 %{buildroot}%{data_dir}/staticfiles
cp -a %{_builddir}/tmp-static/. %{buildroot}%{data_dir}/staticfiles/ 2>/dev/null || :

# ── Runtime data + log directories (owned by flat-manager) ───────────────────
install -d -m 0755 %{buildroot}%{data_dir}/repos
install -d -m 0750 %{buildroot}%{data_dir}/builds
install -d -m 0755 %{buildroot}%{data_dir}/media
install -d -m 0750 %{buildroot}%{log_dir}
install -d -m 0750 %{buildroot}%{conf_dir}

# ── systemd units ─────────────────────────────────────────────────────────────
install -D -m 0644 packaging/systemd/flat-manager-web.service \
    %{buildroot}%{_unitdir}/flat-manager-web.service
install -D -m 0644 packaging/systemd/flat-manager-celery.service \
    %{buildroot}%{_unitdir}/flat-manager-celery.service
install -D -m 0644 packaging/systemd/flat-manager-celery-beat.service \
    %{buildroot}%{_unitdir}/flat-manager-celery-beat.service
install -D -m 0644 packaging/systemd/flat-manager.target \
    %{buildroot}%{_unitdir}/flat-manager.target

# ── nginx config ──────────────────────────────────────────────────────────────
install -D -m 0644 packaging/nginx/flat-manager.conf \
    %{buildroot}%{_sysconfdir}/nginx/conf.d/flat-manager.conf

# ── Environment / config file ─────────────────────────────────────────────────
install -D -m 0640 packaging/conf/flat-manager.env.example \
    %{buildroot}%{conf_dir}/flat-manager.env.example

# ── tmpfiles.d — creates /run/flat-manager at boot ───────────────────────────
install -d %{buildroot}%{_tmpfilesdir}
cat > %{buildroot}%{_tmpfilesdir}/flat-manager.conf <<'EOF'
d /run/flat-manager 0750 flat-manager flat-manager -
EOF

# ── manage.py wrapper ─────────────────────────────────────────────────────────
mkdir -p %{buildroot}%{_bindir}
cat > %{buildroot}%{_bindir}/flat-manager-manage <<'EOF'
#!/bin/sh
# Load the production environment
set -a
[ -f /etc/flat-manager/flat-manager.env ] && . /etc/flat-manager/flat-manager.env
set +a

# ── Built-in commands (not delegated to manage.py) ───────────────────────────
case "${1:-}" in
    update)
        # Check GitHub for a newer release and upgrade if available.
        if [ "$(id -u)" -ne 0 ]; then
            echo "ERROR: 'flat-manager-manage update' must be run as root." >&2
            exit 1
        fi
        REPO="MrMEEE/flat-manager-django"
        API="https://api.github.com/repos/${REPO}/releases/latest"
        OS_VER=$(rpm -E '%{?rhel}' 2>/dev/null)
        if [ -z "${OS_VER}" ]; then
            echo "ERROR: could not detect RHEL major version." >&2
            exit 1
        fi
        echo "Checking for updates..."
        RELEASE_JSON=$(curl -sf --max-time 15 "${API}") || {
            echo "ERROR: failed to contact GitHub API (is curl installed and the host online?)." >&2
            exit 1
        }
        LATEST_TAG=$(printf '%s' "${RELEASE_JSON}" | \
            python3 -c "import sys,json; print(json.load(sys.stdin)['tag_name'])" 2>/dev/null)
        LATEST_VER="${LATEST_TAG#v}"
        if [ -z "${LATEST_VER}" ]; then
            echo "ERROR: could not parse release tag from GitHub response." >&2
            exit 1
        fi
        INSTALLED_VER=$(rpm -q flat-manager-django \
            --queryformat '%{VERSION}' 2>/dev/null)
        if [ -z "${INSTALLED_VER}" ]; then
            echo "ERROR: flat-manager-django does not appear to be installed via RPM." >&2
            exit 1
        fi
        if [ "${LATEST_VER}" = "${INSTALLED_VER}" ]; then
            echo "flat-manager-django is already up to date (${INSTALLED_VER})."
            exit 0
        fi
        echo "  Installed : ${INSTALLED_VER}"
        echo "  Available : ${LATEST_VER}"
        echo ""
        URLS=$(printf '%s' "${RELEASE_JSON}" | \
            python3 -c "import sys,json; [print(a['browser_download_url']) for a in json.load(sys.stdin)['assets']]" \
            | grep "\.el${OS_VER}\." | grep '\.rpm$')
        if [ -z "${URLS}" ]; then
            echo "ERROR: no el${OS_VER} RPM assets found for release ${LATEST_TAG}." >&2
            exit 1
        fi
        echo "Installing update..."
        # shellcheck disable=SC2086
        dnf install -y ${URLS} || {
            echo "ERROR: dnf install failed." >&2
            exit 1
        }
        echo "Update to ${LATEST_VER} complete."
        echo "Run 'flat-manager-manage migrate' to apply any new database migrations."
        exit 0
        ;;
esac

# ── Delegate everything else to Django manage.py ─────────────────────────────
# When invoked as root (e.g. during initial setup: migrate, createsuperuser)
# drop privileges to the flat-manager service account so that any files created
# (logs, .pyc caches, etc.) are owned by flat-manager, not root.
if [ "$(id -u)" -eq 0 ]; then
    exec runuser -u flat-manager -- \
        /opt/flat-manager/venv/bin/python /opt/flat-manager/app/manage.py "$@"
fi

exec /opt/flat-manager/venv/bin/python /opt/flat-manager/app/manage.py "$@"
EOF
chmod 0755 %{buildroot}%{_bindir}/flat-manager-manage

# ── SELinux policy module ─────────────────────────────────────────────────────
install -D -m 0644 packaging/selinux/flat-manager-nginx.pp \
    %{buildroot}%{_datadir}/selinux/packages/flat-manager-nginx.pp

# ── flat-manager-django-client files ─────────────────────────────────────────
install -D -m 0755 packaging/flat-manager-django-client/flat-manager-checkin \
    %{buildroot}%{_bindir}/flat-manager-checkin
install -D -m 0644 packaging/flat-manager-django-client/config.example \
    %{buildroot}%{_sysconfdir}/flat-manager-django-client/config.example

# ─────────────────────────────────────────────────────────────────────────────
%pre
getent group  %{app_group} >/dev/null || groupadd  -r %{app_group}
getent passwd %{app_user}  >/dev/null || \
    useradd -r -g %{app_group} -d %{install_dir} -s /sbin/nologin \
            -c "Flat Manager service account" %{app_user}
exit 0

%post
%systemd_post flat-manager-web.service flat-manager-celery.service flat-manager-celery-beat.service flat-manager.target

chown -R %{app_user}:%{app_group} %{data_dir} %{log_dir} %{conf_dir}
chown    %{app_user}:%{app_group} %{install_dir}
systemd-tmpfiles --create %{_tmpfilesdir}/flat-manager.conf 2>/dev/null || :

# Add nginx to the flat-manager group so it can read OSTree repo data served
# under /repositories/.  Required because dynamically created repo files are
# owned flat-manager:flat-manager; nginx (httpd_t) needs group read access.
getent passwd nginx >/dev/null 2>&1 && usermod -aG %{app_group} nginx || :

# Label /var/run/flat-manager/ so nginx (httpd_t) can connect to the UNIX socket.
# Without this SELinux denies httpd_t write access to var_run_t sock_file.
# Note: use /var/run (not /run) — semanage requires the canonical path.
if command -v semanage >/dev/null 2>&1; then
    semanage fcontext -a -t httpd_var_run_t '/var/run/flat-manager(/.*)?' 2>/dev/null || \
    semanage fcontext -m -t httpd_var_run_t '/var/run/flat-manager(/.*)?' 2>/dev/null || :
    restorecon -Rv /run/flat-manager/ 2>/dev/null || :
    # Label nginx-served data dirs so httpd_t can read them
    for path in \
        '%{data_dir}/repos(/.*)?'  \
        '%{data_dir}/staticfiles(/.*)?'  \
        '%{data_dir}/media(/.*)?' ; do
        semanage fcontext -a -t httpd_sys_content_t "${path}" 2>/dev/null || \
        semanage fcontext -m -t httpd_sys_content_t "${path}" 2>/dev/null || :
    done
    restorecon -Rv %{data_dir}/repos %{data_dir}/staticfiles %{data_dir}/media 2>/dev/null || :
fi

# Install SELinux policy module (allows httpd_t to connect to daphne socket)
if command -v semodule >/dev/null 2>&1; then
    semodule -i %{_datadir}/selinux/packages/flat-manager-nginx.pp 2>/dev/null || :
fi

if [ $1 -eq 1 ] && [ ! -f %{conf_dir}/flat-manager.env ]; then
    cp %{conf_dir}/flat-manager.env.example %{conf_dir}/flat-manager.env
    chmod 0640 %{conf_dir}/flat-manager.env
    chown root:%{app_group} %{conf_dir}/flat-manager.env
fi

# Generate a self-signed snakeoil TLS certificate on first install
# if one does not already exist.
if [ $1 -eq 1 ] && [ ! -f /etc/pki/tls/certs/flat-manager.crt ]; then
    HOSTNAME=$(hostname -f 2>/dev/null || echo localhost)
    openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
        -keyout /etc/pki/tls/private/flat-manager.key \
        -out    /etc/pki/tls/certs/flat-manager.crt \
        -subj "/CN=${HOSTNAME}" 2>/dev/null || :
    chmod 0600 /etc/pki/tls/private/flat-manager.key
fi

# On upgrade: run migrations and restart the web service
if [ $1 -ge 2 ]; then
    echo "Running database migrations..."
    flat-manager-manage migrate --noinput 2>&1 || \
        echo "WARNING: migrate failed — run 'flat-manager-manage migrate' manually"
    if systemctl is-active --quiet flat-manager-web.service; then
        echo "Restarting flat-manager-web..."
        systemctl restart flat-manager-web.service 2>/dev/null || :
    fi
fi

if [ $1 -eq 1 ]; then
    echo ""
    echo "=================================================================="
    echo "  flat-manager-django installed — first-time setup"
    echo "=================================================================="
    echo "  Full guide: /opt/flat-manager/app/docs/INSTALL_RPM.md"
    echo ""
    echo "  Quick steps:"
    echo "  1. Start Redis/Valkey:  systemctl enable --now redis"
    echo "  2. Edit               /etc/flat-manager/flat-manager.env"
    echo "     Set SECRET_KEY, DB_PASSWORD, ALLOWED_HOSTS, etc."
    echo ""
    echo "  3. Create MariaDB database + user (see INSTALL_RPM.md)"
    echo ""
    echo "  4. flat-manager-manage migrate"
    echo "  5. flat-manager-manage createsuperuser"
    echo "  6. Set server_name in /etc/nginx/conf.d/flat-manager.conf"
    echo "     A snakeoil cert was auto-generated; replace with a real cert:"
    echo "     certbot --nginx -d hostname"
    echo "  7. systemctl enable --now nginx flat-manager.target"
    echo "=================================================================="
fi

%preun
%systemd_preun flat-manager-web.service flat-manager-celery.service flat-manager-celery-beat.service flat-manager.target
# Remove SELinux policy module on final uninstall
if [ $1 -eq 0 ] && command -v semodule >/dev/null 2>&1; then
    semodule -r flat-manager-nginx 2>/dev/null || :
fi

%postun
%systemd_postun_with_restart flat-manager-web.service flat-manager-celery.service flat-manager-celery-beat.service

# ─────────────────────────────────────────────────────────────────────────────
%files
%license README.md

# %dir declares the parent directory so it is owned; the trailing-slash glob
# that follows already implies the directory itself — no %dir needed for it.
# Must be owned by app_user: this is the home directory for the service account
# and flatpak user installations write to ~/.local inside it.
%dir %attr(0755, %{app_user}, %{app_group})    %{install_dir}
%{install_dir}/app/

%dir %attr(0755, %{app_user}, %{app_group})    %{data_dir}
%dir %attr(0755, %{app_user}, %{app_group})    %{data_dir}/repos
%dir %attr(0750, %{app_user}, %{app_group})    %{data_dir}/builds
%dir %attr(0755, %{app_user}, %{app_group})    %{data_dir}/media
%{data_dir}/staticfiles/
%dir %attr(0750, %{app_user}, %{app_group})    %{log_dir}

%dir %attr(0750, root, %{app_group})            %{conf_dir}
%attr(0640, root, %{app_group})                 %{conf_dir}/flat-manager.env.example
%ghost %attr(0640, root, %{app_group})          %{conf_dir}/flat-manager.env

%{_unitdir}/flat-manager-web.service
%{_unitdir}/flat-manager-celery.service
%{_unitdir}/flat-manager-celery-beat.service
%{_unitdir}/flat-manager.target
%{_tmpfilesdir}/flat-manager.conf

%config(noreplace) %{_sysconfdir}/nginx/conf.d/flat-manager.conf

%{_bindir}/flat-manager-manage
%{_datadir}/selinux/packages/flat-manager-nginx.pp

# ─────────────────────────────────────────────────────────────────────────────
%files          python-libs
# /opt/flat-manager is already owned by the main package (which this one
# requires), so we must NOT declare it again here — that would be "listed twice".
%{install_dir}/venv/
%{install_dir}/bst1-venv/

# ─────────────────────────────────────────────────────────────────────────────
%post           client
if [ $1 -eq 1 ] && [ ! -f %{_sysconfdir}/flat-manager-django-client/config ]; then
    cp %{_sysconfdir}/flat-manager-django-client/config.example \
       %{_sysconfdir}/flat-manager-django-client/config
    echo ""
    echo "================================================================="
    echo "  flat-manager-django-client installed"
    echo "================================================================="
    echo "  Edit /etc/flat-manager-django-client/config"
    echo "  then run the agent (e.g. via cron or manually):"
    echo "    flat-manager-checkin"
    echo "================================================================="
fi

# ─────────────────────────────────────────────────────────────────────────────
%files          client
%{_bindir}/flat-manager-checkin
%dir %{_sysconfdir}/flat-manager-django-client
%attr(0644, root, root) %{_sysconfdir}/flat-manager-django-client/config.example
%ghost %attr(0600, root, root) %{_sysconfdir}/flat-manager-django-client/config

# ─────────────────────────────────────────────────────────────────────────────
%changelog
* Fri Mar 27 2026 Release Bot <noreply@example.com> - 0.1.93-1
- Release 0.1.93

* Fri Mar 27 2026 Release Bot <noreply@example.com> - 0.1.92-1
- Release 0.1.92

* Fri Mar 27 2026 Release Bot <noreply@example.com> - 0.1.91-1
- Release 0.1.91

* Thu Mar 26 2026 Release Bot <noreply@example.com> - 0.1.90-1
- Release 0.1.90

* Thu Mar 26 2026 Release Bot <noreply@example.com> - 0.1.89-1
- Release 0.1.89

* Sat Mar 21 2026 Release Bot <noreply@example.com> - 0.1.88-1
- Release 0.1.88

* Fri Mar 20 2026 Release Bot <noreply@example.com> - 0.1.87-1
- Release 0.1.87

* Fri Mar 20 2026 Release Bot <noreply@example.com> - 0.1.86-1
- Release 0.1.86

* Fri Mar 20 2026 Release Bot <noreply@example.com> - 0.1.85-1
- Release 0.1.85

* Fri Mar 20 2026 Release Bot <noreply@example.com> - 0.1.84-1
- Release 0.1.84

* Fri Mar 20 2026 Release Bot <noreply@example.com> - 0.1.83-1
- Release 0.1.83

* Fri Mar 20 2026 Release Bot <noreply@example.com> - 0.1.82-1
- Release 0.1.82

* Fri Mar 20 2026 Release Bot <noreply@example.com> - 0.1.81-1
- Release 0.1.81

* Fri Mar 20 2026 Release Bot <noreply@example.com> - 0.1.80-1
- Release 0.1.80

* Fri Mar 20 2026 Release Bot <noreply@example.com> - 0.1.79-1
- Release 0.1.79

* Fri Mar 20 2026 Release Bot <noreply@example.com> - 0.1.78-1
- Release 0.1.78

* Fri Mar 20 2026 Release Bot <noreply@example.com> - 0.1.77-1
- Release 0.1.77

* Fri Mar 20 2026 Release Bot <noreply@example.com> - 0.1.76-1
- Release 0.1.76

* Fri Mar 20 2026 Release Bot <noreply@example.com> - 0.1.75-1
- Release 0.1.75

* Fri Mar 20 2026 Release Bot <noreply@example.com> - 0.1.74-1
- Release 0.1.74

* Fri Mar 20 2026 Release Bot <noreply@example.com> - 0.1.73-1
- Release 0.1.73

* Fri Mar 20 2026 Release Bot <noreply@example.com> - 0.1.72-1
- Release 0.1.72

* Fri Mar 20 2026 Release Bot <noreply@example.com> - 0.1.71-1
- Release 0.1.71

* Fri Mar 20 2026 Release Bot <noreply@example.com> - 0.1.70-1
- Release 0.1.70

* Fri Mar 20 2026 Release Bot <noreply@example.com> - 0.1.69-1
- Release 0.1.69

* Fri Mar 20 2026 Release Bot <noreply@example.com> - 0.1.68-1
- Release 0.1.68

* Thu Mar 19 2026 Release Bot <noreply@example.com> - 0.1.67-1
- Release 0.1.67

* Thu Mar 19 2026 Release Bot <noreply@example.com> - 0.1.66-1
- Release 0.1.66

* Thu Mar 19 2026 Release Bot <noreply@example.com> - 0.1.65-1
- Release 0.1.65

* Wed Mar 18 2026 Release Bot <noreply@example.com> - 0.1.64-1
- Release 0.1.64

* Tue Mar 17 2026 Release Bot <noreply@example.com> - 0.1.63-1
- Release 0.1.63

* Tue Mar 17 2026 Release Bot <noreply@example.com> - 0.1.62-1
- Release 0.1.62

* Tue Mar 17 2026 Release Bot <noreply@example.com> - 0.1.61-1
- Release 0.1.61

* Tue Mar 17 2026 Release Bot <noreply@example.com> - 0.1.60-1
- Release 0.1.60

* Tue Mar 17 2026 Release Bot <noreply@example.com> - 0.1.59-1
- Release 0.1.59

* Tue Mar 17 2026 Release Bot <noreply@example.com> - 0.1.58-1
- Release 0.1.58

* Mon Mar 16 2026 Release Bot <noreply@example.com> - 0.1.57-1
- Release 0.1.57

* Mon Mar 16 2026 Release Bot <noreply@example.com> - 0.1.56-1
- Release 0.1.56

* Mon Mar 16 2026 Release Bot <noreply@example.com> - 0.1.55-1
- Release 0.1.55

* Mon Mar 16 2026 Release Bot <noreply@example.com> - 0.1.54-1
- Release 0.1.54

* Mon Mar 16 2026 Release Bot <noreply@example.com> - 0.1.53-1
- Release 0.1.53

* Mon Mar 16 2026 Release Bot <noreply@example.com> - 0.1.52-1
- Release 0.1.52

* Mon Mar 16 2026 Release Bot <noreply@example.com> - 0.1.51-1
- Release 0.1.51

* Mon Mar 16 2026 Release Bot <noreply@example.com> - 0.1.50-1
- Release 0.1.50

* Mon Mar 16 2026 Release Bot <noreply@example.com> - 0.1.49-1
- Release 0.1.49

* Mon Mar 16 2026 Release Bot <noreply@example.com> - 0.1.48-1
- Release 0.1.48

* Mon Mar 16 2026 Release Bot <noreply@example.com> - 0.1.47-1
- Release 0.1.47

* Mon Mar 16 2026 Release Bot <noreply@example.com> - 0.1.46-1
- Release 0.1.46

* Mon Mar 16 2026 Release Bot <noreply@example.com> - 0.1.45-1
- Release 0.1.45

* Mon Mar 16 2026 Release Bot <noreply@example.com> - 0.1.44-1
- Release 0.1.44

* Mon Mar 16 2026 Release Bot <noreply@example.com> - 0.1.43-1
- Release 0.1.43

* Mon Mar 16 2026 Release Bot <noreply@example.com> - 0.1.42-1
- Release 0.1.42

* Mon Mar 16 2026 Release Bot <noreply@example.com> - 0.1.41-1
- Release 0.1.41

* Mon Mar 16 2026 Release Bot <noreply@example.com> - 0.1.40-1
- Release 0.1.40

* Sun Mar 15 2026 Release Bot <noreply@example.com> - 0.1.39-1
- Release 0.1.39

* Sun Mar 15 2026 Release Bot <noreply@example.com> - 0.1.38-1
- Release 0.1.38

* Sun Mar 15 2026 Release Bot <noreply@example.com> - 0.1.37-1
- Release 0.1.37

* Sun Mar 15 2026 Release Bot <noreply@example.com> - 0.1.36-1
- Release 0.1.36

* Sun Mar 15 2026 Release Bot <noreply@example.com> - 0.1.35-1
- Release 0.1.35

* Sun Mar 15 2026 Release Bot <noreply@example.com> - 0.1.34-1
- Release 0.1.34

* Sun Mar 15 2026 Release Bot <noreply@example.com> - 0.1.33-1
- Release 0.1.33

* Sun Mar 15 2026 Release Bot <noreply@example.com> - 0.1.32-1
- Release 0.1.32

* Sat Mar 14 2026 Release Bot <noreply@example.com> - 0.1.31-1
- Release 0.1.31

* Sat Mar 14 2026 Release Bot <noreply@example.com> - 0.1.30-1
- Release 0.1.30

* Sat Mar 14 2026 Release Bot <noreply@example.com> - 0.1.29-1
- Release 0.1.29

* Fri Mar 13 2026 Release Bot <noreply@example.com> - 0.1.28-1
- Release 0.1.28

* Fri Mar 13 2026 Release Bot <noreply@example.com> - 0.1.27-1
- Release 0.1.27

* Fri Mar 13 2026 Release Bot <noreply@example.com> - 0.1.26-1
- Release 0.1.26

* Fri Mar 13 2026 Release Bot <noreply@example.com> - 0.1.25-1
- Release 0.1.25

* Fri Mar 13 2026 Release Bot <noreply@example.com> - 0.1.24-1
- Release 0.1.24

* Fri Mar 13 2026 Release Bot <noreply@example.com> - 0.1.23-1
- Release 0.1.23

* Fri Mar 13 2026 Release Bot <noreply@example.com> - 0.1.22-1
- Release 0.1.22

* Fri Mar 13 2026 Release Bot <noreply@example.com> - 0.1.21-1
- Release 0.1.21

* Thu Mar 12 2026 Release Bot <noreply@example.com> - 0.1.20-1
- Release 0.1.20

* Thu Mar 12 2026 Release Bot <noreply@example.com> - 0.1.19-1
- Release 0.1.19

* Thu Mar 12 2026 Release Bot <noreply@example.com> - 0.1.18-1
- Release 0.1.18

* Thu Mar 12 2026 Release Bot <noreply@example.com> - 0.1.17-1
- Release 0.1.17

* Wed Mar 11 2026 Release Bot <noreply@example.com> - 0.1.16-1
- Release 0.1.16

* Wed Mar 11 2026 Release Bot <noreply@example.com> - 0.1.15-1
- Release 0.1.15

* Wed Mar 11 2026 Release Bot <noreply@example.com> - 0.1.14-1
- Release 0.1.14

* Wed Mar 11 2026 Release Bot <noreply@example.com> - 0.1.13-1
- Release 0.1.13

* Wed Mar 11 2026 Release Bot <noreply@example.com> - 0.1.12-1
- Release 0.1.12

* Wed Mar 11 2026 Release Bot <noreply@example.com> - 0.1.11-1
- Release 0.1.11

* Wed Mar 11 2026 Release Bot <noreply@example.com> - 0.1.10-1
- Release 0.1.10

* Tue Mar 10 2026 Release Bot <noreply@example.com> - 0.1.9-1
- Release 0.1.9

* Tue Mar 10 2026 Release Bot <noreply@example.com> - 0.1.8-1
- Release 0.1.8

* Tue Mar 10 2026 Release Bot <noreply@example.com> - 0.1.7-1
- Release 0.1.7

* Mon Mar 09 2026 Release Bot <noreply@example.com> - 0.1.6-1
- Release 0.1.6

* Mon Mar 09 2026 Release Bot <noreply@example.com> - 0.1.5-1
- Release 0.1.5

* Mon Mar 09 2026 Release Bot <noreply@example.com> - 0.1.4-1
- Release 0.1.4

* Mon Mar 09 2026 Release Bot <noreply@example.com> - 0.1.3-1
- Release 0.1.3

* Fri Mar 06 2026 Release Bot <noreply@example.com> - 0.1.2-1
- Release 0.1.2

* Fri Mar 06 2026 Release Bot <noreply@example.com> - 0.1.1-1
- Release 0.1.1

* Fri Mar 06 2026 RPM Bot <noreply@example.com> - 0.1.0-1
- Initial release
- Split virtualenv into flat-manager-django-python-libs subpackage
- flat-manager-django is noarch; python-libs carries compiled extensions

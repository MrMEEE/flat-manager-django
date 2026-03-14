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

# ── Exclude the bundled venv from RPM's shebang-mangling check ────────────────
# The venv contains upstream pip packages with shebangs we cannot control
# (e.g. #!/usr/bin/env python in Django's project template).
%global __brp_mangle_shebangs_exclude_from ^%{install_dir}/venv/

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
%global __requires_exclude_from ^%{install_dir}/venv/
%global __provides_exclude_from ^%{install_dir}/venv/

# ─────────────────────────────────────────────────────────────────────────────
#  Main package  (noarch — pure Python + config files)
# ─────────────────────────────────────────────────────────────────────────────
Name:           flat-manager-django
Version:        %{version_string}
Release:        1%{?dist}
Summary:        Flatpak repository manager — Django/Channels web application
License:        MIT
URL:            https://github.com/YOUR_ORG/flat-manager-django
Source0:        %{name}-%{version}.tar.gz
# python-libs subpackage contains compiled C extensions so the whole spec
# must be built for the target arch.  The main package contents are
# architecture-independent but will carry an arch suffix (e.g. .x86_64).

BuildRequires:  %{pypkg_prefix}
BuildRequires:  systemd-rpm-macros
BuildRequires:  checkpolicy
BuildRequires:  policycoreutils

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

# ─────────────────────────────────────────────────────────────────────────────
%changelog
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

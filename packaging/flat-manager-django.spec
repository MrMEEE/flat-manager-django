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
# RPM build support (optional but needed for mock-based builds)
Requires:       mock
Requires:       rpmdevtools

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

BuildRequires:  %{pypkg_prefix}-devel
BuildRequires:  gcc
BuildRequires:  gcc-c++
BuildRequires:  mariadb-connector-c-devel
BuildRequires:  rust
BuildRequires:  cargo

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

# ── Build virtualenv with all Python dependencies ─────────────────────────────
%{pybin} -m venv %{_builddir}/fmvenv
%{_builddir}/fmvenv/bin/pip install --upgrade pip --quiet
%{_builddir}/fmvenv/bin/pip install -r requirements.txt --quiet
# EL9 does not provide an lzip RPM. Install the PyPI module and expose a small
# compatibility CLI for BuildStream tar sources that need an external `lzip`.
%{_builddir}/fmvenv/bin/pip install 'lzip>=1.2.0' --quiet
cat > %{_builddir}/fmvenv/bin/lzip <<'EOF'
#!%{_builddir}/fmvenv/bin/python
import os
import sys

import lzip


def _fail(message):
    print(f"lzip wrapper: {message}", file=sys.stderr)
    return 2


def main():
    decompress = False
    to_stdout = False
    keep_input = False
    force = False
    test_only = False
    files = []

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == '--':
            files.extend(args[i + 1:])
            break
        if arg.startswith('--'):
            if arg == '--decompress':
                decompress = True
            elif arg == '--stdout':
                to_stdout = True
            elif arg == '--keep':
                keep_input = True
            elif arg == '--force':
                force = True
            elif arg == '--test':
                test_only = True
            elif arg == '--quiet':
                pass
            else:
                return _fail(f"unsupported option {arg}")
        elif arg.startswith('-') and arg != '-':
            for ch in arg[1:]:
                if ch == 'd':
                    decompress = True
                elif ch == 'c':
                    to_stdout = True
                elif ch == 'k':
                    keep_input = True
                elif ch == 'f':
                    force = True
                elif ch == 't':
                    test_only = True
                elif ch == 'q':
                    pass
                else:
                    return _fail(f"unsupported option -{ch}")
        else:
            files.append(arg)
        i += 1

    if not files:
        return _fail('stdin mode is not supported')

    # This wrapper implements decompression only, which is what BuildStream
    # needs for .tar.lz sources.
    if not (decompress or test_only or to_stdout):
        decompress = True

    exit_code = 0
    for path in files:
        try:
            data = lzip.decompress_file(path)
            if test_only:
                continue
            if to_stdout:
                sys.stdout.buffer.write(data)
                continue

            out_path = path[:-3] if path.endswith('.lz') else f"{path}.out"
            if os.path.exists(out_path) and not force:
                return _fail(f"output exists: {out_path}")
            with open(out_path, 'wb') as fh:
                fh.write(data)
            if not keep_input:
                try:
                    os.unlink(path)
                except OSError:
                    pass
        except Exception as exc:
            print(f"lzip wrapper: failed to decompress {path}: {exc}", file=sys.stderr)
            exit_code = 1

    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
EOF
chmod 0755 %{_builddir}/fmvenv/bin/lzip

%if 0%{?rhel} != 9
%global bst1pybin    python3
%endif

# ── Build separate virtualenv for BuildStream 1 ───────────────────────────────
# BST 1 and BST 2 use incompatible project.conf formats; each needs its own
# isolated venv.  BST 1.x requires Python ≤ 3.11 — configparser.SafeConfigParser
# was removed in 3.12.  Always use python3.11 here regardless of the main pybin.
# Pin to the 1.6.x stable series; 1.9x.dev builds are actually early BST 2.
%{bst1pybin} -m venv %{_builddir}/bst1venv
%{_builddir}/bst1venv/bin/python -m ensurepip --upgrade 2>/dev/null || true
%{_builddir}/bst1venv/bin/pip install --upgrade pip --quiet
%{_builddir}/bst1venv/bin/pip install 'lzip>=1.2.0' --quiet
cat > %{_builddir}/bst1venv/bin/lzip <<'EOF'
#!%{_builddir}/bst1venv/bin/python
import os
import sys

import lzip


def _fail(message):
    print(f"lzip wrapper: {message}", file=sys.stderr)
    return 2


def main():
    decompress = False
    to_stdout = False
    keep_input = False
    force = False
    test_only = False
    files = []

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == '--':
            files.extend(args[i + 1:])
            break
        if arg.startswith('--'):
            if arg == '--decompress':
                decompress = True
            elif arg == '--stdout':
                to_stdout = True
            elif arg == '--keep':
                keep_input = True
            elif arg == '--force':
                force = True
            elif arg == '--test':
                test_only = True
            elif arg == '--quiet':
                pass
            else:
                return _fail(f"unsupported option {arg}")
        elif arg.startswith('-') and arg != '-':
            for ch in arg[1:]:
                if ch == 'd':
                    decompress = True
                elif ch == 'c':
                    to_stdout = True
                elif ch == 'k':
                    keep_input = True
                elif ch == 'f':
                    force = True
                elif ch == 't':
                    test_only = True
                elif ch == 'q':
                    pass
                else:
                    return _fail(f"unsupported option -{ch}")
        else:
            files.append(arg)
        i += 1

    if not files:
        return _fail('stdin mode is not supported')

    if not (decompress or test_only or to_stdout):
        decompress = True

    exit_code = 0
    for path in files:
        try:
            data = lzip.decompress_file(path)
            if test_only:
                continue
            if to_stdout:
                sys.stdout.buffer.write(data)
                continue

            out_path = path[:-3] if path.endswith('.lz') else f"{path}.out"
            if os.path.exists(out_path) and not force:
                return _fail(f"output exists: {out_path}")
            with open(out_path, 'wb') as fh:
                fh.write(data)
            if not keep_input:
                try:
                    os.unlink(path)
                except OSError:
                    pass
        except Exception as exc:
            print(f"lzip wrapper: failed to decompress {path}: {exc}", file=sys.stderr)
            exit_code = 1

    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
EOF
chmod 0755 %{_builddir}/bst1venv/bin/lzip

# Only for RHEL 9 for now
%if 0%{?rhel} == 9
%{_builddir}/bst1venv/bin/pip install 'BuildStream>=1.0,<1.7' --quiet
%endif

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
install -d -m 0750 %{buildroot}%{data_dir}/rpm-repos
install -d -m 0755 %{buildroot}%{data_dir}/media
install -d -m 0750 %{buildroot}%{data_dir}/tmp
install -d -m 0750 %{buildroot}%{log_dir}
install -d -m 0750 %{buildroot}%{conf_dir}

# ── systemd units ─────────────────────────────────────────────────────────────
install -D -m 0644 packaging/systemd/flat-manager-web.service \
    %{buildroot}%{_unitdir}/flat-manager-web.service
install -D -m 0644 packaging/systemd/flat-manager-celery.service \
    %{buildroot}%{_unitdir}/flat-manager-celery.service
install -D -m 0644 packaging/systemd/flat-manager-celery-ops.service \
    %{buildroot}%{_unitdir}/flat-manager-celery-ops.service
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
install -D -m 0755 packaging/flat-manager-manage \
    %{buildroot}%{_bindir}/flat-manager-manage

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
%systemd_post flat-manager-web.service flat-manager-celery.service flat-manager-celery-ops.service flat-manager-celery-beat.service flat-manager.target

#chown -R %{app_user}:%{app_group} %{data_dir} %{log_dir} %{conf_dir}
#chown    %{app_user}:%{app_group} %{install_dir}
systemd-tmpfiles --create %{_tmpfilesdir}/flat-manager.conf 2>/dev/null || :

# Add nginx to the flat-manager group so it can read OSTree repo data served
# under /repositories/.  Required because dynamically created repo files are
# owned flat-manager:flat-manager; nginx (httpd_t) needs group read access.
getent passwd nginx >/dev/null 2>&1 && usermod -aG %{app_group} nginx || :

# Add the flat-manager service user to the mock group so it can run
# mock-based RPM builds without root privileges.
getent group mock >/dev/null 2>&1 && usermod -aG mock %{app_user} || :

# Ensure the flat-manager user has subuid/subgid ranges so that rootless
# podman (used for container-based RPM repo discovery) can create user
# namespaces.  Only adds the range if it is not already present.
if ! grep -q '^%{app_user}:' /etc/subuid 2>/dev/null; then
    usermod --add-subuids 100000-165535 %{app_user} || :
fi
if ! grep -q '^%{app_user}:' /etc/subgid 2>/dev/null; then
    usermod --add-subgids 100000-165535 %{app_user} || :
fi

# Label /var/run/flat-manager/ so nginx (httpd_t) can connect to the UNIX socket.
# Without this SELinux denies httpd_t write access to var_run_t sock_file.
# Note: use /var/run (not /run) — semanage requires the canonical path.
#if command -v semanage >/dev/null 2>&1; then
#    semanage fcontext -a -t httpd_var_run_t '/var/run/flat-manager(/.*)?' 2>/dev/null || \
#    semanage fcontext -m -t httpd_var_run_t '/var/run/flat-manager(/.*)?' 2>/dev/null || :
#    restorecon -Rv /run/flat-manager/ 2>/dev/null || :
#    # Label nginx-served data dirs so httpd_t can read them
#    for path in \
#        '%{data_dir}/repos(/.*)?'  \
#        '%{data_dir}/staticfiles(/.*)?'  \
#        '%{data_dir}/media(/.*)?' ; do
#        semanage fcontext -a -t httpd_sys_content_t "${path}" 2>/dev/null || \
#        semanage fcontext -m -t httpd_sys_content_t "${path}" 2>/dev/null || :
#    done
#    restorecon -Rv %{data_dir}/repos %{data_dir}/staticfiles %{data_dir}/media 2>/dev/null || :
#fi

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

# On upgrade: run migrations and restart services
if [ $1 -ge 2 ]; then
    echo "Running database migrations..."
    flat-manager-manage migrate --noinput 2>&1 || \
        echo "WARNING: migrate failed — run 'flat-manager-manage migrate' manually"
    for svc in flat-manager-web.service flat-manager-celery.service flat-manager-celery-ops.service flat-manager-celery-beat.service; do
        if systemctl is-active --quiet "${svc}"; then
            echo "Restarting ${svc}..."
            systemctl restart "${svc}" 2>/dev/null || :
        fi
    done
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
%systemd_preun flat-manager-web.service flat-manager-celery.service flat-manager-celery-ops.service flat-manager-celery-beat.service flat-manager.target
# Remove SELinux policy module on final uninstall
if [ $1 -eq 0 ] && command -v semodule >/dev/null 2>&1; then
    semodule -r flat-manager-nginx 2>/dev/null || :
fi

%postun
%systemd_postun_with_restart flat-manager-web.service flat-manager-celery.service flat-manager-celery-ops.service flat-manager-celery-beat.service

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
%dir %attr(0750, %{app_user}, %{app_group})    %{data_dir}/rpm-repos
%dir %attr(0755, %{app_user}, %{app_group})    %{data_dir}/media
%dir %attr(0750, %{app_user}, %{app_group})    %{data_dir}/tmp
%{data_dir}/staticfiles/
%dir %attr(0750, %{app_user}, %{app_group})    %{log_dir}

%dir %attr(0750, root, %{app_group})            %{conf_dir}
%attr(0640, root, %{app_group})                 %{conf_dir}/flat-manager.env.example
%ghost %attr(0640, root, %{app_group})          %{conf_dir}/flat-manager.env

%{_unitdir}/flat-manager-web.service
%{_unitdir}/flat-manager-celery.service
%{_unitdir}/flat-manager-celery-ops.service
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
* Tue Jul 28 2026 Release Bot <m@rtinjuhl.dk> - 0.9.19-1
- Release 0.9.19

* Tue Jul 28 2026 Release Bot <m@rtinjuhl.dk> - 0.9.18-1
- Release 0.9.18

* Mon Jul 27 2026 Release Bot <m@rtinjuhl.dk> - 0.9.17-1
- Release 0.9.17

* Mon Jul 27 2026 Release Bot <m@rtinjuhl.dk> - 0.9.16-1
- Release 0.9.16

* Mon Jul 27 2026 Release Bot <m@rtinjuhl.dk> - 0.9.15-1
- Release 0.9.15

* Fri Jul 24 2026 Release Bot <m@rtinjuhl.dk> - 0.9.14-1
- Release 0.9.14

* Wed Jul 22 2026 Release Bot <m@rtinjuhl.dk> - 0.9.13-1
- Release 0.9.13

* Wed Jul 22 2026 Release Bot <m@rtinjuhl.dk> - 0.9.12-1
- Release 0.9.12

* Thu Jul 16 2026 Release Bot <m@rtinjuhl.dk> - 0.9.11-1
- Release 0.9.11

* Thu Jul 16 2026 Release Bot <m@rtinjuhl.dk> - 0.9.10-1
- Release 0.9.10

* Wed Jul 15 2026 Release Bot <m@rtinjuhl.dk> - 0.9.9-1
- Release 0.9.9

* Wed Jul 15 2026 Release Bot <m@rtinjuhl.dk> - 0.9.8-1
- Release 0.9.8

* Wed Jul 15 2026 Release Bot <m@rtinjuhl.dk> - 0.9.7-1
- Release 0.9.7

* Wed Jul 15 2026 Release Bot <m@rtinjuhl.dk> - 0.9.6-1
- Release 0.9.6

* Wed Jul 15 2026 Release Bot <m@rtinjuhl.dk> - 0.9.5-1
- Release 0.9.5

* Wed Jul 15 2026 Release Bot <m@rtinjuhl.dk> - 0.9.4-1
- Release 0.9.4

* Wed Jul 15 2026 Release Bot <m@rtinjuhl.dk> - 0.9.3-1
- Release 0.9.3

* Tue Jul 14 2026 Release Bot <m@rtinjuhl.dk> - 0.9.2-1
- Release 0.9.2

* Sat Jul 11 2026 Release Bot <m@rtinjuhl.dk> - 0.9.1-1
- Release 0.9.1

* Sat Jul 11 2026 Release Bot <m@rtinjuhl.dk> - 0.9.0-1
- Release 0.9.0

* Thu Jul 02 2026 Release Bot <m@rtinjuhl.dk> - 0.8.23-1
- Release 0.8.23

* Tue Jun 02 2026 Release Bot <m@rtinjuhl.dk> - 0.8.22-1
- Release 0.8.22

* Tue Jun 02 2026 Release Bot <m@rtinjuhl.dk> - 0.8.21-1
- Release 0.8.21

* Tue Jun 02 2026 Release Bot <m@rtinjuhl.dk> - 0.8.20-1
- Release 0.8.20

* Tue Jun 02 2026 Release Bot <m@rtinjuhl.dk> - 0.8.19-1
- Release 0.8.19

* Mon Jun 01 2026 Release Bot <m@rtinjuhl.dk> - 0.8.18-1
- Release 0.8.18

* Mon Jun 01 2026 Release Bot <m@rtinjuhl.dk> - 0.8.17-1
- Release 0.8.17

* Mon Jun 01 2026 Release Bot <m@rtinjuhl.dk> - 0.8.16-1
- Release 0.8.16

* Mon Jun 01 2026 Release Bot <m@rtinjuhl.dk> - 0.8.15-1
- Release 0.8.15

* Mon Jun 01 2026 Release Bot <m@rtinjuhl.dk> - 0.8.14-1
- Release 0.8.14

* Fri May 29 2026 Release Bot <m@rtinjuhl.dk> - 0.8.13-1
- Release 0.8.13

* Fri May 29 2026 Release Bot <m@rtinjuhl.dk> - 0.8.12-1
- Release 0.8.12

* Wed May 27 2026 Release Bot <m@rtinjuhl.dk> - 0.8.11-1
- Release 0.8.11

* Wed May 27 2026 Release Bot <m@rtinjuhl.dk> - 0.8.10-1
- Release 0.8.10

* Wed May 27 2026 Release Bot <m@rtinjuhl.dk> - 0.8.9-1
- Release 0.8.9

* Tue May 26 2026 Release Bot <m@rtinjuhl.dk> - 0.8.8-1
- Release 0.8.8

* Tue May 26 2026 Release Bot <m@rtinjuhl.dk> - 0.8.7-1
- Release 0.8.7

* Tue May 26 2026 Release Bot <m@rtinjuhl.dk> - 0.8.6-1
- Release 0.8.6

* Tue May 26 2026 Release Bot <m@rtinjuhl.dk> - 0.8.5-1
- Release 0.8.5

* Tue May 26 2026 Release Bot <m@rtinjuhl.dk> - 0.8.4-1
- Release 0.8.4

* Fri May 22 2026 Release Bot <m@rtinjuhl.dk> - 0.8.3-1
- Release 0.8.3

* Fri May 22 2026 Release Bot <m@rtinjuhl.dk> - 0.8.2-1
- Release 0.8.2

* Mon May 18 2026 Release Bot <m@rtinjuhl.dk> - 0.8.1-1
- Release 0.8.1

* Mon May 18 2026 Release Bot <m@rtinjuhl.dk> - 0.8.0-1
- Release 0.8.0

* Mon May 18 2026 Release Bot <m@rtinjuhl.dk> - 0.7.15-1
- Release 0.7.15

* Mon May 18 2026 Release Bot <m@rtinjuhl.dk> - 0.7.14-1
- Release 0.7.14

* Mon May 18 2026 Release Bot <m@rtinjuhl.dk> - 0.7.13-1
- Release 0.7.13

* Mon May 18 2026 Release Bot <m@rtinjuhl.dk> - 0.7.12-1
- Release 0.7.12

* Mon May 18 2026 Release Bot <m@rtinjuhl.dk> - 0.7.11-1
- Release 0.7.11

* Mon May 18 2026 Release Bot <m@rtinjuhl.dk> - 0.7.10-1
- Release 0.7.10

* Thu May 14 2026 Release Bot <m@rtinjuhl.dk> - 0.7.9-1
- Release 0.7.9

* Wed May 13 2026 Release Bot <m@rtinjuhl.dk> - 0.7.8-1
- Release 0.7.8

* Wed May 13 2026 Release Bot <m@rtinjuhl.dk> - 0.7.7-1
- Release 0.7.7

* Wed May 13 2026 Release Bot <m@rtinjuhl.dk> - 0.7.6-1
- Release 0.7.6

* Wed May 13 2026 Release Bot <m@rtinjuhl.dk> - 0.7.5-1
- Release 0.7.5

* Wed May 13 2026 Release Bot <m@rtinjuhl.dk> - 0.7.4-1
- Release 0.7.4

* Wed May 13 2026 Release Bot <m@rtinjuhl.dk> - 0.7.3-1
- Release 0.7.3

* Wed May 13 2026 Release Bot <m@rtinjuhl.dk> - 0.7.2-1
- Release 0.7.2

* Wed May 13 2026 Release Bot <m@rtinjuhl.dk> - 0.7.1-1
- Release 0.7.1

* Tue May 12 2026 Release Bot <m@rtinjuhl.dk> - 0.7.0-1
- Release 0.7.0

* Tue May 12 2026 Release Bot <m@rtinjuhl.dk> - 0.6.32-1
- Release 0.6.32

* Tue May 12 2026 Release Bot <m@rtinjuhl.dk> - 0.6.31-1
- Release 0.6.31

* Tue May 12 2026 Release Bot <m@rtinjuhl.dk> - 0.6.30-1
- Release 0.6.30

* Tue May 12 2026 Release Bot <m@rtinjuhl.dk> - 0.6.29-1
- Release 0.6.29

* Tue May 12 2026 Release Bot <m@rtinjuhl.dk> - 0.6.28-1
- Release 0.6.28

* Mon May 11 2026 Release Bot <m@rtinjuhl.dk> - 0.6.27-1
- Release 0.6.27

* Fri May 01 2026 Release Bot <m@rtinjuhl.dk> - 0.6.26-1
- Release 0.6.26

* Fri May 01 2026 Release Bot <m@rtinjuhl.dk> - 0.6.25-1
- Release 0.6.25

* Fri May 01 2026 Release Bot <m@rtinjuhl.dk> - 0.6.24-1
- Release 0.6.24

* Thu Apr 30 2026 Release Bot <m@rtinjuhl.dk> - 0.6.23-1
- Release 0.6.23

* Wed Apr 29 2026 Release Bot <m@rtinjuhl.dk> - 0.6.22-1
- Release 0.6.22

* Wed Apr 29 2026 Release Bot <m@rtinjuhl.dk> - 0.6.21-1
- Release 0.6.21

* Wed Apr 29 2026 Release Bot <m@rtinjuhl.dk> - 0.6.20-1
- Release 0.6.20

* Wed Apr 29 2026 Release Bot <m@rtinjuhl.dk> - 0.6.19-1
- Release 0.6.19

* Fri Apr 24 2026 Release Bot <m@rtinjuhl.dk> - 0.6.18-1
- Release 0.6.18

* Fri Apr 24 2026 Release Bot <m@rtinjuhl.dk> - 0.6.17-1
- Release 0.6.17

* Fri Apr 24 2026 Release Bot <m@rtinjuhl.dk> - 0.6.16-1
- Release 0.6.16

* Fri Apr 24 2026 Release Bot <m@rtinjuhl.dk> - 0.6.15-1
- Release 0.6.15

* Fri Apr 24 2026 Release Bot <m@rtinjuhl.dk> - 0.6.14-1
- Release 0.6.14

* Fri Apr 24 2026 Release Bot <m@rtinjuhl.dk> - 0.6.13-1
- Release 0.6.13

* Fri Apr 24 2026 Release Bot <m@rtinjuhl.dk> - 0.6.12-1
- Release 0.6.12

* Fri Apr 24 2026 Release Bot <m@rtinjuhl.dk> - 0.6.11-1
- Release 0.6.11

* Fri Apr 24 2026 Release Bot <m@rtinjuhl.dk> - 0.6.10-1
- Release 0.6.10

* Thu Apr 23 2026 Release Bot <m@rtinjuhl.dk> - 0.6.9-1
- Release 0.6.9

* Thu Apr 23 2026 Release Bot <m@rtinjuhl.dk> - 0.6.8-1
- Release 0.6.8

* Thu Apr 23 2026 Release Bot <m@rtinjuhl.dk> - 0.6.7-1
- Release 0.6.7

* Thu Apr 23 2026 Release Bot <m@rtinjuhl.dk> - 0.6.6-1
- Release 0.6.6

* Thu Apr 23 2026 Release Bot <m@rtinjuhl.dk> - 0.6.5-1
- Release 0.6.5

* Thu Apr 23 2026 Release Bot <m@rtinjuhl.dk> - 0.6.4-1
- Release 0.6.4

* Thu Apr 23 2026 Release Bot <m@rtinjuhl.dk> - 0.6.3-1
- Release 0.6.3

* Thu Apr 23 2026 Release Bot <m@rtinjuhl.dk> - 0.6.2-1
- Release 0.6.2

* Wed Apr 22 2026 Release Bot <m@rtinjuhl.dk> - 0.6.1-1
- Release 0.6.1

* Wed Apr 22 2026 Release Bot <m@rtinjuhl.dk> - 0.6.0-1
- Release 0.6.0

* Fri Apr 17 2026 Release Bot <m@rtinjuhl.dk> - 0.5.14-1
- Release 0.5.14

* Fri Apr 17 2026 Release Bot <m@rtinjuhl.dk> - 0.5.13-1
- Release 0.5.13

* Thu Apr 16 2026 Release Bot <m@rtinjuhl.dk> - 0.5.12-1
- Release 0.5.12

* Thu Apr 16 2026 Release Bot <m@rtinjuhl.dk> - 0.5.11-1
- Release 0.5.11

* Thu Apr 16 2026 Release Bot <m@rtinjuhl.dk> - 0.5.10-1
- Release 0.5.10

* Thu Apr 16 2026 Release Bot <m@rtinjuhl.dk> - 0.5.9-1
- Release 0.5.9

* Thu Apr 16 2026 Release Bot <m@rtinjuhl.dk> - 0.5.8-1
- Release 0.5.8

* Wed Apr 15 2026 Release Bot <m@rtinjuhl.dk> - 0.5.7-1
- Release 0.5.7

* Wed Apr 15 2026 Release Bot <m@rtinjuhl.dk> - 0.5.6-1
- Release 0.5.6

* Wed Apr 15 2026 Release Bot <m@rtinjuhl.dk> - 0.5.5-1
- Release 0.5.5

* Tue Apr 14 2026 Release Bot <m@rtinjuhl.dk> - 0.5.4-1
- Release 0.5.4

* Tue Apr 14 2026 Release Bot <m@rtinjuhl.dk> - 0.5.3-1
- Release 0.5.3

* Mon Apr 13 2026 Release Bot <m@rtinjuhl.dk> - 0.5.2-1
- Release 0.5.2

* Mon Apr 13 2026 Release Bot <m@rtinjuhl.dk> - 0.5.1-1
- Release 0.5.1

* Sun Apr 12 2026 Release Bot <m@rtinjuhl.dk> - 0.5.0-1
- Release 0.5.0

* Sun Apr 12 2026 Release Bot <m@rtinjuhl.dk> - 0.4.21-1
- Release 0.4.21

* Sun Apr 12 2026 Release Bot <m@rtinjuhl.dk> - 0.4.20-1
- Release 0.4.20

* Sun Apr 12 2026 Release Bot <m@rtinjuhl.dk> - 0.4.19-1
- Release 0.4.19

* Sun Apr 12 2026 Release Bot <m@rtinjuhl.dk> - 0.4.18-1
- Release 0.4.18

* Fri Apr 10 2026 Release Bot <m@rtinjuhl.dk> - 0.4.17-1
- Release 0.4.17

* Tue Apr 07 2026 Release Bot <m@rtinjuhl.dk> - 0.4.16-1
- Release 0.4.16

* Tue Apr 07 2026 Release Bot <m@rtinjuhl.dk> - 0.4.15-1
- Release 0.4.15

* Mon Apr 06 2026 Release Bot <m@rtinjuhl.dk> - 0.4.14-1
- Release 0.4.14

* Mon Apr 06 2026 Release Bot <m@rtinjuhl.dk> - 0.4.13-1
- Release 0.4.13

* Wed Apr 01 2026 Release Bot <m@rtinjuhl.dk> - 0.4.12-1
- Release 0.4.12

* Wed Apr 01 2026 Release Bot <m@rtinjuhl.dk> - 0.4.11-1
- Release 0.4.11

* Wed Apr 01 2026 Release Bot <m@rtinjuhl.dk> - 0.4.10-1
- Release 0.4.10

* Wed Apr 01 2026 Release Bot <m@rtinjuhl.dk> - 0.4.9-1
- Release 0.4.9

* Tue Mar 31 2026 Release Bot <m@rtinjuhl.dk> - 0.4.8-1
- Release 0.4.8

* Tue Mar 31 2026 Release Bot <m@rtinjuhl.dk> - 0.4.7-1
- Release 0.4.7

* Tue Mar 31 2026 Release Bot <m@rtinjuhl.dk> - 0.4.6-1
- Release 0.4.6

* Tue Mar 31 2026 Release Bot <m@rtinjuhl.dk> - 0.4.5-1
- Release 0.4.5

* Tue Mar 31 2026 Release Bot <m@rtinjuhl.dk> - 0.4.4-1
- Release 0.4.4

* Tue Mar 31 2026 Release Bot <m@rtinjuhl.dk> - 0.4.3-1
- Release 0.4.3

* Tue Mar 31 2026 Release Bot <m@rtinjuhl.dk> - 0.4.2-1
- Release 0.4.2

* Tue Mar 31 2026 Release Bot <m@rtinjuhl.dk> - 0.4.1-1
- Release 0.4.1

* Tue Mar 31 2026 Release Bot <m@rtinjuhl.dk> - 0.4.0-1
- Release 0.4.0

* Tue Mar 31 2026 Release Bot <m@rtinjuhl.dk> - 0.3.9-1
- Release 0.3.9

* Tue Mar 31 2026 Release Bot <m@rtinjuhl.dk> - 0.3.8-1
- Release 0.3.8

* Tue Mar 31 2026 Release Bot <m@rtinjuhl.dk> - 0.3.7-1
- Release 0.3.7

* Mon Mar 30 2026 Release Bot <m@rtinjuhl.dk> - 0.3.6-1
- Release 0.3.6

* Mon Mar 30 2026 Release Bot <m@rtinjuhl.dk> - 0.3.5-1
- Release 0.3.5

* Mon Mar 30 2026 Release Bot <m@rtinjuhl.dk> - 0.3.4-1
- Release 0.3.4

* Mon Mar 30 2026 Release Bot <m@rtinjuhl.dk> - 0.3.3-1
- Release 0.3.3

* Mon Mar 30 2026 Release Bot <m@rtinjuhl.dk> - 0.3.2-1
- Release 0.3.2

* Mon Mar 30 2026 Release Bot <m@rtinjuhl.dk> - 0.3.1-1
- Release 0.3.1

* Mon Mar 30 2026 Release Bot <m@rtinjuhl.dk> - 0.3.0-1
- Release 0.3.0

* Mon Mar 30 2026 Release Bot <m@rtinjuhl.dk> - 0.2.2-1
- Release 0.2.2

* Mon Mar 30 2026 Release Bot <m@rtinjuhl.dk> - 0.2.1-1
- Release 0.2.1

* Mon Mar 30 2026 Release Bot <m@rtinjuhl.dk> - 0.2.0-1
- Release 0.2.0

* Mon Mar 30 2026 Release Bot <m@rtinjuhl.dk> - 0.1.125-1
- Release 0.1.125

* Mon Mar 30 2026 Release Bot <m@rtinjuhl.dk> - 0.1.124-1
- Release 0.1.124

* Mon Mar 30 2026 Release Bot <m@rtinjuhl.dk> - 0.1.123-1
- Release 0.1.123

* Mon Mar 30 2026 Release Bot <m@rtinjuhl.dk> - 0.1.122-1
- Release 0.1.122

* Sun Mar 29 2026 Release Bot <m@rtinjuhl.dk> - 0.1.120-1
- Release 0.1.120

* Sun Mar 29 2026 Release Bot <m@rtinjuhl.dk> - 0.1.119-1
- Release 0.1.119

* Sun Mar 29 2026 Release Bot <m@rtinjuhl.dk> - 0.1.118-1
- Release 0.1.118

* Sun Mar 29 2026 Release Bot <m@rtinjuhl.dk> - 0.1.117-1
- Release 0.1.117

* Sun Mar 29 2026 Release Bot <m@rtinjuhl.dk> - 0.1.116-1
- Release 0.1.116

* Sun Mar 29 2026 Release Bot <m@rtinjuhl.dk> - 0.1.115-1
- Release 0.1.115

* Sun Mar 29 2026 Release Bot <m@rtinjuhl.dk> - 0.1.114-1
- Release 0.1.114

* Sun Mar 29 2026 Release Bot <m@rtinjuhl.dk> - 0.1.113-1
- Release 0.1.113

* Sun Mar 29 2026 Release Bot <m@rtinjuhl.dk> - 0.1.112-1
- Release 0.1.112

* Sun Mar 29 2026 Release Bot <m@rtinjuhl.dk> - 0.1.111-1
- Release 0.1.111

* Sat Mar 28 2026 Release Bot <m@rtinjuhl.dk> - 0.1.110-1
- Release 0.1.110

* Sat Mar 28 2026 Release Bot <m@rtinjuhl.dk> - 0.1.109-1
- Release 0.1.109

* Sat Mar 28 2026 Release Bot <m@rtinjuhl.dk> - 0.1.108-1
- Release 0.1.108

* Sat Mar 28 2026 Release Bot <m@rtinjuhl.dk> - 0.1.107-1
- Release 0.1.107

* Sat Mar 28 2026 Release Bot <m@rtinjuhl.dk> - 0.1.106-1
- Release 0.1.106

* Sat Mar 28 2026 Release Bot <m@rtinjuhl.dk> - 0.1.105-1
- Release 0.1.105

* Sat Mar 28 2026 Release Bot <m@rtinjuhl.dk> - 0.1.104-1
- Release 0.1.104

* Fri Mar 27 2026 Release Bot <m@rtinjuhl.dk> - 0.1.103-1
- Release 0.1.103

* Fri Mar 27 2026 Release Bot <m@rtinjuhl.dk> - 0.1.102-1
- Release 0.1.102

* Fri Mar 27 2026 Release Bot <m@rtinjuhl.dk> - 0.1.101-1
- Release 0.1.101

* Fri Mar 27 2026 Release Bot <m@rtinjuhl.dk> - 0.1.100-1
- Release 0.1.100

* Fri Mar 27 2026 Release Bot <m@rtinjuhl.dk> - 0.1.99-1
- Release 0.1.99

* Fri Mar 27 2026 Release Bot <m@rtinjuhl.dk> - 0.1.98-1
- Release 0.1.98

* Fri Mar 27 2026 Release Bot <m@rtinjuhl.dk> - 0.1.97-1
- Release 0.1.97

* Fri Mar 27 2026 Release Bot <m@rtinjuhl.dk> - 0.1.96-1
- Release 0.1.96

* Fri Mar 27 2026 Release Bot <m@rtinjuhl.dk> - 0.1.95-1
- Release 0.1.95

* Fri Mar 27 2026 Release Bot <m@rtinjuhl.dk> - 0.1.94-1
- Release 0.1.94

* Fri Mar 27 2026 Release Bot <m@rtinjuhl.dk> - 0.1.93-1
- Release 0.1.93

* Fri Mar 27 2026 Release Bot <m@rtinjuhl.dk> - 0.1.92-1
- Release 0.1.92

* Fri Mar 27 2026 Release Bot <m@rtinjuhl.dk> - 0.1.91-1
- Release 0.1.91

* Thu Mar 26 2026 Release Bot <m@rtinjuhl.dk> - 0.1.90-1
- Release 0.1.90

* Thu Mar 26 2026 Release Bot <m@rtinjuhl.dk> - 0.1.89-1
- Release 0.1.89

* Sat Mar 21 2026 Release Bot <m@rtinjuhl.dk> - 0.1.88-1
- Release 0.1.88

* Fri Mar 20 2026 Release Bot <m@rtinjuhl.dk> - 0.1.87-1
- Release 0.1.87

* Fri Mar 20 2026 Release Bot <m@rtinjuhl.dk> - 0.1.86-1
- Release 0.1.86

* Fri Mar 20 2026 Release Bot <m@rtinjuhl.dk> - 0.1.85-1
- Release 0.1.85

* Fri Mar 20 2026 Release Bot <m@rtinjuhl.dk> - 0.1.84-1
- Release 0.1.84

* Fri Mar 20 2026 Release Bot <m@rtinjuhl.dk> - 0.1.83-1
- Release 0.1.83

* Fri Mar 20 2026 Release Bot <m@rtinjuhl.dk> - 0.1.82-1
- Release 0.1.82

* Fri Mar 20 2026 Release Bot <m@rtinjuhl.dk> - 0.1.81-1
- Release 0.1.81

* Fri Mar 20 2026 Release Bot <m@rtinjuhl.dk> - 0.1.80-1
- Release 0.1.80

* Fri Mar 20 2026 Release Bot <m@rtinjuhl.dk> - 0.1.79-1
- Release 0.1.79

* Fri Mar 20 2026 Release Bot <m@rtinjuhl.dk> - 0.1.78-1
- Release 0.1.78

* Fri Mar 20 2026 Release Bot <m@rtinjuhl.dk> - 0.1.77-1
- Release 0.1.77

* Fri Mar 20 2026 Release Bot <m@rtinjuhl.dk> - 0.1.76-1
- Release 0.1.76

* Fri Mar 20 2026 Release Bot <m@rtinjuhl.dk> - 0.1.75-1
- Release 0.1.75

* Fri Mar 20 2026 Release Bot <m@rtinjuhl.dk> - 0.1.74-1
- Release 0.1.74

* Fri Mar 20 2026 Release Bot <m@rtinjuhl.dk> - 0.1.73-1
- Release 0.1.73

* Fri Mar 20 2026 Release Bot <m@rtinjuhl.dk> - 0.1.72-1
- Release 0.1.72

* Fri Mar 20 2026 Release Bot <m@rtinjuhl.dk> - 0.1.71-1
- Release 0.1.71

* Fri Mar 20 2026 Release Bot <m@rtinjuhl.dk> - 0.1.70-1
- Release 0.1.70

* Fri Mar 20 2026 Release Bot <m@rtinjuhl.dk> - 0.1.69-1
- Release 0.1.69

* Fri Mar 20 2026 Release Bot <m@rtinjuhl.dk> - 0.1.68-1
- Release 0.1.68

* Thu Mar 19 2026 Release Bot <m@rtinjuhl.dk> - 0.1.67-1
- Release 0.1.67

* Thu Mar 19 2026 Release Bot <m@rtinjuhl.dk> - 0.1.66-1
- Release 0.1.66

* Thu Mar 19 2026 Release Bot <m@rtinjuhl.dk> - 0.1.65-1
- Release 0.1.65

* Wed Mar 18 2026 Release Bot <m@rtinjuhl.dk> - 0.1.64-1
- Release 0.1.64

* Tue Mar 17 2026 Release Bot <m@rtinjuhl.dk> - 0.1.63-1
- Release 0.1.63

* Tue Mar 17 2026 Release Bot <m@rtinjuhl.dk> - 0.1.62-1
- Release 0.1.62

* Tue Mar 17 2026 Release Bot <m@rtinjuhl.dk> - 0.1.61-1
- Release 0.1.61

* Tue Mar 17 2026 Release Bot <m@rtinjuhl.dk> - 0.1.60-1
- Release 0.1.60

* Tue Mar 17 2026 Release Bot <m@rtinjuhl.dk> - 0.1.59-1
- Release 0.1.59

* Tue Mar 17 2026 Release Bot <m@rtinjuhl.dk> - 0.1.58-1
- Release 0.1.58

* Mon Mar 16 2026 Release Bot <m@rtinjuhl.dk> - 0.1.57-1
- Release 0.1.57

* Mon Mar 16 2026 Release Bot <m@rtinjuhl.dk> - 0.1.56-1
- Release 0.1.56

* Mon Mar 16 2026 Release Bot <m@rtinjuhl.dk> - 0.1.55-1
- Release 0.1.55

* Mon Mar 16 2026 Release Bot <m@rtinjuhl.dk> - 0.1.54-1
- Release 0.1.54

* Mon Mar 16 2026 Release Bot <m@rtinjuhl.dk> - 0.1.53-1
- Release 0.1.53

* Mon Mar 16 2026 Release Bot <m@rtinjuhl.dk> - 0.1.52-1
- Release 0.1.52

* Mon Mar 16 2026 Release Bot <m@rtinjuhl.dk> - 0.1.51-1
- Release 0.1.51

* Mon Mar 16 2026 Release Bot <m@rtinjuhl.dk> - 0.1.50-1
- Release 0.1.50

* Mon Mar 16 2026 Release Bot <m@rtinjuhl.dk> - 0.1.49-1
- Release 0.1.49

* Mon Mar 16 2026 Release Bot <m@rtinjuhl.dk> - 0.1.48-1
- Release 0.1.48

* Mon Mar 16 2026 Release Bot <m@rtinjuhl.dk> - 0.1.47-1
- Release 0.1.47

* Mon Mar 16 2026 Release Bot <m@rtinjuhl.dk> - 0.1.46-1
- Release 0.1.46

* Mon Mar 16 2026 Release Bot <m@rtinjuhl.dk> - 0.1.45-1
- Release 0.1.45

* Mon Mar 16 2026 Release Bot <m@rtinjuhl.dk> - 0.1.44-1
- Release 0.1.44

* Mon Mar 16 2026 Release Bot <m@rtinjuhl.dk> - 0.1.43-1
- Release 0.1.43

* Mon Mar 16 2026 Release Bot <m@rtinjuhl.dk> - 0.1.42-1
- Release 0.1.42

* Mon Mar 16 2026 Release Bot <m@rtinjuhl.dk> - 0.1.41-1
- Release 0.1.41

* Mon Mar 16 2026 Release Bot <m@rtinjuhl.dk> - 0.1.40-1
- Release 0.1.40

* Sun Mar 15 2026 Release Bot <m@rtinjuhl.dk> - 0.1.39-1
- Release 0.1.39

* Sun Mar 15 2026 Release Bot <m@rtinjuhl.dk> - 0.1.38-1
- Release 0.1.38

* Sun Mar 15 2026 Release Bot <m@rtinjuhl.dk> - 0.1.37-1
- Release 0.1.37

* Sun Mar 15 2026 Release Bot <m@rtinjuhl.dk> - 0.1.36-1
- Release 0.1.36

* Sun Mar 15 2026 Release Bot <m@rtinjuhl.dk> - 0.1.35-1
- Release 0.1.35

* Sun Mar 15 2026 Release Bot <m@rtinjuhl.dk> - 0.1.34-1
- Release 0.1.34

* Sun Mar 15 2026 Release Bot <m@rtinjuhl.dk> - 0.1.33-1
- Release 0.1.33

* Sun Mar 15 2026 Release Bot <m@rtinjuhl.dk> - 0.1.32-1
- Release 0.1.32

* Sat Mar 14 2026 Release Bot <m@rtinjuhl.dk> - 0.1.31-1
- Release 0.1.31

* Sat Mar 14 2026 Release Bot <m@rtinjuhl.dk> - 0.1.30-1
- Release 0.1.30

* Sat Mar 14 2026 Release Bot <m@rtinjuhl.dk> - 0.1.29-1
- Release 0.1.29

* Fri Mar 13 2026 Release Bot <m@rtinjuhl.dk> - 0.1.28-1
- Release 0.1.28

* Fri Mar 13 2026 Release Bot <m@rtinjuhl.dk> - 0.1.27-1
- Release 0.1.27

* Fri Mar 13 2026 Release Bot <m@rtinjuhl.dk> - 0.1.26-1
- Release 0.1.26

* Fri Mar 13 2026 Release Bot <m@rtinjuhl.dk> - 0.1.25-1
- Release 0.1.25

* Fri Mar 13 2026 Release Bot <m@rtinjuhl.dk> - 0.1.24-1
- Release 0.1.24

* Fri Mar 13 2026 Release Bot <m@rtinjuhl.dk> - 0.1.23-1
- Release 0.1.23

* Fri Mar 13 2026 Release Bot <m@rtinjuhl.dk> - 0.1.22-1
- Release 0.1.22

* Fri Mar 13 2026 Release Bot <m@rtinjuhl.dk> - 0.1.21-1
- Release 0.1.21

* Thu Mar 12 2026 Release Bot <m@rtinjuhl.dk> - 0.1.20-1
- Release 0.1.20

* Thu Mar 12 2026 Release Bot <m@rtinjuhl.dk> - 0.1.19-1
- Release 0.1.19

* Thu Mar 12 2026 Release Bot <m@rtinjuhl.dk> - 0.1.18-1
- Release 0.1.18

* Thu Mar 12 2026 Release Bot <m@rtinjuhl.dk> - 0.1.17-1
- Release 0.1.17

* Wed Mar 11 2026 Release Bot <m@rtinjuhl.dk> - 0.1.16-1
- Release 0.1.16

* Wed Mar 11 2026 Release Bot <m@rtinjuhl.dk> - 0.1.15-1
- Release 0.1.15

* Wed Mar 11 2026 Release Bot <m@rtinjuhl.dk> - 0.1.14-1
- Release 0.1.14

* Wed Mar 11 2026 Release Bot <m@rtinjuhl.dk> - 0.1.13-1
- Release 0.1.13

* Wed Mar 11 2026 Release Bot <m@rtinjuhl.dk> - 0.1.12-1
- Release 0.1.12

* Wed Mar 11 2026 Release Bot <m@rtinjuhl.dk> - 0.1.11-1
- Release 0.1.11

* Wed Mar 11 2026 Release Bot <m@rtinjuhl.dk> - 0.1.10-1
- Release 0.1.10

* Tue Mar 10 2026 Release Bot <m@rtinjuhl.dk> - 0.1.9-1
- Release 0.1.9

* Tue Mar 10 2026 Release Bot <m@rtinjuhl.dk> - 0.1.8-1
- Release 0.1.8

* Tue Mar 10 2026 Release Bot <m@rtinjuhl.dk> - 0.1.7-1
- Release 0.1.7

* Mon Mar 09 2026 Release Bot <m@rtinjuhl.dk> - 0.1.6-1
- Release 0.1.6

* Mon Mar 09 2026 Release Bot <m@rtinjuhl.dk> - 0.1.5-1
- Release 0.1.5

* Mon Mar 09 2026 Release Bot <m@rtinjuhl.dk> - 0.1.4-1
- Release 0.1.4

* Mon Mar 09 2026 Release Bot <m@rtinjuhl.dk> - 0.1.3-1
- Release 0.1.3

* Fri Mar 06 2026 Release Bot <m@rtinjuhl.dk> - 0.1.2-1
- Release 0.1.2

* Fri Mar 06 2026 Release Bot <m@rtinjuhl.dk> - 0.1.1-1
- Release 0.1.1

* Fri Mar 06 2026 RPM Bot <m@rtinjuhl.dk> - 0.1.0-1
- Initial release
- Split virtualenv into flat-manager-django-python-libs subpackage
- flat-manager-django is noarch; python-libs carries compiled extensions

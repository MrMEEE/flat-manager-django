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
BuildArch:      noarch

BuildRequires:  %{pypkg_prefix}
BuildRequires:  systemd-rpm-macros

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

BuildRequires:  %{pypkg_prefix}-pip
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
mkdir -p %{buildroot}%{data_dir}/staticfiles
cp -a %{_builddir}/tmp-static/. %{buildroot}%{data_dir}/staticfiles/ 2>/dev/null || :

# ── Runtime data + log directories (owned by flat-manager) ───────────────────
install -d -m 0750 %{buildroot}%{data_dir}/repos
install -d -m 0750 %{buildroot}%{data_dir}/builds
install -d -m 0750 %{buildroot}%{data_dir}/media
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
set -a
[ -f /etc/flat-manager/flat-manager.env ] && . /etc/flat-manager/flat-manager.env
set +a
exec /opt/flat-manager/venv/bin/python /opt/flat-manager/app/manage.py "$@"
EOF
chmod 0755 %{buildroot}%{_bindir}/flat-manager-manage

# ─────────────────────────────────────────────────────────────────────────────
%pre
getent group  %{app_group} >/dev/null || groupadd  -r %{app_group}
getent passwd %{app_user}  >/dev/null || \
    useradd -r -g %{app_group} -d %{install_dir} -s /sbin/nologin \
            -c "Flat Manager service account" %{app_user}
exit 0

%post
%systemd_post flat-manager-web.service \
              flat-manager-celery.service \
              flat-manager-celery-beat.service \
              flat-manager.target

chown -R %{app_user}:%{app_group} %{data_dir} %{log_dir} %{conf_dir}
systemd-tmpfiles --create %{_tmpfilesdir}/flat-manager.conf 2>/dev/null || :

if [ $1 -eq 1 ] && [ ! -f %{conf_dir}/flat-manager.env ]; then
    cp %{conf_dir}/flat-manager.env.example %{conf_dir}/flat-manager.env
    chmod 0640 %{conf_dir}/flat-manager.env
    chown root:%{app_group} %{conf_dir}/flat-manager.env
fi

if [ $1 -eq 1 ]; then
    echo ""
    echo "=================================================================="
    echo "  flat-manager-django installed — first-time setup"
    echo "=================================================================="
    echo "  1. Edit  /etc/flat-manager/flat-manager.env"
    echo "     Set SECRET_KEY, DB_PASSWORD, ALLOWED_HOSTS, etc."
    echo ""
    echo "  2. Create MariaDB database + user (see README)"
    echo ""
    echo "  3. flat-manager-manage migrate"
    echo "  4. flat-manager-manage createsuperuser"
    echo "  5. systemctl enable --now nginx flat-manager.target"
    echo "=================================================================="
fi

%preun
%systemd_preun flat-manager-web.service \
               flat-manager-celery.service \
               flat-manager-celery-beat.service \
               flat-manager.target

%postun
%systemd_postun_with_restart flat-manager-web.service \
                              flat-manager-celery.service \
                              flat-manager-celery-beat.service

# ─────────────────────────────────────────────────────────────────────────────
%files
%license README.md

%dir                                           %{install_dir}
%dir                                           %{install_dir}/app
%{install_dir}/app/

%dir %attr(0750, %{app_user}, %{app_group})    %{data_dir}
%dir %attr(0750, %{app_user}, %{app_group})    %{data_dir}/repos
%dir %attr(0750, %{app_user}, %{app_group})    %{data_dir}/builds
%dir %attr(0750, %{app_user}, %{app_group})    %{data_dir}/media
%dir %attr(0750, %{app_user}, %{app_group})    %{data_dir}/staticfiles
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

# ─────────────────────────────────────────────────────────────────────────────
%files          python-libs
%dir                                            %{install_dir}
%dir                                            %{install_dir}/venv
%{install_dir}/venv/

# ─────────────────────────────────────────────────────────────────────────────
%changelog
* Fri Mar 06 2026 Release Bot <noreply@example.com> - 0.1.2-1
- Release 0.1.2

* Fri Mar 06 2026 Release Bot <noreply@example.com> - 0.1.1-1
- Release 0.1.1

* Fri Mar 06 2026 RPM Bot <noreply@example.com> - 0.1.0-1
- Initial release
- Split virtualenv into flat-manager-django-python-libs subpackage
- flat-manager-django is noarch; python-libs carries compiled extensions

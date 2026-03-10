# RPM Installation Guide

Supported distributions: **CentOS Stream 9**, **CentOS Stream 10**, **RHEL 9**, **RHEL 10**.

---

## 1. Prerequisites

### Enable required repositories

**RHEL 9 / CentOS Stream 9**
```bash
dnf install -y epel-release
dnf config-manager --set-enabled crb      # enables python3.11-devel etc.
```

**RHEL 10 / CentOS Stream 10**
```bash
dnf install -y epel-release
dnf config-manager --set-enabled crb
```

### Install system dependencies

```bash
dnf install -y \
    mariadb-server \
    nginx \
    redis \           # or: dnf install -y valkey
    flatpak \
    flatpak-builder \
    ostree
```

> **Note:** Either `redis` or `valkey` satisfies the broker requirement.
> Install whichever is available in your distribution's repositories.

---

## 2. Start and enable infrastructure services

Redis (or Valkey) **must** be running before the flat-manager services start.
Start and enable it now so it survives reboots:

```bash
# Redis
systemctl enable --now redis

# --- OR Valkey ---
# systemctl enable --now valkey
```

Start MariaDB and run the secure installation:

```bash
systemctl enable --now mariadb
mysql_secure_installation
```

---

## 3. Install the RPM packages

Download both RPMs for your distribution from the
[GitHub Releases](https://github.com/YOUR_ORG/flat-manager-django/releases)
page, then install them together:

```bash
# Example for RHEL 9
dnf install -y \
    flat-manager-django-<version>.el9.x86_64.rpm \
    flat-manager-django-python-libs-<version>.el9.x86_64.rpm
```

The installer will:
- Create the `flat-manager` system user and group
- Install the application to `/opt/flat-manager/app/`
- Install the Python virtualenv to `/opt/flat-manager/venv/`
- Create runtime directories (`/var/lib/flat-manager/`, `/var/log/flat-manager/`)
- Install systemd units and nginx config
- Copy `/etc/flat-manager/flat-manager.env` from the bundled example

---

## 4. Create the MariaDB database

```sql
mysql -u root -p <<'EOF'
CREATE DATABASE flatmanager
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

CREATE USER 'flatmanager'@'localhost' IDENTIFIED BY 'CHANGE_ME';
GRANT ALL PRIVILEGES ON flatmanager.* TO 'flatmanager'@'localhost';
FLUSH PRIVILEGES;
EOF
```

---

## 5. Configure the application

Edit the environment file:

```bash
vi /etc/flat-manager/flat-manager.env
```

Minimum required settings:

```bash
SECRET_KEY=<long-random-string>          # python3 -c "import secrets; print(secrets.token_hex(50))"
DEBUG=False
ALLOWED_HOSTS=your.server.hostname
CSRF_TRUSTED_ORIGINS=https://your.server.hostname

DB_ENGINE=django.db.backends.mysql
DB_NAME=flatmanager
DB_USER=flatmanager
DB_PASSWORD=CHANGE_ME
DB_HOST=localhost

REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/0
CHANNEL_LAYERS_BACKEND=channels_redis.core.RedisChannelLayer
CHANNEL_LAYERS_HOST=127.0.0.1
CHANNEL_LAYERS_PORT=6379

REPOS_BASE_PATH=/var/lib/flat-manager/repos
FLATPAK_BUILD_PATH=/var/lib/flat-manager/builds
STATIC_ROOT=/var/lib/flat-manager/staticfiles
MEDIA_ROOT=/var/lib/flat-manager/media
LOG_DIR=/var/log/flat-manager
```

---

## 6. Run database migrations and create an admin user

The `flat-manager-manage` wrapper automatically drops privileges to the
`flat-manager` service account, so it is safe to run as root:

```bash
flat-manager-manage migrate
flat-manager-manage createsuperuser
```

---

## 7. Configure nginx

The package installs a ready-to-use nginx config at
`/etc/nginx/conf.d/flat-manager.conf`.

### Why the default nginx test page appears

RHEL/CentOS nginx ships with a `default_server` block in `/etc/nginx/nginx.conf`
that wins over any `conf.d/` file that doesn't also declare `default_server`.
The installed config uses `listen 80 default_server` and
`listen 443 ssl default_server`, so it will take over correctly once you
restart nginx — no manual editing of `nginx.conf` is needed.

### Set the hostname

Edit the config and set `server_name` in both server blocks (HTTP and HTTPS)
to your FQDN:

```bash
vi /etc/nginx/conf.d/flat-manager.conf
```

Replace `_` with your actual hostname, e.g. `flatpak.example.com`
(there are two `server_name` lines — one in each block).

### Obtain a TLS certificate

The RPM post-install script **automatically generates a self-signed snakeoil
certificate** using the server's hostname, so nginx starts immediately without
any manual cert setup. Browsers will show an untrusted-cert warning —
replace it with a real certificate for production use.

**To replace with a Let's Encrypt certificate:**

```bash
dnf install -y certbot python3-certbot-nginx
firewall-cmd --permanent --add-service=http && firewall-cmd --reload
certbot certonly --standalone -d your.server.hostname
```

Then edit `/etc/nginx/conf.d/flat-manager.conf` — comment out the snakeoil
lines and uncomment the `letsencrypt` lines, then reload nginx:

```nginx
# ssl_certificate     /etc/pki/tls/certs/flat-manager.crt;
# ssl_certificate_key /etc/pki/tls/private/flat-manager.key;
ssl_certificate     /etc/letsencrypt/live/your.server.hostname/fullchain.pem;
ssl_certificate_key /etc/letsencrypt/live/your.server.hostname/privkey.pem;
```

```bash
nginx -t && systemctl reload nginx
```

To regenerate the snakeoil cert manually (e.g. after a hostname change):

```bash
openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
  -keyout /etc/pki/tls/private/flat-manager.key \
  -out    /etc/pki/tls/certs/flat-manager.crt \
  -subj "/CN=your.server.hostname"
chmod 0600 /etc/pki/tls/private/flat-manager.key
nginx -t && systemctl reload nginx
```

### Open firewall ports and test

```bash
firewall-cmd --permanent --add-service=http
firewall-cmd --permanent --add-service=https
firewall-cmd --reload

nginx -t
```

---

## 8. Enable and start all services

```bash
systemctl enable --now nginx
systemctl enable --now flat-manager.target
```

The `flat-manager.target` starts three units:
- `flat-manager-web` — Daphne ASGI server (WebSocket + HTTP)
- `flat-manager-celery` — Celery background worker
- `flat-manager-celery-beat` — Celery periodic task scheduler

Check status:

```bash
systemctl status flat-manager-web flat-manager-celery flat-manager-celery-beat
```

All three should show `active (running)`.

---

## 9. Verify the installation

```bash
# HTTPS should return 200 (use -k for self-signed certs)
curl -sI https://your.server.hostname/ | head -1

# HTTP should redirect to HTTPS (301)
curl -sI http://your.server.hostname/ | head -2

# Socket created and reachable by nginx
ls -al /run/flat-manager/daphne.sock

# Log files owned by flat-manager (not root)
ls -al /var/log/flat-manager/

# Redis connectivity (from service account)
runuser -u flat-manager -- \
    /opt/flat-manager/venv/bin/python -c \
    "import redis; r = redis.Redis(); print(r.ping())"
```

---

## Troubleshooting

### Services fail to start — permission denied on log file

A log file was created as root (e.g. by running `manage.py` as root before the
fix in ≥ 0.1.7). Fix it manually:

```bash
chown flat-manager:flat-manager /var/log/flat-manager/*.log
systemctl restart flat-manager.target
```

### Celery connects to Redis but returns no workers

`celery inspect active` sends a broadcast over Redis to running workers — if
Redis was down when the worker started, reconnect by restarting:

```bash
systemctl restart flat-manager-celery flat-manager-celery-beat
```

### `celery inspect` is not a Django management command

Use the celery binary directly:

```bash
cd /opt/flat-manager/app
source /etc/flat-manager/flat-manager.env
/opt/flat-manager/venv/bin/celery -A config inspect active
```

### nginx 502 Bad Gateway

The Daphne UNIX socket is not ready yet or has wrong permissions:

```bash
systemctl status flat-manager-web
ls -al /run/flat-manager/daphne.sock
```

The socket should be `srwxrwxrwx` (666). If it shows `srw-rw----`, the service
is running an older unit file — restart it to pick up the new permissions:

```bash
systemctl restart flat-manager-web
```

---

## Upgrading

```bash
dnf upgrade -y \
    flat-manager-django-<new-version>.rpm \
    flat-manager-django-python-libs-<new-version>.rpm

flat-manager-manage migrate          # apply any new migrations
systemctl restart flat-manager.target
```

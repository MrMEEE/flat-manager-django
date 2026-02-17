# Flat Manager - Quick Reference Guide

## Daily Commands

### Start/Stop Services
```bash
./start.sh      # Start all services
./stop.sh       # Stop all services
./restart.sh    # Restart all services
./status.sh     # Check service status
```

### Django Management
```bash
# Activate virtual environment first
source venv/bin/activate

# Create migrations
python manage.py makemigrations

# Apply migrations
python manage.py migrate

# Create superuser
python manage.py createsuperuser

# Django shell
python manage.py shell

# Collect static files
python manage.py collectstatic
```

### Celery Tasks
```bash
# Start worker manually
celery -A config worker --loglevel=info

# Start beat scheduler manually
celery -A config beat --loglevel=info

# List tasks
celery -A config inspect registered

# Purge all tasks
celery -A config purge
```

### Database
```bash
# SQLite (Development)
sqlite3 db.sqlite3

# MySQL/MariaDB (Production)
mysql -u flatmanager -p flatmanager
```

## API Usage Examples

### Authentication
```bash
# Get token
curl -X POST http://localhost:8000/api/auth/login/ \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"password"}'
```

### Repositories
```bash
# List repositories
curl http://localhost:8000/api/repositories/ \
  -H "Authorization: Token YOUR_TOKEN"

# Create repository
curl -X POST http://localhost:8000/api/repositories/ \
  -H "Authorization: Token YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"test-repo","description":"Test","default_branch":"stable"}'

# Get repository details
curl http://localhost:8000/api/repositories/1/ \
  -H "Authorization: Token YOUR_TOKEN"
```

### Builds
```bash
# List builds
curl http://localhost:8000/api/builds/ \
  -H "Authorization: Token YOUR_TOKEN"

# Create build
curl -X POST http://localhost:8000/api/builds/ \
  -H "Authorization: Token YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "repository_id": 1,
    "build_id": "build-001",
    "app_id": "org.example.App",
    "branch": "stable",
    "arch": "x86_64"
  }'

# Get build details
curl http://localhost:8000/api/builds/1/ \
  -H "Authorization: Token YOUR_TOKEN"

# Get build logs
curl http://localhost:8000/api/builds/1/logs/ \
  -H "Authorization: Token YOUR_TOKEN"
```

## WebSocket Usage

### JavaScript Example
```javascript
const ws = new WebSocket('ws://localhost:8001/ws/builds/1/');

ws.onopen = function() {
    console.log('Connected to build updates');
};

ws.onmessage = function(e) {
    const data = JSON.parse(e.data);
    console.log('Build status:', data);
};

ws.onclose = function() {
    console.log('Disconnected');
};
```

### Python Example
```python
import websockets
import asyncio
import json

async def watch_build():
    uri = "ws://localhost:8001/ws/builds/1/"
    async with websockets.connect(uri) as websocket:
        while True:
            message = await websocket.recv()
            data = json.loads(message)
            print(f"Build status: {data}")

asyncio.run(watch_build())
```

## Log Files

```bash
# View logs
tail -f logs/django.log
tail -f logs/celery.log
tail -f logs/celery-beat.log
tail -f logs/daphne.log

# Clear logs
> logs/django.log
> logs/celery.log
```

## Troubleshooting

### Redis not connecting
```bash
# Check if Redis is running
redis-cli ping

# Start Redis
redis-server &

# Check Redis config
redis-cli config get *
```

### Database migrations fail
```bash
# Reset migrations (development only!)
find . -path "*/migrations/*.py" -not -name "__init__.py" -delete
find . -path "*/migrations/*.pyc" -delete
python manage.py makemigrations
python manage.py migrate
```

### Celery worker not processing tasks
```bash
# Check if worker is running
ps aux | grep celery

# Check task queue
celery -A config inspect active

# Restart worker
./restart.sh
```

### Port already in use
```bash
# Find process using port 8000
lsof -i :8000
sudo netstat -tulpn | grep :8000

# Kill process
kill -9 <PID>
```

## Development Tips

### Create new app
```bash
python manage.py startapp myapp
# Then add 'apps.myapp' to INSTALLED_APPS in settings.py
```

### Run tests
```bash
pytest
pytest --cov  # With coverage
```

### Code formatting
```bash
black .
isort .
flake8
```

### Database backup
```bash
# SQLite
cp db.sqlite3 db.sqlite3.backup

# MySQL/MariaDB
mysqldump -u flatmanager -p flatmanager > backup.sql
```

### Database restore
```bash
# SQLite
cp db.sqlite3.backup db.sqlite3

# MySQL/MariaDB
mysql -u flatmanager -p flatmanager < backup.sql
```

## URLs

- Web UI: http://localhost:8000
- API Root: http://localhost:8000/api/
- API Docs: http://localhost:8000/api/ (browsable)
- WebSockets: ws://localhost:8001/ws/
- Django Admin: http://localhost:8000/admin/

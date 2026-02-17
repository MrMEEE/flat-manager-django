# Flat Manager - Django

A modern reimplementation of [flat-manager](https://github.com/flatpak/flat-manager) in Python/Django with a full REST API, WebSocket support, and a Bootstrap-based web UI.

## Features

- **Full REST API** - Complete API for repository and build management
- **Real-time Updates** - WebSocket support for live build status updates
- **Background Processing** - Celery-based task queue for async operations
- **User Administration** - Custom user management system (not Django admin)
- **Modern UI** - Bootstrap 5-based responsive web interface
- **Multi-database** - SQLite for development, MariaDB/MySQL for production
- **Redis/Valkey** - For Celery broker and WebSocket channel layer

## Architecture

```
┌─────────────────┐
│   Web UI        │  Bootstrap 5 Frontend
│  (Templates)    │
└────────┬────────┘
         │
┌────────▼────────┐
│   Django        │  Main Application Server
│   (Views/API)   │
└────┬────────┬───┘
     │        │
     │        └────► WebSockets (Daphne/Channels)
     │
┌────▼────────┐
│  Database   │  SQLite/MariaDB
└─────────────┘
     │
┌────▼────────┐
│   Celery    │  Background Tasks
│   Workers   │
└─────────────┘
     │
┌────▼────────┐
│ Redis/      │  Message Broker
│ Valkey      │  & Channel Layer
└─────────────┘
```

## Installation

### Prerequisites

- Python 3.10+
- Redis or Valkey
- (Optional) MariaDB/MySQL for production

### Quick Start

1. **Clone the repository**
   ```bash
   cd /home/mj/Ansible/flat-manager-django
   ```

2. **Create virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Linux/Mac
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Set up environment variables**
   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

5. **Start Redis (if not already running)**
   ```bash
   redis-server &
   ```

6. **Make scripts executable**
   ```bash
   chmod +x start.sh stop.sh restart.sh status.sh
   ```

7. **Run migrations**
   ```bash
   python manage.py migrate
   ```

8. **Create superuser**
   ```bash
   python manage.py createsuperuser
   ```

9. **Start all services**
   ```bash
   ./start.sh
   ```

## Service Management

### Start all services
```bash
./start.sh
```

This starts:
- Django development server (port 8000)
- Daphne WebSocket server (port 8001)
- Celery worker
- Celery beat scheduler

### Stop all services
```bash
./stop.sh
```

### Restart all services
```bash
./restart.sh
```

### Check service status
```bash
./status.sh
```

## Access

- **Web UI**: http://localhost:8000
- **API**: http://localhost:8000/api/
- **WebSockets**: ws://localhost:8001/ws/
- **Django Admin**: http://localhost:8000/admin/

## Project Structure

```
flat-manager-django/
├── config/                 # Django configuration
│   ├── settings.py        # Main settings
│   ├── urls.py            # URL routing
│   ├── asgi.py            # ASGI configuration
│   ├── wsgi.py            # WSGI configuration
│   ├── celery.py          # Celery configuration
│   └── routing.py         # WebSocket routing
├── apps/                   # Django apps
│   ├── users/             # User management
│   ├── flatpak/           # Flatpak repository/build management
│   └── api/               # REST API
├── templates/             # HTML templates
├── static/                # Static files
├── media/                 # User uploads
├── logs/                  # Application logs
├── repos/                 # Flatpak repositories
├── builds/                # Build artifacts
├── manage.py              # Django management
├── requirements.txt       # Python dependencies
├── start.sh               # Start services
├── stop.sh                # Stop services
├── restart.sh             # Restart services
└── status.sh              # Check service status
```

## API Endpoints

### Authentication
- `POST /api/auth/login/` - Login
- `POST /api/auth/logout/` - Logout

### Users
- `GET /api/users/` - List users
- `GET /api/users/{id}/` - Get user details
- `GET /api/users/me/` - Get current user

### Repositories
- `GET /api/repositories/` - List repositories
- `POST /api/repositories/` - Create repository
- `GET /api/repositories/{id}/` - Get repository details
- `GET /api/repositories/{id}/builds/` - Get repository builds

### Builds
- `GET /api/builds/` - List builds
- `POST /api/builds/` - Create build
- `GET /api/builds/{id}/` - Get build details
- `POST /api/builds/{id}/start/` - Start build
- `POST /api/builds/{id}/cancel/` - Cancel build
- `POST /api/builds/{id}/publish/` - Publish build
- `GET /api/builds/{id}/logs/` - Get build logs

### Tokens
- `GET /api/tokens/` - List tokens
- `POST /api/tokens/` - Create token

## WebSocket Events

### Build Status Updates
Connect to: `ws://localhost:8001/ws/builds/{build_id}/`

Events:
```json
{
  "type": "status_update",
  "build_id": 123,
  "status": "building",
  "message": "Build started",
  "timestamp": "2026-02-16T10:30:00Z"
}
```

### Repository Updates
Connect to: `ws://localhost:8001/ws/repos/{repo_id}/`

## Development

### Running Tests
```bash
pytest
```

### Code Formatting
```bash
black .
isort .
```

### Linting
```bash
flake8
```

## Environment Variables

Key environment variables in `.env`:

```bash
DEBUG=True
SECRET_KEY=your-secret-key

# Database
DB_ENGINE=sqlite  # or mysql
DB_NAME=flatmanager
DB_USER=flatmanager
DB_PASSWORD=password
DB_HOST=localhost
DB_PORT=3306

# Redis
CELERY_BROKER_URL=redis://localhost:6379/0
REDIS_URL=redis://localhost:6379/1

# Paths
FLATPAK_REPO_PATH=/path/to/repos
FLATPAK_BUILD_PATH=/path/to/builds
```

## Production Deployment

For production:

1. Set `DEBUG=False`
2. Use MariaDB/MySQL (`DB_ENGINE=mysql`)
3. Use a production-grade Redis
4. Use Gunicorn/uWSGI instead of runserver
5. Use Nginx as reverse proxy
6. Set strong `SECRET_KEY`
7. Configure proper CORS settings

## Next Steps

This is the base Django application with all infrastructure in place. Features can now be added incrementally:

1. Implement flatpak-builder integration
2. Add OSTree repository management
3. Implement upload/download endpoints
4. Add build artifact storage
5. Implement repository publishing
6. Add build scheduling
7. Implement token-based authentication for builds
8. Add webhook support

## License

[Your License Here]

## Contributing

[Contribution Guidelines]

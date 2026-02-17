# Flat Manager Django - Getting Started

## 🎯 What You Have Now

A complete Django application framework for reimplementing flat-manager with:

✅ **Django 5** project structure
✅ **Custom User Management** (not Django admin)
✅ **REST API** with Django REST Framework
✅ **WebSockets** via Django Channels
✅ **Celery** for background tasks
✅ **Bootstrap 5** UI
✅ **Service management scripts** (start/stop/restart)
✅ **SQLite** for development (MariaDB ready for production)
✅ **Redis/Valkey** integration

## 🚀 Quick Start (5 minutes)

### 1. Initial Setup
```bash
cd /home/mj/Ansible/flat-manager-django

# Run the setup script (creates venv, installs dependencies, runs migrations)
./setup.sh
```

The setup script will:
- Create Python virtual environment
- Install all dependencies
- Create database and run migrations
- Prompt you to create a superuser account
- Set up directories

### 2. Start Redis
```bash
# In a separate terminal
redis-server
```

### 3. Start All Services
```bash
./start.sh
```

This starts:
- Django server on http://localhost:8000
- WebSocket server on ws://localhost:8001
- Celery worker for background tasks
- Celery beat for scheduled tasks

### 4. Access the Application
- **Web UI**: http://localhost:8000
- **API**: http://localhost:8000/api/
- **Admin**: http://localhost:8000/admin/

## 📚 What's Included

### Models
- **User** - Custom user with repo/build admin flags
- **UserProfile** - Extended user information
- **APIToken** - API access tokens
- **Repository** - Flatpak repositories
- **Build** - Build tracking with status
- **BuildLog** - Build progress logs
- **BuildArtifact** - Uploaded build artifacts
- **Token** - Repository access tokens

### API Endpoints
```
GET  /api/users/           - List users
GET  /api/users/me/        - Current user
GET  /api/repositories/    - List repositories
POST /api/repositories/    - Create repository
GET  /api/builds/          - List builds
POST /api/builds/          - Create build (auto-starts Celery task)
POST /api/builds/{id}/start/   - Start build
POST /api/builds/{id}/cancel/  - Cancel build
GET  /api/builds/{id}/logs/    - Get build logs
```

### WebSocket Endpoints
```
ws://localhost:8001/ws/builds/{build_id}/     - Build updates
ws://localhost:8001/ws/repos/{repo_id}/       - Repository updates
ws://localhost:8001/ws/notifications/         - General notifications
```

### UI Pages
- Landing page
- Login/Logout
- Dashboard with statistics
- Repository list and detail views
- Build list and detail views
- User profile
- User management (admin only)

## 🛠️ Development Workflow

### Daily Commands
```bash
./start.sh      # Start all services
./status.sh     # Check what's running
./stop.sh       # Stop all services
./restart.sh    # Restart all services
```

### Django Commands
```bash
source venv/bin/activate  # Activate virtual environment

python manage.py makemigrations  # Create migrations
python manage.py migrate         # Apply migrations
python manage.py createsuperuser # Create admin user
python manage.py shell          # Django shell
```

### View Logs
```bash
tail -f logs/django.log      # Django logs
tail -f logs/celery.log      # Celery worker logs
tail -f logs/daphne.log      # WebSocket server logs
```

## 📖 Documentation

- **README.md** - Main documentation with architecture
- **QUICKREF.md** - Quick reference for commands and API
- **STRUCTURE.md** - Project structure explanation
- **GETTING_STARTED.md** - This file

## 🎯 Next Steps - Building Features

Now you can add features one at a time. Here's a suggested order:

### Phase 1: Core Flatpak Integration
1. **OSTree repository initialization**
   - Create repository on disk
   - Initialize OSTree repo structure

2. **Build upload endpoint**
   - File upload API
   - Store artifacts
   - Validate uploads

3. **Build processing**
   - Implement actual flatpak-builder integration
   - Process uploaded files
   - Generate commits

### Phase 2: Repository Management
4. **Publish builds**
   - Commit to OSTree repository
   - Update repository metadata
   - Generate deltas

5. **Repository serving**
   - Serve OSTree repository via HTTP
   - Implement repository signatures
   - Handle multiple architectures

### Phase 3: Advanced Features
6. **Token-based access**
   - Upload tokens
   - Download tokens
   - Token expiration

7. **Build scheduling**
   - Queue management
   - Priority builds
   - Parallel builds

8. **Webhooks**
   - Build completion webhooks
   - Repository update webhooks
   - Custom webhook endpoints

## 🔍 Testing the Setup

### 1. Create a Test Repository
```bash
curl -X POST http://localhost:8000/api/repositories/ \
  -u admin:password \
  -H "Content-Type: application/json" \
  -d '{"name":"test-repo","description":"Test Repository","default_branch":"stable"}'
```

### 2. Create a Test Build
```bash
curl -X POST http://localhost:8000/api/builds/ \
  -u admin:password \
  -H "Content-Type: application/json" \
  -d '{
    "repository_id": 1,
    "build_id": "build-001",
    "app_id": "org.example.TestApp",
    "branch": "stable",
    "arch": "x86_64"
  }'
```

### 3. Watch Build Updates via WebSocket
```javascript
// Open browser console on http://localhost:8000
const ws = new WebSocket('ws://localhost:8001/ws/builds/1/');
ws.onmessage = (e) => console.log(JSON.parse(e.data));
```

## 📝 Environment Configuration

### Development (.env)
- Uses SQLite
- Debug mode enabled
- Local Redis
- All services on localhost

### Production (.env.production)
- Uses MariaDB/MySQL
- Debug mode disabled
- Strong secret keys
- Proper ALLOWED_HOSTS
- Production Redis

## 🐛 Troubleshooting

### Services won't start
1. Check Redis is running: `redis-cli ping`
2. Check ports are available: `lsof -i :8000`
3. Check logs: `cat logs/django.log`

### Database errors
1. Delete db.sqlite3 and run migrations again
2. Or run: `python manage.py migrate --run-syncdb`

### Celery not processing tasks
1. Check worker is running: `ps aux | grep celery`
2. Check Redis connection: `redis-cli ping`
3. Restart services: `./restart.sh`

## 🤝 Getting Help

1. Check logs in `logs/` directory
2. Review QUICKREF.md for common commands
3. Check STRUCTURE.md for project layout
4. Review README.md for architecture details

## 🎉 You're Ready!

The foundation is complete. You now have:
- A working Django application
- Full API infrastructure
- Real-time WebSocket updates
- Background task processing
- User management
- Bootstrap UI

Start building features incrementally, testing as you go. Each feature can be developed independently and integrated smoothly into this foundation.

Happy coding! 🚀

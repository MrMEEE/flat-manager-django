# 🚀 Flat Manager Django - Setup Checklist

Use this checklist to get your development environment running.

## ✅ Pre-Setup Checklist

Before running `./setup.sh`, verify these:

- [ ] Python 3.10+ installed (`python3 --version`)
- [ ] pip installed (`pip --version`)
- [ ] Redis installed (`redis-server --version`)
- [ ] Git installed (optional, for version control)
- [ ] At least 1GB free disk space

## 🔧 Initial Setup

Run these commands in order:

### 1. Navigate to Project
```bash
cd /home/mj/Ansible/flat-manager-django
```
- [ ] Confirmed I'm in the project directory

### 2. Start Redis
```bash
redis-server &
```
- [ ] Redis is running (`redis-cli ping` returns PONG)

### 3. Run Setup Script
```bash
./setup.sh
```

This will:
- [ ] Create virtual environment
- [ ] Install Python dependencies
- [ ] Create .env file
- [ ] Create necessary directories
- [ ] Run database migrations
- [ ] Collect static files
- [ ] Prompt for superuser creation

**Create superuser when prompted!** You'll need this to login.

### 4. Start Services
```bash
./start.sh
```

This starts:
- [ ] Django server (port 8000)
- [ ] Daphne WebSocket server (port 8001)
- [ ] Celery worker
- [ ] Celery beat scheduler

### 5. Verify Services
```bash
./status.sh
```

Check that all services are running:
- [ ] Django Server ✅
- [ ] Celery Worker ✅
- [ ] Celery Beat ✅
- [ ] Daphne (WebSocket) ✅

## 🧪 Testing the Setup

### 1. Access Web UI
- [ ] Open browser: http://localhost:8000
- [ ] See landing page
- [ ] Can navigate to login page

### 2. Login
- [ ] Login with superuser credentials
- [ ] See dashboard
- [ ] Sidebar navigation works

### 3. Test Repository Creation
- [ ] Navigate to Repositories
- [ ] Click "New Repository"
- [ ] Create test repository
- [ ] Repository appears in list

### 4. Test API
```bash
# Get API token (use your username/password)
curl -u admin:password http://localhost:8000/api/users/me/

# Or use the browsable API
```
- [ ] Open http://localhost:8000/api/
- [ ] See API root
- [ ] Can browse endpoints

### 5. Test WebSocket Connection
- [ ] Dashboard shows WebSocket status
- [ ] Green indicator = connected
- [ ] Check browser console for "WebSocket connected"

## 🔍 Troubleshooting

### Redis Not Running
```bash
# Check Redis
redis-cli ping

# If not running, start it
redis-server &
```
- [ ] Fixed Redis issue

### Port Already in Use
```bash
# Find what's using port 8000
lsof -i :8000

# Kill the process
kill -9 <PID>

# Restart services
./restart.sh
```
- [ ] Fixed port conflict

### Service Won't Start
```bash
# Check logs
cat logs/django.log
cat logs/celery.log

# Try stopping and starting again
./stop.sh
sleep 2
./start.sh
```
- [ ] Fixed service issue

### Database Errors
```bash
# Reset database (development only!)
rm db.sqlite3
python manage.py migrate
python manage.py createsuperuser
```
- [ ] Fixed database issue

## 📱 Daily Development Workflow

### Starting Work
```bash
cd /home/mj/Ansible/flat-manager-django
./start.sh
# Wait for all services to start
```
- [ ] Services started

### Checking Status
```bash
./status.sh
```
- [ ] All services running

### Viewing Logs
```bash
# Django
tail -f logs/django.log

# Celery
tail -f logs/celery.log

# WebSocket
tail -f logs/daphne.log
```
- [ ] Can view logs

### Making Changes
- [ ] Edit code
- [ ] Django auto-reloads (for most changes)
- [ ] For Celery changes: `./restart.sh`

### Stopping Work
```bash
./stop.sh
```
- [ ] All services stopped

## 🎯 Next Steps After Setup

### 1. Explore the UI
- [ ] Check out dashboard
- [ ] Browse repositories
- [ ] View builds
- [ ] Check user profile

### 2. Test the API
- [ ] Browse http://localhost:8000/api/
- [ ] Try creating repository via API
- [ ] Test authentication
- [ ] Review API documentation

### 3. Review Documentation
- [ ] Read GETTING_STARTED.md
- [ ] Check QUICKREF.md
- [ ] Review STRUCTURE.md
- [ ] Read TODO.md for features to implement

### 4. Start Developing
- [ ] Pick a feature from TODO.md
- [ ] Create feature branch (optional)
- [ ] Implement feature
- [ ] Test thoroughly
- [ ] Move to next feature

## ✅ Setup Complete Checklist

Before you consider setup complete, verify:

- [ ] Redis is running
- [ ] All 4 services are running (./status.sh shows green)
- [ ] Can access web UI at http://localhost:8000
- [ ] Can login with superuser credentials
- [ ] Can access API at http://localhost:8000/api/
- [ ] WebSocket shows connected (green indicator)
- [ ] Created at least one test repository
- [ ] No errors in logs

## 🎉 You're Ready!

If all items above are checked, your development environment is ready!

### Quick Commands Reference
```bash
./start.sh      # Start all services
./stop.sh       # Stop all services
./restart.sh    # Restart all services
./status.sh     # Check service status

# Django management
source venv/bin/activate
python manage.py <command>
```

### URLs to Remember
- Web UI: http://localhost:8000
- API: http://localhost:8000/api/
- WebSocket: ws://localhost:8001/ws/

### Documentation
- GETTING_STARTED.md - Quick start guide
- QUICKREF.md - Command reference
- TODO.md - Features to implement
- STRUCTURE.md - Project architecture

---

**Status**: Ready for feature development! 🚀

Pick a feature from TODO.md and start coding!

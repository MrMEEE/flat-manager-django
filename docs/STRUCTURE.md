# Flat Manager Django - Project Structure

```
flat-manager-django/
│
├── config/                          # Django project configuration
│   ├── __init__.py                 # Celery app initialization
│   ├── settings.py                 # Main Django settings
│   ├── urls.py                     # Root URL configuration
│   ├── wsgi.py                     # WSGI configuration
│   ├── asgi.py                     # ASGI configuration (WebSockets)
│   ├── celery.py                   # Celery configuration
│   └── routing.py                  # WebSocket routing
│
├── apps/                            # Django applications
│   ├── __init__.py
│   │
│   ├── users/                      # User management app
│   │   ├── __init__.py
│   │   ├── models.py              # User, UserProfile, APIToken models
│   │   ├── views.py               # User views (login, dashboard, etc.)
│   │   ├── urls.py                # User URL patterns
│   │   ├── admin.py               # Django admin configuration
│   │   ├── apps.py                # App configuration
│   │   └── signals.py             # User signals (profile creation)
│   │
│   ├── flatpak/                    # Flatpak management app
│   │   ├── __init__.py
│   │   ├── models.py              # Repository, Build, BuildLog, Token models
│   │   ├── views.py               # Flatpak views
│   │   ├── urls.py                # Flatpak URL patterns
│   │   ├── admin.py               # Django admin configuration
│   │   ├── apps.py                # App configuration
│   │   ├── consumers.py           # WebSocket consumers
│   │   └── tasks.py               # Celery tasks (build processing)
│   │
│   └── api/                        # REST API app
│       ├── __init__.py
│       ├── views.py               # API viewsets
│       ├── urls.py                # API URL patterns
│       ├── serializers.py         # DRF serializers
│       └── apps.py                # App configuration
│
├── templates/                       # HTML templates
│   ├── base.html                  # Base template with sidebar & navbar
│   ├── users/
│   │   ├── index.html             # Landing page
│   │   ├── login.html             # Login page
│   │   ├── dashboard.html         # User dashboard
│   │   └── profile.html           # User profile
│   └── flatpak/
│       ├── repository_list.html   # Repository list
│       ├── repository_detail.html # Repository details
│       ├── repository_form.html   # Repository create/edit
│       ├── build_list.html        # Build list
│       └── build_detail.html      # Build details with logs
│
├── static/                          # Static files (CSS, JS, images)
├── media/                          # User uploaded files
├── logs/                           # Application logs
│   ├── django.log
│   ├── celery.log
│   ├── celery-beat.log
│   └── daphne.log
│
├── repos/                          # Flatpak repositories
├── builds/                         # Build artifacts
├── staticfiles/                    # Collected static files
│
├── manage.py                       # Django management script
├── requirements.txt                # Python dependencies
│
├── .env                            # Environment variables (development)
├── .env.example                   # Environment variables template
├── .env.production                # Production environment template
├── .gitignore                     # Git ignore rules
│
├── setup.sh                        # Initial setup script
├── start.sh                        # Start all services
├── stop.sh                         # Stop all services
├── restart.sh                      # Restart all services
├── status.sh                       # Check service status
│
├── README.md                       # Main documentation
├── QUICKREF.md                    # Quick reference guide
└── STRUCTURE.md                   # This file
```

## Key Components

### Configuration (`config/`)
- **settings.py**: Django settings with environment-based configuration
- **celery.py**: Celery configuration for background tasks
- **asgi.py**: ASGI server configuration for WebSockets
- **routing.py**: WebSocket URL routing

### Applications (`apps/`)

#### Users App
- Custom User model extending AbstractUser
- UserProfile for extended user information
- APIToken for programmatic access
- Custom authentication views (not Django admin)
- User management interface

#### Flatpak App
- Repository model for Flatpak repositories
- Build model for tracking builds
- BuildLog for build progress tracking
- BuildArtifact for uploaded files
- Token for repository access control
- WebSocket consumers for real-time updates
- Celery tasks for background build processing

#### API App
- REST API using Django REST Framework
- ViewSets for all models
- Token authentication
- Pagination and filtering
- API documentation via browsable API

### Templates
- Bootstrap 5-based responsive UI
- Base template with sidebar navigation
- Dashboard with statistics
- Repository and build management interfaces
- Real-time updates via WebSocket integration

### Service Scripts
- **setup.sh**: One-time setup (venv, dependencies, migrations)
- **start.sh**: Start Django, Celery, Daphne servers
- **stop.sh**: Stop all services gracefully
- **restart.sh**: Restart all services
- **status.sh**: Check if services are running

## Data Flow

### Web Request Flow
```
Browser → Django (port 8000) → Views → Models → Database
                              ↓
                         Templates → HTML Response
```

### API Request Flow
```
Client → Django REST Framework → ViewSets → Serializers → Models → Database
```

### WebSocket Flow
```
Browser → Daphne (port 8001) → Channels → Consumers → Redis → Database
```

### Background Task Flow
```
API/View → Celery Task → Celery Worker → Database
                       ↓
                   Redis Queue
                       ↓
                 WebSocket Update
```

## Service Architecture

```
┌──────────────┐
│   Browser    │
└──────┬───────┘
       │
       ├──────────► Django (8000) ──► SQLite/MariaDB
       │                  │
       │                  └──► Celery Tasks
       │
       └──────────► Daphne (8001) ──► Redis ──► Celery Workers
                                        │
                                        └──► Channel Layer
```

## Development Workflow

1. **Setup**: Run `./setup.sh` once
2. **Start**: Run `./start.sh` to start all services
3. **Develop**: Edit code, changes auto-reload
4. **Test**: Access http://localhost:8000
5. **API**: Test API at http://localhost:8000/api/
6. **Stop**: Run `./stop.sh` when done

## Next Feature Implementation

Features can be added incrementally:
1. Each feature gets its own branch
2. Models added to appropriate app
3. API endpoints in `apps/api/`
4. Background tasks in `apps/flatpak/tasks.py`
5. UI templates in `templates/`
6. WebSocket updates in consumers

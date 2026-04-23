"""
Django settings for flat-manager project.
"""
import os
from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get('SECRET_KEY', 'django-insecure-dev-key-change-in-production')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.environ.get('DEBUG', 'True') == 'True'

ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')

# Required when serving over HTTPS — must include the scheme.
# e.g. CSRF_TRUSTED_ORIGINS=https://flatpak.example.com
CSRF_TRUSTED_ORIGINS = os.environ.get('CSRF_TRUSTED_ORIGINS', '').split(',') if os.environ.get('CSRF_TRUSTED_ORIGINS') else []

# Trust the X-Forwarded-Proto header set by nginx so Django knows the
# connection is HTTPS. Without this request.is_secure() returns False
# and CSRF referer validation fails even with CSRF_TRUSTED_ORIGINS set.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Application definition
INSTALLED_APPS = [
    'daphne',  # Must be first for channels
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    
    # Third-party apps
    'rest_framework',
    'rest_framework.authtoken',
    'channels',
    'corsheaders',
    'django_celery_beat',
    'django_celery_results',
    
    # Local apps
    'apps.users',
    'apps.flatpak',
    'apps.api',
    'apps.rpm',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'apps.flatpak.middleware.TimezoneMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'

# Database
# https://docs.djangoproject.com/en/5.0/ref/settings/#databases
DB_ENGINE = os.environ.get('DB_ENGINE', 'sqlite')

if DB_ENGINE == 'mysql':
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.mysql',
            'NAME': os.environ.get('DB_NAME', 'flatmanager'),
            'USER': os.environ.get('DB_USER', 'flatmanager'),
            'PASSWORD': os.environ.get('DB_PASSWORD', ''),
            'HOST': os.environ.get('DB_HOST', 'localhost'),
            'PORT': os.environ.get('DB_PORT', '3306'),
            'OPTIONS': {
                'charset': 'utf8mb4',
            },
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

# Custom User Model
AUTH_USER_MODEL = 'users.User'

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = os.environ.get('TIME_ZONE', 'UTC')
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = 'static/'
STATIC_ROOT = os.environ.get('STATIC_ROOT', str(BASE_DIR / 'staticfiles'))
# Only add the project-level static/ source dir when it actually exists.
# During production deployment only STATIC_ROOT (staticfiles/) is present;
# including a non-existent path triggers Django's staticfiles.W004 warning.
_static_src = BASE_DIR / 'static'
STATICFILES_DIRS = [_static_src] if _static_src.exists() else []

MEDIA_URL = 'media/'
MEDIA_ROOT = os.environ.get('MEDIA_ROOT', str(BASE_DIR / 'media'))

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Authentication URLs
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/dashboard/'
LOGOUT_REDIRECT_URL = '/login/'

# Celery Configuration
CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers:DatabaseScheduler'

# Ops worker pool size (publish, commit, promote, etc. — everything except builds)
CELERY_OPS_WORKER_CONCURRENCY = int(os.environ.get('CELERY_OPS_WORKER_CONCURRENCY', '4'))

# Channels Configuration
CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {
            "hosts": [os.environ.get('REDIS_URL', 'redis://localhost:6379/1')],
        },
    },
}

# Flatpak Repository Configuration
REPOS_BASE_PATH = os.environ.get('REPOS_BASE_PATH', str(BASE_DIR / 'repos'))
os.makedirs(REPOS_BASE_PATH, exist_ok=True)

# REST Framework Configuration
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.TokenAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 50,
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
        'rest_framework.renderers.BrowsableAPIRenderer',
    ],
}

# CORS Configuration
CORS_ALLOWED_ORIGINS = os.environ.get(
    'CORS_ALLOWED_ORIGINS',
    'http://localhost:3000,http://127.0.0.1:3000'
).split(',')

# Flatpak Repository Settings
FLATPAK_REPO_PATH = os.environ.get('REPOS_BASE_PATH', str(BASE_DIR / 'repos'))
FLATPAK_BUILD_PATH = os.environ.get('FLATPAK_BUILD_PATH', str(BASE_DIR / 'builds'))

# RPM Repository Settings
# Both RPM_REPO_BASE_PATH and TEMP_DIR default to siblings of REPOS_BASE_PATH
# so they land under /var/lib/flat-manager/ in production (where the service
# user has write access) rather than inside the read-only install tree.
_data_parent = str(Path(REPOS_BASE_PATH).parent)
RPM_REPO_BASE_PATH = os.environ.get('RPM_REPO_BASE_PATH', os.path.join(_data_parent, 'rpm-repos'))

# Base path for per-build RPM artifacts (sources/spec, mock cfg, and results).
# If not explicitly configured, keep it alongside Flatpak build artifacts.
RPM_BUILD_PATH = os.environ.get('RPM_BUILD_PATH', os.path.join(FLATPAK_BUILD_PATH, 'rpms'))

# Temporary directory for git clones and other short-lived work.
TEMP_DIR = os.environ.get('TEMP_DIR', os.path.join(_data_parent, 'tmp'))

# How often to auto-sync RPM repository lists from subscription-manager (hours).
RPM_REPO_SYNC_INTERVAL_HOURS = int(os.environ.get('RPM_REPO_SYNC_INTERVAL_HOURS', '24'))

# BuildStream 1 venv path (BST 1 and BST 2 have incompatible project.conf formats).
# Set to the root of a virtualenv that has BuildStream 1 installed.
# BST 2 is assumed to be available as 'bst' in the primary virtualenv.
BST1_VENV_PATH = '/opt/flat-manager/bst1-venv'

# Logging Configuration
_LOG_DIR = os.environ.get('LOG_DIR', str(BASE_DIR / 'logs'))
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
        'file': {
            'class': 'logging.FileHandler',
            'filename': os.path.join(_LOG_DIR, 'django.log'),
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console', 'file'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console', 'file'],
            'level': os.getenv('DJANGO_LOG_LEVEL', 'INFO'),
            'propagate': False,
        },
        'celery': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}

# Create required directories if they don't exist
os.makedirs(_LOG_DIR, exist_ok=True)
os.makedirs(FLATPAK_REPO_PATH, exist_ok=True)
os.makedirs(FLATPAK_BUILD_PATH, exist_ok=True)
os.makedirs(RPM_REPO_BASE_PATH, exist_ok=True)

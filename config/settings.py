"""
Django settings for OCAMDemo project.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# ==========================================
# Environment Configuration (항목 L)
# ==========================================
APP_ENV = os.environ.get("APP_ENV", "local")
if APP_ENV == "render":
    pass
elif APP_ENV == "docker":
    env_path = BASE_DIR / ".env.docker"
    print(f"[DOCKER] Loading environment: ({env_path})")
    load_dotenv(env_path)
else:
    env_path = BASE_DIR / ".env.local"
    print(f"[LOCAL] Loading environment: ({env_path})")
    load_dotenv(env_path)

SECRET_KEY = os.getenv("SECRET_KEY", "insecure-default-key")
DEBUG = os.getenv("DEBUG", "False").lower() == "true"
ALLOWED_HOSTS = os.environ.get(
    "ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

# ==========================================
# Static & Media
# ==========================================
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = os.path.join(BASE_DIR, "staticfiles")

# ==========================================
# Application definition
# ==========================================
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    # Custom Apps
    "common",
    "instance",
    "simulation",
    "result",
    "api",
]

# 항목 H: Debug Toolbar 조건부 활성화
if DEBUG:
    INSTALLED_APPS += ["debug_toolbar"]

REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    'whitenoise.middleware.WhiteNoiseMiddleware',
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

if DEBUG:
    MIDDLEWARE += ["debug_toolbar.middleware.DebugToolbarMiddleware"]

INTERNAL_IPS = ["127.0.0.1"]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                # 항목 C: 메뉴 자동 전달
                "common.context_processors.global_menus",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# ==========================================
# Database — SQLite
# ==========================================
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "ko-kr"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ==========================================
# Logs
# ==========================================
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# ==========================================
# Authentication
# ==========================================
LOGIN_REDIRECT_URL = "/instance/"
LOGOUT_REDIRECT_URL = "/accounts/login/"
LOGIN_URL = "/accounts/login/"

# ==========================================
# File-based Data Paths
# ==========================================
INSTANCES_DIR = BASE_DIR / "instances"
ALGORITHMS_DIR = BASE_DIR / "algorithms"
OUTPUTS_DIR = BASE_DIR / "outputs"

# ==========================================
# Celery Configuration (항목 D)
# ==========================================
CELERY_BROKER_URL = os.getenv(
    "CELERY_BROKER_URL",
    "redis://redis:6379/0" if APP_ENV == "docker" else "",
)
CELERY_RESULT_BACKEND = os.getenv(
    "CELERY_RESULT_BACKEND",
    "redis://redis:6379/0" if APP_ENV == "docker" else "",
)

if CELERY_BROKER_URL and CELERY_RESULT_BACKEND:
    CELERY_TASK_ALWAYS_EAGER = False
else:
    print("[LOCAL] Celery running in EAGER mode (Synchronous)")
    CELERY_TASK_ALWAYS_EAGER = True
    CELERY_TASK_EAGER_PROPAGATES = True
    CELERY_BROKER_URL = "memory://"
    CELERY_RESULT_BACKEND = "cache+memory://"

CELERY_RESULT_EXPIRES = 3600
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE

# ==========================================
# HTTPS / Security (항목 L)
# ==========================================
if APP_ENV == "docker":
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = False
    CSRF_TRUSTED_ORIGINS = [
        "https://localhost",
        "https://127.0.0.1",
    ]
elif APP_ENV == "render":
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

    CSRF_TRUSTED_ORIGINS = [
        "https://<service-name>.onrender.com"
    ]
else:
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False
    SECURE_SSL_REDIRECT = False
    CSRF_TRUSTED_ORIGINS = [
        "http://localhost",
        "http://127.0.0.1",
    ]

# 테스트 속도 향상
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

USE_WHITENOISE = os.environ.get('USE_WHITENOISE', 'False').upper() == 'TRUE'
if USE_WHITENOISE:
    STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
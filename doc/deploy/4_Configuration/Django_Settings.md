[🏠 홈](../README.md) > **Django Settings**

# ⚙️ Django settings.py 설정 가이드

---

## 📋 개요

Django 애플리케이션이 다양한 배포 환경에서 동작하기 위해 필요한 설정들

---

## 🔧 필수 확인 사항

### 1️⃣ APP_ENV 변수 확인

**위치**: `config/settings.py`

```python
import os
from pathlib import Path

# 환경 구분
APP_ENV = os.environ.get("APP_ENV", "local")
```

### 2️⃣ 환경별 .env 파일 로드

```python
from dotenv import load_dotenv

if APP_ENV == "docker":
    env_path = BASE_DIR / ".env.docker"
    load_dotenv(env_path)
elif APP_ENV == "render":
    env_path = BASE_DIR / ".env.render"
    load_dotenv(env_path)
else:  # local
    env_path = BASE_DIR / ".env.local"
    load_dotenv(env_path)
```

### 3️⃣ DEBUG 설정

```python
DEBUG = os.environ.get("DEBUG", "True") == "True"
```

**주의**:
- 개발: `DEBUG=True`
- 배포: `DEBUG=False`

### 4️⃣ SECRET_KEY 설정

```python
SECRET_KEY = os.environ.get(
    "SECRET_KEY",
    "django-insecure-local-development-key"  # ← 절대 프로덕션에 사용 금지!
)
```

---

## 🌐 Docker 배포 시 필수 설정

### ALLOWED_HOSTS

```python
ALLOWED_HOSTS = os.environ.get(
    "ALLOWED_HOSTS",
    "localhost,127.0.0.1"
).split(",")
```

**Docker 환경 (.env.docker):**
```env
ALLOWED_HOSTS=*,localhost,127.0.0.1
```

### CSRF_TRUSTED_ORIGINS

```python
if APP_ENV == "docker":
    CSRF_TRUSTED_ORIGINS = [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "https://*.trycloudflare.com",  # Cloudflare Tunnel용
    ]
```

### 정적 파일 (WhiteNoise)

```python
# Middleware에 추가
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # ← 추가
    # ... 나머지 미들웨어
]

# 정적 파일 설정
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# WhiteNoise 최적화
STATICFILES_STORAGE = (
    "whitenoise.storage.CompressedManifestStaticFilesStorage"
)
```

---

## 📝 완전한 설정 예시

```python
# config/settings.py

import os
from pathlib import Path
from dotenv import load_dotenv

# ==========================================
# 기본 설정
# ==========================================
BASE_DIR = Path(__file__).resolve().parent.parent

# ==========================================
# 환경 구분
# ==========================================
APP_ENV = os.environ.get("APP_ENV", "local")

# 환경별 .env 파일 로드
if APP_ENV == "docker":
    env_path = BASE_DIR / ".env.docker"
elif APP_ENV == "render":
    env_path = BASE_DIR / ".env.render"
else:
    env_path = BASE_DIR / ".env.local"

if env_path.exists():
    load_dotenv(env_path)

# ==========================================
# 보안 설정
# ==========================================
DEBUG = os.environ.get("DEBUG", "True") == "True"

SECRET_KEY = os.environ.get(
    "SECRET_KEY",
    "django-insecure-local-only-never-use-in-production"
)

ALLOWED_HOSTS = os.environ.get(
    "ALLOWED_HOSTS",
    "localhost,127.0.0.1"
).split(",")

# ==========================================
# CSRF 설정 (Docker 환경)
# ==========================================
if APP_ENV == "docker":
    CSRF_TRUSTED_ORIGINS = [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "https://*.trycloudflare.com",
    ]

# ==========================================
# 정적 파일
# ==========================================
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

STATICFILES_STORAGE = (
    "whitenoise.storage.CompressedManifestStaticFilesStorage"
)

# ==========================================
# Middleware
# ==========================================
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # ← 정적 파일
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# ==========================================
# 데이터베이스
# ==========================================
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# ==========================================
# Celery (비동기 작업)
# ==========================================
if APP_ENV == "render":
    CELERY_TASK_ALWAYS_EAGER = True  # Render에서는 동기 실행
else:
    CELERY_TASK_ALWAYS_EAGER = False

# ==========================================
# 이메일
# ==========================================
if DEBUG or APP_ENV == "local":
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
else:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = os.environ.get("EMAIL_HOST", "smtp.gmail.com")
    EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
    EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "True") == "True"

# ==========================================
# 로깅
# ==========================================
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": os.environ.get("LOGGING_LEVEL", "INFO"),
    },
}
```

---

## 🆘 일반적인 설정 에러

### Q: Invalid HTTP_HOST header

```
에러: Invalid HTTP_HOST header

원인: ALLOWED_HOSTS 부정확

해결:
1. config/settings.py에서 ALLOWED_HOSTS 확인
2. .env.docker에서 ALLOWED_HOSTS=* 확인
3. 컨테이너 재시작
   ./run-docker.bat
```

### Q: CSRF Verification Failed (403)

```
에러: 403 CSRF Verification Failed

원인: CSRF_TRUSTED_ORIGINS 누락

해결:
1. CSRF_TRUSTED_ORIGINS에 도메인 추가
2. Cloudflare Tunnel 사용 시:
   "https://*.trycloudflare.com" 추가
3. 컨테이너 재시작
```

### Q: ModuleNotFoundError: whitenoise

```
에러: ModuleNotFoundError

해결:
1. requirements.txt에 whitenoise 있는지 확인
2. requirements.txt 없으면 설치
   pip install whitenoise
3. 이미지 재빌드
   Y 입력하여 run-docker.bat
```

---

## 📚 관련 문서

- [환경 변수](Environment_Variables.md)
- [Docker 설정](Docker_Config.md)
- Django 공식: https://docs.djangoproject.com/en/stable/

---

**마지막 업데이트**: 2026-06-19


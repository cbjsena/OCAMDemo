[🏠 홈](../README.md) > **Docker 설정**

# 🐳 Docker/Podman 설정 상세 가이드

---

## 📋 구성 요소

```
OCAMDemo/
├── Dockerfile              # 컨테이너 이미지 정의
├── .dockerignore          # 빌드 시 제외 파일
├── docker-entrypoint.sh   # 컨테이너 시작 스크립트
├── run-docker.bat         # Windows 실행 스크립트
└── run-docker.sh          # Mac/Linux 실행 스크립트
```

---

## 🏗️ Dockerfile 상세 설명

**파일**: `Dockerfile`

```dockerfile
# ==========================================
# 1. 기본 이미지 설정
# ==========================================
FROM python:3.11-slim

# 환경 변수 (성능 최적화)
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_HOME=/app

# 작업 디렉토리 설정
WORKDIR ${APP_HOME}

# ==========================================
# 2. 시스템 패키지 설치
# ==========================================
# gcc, build-essential: 파이썬 C 확장 컴파일 필요 (numpy, scipy 등)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# ==========================================
# 3. 프로젝트 코드 복사
# ==========================================
COPY . .

# ==========================================
# 4. Python 의존성 설치
# ==========================================
RUN pip install --upgrade pip setuptools wheel && \
    pip install -r requirements.txt

# ==========================================
# 5. 정적 파일 수집 (WhiteNoise용)
# ==========================================
RUN python manage.py collectstatic --noinput --clear

# ==========================================
# 6. 스크립트 실행 권한 설정
# ==========================================
RUN chmod +x docker-entrypoint.sh

# ==========================================
# 7. 포트 노출
# ==========================================
EXPOSE 8000

# ==========================================
# 8. 데이터 보존 (호스트에 마운트)
# ==========================================
VOLUME ["/app/outputs", "/app/logs", "/app/instances", "/app/media"]

# ==========================================
# 9. 헬스 체크
# ==========================================
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8000/admin/')" || exit 1

# ==========================================
# 10. 시작 스크립트
# ==========================================
ENTRYPOINT ["./docker-entrypoint.sh"]

# 기본 명령어
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "4", \
     "--timeout", "120"]
```

### 각 섹션 설명

| 섹션 | 목적 | 중요도 |
|------|------|--------|
| FROM python:3.11 | Python 3.11 기반 이미지 | 필수 |
| ENV 변수 | 성능 최적화 | 권장 |
| apt-get | 시스템 패키지 | 필요시 |
| COPY . . | 코드 복사 | 필수 |
| pip install | Python 패키지 | 필수 |
| collectstatic | 정적 파일 | 권장 |
| EXPOSE 8000 | 포트 노출 | 필수 |
| VOLUME | 데이터 보존 | 필수 |
| HEALTHCHECK | 상태 감시 | 선택 |

---

## 📄 docker-entrypoint.sh 상세 설명

**파일**: `docker-entrypoint.sh`

```bash
#!/bin/bash
set -e

# ==========================================
# 설정
# ==========================================
DJANGO_PROJECT="config"
WSGI_MODULE="${DJANGO_PROJECT}.wsgi:application"
WORKERS=${GUNICORN_WORKERS:-4}
TIMEOUT=${GUNICORN_TIMEOUT:-120}
BIND=${GUNICORN_BIND:-"0.0.0.0:8000"}

# ==========================================
# 1. 데이터베이스 마이그레이션
# ==========================================
# 테이블 생성/업그레이드
python manage.py migrate --noinput

# ==========================================
# 2. 정적 파일 수집
# ==========================================
# CSS, JS, 이미지 등을 collectstatic 디렉토리로 복사
python manage.py collectstatic --noinput --clear

# ==========================================
# 3. Gunicorn 시작
# ==========================================
# 옵션 설명:
# --bind 0.0.0.0:8000    모든 인터페이스에서 8000 포트 수신
# --workers 4             4개의 워커 프로세스 (병렬 처리)
# --timeout 120           120초 요청 타임아웃
# --access-logfile -      STDOUT로 접근 로그 출력
# --error-logfile -       STDOUT로 에러 로그 출력
# --log-level info        로그 레벨

exec gunicorn \
    "${WSGI_MODULE}" \
    --bind "${BIND}" \
    --workers "${WORKERS}" \
    --timeout "${TIMEOUT}" \
    --access-logfile - \
    --error-logfile - \
    --log-level info
```

### 각 단계의 목적

| 단계 | 명령어 | 목적 |
|------|--------|------|
| 1 | migrate | DB 테이블 생성 |
| 2 | collectstatic | 정적 파일 준비 |
| 3 | gunicorn | 웹 서버 시작 |

---

## ⚙️ .dockerignore 설정

**파일**: `.dockerignore`

```
# 버전 관리
.git
.gitignore

# Python 캐시
__pycache__/
*.py[cod]
*.so
.Python
build/
dist/
*.egg-info/

# 가상환경
.venv/
venv/
ENV/

# IDE
.vscode/
.idea/
*.swp
*.swo

# OS
.DS_Store
Thumbs.db

# Django
db.sqlite3
staticfiles/
media/
logs/
.env*

# 테스트
.pytest_cache/
.coverage
htmlcov/

# 기타
*.log
.DS_Store
node_modules/
```

**목적**: 빌드 시간 단축 (불필요한 파일 제외)

---

## 🔄 빌드 프로세스

### 1단계: 이미지 빌드

```bash
podman build -t ocamdemo:latest .
```

**이 과정에서:**
1. Dockerfile 읽기
2. .dockerignore 파일 제외
3. 각 FROM, RUN, COPY 단계 실행
4. 이미지 계층 생성 및 캐싱
5. 최종 이미지 태그 지정

### 2단계: 컨테이너 실행

```bash
podman run -d \
  --name ocamdemo \
  -p 8000:8000 \
  -v outputs:/app/outputs \
  -v logs:/app/logs \
  ocamdemo:latest
```

**이 과정에서:**
1. 이미지에서 컨테이너 생성
2. 포트 매핑 설정
3. 볼륨 마운트
4. entrypoint 스크립트 실행
5. Gunicorn 서버 시작

---

## 🚀 고급 옵션

### 워커 수 조정

```bash
# 환경 변수로 지정
podman run -e GUNICORN_WORKERS=8 ocamdemo:latest

# 또는 .env.docker에 추가
GUNICORN_WORKERS=8
```

**최적값:**
```
권장 = (CPU 코어 수 × 2) + 1

예:
- 2코어  → 5개
- 4코어  → 9개
- 8코어  → 17개

실제 추천:
- 소규모 → 2-4개
- 중규모 → 4-8개
- 대규모 → 8-16개
```

### 타임아웃 조정

```bash
# 긴 연산이 필요한 경우
podman run -e GUNICORN_TIMEOUT=300 ocamdemo:latest
```

### 메모리 제한

```bash
podman run \
  --memory 4g \           # 최대 4GB 사용
  --memory-swap 6g \      # 스왑 포함 6GB
  ocamdemo:latest
```

---

## 📊 이미지 최적화

### 현재 이미지 크기 확인

```bash
podman images | grep ocamdemo
```

### 이미지 크기 줄이기

```bash
# 멀티스테이지 빌드 (고급)
# Dockerfile에서:
FROM python:3.11-slim as builder
# ... 빌드 단계 ...

FROM python:3.11-slim
# ... 런타임 단계 (불필요한 빌드 도구 제외) ...
```

---

## 🆘 문제 해결

### Q: 이미지 빌드 실패

```bash
# 캐시 삭제 후 재빌드
podman build --no-cache -t ocamdemo:latest .

# 상세 로그 보기
podman build --progress=plain -t ocamdemo:latest .
```

### Q: 컨테이너가 자꾸 꺼짐

```bash
# 로그 확인
podman logs ocamdemo

# 헬스 체크 상태 확인
podman inspect --format='{{.State.Health.Status}}' ocamdemo

# 상태 상세 보기
podman inspect ocamdemo | grep -A 5 Health
```

### Q: 특정 패키지 설치 실패

```dockerfile
# Dockerfile에서 필요한 시스템 패키지 추가
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \      # PostgreSQL 필요시
    libmysqlclient-dev \  # MySQL 필요시
    && rm -rf /var/lib/apt/lists/*
```

---

## 📚 관련 문서

- [환경 변수](Environment_Variables.md)
- [Django 설정](Django_Settings.md)
- Dockerfile 공식: https://docs.docker.com/engine/reference/builder/

---

**마지막 업데이트**: 2026-06-19


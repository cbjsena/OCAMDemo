# ==========================================
# Dockerfile - Django + Gunicorn
# ==========================================
# Python 3.11 기반 이미지 사용
FROM python:3.11-slim

# 환경 변수 설정
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_HOME=/app

# 작업 디렉토리 설정
WORKDIR ${APP_HOME}

# 시스템 패키지 업데이트 및 필요한 도구 설치
# gcc, python3-dev: C 확장 컴파일 시 필요 (numpy, numba 등)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# 로컬 코드 복사
COPY . .

# Python 의존성 설치
RUN pip install --upgrade pip setuptools wheel && \
    pip install -r requirements.txt

# 정적 파일 수집 (WhiteNoise용)
RUN python manage.py collectstatic --noinput --clear

# docker-entrypoint.sh 실행 권한 설정
RUN chmod +x docker-entrypoint.sh

# 포트 노출
EXPOSE 8000

# 볼륨 마운트 포인트 선언
# (실제 마운트는 podman run 시에 -v 옵션으로 수행)
VOLUME ["/app/outputs", "/app/logs", "/app/instances", "/app/media"]

# 헬스 체크 설정 (선택)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8000/admin/')" || exit 1

# 시작 스크립트 실행
ENTRYPOINT ["./docker-entrypoint.sh"]

# 기본 명령어 (entrypoint에서 지정할 수도 있음)
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "4", "--timeout", "120"]


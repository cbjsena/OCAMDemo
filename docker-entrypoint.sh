#!/bin/bash
# Docker 진입점 스크립트
# Django 애플리케이션을 시작하기 전에 필요한 작업 수행

set -e

# ==========================================
# 변수 설정
# ==========================================
DJANGO_PROJECT="config"
WSGI_MODULE="${DJANGO_PROJECT}.wsgi:application"
WORKERS=${GUNICORN_WORKERS:-4}
TIMEOUT=${GUNICORN_TIMEOUT:-120}
BIND=${GUNICORN_BIND:-"0.0.0.0:8000"}

echo "================================"
echo "🚀 Django 애플리케이션 시작"
echo "================================"

# ==========================================
# 1. 데이터베이스 마이그레이션
# ==========================================
echo ""
echo "📦 데이터베이스 마이그레이션 실행..."
python manage.py migrate --noinput
if [ $? -eq 0 ]; then
    echo "✅ 마이그레이션 완료"
else
    echo "⚠️ 마이그레이션 경고 (무시하고 계속)"
fi

# ==========================================
# 2. 정적 파일 수집
# ==========================================
echo ""
echo "📦 정적 파일 수집..."
python manage.py collectstatic --noinput --clear
echo "✅ 정적 파일 수집 완료"

# ==========================================
# 3. Gunicorn 시작
# ==========================================
echo ""
echo "================================"
echo "✨ Gunicorn 서버 시작"
echo "================================"
echo "설정:"
echo "  - Bind: ${BIND}"
echo "  - Workers: ${WORKERS}"
echo "  - Timeout: ${TIMEOUT}s"
echo "  - App: ${WSGI_MODULE}"
echo ""

# gunicorn 실행
exec gunicorn \
    "${WSGI_MODULE}" \
    --bind "${BIND}" \
    --workers "${WORKERS}" \
    --timeout "${TIMEOUT}" \
    --access-logfile - \
    --error-logfile - \
    --log-level info


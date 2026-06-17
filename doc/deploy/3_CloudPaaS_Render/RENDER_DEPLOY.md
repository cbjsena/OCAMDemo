[🏠 홈](../README.md) > **Render PaaS 배포** (참고용)

# ☁️ Render PaaS 배포 가이드 (폐기됨)

**⚠️ 주의**: 현재 프로젝트에는 **권장하지 않습니다**. [폐기 사유](DEPRECATED.md) 참고.

---

## 📋 개요

Render는 무료 PaaS 서비스지만, 현재 프로젝트의 요구사항과 맞지 않습니다.

### 주요 문제

| 문제 | 영향 |
|------|------|
| **메모리 512MB** | Gurobi 등 최적화 연산 불가 (OOM 에러) |
| **데이터 손실** | 인스턴스 재배포 시 SQLite 데이터 초기화 |
| **Celery 제약** | `CELERY_TASK_ALWAYS_EAGER=True` 강제 (비동기 불가) |
| **종료 정책** | 비활동 시 500 에러 (Cold Start 문제) |

---

## ✅ 권장 대안

### 개발/테스트
→ [Podman Local](../1_LocalPC_Podman/QUICK_START.md)

### 외부 공개 (도메인 없음)
→ [Cloudflare Quick Tunnel](../2_PublicTunnel_Cloudflare/QUICK_TUNNEL_SETUP.md)

### 정식 서비스 (도메인 있음)
→ [Cloudflare Permanent Tunnel](../2_PublicTunnel_Cloudflare/PERMANENT_TUNNEL_SETUP.md)

---

## 📚 Render 설정 (참고용)

혹시 Render를 사용해야 한다면, 다음 설정이 필요합니다:

### 1. requirements.txt 생성

```bash
pip freeze > requirements.txt
```

### 2. Django settings.py 수정

```python
# Render 환경 분리
APP_ENV = os.environ.get("APP_ENV", "local")

if APP_ENV == "render":
    DEBUG = False
    SECRET_KEY = os.environ.get("SECRET_KEY")
    ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "").split(",")
    
    # HTTPS 설정
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    CSRF_TRUSTED_ORIGINS = [
        f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME')}",
    ]
    
    # Celery 동기 모드 (중요!)
    CELERY_TASK_ALWAYS_EAGER = True
```

### 3. Render.yaml (선택)

```yaml
services:
  - type: web
    name: ocamdemo
    env: python
    region: singapore
    plan: free
    buildCommand: |
      pip install -r requirements.txt
      python manage.py collectstatic --noinput
      python manage.py migrate
    startCommand: gunicorn config.wsgi:application
```

### 4. Render 환경변수

```
APP_ENV=render
DEBUG=False
SECRET_KEY=your-secret-key-here
ALLOWED_HOSTS=ocamdemo.onrender.com
```

---

## ❌ 결론

Render는 일반적인 Django 앱에는 좋지만, **다음과 같은 이유로 OCAMDemo에는 부적합**:

```
1. 메모리 부족
   → 최적화 알고리즘 실행 불가

2. 데이터 보존 어려움
   → 재배포 시 데이터 손실

3. Celery 제약
   → 비동기 작업 불가능

4. 성능 제한
   → 동시 사용자 1-2명만 가능
```

---

## 🎯 최종 권장사항

```
┌─ Render을 고려 중?
│
├─ 크기: Medium 이상 (최소 1GB)
├─ 데이터: PostgreSQL (SQLite 대체)
├─ 비용: $20+/월
│
└─ 결론: 이 비용이라면 전용 VPS가 낫다!
```

---

## 📞 추가 정보

- Render 공식: https://render.com
- Render 문서: https://render.com/docs

---

**마지막 업데이트**: 2026-06-19  
**상태**: 폐기됨 - 현재 프로젝트에 부적합


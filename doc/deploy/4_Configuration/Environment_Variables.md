[🏠 홈](../README.md) > **환경 변수 설정**

# 🔐 환경 변수 설정 가이드

---

## 📋 개요

Django 애플리케이션은 배포 환경에 따라 다른 설정이 필요합니다:

```
.env.local       로컬 개발 환경
.env.docker      Docker/Podman 환경  ← 현재
.env.render      Render PaaS (참고용)
```

---

## 🚀 .env.docker 파일 생성

**파일 위치**: 프로젝트 루트 (`OCAMDemo/` 폴더)

**파일명**: `.env.docker` (정확히)

### ✅ 필수 항목

```env
# Django 기본 설정
DEBUG=False
SECRET_KEY=django-insecure-example-key-12345678901234567890example
APP_ENV=docker

# 호스트 설정 (외부 접속 허용)
ALLOWED_HOSTS=*,localhost,127.0.0.1

# 데이터베이스 (SQLite 사용)
DATABASE_URL=sqlite:///db.sqlite3
```

### 📌 권장 항목

```env
# CSRF 보안 설정
CSRF_TRUSTED_ORIGINS=http://localhost:8000,http://127.0.0.1:8000

# 로깅
LOGGING_LEVEL=INFO

# 이메일 (개발 중에는 콘솔로 출력)
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
```

### 🌐 외부 공개 시 추가

**Cloudflare Tunnel 사용하는 경우:**

```env
# Quick Tunnel (URL이 변할 때마다 업데이트)
ALLOWED_HOSTS=*,localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=http://localhost:8000,https://*.trycloudflare.com

# Permanent Tunnel (도메인 고정)
ALLOWED_HOSTS=*,localhost,127.0.0.1,ocamdemo.example.com
CSRF_TRUSTED_ORIGINS=http://localhost:8000,https://ocamdemo.example.com
```

---

## 🔑 SECRET_KEY 생성

Django 보안 키를 안전하게 생성:

### 방법 1: Django 기본 제공 명령어

```bash
python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'
```

출력:
```
django-insecure-a_b_c_d_e_f_g_h_i_j_k_l_m_n_o_p_q_r_s_t_u_v_w_x_y_z
```

### 방법 2: Python 표준 라이브러리

```bash
python -c 'import secrets; print(secrets.token_urlsafe(50))'
```

### 생성된 키를 .env.docker에 저장

```env
SECRET_KEY=django-insecure-a_b_c_d_e_f_g_h_i_j_k_l_m_n_o_p_q_r_s_t_u_v_w_x_y_z
```

---

## ✅ 완전한 .env.docker 예시

```env
# ==========================================
# Django 기본 설정
# ==========================================
DEBUG=False
SECRET_KEY=django-insecure-abcdefghijklmnopqrstuvwxyz1234567890abcd
APP_ENV=docker

# ==========================================
# 호스트 및 도메인 설정
# ==========================================
# 로컬 + Cloudflare Tunnel 허용
ALLOWED_HOSTS=*,localhost,127.0.0.1

# CSRF 교차 사이트 요청 방지
CSRF_TRUSTED_ORIGINS=http://localhost:8000,http://127.0.0.1:8000,https://*.trycloudflare.com

# ==========================================
# 데이터베이스
# ==========================================
DATABASE_URL=sqlite:///db.sqlite3

# ==========================================
# 보안 (Docker 환경에서는 False)
# ==========================================
SECURE_SSL_REDIRECT=False
SESSION_COOKIE_SECURE=False
CSRF_COOKIE_SECURE=False

# ==========================================
# 로깅
# ==========================================
LOGGING_LEVEL=INFO

# ==========================================
# 이메일 (개발/테스트용: 콘솔 출력)
# ==========================================
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
```

---

## 🔧 환경별 사용 예시

### 발전 1: 로컬 개발

```env
DEBUG=False
SECRET_KEY=your-secret-key
APP_ENV=docker
ALLOWED_HOSTS=*,localhost,127.0.0.1
```

### 단계 2: Cloudflare Quick Tunnel (임시)

```env
DEBUG=False
SECRET_KEY=your-secret-key
APP_ENV=docker
ALLOWED_HOSTS=*,localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=http://localhost:8000,https://*.trycloudflare.com
```

### 단계 3: Permanent Tunnel (정식)

```env
DEBUG=False
SECRET_KEY=your-secret-key
APP_ENV=docker
ALLOWED_HOSTS=*,localhost,127.0.0.1,ocamdemo.example.com
CSRF_TRUSTED_ORIGINS=http://localhost:8000,https://ocamdemo.example.com
```

---

## ⚠️ 주의사항

### 1️⃣ .env 파일은 커밋하지 말기

```bash
# .gitignore에 추가됨 (이미 설정됨)
.env*
```

### 2️⃣ SECRET_KEY 노출 금지

```
❌ 하지 말것:
- GitHub에 커밋
- 다른 사람에게 공유
- 공개 문서에 명시

✅ 할 것:
- 로컬에만 보관
- 강력한 랜덤 키 사용
- 정기적으로 변경
```

### 3️⃣ ALLOWED_HOSTS 신중하게

```
❌ 위험:
ALLOWED_HOSTS=*  (모든 호스트 허용)

✅ 안전:
ALLOWED_HOSTS=localhost,127.0.0.1,ocamdemo.example.com
```

---

## 🔍 파일 확인

### 파일 존재 확인

```bash
# Windows
dir .env.docker

# Mac/Linux
ls -la .env.docker
```

### 내용 확인

```bash
# 전체 내용 보기
cat .env.docker

# 특정 변수만 보기
grep ALLOWED_HOSTS .env.docker
```

---

## 🆘 문제 해결

### Q: .env 파일을 찾을 수 없어요

```
확인:
1. 파일명이 정확한가? (.env.docker)
2. 파일이 프로젝트 루트에 있나?
3. 숨김 파일인가? (Windows: 보기 → 숨김 파일 표시)
```

### Q: 환경 변수가 적용되지 않아요

```
해결:
1. 파일명 재확인 (.env.docker)
2. 컨테이너 재시작
   ./run-docker.bat
3. 로그 확인
   podman logs ocamdemo | grep SECRET_KEY
```

### Q: DEBUG=False인데 에러 페이지가 안 보여요

```
진단:
- 의도됨 (DEBUG=False는 보안임)
- 로그로 확인
  podman logs ocamdemo

해결:
- 개발할 때만 DEBUG=True
- 배포할 때는 DEBUG=False
```

---

## 📚 관련 문서

- [Django 설정](Django_Settings.md)
- [Docker 설정](Docker_Config.md)
- [Podman Local](../1_LocalPC_Podman/QUICK_START.md)

---

**마지막 업데이트**: 2026-06-19


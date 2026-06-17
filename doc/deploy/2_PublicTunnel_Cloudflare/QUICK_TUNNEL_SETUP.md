[🏠 홈](../README.md) > **Cloudflare Quick Tunnel**

# ⚡ Cloudflare Quick Tunnel (도메인 없이 외부 공개)

**목표**: 도메인 없이도 스마트폰이나 외부 네트워크에서 접속 가능한 HTTPS URL 생성

---

## 💡 Quick Tunnel이란?

```
로컬 PC (Podman) 
         ↓
  http://localhost:8000
         ↓
  Cloudflare Quick Tunnel
         ↓
  https://xxx-yyyy-zzzz.trycloudflare.com (자동 생성 URL)
         ↓
  외부 기기에서 접속 가능! 🌐
```

### 특징
✅ **도메인 불필요** - 도메인 구매 없이 즉시 공개 URL 생성  
✅ **30초 설정** - 매우 간단  
✅ **HTTPS 자동** - 보안 연결 자동 제공  
✅ **방화벽 우회** - 사내 방화벽 우회 가능  
❌ **임시 URL** - 매번 실행할 때마다 URL 변경  
❌ **영구성 없음** - 임시 테스트용  

---

## 🚀 빠른 시작 (2개 터미널)

### 터미널 1: Django 실행

```bash
# Podman 컨테이너 시작
./run-docker.bat          # Windows
# 또는
./run-docker.sh           # Mac/Linux
```

### 터미널 2: Tunnel 공개

```bash
# cloudflared 설치 확인
cloudflared --version

# Quick Tunnel 시작
cloudflared tunnel --url http://localhost:8000
```

**출력 예:**
```
Your quick tunnel is online!
URL: https://brilliant-elephant-abc123.trycloudflare.com
```

### 3️⃣ 접속 테스트

```
스마트폰, 태블릿, 다른 PC에서 접속:
https://brilliant-elephant-abc123.trycloudflare.com
```

❗ **중요**: URL은 터미널 2를 재시작할 때마다 변경됩니다

---

## 🔧 설정 확인

Quick Tunnel을 사용하려면 Django 설정이 필요합니다.

### 1️⃣ .env.docker 확인

```env
DEBUG=False
SECRET_KEY=your-secret-key
APP_ENV=docker
ALLOWED_HOSTS=*,localhost,127.0.0.1
```

**중요**: `ALLOWED_HOSTS=*` 로 모든 외부 접속 허용

### 2️⃣ config/settings.py 확인

앱이 `docker` 모드일 때:

```python
if APP_ENV == "docker":
    CSRF_TRUSTED_ORIGINS = [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "https://*.trycloudflare.com",  # ← 이 줄 필수!
    ]
```

**없으면 추가**

---

## ⏱️ 워크플로우

### 매일 반복하는 과정

```
아침:
1. 터미널 1 열기 → run-docker.bat 실행
2. 컨테이너 시작 대기 (1-2분)
3. 로그에서 "✅ Gunicorn 서버 시작" 확인

4. 터미널 2 열기 → cloudflared tunnel --url http://localhost:8000 실행
5. 출력된 URL을 팀에 공유
6. 외부 접속 가능한 시간 (터미널 2 실행 중):
   
저녁:
7. Ctrl+C 로 터미널 2 종료
8. Ctrl+C 로 터미널 1 종료 (또는 그냥 놔둠)
```

---

## 🚨 주의사항

### 1️⃣ URL은 계속 변한다

```
Session 1: https://xxx-yyy-zzz.trycloudflare.com
↓ (터미널 재시작)
Session 2: https://aaa-bbb-ccc.trycloudflare.com (새로운 URL!)
```

**해결**: 
- 터미널 2를 **계속 켜두기** → 같은 URL 유지
- 코드 수정 시 터미널 1만 재시작 (터미널 2는 유지)

### 2️⃣ 터미널을 닫으면 접속 불가

```
터미널 2 닫음 → 즉시 외부 접속 불가
```

**정식 운영이 필요하면** → [Permanent Tunnel](PERMANENT_TUNNEL_SETUP.md) 사용

### 3️⃣ 보안

Quick Tunnel은 테스트용이므로:
- 민감한 데이터 노출 주의
- 공개하기 전에 .env.docker에서 DEBUG=False 확인
- SECRET_KEY 변경 필수

---

## 🆘 문제 해결

### Q: CSRF 에러가 나요
```
에러: CSRF Verification Failed (403)

해결:
1. ALLOWED_HOSTS 확인
   ALLOWED_HOSTS=*
   
2. CSRF_TRUSTED_ORIGINS 확인
   "https://*.trycloudflare.com"
   
3. 컨테이너 재시작
   ./run-docker.bat
```

### Q: HTTP_HOST 에러가 나요
```
에러: Invalid HTTP_HOST header

같은 해결법:
→ ALLOWED_HOSTS=* 확인 후 재시작
```

### Q: cloudflared 설치 안 됨

```bash
# Windows
winget install cloudflare.cloudflared

# Mac
brew install cloudflare/cloudflare/cloudflared

# Linux
curl -L https://pkg.cloudflare.com/cloudflared-release.gpg.key | sudo apt-key add -
sudo apt install cloudflared
```

---

## 📚 다음 단계

### URL을 유지하면서 정식 서비스를 원하신가요?

→ [Permanent Tunnel Setup](PERMANENT_TUNNEL_SETUP.md) (도메인 필요)

### 로컬에서만 사용하실 건가요?

→ [Podman Local](../1_LocalPC_Podman/QUICK_START.md)

---

## 🎯 Quick Tunnel vs Permanent Tunnel

| 항목 | Quick Tunnel | Permanent Tunnel |
|------|------|------|
| **도메인** | 불필요 | 필요 |
| **URL 변경** | 매번 변함 | 고정 |
| **비용** | 무료 | $20+/년 (도메인) |
| **설정** | 30초 | 10분 |
| **추천** | 임시 테스트 데모 | 정식 운영 서비스 |

---

**마지막 업데이트**: 2026-06-19


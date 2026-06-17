[🏠 홈](../README.md) > **Cloudflare Permanent Tunnel**

# 🔒 Cloudflare Permanent Tunnel (도메인 기반 정식 배포)

**목표**: 고정 도메인으로 영구 공개 URL 생성

---

## 💡 Permanent Tunnel이란?

```
로컬 PC (Podman)
         ↓
  http://localhost:8000
         ↓  
  Cloudflare Tunnel
         ↓
  https://ocamdemo.example.com (고정 도메인)
         ↓
  정식 서비스! 🎯
```

### 특징
✅ **고정 도메인** - URL 영구 변경 없음  
✅ **정식 서비스** - 운영 서비스용  
✅ **HTTPS 자동** - 보안  
✅ **커스텀 도메인** - 자신의 도메인 사용  
❌ **도메인 필수** - 도메인 구매/등록 필요  
❌ **추가 비용** - 도메인 연간 비용  

---

## 📋 사전 준비 (필수)

### 1️⃣ Cloudflare 계정

- [ ] Cloudflare 계정 생성: https://dash.cloudflare.com/sign-up
- [ ] 계정 이메일: ________________

### 2️⃣ 도메인 준비

- [ ] 도메인 이미 소유 중
- [ ] 또는 도메인 구매 필요 (GoDaddy, Namecheap 등)
- [ ] Cloudflare에 도메인 등록 필요
- [ ] 도메인: ________________

### 3️⃣ 서브도메인 결정

- [ ] 서브도메인 이름 결정 (예: ocamdemo)
- [ ] 최종 URL: **ocamdemo.example.com** (예시)

### 4️⃣ 필수 도구

- [ ] cloudflared 설치됨 (`cloudflared --version`)
- [ ] Podman 설치되고 실행 가능
- [ ] Django 컨테이너 준비됨

---

## 🚀 설정 단계별 진행

### Step 1: cloudflared 로그인

```bash
cloudflared tunnel login
```

실행 후:
1. 브라우저가 자동으로 열림 (Cloudflare 로그인 페이지)
2. 계정으로 로그인
3. "Select your domain" 창에서 **도메인 선택**
4. "Authorize" 버튼 클릭
5. 터미널에서 성공 메시지 확인

**완료 표시:**
```
✓ Successfully connected! Tunnel credentials have been saved to:
  ~/.cloudflared/cert.pem
```

---

### Step 2: Tunnel 생성

```bash
cloudflared tunnel create ocamdemo
```

**출력:**
```
Tunnel UUID: 12345678-abcd-efgh-ijkl-0000examples000

Tunnel credentials have been saved to:
~/.cloudflared/12345678-abcd-efgh-ijkl-0000examples000.json

Created tunnel ocamdemo
```

🔖 **반드시 기록:**
```
Tunnel Name: ocamdemo
Tunnel ID:   12345678-abcd-efgh-ijkl-0000examples000  ← 복사!
```

---

### Step 3: config.yml 작성

**파일 위치:**
- Windows: `C:\Users\<USERNAME>\.cloudflared\config.yml`
- Mac/Linux: `~/.cloudflared/config.yml`

**파일 내용:**

```yaml
tunnel: 12345678-abcd-efgh-ijkl-0000examples000
credentials-file: ~/.cloudflared/12345678-abcd-efgh-ijkl-0000examples000.json

ingress:
  - hostname: ocamdemo.example.com
    service: http://localhost:8000
  - service: http_status:404
```

**변경 필요:**
- `tunnel`: Step 2의 Tunnel ID 입력
- `credentials-file`: Step 2의 파일명 입력  
- `hostname`: 자신의 도메인 (예: myapp.mydomain.com)

---

### Step 4: DNS CNAME 레코드 추가

**Cloudflare 대시보드에서:**

1. 🌐 **Websites** 클릭 → 도메인 선택
2. 🔧 **DNS** 탭 클릭
3. ➕ **Add record** 버튼 클릭
4. 다음 정보 입력:

| 필드 | 값 |
|------|-----|
| Type | **CNAME** |
| Name | **ocamdemo** |
| Content | **12345678-abcd-efgh.cfargotunnel.com** |
| TTL | Auto |
| Proxy status | **Proxied** (🟠 주황색) |

**입력 후:**
- Save 클릭
- DNS 전파 대기 (보통 1-5분)

---

### Step 5: Tunnel 실행

**터미널 1: Django 컨테이너 시작**

```bash
./run-docker.bat          # Windows
# 또는
./run-docker.sh           # Mac/Linux
```

컨테이너 시작 완료 대기 (로그에서 "✅ Gunicorn 시작" 확인)

**터미널 2: Cloudflare Tunnel 시작**

```bash
cloudflared tunnel run ocamdemo
```

**정상 출력:**
```
INF | Welcome to Cloudflare Tunnel
INF | 
INF | This application will now serve traffic from your machine to the internet
INF | at https://ocamdemo.example.com
INF |
INF | Registered tunnel connection connIndex=0 connection=...
```

---

## 🧪 테스트

### 로컬 PC에서 테스트

```bash
# 1. URL 접속
https://ocamdemo.example.com

# 2. curl로 테스트
curl https://ocamdemo.example.com
```

### 다른 기기/네트워크에서 테스트

```
스마트폰에서 접속:
https://ocamdemo.example.com

또는 다른 PC에서:
https://ocamdemo.example.com
```

**확인 사항:**
- ✅ 페이지 로드됨
- ✅ HTTPS (🔒 안전)로 표시
- ✅ 정적 파일(CSS, JS) 정상 로드
- ✅ 로그인/기본 기능 작동

---

## 📝 Django 설정 확인

### .env.docker

```env
DEBUG=False
SECRET_KEY=your-strong-secret-key
APP_ENV=docker
ALLOWED_HOSTS=*,localhost,127.0.0.1,ocamdemo.example.com
CSRF_TRUSTED_ORIGINS=http://localhost:8000,https://ocamdemo.example.com
```

### config/settings.py

```python
if APP_ENV == "docker":
    CSRF_TRUSTED_ORIGINS = [
        "http://localhost:8000",
        "https://ocamdemo.example.com",
    ]
```

---

## ✅ 배포 완료 체크리스트

```
✅ cloudflared 설치되어 있음
✅ Cloudflare 계정 준비
✅ 도메인 Cloudflare 등록
✅ Step 1: cloudflared tunnel login 완료
✅ Step 2: cloudflared tunnel create 완료
✅ Step 3: config.yml 작성 완료
✅ Step 4: DNS CNAME 레코드 추가 완료
✅ Step 5: Tunnel 실행 중
✅ https://ocamdemo.example.com 접속 가능
✅ 정적 파일 정상 로드
✅ 데이터베이스 마이그레이션 성공

배포 완료! 🎉
```

---

## 🔄 일상 운영

### 매일 시작

```bash
# 터미널 1
./run-docker.bat

# 터미널 2 (별도 터미널에서)
cloudflared tunnel run ocamdemo
```

### 코드 수정 후

```bash
# 터미널 2는 그대로 두고
# 터미널 1에서만 컨테이너 재시작

Y 입력하여 이미지 재빌드 → 컨테이너 재시작
```

### 종료

```bash
# 크기 순서대로 정리 (선택)
# 터미널 2: Ctrl+C (Tunnel 종료)
# 터미널 1: Ctrl+C (Django 종료)
```

---

## 🆘 문제 해결

### CNAME 레코드 추가 안 됨

```
확인:
1. Cloudflare 대시보드에서 정말 추가되었나?
2. Type: CNAME
3. Proxy status: Proxied (🟠 주황색)

해결:
1. 다시 한번 확인
2. 5-10분 기다리기 (DNS 전파)
3. 재시도
```

### DNS lookup failure 에러

```
진단:
cloudflared logs 확인

해결:
1. DNS 전파 완료 대기 (5-15분)
2. Cloudflare config.yml의 hostname 확인
3. CNAME 레코드 재확인

# Mac/Linux에서 DNS 확인
nslookup ocamdemo.example.com
```

### Connection refused

```
에러: connection refused at localhost:8000

확인:
1. 터미널 1에서 Django 실행 중인가?
   podman ps | grep ocamdemo
   
2. 포트 8000이 열려있나?
   
해결:
1. 터미널 1 재시작
   ./run-docker.bat
   
2. 컨테이너 로그 확인
   podman logs ocamdemo
```

### CSRF Verification Failed

```
에러: 403 CSRF Verification Failed

원인: CSRF_TRUSTED_ORIGINS 미설정

해결:
1. config/settings.py에서 확인
   CSRF_TRUSTED_ORIGINS에 도메인 추가
   
2. .env.docker도 확인
   
3. 컨테이너 재시작
   ./run-docker.bat
```

---

## 🏃 고급: 스크립트 자동화

매번 명령어 입력 대신 배치 파일로 자동화:

**start_tunnel.bat (Windows)**
```batch
@echo off
echo Starting Cloudflare Tunnel...
cloudflared tunnel run ocamdemo
```

**start_tunnel.sh (Mac/Linux)**
```bash
#!/bin/bash
echo "Starting Cloudflare Tunnel..."
cloudflared tunnel run ocamdemo
```

사용:
```bash
./start_tunnel.bat
# 또는
./start_tunnel.sh
```

---

## 📚 관련 문서

- [Quick Tunnel (도메인 불필요)](QUICK_TUNNEL_SETUP.md)
- [Podman Local](../1_LocalPC_Podman/QUICK_START.md)
- [환경 변수](../4_Configuration/Environment_Variables.md)
- [Django 설정](../4_Configuration/Django_Settings.md)

---

## 📞 도움말

- Cloudflare Tunnel 공식: https://developers.cloudflare.com/cloudflare-one/
- cloudflared 문서: https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/
- cloudflared 릴리스: https://github.com/cloudflare/cloudflared/releases

---

**마지막 업데이트**: 2026-06-19


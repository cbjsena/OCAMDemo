[🏠 홈](../README.md) > **Podman Local 배포**

# ⚡ 30초 시작 가이드 - Podman 로컬 실행

## 📋 목표
로컬 PC에서 Django 애플리케이션을 빠르게 실행하고 테스트하기

---

## 🚀 Windows 사용자

### 1️⃣ 프로젝트 폴더로 이동
```bash
cd D:\dev\django\OCAMDemo
```

### 2️⃣ 스크립트 실행
```bash
# 방법 1: 파일 탐색기에서 직접 실행
run-docker.bat 이중클릭

# 방법 2: 터미널에서 실행
run-docker.bat
```

### 3️⃣ 웹 접속
```
http://localhost:8000
```

---

## 🍎 Mac 사용자

### 1️⃣ 터미널 열기
```bash
cd /path/to/OCAMDemo
```

### 2️⃣ 스크립트 실행
```bash
chmod +x run-docker.sh  # 처음 1회만
./run-docker.sh
```

### 3️⃣ 웹 접속
```
http://localhost:8000
```

---

## 🐧 Linux 사용자

### 1️⃣ 터미널에서 실행
```bash
cd ~/OCAMDemo
chmod +x run-docker.sh
./run-docker.sh
```

### 2️⃣ 웹 접속
```
http://localhost:8000
```

---

## ✅ 스크립트가 자동으로 하는 것

실행하면 다음이 자동으로 진행됩니다:

1. **필요한 폴더 생성**
   - `outputs/` - 알고리즘 결과 저장용
   - `logs/` - 로그 파일 저장용
   - `instances/` - 테스트 데이터용
   - `media/` - 사용자 업로드 파일용

2. **Docker 이미지 빌드 (선택)**
   - 코드 변경했으면: `Y` 입력
   - 코드 미변경: `N` 입력

3. **Podman 컨테이너 시작**
   - 기존 컨테이너 자동 정지
   - 새 컨테이너 실행
   - 데이터베이스 마이그레이션
   - 정적 파일 수집

4. **로그 출력**
   - 실시간 로그 보기
   - `Ctrl+C` 입력으로 종료

---

## 🌐 다양한 방법으로 접속

### 같은 PC에서
```
http://localhost:8000
```

### 같은 네트워크의 다른 기기에서
```bash
# 1. PC의 IP 주소 확인
Windows:  ipconfig
Mac/Linux: ifconfig

# 2. 다른 기기에서 접속
http://<PC_IP>:8000

예: http://192.168.1.100:8000
```

---

## 📊 실행 상태 확인

### 터미널 명령어
```bash
# 컨테이너 목록 확인
podman ps

# 특정 컨테이너 로그 보기
podman logs ocamdemo

# 컨테이너 상세 정보
podman inspect ocamdemo
```

### 웹에서 확인
```
http://localhost:8000/admin/
```
Django 관리 페이지 접속 가능 여부 확인

---

## 🛑 종료하기

### 방법 1: 터미널에서 Ctrl+C 입력
```
(그냥 누르면 됨)
```

### 방법 2: 다른 터미널에서 명령어 실행
```bash
podman stop ocamdemo
```

---

## ⚠️ 주의사항

1. **Podman 필수**: Docker 대신 Podman을 사용합니다
2. **포트 8000 확인**: 다른 프로그램이 포트 8000을 사용 중이면 충돌 발생
3. **첫 실행 시간**: 첫 빌드는 수 분 소요 (이후는 빠름)

---

## 🔍 문제가 발생했나요?

[FAQ & 문제 해결](FAQ_TROUBLESHOOTING.md) 문서 참고

---

## 📖 다음 단계

더 자세히 알고 싶으면:
- [상세 가이드](DETAILED_GUIDE.md) - 각 단계별 상세 설명
- [Docker 설정](../4_Configuration/Docker_Config.md) - Dockerfile, entrypoint 이해
- [환경 변수](../4_Configuration/Environment_Variables.md) - .env.docker 설정

---

## 🎯 외부 공개를 원한다면

이미 로컬에서 실행 중이라면:
- **도메인 없음** → [Cloudflare Quick Tunnel](../2_PublicTunnel_Cloudflare/QUICK_TUNNEL_SETUP.md)
- **도메인 있음** → [Cloudflare Permanent Tunnel](../2_PublicTunnel_Cloudflare/PERMANENT_TUNNEL_SETUP.md)

---

**마지막 업데이트**: 2026-06-19


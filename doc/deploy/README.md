# 🚀 OCAMDemo 배포 가이드

**목차**: 자신의 상황에 맞는 배포 방식을 선택하세요

---

## 📊 배포 방식 비교 및 선택

| 상황 | 추천 방식 | 난이도 | 비용 | 성능 | 링크 |
|------|----------|--------|------|------|------|
| **개발/테스트 중** | 🟢 Podman Local | ⭐ | 무료 | ⭐⭐⭐ | [시작](1_LocalPC_PodmanUICK_START.md) |
| **임시 외부 공개** | 🟡 Quick Tunnel | ⭐⭐ | 무료 | ⭐⭐ | [시작](2_PublicTunnel_CloudflareUICK_TUNNEL_SETUP.md) |
| **정식 운영 (도메인)** | 🔵 Permanent Tunnel | ⭐⭐⭐ | $20+/년 | ⭐⭐⭐ | [시작](2_PublicTunnel_CloudflareERMANENT_TUNNEL_SETUP.md) |
| **클라우드 배포** | 🔴 Render (비추천) | ⭐⭐ | 제한 무료 | ⭐ | [참고](3_CloudPaaS_RenderEPRECATED.md) |

---

## 🎯 빠른 선택 가이드

### 💻 로컬 PC에서만 실행하고 싶어요
```
상황: 개발 중이거나 같은 네트워크 내 기기에서만 접속
→ 1_LocalPC_Podman/QUICK_START.md 로 이동
```

### 🌐 외부에서도 접속 가능하게 하고 싶어요 (도메인 없음)
```
상황: 스마트폰이나 외부 네트워크에서 접속, 도메인 불필요
→ 2_PublicTunnel_Cloudflare/QUICK_TUNNEL_SETUP.md 로 이동
```

### 🏢 정식 도메인으로 서비스하고 싶어요
```
상황: 고정 도메인 필요, 정식 운영
→ 2_PublicTunnel_Cloudflare/PERMANENT_TUNNEL_SETUP.md 로 이동
```

### ☁️ 클라우드에 배포하고 싶어요
```
⚠️ 주의: 현재 Render는 권장하지 않음 (메모리 부족)
참고: 3_CloudPaaS_Render/DEPRECATED.md
```

---

## 📁 폴더 구조

```
final/
├── README.md (이 파일)
│
├── 1_LocalPC_Podman/
│   ├── QUICK_START.md              ⭐ 가장 먼저 읽을 것
│   ├── DETAILED_GUIDE.md           자세한 설명
│   └── FAQ_TROUBLESHOOTING.md      문제 해결
│
├── 2_PublicTunnel_Cloudflare/
│   ├── QUICK_TUNNEL_SETUP.md       도메인 없이 외부 공개
│   ├── PERMANENT_TUNNEL_SETUP.md   도메인으로 정식 운영
│   └── NGROK_ALTERNATIVE.md        Ngrok 대안 (참고용)
│
├── 3_CloudPaaS_Render/
│   ├── RENDER_DEPLOY.md            Render PaaS 배포
│   └── DEPRECATED.md               폐기 사유
│
├── 4_Configuration/
│   ├── Environment_Variables.md    .env 설정
│   ├── Django_Settings.md          Django settings.py
│   └── Docker_Config.md            Docker 설정
│
└── 5_Reference/
    ├── Architecture_Diagram.md     시스템 구조
    ├── Deployment_Matrix.md        상세 비교표
    └── Checklists/
        ├── Pre_Deployment_Checklist.md
        ├── Post_Deployment_Checklist.md
        └── Troubleshooting_Checklist.md
```

---

## ✅ 사전 준비 (모든 배포 방식 공통)

### 1. 필수 도구 설치

**Windows:**
```bash
# Podman 설치 (Docker 대신 사용)
winget install podman

# cloudflared 설치 (Tunnel 공개 시에만 필요)
winget install cloudflare.cloudflared
```

**Mac:**
```bash
brew install podman
brew install cloudflare/cloudflare/cloudflared
```

**Linux:**
```bash
# Podman 설치 (배포판별로 다름)
sudo apt install podman  # Ubuntu/Debian
sudo yum install podman  # CentOS/RHEL

# cloudflared 설치
curl -L https://pkg.cloudflare.com/cloudflared-release.gpg.key | sudo apt-key add -
sudo apt install cloudflared
```

### 2. 프로젝트 준비 확인

- [ ] `.env.docker` 파일 존재 또는 생성 예정
- [ ] `Dockerfile` 존재 ✅
- [ ] `docker-entrypoint.sh` 존재 ✅
- [ ] `run-docker.bat` (Windows) 또는 `run-docker.sh` (Mac/Linux) 존재 ✅

---

## 🚀 다음 단계

1. **위 "빠른 선택 가이드"에서 자신의 상황에 맞는 항목 선택**
2. 해당 폴더로 이동하여 가이드 문서 읽기
3. 단계별 진행
4. 완료 후 [배포 후 체크리스트](5_Referencehecklists/Post_Deployment_Checklist.md) 확인

---

## 📞 도움말

- 문제 발생? → [문제 해결 가이드](1_LocalPC_PodmanAQ_TROUBLESHOOTING.md)
- 전체 아키텍처 이해? → [시스템 구조](5_Referencerchitecture_Diagram.md)
- 배포 방식 상세 비교? → [비교표](5_Referenceeployment_Matrix.md)
- 환경 변수 설정? → [환경 변수 가이드](4_Configurationnvironment_Variables.md)

---

**마지막 업데이트**: 2026-06-19  
**버전**: 1.0


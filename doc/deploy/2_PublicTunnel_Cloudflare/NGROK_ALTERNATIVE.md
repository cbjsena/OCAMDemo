[🏠 홈](../README.md) > **Ngrok 대안**

# 🔗 Ngrok을 대신하는 방법들

**배경**: 일부 환경에서 Ngrok이 방화벽에 차단될 수 있습니다. 이 문서는 대안을 제시합니다.

---

## 📊 Ngrok vs 대안

| 특징 | Ngrok | Cloudflare Tunnel | SSH Tunne |
|------|----------|-------------------|-----------|
| **도메인 불필요** | ✅ | ✅ | ✅ |
| **설정 난이도** | ⭐⭐ | ⭐ | ⭐⭐⭐ |
| **방화벽 우회** | ❌ (차단됨) | ✅ | ✅ |
| **HTTPS** | ✅ | ✅ | 필요시 |
| **무료** | 제한적 | ✅ | ✅ |
| **성능** | ⭐⭐ | ⭐⭐⭐ | ⭐⭐ |
| **추천** | ❌ 비추천 | ✅ 권장 | △ 고급용 |

---

## ✅ 권장: Cloudflare Tunnel

**가장 간단하고 안정적인 대안**

- 도메인 없이도 가능 (Quick Tunnel)
- Ngrok보다 안정적
- 기업 환경에서도 일반적으로 허용
- 성능이 더 좋음

→ [Cloudflare Quick Tunnel Setup](QUICK_TUNNEL_SETUP.md)

---

## △ 고급: SSH Tunnel (VPS 필요)

**공개 서버(VPS)가 있는 경우만 사용**

### 개념

```
로컬 PC (localhost:8000)
    ↓ SSH Tunnel
공개 VPS (예: mydomain.com)
    ↓
외부 사용자 접속
```

### 설정 (Mac/Linux)

```bash
# SSH 터널 생성
ssh -R 80:localhost:8000 root@yourdomain.com

# 또는 포트 지정
ssh -R 8000:localhost:8000 root@yourdomain.com
```

### 설정 (Windows)

PuTTY 사용:
1. PuTTY 설치
2. Host: yourdomain.com
3. SSH → Tunnels → Remote ports do the same

### 단점

- VPS 비용
- SSH 키 관리 필요
- 네트워크 지연 가능성
- 문제 진단 어려움

---

## ❌ 비추천: Ngrok (방화벽 차단됨)

### 문제

```
에러:
403 Forbidden - Blocked by Corporate Firewall
또는 Ngrok 연결 끊김
```

### 이유

1. **보안 정책**: 회사/기관에서 Ngrok 트래픽 차단
2. **비용 정책**: free tier 도메인 생성 제한
3. **우회 서비스**: 우회 서비스로 간주

### 해결 → Cloudflare 사용

Cloudflare Tunnel이 더 많은 환경에서 작동합니다.

---

## 🔍 어떤 것을 선택해야 할까?

### 상황 1: 회사/기관 환경, 도메인 없음
```
→ Cloudflare Quick Tunnel
```

### 상황 2: 회사/기관 환경, 도메인 있음
```
→ Cloudflare Permanent Tunnel
```

### 상황 3: 개인 서버(VPS) 있음
```
→ SSH Tunnel (기술적 도전을 원할 때)
또는 Cloudflare Tunnel (더 간단)
```

### 상황 4: 인터넷 접속 완전 차단 환경
```
→ 배포 불가능
로컬 PC에서만 결과 확인 필요
```

---

## 📞 도움말

- Cloudflare 공식 문서: https://developers.cloudflare.com/cloudflare-one/
- SSH Tunneling 상세: https://www.ssh.com/ssh/tunneling/

---

**마지막 업데이트**: 2026-06-19


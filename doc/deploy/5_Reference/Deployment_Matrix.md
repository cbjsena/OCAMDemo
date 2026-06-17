[🏠 홈](../README.md) > **배포 방식 비교**

# 📊 배포 방식 상세 비교표

---

## 🎯 전체 비교

| 항목 | Podman Local | Quick Tunnel | Permanent Tunnel | Render (구식) |
|------|------|------|------|------|
| **도메인** | 불필요 | 불필요 | 필요 | 불필요 |
| **URL** | localhost:8000 | https://xxx.trycloudflare.com | https://domain.com | https://app.onrender.com |
| **URL 안정성** | 영구 | 임시 (매번 변함) | 영구 | 영구 |
| **외부 접속** | 아니오 | 예 | 예 | 예 |
| **도메인 비용** | 무료 | 무료 | $20+/년 | 무료 |
| **총 비용** | 무료 | 무료 | $20+/년 | 무료 |

---

## ⚡ 성능 비교

| 항목 | Podman Local | Quick Tunnel | Permanent Tunnel | Render |
|------|------|------|------|------|
| **메모리** | PC 전체 | PC 전체 | PC 전체 | 512MB ❌ |
| **CPU** | PC 전체 | PC 전체 | PC 전체 | 공유 |
| **알고리즘 연산** | ⭐⭐⭐ 가능 | ⭐⭐⭐ 가능 | ⭐⭐⭐ 가능 | ⭐ 불가능 |
| **동시 사용자** | ⭐⭐⭐ 많음 | ⭐⭐⭐ 많음 | ⭐⭐⭐ 많음 | ⭐ 1-2명 |
| **응답 속도** | ⭐⭐⭐ 빠름 | ⭐⭐ 보통 | ⭐⭐ 보통 | ⭐ 느림 |
| **Gurobi 지원** | ✅ 예 | ✅ 예 | ✅ 예 | ❌ 아니오 |

---

## 🔧 설정 난이도

| 항목 | Podman Local | Quick Tunnel | Permanent Tunnel | Render |
|------|------|------|------|------|
| **필요 도구** | Podman | Podman + cloudflared | Podman + cloudflared | GitHub |
| **설정 단계** | 1단계 | 2단계 | 5단계 | 3단계 |
| **설정 시간** | 5분 | 10분 | 30분 | 20분 |
| **난이도** | ⭐ 매우 쉬움 | ⭐⭐ 쉬움 | ⭐⭐⭐ 중간 | ⭐⭐ 쉬움 |
| **유지보수** | ⭐ 간단 | ⭐ 간단 | ⭐⭐ 중간 | ⭐⭐ 중간 |

---

## 🌐 네트워크/접근

| 항목 | Podman Local | Quick Tunnel | Permanent Tunnel | Render |
|------|------|------|------|------|
| **로컬 접속** | ✅ | ✅ | ✅ | ❌ |
| **같은 네트워크** | ✅ | ✅ | ✅ | ✅ |
| **인터넷 공개** | ❌ | ✅ | ✅ | ✅ |
| **고정 URL** | ✅ | ❌ | ✅ | ✅ |
| **방화벽 우회** | ❌ | ✅ | ✅ | ✅ |
| **회사 환경** | ✅ | ✅ | ✅ | ❌ (차단될 수 있음) |

---

## 💾 데이터 관리

| 항목 | Podman Local | Quick Tunnel | Permanent Tunnel | Render |
|------|------|------|------|------|
| **DB 유형** | SQLite | SQLite | SQLite | SQLite/PostgreSQL |
| **데이터 보존** | ✅ 영구 | ✅ 영구 | ✅ 영구 | ❌ 재배포 시 손실 |
| **자동 백업** | ❌ | ❌ | ❌ | ✅ |
| **백업 간격** | 수동 | 수동 | 수동 | 자동 |
| **데이터 안전성** | ⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐ |

---

## 🚀 운영 특성

| 항목 | Podman Local | Quick Tunnel | Permanent Tunnel | Render |
|------|------|------|------|------|
| **시작 시간** | 1-2분 | 1-2분 | 1-2분 | 30초 |
| **재시작 시간** | 30초 | 30초 | 30초 | 3-5분 |
| **가용성** | PC 켜있을 때만 | PC 켜있을 때만 | PC 켜있을 때만 | 24/7 |
| **자동 재시작** | ❌ | ❌ | ❌ | ✅ |
| **모니터링** | 수동 | 수동 | 수동 | ✅ Render 제공 |

---

## 🎓 추천 용도

### Podman Local ✅ 이상적

```
✅ 다음 경우에 권장:
- 개발/테스트 중
- 알고리즘 디버깅
- 정적 파일/UI 개발
- 임시 테스트
- Gurobi 등 고사양 연산 필요

❌ 피해야 할 경우:
- 24/7 서비스 필요
- 여러 사람이 동시 접속
- 외부 공개 필수
```

### Quick Tunnel ✅ 이상적

```
✅ 다음 경우에 권장:
- 데모/프로토타입
- 외부 사람에게 임시 공유
- 테스트 환경 검증
- 도메인 없을 때

❌ 피해야 할 경우:
- URL이 자주 바뀌어도 되지 않을 때
- 종종 컨테이너를 재시작할 때
```

### Permanent Tunnel ✅ 이상적

```
✅ 다음 경우에 권장:
- 정식 서비스
- 팀이 지속적으로 접속
- 고정 URL 필요
- 도메인 있을 때

❌ 피해야 할 경우:
- 도메인 구매 비용이 부담스러울 때
```

### Render ❌ 권장하지 않음

```
❌ 피해야 할 이유:
- 메모리 부족 (512MB)
- Gurobi 등 고사양 연산 불가능
- SQLite 데이터 손실 위험
- 종료 정책 (비활동 시 500 에러)

⚠️ 따라서 현재 프로젝트에는 적합하지 않음
```

---

## 📋 결정 도트

```
┌─ 개발/테스트 중?
│  └─ YES → Podman Local 사용
│           (파트 1 문서 참고)
│
└─ 외부에 공개해야 함?
   │
   ├─ 도메인 없음
   │  └─ Cloudflare Quick Tunnel
   │     (파트 2-1 문서 참고)
   │
   └─ 도메인 있음
      └─ Cloudflare Permanent Tunnel
         (파트 2-2 문서 참고)
```

---

## 🔍 빠른 선택 기준

### "지금 뭘 써야 할까?"

**질문 1**: 개발 지금 어느 단계?
- 개발 중 → **Podman Local**
- 거의 완성 → 아래로

**질문 2**: 외부 공개 필요?
- 아니오 → **Podman Local**
- 예 → 아래로

**질문 3**: 도메인 있음?
- 아니오 → **Cloudflare Quick Tunnel**
- 예 → **Cloudflare Permanent Tunnel**

---

## 📚 각 방식의 상세 문서

1. **Podman Local**
   - [빠른 시작](../1_LocalPC_Podman/QUICK_START.md)
   - [상세 가이드](../1_LocalPC_Podman/DETAILED_GUIDE.md)

2. **Cloudflare Quick Tunnel**
   - [설정 가이드](../2_PublicTunnel_Cloudflare/QUICK_TUNNEL_SETUP.md)

3. **Cloudflare Permanent Tunnel**
   - [설정 가이드](../2_PublicTunnel_Cloudflare/PERMANENT_TUNNEL_SETUP.md)

4. **Render (참고용)**
   - [폐기 사유](../3_CloudPaaS_Render/DEPRECATED.md)

---

**마지막 업데이트**: 2026-06-19


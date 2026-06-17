[🏠 홈](../README.md) > **Render PaaS**

# 🔴 Render PaaS 배포 (권장하지 않음)

**상태**: ❌ 폐기 - 메모리 부족 문제

---

## 📊 문제점

### 메모리 부족

```
Render 무료 티어: 512MB
요구사항: 최적화 연산(Gurobi) 실행 시 2GB+

결과: OOM (Out of Memory) 에러 발생
```

### 성능 이슈

```
- Gunicorn 워커 제한 (메모리 부족)
- 긴 작업 타임아웃
- 동시 사용자 처리 불가
```

### 데이터 손실 위험

```
Render 인스턴스 재배포 시:
   ↓
SQLite 데이터 초기화 (Ephemeral Storage)
   ↓
기존 데이터 손실
```

### Celery 제약

```
Render CELERY_TASK_ALWAYS_EAGER=True:
   ↓
비동기 큐잉 불가능
   ↓
긴 작업 시 응답 지연
```

---

## ✅ 권장 대안

| 요구사항 | 권장 방식 |
|---------|---------|
| **개발/테스트** | Podman Local |
| **임시 외부 공개** | Cloudflare Quick Tunnel |
| **정식 운영** | Cloudflare Permanent Tunnel |
| **고사양 클라우드** | AWS/Azure (고비용) |

---

## 📚 관련 문서

- [Podman Local](../1_LocalPC_Podman/QUICK_START.md)
- [Cloudflare Quick Tunnel](./QUICK_TUNNEL_SETUP.md)
- [Cloudflare Permanent Tunnel](./PERMANENT_TUNNEL_SETUP.md)

---

**마지막 업데이트**: 2026-06-19  
**폐기 사유**: 메모리 부족 및 데이터 손실 위험


[🏠 홈](../README.md) > **배포 전 체크리스트**

# ✅ 배포 전 점검 (Pre-Deployment Checklist)

배포 전에 다음 항목들을 모두 확인하세요.

---

## 🖥️ 시스템 환경 점검

### 필수 도구 설치

- [ ] Podman 설치됨
  ```bash
  podman --version  # 4.0+ 권장
  ```

- [ ] Python 3.11+ 설치됨 (개발 시)
  ```bash
  python --version
  ```

- [ ] cloudflared 설치됨 (외부 공개 시)
  ```bash
  cloudflared --version
  ```

### 리소스 확인

- [ ] 디스크 공간 충분함 (최소 5GB)
  ```bash
  df -h  # Mac/Linux
  dir    # Windows
  ```

- [ ] 메모리 충분함 (권장 4GB+)
  ```bash
  # Windows: Task Manager
  # Mac: Activity Monitor
  # Linux: free -h
  ```

- [ ] 포트 8000 사용 중 아님
  ```bash
  # Windows
  netstat -ano | findstr 8000
  # Mac/Linux
  lsof -i :8000
  ```

---

## 📂 프로젝트 파일 확인

### 필수 파일

- [ ] Dockerfile 존재
  ```bash
  ls -la Dockerfile
  ```

- [ ] docker-entrypoint.sh 존재
  ```bash
  ls -la docker-entrypoint.sh
  ```

- [ ] run-docker.bat 또는 run-docker.sh 존재
  ```bash
  ls -la run-docker.*
  ```

- [ ] .dockerignore 존재
  ```bash
  ls -la .dockerignore
  ```

### 설정 파일

- [ ] .env.docker 파일 존재
  ```bash
  ls -la .env.docker
  ```

- [ ] .env.docker 내용 확인
  ```bash
  cat .env.docker  # 민감 정보 주의
  ```

- [ ] requirements.txt 최신
  ```bash
  pip freeze > requirements.txt  # (선택사항)
  ```

---

## 🔧 Django 설정 확인

### settings.py

- [ ] APP_ENV 변수 설정됨
  ```python
  APP_ENV = os.environ.get("APP_ENV", "local")
  ```

- [ ] DEBUG=False 배포용으로 설정 계획됨
  ```python
  DEBUG = os.environ.get("DEBUG", "True") == "True"
  ```

- [ ] SECRET_KEY 설정됨
  ```python
  SECRET_KEY = os.environ.get("SECRET_KEY", "your-key-here")
  ```

- [ ] ALLOWED_HOSTS 설정됨
  ```python
  ALLOWED_HOSTS = [...]  # 또는
  ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "").split(",")
  ```

- [ ] CSRF_TRUSTED_ORIGINS 설정됨 (Django 4.0+)
  ```python
  CSRF_TRUSTED_ORIGINS = [...]
  ```

- [ ] WhiteNoise Middleware 추가됨
  ```python
  MIDDLEWARE = [
      "whitenoise.middleware.WhiteNoiseMiddleware",
      ...
  ]
  ```

### 마이그레이션

- [ ] 모든 마이그레이션 파일 생성됨
  ```bash
  python manage.py makemigrations
  ```

- [ ] 마이그레이션 확인됨
  ```bash
  python manage.py showmigrations
  ```

---

## 📚 데이터/로그 준비

### 폴더 구조

- [ ] outputs/ 폴더 존재 또는 생성 예정
  ```bash
  mkdir -p outputs logs instances media
  ```

- [ ] media/ 폴더 존재
  
- [ ] logs/ 폴더 존재
  
- [ ] instances/ 폴더 존재

### 데이터 보존

- [ ] db.sqlite3 데이터베이스 확인
  ```bash
  ls -la db.sqlite3
  ```

- [ ] 중요 데이터 백업 계획됨
  ```bash
  cp db.sqlite3 db.sqlite3.backup
  ```

---

## 🔐 보안 점검

### 환경 변수

- [ ] SECRET_KEY 강력한 임의 값으로 설정됨
  ```bash
  # 생성:
  python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'
  ```

- [ ] DEBUG=False 배포 환경용으로 설정됨

- [ ] .env 파일들이 .gitignore에 있음
  ```bash
  cat .gitignore | grep .env
  ```

### 권한/액세스

- [ ] 민감 파일 권한 설정됨
  ```bash
  chmod 600 .env.docker  # 소유자만 읽기/쓰기
  ```

- [ ] db.sqlite3 권한 확인됨
  ```bash
  chmod 600 db.sqlite3
  ```

---

## 🌐 배포 방식 선택

### Podman Local 선택

- [ ] 로컬 PC에서만 실행할 것임
- [ ] 외부 공개 불필요함

  **다음 단계**: [Podman Local QUICK_START](../1_LocalPC_Podman/QUICK_START.md)

### Cloudflare Quick Tunnel 선택

- [ ] 외부 공개 필요함
- [ ] 도메인 없음 또는 임시 URL 필요함
- [ ] cloudflared 설치됨
  ```bash
  cloudflared --version
  ```

  **다음 단계**: [Quick Tunnel Setup](../2_PublicTunnel_Cloudflare/QUICK_TUNNEL_SETUP.md)

### Cloudflare Permanent Tunnel 선택

- [ ] 도메인 소유 중임
- [ ] Cloudflare 계정 있음
- [ ] cloudflared 설치됨
  ```bash
  cloudflared --version
  ```

  **다음 단계**: [Permanent Tunnel Setup](../2_PublicTunnel_Cloudflare/PERMANENT_TUNNEL_SETUP.md)

---

## 📋 테스트 및 검증

### 로컬 테스트

- [ ] Podman 테스트 실행
  ```bash
  podman --help
  ```

- [ ] Docker 이미지 수동 빌드 테스트 (선택사항)
  ```bash
  podman build -t ocamdemo:test .
  ```

- [ ] 포트 8000 접근 가능한지 테스트
  ```bash
  curl http://localhost:8000
  ```

### Django 테스트

- [ ] Django 서버 로컬 실행 테스트
  ```bash
  python manage.py runserver
  ```

- [ ] collectstatic 수행 테스트
  ```bash
  python manage.py collectstatic --noinput --dry-run
  ```

- [ ] migrate 테스트
  ```bash
  python manage.py migrate --plan
  ```

### .env 파일 유효성

- [ ] SECRET_KEY 설정됨 (빈 값 아님)
- [ ] ALLOWED_HOSTS 설정됨
- [ ] DEBUG 명시적으로 설정됨

---

## 📝 문서화

- [ ] 배포 메모 작성됨
  ```
  예:
  - 사용한 배포 방식: Podman Local
  - 마이그레이션 대상: Users 테이블 추가
  - 알려진 문제: 없음
  ```

- [ ] 팀원에게 공지함 (필요시)

- [ ] 롤백 계획 수립 (필수):
  ```
  긴급 롤백:
  1. 컨테이너 중지: podman stop ocamdemo
  2. 이전 이미지 사용: podman rm ocamdemo
  3. 이전 .env 복원
  4. 재시작
  ```

---

## 🎯 최종 확인

### 출발 전 최종 체크

```
___  준비 완료!
|✓| 도구 설치 완료
|✓| 파일 확인 완료
|✓| Django 설정 확인 완료
|✓| 보안 설정 확인 완료
|✓| 배포 방식 선택 완료
|✓| 문서화 완료
```

---

## 🚀 다음 단계

배포 방식에 따라 다음 문서로 이동:

1. **Podman Local**
   → [QUICK_START](../1_LocalPC_Podman/QUICK_START.md)

2. **Cloudflare Quick Tunnel**
   → [QUICK_TUNNEL_SETUP](../2_PublicTunnel_Cloudflare/QUICK_TUNNEL_SETUP.md)

3. **Cloudflare Permanent Tunnel**
   → [PERMANENT_TUNNEL_SETUP](../2_PublicTunnel_Cloudflare/PERMANENT_TUNNEL_SETUP.md)

---

## ⚠️ 문제 발생 시

- [FAQ & 문제 해결](../1_LocalPC_Podman/FAQ_TROUBLESHOOTING.md) 참고

---

**마지막 업데이트**: 2026-06-19


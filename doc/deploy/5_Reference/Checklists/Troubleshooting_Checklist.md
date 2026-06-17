[🏠 홈](../README.md) > **문제 해결 체크리스트**

# 🆘 배포 문제 해결 체크리스트

배포 후 문제 발생 시 단계별로 진행하세요.

---

## 🔴 긴급 상황 (서비스 중단)

### 상황: 웹사이트 완전 접속 불가

#### Step 1: 컨테이너 상태 확인
```bash
# 컨테이너 실행 여부
podman ps | grep ocamdemo

# 컨테이너 상태 상세
podman inspect ocamdemo | grep Status
```

#### Step 2: 로그 확인
```bash
# 마지막 100줄 확인
podman logs --tail 100 ocamdemo

# 에러 필터링
podman logs ocamdemo | grep ERROR
```

#### Step 3: 즉시 대응

- [ ] 컨테이너 재시작
  ```bash
  podman restart ocamdemo
  ```

- [ ] 여전히 안 되면 컨테이너 재생성
  ```bash
  ./run-docker.bat  # 또는 .sh
  ```

- [ ] 5분 후 재접속 시도

#### Step 4: 여전히 안 됨 - 롤백

```bash
# 1. 현재 문제 기록
podman logs ocamdemo > error_log.txt

# 2. 이전 .env 복원 (백업이 있으면)
cp .env.docker.backup .env.docker

# 3. 컨테이너 재기동
podman stop ocamdemo
podman rm ocamdemo
./run-docker.bat

# 4. 다시 접속 시도
```

#### Step 5: 전문가 도움

- 로그 파일 저장
- [FAQ 가이드](../1_LocalPC_Podman/FAQ_TROUBLESHOOTING.md) 확인

---

## 🟡 경고 상황 (부분 장애)

### 상황: 로그인은 되지만 일부 기능 오류

#### 진단
```bash
# 에러 로그 확인
podman logs ocamdemo | grep -i error | tail -20

# 마이그레이션 상태 확인
podman exec ocamdemo python manage.py showmigrations

# 데이터베이스 무결성 확인
podman exec ocamdemo python manage.py check
```

#### 해결
- [ ] 마이그레이션 재실행
  ```bash
  podman exec ocamdemo python manage.py migrate
  ```

- [ ] 정적 파일 재수집
  ```bash
  podman exec ocamdemo python manage.py collectstatic --noinput
  ```

- [ ] 컨테이너 재시작
  ```bash
  podman restart ocamdemo
  ```

---

## 🔵 주의 상황 (경미한 문제)

### 상황 1: 페이지 로드는 되지만 스타일 없음

#### 진단
```bash
# 정적 파일 디렉토리 확인
podman exec ocamdemo ls -la /app/staticfiles/

# 개발자도구에서 정적 파일 404 확인
# F12 → Network 탭 → CSS/JS 파일 404 에러
```

#### 해결
```bash
# 정적 파일 재수집
podman exec ocamdemo python manage.py collectstatic --clear --noinput

# 컨테이너 재시작
podman restart ocamdemo

# 브라우저 캐시 삭제
Ctrl+Shift+Del (브라우저)
```

### 상황 2: CSRF 에러 (403)

#### 진단
```bash
# CSRF_TRUSTED_ORIGINS 확인
grep CSRF_TRUSTED_ORIGINS config/settings.py

# .env.docker 확인
cat .env.docker
```

#### 해결
```
1. settings.py 확인
   CSRF_TRUSTED_ORIGINS에 현재 도메인 추가

2. .env.docker 확인
   ALLOWED_HOSTS에 도메인 포함

3. 컨테이너 재시작
   podman restart ocamdemo
```

### 상황 3: 데이터 조회는 되는데 저장 안 됨

#### 진단
```bash
# 데이터베이스 연결 확인
podman exec ocamdemo python manage.py dbshell

# 쿼리 테스트
sqlite> SELECT COUNT(*) FROM auth_user;

# 마이그레이션 상태 확인
podman exec ocamdemo python manage.py showmigrations
```

#### 해결
```bash
# 1. 마이그레이션 확인
podman exec ocamdemo python manage.py migrate --check

# 2. 마이그레이션 실행
podman exec ocamdemo python manage.py migrate

# 3. 데이터베이스 무결성 확인
podman exec ocamdemo python manage.py check
```

---

## 📋 일반적인 문제별 체크리스트

### 문제: "localhost:8000에 연결할 수 없음"

```
__  진단 단계
[1] 컨테이너 실행 확인
    podman ps | grep ocamdemo
    
[2] 포트 사용 확인
    netstat -ano | findstr 8000  (Windows)
    lsof -i :8000                (Mac/Linux)
    
[3] 로그 확인
    podman logs ocamdemo
    
[4] 컨테이너 재시작
    ./run-docker.bat
    
[5] 여전히 안 되면
    → [FAQ 가이드](../1_LocalPC_Podman/FAQ_TROUBLESHOOTING.md)
```

### 문제: "Invalid HTTP_HOST header"

```
__  진단 단계
[1] .env.docker 확인
    ALLOWED_HOSTS=* 인지 확인
    
[2] settings.py 확인
    ALLOWED_HOSTS 확인
    
[3] 컨테이너 재시작
    ./run-docker.bat
    
[4] 브라우저 캐시 삭제
    Ctrl+Shift+Del
```

### 문제: "컨테이너가 자꾸 꺼짐"

```
__  진단 단계
[1] 로그 확인
    podman logs ocamdemo | tail -50
    
[2] 에러 타입 확인
    - Timeout: GUNICORN_TIMEOUT 증가
    - OOM: 메모리 부족 → 워커 수 감소
    - Segfault: 워커 수 감소
    
[3] 설정 수정
    docker-entrypoint.sh 수정:
    TIMEOUT=300  (또는 WORKERS=2)
    
[4] 이미지 재빌드
    Y 입력하여 run-docker.bat 실행
```

### 문제: "메모리 부족"

```
__  진단 단계
[1] 현재 사용량 확인
    podman stats ocamdemo
    
[2] PC 메모리 상태 확인
    Task Manager (Windows)
    Activity Monitor (Mac)
    free -h (Linux)
    
[3] 워커 수 감소
    docker-entrypoint.sh:
    WORKERS=2  (기본값 4)
    
[4] 이미지 재빌드 후 재시작
    Y 입력하여 run-docker.bat
```

---

## 🔍 심화 진단

### 전체 시스템 상태 진단

```bash
echo "=== Podman 상태 ===" && \
podman ps -a && \
echo "" && \
echo "=== 컨테이너 리소스 ===" && \
podman stats --no-stream ocamdemo && \
echo "" && \
echo "=== 포트 사용 ===" && \
netstat -ano | findstr 8000 && \
echo "" && \
echo "=== 디스크 사용 ===" && \
podman system df
```

### 컨테이너 내부 진단

```bash
# 컨테이너 내부 셸 접속
podman exec -it ocamdemo bash

# 내부에서 확인할 사항:
ls -la /app/
python --version
pip list | grep Django
python manage.py check
```

### 네트워크 진단 (Mac/Linux)

```bash
# DNS 확인 (Tunnel 사용 시)
nslookup ocamdemo.example.com

# Tunnel 연결 상태 확인
cloudflared tunnel list

# 인터페이스 확인
ifconfig
```

---

## 📞 도움말 리소스

### 내부 문서
- [FAQ & 문제 해결](../1_LocalPC_Podman/FAQ_TROUBLESHOOTING.md)
- [상세 가이드](../1_LocalPC_Podman/DETAILED_GUIDE.md)

### 외부 리소스
- Django 공식: https://docs.djangoproject.com
- Podman 문서: https://docs.podman.io
- Cloudflare 지원: https://support.cloudflare.com

---

## 📝 기록

### 문제 발생 기록

```
날짜: ________________
문제: ________________
증상: ________________
원인: ________________
해결책: ________________
소요 시간: ________________
```

---

**마지막 업데이트**: 2026-06-19


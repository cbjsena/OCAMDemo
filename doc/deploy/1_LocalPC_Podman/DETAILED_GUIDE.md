[🏠 홈](../README.md) > [Podman Local](QUICK_START.md) > **상세 가이드**

# 📚 Podman 로컬 배포 상세 가이드

## 📋 목차
1. [시스템 요구사항](#시스템-요구사항)
2. [설치 및 준비](#설치-및-준비)
3. [실행 프로세스 상세 설명](#실행-프로세스-상세-설명)
4. [데이터 관리](#데이터-관리)
5. [성능 최적화](#성능-최적화)
6. [고급 옵션](#고급-옵션)

---

## 🖥️ 시스템 요구사항

### 하드웨어 요구사항
| 항목 | 최소 사양 | 권장 사양 |
|------|----------|----------|
| CPU | 2 cores | 4+ cores |
| RAM | 4GB | 8GB+ |
| Disk | 5GB (OS+런타임) | 20GB+ (결과 저장용) |
| OS | Windows 10/11, macOS 11+, Ubuntu 18+ | 최신 버전 권장 |

### 필수 소프트웨어
- **Podman** 4.0+
- **Python** 3.11+ (개발 시에만, 컨테이너 실행 시 불필요)
- **.env.docker** 파일

---

## 🔧 설치 및 준비

### 1. Podman 설치 확인

```bash
# 설치 여부 확인
podman --version

# 미설치 시 설치
# Windows: winget install podman
# Mac:  brew install podman
# Linux (Ubuntu/Debian): sudo apt-get install podman
# Linux (CentOS/RHEL): sudo yum install podman


### 2. 프로젝트 폴더 구조 확인
```
OCAMDemo/
├── Dockerfile ✅
├── docker-entrypoint.sh ✅
├── run-docker.bat (Windows) 또는
├── run-docker.sh (Mac/Linux) ✅
├── .env.docker (필수 - 없으면 생성)
└── requirements.txt ✅
```

### 3. .env.docker 파일 확인/생성

프로젝트 루트에 .env.docker 파일, 없다면 4_Configuration > Environment_Variables.md 참고

```env example
DEBUG=False
SECRET_KEY=your-very-secure-random-key-here
APP_ENV=docker
ALLOWED_HOSTS=*,localhost,127.0.0.1
DATABASE_URL=sqlite:///db.sqlite3
```

---

## 🚀 실행 프로세스 상세 설명

### 전체 플로우

```
1. 필요한 폴더 생성
   ↓
2. Docker 이미지 빌드 여부 결정
   ├─ Y: Dockerfile 실행 (3-5분)
   └─ N: 기존 이미지 사용 (5초)
   ↓
3. 기존 컨테이너 정리
   ↓
4. 새 컨테이너 생성 및 시작
   ├─ 마이그레이션 실행
   ├─ 정적 파일 수집
   └─ Gunicorn 서버 시작
   ↓
5. 로그 출력 및 서비스 시작
```

### 각 단계 상세

#### Step 1: 필요한 폴더 생성

```bash
# 자동 생성되는 폴더
outputs/     # 알고리즘 최적화 결과 저장
logs/        # 애플리케이션 로그
instances/   # 테스트 데이터
media/       # 사용자 업로드 파일
```

**역할:**
- **outputs/** : 최적화 알고리즘 실행 결과 저장
- **logs/** : Django, Gunicorn, Celery 로그 저장
- **instances/** : 시뮬레이션 입력 데이터
- **media/** : 웹에서 업로드하는 파일

#### Step 2: Docker 이미지 빌드

이미지 빌드 시 수행되는 작업:

```dockerfile
# 1. Python 3.11 기반 이미지 가져오기
FROM python:3.11-slim

# 2. 시스템 패키지 설치 (gcc, build-essential 등)
RUN apt-get update && apt-get install -y build-essential

# 3. 프로젝트 코드 복사
COPY . /app

# 4. Python 의존성 설치
RUN pip install -r requirements.txt

# 5. 정적 파일 수집 (CSS, JS, images)
RUN python manage.py collectstatic --noinput

# 6. 포트 8000 노출
EXPOSE 8000
```

**언제 빌드해야 하나?**
- ✅ 다음 상황에 `Y` 입력:
  - requirements.txt 변경
  - Django 코드 수정
  - settings.py 변경
  - 템플릿/정적 파일 추가
  
- ❌ 다음 상황에 `N` 입력:
  - 데이터만 변경
  - 로직 재실행만 원함
  - 빠른 재시작 필요

#### Step 3: 기존 컨테이너 정리

```bash
# run-docker 스크립트가 자동으로 실행
podman stop ocamdemo    # 실행 중인 컨테이너 정지
podman rm ocamdemo      # 컨테이너 삭제 (데이터는 안전)
```

**안전한 이유:**
- Volumes로 마운트된 폴더 (outputs, logs 등)는 PC에 남음
- 컨테이너만 삭제, 데이터는 보존

#### Step 4: 컨테이너 생성 및 시작

```bash
podman run -d \
  --name ocamdemo \
  -p 8000:8000 \                    # 포트 매핑
  -v outputs:/app/outputs \         # 결과 폴더 마운트
  -v logs:/app/logs \               # 로그 폴더 마운트
  -v instances:/app/instances \     # 데이터 폴더 마운트
  -v media:/app/media \             # 업로드 폴더 마운트
  -v db.sqlite3:/app/db.sqlite3 \   # 데이터베이스 마운트
  --env-file .env.docker \          # 환경 변수 주입
  ocamdemo:latest
```

**각 옵션의 의미:**
- `-d` : 백그라운드 실행
- `--name` : 컨테이너 이름 지정
- `-p 8000:8000` : 호스트 포트 ↔ 컨테이너 포트 연결
- `-v` : 호스트 폴더 ↔ 컨테이너 폴더 마운트 (영구 보존)
- `--env-file` : 환경 변수 파일 로드

**마운트된 폴더의 첫 번째 실행:**

```bash
# docker-entrypoint.sh에서 자동으로 실행
python manage.py migrate --noinput      # DB 마이그레이션
python manage.py collectstatic          # 정적 파일 수집
```

#### Step 5: Gunicorn 서버 시작

```bash
gunicorn config.wsgi:application \
  --bind 0.0.0.0:8000 \       # 모든 인터페이스에서 수신
  --workers 4 \               # 프로세스 워커 수
  --timeout 120 \             # 요청 타임아웃 (초)
  --access-logfile - \        # 접근 로그 출력
  --error-logfile -           # 에러 로그 출력
```

---

## 💾 데이터 관리

### 데이터 보존 (컨테이너 재시작 후)

컨테이너가 재시작되거나 삭제되어도 다음 폴더의 데이터는 보존됩니다:

```
PC 하드디스크에 저장됨 (Volumes)
├── outputs/       ← 알고리즘 결과
├── logs/          ← 실행 로그
├── instances/     ← 테스트 데이터
├── media/         ← 업로드 파일
└── db.sqlite3     ← 데이터베이스
```

### 데이터 백업

```bash
# 중요한 폴더만 백업
cp -r outputs/ backup_outputs/
cp -r media/ backup_media/
cp db.sqlite3 backup_db.sqlite3

# 또는 전체 백업 (압축)
tar -czf backup_$(date +%Y%m%d).tar.gz outputs/ media/ logs/ db.sqlite3
```

### 데이터 초기화

```bash
# 경고: 이 명령어는 데이터를 삭제합니다!

# 개별 삭제
rm -rf outputs/*
rm -rf logs/*
rm db.sqlite3

# 컨테이너 내부 데이터만 초기화 (폴더는 유지)
podman exec ocamdemo python manage.py migrate --run-syncdb
```

### 로그 파일 위치

```bash
# 실시간 로그 보기
podman logs -f ocamdemo

# 마지막 100줄만 보기
podman logs --tail 100 ocamdemo

# 로그 파일로 저장된 기록
cat logs/gunicorn.log
```

---

## ⚡ 성능 최적화

### 워커 프로세스 조정

`docker-entrypoint.sh` 수정:

```bash
# 기본값: 4개
WORKERS=${GUNICORN_WORKERS:-4}

# 최적값:
# CPU 코어 수 * 2 + 1
# 예: 4 코어 → 9개
```

```bash
# 컨테이너 시작 시 환경 변수로 지정
podman run -e GUNICORN_WORKERS=8 ...
```

### 타임아웃 조정

```bash
# docker-entrypoint.sh
TIMEOUT=${GUNICORN_TIMEOUT:-120}

# 긴 연산이 필요하면 시간 증가
# 예: 300초 (5분)
# 컨테이너 시작 시:
podman run -e GUNICORN_TIMEOUT=300 ...
```

### 메모리 제한 설정

```bash
# 컨테이너가 사용할 수 있는 메모리 제한
podman run \
  --memory 4g \           # 최대 4GB
  --memory-swap 6g \      # 스왑 포함 최대 6GB
  ocamdemo:latest
```

---

## 🔧 고급 옵션

### 컨테이너 자동 재시작

```bash
podman run \
  --restart always \   # Docker 재시작 시 자동 실행
  ocamdemo:latest
```

### 컨테이너 정보 확인

```bash
# 실행 중인 컨테이너 목록
podman ps

# 모든 컨테이너 (정지 포함)
podman ps -a

# 컨테이너 상세 정보
podman inspect ocamdemo

# 컨테이너 리소스 사용량
podman stats ocamdemo
```

### 컨테이너 내부 터미널 접속

```bash
# 컨테이너 내부 bash 터미널 접속
podman exec -it ocamdemo bash

# 명령어 실행 후 종료
podman exec ocamdemo python manage.py createsuperuser
```

### 컨테이너 이미지 관리

```bash
# 이미지 목록
podman images

# 이미지 상세 정보
podman inspect ocamdemo:latest

# 미사용 이미지 삭제
podman image prune

# 캐시 완전 삭제 후 빌드
podman build --no-cache -t ocamdemo:latest .
```

---

## 📖 관련 문서

- [FAQ & 문제 해결](FAQ_TROUBLESHOOTING.md)
- [Docker 설정](../4_Configuration/Docker_Config.md)
- [환경 변수](../4_Configuration/Environment_Variables.md)
- [외부 공개 - Cloudflare Tunnel](../2_PublicTunnel_Cloudflare/QUICK_TUNNEL_SETUP.md)

---

**마지막 업데이트**: 2026-06-19


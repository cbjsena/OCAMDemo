[🏠 홈](../README.md) > [Podman Local](QUICK_START.md) > **문제 해결**

# 🆘 FAQ & 문제 해결 가이드

## 📋 목차
1. [설치 관련](#설치-관련)
2. [실행 관련](#실행-관련)
3. [웹 접속 관련](#웹-접속-관련)
4. [데이터/로그 관련](#데이터로그-관련)
5. [성능 관련](#성능-관련)
6. [기타](#기타)

---

## 🔧 설치 관련

### Q1: Podman이 설치되지 않았어요
```bash
# 에러 메시지
'podman' is not recognized as an internal or external command

# 해결 방법
# Windows: 관리자 터미널에서
winget install podman

# 또는 직접 다운로드
https://github.com/containers/podman/releases

# 설치 확인
podman --version
```

### Q2: podman 버전이 너무 낡아요
```bash
# 현재 버전 확인
podman --version

# 최신 버전으로 업데이트
# Windows
winget upgrade podman

# Mac
brew upgrade podman

# Linux
sudo apt-get upgrade podman
```

### Q3: .env.docker 파일을 찾을 수 없어요
```
에러:
ERROR: Can't find .env.docker file in project root

해결:
1. 프로젝트 루트 폴더 확인 (OCAMDemo/)
2. 파일이 없으면 생성:
   
   DEBUG=False
   SECRET_KEY=django-insecure-your-secret-key
   APP_ENV=docker
   ALLOWED_HOSTS=*,localhost,127.0.0.1
   
3. 파일명이 정확한지 확인 (.env.docker, .env.local 아님)
```

---

## 🚀 실행 관련

### Q4: 스크립트 실행이 안 되어요 (Windows)
```
에러:
"run-docker.bat is not recognized"

해결:
1. 프로젝트 폴더에서 실행하는지 확인
   cd D:\dev\django\OCAMDemo
   
2. 파일이 있는지 확인
   dir run-docker.bat
   
3. 다시 실행
   run-docker.bat
```

### Q5: 스크립트 실행이 안 되어요 (Mac/Linux)
```bash
에러:
bash: ./run-docker.sh: Permission denied

해결:
chmod +x run-docker.sh    # 실행 권한 부여
./run-docker.sh           # 다시 실행
```

### Q6: 이미지 빌드가 실패해요
```
에러:
ERROR: failed to build image

원인 분석:
1. requirements.txt 문제
   → python 패키지 충돌 또는 설치 불가능
   
2. Dockerfile 문제
   → Python 설치 실패, 네트워크 문제
   
해결:
1. 에러 메시지 끝부분 보기 (실제 문제 표시됨)
2. 필요시 --no-cache로 캐시 삭제
   podman build --no-cache -t ocamdemo:latest .
3. 네트워크 연결 확인 (시간 경과 후 재시도)
```

### Q7: 컨테이너 시작이 실패해요
```
에러:
ERROR: failed to start container

진단:
podman ps -a            # 컨테이너 상태 확인
podman logs ocamdemo    # 에러 로그 보기

일반적 원인:
1. 포트 8000이 이미 사용 중
   → 해결: 다른 프로그램 종료 또는 포트 변경
   
2. 마운트 폴더 권한 문제
   → 해결: 폴더 권한 확인 (chmod 755)
   
3. 환경 변수 문제
   → 해결: .env.docker 파일 확인
```

### Q8: 컨테이너가 자꾸 꺼져요
```
진단:
podman logs ocamdemo           # 로그 확인
podman inspect ocamdemo        # 상태 확인

원인별 해결:

1. 타임아웃으로 인한 종료
   에러: 504 Gateway Timeout
   해결:
   docker-entrypoint.sh에서 TIMEOUT 증가
   TIMEOUT=${GUNICORN_TIMEOUT:-300}

2. 메모리 부족
   진단: podman stats 에서 메모리 100%
   해결: 
   podman run --memory 4g ...
   또는 PC 메모리 여유 확보

3. Gunicorn 크래시
   에러: Segmentation fault
   해결: 워커 수 감소
   WORKERS=${GUNICORN_WORKERS:-2}
```

---

## 🌐 웹 접속 관련

### Q9: localhost:8000에 접속할 수 없어요
```
에러:
localhost:8000 에 접속할 수 없음
또는 ERR_CONNECTION_REFUSED

진단 순서:
1. 컨테이너가 실행 중인가?
   podman ps | grep ocamdemo
   
2. 포트가 열려있는가?
   # Windows
   netstat -ano | findstr 8000
   
   # Mac/Linux
   lsof -i :8000

해결 방법:
1. 컨테이너 확인
   podman ps -a
   
2. 로그 확인
   podman logs ocamdemo
   
3. 컨테이너 재시작
   podman stop ocamdemo
   podman rm ocamdemo
   ./run-docker.bat (또는 .sh)
```

### Q10: 포트 8000이 이미 사용 중이어요
```
진단:
netstat -ano | findstr 8000  # Windows
lsof -i :8000               # Mac/Linux

해결 방법 1: 기존 프로그램 종료
1. 다른 포트를 사용 중인 프로그램 찾아 종료
2. 예: 다른 Django 서버, 다른 Podman 컨테이너 등

해결 방법 2: 다른 포트 사용
run-docker.bat 또는 run-docker.sh 수정:
podman run -p 8001:8000 ...  # 호스트 8001 → 컨테이너 8000
웹 접속: http://localhost:8001

해결 방법 3: 강제 종료 (Windows)
taskkill /PID <PID> /F
```

### Q11: 로그인 페이지가 안 열려요
```
에러:
Invalid HTTP_HOST header

원인:
ALLOWED_HOSTS 설정이 잘못됨

해결:
1. .env.docker 확인
   ALLOWED_HOSTS=*,localhost,127.0.0.1
   
2. Django 재시작
   podman stop ocamdemo
   podman rm ocamdemo
   ./run-docker.bat

3. 캐시 삭제
   로컬 브라우저 캐시 지우기 (Ctrl+Shift+Del)
```

### Q12: CSS, JS 파일이 로드되지 않아요
```
증상:
웹페이지가 열리지만 스타일이 없음 (흰색 텍스트만)
또는 브라우저 개발자도구에서 404 에러

해결:
1. 이미지 재빌드로 정적 파일 다시 수집
   Y 입력하여 run-docker.bat 실행
   
2. 수동으로 수집
   podman exec ocamdemo python manage.py collectstatic
   
3. 로그 확인
   podman logs ocamdemo | grep static
```

---

## 💾 데이터/로그 관련

### Q13: outputs 폴더에 결과가 안 저장되어요
```
진단:
1. 폴더 존재 확인
   ls -la outputs/
   
2. 컨테이너 내부 폴더 확인
   podman exec ocamdemo ls -la /app/outputs
   
3. 마운트 여부 확인
   podman inspect ocamdemo | grep outputs

해결:
1. 폴더가 없으면 생성
   mkdir outputs
   chmod 775 outputs
   
2. 마운트 다시 확인
   컨테이너 재시작:
   ./run-docker.bat
```

### Q14: 로그 파일을 찾을 수 없어요
```
로그 위치:
1. 실시간 로그 (터미널 실행 중)
   podman logs -f ocamdemo
   
2. 저장된 로그 파일
   logs/ 폴더 안의 파일들
   
3. 컨테이너 내부에서 직접 보기
   podman exec ocamdemo cat /app/logs/gunicorn.log

로그 전체 기록:
1. 파일로 저장
   podman logs ocamdemo > all_logs.txt
   
2. 특정 갯수만 출력
   podman logs --tail 200 ocamdemo

문제 진단:
1. 에러 로그만 필터링
   podman logs ocamdemo | grep ERROR
   
2. 특정 시간대 로그 보기
   podman logs --since 10m ocamdemo  # 최근 10분
```

### Q15: 데이터베이스 초기화하고 싶어요
```
경고: 이 명령어는 데이터를 삭제합니다!

1단계: 데이터베이스 백업 (선택사항)
cp db.sqlite3 db.sqlite3.backup

2단계: 데이터베이스 초기화
rm db.sqlite3

3단계: 컨테이너 재시작 (마이그레이션 자동 실행)
./run-docker.bat

4단계: 관리자 계정 생성
podman exec -it ocamdemo python manage.py createsuperuser
```

---

## ⚡ 성능 관련

### Q16: 저는 느려요 (응답이 느림)
```
진단:
1. CPU 사용률 확인
   podman stats ocamdemo
   
2. 마이그레이션 시간
   포함된 알고리즘 연산 시간은?

해결:
1. 워커 수 증가
   WORKERS=8 (기본 4개)
   
2. 타임아웃 증가
   TIMEOUT=300
   
3. PC 재부팅
   다른 프로그램으로 인한 간섭 제거
```

### Q17: 메모리 사용량이 높아요
```
진단:
podman stats ocamdemo
또는
podman inspect ocamdemo

메모리 사용 줄이기:
1. 워커 수 감소
   WORKERS=2

2. 페이지 메모리 제한
   podman run --memory 2g ...

3. 큰 알고리즘 작업 분할
   한번에 많은 데이터 처리 피하기
```

### Q18: Gunicorn 워커 너무 많으면?
```
증상:
- CPU 100% 도달
- 응답 시간 증가
- 메모리 부족

해결:
1. 워커 수 확인
   docker-entrypoint.sh에서 WORKERS 값
   
2. 최적값 계산
   기본값 = CPU 코어 수 * 2 + 1
   
   4 코어 → 9개 (과다, 톤다운)
   → 4-6개 권장 시작
   
3. 조정 후 재시작
   WORKERS=4
   ./run-docker.bat
```

---

## 🔄 기타

### Q19: 스크립트가 자동으로 이미지 빌드를 선택하게 하려면?
```
현재: Y 또는 N 입력 필요

자동화 방법:
run-docker.bat 파일 수정:

현재:
set /p BUILD_IMAGE="선택 (Y/N, 기본값=N): "

변경 (항상 빌드):
set BUILD_IMAGE=Y

변경 (항상 기존 이미지 사용):
set BUILD_IMAGE=N
```

### Q20: Mac/Linux에서 색상 출력이 이상해요
```
증상:
터미널 출력이 깨짐, 색상 표시 오류

해결:
1. 터미널 인코딩 확인 (UTF-8인지)
   export LANG=en_US.UTF-8
   
2. 다시 실행
   ./run-docker.sh

또는 색상 없이 실행:
podman logs ocamdemo --no-stream
```

### Q21: 컨테이너 안전하게 삭제하려면?
```bash
# 1. 컨테이너 정지
podman stop ocamdemo

# 2. 컨테이너 삭제 (데이터는 보존!)
podman rm ocamdemo

# 3. 이미지도 삭제하려면
podman rmi ocamdemo:latest

# 주의: 다음은 절대 하지 말 것!
podman volume rm outputs  # ← 데이터 손실!
```

### Q22: 다른 명령어는 어디서 찾나요?
```bash
# Podman 도움말
podman --help

# 특정 명령어 도움말
podman run --help
podman logs --help
podman inspect --help

# 공식 문서
https://docs.podman.io
```

---

## 📞 추가 도움

문제가 해결되지 않으면:

1. **로그 확인**
   ```bash
   podman logs ocamdemo > diagnostic.log
   ```

2. **에러 메시지 검색**
   - Google에서 정확한 에러 메시지 검색
   - Stack Overflow 검색

3. **Django 관련 문제**
   - Django 공식 문서: https://docs.djangoproject.com
   - Django 스택 오버플로우

4. **Podman 관련 문제**
   - Podman Issues: https://github.com/containers/podman/issues
   - Podman Troubleshooting: https://docs.podman.io

---

## 🎯 빠른 진단 명령어 모음

```bash
# 모든 정보 한번에 확인
echo "=== Podman 상태 ===" && \
podman --version && \
echo "" && \
echo "=== 실행 중인 컨테이너 ===" && \
podman ps && \
echo "" && \
echo "=== 리소스 사용 ===" && \
podman stats ocamdemo --no-stream && \
echo "" && \
echo "=== 로그 마지막 30줄 ===" && \
podman logs --tail 30 ocamdemo
```

---

**마지막 업데이트**: 2026-06-19


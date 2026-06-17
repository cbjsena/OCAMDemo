[🏠 홈](../README.md) > **배포 후 체크리스트**

# ✅ 배포 후 점검 (Post-Deployment Checklist)

배포 후에 다음 항목들을 모두 확인하세요.

---

## 🟢 기본 동작 확인

### 웹 서버 상태

- [ ] 컨테이너 실행 중인가?
  ```bash
  podman ps | grep ocamdemo
  ```

- [ ] Gunicorn 로그 정상인가?
  ```bash
  podman logs ocamdemo | tail -20
  ```

- [ ] 포트 8000 열려있는가?
  ```bash
  # Windows
  netstat -ano | findstr 8000
  # Mac/Linux
  lsof -i :8000
  ```

### 웹 접속

- [ ] localhost:8000 접속 가능한가?
  ```
  http://localhost:8000
  ```

- [ ] 페이지가 로드되는가? (흰색 화면 아님)

- [ ] 로고/이미지가 표시되는가?

- [ ] 정적 파일(CSS, JS)이 로드되는가?
  - 브라우저 개발자도구 (F12) → Network 탭 확인
  - 404 에러 없는지 확인

---

## 🗄️ 데이터베이스 확인

### 마이그레이션

- [ ] 마이그레이션 정상 완료됨
  ```bash
  podman logs ocamdemo | grep "migrate"
  ```

- [ ] 테이블이 생성되었나?
  ```bash
  podman exec ocamdemo python manage.py showmigrations
  ```

- [ ] 초기 데이터 있는가?
  ```bash
  podman exec ocamdemo python manage.py shell
  # >>> from django.contrib.auth.models import User
  # >>> User.objects.count()
  ```

### 데이터 보존

- [ ] outputs/ 폴더에 접근 가능한가?
  ```bash
  ls -la outputs/
  ```

- [ ] logs/ 폴더에 로그가 생성되는가?
  ```bash
  ls -la logs/
  ```

- [ ] media/ 폴더 준비됨 (필요시)
  ```bash
  ls -la media/
  ```

- [ ] db.sqlite3 파일 존재함
  ```bash
  ls -la db.sqlite3
  ```

---

## 👤 사용자 접근 테스트

### 로그인 페이지

- [ ] 로그인 페이지 접속 가능?
  ```
  http://localhost:8000/admin/
  ```

- [ ] 로그인 폼이 표시되나?

- [ ] CSRF 토큰 표시되나? (페이지 소스에서 csrfmiddlewaretoken)

### 로그인

- [ ] 기본 관리자 계정으로 로그인 가능?
  ```bash
  # 관리자 계정 생성 (없으면)
  podman exec -it ocamdemo python manage.py createsuperuser
  ```

- [ ] 로그인 후 대시보드 표시되나?

- [ ] 세션 쿠키 설정되었나? (개발자도구 → Application → Cookies)

- [ ] 로그아웃 기능 작동되나?

---

## 🔍 기능 테스트

### 기본 기능

- [ ] 홈페이지 정상 작동?

- [ ] 메뉴 네비게이션 작동?

- [ ] 검색 기능 작동? (있으면)

- [ ] API 엔드포인트 응답? (있으면)
  ```bash
  curl http://localhost:8000/api/
  ```

### 데이터 입출력

- [ ] 데이터 조회 가능?

- [ ] 데이터 생성 가능?

- [ ] 데이터 수정 가능?

- [ ] 데이터 삭제 가능?

### 파일 업로드 (필요시)

- [ ] 파일 업로드 가능?

- [ ] 업로드된 파일이 media/ 저장되나?

- [ ] 업로드된 파일 다운로드 가능?

---

## ⚙️ 외부 공개 확인 (Tunnel 사용 시)

### Cloudflare Quick Tunnel

- [ ] cloudflared 프로세스 실행 중?
  ```bash
  # Windows: Task Manager에서 확인
  # Mac/Linux:
  ps aux | grep cloudflared
  ```

- [ ] Tunnel URL 출력됨?
  ```
  예: https://xxx-yyy-zzz.trycloudflare.com
  ```

- [ ] 터미널 2가 계속 실행 중?
  - 종료되면 외부 접속 불가

- [ ] 다른 기기에서 접속 가능?
  ```
  스마트폰에서 Tunnel URL로 접속
  ```

### Cloudflare Permanent Tunnel

- [ ] Tunnel 설정 완료됨?
  ```bash
  cloudflared tunnel list
  ```

- [ ] DNS 레코드 추가됨?
  - Cloudflare 대시보드 → DNS 확인

- [ ] 도메인으로 접속 가능?
  ```
  https://ocamdemo.example.com
  ```

- [ ] HTTPS 인증서 정상?
  - 브라우저에서 🔒 아이콘 확인

---

## 🚨 보안 확인

### HTTPS/SSL

- [ ] HTTPS 연결인가? (외부 공개 시)
  - 주소 표시줄에서 🔒 확인

- [ ] SSL 인증서 유효한가?
  - 브라우저 개발자도구에서 확인

### CSRF 보호

- [ ] CSRF 토큰이 생성되나?
  - 페이지 소스에서 `csrfmiddlewaretoken` 확인

- [ ] 폼 제출 시 CSRF 체크 활성화되나?

### 권한 확인

- [ ] 로그인하지 않으면 제한된 페이지 접근 불가?

- [ ] 다른 사용자 정보 접근 불가?

### DEBUG 설정

- [ ] DEBUG=False 설정 확인?
  ```python
  # settings.py에서 확인
  DEBUG = False
  ```

- [ ] 에러 페이지가 500.html 표시?
  - 의도적으로 에러 발생시켜 확인

- [ ] 에러 상세 내용이 노출되지 않나?

---

## 📊 성능 점검

### 메모리 사용

- [ ] 메모리 사용량 정상?
  ```bash
  podman stats ocamdemo
  ```

- [ ] 메모리 누수 없는가? (시간이 지나도 안정적)

### CPU 사용

- [ ] CPU 사용률 정상?
  ```bash
  podman stats ocamdemo
  ```

- [ ] CPU 스파이크 없는가?

### 응답 시간

- [ ] 페이지 로드 시간 합리적?
- [ ] 느린 요청 없는가?
  ```bash
  podman logs ocamdemo | grep "completed"
  ```

---

## 🔄 재시작 안정성

### 컨테이너 재시작

- [ ] 컨테이너 재시작 후 정상 작동?
  ```bash
  podman restart ocamdemo
  podman exec ocamdemo curl http://localhost:8000
  ```

- [ ] 데이터 손실 없는가?
  ```bash
  # 재시작 전후 데이터 비교
  ```

- [ ] 마이그레이션 재실행되나?
  ```bash
  podman restart ocamdemo
  podman logs ocamdemo | grep migrate
  ```

### 이미지 재빌드

- [ ] 새 이미지 빌드 후 정상 작동?
- [ ] 빌드 시간 합리적?
- [ ] 캐시 효율적으로 작동?

---

## 📝 로그 확인

### 로그 위치

- [ ] logs/ 폴더에 로그 파일 생성?
  ```bash
  ls -la logs/
  ```

- [ ] 실시간 로그 확인 가능?
  ```bash
  podman logs -f ocamdemo
  ```

### 로그 내용

- [ ] ERROR 메시지 없는가?
  ```bash
  podman logs ocamdemo | grep ERROR
  ```

- [ ] WARNING 메시지 확인됨?
  ```bash
  podman logs ocamdemo | grep WARNING
  ```

- [ ] 중요한 이벤트 기록됨?
  ```bash
  podman logs ocamdemo | grep ocamdemo_notification
  ```

---

## 🔔 알림/모니터링 (선택사항)

- [ ] 상태 모니터링 계획 수립됨?

- [ ] 에러 발생 시 알림받을 준비됨?

- [ ] 로그 수집 계획 수립됨?

---

## 🎯 최종 체크리스트

```
___  배포 완료!
|✓| 웹 서버 실행 중
|✓| 로컬 접속 가능
|✓| 데이터베이스 정상
|✓| 기본 기능 작동
|✓| 보안 설정 확인
|✓| 성능 정상
|✓| 로그 생성
```

---

## 📞 문제 발생 시

문제가 있으면 다음을 확인:

1. **로그 확인**
   ```bash
   podman logs ocamdemo | tail -50
   ```

2. **컨테이너 상태 확인**
   ```bash
   podman inspect ocamdemo
   ```

3. **문제 해결 가이드**
   → [FAQ & 문제 해결](../1_LocalPC_Podman/FAQ_TROUBLESHOOTING.md)

---

## ✅ 다음 단계

```
배포 완료! 🎉

1. 팀에 공지
2. 백업 정책 수립
3. 모니터링 설정
4. 지속적 개선
```

---

**마지막 업데이트**: 2026-06-19


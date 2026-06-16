# OCAMDemo v0.1 Release Notes

## 릴리즈 날짜
2026-06-15

## 주요 기능
### Instance 관리
- `instances/` 폴더 자동 스캔 (metadata.csv 기반)
- 하위 폴더 지원 (~ 구분자)
- CSV 파일 조회 / 업로드 / 다운로드

### Simulation 실행
- Instance + Algorithm 선택 후 비동기 실행
- `algorithms/<researcher>/<name>/solver.py` 자동 탐색
- Mock 엔진 (6초×10단계 = 60초 완료)
- 실시간 진행률 모니터링 (AJAX polling)
- 중단(Cancel) 기능

### Result 확인
- `outputs/<날짜_시간>/<알고리즘>.json` 자동 스캔
- 결과 상세 조회 (Summary Cards + Raw JSON)

### 공통 기능
- 로그인/로그아웃 (Django auth)
- Toast 알림 (Bootstrap)
- Context Processor로 메뉴 자동 전달
- 커스텀 템플릿 필터 (split, strip, replace_underscore)
- Celery Eager/Async 자동 전환
- Debug Toolbar 조건부 활성화
- pytest fixture 통합


# OCAMDemo — 프로젝트 개요

## 목적
다양한 최적화 알고리즘을 테스트하기 위한 파일 기반 웹 플랫폼.
DB 대신 CSV/JSON 파일로 입력 데이터와 결과를 관리한다.

## 3대 메뉴 구조
1. **Instance** — 입력 데이터 관리 (CSV 파일 기반)
2. **Simulation** — Instance + Algorithm 선택 후 실행
3. **Result** — 실행 결과 확인

## 기술 스택
- Django 5.2+ / SQLite (SimulationRun 모델만)
- Celery (로컬: Eager 모드, Docker: Redis)
- Bootstrap 5 + Bootstrap Icons
- pytest + pytest-django

## 폴더 규칙
- `instances/` — CSV 인스턴스 데이터
- `algorithms/` — `<researcher>/<algo>/solver.py`
- `outputs/` — `<YYYYMMDD_HHMMSS>/<algo>.json`


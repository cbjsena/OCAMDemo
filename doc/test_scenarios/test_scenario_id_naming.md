# 테스트 시나리오 ID 명명 규칙 (v2)

---

## 1. 형식

```
{APP}_{MENU}_{TYPE}_{NNN}
```

| 세그먼트 | 필수 | 설명 | 예시 |
|---------|------|------|------|
| **APP** | ✅ | Django 앱 식별자 (2~3자) | `IN`, `SIM`, `RPT` |
| **MENU** | ✅ | 메뉴/모듈 식별자 (2~4자) | `PF`, `CV`, `MTR` |
| **TYPE** | ✅ | 테스트 계층 | `DIS`, `SVC`, `MDL`, `CMD`, `API` |
| **NNN** | ✅ | 3자리 일련번호 | `001`, `002`, ... |


---

## 2. APP 코드표

| APP | Django 앱     | 설명 | 상태 |
|-----|--------------|------|------|
| `IN` | `instance`   | 인스턴스 폴더 관리 (업로드/다운로드/비교) | 현재 |
| `AP` | `api`        | 내부 데이터 통신 API | 현재 |
| `CM` | `common`     | 공통 인프라 (DB Doc, Auth 등) | 현재 |
| `SIM` | `simulation` | 시뮬레이션 실행 (List/Create/Monitoring) | 현재 |
| `RPT` | `result`     | 결과 분석/리포트 | 예정 |

---

## 3. MENU 코드표

### AP (api) — 내부 데이터 통신 API
| MENU | 대상 | 비고 |
|------|------|------|
| `DST` | Distance API | 포트 간 거리 조회 |
| `PF` | Proforma API | Lane/PF 목록, 상세 조회 |
| `VSL` | Vessel API | 선박 목록, 점유 확인, 옵션 |
| `BVL` | Base Vessel API | BaseVesselInfo 마스터 목록 |

### SIM (simulation) — 시뮬레이션 실행
| MENU | 대상 | 비고 |
|------|------|------|
| `LST` | Simulation List | 목록 / 필터 |
| `CRT` | Simulation Create | 인스턴스/알고리즘 선택 후 생성 |
| `MON` | Simulation Monitoring | 진행 중인 시뮬레이션 모니터링 |
| `ALG` | Algorithm Add | 알고리즘 추가 (향후) |

### IN (instance) — 인스턴스 폴더 관리
| MENU | 대상 | 비고 |
|------|------|------|
| `LST` | Instance List | 인스턴스 목록 / 폴더 다운로드 |
| `UPL` | Instance Upload | 폴더 업로드 (ZIP) |
| `CMP` | Compare Instances | 두 인스턴스 파일 비교 |

### CM (common) — 공통 인프라
| MENU | 대상 | 비고 |
|------|------|------|
| `DOC` | DB Comment / Table Definition | CLI + Signal |
| `AUTH` | 인증 / 접근 제어 | |

---

## 4. TYPE 키워드

| TYPE | 의미 | 사용 시점 |
|------|------|----------|
| `DIS` | Display (화면) | 목록, 필터, 추가, 삭제, 검색 등 화면 CRUD |
| `SVC` | Service | 비즈니스 로직 (서비스 계층) |
| `MDL` | Model | DB 모델 (FK, Unique, Cascade) |
| `CMD` | Command | Management Command (CLI) |
| `API` | API | AJAX 엔드포인트 |

---

## 5. 전체 매핑표 (현재 → 신규)

### SIM (simulation) — 시뮬레이션 실행
| 현재 ID | 신규 ID | 기능명 |
|---------|---------|--------|
| `SIM_LIST_001` | `SIM_LST_DIS_001` | Simulation List 메뉴 라우팅 |
| `SIM_LIST_002` | `SIM_LST_DIS_002` | Simulation 목록 조회 (정상) |
| `SIM_LIST_003` | `SIM_LST_DIS_003` | Simulation 목록 조회 (없음) |
| `SIM_LIST_004` | `SIM_LST_DIS_004` | Simulation 목록 상태 필터 (진행 중) |
| `SIM_LIST_005` | `SIM_LST_DIS_005` | Simulation 목록 상태 필터 (완료) |
| `SIM_LIST_006` | `SIM_LST_DIS_006` | Simulation 목록 정렬 (최신순) |
| `SIM_LIST_007` | `SIM_LST_DIS_007` | Simulation 목록 로그인 검증 |
| `SIM_CREATE_001` | `SIM_CRT_DIS_001` | Simulation Create 메뉴 라우팅 |
| `SIM_CREATE_002` | `SIM_CRT_DIS_002` | Instance 드롭다운 조회 |
| `SIM_CREATE_003` | `SIM_CRT_DIS_003` | Instance 사전 선택 (쿼리 파라미터) |
| `SIM_CREATE_004` | `SIM_CRT_DIS_004` | Algorithm 드롭다운 조회 |
| `SIM_CREATE_005` | `SIM_CRT_DIS_005` | 시뮬레이션 생성 (정상) |
| `SIM_CREATE_006` | `SIM_CRT_DIS_006` | 시뮬레이션 생성 (파라미터 누락) |
| `SIM_CREATE_007` | `SIM_CRT_DIS_007` | 시뮬레이션 생성 (설명 추가) |
| `SIM_CREATE_008` | `SIM_CRT_DIS_008` | Create 페이지 로그인 검증 |
| `SIM_MONITOR_001` | `SIM_MON_DIS_001` | Simulation Monitoring 페이지 라우팅 |
| `SIM_MONITOR_002` | `SIM_MON_DIS_002` | 진행 중인 시뮬레이션만 표시 |
| `SIM_MONITOR_003` | `SIM_MON_DIS_003` | 시뮬레이션 진행률 표시 |
| `SIM_MONITOR_004` | `SIM_MON_DIS_004` | 시뮬레이션 상태 API (AJAX) |
| `SIM_MONITOR_005` | `SIM_MON_DIS_005` | 시뮬레이션 취소 버튼 |
| `SIM_MONITOR_006` | `SIM_MON_DIS_006` | 시뮬레이션 취소 (정상) |
| `SIM_MONITOR_007` | `SIM_MON_DIS_007` | 시뮬레이션 취소 (이미 완료) |
| `SIM_MONITOR_008` | `SIM_MON_DIS_008` | Monitoring 페이지 로그인 검증 |

### IN (instance) — 인스턴스 폴더 관리
| 현재 ID | 신규 ID | 기능명 |
|---------|---------|--------|
| `INST_LIST_001` | `IN_LST_DIS_001` | Instance List 메뉴 라우팅 |
| `INST_LIST_002` | `IN_LST_DIS_002` | Instance 사이드바 3개 메뉴 구성 |
| `INST_DOWN_001` | `IN_LST_DIS_003` | Instance List 다운로드 컬럼 노출 |
| `INST_DOWN_002` | `IN_LST_DIS_004` | 인스턴스 폴더 다운로드 (정상) |
| `INST_DOWN_003` | `IN_LST_DIS_005` | 인스턴스 폴더 다운로드 (없음) |
| `INST_UPLOAD_001` | `IN_UPL_DIS_001` | Instance Upload 메뉴 라우팅 |
| `INST_UPLOAD_002` | `IN_UPL_DIS_002` | 인스턴스 폴더 업로드 (정상) |
| `INST_UPLOAD_003` | `IN_UPL_DIS_003` | 인스턴스 폴더 업로드 (파일 미선택) |
| `INST_UPLOAD_004` | `IN_UPL_DIS_004` | 인스턴스 폴더 업로드 (확장자 오류) |
| `INST_UPLOAD_005` | `IN_UPL_DIS_005` | 인스턴스 폴더 업로드 (metadata 누락) |
| `INST_UPLOAD_006` | `IN_UPL_DIS_006` | 인스턴스 폴더 업로드 (중복 이름) |
| `INST_COMPARE_001` | `IN_CMP_DIS_001` | Compare Instances 메뉴 라우팅 |
| `INST_COMPARE_002` | `IN_CMP_DIS_002` | Compare Instances 유효성 (미선택) |
| `INST_COMPARE_003` | `IN_CMP_DIS_003` | Compare Instances 유효성 (동일 인스턴스 선택) |
| `INST_COMPARE_004` | `IN_CMP_DIS_004` | Compare Instances 결과 (완전 동일) |
| `INST_COMPARE_005` | `IN_CMP_DIS_005` | Compare Instances 결과 (차이 행 개수) |
| `INST_COMPARE_006` | `IN_CMP_DIS_006` | Compare Instances 결과 (파일 누락) |
| `INST_COMPARE_007` | `IN_CMP_DIS_007` | Compare Instances 결과 (헤더 불일치) |
| `INST_COMPARE_008` | `IN_CMP_DIS_008` | Compare Instances 결과 (행 수 불일치) |

---

## 6. 규칙 요약

| # | 규칙 |
|---|------|
| 1 | 형식: **`{APP}_{MENU}_{TYPE}_{NNN}`** (4단계 고정) |
| 2 | APP: 앱별 2~3자 고정 코드 (`IN`, `CM`, `SIM`, `RPT`) |
| 3 | MENU: 메뉴별 2~4자 고정 코드 (코드표 참조) |
| 4 | TYPE: **필수** — `DIS`(화면), `SVC`(서비스), `MDL`(모델), `CMD`(커맨드), `API` |
| 5 | NNN: 3자리 일련번호, **APP+MENU+TYPE 범위 내** 순차 부여 |
| 6 | 화면 CRUD 테스트는 `DIS` 사용 → `IN_LST_DIS_001` (Instance List), `IN_UPL_DIS_001` (Instance Upload), `SIM_LST_DIS_001` (Simulation List) |
| 7 | 접두사만으로 **앱 → 메뉴 → 계층** 즉시 식별 가능 |
| 8 | 향후 앱 추가 시 APP 코드만 추가하면 기존 ID와 충돌 없음 |

# OCAM Solution Visualizer

OCAM 실행 결과를 브라우저에서 읽어 요약, feasible/infeasible 상태, objective breakdown, lane coverage, vessel schedule timeline, metadata, logs, raw JSON을 함께 보여주는 정적 GUI입니다.

## 기술 스택

- `HTML/CSS/vanilla JavaScript` 단일 페이지 앱
- 별도 빌드, 패키지 설치, 백엔드 서버 없음
- `outputs` 안의 실행 결과와 `outputs/leaderboard` 안의 best 결과를 브라우저에서 직접 읽음

## 실행

```text
visualizer/index.html
```

브라우저에서 `visualizer/index.html` 파일을 직접 엽니다. 처음 실행할 때 `outputs` 폴더만 한 번 선택하면 되고, 상단의 source 모드로 `Runs`와 `Leaderboard`를 전환할 수 있습니다.

브라우저 보안 정책상 로컬 HTML 파일은 `../outputs` 같은 주변 폴더를 자동 스캔할 수 없습니다. File System Access API를 지원하는 브라우저에서는 한 번 선택한 `outputs` 폴더 권한을 기억해 다음 실행부터 자동 재연결을 시도합니다. 지원하지 않는 브라우저에서는 `+` 버튼으로 폴더를 선택합니다.

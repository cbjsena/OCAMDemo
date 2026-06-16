# 알고리즘 가이드

이 문서는 `algorithms/` 아래에 새로운 알고리즘을 추가하려는 연구자를 위한 안내서입니다.

## 디렉터리 규칙

알고리즘은 아래와 같은 구조로 탐색됩니다.

```text
algorithms/
└── <researcher_name>/
    └── <algorithm_name>/
        ├── __init__.py
        └── solver.py
```

현재 레지스트리는 `algorithms/<researcher_name>/<algorithm_name>/solver.py`를 직접 읽고, 그 파일 안에 callable한 `algorithm()` 함수가 있기를 기대합니다.

설정 파일에서 사용하는 공개 알고리즘 이름은 다음 형식입니다.

```text
<researcher_name>/<algorithm_name>
```

예시:

```text
yongs/only_virtual
gildong/greedy_search_v2
```

## 새 알고리즘을 만드는 권장 방법

1. `algorithms/` 아래에 자신의 폴더를 만듭니다.
2. `algorithms/yongs/template_algorithm/`을 자신의 폴더 아래로 복사합니다.
3. 복사한 디렉터리 이름을 자신의 알고리즘 이름으로 바꿉니다.
4. `solver.py`의 `algorithm()`을 작성합니다.

예시:

```text
algorithms/
└── gildong/
    └── vessel_swap_heuristic/
        ├── __init__.py
        └── solver.py
```

## 최소 구현

`solver.py`에는 반드시 아래 형태가 있어야 합니다.

```python
from ocam.models import CascadingSolution, InstanceData

DESCRIPTION = "사용자에게 보여줄 한 줄 설명"

def algorithm(instance_data: InstanceData, timelimit: int) -> CascadingSolution:
    ...
```

규칙:

- 함수 인자는 정확히 `InstanceData`와 `timelimit`만 받습니다.
- 반환값은 반드시 `CascadingSolution`이어야 합니다.
- 알고리즘 내부에서 원본 입력 파일을 다시 읽지 않습니다.
- 알고리즘 내부에서 `RunConfig`나 CLI 파싱에 의존하지 않습니다.
- 네이티브 코드가 필요하면 자신의 패키지 안에서 관리합니다.

## OCAM이 알고리즘을 실행하는 방식

알고리즘이 시작되기 전에 OCAM 내에서 다음 작업이 먼저 진행됩니다.

- YAML 설정 파일 파싱
- 입력 인스턴스 디렉터리 로딩
- 각 입력 묶음을 `InstanceData` 하나로 변환하는 전처리

알고리즘이 솔루션을 반환한 뒤에는 다음이 수행됩니다.

- 반환값이 `CascadingSolution`인지 검사
- 솔루션의 feasibility 검증
- 솔루션의 목적함수값 계산
- 콘솔 출력 캡처 및 로그 저장
- 후처리와 결과 파일 기록

## 알고리즘 실행 방법

예를 들어 아래와 같은 설정 파일을 만듭니다.

```yaml
algorithms:
  - gildong/vessel_swap_heuristic
instances:
  - instances/toy_v1
outputs: outputs
leaderboard: outputs/leaderboard
timelimit: 60
```

그 다음 아래처럼 실행합니다.

```bash
python3 main.py my_config.yaml
```

경로를 생략하면 `python3 main.py`는 `default_config.yaml`을 사용합니다.

## 여러 알고리즘 비교 방법

여러 알고리즘은 `algorithms` 배열에, 여러 인스턴스는 `instances` 배열에 원하는 순서대로 넣으면 됩니다.

```yaml
algorithms:
  - gildong/vessel_swap_heuristic
  - yongs/only_virtual
  - bob/mip_baseline
instances:
  - instances/toy_v1
  - instances/toy_v2/006_EC2
```

OCAM은 `instances × algorithms` 조합을 순차 실행하고, 여러 인스턴스를 지정한 경우 결과 파일을 인스턴스별 하위 폴더에 나눠서 생성합니다.

## 구현 전에 먼저 보면 좋은 파일

새 알고리즘을 구현하기 전에 아래 파일을 먼저 읽는 것을 권장합니다.

- [README.md](../README.md)
- [docs/instance-data.md](../docs/instance-data.md)
- [docs/output-files.md](../docs/output-files.md)
- [docs/solution-constraint.md](../docs/solution-constraint.md)
- [algorithms/yongs/template_algorithm/solver.py](yongs/template_algorithm/solver.py)
- [algorithms/yongs/only_virtual/solver.py](yongs/only_virtual/solver.py)

## 자주 하는 실수

- 설정 파일에 알고리즘 이름을 잘못 적는 경우. 형식은 반드시 `<researcher>/<algorithm>`입니다.
- `CascadingSolution` 대신 일반 dict를 반환하는 경우.
- `InstanceData`가 원본 CSV와 완전히 같은 구조라고 가정하는 경우.
- 파일 입출력을 알고리즘 안에 넣는 경우.

# 결과 파일 가이드

이 문서는 연구자가 알고리즘을 실행했을 때 어떤 결과 파일이 생성되는지, 각 파일에 무엇이 들어 있는지, 그리고 실무적으로 어떻게 활용하면 좋은지를 설명합니다.

## 언제 생성되는가

OCAM은 `python3 main.py` 또는 `python3 main.py <config.yaml>`로 실행할 때 결과 파일을 생성합니다.

알고리즘 실행과 후처리가 끝나면 `config.yaml`의 `outputs` 아래에 실행 시각 기준 하위 디렉터리가 하나 생성됩니다.

디렉터리 이름 형식:

```text
YYMMDD_HHMM
```

예시:

```text
outputs/
└── 260417_1842/
```

같은 분(minute)에 여러 번 실행해서 이름이 충돌하면 `(2)`, `(3)` 같은 suffix가 뒤에 붙습니다.

## 어떤 파일이 생성되는가

하나의 algorithm-instance 조합마다 아래 3개 파일이 생성됩니다.

```text
<researcher>_<algorithm>_solution.json
<researcher>_<algorithm>_metadata.csv
<researcher>_<algorithm>_logs.txt
```

예시:

```text
yongs_only_virtual_solution.json
yongs_only_virtual_metadata.csv
yongs_only_virtual_logs.txt
```

알고리즘 이름은 내부적으로 `<researcher>/<algorithm>` 형식이지만, 파일명에서는 `/` 대신 `_`를 사용합니다.

## 여러 알고리즘과 여러 인스턴스를 실행하면 어떻게 되는가

인스턴스가 하나면 기존처럼 같은 실행 디렉터리 아래에 알고리즘별 파일이 생성됩니다.

예를 들어:

```yaml
algorithms:
  - gildong/my_heuristic
  - yongs/only_virtual
  - cheolsu/mip_baseline
instances:
  - instances/toy_v1
```

이 경우 한 번의 실행으로 같은 시각 디렉터리 안에 총 9개 파일이 생깁니다.

- `gildong_my_heuristic_solution.json`
- `gildong_my_heuristic_metadata.csv`
- `gildong_my_heuristic_logs.txt`
- `yongs_only_virtual_solution.json`
- `yongs_only_virtual_metadata.csv`
- `yongs_only_virtual_logs.txt`
- `cheolsu_mip_baseline_solution.json`
- `cheolsu_mip_baseline_metadata.csv`
- `cheolsu_mip_baseline_logs.txt`

이 구조 덕분에 같은 조건으로 비교한 알고리즘 결과를 한 폴더 안에서 바로 비교할 수 있습니다.

인스턴스가 여러 개면 실행 디렉터리 아래에 인스턴스별 하위 폴더가 먼저 생깁니다.

```yaml
algorithms:
  - gildong/my_heuristic
  - yongs/only_virtual
instances:
  - instances/toy_v1
  - instances/toy_v2/006_EC2
```

예시 구조:

```text
outputs/
└── 260425_1530/
    ├── toy_v1/
    │   ├── gildong_my_heuristic_solution.json
    │   ├── gildong_my_heuristic_metadata.csv
    │   ├── gildong_my_heuristic_logs.txt
    │   ├── yongs_only_virtual_solution.json
    │   ├── yongs_only_virtual_metadata.csv
    │   └── yongs_only_virtual_logs.txt
    └── 006_EC2/
        ├── gildong_my_heuristic_solution.json
        ├── gildong_my_heuristic_metadata.csv
        ├── gildong_my_heuristic_logs.txt
        ├── yongs_only_virtual_solution.json
        ├── yongs_only_virtual_metadata.csv
        └── yongs_only_virtual_logs.txt
```

## leaderboard에는 무엇이 들어 있는가

`outputs/leaderboard/`는 validate를 통과하고 목적함수 계산까지 성공한 결과만 대상으로, 각 `scenario_name`과 알고리즘 조합의 현재 best 결과를 보관하는 폴더입니다.

구조는 아래와 같습니다.

```text
outputs/
└── leaderboard/
    └── <scenario_name>/
        └── <researcher>_<algorithm>.json
```

이 JSON 안에는 기존 `solution.json`, `metadata.csv`, `logs.txt`의 내용이 모두 들어 있습니다. 구조화된 필드(`solution`, `metadata`, `objective`, `logs`)와 원본 artifact 문자열(`artifacts.solution_json`, `artifacts.metadata_csv`, `artifacts.logs_txt`)이 함께 저장됩니다.

같은 알고리즘이 다시 실행되어 더 낮은 `total_cost`를 만들면 해당 파일이 덮어써집니다. validation 실패, 실행 오류, objective 계산 실패 결과는 leaderboard에 반영되지 않습니다.

## solution.json에는 무엇이 들어 있는가

`solution.json`에는 알고리즘이 반환한 `CascadingSolution` 내용만 들어 있습니다.

즉, 상태 코드나 로그, 메타데이터를 섞지 않고 해 자체만 JSON으로 저장합니다.

대표적인 top-level 필드:

- `declared_positions`
- `vessel_schedules`
- `virtual_vessel_schedules`
- `num_virtual_vessels_used`

이 파일은 아래 상황에 가장 적합합니다.

- 생성된 해를 다른 스크립트에서 다시 읽고 분석할 때
- 특정 선박 또는 특정 lane의 이벤트를 추출할 때
- validation 실패 사례를 재현하거나 디버깅할 때
- 알고리즘 간 해 구조를 비교할 때

## metadata.csv에는 무엇이 들어 있는가

`metadata.csv`는 사람이 바로 열어보기 쉬운 실행 요약 파일입니다.

형식은 단순한 2열 CSV입니다.

```text
field,value
algorithm,yongs/only_virtual
status,ok
objective,123.45
solution_file,yongs_only_virtual_solution.json
logs_file,yongs_only_virtual_logs.txt
elapsed_seconds,1.234
postprocessed,True
...
```

기본적으로 다음 정보가 들어갑니다.

- `algorithm`
- `status`
- `objective`
- `solution_file`
- `logs_file`

그리고 `result.metadata`에 들어 있는 값들이 추가됩니다. 예를 들어:

- `elapsed_seconds`
- `postprocessed`
- 오류가 난 경우 `error_type`, `error_message`, `error_context`

이 파일은 아래 상황에 가장 적합합니다.

- 여러 알고리즘 실행 결과를 빠르게 훑어볼 때
- objective, status, 수행 시간만 먼저 확인할 때
- 스프레드시트로 모아서 비교할 때
- 실패 실행의 오류 타입과 메시지를 빠르게 확인할 때

## logs.txt에는 무엇이 들어 있는가

`logs.txt`에는 알고리즘 실행 중 `print()`로 출력한 내용이 저장됩니다.

이 파일은 아래 상황에 가장 유용합니다.

- 알고리즘 내부 분기나 단계별 진행 상황을 추적할 때
- 왜 특정 fallback이 발생했는지 볼 때
- 특정 선박 또는 특정 lane 관련 디버그 메시지를 확인할 때
- 오류가 나기 직전 어떤 상태였는지 볼 때

## 어떤 경우에 어떤 파일을 주로 쓰면 좋은가

### 알고리즘 성능 비교가 목적일 때

- 우선 `metadata.csv`만 모아서 objective와 elapsed time을 비교하는 것이 가장 효율적입니다.

### 스케줄 구조를 자세히 보고 싶을 때

- `solution.json`에서 `vessel_schedules`와 `virtual_vessel_schedules`를 확인합니다.

### 디버깅이 목적일 때

- `logs.txt`와 `metadata.csv`를 먼저 보고, 필요하면 `solution.json`까지 내려갑니다.

### validation 오류를 해석할 때

- `metadata.csv`의 오류 정보와 `logs.txt`를 먼저 보고,
- 어느 선박/항로/이벤트가 문제인지 확인되면 `solution.json`에서 해당 부분을 직접 찾는 방식이 좋습니다.

## 주의할 점

- `solution.json`은 사람이 읽을 수도 있지만, 기본적으로는 해 데이터를 보존하는 파일입니다.
- 요약 비교를 위해서는 `solution.json`보다 `metadata.csv`가 훨씬 편합니다.
- 가상 선박은 `virtual_vessel_schedules`에 별도로 저장될 수 있습니다.
- 예전 알고리즘은 호환성 때문에 가상 선박을 `vessel_schedules`에 함께 넣을 수도 있으므로, 구형 결과를 볼 때는 이 점을 염두에 두는 것이 좋습니다.

## 관련 문서

- [README.md](../README.md)
- [알고리즘 가이드](../algorithms/README.md)
- [InstanceData 계약](instance-data.md)
- [Solution 제약](solution-constraint.md)

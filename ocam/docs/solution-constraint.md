# Solution 제약

모든 알고리즘은 반드시 `CascadingSolution`을 반환해야 합니다.

오케스트레이션 레이어는 다른 타입의 반환값을 허용하지 않습니다.

## CascadingSolution

현재 생성자 형태는 아래와 같습니다.

```python
CascadingSolution(
    declared_positions=...,
    vessel_schedules=...,
    virtual_vessel_schedules=...,
    num_virtual_vessels_used=0,
)
```

반드시 지켜야 하는 규칙:

- `declared_positions`는 `None`이면 안 됩니다.
- `vessel_schedules`는 `None`이면 안 됩니다.
- `virtual_vessel_schedules`를 생략하면 빈 매핑으로 처리됩니다.
- `vessel_schedules`와 `virtual_vessel_schedules`는 같은 `vessel_code`를 공유하면 안 됩니다.
- `num_virtual_vessels_used`는 음수가 아닌 정수여야 합니다.

## declared_positions

`declared_positions`는 `DeclaredPosition` 레코드의 리스트입니다.

각 레코드는 어떤 lane/proforma position을 알고리즘이 선언해서 사용한다는 뜻입니다.

하나의 레코드는 아래 정보를 가집니다.

```python
DeclaredPosition(
    lane_code: str,
    proforma_name: str,
    declared_position_no: int,
)
```

내부에서 dict나 3-tuple을 입력해도 `DeclaredPosition`으로 자동 변환을 해주지만, 가독성을 위해 가능하면 `DeclaredPosition(...)` 형태를 직접 사용하는 것을 권장합니다.

## vessel_schedules

`vessel_schedules`는 실제 선박의 `vessel_code`를 key로 하는 매핑입니다.

개념적으로는 아래와 같습니다.

```python
{
    "VESSEL001": VesselSchedule([...]),
}
```

각 value는 하나의 실제 선박에 대한 시간순 이벤트 목록입니다.

## virtual_vessel_schedules

`virtual_vessel_schedules`는 가상 선박의 `vessel_code`를 key로 하는 매핑입니다.

개념적으로는 아래와 같습니다.

```python
{
    "VIRTUAL001": VesselSchedule([...]),
    "VIRTUAL002": VesselSchedule([...]),
}
```

각 value는 하나의 가상 선박에 대한 시간순 이벤트 목록입니다.

가상 선박이 없다면 빈 매핑이면 됩니다.

검증 로직은 `virtual_vessel_schedules`에 포함되어 있는지를 통해 가상 선박 여부를 판단합니다.

## 전체 스케줄 뷰

validation의 lane view 검증은 실제 선박과 가상 선박 스케줄을 합친 전체 스케줄 뷰를 기준으로 동작합니다.

반면 vessel view 검증은 `instance_data.vessels`에 존재하는 실제 선박만 대상으로 수행합니다.

## 이벤트 타입 전체 목록

정확한 클래스 정의는 [ocam/models/schedule_events.py](../ocam/models/schedule_events.py)에 있습니다. 아래는 현재 지원되는 이벤트 타입 전체 목록과 필드 설명입니다.

### 공통 규칙

- 시간 필드는 모두 `datetime`
- 항구 코드(`port_code`)는 모두 문자열
- `port_seq`, `position_no`는 정수
- 어떤 일이 발생했음을 표현하는 **점** 이벤트는 시간 필드를 하나만 가집니다.
- 특정 기간 동안 어떤 일이 일어나고 있음을 표현하는 **구간** 이벤트는 시작 시각과 끝 시각으로 두 개 의 시간 필드를 가집니다.

### 1. InLaneSail

서비스 항로에 배정된 상태에서 한 port call에서 다음 port call로 이동하는 항해 **구간** 이벤트입니다.

필드:

- `lane_code`: 서비스 항로 코드
- `proforma_name`: proforma 이름
- `position_no`: 해당 항로에서의 position 번호
- `from_port_code`: 출발 항구 코드
- `from_port_seq`: 출발 항구 순번
- `sea_sail_start`: 출항 시각
- `to_port_code`: 도착 항구 코드
- `to_port_seq`: 도착 항구 순번
- `sea_sail_end`: 도착 시각

`sailing_start`는 출발 항구의 ETD 시점으로부터 Pilot Out만큼의 시간이 경과한 지점을 말합니다. 즉, sailing 시간은 항구와 무관하게 바다에서 이동하는 상태를 표현합니다. 마찬가지로, `sailing_end`는 도착 항구의 ETA와 동일합니다. (참고: ETA로부터 Pilot In 시간이 경과해 접안을 하는 시점이 ETB이며, ETB부터 ETD까지 PortStay합니다.)

### 2. OutLaneSail

서비스 항로 밖에서 선박이 이동하는 항해 **구간** 이벤트입니다.

필드:

- `from_port_code`: 출발 항구 코드
- `sea_sail_start`: 출항 시각
- `to_port_code`: 도착 항구 코드
- `sea_sail_end`: 도착 시각

### 3. PhaseIn

선박이 특정 lane/proforma/position에 투입되는 시점을 표현하는 **점** 이벤트입니다.

필드:

- `lane_code`: 서비스 항로 코드
- `proforma_name`: proforma 이름
- `position_no`: 해당 항로에서의 position 번호
- `phase_in_port_code`: 투입 항구의 코드
- `phase_in_port_seq`: 투입 항구의 순번
- `phase_in_time`: 투입 시각

### 4. PhaseOut

선박이 특정 lane/proforma/position에서 빠지는 시점을 표현하는 **점** 이벤트입니다.

필드:

- `lane_code`: 서비스 항로 코드
- `proforma_name`: proforma 이름
- `position_no`: 해당 항로에서의 position 번호
- `phase_out_port_code`: 이탈 항구 코드
- `phase_out_port_seq`: 이탈 항구 순번
- `phase_out_time`: 이탈 시각

### 5. PortStay

항구 입항, 접안, 작업, 출항까지의 체류 구간을 표현하는 이벤트입니다. 즉, "ETA" ~ "ETD + Pilot Out"까지의 기간을 나타냅니다. 이 이벤트의 정보가 정확히 proforma와 일치해야지만 수요가 만족된 것으로 판단합니다.

필드:

- `lane_code`: 서비스 항로 코드
- `proforma_name`: proforma 이름
- `position_no`: 해당 항로에서의 position 번호
- `port_code`: 기항 항구 코드
- `port_seq`: 기항 항구 순번
- `pilot_in_start`: 도선 시작 시각 = ETA.
- `berthing_start`: 접안 시작 시각 = ETB.
- `berthing_end`: 접안 종료 시각 = ETD
- `pilot_out_end`: 출항 완료 시각 = ETD + Pilot Out

### 6. TransshipmentUnload

기존 선박이 환적 항구에서 화물을 내려놓는 구간 이벤트입니다. 환적 상황에서는 도선 시간은 고려하지 않고, OutLaneSail을 시작하기까지 고정된 시간이 소요된다고 가정합니다(예: 6시간).

필드:

- `lane_code`: 서비스 항로 코드
- `proforma_name`: proforma 이름
- `position_no`: 해당 항로에서의 position 번호
- `ts_port_code`: 환적 항구 코드
- `ts_port_seq`: 환적 항구 순번
- `unload_start`: 하역 시작 시각
- `unload_end`: 하역 종료 시각 = `unload_start` + 6H

### 7. TransshipmentLoad

새 선박이 환적 항구에서 화물을 싣는 구간 이벤트입니다. 마찬가지로 도선 시간을 고려하지 않습니다.

필드:

- `lane_code`: 서비스 항로 코드
- `proforma_name`: proforma 이름
- `position_no`: 해당 항로에서의 position 번호
- `ts_port_code`: 환적 항구 코드
- `ts_port_seq`: 환적 항구 순번
- `load_start`: 적재 시작 시각
- `load_end`: 적재 종료 시각

### 8. DryDock

선박이 dry dock에 들어가서 나오는 정비 구간 이벤트입니다.

필드:

- `dock_port_code`: dry dock 항구 코드
- `dock_in`: 입거 시각
- `dock_out`: 출거 시각

### 9. Idle

선박이 특정 항구 근처에서 대기하는 구간 이벤트입니다. 서비스 항로에 할당되어 있지 않은 상태에서만 `Idle`할 수 있습니다.

필드:

- `port_code`: 대기 항구 코드
- `idle_start`: 대기 시작 시각
- `idle_end`: 대기 종료 시각

### 10. Delivery

신조선 인도 또는 용선 시작으로 선박이 운용 가능 상태가 되는 시점을 표현하는 점 이벤트입니다. 정확히 `delivery_time`때부터 Phase In이나 항해가 가능합니다.

필드:

- `delivery_port_code`: 인도 항구 코드
- `delivery_time`: 인도 시각

### 11. Redelivery

용선 선박을 반선하는 시점을 표현하는 점 이벤트입니다.

필드:

- `redelivery_port_code`: 반선 항구 코드
- `redelivery_time`: 반선 시각

## 검증 제약 조건

실제 해 검증 기준은 [ocam/validation.py](../ocam/validation.py)의 `validate_solution()`에 들어 있습니다. 아래 목록은 현재 코드의 검증 항목을 그대로 문서화한 것입니다.

주의:

- 아래는 현재 구현된 검증 목록입니다.
- `validation.py` 안에도 일부 `TODO`가 남아 있습니다.
- 문서와 코드가 다를 때는 코드를 기준으로 봐야 합니다.

### Declared Positions Validation

#### POSITION-1. 모든 버전마다 포지션이 적절하게 선언되었는가?

#### POSITION-1-1. 모든 버전마다 `own_vessel_count`만큼의 포지션이 선언되었는가?

- `available_positions`가 비어 있지 않은 version마다 선언 수가 정확히 맞아야 합니다.

#### POSITION-1-2. 모든 버전마다 `available_positions`에 포함된 포지션만이 선언되었는가?

- 선언한 `declared_position_no`는 해당 version의 `available_positions` 안에 있어야 합니다.

#### POSITION-2. 포지션이 선언된 버전이 실제로 존재하는가?

- 선언된 `lane_code`가 실제 lane이어야 합니다.
- 선언된 `proforma_name`이 해당 lane의 실제 version이어야 합니다.
- 해당 version은 실제로 선언 가능한 position을 가진 version이어야 합니다.

### Vessel View Validation

`validation.py`는 `instance_data.vessels`에 있는 각 선박마다 vessel view 검증을 수행합니다.

#### VESSEL-1. 시간 순서가 알맞게 되어 있는가?

#### VESSEL-1-1. 이벤트마다 시작 시간과 종료 시간이 올바르게 되어 있는가? `(시작 <= 종료)`

- 구간 이벤트의 시작 시각이 종료 시각보다 뒤면 안 됩니다.

#### VESSEL-1-2. 이벤트들이 시간 순서대로 나열되어 있으며 빈틈이 없는가? `(이전 이벤트 종료 = 다음 이벤트 시작)`

- 모든 인접 이벤트는 정확히 이어져야 합니다.

#### VESSEL-2. 이벤트 종류의 연속 관계가 적절한가?

현재 코드의 허용 전이 관계는 다음과 같습니다.

- `InLaneSail -> InLaneSail | TransshipmentUnload | PortStay | PhaseOut`
- `OutLaneSail -> OutLaneSail | PhaseIn | DryDock | Idle | Redelivery`
- `PortStay -> InLaneSail | PhaseOut | TransshipmentUnload`
- `PhaseIn -> InLaneSail | TransshipmentLoad | PortStay`
- `PhaseOut -> OutLaneSail | Idle | DryDock | Redelivery | PhaseIn`
- `TransshipmentUnload -> PhaseOut`
- `TransshipmentLoad -> InLaneSail | PortStay`
- `DryDock -> OutLaneSail | Idle | PhaseIn | Redelivery`
- `Idle -> OutLaneSail | PhaseIn | DryDock | Redelivery`
- `Delivery -> OutLaneSail | PhaseIn | DryDock | Idle | Redelivery`
- `Redelivery ->` 더 이상 어떤 이벤트도 올 수 없음

#### VESSEL-3. 선박의 일정 제약이 잘 지켜졌는가? (Delivery, Dry-Dock, Redelivery)

#### VESSEL-3-1. `PLANNING START` 이후 신조선 및 용선되는 선박은 반드시 `Delivery` 이벤트로 시작해야 한다.

- `available_from`이 planning horizon 시작 이후인 선박은 첫 이벤트가 `Delivery`여야 합니다.
- `Delivery`가 있다면 `delivery_time`, `delivery_port_code`가 vessel master의 값과 일치해야 합니다.

#### VESSEL-3-2. planning horizon 내 D/D 일정이 충족될 수 있는가?

- `next_dock_in`이 planning horizon 안에 있으면 해당 시각의 `DryDock` 이벤트가 반드시 있어야 합니다.
- 그 `DryDock` 이벤트의 `dock_in`, `dock_out`, `dock_port_code`는 vessel master와 일치해야 합니다.

#### VESSEL-3-3. `PLANNING END` 이후의 D/D 일정이 충족될 수 있는가?

- 현재 코드에 `TODO`로 남아 있습니다.

#### VESSEL-3-3. `PLANNING END` 이전에 반선 일정이 존재하는 경우 반드시 `Redelivery` 이벤트로 종료해야 한다.

- `available_to`가 planning horizon 안에 있으면 마지막 이벤트는 `Redelivery`여야 합니다.
- `Redelivery`가 있다면 `redelivery_time`, `redelivery_port_code`가 vessel master와 일치해야 합니다.

#### VESSEL-4. 항해 일정의 물리적 정합성: 이벤트의 시작 항구가 이전 이벤트의 종료 항구와 일치하는가?

- 인접한 두 이벤트에 대해 이전 이벤트의 종료 항구와 다음 이벤트의 시작 항구가 같아야 합니다.

#### VESSEL-5. InLane 이벤트 사이의 `lane-proforma-position`이 일관되는가?

여기서 InLane 이벤트란 `PhaseIn`, `InLaneSail`, `TransshipmentLoad`, `PortStay`, `TransshipmentUnload`, `PhaseOut`을 의미합니다.

#### VESSEL-5-1. InLane 이벤트 연속성 체크

- 선박의 모든 InLane 이벤트는 반드시 `PhaseIn`으로 시작해서 InLane 이벤트만 발생하다가 `PhaseOut`으로 끝나야 합니다.
- 다만 planning horizon 경계와 겹치는 경우에는 schedule의 시작이나 끝에서 `PhaseIn` 또는 `PhaseOut` 없이 잘릴 수 있습니다.
- 이 항목은 코드 주석상 `VESSEL-2`에서 함께 체크된다고 설명되어 있습니다.

#### VESSEL-5-2. InLane 이벤트의 `lane_code-proforma_name-position_no` 일관성 체크

- 연속한 두 InLane 이벤트 사이에서, 앞 이벤트가 `PhaseOut`이 아니면 `lane_code`, `proforma_name`, `position_no`가 모두 같아야 합니다.
- 앞 이벤트가 `PhaseOut`이면 다음 이벤트는 사실상 `PhaseIn`이어야 하며, 두 이벤트의 `lane/proforma/position`이 완전히 같으면 불필요한 `PhaseOut -> PhaseIn`으로 간주되어 불허됩니다.

#### VESSEL-6. 항해 이벤트의 속력이 물리적으로 가능한 범위 내에 있는가? `(< 20 knot)`

- `InLaneSail`, `OutLaneSail`에 대해 거리와 시간으로 계산한 속력이 20 knot를 넘으면 안 됩니다.

### Lane View Validation

lane view 검증은 각 `lane_code / proforma_name / position_no` 조합마다, 모든 선박의 InLane 이벤트를 모아 시간순으로 정렬한 뒤 수행됩니다.

#### LANE-1. 모든 이벤트의 순서가 적절한가?

#### LANE-1-1. 이벤트 간의 연속 관계가 적절한가?

현재 lane view에서 허용되는 전이 관계는 다음과 같습니다.

- `InLaneSail -> InLaneSail | TransshipmentUnload | PortStay`
- `PortStay -> InLaneSail | PhaseOut | TransshipmentUnload`
- `PhaseIn -> InLaneSail | TransshipmentLoad | PortStay`
- `PhaseOut -> PhaseIn`
- `TransshipmentUnload -> PhaseOut`
- `TransshipmentLoad -> InLaneSail | PortStay`

#### LANE-1-2. 선박의 변경이 발생한 경우 TS Chain이 존재해야 한다.

코드에서 TS Chain은 아래 연속 발생을 뜻합니다.

- `TransshipmentUnload -> PhaseOut -> PhaseIn -> TransshipmentLoad`

검증 규칙:

- lane view에서 연속 이벤트 사이에 담당 선박이 바뀌면, 그 변경은 반드시 TS Chain 안에서 일어나야 합니다.

#### LANE-1-3. TS Chain이 존재하는 경우 반드시 선박이 변경되어야 한다.

- TS Chain이 있는데 나가는 선박과 들어오는 선박이 같으면 안 됩니다.

#### LANE-1-4. `TransshipmentUnload`와 `TransshipmentLoad`는 TS Chain 속에서만 발생할 수 있고, `PhaseIn`과 `PhaseOut`은 TS Chain 또는 `lane_events`의 처음과 끝에서만 발생할 수 있다.

- `TransshipmentUnload`, `TransshipmentLoad`는 TS Chain 바깥에 있으면 안 됩니다.
- `PhaseIn`은 TS Chain 바깥이라면 lane event의 첫 이벤트여야 합니다.
- `PhaseOut`은 TS Chain 바깥이라면 lane event의 마지막 이벤트여야 합니다.

#### LANE-2. 모든 선박은 요구 서비스 기간 동안 이벤트가 빠짐 없이 존재하는가?

`validation.py`는 service start/end와 port rotation을 이용해 expected port call을 생성하고, 이를 기준으로 coverage를 검증합니다.

#### LANE-2-1. 첫 이벤트 기간에 `test_start`가 걸쳐 있어야 한다.

- 첫 번째 구간 이벤트가 coverage 시작 시점을 포함해야 합니다.

#### LANE-2-2. 마지막 이벤트 기간에 `test_end`가 걸쳐 있어야 한다.

- 마지막 구간 이벤트가 coverage 종료 시점을 포함해야 합니다.

#### LANE-2-3. 모든 이벤트는 시간적으로 연속되어야 한다. TS의 경우 `[1일, 7일]`의 빈틈이 있어야 한다.

- 일반 연속 이벤트는 이전 종료 시각과 다음 시작 시각이 정확히 같아야 합니다.
- TS Chain에서는 `TransshipmentUnload` 종료와 `PhaseOut` 시작이 같아야 하고, `PhaseIn` 종료와 `TransshipmentLoad` 시작이 같아야 합니다.
- 또한 `PhaseOut`과 `PhaseIn` 사이의 간격은 1일 이상 7일 이하여야 합니다.

#### LANE-3. 모든 기항 일정이 `PortStay` 이벤트로 정확히 충족되어 있는가?

- validation이 생성한 expected port call과 실제 `PortStay` 이벤트의 `port_code`, `port_seq`, `pilot_in_start`, `berthing_start`, `berthing_end`, `pilot_out_end`가 모두 일치해야 합니다.
- expected port call 개수와 실제 `PortStay` 개수도 같아야 합니다.

#### LANE-4. 모든 현행 Lane은 Current Assignment 정보대로의 선박으로 시작하는가?

- 현재 assignment 정보가 있는 position은 첫 lane event의 선박이 그 assigned vessel이어야 합니다.

#### LANE-5. 모든 화물이 시간 및 물리적 일관성 속에서 항로를 순회하고 있는가?

- 현재 코드에 `TODO`로 남아 있습니다.

#### LANE-6. 할당된 선박은 모두 선복 제약 조건을 만족하는가?

- `virtual_vessel_schedules`에 들어 있는 선박 코드는 이 검증에서 제외됩니다.
- 실제 선박에 대해서는 `capacity_teu`가 `required_capacity_teu`의 5% 허용 범위 안에 있어야 합니다.
- `reefer_plug`도 `required_reefer_plug` 기준 허용 범위 안에 있어야 합니다.

## 최소 빈 해답 예시

부트스트랩 단계에서 유효한 placeholder가 필요하다면 아래처럼 만들 수 있습니다.

```python
from ocam.models import CascadingSolution

solution = CascadingSolution(
    declared_positions=[],
    vessel_schedules={},
    virtual_vessel_schedules={},
    num_virtual_vessels_used=0,
)
```

## 예시 스켈레톤

```python
from ocam.models import CascadingSolution, DeclaredPosition, VesselSchedule, PhaseIn

solution = CascadingSolution(
    declared_positions=[
        DeclaredPosition("LANE1", "PF_A", 1),
    ],
    vessel_schedules={
        "VESSEL001": [
            PhaseIn(
                lane_code="LANE1",
                proforma_name="PF_A",
                position_no=1,
                phase_in_port_code="KRPUS",
                phase_in_port_seq=1,
                phase_in_time=start_time,
            )
        ]
    },
    virtual_vessel_schedules={},
    num_virtual_vessels_used=0,
)
```

## 로그와 디버깅

알고리즘 안에서 사용한 `print()` 출력은 오케스트레이션 레이어가 캡처해서 실행 로그에 저장합니다.

따라서 아래 정도의 가벼운 런타임 로그는 유용합니다.

- 주요 단계 시작/종료
- 선택된 파라미터
- 생성한 vessel, schedule, event 개수
- 조기 종료나 fallback의 이유

다만 특정 버그를 추적하는 중이 아니라면, 이벤트 단위의 과도한 로그는 피하는 것이 좋습니다.

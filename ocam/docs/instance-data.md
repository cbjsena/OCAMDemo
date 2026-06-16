# InstanceData 계약

`InstanceData`는 모든 알고리즘에 첫 번째 인자로 전달되는 전처리 완료 입력 객체입니다.

알고리즘은 이 객체를 표준 입력으로 사용해야 하며, 원본 CSV 파일을 다시 열어 읽는 방식에 의존하지 않는 것이 원칙입니다.

## 최상위 필드

현재 `InstanceData`에는 아래와 같은 최상위 필드가 있습니다.

- `planning_horizon`
- `service_lanes`
- `vessels`
- `distances`
- `canal_fee`
- `canal_direction`
- `bunker_consumption_port`
- `bunker_consumption_sea`
- `bunker_price`
- `transshipment_cost`
- `opportunity_cost`

원본 입력 묶음은 `instance_data.raw`에도 남아 있지만, 연구 알고리즘은 가능하면 위의 정규화된 필드를 기준으로 구현하는 것이 좋습니다.

## planning_horizon

`planning_horizon`은 아래와 같은 dict입니다.

```python
{
    "start": datetime,
    "end": datetime,
}
```

이 값은 YAML 설정 파일에서 들어오며, 전처리 전에 주입됩니다.

알고리즘은 이 기간을 기준으로 다음을 판단해야 합니다.

- 어떤 lane version이 의미 있는지
- 어떤 스케줄을 잘라야 하거나 연장해야 하는지
- 어떤 의사결정이 계획 구간 안에 속하는지

## service_lanes

대부분의 알고리즘에서 가장 중요한 필드는 `service_lanes`입니다.

이 필드는 lane dict의 리스트이며, 각 lane은 `lane_code`와 `versions` 리스트를 가집니다.

개념적으로는 아래와 같은 형태입니다.

```python
{
    "lane_code": str,
    "versions": [
        {
            "proforma_name": str,
            "effective_from": datetime,
            "effective_to": datetime | None,
            "anchor_date": datetime,
            "service_duration": int,
            "declared_count": int,
            "declared_positions": list[int],
            "available_positions": list[int],
            "required_reefer_plug": int,
            "vessel_assignments": [
                {
                    "position_no": int,
                    "vessel_code": str,
                }
            ],
            "port_rotation": [
                {
                    "port_code": str,
                    "port_seq": int,
                    "eta_offset_minutes": int,
                    "etb_offset_minutes": int,
                    "etd_offset_minutes": int,
                    "pilot_in_minutes": int,
                    "pilot_out_minutes": int,
                    "berthing_minutes": int,
                    "in_port_minutes": int,
                }
            ],
        }
    ],
}
```

## 전처리 단계

전처리 단계는 알고리즘이 실행되기 전에 몇 가지 도메인 가정을 이미 강제합니다. 알고리즘 작성자는 이 규칙을 알아야, 오류가 왜 알고리즘 실행 전에 나는지 이해할 수 있습니다.

- planning horizon과 겹치는 lane version만 유지됩니다.
- 미래 lane version에는 declared position이 있으면 안 됩니다.
- 미래 lane version에는 vessel assignment가 있으면 안 됩니다.
- 현재 운영 중인 lane version에는 declared position이 반드시 있어야 합니다.
- 현재 운영 중인 lane version에는 vessel assignment가 반드시 있어야 합니다.

이 규칙들은 [ocam/preprocessing.py](../ocam/preprocessing.py)에 구현되어 있습니다.

## vessels

`vessels`는 전처리에서 정리된 vessel 마스터 데이터 리스트입니다. `lookup_vessel()` 같은 helper 함수도 이 데이터를 기준으로 동작합니다.

특정 vessel 필드를 코드에서 직접 쓰기 전에, 실제 한 행을 출력하거나 디버거로 확인한 뒤 그 가정을 문서나 주석에 남기는 것을 권장합니다.

## distances

`distances`는 `ocam.utils.lookup_distance(from_port_code, to_port_code)`가 사용하는 거리 데이터의 기반입니다.

실무적으로는 아래를 권장합니다.

- 알고리즘 안에서 별도 거리 조회 로직을 다시 만들기보다 `lookup_distance()`를 우선 사용합니다.
- 이 유틸리티는 direct lookup, 일부 alias fallback, 그래프 기반 fallback을 이미 처리합니다.
- alias fallback은 물리적으로 동일한 항구 EGSUZ/EGSCA가 이름이 달라 한쪽에만 정의된 거리가 있는 경우에 대응합니다.
- Fallback 처리는 입력된 port to port 거리가 존재하지 않는 경우에는 그래프에서의 최단 거리를 구해 반환합니다.

## 비용 관련 테이블

아래 필드들은 정규화된 row 리스트로 제공됩니다.

- `canal_fee`
- `canal_direction`
- `bunker_consumption_port`
- `bunker_consumption_sea`
- `bunker_price`
- `transshipment_cost`
- `opportunity_cost`

from __future__ import annotations

from ocam.models import CascadingSolution, InstanceData

DESCRIPTION = "Copy this directory to bootstrap a new algorithm package."


def algorithm(instance_data: InstanceData, timelimit: int) -> CascadingSolution:
    """
    모든 알고리즘 패키지가 따라야 하는 최소 계약.

    입력:
    - instance_data: 전처리로 만들어진 정규화 입력 객체
    - timelimit: config.yaml에서 전달된 정수 시간 제한

    규칙:
    - 인자는 정확히 InstanceData와 timelimit만 받는다.
    - 반환값은 반드시 CascadingSolution이어야 한다.
    - 여기서 원본 파일을 다시 읽지 않는다. 로딩과 전처리는 OCAM이 이미 수행한다.
    - 로그는 캡처되어 저장되므로, 너무 장황하지 않게 남기는 것이 좋다.
    """

    print("template_algorithm: template package invoked")
    print(f"template_algorithm: received timelimit={timelimit}")

    # TODO: Replace this example return value with the real algorithm output.
    return CascadingSolution(
        declared_positions=[],
        vessel_schedules={},
        virtual_vessel_schedules={},
        num_virtual_vessels_used=0,
    )

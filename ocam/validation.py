from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

from ocam.models import *
from ocam.utils import *


# InfeasibleError 정의
class InfeasibleError(Exception):
    """Raised when a solution is found to be infeasible during validation."""

    pass


ALLOWED_NEXT_EVENT_TYPES: dict[type[VesselScheduleEvent], tuple[type[VesselScheduleEvent], ...]] = {
    InLaneSail: (InLaneSail, TransshipmentUnload, PortStay, PhaseOut),
    OutLaneSail: (OutLaneSail, CanalPassage, PhaseIn, DryDock, Idle, Redelivery),
    CanalPassage: (OutLaneSail,),
    PortStay: (InLaneSail, PhaseOut, TransshipmentUnload),
    PhaseIn: (InLaneSail, TransshipmentLoad, PortStay),
    PhaseOut: (OutLaneSail, Idle, DryDock, Redelivery, PhaseIn),
    TransshipmentUnload: (PhaseOut),
    TransshipmentLoad: (InLaneSail, PortStay),
    DryDock: (OutLaneSail, Idle, PhaseIn, Redelivery),
    Idle: (
        OutLaneSail,
        PhaseIn,
        DryDock,
        Redelivery,
    ),
    Delivery: (OutLaneSail, PhaseIn, DryDock, Idle, Redelivery),
    Redelivery: tuple(),  # Redelivery 이후에는 어떤 이벤트도 올 수 없다. (스케줄 종료)
}


LANE_VIEW_ALLOWED_NEXT_EVENT_TYPES: dict[type[InLaneEvent], tuple[type[InLaneEvent], ...]] = {
    InLaneSail: (InLaneSail, TransshipmentUnload, PortStay),
    PortStay: (InLaneSail, PhaseOut, TransshipmentUnload),
    PhaseIn: (InLaneSail, TransshipmentLoad, PortStay),
    PhaseOut: (PhaseIn,),
    TransshipmentUnload: (PhaseOut),
    TransshipmentLoad: (InLaneSail, PortStay),
}


def _allowed_next_event_types(
    prev_event: VesselScheduleEvent,
) -> tuple[type[VesselScheduleEvent], ...]:
    for event_type in type(prev_event).__mro__:
        if event_type in ALLOWED_NEXT_EVENT_TYPES:
            return ALLOWED_NEXT_EVENT_TYPES[event_type]
    return tuple()


def _lane_view_allowed_next_event_types(
    prev_event: InLaneEvent,
) -> tuple[type[InLaneEvent], ...]:
    for event_type in type(prev_event).__mro__:
        if event_type in LANE_VIEW_ALLOWED_NEXT_EVENT_TYPES:
            return LANE_VIEW_ALLOWED_NEXT_EVENT_TYPES[event_type]
    return tuple()


def can_follow_event(prev_event: VesselScheduleEvent, next_event: VesselScheduleEvent) -> bool:
    if not isinstance(prev_event, VesselScheduleEvent):
        raise TypeError(f"prev_event must be VesselScheduleEvent, got {type(prev_event)!r}")
    if not isinstance(next_event, VesselScheduleEvent):
        raise TypeError(f"next_event must be VesselScheduleEvent, got {type(next_event)!r}")

    allowed_next_types = _allowed_next_event_types(prev_event)
    return isinstance(next_event, allowed_next_types)


def can_follow_lane_view_event(prev_event: InLaneEvent, next_event: InLaneEvent) -> bool:
    if not isinstance(prev_event, InLaneEvent):
        raise TypeError(f"prev_event must be InLaneEvent, got {type(prev_event)!r}")
    if not isinstance(next_event, InLaneEvent):
        raise TypeError(f"next_event must be InLaneEvent, got {type(next_event)!r}")

    allowed_next_types = _lane_view_allowed_next_event_types(prev_event)
    return isinstance(next_event, allowed_next_types)


def validate_solution(solution: CascadingSolution, instance_data: InstanceData) -> None:
    """TODO"""

    init_utils(instance_data)
    all_vessel_schedules = solution.all_vessel_schedules
    lane_view = solution.to_lane_view()
    actual_vessel_codes = {vessel["vessel_code"] for vessel in instance_data.vessels}
    # Backward compatibility:
    # older algorithms may still place virtual vessels inside vessel_schedules.
    # Validation should treat any schedule not backed by instance_data.vessels
    # as a virtual vessel as well.
    virtual_vessel_codes = set(solution.virtual_vessel_schedules).union(set(all_vessel_schedules) - actual_vessel_codes)

    PLANNING_START = instance_data.planning_horizon["start"]
    PLANNING_END = instance_data.planning_horizon["end"]

    # 운하 데이터에서는 EGSUZ가 들어올 수 있지만 계산/검증 기준 항구 코드는 EGSCA로 통일한다.
    def _canonical_canal_port_code(port_code: str) -> str:
        return "EGSCA" if port_code == "EGSUZ" else port_code

    def _is_canal_port_code(port_code: str) -> bool:
        return _canonical_canal_port_code(port_code) in {"EGSCA", "PAPCA"}

    canal_passage_hours_by_key = {
        (_canonical_canal_port_code(row["port_code"]), row["direction"]): float(row["passage_hours"])
        for row in instance_data.canal_passage_time
    }
    canal_direction_by_key = {
        (
            _canonical_canal_port_code(row["from_port_code"]),
            _canonical_canal_port_code(row["canal_port_code"]),
            _canonical_canal_port_code(row["to_port_code"]),
        ): row["direction"]
        for row in instance_data.canal_direction
    }

    # VesselSchedule: vessel view 정합성 및 vessel에 대한 hard constraints 충족 여부 확인
    # LaneView 정합성 및 lane에 대한 hard constraints 충족 여부 확인
    # 기타 필요한 정보 검증...

    # region Declared Positions Validation
    # 포지션 선언이 필요한 lane 정보 수집
    new_proformas = []
    for lane in instance_data.service_lanes:
        lane_code = lane["lane_code"]
        for version in lane["versions"]:
            available_positions = version["available_positions"]
            if len(available_positions) > 0:
                new_proformas.append((lane_code, version))

    # DeclaredPosition을 version별로 분류
    declared_positions_by_version: dict[tuple[str, str], list[DeclaredPosition]] = {}
    for position in solution.declared_positions:
        key = (position.lane_code, position.proforma_name)
        if key not in declared_positions_by_version:
            declared_positions_by_version[key] = []
        declared_positions_by_version[key].append(position)

    # POSITION-1. 모든 버전마다 포지션이 적절하게 선언되었는가?
    # POSITION-1-1. 모든 버전마다 own_vessel_count만큼의 포지션이 선언되었는가?
    # POSITION-1-2. 모든 버전마다 available_positions에 포함된 포지션만이 선언되었는가?
    for lane_code, version in new_proformas:
        proforma_name = version["proforma_name"]
        own_vessel_count = version["own_vessel_count"]
        available_positions = version["available_positions"]
        declared_positions = declared_positions_by_version.get((lane_code, proforma_name), [])

        if len(declared_positions) != own_vessel_count:
            raise InfeasibleError(
                f"Lane {lane_code} proforma {proforma_name} requires {own_vessel_count} declared positions, "
                f"but {len(declared_positions)} were declared: {declared_positions!r}."
            )

        if not all(position.declared_position_no in available_positions for position in declared_positions):
            raise InfeasibleError(
                f"Lane {lane_code} proforma {proforma_name} has declared positions with invalid position numbers: "
                f"available positions are {available_positions}, but declared positions are {declared_positions!r}."
            )

    # POSITION-2. 포지션이 선언된 버전이 실제로 존재하는가? (존재하지 않는 버전에 포지션이 선언되어 있으면 안됨)
    for position in solution.declared_positions:
        lane_code = position.lane_code
        proforma_name = position.proforma_name
        lane = next(
            (lane for lane in instance_data.service_lanes if lane["lane_code"] == lane_code),
            None,
        )
        if lane is None:
            raise InfeasibleError(f"Declared position {position!r} references non-existent lane {lane_code}.")
        version = next(
            (version for version in lane["versions"] if version["proforma_name"] == proforma_name),
            None,
        )
        if version is None:
            raise InfeasibleError(
                f"Declared position {position!r} references non-existent proforma {proforma_name} in lane {lane_code}."
            )
        if version["available_positions"] is None or len(version["available_positions"]) == 0:
            raise InfeasibleError(
                f"Declared position {position!r} references lane {lane_code} proforma {proforma_name} that does not allow any positions."
            )
    # endregion

    # region Vessel View Validation
    for vessel in instance_data.vessels:
        vessel_code = vessel["vessel_code"]
        if vessel_code not in solution.vessel_schedules.keys():
            raise InfeasibleError(f"Vessel {vessel_code} is missing from solution vessel schedules.")

    for vessel_code, schedules in all_vessel_schedules.items():
        vessel = lookup_vessel(vessel_code) if vessel_code not in virtual_vessel_codes else None

        # 모든 선박은 스케줄이 존재해야 한다. (스케줄이 없는 선박은 적어도 Idle이벤트라도 있어야 함)
        if len(schedules) == 0:
            raise InfeasibleError(f"Vessel {vessel_code} has no schedule.")

        # VESSEL-1. 시간 순서가 알맞게 되어 있는가?
        # VESSEL-1-1. 이벤트마다 시작 시간과 종료 시간이 올바르게 되어 있는가? (시작 <= 종료)
        for event in schedules:
            if is_interval_event(event):
                start_time = event_start_time(event)
                end_time = event_end_time(event)
                if start_time > end_time:
                    raise InfeasibleError(f"Vessel {vessel_code} has invalid event with negative duration: {event!r}.")

        # VESSEL-1-2. 이벤트들이 시간 순서대로 나열되어 있으며 빈틈이 없는가? (이전 이벤트 종료 = 다음 이벤트 시작)
        for prev_event, next_event in zip(schedules, schedules[1:]):
            prev_end_time = event_end_time(prev_event)
            next_start_time = event_start_time(next_event)
            if prev_end_time != next_start_time:
                raise InfeasibleError(
                    f"Vessel {vessel_code} has non-continuous schedule with time gap between events: "
                    f"previous event {prev_event!r} ends at {prev_end_time} but next event {next_event!r} starts at {next_start_time}."
                )

        # VESSEL-1-3. 선박 스케줄은 PLANNING_START부터 PLANNING_END까지의 기간을 완전히 커버해야 한다. (비어있다면 Idle 이벤트라도 있어야 함)
        if vessel is not None:
            required_start = (
                max(PLANNING_START, vessel["available_from"]) if vessel["available_from"] else PLANNING_START
            )
            required_end = min(PLANNING_END, vessel["available_to"]) if vessel["available_to"] else PLANNING_END
            first_event = schedules[0]
            last_event = schedules[-1]
            first_start_time = event_start_time(first_event)
            last_end_time = event_end_time(last_event)

            if first_start_time > required_start:
                print(
                    vessel_code,
                    vessel["available_from"],
                    vessel["available_from_port_code"],
                    vessel["available_to"],
                    vessel["available_to_port_code"],
                )
                raise InfeasibleError(
                    f"Vessel {vessel_code} does not cover its required start time: "
                    f"required_start={required_start}, first event {first_event!r} starts at {first_start_time}."
                )
            if last_end_time < required_end:
                raise InfeasibleError(
                    f"Vessel {vessel_code} does not cover its required end time: "
                    f"required_end={required_end}, last event {last_event!r} ends at {last_end_time}."
                )

        # VESSEL-1-4. 선박 스케줄은 Planning Horizon을 벗어나는 이벤트를 포함할 수 없다.
        if vessel is not None:
            for event in schedules:
                if event_end_time(event) < PLANNING_START and not isinstance(event, (Delivery, DryDock)):
                    raise InfeasibleError(
                        f"Vessel {vessel_code} has event {event!r} that ends before planning horizon starts: "
                        f"event ends at {event_end_time(event)}, but planning horizon starts at {PLANNING_START}."
                    )
                if event_start_time(event) > PLANNING_END:
                    raise InfeasibleError(
                        f"Vessel {vessel_code} has event {event!r} that starts after planning horizon ends: "
                        f"event starts at {event_start_time(event)}, but planning horizon ends at {PLANNING_END}."
                    )

        # VESSEL-2. 이벤트 종류의 연속 관계가 적절한가? (예: InLaneSail 다음에는 InLaneSail, TransshipmentUnload, PortStay, PhaseOut만 올 수 있다.)
        for prev_event, next_event in zip(schedules, schedules[1:]):
            if not can_follow_event(prev_event, next_event):
                raise InfeasibleError(
                    f"Vessel {vessel_code} has invalid event sequence: {prev_event!r} cannot be followed by {next_event!r}."
                )

        # VESSEL-3. 선박의 일정 제약이 잘 지켜졌는가?
        if vessel is not None:
            # VESSEL-3-1. PLANNING START 이후 신조선 및 용선되는 선박은 반드시 Delivery 이벤트로 시작해야 한다.
            if vessel["available_from"] is not None:
                available_from = vessel["available_from"]
                available_from_port_code = vessel["available_from_port_code"]
                first_event = schedules[0]
                if available_from >= PLANNING_START and not isinstance(first_event, Delivery):
                    raise InfeasibleError(
                        f"Vessel {vessel_code} is newly built or chartered after planning start but does not start with Delivery event: first event is {first_event!r}."
                    )
                if isinstance(first_event, Delivery):
                    if first_event.delivery_time != available_from:
                        raise InfeasibleError(
                            f"Vessel {vessel_code} has Delivery event with delivery_time {first_event.delivery_time} that does not match available_from {available_from}."
                        )
                    if first_event.delivery_port_code != available_from_port_code:
                        raise InfeasibleError(
                            f"Vessel {vessel_code} has Delivery event with delivery_port_code {first_event.delivery_port_code} that does not match available_from_port_code {available_from_port_code}."
                        )

            # VESSEL-3-2. planning horizon 내 D/D 일정이 충족될 수 있는가?
            next_dock_in = (
                vessel["next_dock_in"]
                if vessel["next_dock_in"] is not None and vessel["next_dock_in"] <= PLANNING_END
                else None
            )
            next_dock_out = (
                vessel["next_dock_out"]
                if vessel["next_dock_in"] is not None and vessel["next_dock_in"] <= PLANNING_END
                else None
            )
            next_dock_port_code = vessel["next_dock_port_code"]
            if next_dock_in is not None:
                dock_event = next(
                    (event for event in schedules if isinstance(event, DryDock) and event.dock_in == next_dock_in),
                    None,
                )
                if dock_event is None:
                    raise InfeasibleError(
                        f"Vessel {vessel_code} has next_dock_in {next_dock_in} but no matching DryDock event in schedule."
                    )
                if (
                    (dock_event.dock_in != next_dock_in)
                    or (dock_event.dock_port_code != next_dock_port_code)
                    or (dock_event.dock_out != next_dock_out)
                ):
                    raise InfeasibleError(
                        f"Vessel {vessel_code} has DryDock event {dock_event!r} that does not match next_dock_in {next_dock_in}, "
                        f"next_dock_port_code {next_dock_port_code}, next_dock_out {next_dock_out}."
                    )

            # VESSEL-3-3. PLANNING END 이후의 D/D 일정이 충족될 수 있는가?
            # TODO

            # VESSEL-3-3. PLNNING END 이전에 반선 일정이 존재하는 경우 반드시 Redelivery 이벤트로 종료해야 한다.
            if vessel["available_to"] is not None:
                available_to = vessel["available_to"]
                available_to_port_code = vessel["available_to_port_code"]
                last_event = schedules[-1]
                if available_to <= PLANNING_END and not isinstance(last_event, Redelivery):
                    raise InfeasibleError(
                        f"Vessel {vessel_code} is chartered until before planning end but does not end with Redelivery event: last event is {last_event!r}."
                    )
                if isinstance(last_event, Redelivery):
                    if last_event.redelivery_time != available_to:
                        raise InfeasibleError(
                            f"Vessel {vessel_code} has Redelivery event with redelivery_time {last_event.redelivery_time} that does not match available_to {available_to}."
                        )
                    if last_event.redelivery_port_code != available_to_port_code:
                        raise InfeasibleError(
                            f"Vessel {vessel_code} has Redelivery event with redelivery_port_code {last_event.redelivery_port_code} that does not match available_to_port_code {available_to_port_code}."
                        )

        # VESSEL-4. 항해 일정의 물리적 정합성: 이벤트의 시작 항구가 이전 이벤트의 종료 항구와 일치하는가?
        for prev_event, next_event in zip(schedules, schedules[1:]):
            prev_end_port_code = event_end_port_code(prev_event)
            next_start_port_code = event_start_port_code(next_event)
            if prev_end_port_code != next_start_port_code:
                raise InfeasibleError(
                    f"Vessel {vessel_code} has physically infeasible schedule with mismatched ports between events: {prev_event!r} ends at {prev_end_port_code} but next event {next_event!r} starts at {next_start_port_code}."
                )

        # VESSEL-5. InLane 이벤트 사이의 lane-proforma-position이 일관되는가?
        # Note: 여기서 InLane 이벤트란 서비스 항로에 할당된 상태에서 발생하는 VesselScheduleEvent를 의미함: PhaseIn, InLaneSail, TransshipmentLoad, TransshipmentUnload, PhaseOut.
        # VESSEL-5-1. InLane 이벤트 연속성 체크: 선박의 모든 InLane 이벤트는 반드시 PhaseIn으로 시작해서 InLane 이벤트만 발생하다가 PhaseOut으로 끝나야 한다. 단, PLANNING START/END와 겹치는 경우(InLane 이벤트가 scheduls의 0, -1 인덱스에 위치한 경우)는 PhaseIn/Out 없이 연속되기만 하면 된다.
        # Note: 이것은 VESSEL-2 항목에서 체크됨.
        # VESSEL-5-2. InLane 이벤트의 lane_code-proforma_name-position_no 일관성 체크
        for prev_event, next_event in zip(schedules, schedules[1:]):
            prev_inlane = isinstance(prev_event, InLaneEvent)
            next_inlane = isinstance(next_event, InLaneEvent)
            if not prev_inlane and not next_inlane:
                continue  # 둘 다 InLane 이벤트가 아니면 건너뛴다.

            elif prev_inlane and next_inlane:
                # 앞 이벤트가 PhaseOut이 아닌 경우
                if not isinstance(prev_event, PhaseOut):
                    if (
                        (prev_event.lane_code != next_event.lane_code)
                        or (prev_event.proforma_name != next_event.proforma_name)
                        or (prev_event.position_no != next_event.position_no)
                    ):
                        raise InfeasibleError(
                            f"Vessel {vessel_code} has inconsistent lane/proforma/position between consecutive InLane events: {prev_event!r} and {next_event!r}."
                        )
                else:
                    # 앞 이벤트가 PhaseOut인 경우 VESSEL-2로부터 뒤 이벤트는 PhaseIn일수밖에 없음
                    # 같은 항구에서 다른 항로로 재배치하는 경우를 표현하기 위해 PO->PI가 가능함. 불필요한 솔루션 길이를 줄이기 위해 두 이벤트의 항로 정보는 달라야만 함.
                    if (
                        (prev_event.lane_code == next_event.lane_code)
                        and (prev_event.proforma_name == next_event.proforma_name)
                        and (prev_event.position_no == next_event.position_no)
                    ):
                        raise InfeasibleError(
                            f"Vessel {vessel_code} has redundant PhaseOut->PhaseIn with identical lane/proforma/position: {prev_event!r} and {next_event!r}."
                        )

        # VESSEL-6. 항해 이벤트의 속력이 물리적으로 가능한 범위 내에 있는가? (<= 20knot)
        for event_index, event in enumerate(schedules):
            if isinstance(event, (InLaneSail, OutLaneSail)):
                distance = lookup_distance(event.from_port_code, event.to_port_code)
                duration_hours = (event_end_time(event) - event_start_time(event)).total_seconds() / 3600
                if duration_hours <= 0:
                    raise InfeasibleError(
                        f"Vessel {vessel_code} has sailing event with non-positive duration: {event!r}."
                    )
                speed = distance / duration_hours
                if speed > 20:
                    raise InfeasibleError(
                        f"Vessel {vessel_code} has sailing event with unrealistic speed {speed:.2f} knots: {event!r}."
                    )

            # VESSEL-7. CanalPassage는 반드시 OutLaneSail(A->C), CanalPassage(C), OutLaneSail(C->B) 구조를 가져야 한다.
            # CanalPassage의 route metadata는 fee/direction lookup에 쓰이므로 앞뒤 항해 이벤트와 불일치하면 안 된다.
            if isinstance(event, CanalPassage):
                if event_index == 0 or event_index == len(schedules) - 1:
                    raise InfeasibleError(
                        f"Vessel {vessel_code} has CanalPassage without adjacent sailing events: {event!r}."
                    )
                prev_event = schedules[event_index - 1]
                next_event = schedules[event_index + 1]
                if not isinstance(prev_event, OutLaneSail) or not isinstance(next_event, OutLaneSail):
                    raise InfeasibleError(
                        "Vessel "
                        f"{vessel_code} has CanalPassage that is not bracketed by OutLaneSail events: "
                        f"previous={prev_event!r}, canal={event!r}, next={next_event!r}."
                    )
                canal_port_code = _canonical_canal_port_code(event.canal_port_code)
                if _canonical_canal_port_code(prev_event.to_port_code) != canal_port_code:
                    raise InfeasibleError(
                        "Vessel "
                        f"{vessel_code} has CanalPassage whose canal port does not match the previous sail arrival: "
                        f"previous={prev_event!r}, canal={event!r}."
                    )
                if _canonical_canal_port_code(next_event.from_port_code) != canal_port_code:
                    raise InfeasibleError(
                        "Vessel "
                        f"{vessel_code} has CanalPassage whose canal port does not match the next sail departure: "
                        f"canal={event!r}, next={next_event!r}."
                    )
                if event.from_port_code != prev_event.from_port_code or event.to_port_code != next_event.to_port_code:
                    raise InfeasibleError(
                        "Vessel "
                        f"{vessel_code} has CanalPassage route metadata that does not match adjacent sails: "
                        f"previous={prev_event!r}, canal={event!r}, next={next_event!r}."
                    )
                expected_direction = canal_direction_by_key.get(
                    (
                        _canonical_canal_port_code(prev_event.from_port_code),
                        canal_port_code,
                        _canonical_canal_port_code(next_event.to_port_code),
                    )
                )
                if expected_direction is None:
                    raise InfeasibleError(
                        "Missing canal direction for "
                        f"{prev_event.from_port_code}->{canal_port_code}->{next_event.to_port_code}."
                    )
                if expected_direction != event.direction:
                    raise InfeasibleError(
                        "Vessel "
                        f"{vessel_code} has CanalPassage direction mismatch for "
                        f"{prev_event.from_port_code}->{canal_port_code}->{next_event.to_port_code}: "
                        f"event={event.direction!r}, expected={expected_direction!r}."
                    )
                expected_hours = canal_passage_hours_by_key.get((canal_port_code, event.direction))
                if expected_hours is None:
                    raise InfeasibleError(
                        "Missing canal passage time for "
                        f"canal_port_code={canal_port_code!r}, direction={event.direction!r}."
                    )
                actual_duration = event.passage_end - event.passage_start
                expected_duration = timedelta(hours=expected_hours)
                if actual_duration != expected_duration:
                    duration_hours = actual_duration.total_seconds() / 3600
                    raise InfeasibleError(
                        f"Vessel {vessel_code} has CanalPassage duration {duration_hours}h, "
                        f"but expected {expected_hours}h for {canal_port_code}/{event.direction}: {event!r}."
                    )

        # VESSEL-8. OutLaneSail 두 개가 운하 항구에서 바로 맞닿는 암묵적 운하 표현은 허용하지 않는다.
        # 운하 비용과 통과시간은 CanalPassage 이벤트가 있을 때만 검증/평가된다.
        for prev_event, next_event in zip(schedules, schedules[1:]):
            if (
                isinstance(prev_event, OutLaneSail)
                and isinstance(next_event, OutLaneSail)
                and _is_canal_port_code(prev_event.to_port_code)
                and _canonical_canal_port_code(prev_event.to_port_code)
                == _canonical_canal_port_code(next_event.from_port_code)
            ):
                raise InfeasibleError(
                    "Vessel "
                    f"{vessel_code} has adjacent OutLaneSail events through canal port "
                    f"{prev_event.to_port_code}; explicit CanalPassage is required between them."
                )

    # endregion

    # region Lane View Validation
    for lane in instance_data.service_lanes:
        lane_code = lane["lane_code"]
        for version in lane["versions"]:
            proforma_name = version["proforma_name"]

            declared_positions = (
                version["declared_positions"]
                if len(version["declared_positions"]) > 0
                else [x.declared_position_no for x in declared_positions_by_version[(lane_code, proforma_name)]]
            )

            for position_no in declared_positions:
                # lane-proforma-position에 관련한 모든 이벤트를 수집하고, 시간순으로 정렬한다.
                lane_events = lane_view.get((lane_code, proforma_name, position_no), [])

                if len(lane_events) == 0:
                    raise InfeasibleError(
                        f"Lane {lane_code} proforma {proforma_name} position {position_no} has no events in vessel schedules."
                    )

                # LANE-1. 모든 이벤트의 순서가 적절한가?
                # LANE-1-1. 이벤트 간의 연속 관계가 적절한가?
                for prev_event, next_event in zip(lane_events, lane_events[1:]):
                    if not can_follow_lane_view_event(prev_event.event, next_event.event):
                        raise InfeasibleError(
                            f"Lane {lane_code} proforma {proforma_name} position {position_no} has invalid event sequence: "
                            f"{prev_event!r} cannot be followed by {next_event!r} in lane view."
                        )

                # DEFINE: TS Chain = TransshipmentUnload->PhaseOut->PhaseIn->TransshipmentLoad의 연속적 발생
                # LANE-1-2. 선박의 변경이 발생한 경우 TS Chain이 존재해야 한다. (Note: 정확히 PhaseOut->PhaseIn에서 선박이 변경됨)
                ts_chain_phase_out_indices: set[int] = set()
                for i in range(1, len(lane_events) - 2):
                    if (
                        isinstance(lane_events[i - 1].event, TransshipmentUnload)
                        and isinstance(lane_events[i].event, PhaseOut)
                        and isinstance(lane_events[i + 1].event, PhaseIn)
                        and isinstance(lane_events[i + 2].event, TransshipmentLoad)
                    ):
                        ts_chain_phase_out_indices.add(i)

                for i, (prev_lane_event, next_lane_event) in enumerate(zip(lane_events, lane_events[1:])):
                    vessel_changed = prev_lane_event.vessel_code != next_lane_event.vessel_code
                    if not vessel_changed:
                        continue
                    if i not in ts_chain_phase_out_indices:
                        raise InfeasibleError(
                            f"Lane {lane_code} proforma {proforma_name} position {position_no} changes assigned vessel "
                            f"from {prev_lane_event.vessel_code} to {next_lane_event.vessel_code} without a valid TS chain: "
                            f"{prev_lane_event!r} -> {next_lane_event!r}."
                        )

                # LANE-1-3. TS Chain이 존재하는 경우 반드시 선박이 변경되어야 한다.
                for i in ts_chain_phase_out_indices:
                    outgoing_vessel_code = lane_events[i].vessel_code
                    incoming_vessel_code = lane_events[i + 1].vessel_code
                    if outgoing_vessel_code == incoming_vessel_code:
                        raise InfeasibleError(
                            f"Lane {lane_code} proforma {proforma_name} position {position_no} has TS chain without vessel reassignment: "
                            f"{lane_events[i - 1]!r}, {lane_events[i]!r}, {lane_events[i + 1]!r}, {lane_events[i + 2]!r}."
                        )

                # LANE-1-4. TransshipmentUnload와 TransshipmentLoad는 TS Chain 속에서만 발생할 수 있고,
                #           PhaseIn과 PhaseOut은 TS Chain 또는 lane_events의 처음과 끝에서만 발생할 수 있다.
                ts_chain_indices = {
                    index
                    for phase_out_index in ts_chain_phase_out_indices
                    for index in (
                        phase_out_index - 1,
                        phase_out_index,
                        phase_out_index + 1,
                        phase_out_index + 2,
                    )
                }
                for i, lane_event in enumerate(lane_events):
                    event = lane_event.event
                    if isinstance(event, (TransshipmentUnload, TransshipmentLoad)) and i not in ts_chain_indices:
                        raise InfeasibleError(
                            f"Lane {lane_code} proforma {proforma_name} position {position_no} has {event!r} outside any TS chain."
                        )
                    if isinstance(event, PhaseIn) and i not in ts_chain_indices and i != 0:
                        raise InfeasibleError(
                            f"Lane {lane_code} proforma {proforma_name} position {position_no} has {event!r} outside any TS chain "
                            "and not at index 0."
                        )
                    if isinstance(event, PhaseOut) and i not in ts_chain_indices and i != len(lane_events) - 1:
                        raise InfeasibleError(
                            f"Lane {lane_code} proforma {proforma_name} position {position_no} has {event!r} outside any TS chain "
                            "and not at the last index."
                        )

                # LANE-2 / LANE-3 검증을 위한 expected port call 생성
                port_stay_events = sorted([x.event for x in lane_events if isinstance(x.event, PortStay)])
                service_start = get_service_start_datetime(lane_code, proforma_name, position_no)
                service_end = get_service_end_datetime(lane_code, proforma_name, position_no)
                n_round_trips = 0
                service_duration = version["service_duration"]

                # 일단 service_start부터 service_end까지 모든 port call을 생성하고, 이후 planning horizon으로 필터
                port_calls = []
                while service_start + n_round_trips * timedelta(days=service_duration) < service_end:
                    _offset = service_start + timedelta(days=n_round_trips * service_duration)
                    next_offset = _offset + timedelta(days=service_duration)
                    rotations = (
                        version["port_rotation"] if next_offset >= service_end else version["port_rotation"][:-1]
                    )
                    for rotation in rotations:
                        port_calls.append(
                            {
                                "port_code": rotation["port_code"],
                                "port_seq": rotation["port_seq"],
                                "pilot_in_start": _offset + timedelta(minutes=rotation["eta_offset_minutes"]),
                                "berthing_start": _offset + timedelta(minutes=rotation["etb_offset_minutes"]),
                                "berthing_end": _offset + timedelta(minutes=rotation["etd_offset_minutes"]),
                                "pilot_out_end": _offset
                                + timedelta(minutes=rotation["etd_offset_minutes"] + rotation["pilot_out_minutes"]),
                            }
                        )
                    n_round_trips += 1

                # PortStay는 구간 이벤트이므로 부등호가 strict해도 됨
                port_calls = [x for x in port_calls if x["pilot_in_start"] < PLANNING_END]
                port_calls = [x for x in port_calls if x["pilot_out_end"] > PLANNING_START]

                # LANE-2. 모든 항로는 요구 서비스 기간 동안 이벤트가 빠짐 없이 존재하는가?
                # coverage 기준은 validation에서 생성한 expected port call의 시작/종료 시각이다.
                test_start = port_calls[0]["pilot_in_start"]
                test_end = port_calls[-1]["pilot_out_end"]

                # LANE-2-1. 첫 이벤트 기간에 test_start가 걸쳐 있어야 한다.
                first_interval_event = next(
                    (event for event in lane_events if is_interval_event(event.event)),
                    None,
                ).event
                if not (event_start_time(first_interval_event) <= test_start <= event_end_time(first_interval_event)):
                    raise InfeasibleError(
                        f"Lane {lane_code} proforma {proforma_name} position {position_no} has first event that does not include test_start."
                    )

                # LANE-2-2. 마지막 이벤트 기간에 test_end가 걸쳐 있어야 한다.
                last_interval_event = next(
                    (event for event in lane_events[::-1] if is_interval_event(event.event)),
                    None,
                ).event
                if not (event_start_time(last_interval_event) <= test_end <= event_end_time(last_interval_event)):
                    print_events(lane_events)
                    print(test_start, test_end)
                    raise InfeasibleError(
                        f"Lane {lane_code} proforma {proforma_name} position {position_no} has last event that does not include test_end."
                    )

                # LANE-2-3. 모든 이벤트는 시간적으로 연속되어야 한다. TS의 경우 [1일, 7일]의 빈틈이 있어야 한다.
                i = 0
                while i < len(lane_events) - 1:
                    prev_event = lane_events[i].event
                    next_event = lane_events[i + 1].event
                    prev_end_time = event_end_time(prev_event)
                    next_start_time = event_start_time(next_event)

                    if i not in ts_chain_phase_out_indices and prev_end_time == next_start_time:
                        i += 1
                        continue
                    if i in ts_chain_phase_out_indices:
                        tsu_event = lane_events[i - 1].event
                        tsl_event = lane_events[i + 2].event

                        if event_end_time(tsu_event) != event_start_time(prev_event):
                            raise InfeasibleError(
                                f"Lane {lane_code} proforma {proforma_name} position {position_no} has invalid TS chain: "
                                f"{tsu_event!r} must end exactly when {prev_event!r} starts."
                            )
                        if event_end_time(next_event) != event_start_time(tsl_event):
                            raise InfeasibleError(
                                f"Lane {lane_code} proforma {proforma_name} position {position_no} has invalid TS chain: "
                                f"{next_event!r} must end exactly when {tsl_event!r} starts."
                            )

                        gap_days = (next_start_time - prev_end_time).total_seconds() / (3600 * 24)
                        if not (1 <= gap_days <= 7):
                            raise InfeasibleError(
                                f"Lane {lane_code} proforma {proforma_name} position {position_no} has invalid TS gap of {gap_days:.2f} days "
                                f"between {prev_event!r} and {next_event!r}."
                            )

                        i += 2
                        continue

                    raise InfeasibleError(
                        f"Lane {lane_code} proforma {proforma_name} position {position_no} has non-continuous events with invalid gap: "
                        f"previous event {prev_event!r} ends at {prev_end_time} but next event {next_event!r} starts at {next_start_time}."
                    )

                # LANE-3. 모든 기항 일정이 PortStay 이벤트로 정확히 충족되어 있는가?

                for call, stay in zip(port_calls, port_stay_events):
                    if (
                        (call["port_code"] != stay.port_code)
                        or (call["port_seq"] != stay.port_seq)
                        or (call["pilot_in_start"] != stay.pilot_in_start)
                        or (call["berthing_start"] != stay.berthing_start)
                        or (call["berthing_end"] != stay.berthing_end)
                        or (call["pilot_out_end"] != stay.pilot_out_end)
                    ):
                        raise InfeasibleError(
                            f"Lane {lane_code} proforma {proforma_name} position {position_no} has mismatched PortStay event for expected port call: "
                            f"expected {call!r} but got {stay!r}."
                        )

                if len(port_calls) != len(port_stay_events):
                    print_events(port_calls)
                    print_events(port_stay_events)
                    print(test_start, test_end)
                    raise InfeasibleError(
                        f"Lane {lane_code} proforma {proforma_name} position {position_no} has different number of PortStay events than expected port calls: "
                        f"expected {len(port_calls)} but got {len(port_stay_events)}."
                    )

                # LANE-4. 모든 현행 Lane은 Current Assignment 정보대로의 선박으로 시작하는가?
                assigned_vessel_code = next(
                    (
                        assignment["vessel_code"]
                        for assignment in version["vessel_assignments"]
                        if assignment["position_no"] == position_no
                    ),
                    None,
                )
                if assigned_vessel_code is not None and lane_events[0].vessel_code != assigned_vessel_code:
                    raise InfeasibleError(
                        f"Lane {lane_code} proforma {proforma_name} position {position_no} must start with currently assigned vessel "
                        f"{assigned_vessel_code}, but starts with {lane_events[0].vessel_code}: {lane_events[0]!r}."
                    )

                # LANE-5. 모든 화물이 시간 및 물리적 일관성 속에서 항로를 순회하고 있는가?
                # TODO

                # LANE-6. 할당된 선박은 모두 선복 제약 조건을 만족하는가?
                required_capacity_teu = version["required_capacity_teu"]
                required_reefer_plug = version["required_reefer_plug"]
                capacity_tolerance = required_capacity_teu * 0.05
                reefer_tolerance = required_reefer_plug * 0.05 + 1000000000

                for vessel_code in {lane_event.vessel_code for lane_event in lane_events}:
                    if vessel_code in virtual_vessel_codes:
                        continue

                    vessel = lookup_vessel(vessel_code)
                    capacity_teu = vessel["capacity_teu"]
                    reefer_plug = vessel["reefer_plug"]

                    if abs(capacity_teu - required_capacity_teu) > capacity_tolerance:
                        raise InfeasibleError(
                            f"Lane {lane_code} proforma {proforma_name} position {position_no} uses vessel {vessel_code} with "
                            f"capacity_teu={capacity_teu}, which is outside the allowed 5% range of required_capacity_teu={required_capacity_teu}."
                        )

                    if abs(reefer_plug - required_reefer_plug) > reefer_tolerance:
                        raise InfeasibleError(
                            f"Lane {lane_code} proforma {proforma_name} position {position_no} uses vessel {vessel_code} with "
                            f"reefer_plug={reefer_plug}, which is outside the allowed 5% range of required_reefer_plug={required_reefer_plug}."
                        )

    # endregion


def evaluate_solution(solution: CascadingSolution, instance_data: InstanceData) -> dict[str, Any] | None:
    """
    해의 총 평가 비용을 계산한다.

    구성은 아래 region 순서를 따른다.
    - Common Helpers: 공통 보조 함수와 기본 판정 로직을 정의한다.
    - Bunker Cost: 실제 선박의 해상 항해 및 PortStay 이벤트에 대한 벙커 소모를 계산한다.
    - Canal Fee: 실제 선박이 수에즈/파나마 운하를 통과하는 경우 방향과 선박별 운하비를 계산한다.
    - Transshipment Cost: lane view에서 TS chain을 찾아 lane-port 기준 환적 비용을 계산한다.
    - Opportunity Cost: virtual vessel이 커버한 in-lane 시간에 대해 방향별 일 단위 기회비용을 계산한다.

    참고:
    - 벙커는 우선 이벤트별 소모량을 계산한 뒤 합산한다.
    """

    init_utils(instance_data)
    all_vessel_schedules = solution.all_vessel_schedules
    if not (solution.declared_positions or all_vessel_schedules):
        return None
    lane_view = solution.to_lane_view()

    actual_vessel_codes = {vessel["vessel_code"] for vessel in instance_data.vessels}
    virtual_vessel_codes = set(solution.virtual_vessel_schedules.keys())

    # region Common Helpers
    def _add_event_cost(event: VesselScheduleEvent, key: str, value: float) -> None:
        if not math.isfinite(value):
            return
        event_costs = getattr(event, "event_costs", None)
        if not isinstance(event_costs, dict):
            event_costs = {}
            event.event_costs = event_costs
        event_costs[key] = event_costs.get(key, 0.0) + float(value)

    def _lookup_capacity_teu(vessel_code: str) -> int:
        return int(lookup_vessel(vessel_code)["capacity_teu"])

    def _canonical_canal_port_code(port_code: str) -> str:
        return "EGSCA" if port_code == "EGSUZ" else port_code

    def _round_sailing_speed(speed: float) -> float:
        rounded = math.ceil(speed * 2.0) / 2.0
        return min(20.0, max(14.0, rounded))

    def _interval_days(event: VesselScheduleEvent) -> float:
        return (event_end_time(event) - event_start_time(event)).total_seconds() / (24 * 3600)

    for schedules in (solution.vessel_schedules.values(), solution.virtual_vessel_schedules.values()):
        for vessel_schedule in schedules:
            for event in vessel_schedule:
                event.event_costs = {}

    # endregion

    # region Bunker Cost
    bunker_port_by_capacity = {row["capacity_teu"]: row["consumption"] for row in instance_data.bunker_consumption_port}
    bunker_sea_by_capacity = {
        row["capacity_teu"]: {
            consumption["speed"]: consumption["consumption_for_sailing"] for consumption in row["consumption"]
        }
        for row in instance_data.bunker_consumption_sea
    }

    bunker_price_by_key = {
        (row["year_month"], row["lane_code"], row["bunker_type"]): row["price"] for row in instance_data.bunker_price
    }
    unattributed_bunker_price_per_ton = 0.0
    distance_by_leg = {
        (row["from_port_code"], row["to_port_code"]): {
            "distance": row["distance"],
            "eca_distance": row["eca_distance"],
        }
        for row in instance_data.distances
    }

    def _lookup_bunker_sea_consumption(capacity_teu: int, speed: float) -> float:
        try:
            return bunker_sea_by_capacity[capacity_teu][speed]
        except KeyError as exc:
            raise ValueError(
                f"evaluate_solution: missing sea bunker consumption for capacity={capacity_teu}, speed={speed}."
            ) from exc

    def _lookup_bunker_port_consumption(capacity_teu: int) -> dict:
        try:
            return bunker_port_by_capacity[capacity_teu]
        except KeyError as exc:
            raise ValueError(
                f"evaluate_solution: missing port bunker consumption for capacity={capacity_teu}."
            ) from exc

    def _lookup_distance_components(from_port_code: str, to_port_code: str) -> tuple[float, float]:
        leg_info = distance_by_leg.get((from_port_code, to_port_code))
        if leg_info is None:
            raise ValueError("evaluate_solution: missing distance components for " f"{from_port_code}->{to_port_code}.")
        return float(leg_info["distance"]), float(leg_info["eca_distance"])

    def _bunker_event_cost_component(
        vessel_schedules: list[VesselScheduleEvent],
        event_index: int,
        lsfo_consumption: float,
        mgo_consumption: float,
    ) -> float:
        event = vessel_schedules[event_index]
        year_month = to_year_month(event_end_time(event))
        base_event = event
        if not isinstance(event, InLaneEvent):
            for i in range(event_index, len(vessel_schedules)):
                if isinstance(vessel_schedules[i], InLaneEvent):
                    base_event = vessel_schedules[i]
                    break

        if not isinstance(base_event, InLaneEvent):
            return (lsfo_consumption + mgo_consumption) * unattributed_bunker_price_per_ton

        lane_code = base_event.lane_code
        mgo_price = bunker_price_by_key.get((year_month, lane_code, "MGO"))
        lsfo_price = bunker_price_by_key.get((year_month, lane_code, "LSFO"))
        if mgo_price is None or lsfo_price is None:
            missing_types = []
            if mgo_price is None:
                missing_types.append("MGO")
            if lsfo_price is None:
                missing_types.append("LSFO")
            raise ValueError(
                "evaluate_solution: missing bunker price for "
                f"year_month={year_month}, lane_code={lane_code}, bunker_type={missing_types}."
            )
        return lsfo_consumption * lsfo_price + mgo_consumption * mgo_price

    bunker_cost = 0.0
    bunker_cost_by_inlane_sail = 0.0
    bunker_cost_by_port_stay = 0.0
    bunker_cost_by_outlane_sail = 0.0
    for vessel_code, schedules in solution.vessel_schedules.items():
        capacity_teu = _lookup_capacity_teu(vessel_code)
        port_consumption = _lookup_bunker_port_consumption(capacity_teu)

        for i, event in enumerate(schedules):
            if isinstance(event, (InLaneSail, OutLaneSail)):
                distance, eca_distance = _lookup_distance_components(event.from_port_code, event.to_port_code)
                mgo_rate = eca_distance / distance

                duration_hours = (event_end_time(event) - event_start_time(event)).total_seconds() / 3600
                if duration_hours <= 0:
                    raise ValueError(
                        f"evaluate_solution: vessel {vessel_code} has sailing event with non-positive duration: {event!r}."
                    )

                avg_speed = distance / duration_hours
                rounded_speed = _round_sailing_speed(avg_speed)
                sail_hours = distance / rounded_speed if rounded_speed > 0 else 0.0  # 대기 시간 제외 실제 이동한 시간
                daily_consumption = _lookup_bunker_sea_consumption(capacity_teu, rounded_speed)
                bunker_consumption_tons = (daily_consumption / 24.0) * sail_hours
                mgo_consumption = bunker_consumption_tons * mgo_rate
                lsfo_consumption = bunker_consumption_tons - mgo_consumption
                event_bunker_cost = _bunker_event_cost_component(schedules, i, lsfo_consumption, mgo_consumption)
                bunker_cost += event_bunker_cost
                _add_event_cost(event, "bunker_cost", event_bunker_cost)
                if isinstance(event, InLaneSail):
                    bunker_cost_by_inlane_sail += event_bunker_cost
                    _add_event_cost(event, "bunker_cost_by_inlane_sail", event_bunker_cost)
                else:
                    bunker_cost_by_outlane_sail += event_bunker_cost
                    _add_event_cost(event, "bunker_cost_by_outlane_sail", event_bunker_cost)

            elif isinstance(event, PortStay):
                pilot_hours = (event.berthing_start - event.pilot_in_start).total_seconds() / 3600
                pilot_hours += (event.pilot_out_end - event.berthing_end).total_seconds() / 3600
                bunker_consumption_tons = port_consumption["consumption_for_pilot"] * pilot_hours
                port_code = event.port_code
                if port_code in instance_data.eca_ports:
                    mgo_consumption = bunker_consumption_tons
                    lsfo_consumption = 0.0
                else:
                    mgo_consumption = 0
                    lsfo_consumption = bunker_consumption_tons
                event_bunker_cost = _bunker_event_cost_component(schedules, i, lsfo_consumption, mgo_consumption)
                bunker_cost += event_bunker_cost
                _add_event_cost(event, "bunker_cost", event_bunker_cost)
                bunker_cost_by_port_stay += event_bunker_cost
                _add_event_cost(event, "bunker_cost_by_port_stay", event_bunker_cost)

            elif isinstance(event, CanalPassage):
                passage_hours = (event.passage_end - event.passage_start).total_seconds() / 3600
                if passage_hours <= 0:
                    raise ValueError(
                        f"evaluate_solution: vessel {vessel_code} has CanalPassage with non-positive duration: {event!r}."
                    )

                bunker_consumption_tons = port_consumption["consumption_for_pilot"] * passage_hours
                canal_port_code = _canonical_canal_port_code(event.canal_port_code)
                if canal_port_code in instance_data.eca_ports:
                    mgo_consumption = bunker_consumption_tons
                    lsfo_consumption = 0.0
                else:
                    mgo_consumption = 0.0
                    lsfo_consumption = bunker_consumption_tons
                event_bunker_cost = _bunker_event_cost_component(schedules, i, lsfo_consumption, mgo_consumption)
                bunker_cost += event_bunker_cost
                _add_event_cost(event, "bunker_cost", event_bunker_cost)
                bunker_cost_by_outlane_sail += event_bunker_cost
                _add_event_cost(event, "bunker_cost_by_outlane_sail", event_bunker_cost)
    # endregion

    # region Canal Fee
    # 운하 fee/direction lookup도 validation과 같은 canonical key를 사용한다.
    # 이렇게 해야 EGSUZ 입력과 EGSCA 이벤트가 같은 운하 항구로 평가된다.
    canal_fee_by_key = {
        (row["vessel_code"], row["direction"], _canonical_canal_port_code(row["port_code"])): row["fee"]
        for row in instance_data.canal_fee
    }
    canal_direction_by_key = {
        (
            _canonical_canal_port_code(row["from_port_code"]),
            _canonical_canal_port_code(row["canal_port_code"]),
            _canonical_canal_port_code(row["to_port_code"]),
        ): row["direction"]
        for row in instance_data.canal_direction
    }

    canal_fee_cost = 0.0
    for vessel_code, schedules in solution.vessel_schedules.items():
        # 명시적 CanalPassage 이벤트만 out-lane 운하 경유 비용을 발생시킨다.
        # route metadata가 앞뒤 OutLaneSail과 같은 A->C->B를 가리키는지도 evaluation에서 다시 확인한다.
        for event_index, event in enumerate(schedules):
            if not isinstance(event, CanalPassage):
                continue
            if event_index == 0 or event_index == len(schedules) - 1:
                raise ValueError(f"evaluate_solution: CanalPassage must be bracketed by OutLaneSail events: {event!r}.")
            prev_event = schedules[event_index - 1]
            next_event = schedules[event_index + 1]
            if not isinstance(prev_event, OutLaneSail) or not isinstance(next_event, OutLaneSail):
                raise ValueError(
                    "evaluate_solution: CanalPassage must be bracketed by OutLaneSail events: "
                    f"previous={prev_event!r}, canal={event!r}, next={next_event!r}."
                )
            canal_port_code = _canonical_canal_port_code(event.canal_port_code)
            if _canonical_canal_port_code(prev_event.to_port_code) != canal_port_code:
                raise ValueError(
                    "evaluate_solution: CanalPassage canal port does not match previous sail arrival: "
                    f"previous={prev_event!r}, canal={event!r}."
                )
            if _canonical_canal_port_code(next_event.from_port_code) != canal_port_code:
                raise ValueError(
                    "evaluate_solution: CanalPassage canal port does not match next sail departure: "
                    f"canal={event!r}, next={next_event!r}."
                )
            if event.from_port_code != prev_event.from_port_code or event.to_port_code != next_event.to_port_code:
                raise ValueError(
                    "evaluate_solution: CanalPassage route metadata does not match adjacent sails: "
                    f"previous={prev_event!r}, canal={event!r}, next={next_event!r}."
                )
            expected_direction = canal_direction_by_key.get(
                (
                    _canonical_canal_port_code(prev_event.from_port_code),
                    canal_port_code,
                    _canonical_canal_port_code(next_event.to_port_code),
                )
            )
            if expected_direction is None:
                raise ValueError(
                    "evaluate_solution: missing canal direction for "
                    f"{prev_event.from_port_code}->{canal_port_code}->{next_event.to_port_code}."
                )
            if expected_direction != event.direction:
                raise ValueError(
                    "evaluate_solution: CanalPassage direction mismatch for "
                    f"{prev_event.from_port_code}->{canal_port_code}->{next_event.to_port_code}: "
                    f"event={event.direction!r}, expected={expected_direction!r}."
                )
            fee_key = (vessel_code, event.direction, canal_port_code)
            if fee_key not in canal_fee_by_key:
                raise ValueError(f"evaluate_solution: missing canal fee for {fee_key}.")
            event_canal_fee_cost = canal_fee_by_key[fee_key]
            canal_fee_cost += event_canal_fee_cost
            _add_event_cost(event, "canal_fee_cost", event_canal_fee_cost)

        # 기존 in-lane port stay 방식의 운하 통과는 PortStay 자체가 운하 서비스 이벤트일 때만 비용을 부과한다.
        for prev_event, mid_event, next_event in zip(schedules, schedules[1:], schedules[2:]):
            if (
                isinstance(prev_event, InLaneSail)
                and isinstance(mid_event, PortStay)
                and isinstance(next_event, InLaneSail)
                and mid_event.port_code in {"EGSUZ", "EGSCA", "PAPCA"}
            ):
                canal_port_code = _canonical_canal_port_code(mid_event.port_code)
                direction = canal_direction_by_key.get(
                    (
                        _canonical_canal_port_code(prev_event.from_port_code),
                        canal_port_code,
                        _canonical_canal_port_code(next_event.to_port_code),
                    )
                )
                if direction is None:
                    raise ValueError(
                        "evaluate_solution: missing canal direction for "
                        f"{prev_event.from_port_code}->{canal_port_code}->{next_event.to_port_code}."
                    )
                fee_key = (vessel_code, direction, canal_port_code)
                if fee_key not in canal_fee_by_key:
                    raise ValueError(f"evaluate_solution: missing canal fee for {fee_key}.")
                event_canal_fee_cost = canal_fee_by_key[fee_key]
                canal_fee_cost += event_canal_fee_cost
                _add_event_cost(mid_event, "canal_fee_cost", event_canal_fee_cost)

        # out-lane 운하 경유는 CanalPassage 없이 두 OutLaneSail을 직접 붙여 표현할 수 없다.
        # 여기서 조용히 fee를 붙이면 잘못된 solution 표현을 정상 비용처럼 받아들이게 된다.
        for prev_event, next_event in zip(schedules, schedules[1:]):
            if (
                isinstance(prev_event, OutLaneSail)
                and isinstance(next_event, OutLaneSail)
                and _canonical_canal_port_code(prev_event.to_port_code) in {"EGSCA", "PAPCA"}
                and _canonical_canal_port_code(prev_event.to_port_code)
                == _canonical_canal_port_code(next_event.from_port_code)
            ):
                raise ValueError(
                    "evaluate_solution: adjacent OutLaneSail events through canal port "
                    f"{prev_event.to_port_code} require an explicit CanalPassage event."
                )
    # endregion

    # region Transshipment Cost
    ts_cost_exact: dict[tuple[str, str, str], float] = {}
    for row in instance_data.transshipment_cost:
        year_month = row["year_month"]
        lane_code = row["lane_code"]
        for port in row["ports"]:
            key = (year_month, lane_code, port["port_code"])
            ts_cost_exact[key] = port["ts_cost"]

    def _lookup_ts_cost(lane_code: str, port_code: str, when: datetime) -> float:
        exact_key = (to_year_month(when), lane_code, port_code)
        if exact_key in ts_cost_exact:
            return ts_cost_exact[exact_key]

        raise ValueError(
            "evaluate_solution: could not determine TS cost for "
            f"lane={lane_code}, port={port_code}, year_month={to_year_month(when)}."
        )

    declared_positions_by_version: dict[tuple[str, str], list[DeclaredPosition]] = {}
    for position in solution.declared_positions:
        declared_positions_by_version.setdefault((position.lane_code, position.proforma_name), []).append(position)

    transshipment_cost = 0.0
    transshipment_count = 0
    for lane in instance_data.service_lanes:
        lane_code = lane["lane_code"]
        for version in lane["versions"]:
            proforma_name = version["proforma_name"]
            declared_positions = (
                version["declared_positions"]
                if len(version["declared_positions"]) > 0
                else [x.declared_position_no for x in declared_positions_by_version.get((lane_code, proforma_name), [])]
            )

            for position_no in declared_positions:
                lane_events = lane_view.get((lane_code, proforma_name, position_no), [])
                if not lane_events:
                    continue

                for i in range(1, len(lane_events) - 2):
                    if (
                        isinstance(lane_events[i - 1].event, TransshipmentUnload)
                        and isinstance(lane_events[i].event, PhaseOut)
                        and isinstance(lane_events[i + 1].event, PhaseIn)
                        and isinstance(lane_events[i + 2].event, TransshipmentLoad)
                    ):
                        ts_event = lane_events[i - 1].event
                        event_ts_cost = _lookup_ts_cost(lane_code, ts_event.ts_port_code, ts_event.unload_start)
                        transshipment_cost += event_ts_cost
                        transshipment_count += 1
                        _add_event_cost(ts_event, "transshipment_cost", event_ts_cost)
    # endregion

    # region Opportunity Cost
    opportunity_cost_by_key = {
        (row["lane_code"], row["proforma_name"], row["direction"]): row["opportunity_cost"]
        for row in instance_data.opportunity_cost
    }
    direction_by_lane_version_seq: dict[tuple[str, str, int], str] = {}
    for lane in instance_data.service_lanes:
        lane_code = lane["lane_code"]
        for version in lane["versions"]:
            proforma_name = version["proforma_name"]
            for port in version["port_rotation"]:
                direction_by_lane_version_seq[(lane_code, proforma_name, port["port_seq"])] = port["direction"]

    def _lookup_lane_direction(lane_code: str, proforma_name: str, port_seq: int) -> str:
        try:
            return direction_by_lane_version_seq[(lane_code, proforma_name, port_seq)]
        except KeyError as exc:
            raise ValueError(
                f"evaluate_solution: missing direction for lane={lane_code}, proforma={proforma_name}, port_seq={port_seq}."
            ) from exc

    def _direction_of_inlane_event(event: InLaneEvent) -> str:
        if isinstance(event, InLaneSail):
            return _lookup_lane_direction(event.lane_code, event.proforma_name, event.from_port_seq)
        if isinstance(event, PortStay):
            return _lookup_lane_direction(event.lane_code, event.proforma_name, event.port_seq)
        if isinstance(event, PhaseIn):
            return _lookup_lane_direction(event.lane_code, event.proforma_name, event.phase_in_port_seq)
        if isinstance(event, PhaseOut):
            return _lookup_lane_direction(event.lane_code, event.proforma_name, event.phase_out_port_seq)
        if isinstance(event, TransshipmentUnload):
            return _lookup_lane_direction(event.lane_code, event.proforma_name, event.ts_port_seq)
        if isinstance(event, TransshipmentLoad):
            return _lookup_lane_direction(event.lane_code, event.proforma_name, event.ts_port_seq)
        raise TypeError(f"evaluate_solution: unsupported in-lane event type {type(event)!r} for direction lookup.")

    opportunity_cost = 0.0
    for lane in instance_data.service_lanes:
        lane_code = lane["lane_code"]
        for version in lane["versions"]:
            proforma_name = version["proforma_name"]
            declared_positions = (
                version["declared_positions"]
                if len(version["declared_positions"]) > 0
                else [x.declared_position_no for x in declared_positions_by_version.get((lane_code, proforma_name), [])]
            )

            for position_no in declared_positions:
                lane_events = lane_view.get((lane_code, proforma_name, position_no), [])
                for lane_event in lane_events:
                    event = lane_event.event
                    if lane_event.vessel_code not in virtual_vessel_codes:
                        continue
                    if not is_interval_event(event):
                        continue

                    direction = _direction_of_inlane_event(event)
                    opp_key = (lane_code, proforma_name, direction)
                    if opp_key not in opportunity_cost_by_key:
                        raise ValueError(f"evaluate_solution: missing opportunity cost for {opp_key}.")
                    event_opportunity_cost = opportunity_cost_by_key[opp_key] * _interval_days(event)
                    opportunity_cost += event_opportunity_cost
                    _add_event_cost(event, "opportunity_cost", event_opportunity_cost)
    # endregion

    # region Operational KPIs
    planning_start = instance_data.planning_horizon["start"]
    planning_end = instance_data.planning_horizon["end"]
    actual_schedules_by_code = {
        vessel_code: list(solution.vessel_schedules.schedules.get(vessel_code, []))
        for vessel_code in actual_vessel_codes
    }

    def _overlap_days(start: datetime, end: datetime, window_start: datetime, window_end: datetime) -> float:
        overlap_start = max(start, window_start)
        overlap_end = min(end, window_end)
        if overlap_end <= overlap_start:
            return 0.0
        return (overlap_end - overlap_start).total_seconds() / (24 * 3600)

    total_port_calls = 0
    actual_port_calls = 0
    service_days = 0.0
    available_days = 0.0
    drydock_days = 0.0
    required_teu_days = 0.0
    deployed_teu_days = 0.0
    required_capacity_by_version = {
        (lane["lane_code"], version["proforma_name"]): float(version["required_capacity_teu"])
        for lane in instance_data.service_lanes
        for version in lane["versions"]
    }

    for vessel in instance_data.vessels:
        vessel_code = vessel["vessel_code"]
        vessel_events = actual_schedules_by_code.get(vessel_code, [])
        delivery_event = next((event for event in vessel_events if isinstance(event, Delivery)), None)
        redelivery_event = next((event for event in vessel_events if isinstance(event, Redelivery)), None)
        available_start = delivery_event.delivery_time if delivery_event is not None else planning_start
        available_end = redelivery_event.redelivery_time if redelivery_event is not None else planning_end
        available_start = max(planning_start, available_start)
        available_end = min(planning_end, available_end)
        if available_end > available_start:
            vessel_available_days = (available_end - available_start).total_seconds() / (24 * 3600)
            vessel_drydock_days = sum(
                _overlap_days(event.dock_in, event.dock_out, available_start, available_end)
                for event in vessel_events
                if isinstance(event, DryDock)
            )
            drydock_days += vessel_drydock_days
            available_days += max(0.0, vessel_available_days - vessel_drydock_days)

        vessel_capacity = float(_lookup_capacity_teu(vessel_code))
        for event in vessel_events:
            if isinstance(event, PortStay) and event.lane_code:
                total_port_calls += 1
                actual_port_calls += 1
            if not isinstance(event, InLaneEvent) or not is_interval_event(event):
                continue
            event_days = _overlap_days(event_start_time(event), event_end_time(event), available_start, available_end)
            service_days += event_days
            required_capacity = required_capacity_by_version.get((event.lane_code, event.proforma_name))
            if required_capacity is None:
                continue
            required_teu_days += required_capacity * event_days
            deployed_teu_days += vessel_capacity * event_days

    for schedules in solution.virtual_vessel_schedules.values():
        for event in schedules:
            if isinstance(event, PortStay) and event.lane_code:
                total_port_calls += 1

    service_lane_coverage_rate = actual_port_calls / total_port_calls if total_port_calls else None
    vessel_utilization_rate = service_days / available_days if available_days else None
    slot_utilization_rate = required_teu_days / deployed_teu_days if deployed_teu_days else None
    # endregion

    total_cost = bunker_cost + canal_fee_cost + transshipment_cost + opportunity_cost
    print(
        "[Evaluation] Bunker Cost: {:.2%}, Canal Fee: {:.2%}, Transshipment Cost: {:.2%}, Opportunity Cost: {:.2%}, #Virtual Vessels: {}".format(
            bunker_cost / total_cost,
            canal_fee_cost / total_cost,
            transshipment_cost / total_cost,
            opportunity_cost / total_cost,
            len(virtual_vessel_codes),
        )
    )
    return {
        "total_cost": total_cost,
        "bunker_cost": bunker_cost,
        "bunker_cost_by_inlane_sail": bunker_cost_by_inlane_sail,
        "bunker_cost_by_port_stay": bunker_cost_by_port_stay,
        "bunker_cost_by_outlane_sail": bunker_cost_by_outlane_sail,
        "canal_fee_cost": canal_fee_cost,
        "transshipment_cost": transshipment_cost,
        "transshipment_count": transshipment_count,
        "opportunity_cost": opportunity_cost,
        "num_virtual_vessels": len(virtual_vessel_codes),
        "actual_port_calls": actual_port_calls,
        "total_port_calls": total_port_calls,
        "service_lane_coverage_rate": service_lane_coverage_rate,
        "vessel_service_days": service_days,
        "vessel_available_days": available_days,
        "vessel_drydock_days": drydock_days,
        "vessel_utilization_rate": vessel_utilization_rate,
        "slot_utilization_required_teu_days": required_teu_days,
        "slot_utilization_deployed_teu_days": deployed_teu_days,
        "slot_utilization_rate": slot_utilization_rate,
    }

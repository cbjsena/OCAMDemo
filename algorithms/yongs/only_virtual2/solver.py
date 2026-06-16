from __future__ import annotations

from datetime import datetime, timedelta

from ocam.models import *
from ocam.utils import *

DESCRIPTION = "선박이 필요해지는 모든 곳에 virtual vessel을 사용한다."

INSTANCE_DATA = None
PLANNING_START = None
PLANNING_END = None

NUM_VIRTUAL_VESSELS_USED = 0
USED_VIRTUAL_VESSEL_CODES = set()

TS_WORK_HOUR = 6
TS_SLACK_HOUR = 24

print = lambda *args, **kwargs: None  # disable print


# region utils
def init_solver_globals(instance_data: InstanceData) -> None:
    global INSTANCE_DATA, PLANNING_START, PLANNING_END

    INSTANCE_DATA = instance_data
    PLANNING_START = instance_data.planning_horizon["start"]
    PLANNING_END = instance_data.planning_horizon["end"]
    init_utils(instance_data)


def get_new_virtual_vessel_code() -> str:
    global NUM_VIRTUAL_VESSELS_USED
    NUM_VIRTUAL_VESSELS_USED += 1

    x = 1
    new_code = "VIRTUAL" + str(x).zfill(3)
    while new_code in USED_VIRTUAL_VESSEL_CODES:
        x += 1
        new_code = "VIRTUAL" + str(x).zfill(3)

    USED_VIRTUAL_VESSEL_CODES.add(new_code)
    return new_code


def first_inlane_event(schedule: list[VesselScheduleEvent]) -> VesselScheduleEvent:
    for event in schedule:
        if isinstance(event, InLaneEvent):
            return event
    raise ValueError("first_lane_event: no lane event found in schedule.")


# endregion


def out_sail_or_idle(
    from_port_code: str, sea_sail_start: datetime, sea_sail_end: datetime, to_port_code: str
) -> OutLaneSail | Idle:
    if from_port_code == to_port_code:
        return Idle(
            port_code=from_port_code,
            idle_start=sea_sail_start,
            idle_end=sea_sail_end,
        )
    else:
        return OutLaneSail(
            from_port_code=from_port_code,
            sea_sail_start=sea_sail_start,
            sea_sail_end=sea_sail_end,
            to_port_code=to_port_code,
        )


def _connection_if_positive(
    from_port_code: str,
    sea_sail_start: datetime,
    sea_sail_end: datetime,
    to_port_code: str,
) -> list[OutLaneSail | Idle]:
    if sea_sail_start >= sea_sail_end:
        return []
    return [
        out_sail_or_idle(
            from_port_code=from_port_code,
            sea_sail_start=sea_sail_start,
            sea_sail_end=sea_sail_end,
            to_port_code=to_port_code,
        )
    ]


def _build_surplus_replacement_schedule(
    old_schedule: list[VesselScheduleEvent],
    inserted_schedule: list[VesselScheduleEvent],
    *,
    include_boundary: bool,
) -> list[VesselScheduleEvent]:
    target_start = event_start_time(inserted_schedule[0])
    target_end = event_end_time(inserted_schedule[-1])
    if include_boundary:
        old_schedule_leg1 = [s for s in old_schedule if event_end_time(s) <= target_start]
        old_schedule_leg2 = [s for s in old_schedule if event_start_time(s) >= target_end]
    else:
        old_schedule_leg1 = [s for s in old_schedule if event_end_time(s) < target_start]
        old_schedule_leg2 = [s for s in old_schedule if event_start_time(s) > target_end]

    if old_schedule_leg1:
        anchor_time = event_end_time(old_schedule_leg1[-1])
        anchor_port_code = event_end_port_code(old_schedule_leg1[-1])
    else:
        overlap = next(
            (
                s
                for s in old_schedule
                if event_start_time(s) <= target_start <= event_end_time(s)
            ),
            old_schedule[0] if old_schedule else None,
        )
        if overlap is None:
            anchor_time = target_start
            anchor_port_code = event_start_port_code(inserted_schedule[0])
        else:
            anchor_time = event_start_time(overlap)
            anchor_port_code = event_start_port_code(overlap)

    new_schedule = (
        old_schedule_leg1
        + _connection_if_positive(
            from_port_code=anchor_port_code,
            sea_sail_start=anchor_time,
            sea_sail_end=event_start_time(inserted_schedule[0]),
            to_port_code=event_start_port_code(inserted_schedule[0]),
        )
        + inserted_schedule
    )
    if old_schedule_leg2:
        new_schedule += (
            _connection_if_positive(
                from_port_code=event_end_port_code(inserted_schedule[-1]),
                sea_sail_start=event_end_time(inserted_schedule[-1]),
                sea_sail_end=event_start_time(old_schedule_leg2[0]),
                to_port_code=event_start_port_code(old_schedule_leg2[0]),
            )
            + old_schedule_leg2
        )
    return new_schedule


def split_inlane_schedule(
    schedule: list[VesselScheduleEvent],
    target_time: datetime,
    target_port_code: str,
) -> tuple[list[VesselScheduleEvent], list[VesselScheduleEvent]]:
    if len(schedule) == 0:
        raise ValueError("split_inlane_schedule: schedule must not be empty.")

    first_schedule = first_inlane_event(schedule)
    lane_code = first_schedule.lane_code
    proforma_name = first_schedule.proforma_name
    position_no = first_schedule.position_no

    version = lookup_version(lane_code, proforma_name)

    # 항구별 거리 계산
    target_port_distances = {}
    for rot in version["port_rotation"]:
        from_port_code = rot["port_code"]
        target_port_distances[from_port_code] = lookup_distance(from_port_code, target_port_code)

    # schedule는 PhaseIn, PortStay, InLaneSail, PhaseOut만 존재한다.
    # 환적 가능 시점은 PortStay 직전(직전 InLaneSail을 앞당겨 도착) 또는 PortStay 직후이다.
    # 가장 "마지막" 환적 일정을 찾아야 하므로 뒤에서부터 PortStay를 기준으로 탐색하고,
    # 같은 PortStay라면 더 늦은 직후 환적(after_portstay)을 우선한다.
    last_feasible_split_mode = None  # "before_portstay" | "after_portstay"
    last_feasible_port_stay_index = None

    # 환적이 필요 없으면 바로 반환
    last_event = schedule[-1]
    distance_to_target = target_port_distances[event_end_port_code(last_event)]
    sea_time_to_target = (target_time - event_end_time(last_event)).total_seconds() / 3600

    if sea_time_to_target > 0 and distance_to_target / (sea_time_to_target + 1e-5) <= 20:
        return schedule, []

    for port_stay_index in range(len(schedule) - 1, -1, -1):
        port_stay = schedule[port_stay_index]
        if not isinstance(port_stay, PortStay):
            continue

        ts_port_code = port_stay.port_code
        distance_target = target_port_distances[ts_port_code]

        # 운하 항구에서는 환적 불가능
        if ts_port_code in ("EGSUZ", "EGSCA", "PAPCA"):
            continue

        # Case 1) PortStay 직후 환적:
        # PortStay 종료 직후 PO slack 이후에 phase-out 하고 target에 제시간 도착 가능해야 하며,
        # PI 선박이 다음 항구까지 20knot 이내로 도착 가능해야 한다.
        if port_stay_index == len(schedule) - 1:
            continue

        next_event = schedule[port_stay_index + 1]
        if isinstance(next_event, InLaneSail):
            po_vessel_departure = event_end_time(port_stay) + timedelta(hours=TS_WORK_HOUR)
            sea_time_target = (target_time - po_vessel_departure).total_seconds() / 3600

            if sea_time_target > 0 and distance_target / (sea_time_target + 1e-5) <= 20:
                # target 조건은 만족함
                # PI 선박 조건 체크
                pi_vessel_arrival = po_vessel_departure + timedelta(hours=TS_SLACK_HOUR)
                pi_vessel_departure = pi_vessel_arrival + timedelta(hours=TS_WORK_HOUR)
                sea_time_inlane = (next_event.sea_sail_end - pi_vessel_departure).total_seconds() / 3600
                distance_inlane = lookup_distance(next_event.from_port_code, next_event.to_port_code)
                if sea_time_inlane > 0 and distance_inlane / (sea_time_inlane + 1e-5) <= 20:
                    last_feasible_split_mode = "after_portstay"
                    last_feasible_port_stay_index = port_stay_index
                    break

        # Case 2) PortStay 직전 환적:
        # 직전 InLaneSail의 도착 시각을 PortStay 시작보다 (PO+PI)시간 앞당길 수 있고,
        # 그 뒤 PO slack 이후 phase-out 하여 target에 제시간 도착 가능해야 한다.
        if port_stay_index == 0:
            continue
        prev_event = schedule[port_stay_index - 1]
        if not isinstance(prev_event, InLaneSail):
            continue

        po_vessel_arrival = event_start_time(port_stay) - timedelta(hours=TS_WORK_HOUR + TS_SLACK_HOUR + TS_WORK_HOUR)
        po_vessel_departure = po_vessel_arrival + timedelta(hours=TS_WORK_HOUR)
        sea_time_inlane = (po_vessel_arrival - prev_event.sea_sail_start).total_seconds() / 3600
        distance_inlane = lookup_distance(prev_event.from_port_code, prev_event.to_port_code)
        if sea_time_inlane <= 0 or distance_inlane / (sea_time_inlane + 1e-5) > 20:
            continue

        sea_time_target = (target_time - po_vessel_departure).total_seconds() / 3600
        if sea_time_target <= 0 or distance_target / (sea_time_target + 1e-5) > 20:
            continue

        last_feasible_split_mode = "before_portstay"
        last_feasible_port_stay_index = port_stay_index
        break

    if last_feasible_port_stay_index is None:
        s1, s2 = [], schedule
    else:
        s1, s2 = schedule[:last_feasible_port_stay_index], schedule[last_feasible_port_stay_index:]
        if not isinstance(s1[-1], InLaneSail) or not isinstance(s2[0], PortStay):
            raise ValueError(
                "split_inlane_schedule: infeasible split detected. "
                f"s1 last event: {s1[-1]!r}, s2 first event: {s2[0]!r}"
            )

    if len(s1) > 0 and len(s2) > 0:
        if last_feasible_split_mode == "before_portstay":
            # PortStay 직전 환적: s1의 마지막 InLaneSail을 앞당겨 도착하여 PO slack 이후 phase-out 하고, s2는 그대로.
            po_vessel_arrival = s2[0].pilot_in_start - timedelta(hours=TS_WORK_HOUR + TS_SLACK_HOUR + TS_WORK_HOUR)
            po_vessel_departure = po_vessel_arrival + timedelta(hours=TS_WORK_HOUR)
            pi_vessel_arrival = po_vessel_departure + timedelta(hours=TS_SLACK_HOUR)

            ts_port_code = s1[-1].to_port_code
            # if ts_port_code in ("EGSUZ", "EGSCA", "PAPCA"):
            #     print(f"split_inlane_schedule: unexpected canal port in schedule. s1 last event: {s1[-1]!r}")

            ts_port_seq = s1[-1].to_port_seq
            s1[-1].sea_sail_end = po_vessel_arrival
            s1.extend(
                [
                    TransshipmentUnload(
                        lane_code=lane_code,
                        proforma_name=proforma_name,
                        position_no=position_no,
                        ts_port_code=ts_port_code,
                        ts_port_seq=ts_port_seq,
                        unload_start=po_vessel_arrival,
                        unload_end=po_vessel_departure,
                    ),
                    PhaseOut(
                        lane_code=lane_code,
                        proforma_name=proforma_name,
                        position_no=position_no,
                        phase_out_time=po_vessel_departure,
                        phase_out_port_code=ts_port_code,
                        phase_out_port_seq=ts_port_seq,
                    ),
                ]
            )
            s2 = [
                PhaseIn(
                    lane_code=lane_code,
                    proforma_name=proforma_name,
                    position_no=position_no,
                    phase_in_port_code=ts_port_code,
                    phase_in_port_seq=ts_port_seq,
                    phase_in_time=pi_vessel_arrival,
                ),
                TransshipmentLoad(
                    lane_code=lane_code,
                    proforma_name=proforma_name,
                    position_no=position_no,
                    ts_port_code=ts_port_code,
                    ts_port_seq=ts_port_seq,
                    load_start=pi_vessel_arrival,
                    load_end=pi_vessel_arrival + timedelta(hours=TS_WORK_HOUR),
                ),
            ] + s2
        elif last_feasible_split_mode == "after_portstay":
            # PortStay 직후 환적: s1은 그대로 두고, s2의 첫 PortStay를 뒤로 미루어 PO slack 이후 phase-out 하고, s2 앞에 환적 일정 추가.
            # 앞 선박이 PortStay까지 마침
            s1.append(s2[0])
            s2 = s2[1:]

            unload_start = s1[-1].pilot_out_end
            po_vessel_departure = s1[-1].pilot_out_end + timedelta(hours=TS_WORK_HOUR)
            pi_vessel_arrival = po_vessel_departure + timedelta(hours=TS_SLACK_HOUR)
            pi_vessel_departure = pi_vessel_arrival + timedelta(hours=TS_WORK_HOUR)
            ts_port_code = s1[-1].port_code
            ts_port_seq = s1[-1].port_seq
            s1.extend(
                [
                    TransshipmentUnload(
                        lane_code=lane_code,
                        proforma_name=proforma_name,
                        position_no=position_no,
                        ts_port_code=ts_port_code,
                        ts_port_seq=ts_port_seq,
                        unload_start=unload_start,
                        unload_end=po_vessel_departure,
                    ),
                    PhaseOut(
                        lane_code=lane_code,
                        proforma_name=proforma_name,
                        position_no=position_no,
                        phase_out_time=po_vessel_departure,
                        phase_out_port_code=ts_port_code,
                        phase_out_port_seq=ts_port_seq,
                    ),
                ]
            )
            s2[0].sea_sail_start = pi_vessel_departure
            s2 = [
                PhaseIn(
                    lane_code=lane_code,
                    proforma_name=proforma_name,
                    position_no=position_no,
                    phase_in_port_code=ts_port_code,
                    phase_in_port_seq=ts_port_seq,
                    phase_in_time=pi_vessel_arrival,
                ),
                TransshipmentLoad(
                    lane_code=lane_code,
                    proforma_name=proforma_name,
                    position_no=position_no,
                    ts_port_code=ts_port_code,
                    ts_port_seq=ts_port_seq,
                    load_start=pi_vessel_arrival,
                    load_end=pi_vessel_departure,
                ),
            ] + s2

        else:
            raise ValueError(f"split_inlane_schedule: invalid last_feasible_split_mode {last_feasible_split_mode!r}")
    elif len(s1) > 0 and len(s2) == 0:
        # 환적 없음. 기존 선박이 서비스 종료까지 수행 후 Phase Out.
        pass
    else:
        # remain cases:
        #   len(s1) == 0 and len(s2) == 0: 이거는 어차피 입력 때 예외 처리되므로 아님.
        #   len(s1) == 0 and len(s2) > 0: 이 경우가 발생한 것인데, PLANNING_START 시점에서는 이미 D/D나 반선 일정을 충족시킬 수 있는 상황이 아님. 입력 데이터 오류임.
        print(len(s1), len(s2))
        raise ValueError(
            "split_inlane_schedule: Unexpected case. This means that the provided schedule cannot meet the D/D or redelivery schedule starting from PLANNING_START. Please check the input data for the vessel's current assignment and the D/D/redelivery schedule."
        )

    return s1, s2


def make_inlane_schedule(
    lane_code: str, proforma_name: str, position_no: int, start_time: datetime | None, end_time: datetime | None
) -> list[InLaneSail]:
    """
    `start_time`부터 min(서비스 종료, end_time)까지 지정된 선박의 inlane sail 일정을 생성한다.
    """

    if INSTANCE_DATA is None:
        raise ValueError("make_inlane_schedule: solver globals are not initialized.")

    version = lookup_version(lane_code, proforma_name)

    service_duration = version["service_duration"]
    position_start = get_service_start_datetime(lane_code, proforma_name, position_no)  # ETA임
    position_end = get_service_end_datetime(lane_code, proforma_name, position_no)

    port_stays: list[PortStay] = []
    n_round_trips = 0

    while position_start + timedelta(days=n_round_trips * service_duration) < position_end:
        trip_offset = position_start + timedelta(days=n_round_trips * service_duration)
        next_trip_offset = trip_offset + timedelta(days=service_duration)
        port_rotation = version["port_rotation"] if next_trip_offset >= position_end else version["port_rotation"][:-1]
        for rotation in port_rotation:
            port_stays.append(
                PortStay(
                    lane_code=lane_code,
                    proforma_name=proforma_name,
                    position_no=position_no,
                    port_code=rotation["port_code"],
                    port_seq=rotation["port_seq"],
                    pilot_in_start=trip_offset + timedelta(minutes=rotation["eta_offset_minutes"]),
                    berthing_start=trip_offset + timedelta(minutes=rotation["etb_offset_minutes"]),
                    berthing_end=trip_offset + timedelta(minutes=rotation["etd_offset_minutes"]),
                    pilot_out_end=trip_offset
                    + timedelta(minutes=rotation["etd_offset_minutes"] + rotation["pilot_out_minutes"]),
                )
            )
        n_round_trips += 1

    if len(port_stays) == 0:
        raise ValueError("make_inlane_schedule: no port stays were generated.")

    schedule: list[VesselScheduleEvent] = [
        PhaseIn(
            lane_code=lane_code,
            proforma_name=proforma_name,
            position_no=position_no,
            phase_in_time=port_stays[0].pilot_in_start,
            phase_in_port_code=port_stays[0].port_code,
            phase_in_port_seq=port_stays[0].port_seq,
        ),
    ]

    for current_stay, next_stay in zip(port_stays, port_stays[1:]):
        distance = lookup_distance(current_stay.port_code, next_stay.port_code)
        schedule.extend(
            [
                current_stay,
                InLaneSail(
                    lane_code=lane_code,
                    proforma_name=proforma_name,
                    position_no=position_no,
                    sea_sail_start=current_stay.pilot_out_end,
                    sea_sail_end=next_stay.pilot_in_start,
                    from_port_code=current_stay.port_code,
                    from_port_seq=current_stay.port_seq,
                    to_port_code=next_stay.port_code,
                    to_port_seq=next_stay.port_seq,
                    distance=distance,
                    avg_speed=distance
                    / ((next_stay.pilot_in_start - current_stay.pilot_out_end).total_seconds() / 3600),
                ),
            ]
        )
    schedule.append(port_stays[-1])

    schedule.append(
        PhaseOut(
            lane_code=lane_code,
            proforma_name=proforma_name,
            position_no=position_no,
            phase_out_time=port_stays[-1].pilot_out_end,
            phase_out_port_code=port_stays[-1].port_code,
            phase_out_port_seq=port_stays[-1].port_seq,
        )
    )
    # start_time과 end_time 사이에 있는 일정만 반환
    if start_time is not None:
        schedule = [s for s in schedule if event_end_time(s) >= start_time]
    if end_time is not None:
        schedule = [s for s in schedule if event_start_time(s) <= end_time]

    return schedule


def make_inlane_schedule_for_vessel(vessel_code: str) -> list[InLaneSail]:
    """
    PLANNING_START부터 서비스 종료까지 지정된 선박의 inlane sail 일정을 생성한다.
    """
    if INSTANCE_DATA is None:
        raise ValueError("make_inlane_schedule: solver globals are not initialized.")

    vessel = lookup_vessel(vessel_code)
    current_assignment = vessel["current_assignment"]
    if current_assignment is None:
        raise ValueError(f"Vessel {vessel_code} has no current assignment, cannot generate inlane schedule.")

    lane_code = current_assignment["lane_code"]
    proforma_name = current_assignment["proforma_name"]
    position_no = current_assignment["position_no"]

    schedule = make_inlane_schedule(lane_code, proforma_name, position_no, PLANNING_START, PLANNING_END)

    # 만약 선박 스케줄 첫 이벤트가 PLANNING_START보다 나중이라면,
    # 항로-포지션이 앞서서 계획되었고 사용할 선박도 결정되어 입력되었으나, 그 항로-포지션의 첫 출항이 PLANNING_START보다 늦은 경우임
    #   1. 이 경우 할당된 선박이 용선이어서 Delivery가 PLANNING_START보다 나중일 수도 있음.
    #   2. 자사선인 경우 반드시 PLANNING_START와 첫 스케줄 사이 공백이 있어서는 안됨.
    first_schedule_start_time = event_start_time(schedule[0])
    delivered = vessel["available_from"] is not None and (
        PLANNING_START <= vessel["available_from"] <= first_schedule_start_time
    )
    if first_schedule_start_time >= PLANNING_START:
        if vessel["available_from_port_code"] is None:
            raise ValueError(
                f"Vessel {vessel_code} has available_from={vessel['available_from']} but no available_from_port_code, cannot generate idle schedule before first inlane event."
            )

        idle_start = max(vessel["available_from"], PLANNING_START) if delivered else PLANNING_START
        if first_schedule_start_time > idle_start:
            schedule.insert(
                0,
                Idle(
                    port_code=event_start_port_code(first_inlane_event(schedule)),
                    idle_start=idle_start,
                    idle_end=first_schedule_start_time,
                ),
            )
        if delivered:
            schedule.insert(
                0,
                Delivery(
                    delivery_time=vessel["available_from"],
                    delivery_port_code=vessel["available_from_port_code"],
                ),
            )
    return schedule


def algorithm(instance_data: InstanceData, timelimit: int) -> CascadingSolution:
    # 1. 현행 LRS는 변경하지 않는다.
    # 2. 다만, Dry-Dock 또는 반선으로 인해 선박을 어느 시점부터 운영할 수 없게 되는 경우 가상의 선박을 이용한다.
    # 3. 미래의 proforma 수요는 모두 가상의 선박으로 충족한다.
    # 4. Lane position은 available positions 중 가장 낮은 번호를 선언한다. (예: 8주 길이 lane중 3척 투입이면 1, 2, 3을 선언)

    init_solver_globals(instance_data)

    # solutions
    declared_positions = []
    actual_vessel_schedules = {}
    virtual_vessel_schedules = {}

    # region Positions
    # 포지션 선언이 필요한 lane-proforma (version)
    new_proformas = [
        (lane["lane_code"], version["proforma_name"], version["available_positions"], version["own_vessel_count"])
        for lane in instance_data.service_lanes
        for version in lane["versions"]
        if version["available_positions"]
    ]

    for lane_code, proforma_name, available_positions, own_vessel_count in new_proformas:
        for declared_position_no in sorted(available_positions)[:own_vessel_count]:
            declared_positions.append(
                DeclaredPosition(
                    lane_code=lane_code,
                    proforma_name=proforma_name,
                    declared_position_no=declared_position_no,
                )
            )
    # endregion

    # region 선박 케이스 분류 (상기 11개 경우의 수)
    """모든 선박의 planning horizon 내의 스케줄을 명시해야 한다.

    각각의 선박들은 다음의 경우들 중 하나에 해당한다.
    1. Assigned (to a lane) / No Dry-Dock / No Redelivery
    2. Assigned / Dry-Dock planned / No Redelivery
    3. Assigned / No Dry-Dock / Redelivery planned
    4. Assigned / Dry-Dock planned / Redelivery planned
    5. Not assigned / No Dry-Dock / No Redelivery
    6. Not assigned / Dry-Dock / No Redelivery
    7. Not assigned / No Dry-Dock / Redelivery
    8. Not assigned / Dry-Dock / Redelivery after Dry-Dock
    
    Note: 반선 일정은 D/D 일정 앞에 있을 수 없음. 그런 경우 No D/D임.
    """
    vessel_cases = {i: [] for i in range(1, 9)}
    for vessel in instance_data.vessels:
        vessel_code = vessel["vessel_code"]
        current_assignment = vessel["current_assignment"]

        next_dock_in = (
            vessel["next_dock_in"]
            if vessel["next_dock_in"] is not None and vessel["next_dock_in"] <= PLANNING_END
            else None
        )
        next_dock_out = vessel["next_dock_out"]
        available_to = (
            vessel["available_to"]
            if vessel["available_to"] is not None and vessel["available_to"] <= PLANNING_END
            else None
        )

        # next_dock_in, next_dock_in_port_code, next_dock_out은 모두 None이거나 not None이다.
        if (next_dock_in is None) != (vessel["next_dock_port_code"] is None) != (next_dock_out is None):
            raise ValueError(
                f"Vessel {vessel_code} has inconsistent dry-dock information. "
                f"next_dock_in={next_dock_in}, next_dock_port_code={vessel['next_dock_port_code']}, next_dock_out={next_dock_out}"
            )

        if next_dock_in is not None and available_to is not None and next_dock_in >= available_to:
            # 반선 일정이 D/D 일정보다 이후에 있는 경우, D/D가 없는 것으로 간주한다.
            next_dock_in = None
            next_dock_out = None
            vessel["next_dock_in"] = None
            vessel["next_dock_out"] = None
            vessel["next_dock_port_code"] = None
        if next_dock_out is not None and available_to is not None and next_dock_out >= available_to:
            # D/D 일정과 반선 일정이 겹치는 경우 반선은 무시하고 D/D를 수행하는 계획을 세우도록 한다.
            # 반선은 알아서...? 데이터가 잘못된 것이라 가정
            available_to = None
            vessel["available_to"] = None
            vessel["available_to_port_code"] = None

        case_suffix = {
            (True, False, False): 1,
            (True, True, False): 2,
            (True, False, True): 3,
            (True, True, True): 4,
            (False, False, False): 5,
            (False, True, False): 6,
            (False, False, True): 7,
            (False, True, True): 8,
        }[(current_assignment is not None, next_dock_in is not None, available_to is not None)]

        if current_assignment:
            if case_suffix == 1:
                vessel_cases[1].append(vessel_code)
                continue

            lane_code = current_assignment["lane_code"]
            proforma_name = current_assignment["proforma_name"]

            payload = (
                (vessel_code, next_dock_in)
                if case_suffix == 2
                else (vessel_code, available_to) if case_suffix == 3 else (vessel_code, next_dock_in, available_to)
            )
            vessel_cases[case_suffix].append(payload)
        else:
            if case_suffix == 5:
                vessel_cases[5].append(vessel_code)
            else:
                payload = (
                    (vessel_code, next_dock_in)
                    if case_suffix == 6
                    else (vessel_code, available_to) if case_suffix == 7 else (vessel_code, next_dock_in, available_to)
                )
                vessel_cases[case_suffix].append(payload)
    # endregion

    for k, v in vessel_cases.items():
        print(f"Case {k}: {[(x[0] if isinstance(x, tuple) else x) for x in v]}")

    # region Case by Case Vessel Schedule Generation
    # Case 1: Assigned / No Dry-Dock / No Redelivery
    for vessel_code in vessel_cases[1]:
        schedule = make_inlane_schedule_for_vessel(vessel_code)
        if event_end_time(schedule[-1]) < PLANNING_END:
            if not isinstance(schedule[-1], PhaseOut):
                raise ValueError(
                    f"Case 1 vessel {vessel_code}: Last event in schedule is not PhaseOut. "
                    f"Last event: {schedule[-1]!r}"
                )
            schedule.extend(
                [
                    Idle(
                        port_code=schedule[-1].phase_out_port_code,
                        idle_start=schedule[-1].phase_out_time,
                        idle_end=PLANNING_END,
                    ),
                ]
            )
        actual_vessel_schedules[vessel_code] = schedule

    for case_no, has_drydock, has_redelivery in ((2, True, False), (3, False, True), (4, True, True)):
        for case_data in vessel_cases[case_no]:
            vessel_code = case_data[0]
            vessel = lookup_vessel(vessel_code)

            next_dock_in = case_data[1] if has_drydock else None
            next_dock_port_code = vessel["next_dock_port_code"] if has_drydock else None
            next_dock_out = vessel["next_dock_out"] if has_drydock else None
            available_to = case_data[-1] if has_redelivery else None
            redelivery_port_code = vessel["available_to_port_code"] if has_redelivery else None

            target_time = next_dock_in if has_drydock else available_to
            target_port_code = next_dock_port_code if has_drydock else redelivery_port_code

            if target_time is None or target_port_code is None:
                raise ValueError(
                    f"Case {case_no} vessel {vessel_code}: Missing target_time or target_port_code. "
                    f"target_time={target_time}, target_port_code={target_port_code}"
                )

            # TODO 아래 schaules len == 0인 경우 대응(free from = service end인 경우)
            # PLANNING_START부터 서비스 종료까지의 서비스 항로 일정
            schedule = make_inlane_schedule_for_vessel(vessel_code)
            _original_schedule = schedule.copy()

            # dry-dock or redelivery 일정을 충족시키기 위해 스케줄 분리, 분리된 지점에서 가상 선박으로 환적 발생
            schedule, virtual_schedule = split_inlane_schedule(schedule, target_time, target_port_code)

            current_assignment = vessel["current_assignment"]
            version = lookup_version(current_assignment["lane_code"], current_assignment["proforma_name"])

            phase_out_event = schedule[-1]
            # Dry-Dock 또는 Redelivery로 인해 가상의 선박이 필요한 경우
            if virtual_schedule:
                virtual_vessel_code = get_new_virtual_vessel_code()
                virtual_vessel_schedules[virtual_vessel_code] = virtual_schedule

            if has_drydock:
                schedule.extend(
                    [
                        out_sail_or_idle(
                            from_port_code=phase_out_event.phase_out_port_code,
                            sea_sail_start=phase_out_event.phase_out_time,
                            sea_sail_end=next_dock_in,
                            to_port_code=next_dock_port_code,
                        ),
                        DryDock(
                            dock_in=next_dock_in,
                            dock_port_code=next_dock_port_code,
                            dock_out=next_dock_out,
                        ),
                    ]
                )
                schedule += (
                    [
                        out_sail_or_idle(
                            from_port_code=next_dock_port_code,
                            sea_sail_start=next_dock_out,
                            sea_sail_end=available_to,
                            to_port_code=redelivery_port_code,
                        ),
                        Redelivery(
                            redelivery_port_code=redelivery_port_code,
                            redelivery_time=available_to,
                        ),
                    ]
                    if has_redelivery
                    else [
                        Idle(
                            port_code=next_dock_port_code,
                            idle_start=next_dock_out,
                            idle_end=PLANNING_END,
                        )
                    ]
                )
            elif has_redelivery:
                schedule.extend(
                    [
                        out_sail_or_idle(
                            from_port_code=phase_out_event.phase_out_port_code,
                            sea_sail_start=phase_out_event.phase_out_time,
                            sea_sail_end=available_to,
                            to_port_code=redelivery_port_code,
                        ),
                        Redelivery(
                            redelivery_port_code=redelivery_port_code,
                            redelivery_time=available_to,
                        ),
                    ]
                )

            actual_vessel_schedules[vessel_code] = schedule

    for case_no, has_drydock, has_redelivery in (
        (5, False, False),
        (6, True, False),
        (7, False, True),
        (8, True, True),
    ):
        for case_data in vessel_cases[case_no]:
            vessel_code = case_data if case_no == 5 else case_data[0]
            vessel = lookup_vessel(vessel_code)

            next_dock_in = case_data[1] if has_drydock else None
            next_dock_port_code = vessel["next_dock_port_code"] if has_drydock else None
            next_dock_out = vessel["next_dock_out"] if has_drydock else None
            available_from = vessel["available_from"]
            available_from_port_code = vessel["available_from_port_code"]
            available_to = case_data[-1] if has_redelivery else None
            redelivery_port_code = vessel["available_to_port_code"] if has_redelivery else None

            # 둘 다 not None이어야 함
            if available_from is None or available_from_port_code is None:
                raise ValueError(
                    f"Case {case_no} vessel {vessel_code}: Missing available_from or available_from_port_code. "
                )

            if available_from is None:
                # 미래에 용선/신조선되는 경우가 아닌데 not assigned인 경우 어디서 놀고 있는 경우임. 이 데이터는 어떻게 받을지? TODO
                # 일단은 curr assignment가 없는 경우에는 반드시 Planning start 뒤에 delivery된다고 가정하자.
                raise NotImplementedError(
                    f"Case {case_no} vessel {vessel_code}: No available_from time specified. "
                    "This case is not implemented in the current algorithm. Please provide available_from time for this vessel."
                )

            if case_no == 5:
                actual_vessel_schedules[vessel_code] = [
                    Delivery(
                        delivery_port_code=available_from_port_code,
                        delivery_time=available_from,
                    ),
                    Idle(
                        port_code=available_from_port_code,
                        idle_start=available_from,
                        idle_end=PLANNING_END,
                    ),
                ]
                continue

            if has_drydock:
                schedule.extend(
                    [
                        out_sail_or_idle(
                            from_port_code=available_from_port_code,
                            sea_sail_start=available_from,
                            sea_sail_end=next_dock_in,
                            to_port_code=next_dock_port_code,
                        ),
                        DryDock(
                            dock_port_code=next_dock_port_code,
                            dock_in=next_dock_in,
                            dock_out=next_dock_out,
                        ),
                    ]
                )
                schedule += (
                    [
                        out_sail_or_idle(
                            from_port_code=next_dock_port_code,
                            sea_sail_start=next_dock_out,
                            sea_sail_end=available_to,
                            to_port_code=redelivery_port_code,
                        ),
                        Redelivery(
                            redelivery_port_code=redelivery_port_code,
                            redelivery_time=available_to,
                        ),
                    ]
                    if has_redelivery
                    else [
                        Idle(
                            port_code=next_dock_port_code,
                            idle_start=next_dock_out,
                            idle_end=PLANNING_END,
                        )
                    ]
                )
            elif has_redelivery:
                schedule.extend(
                    [
                        out_sail_or_idle(
                            from_port_code=available_from_port_code,
                            sea_sail_start=available_from,
                            sea_sail_end=available_to,
                            to_port_code=redelivery_port_code,
                        ),
                        Redelivery(
                            redelivery_port_code=redelivery_port_code,
                            redelivery_time=available_to,
                        ),
                    ]
                )

            actual_vessel_schedules[vessel_code] = schedule

    # endregion

    global NUM_VIRTUAL_VESSELS_USED

    # region Cover new proformas with surplus vessels
    def _find_surplus_vessel(
        start_time: datetime,
        start_port_code: str,
        end_time: datetime,
        end_port_code: str,
        required_capacity_teu: int,
        required_reefer_plug: int,
    ) -> str | None:
        candidate_vessels = []
        for vessel_code, schedule in actual_vessel_schedules.items():
            # start_time, end_time에 걸치는 일정을 모두 필터
            schedule_during_period = [
                (i, s)
                for i, s in enumerate(schedule)
                if event_end_time(s) >= start_time and event_start_time(s) <= end_time
            ]
            if len(schedule_during_period) == 0:
                continue
            if any(not isinstance(s, (Idle, OutLaneSail)) for i, s in schedule_during_period):
                continue

            if schedule_during_period[-1][0] < len(schedule) - 1:
                remain_schedule = schedule[schedule_during_period[-1][0] + 1 :]
            else:
                remain_schedule = []

            schedule_during_period = [s for _, s in schedule_during_period]
            depart_time = event_start_time(schedule_during_period[0])
            depart_port_code = event_start_port_code(schedule_during_period[0])
            arrival_time = event_start_time(remain_schedule[0]) if remain_schedule else None
            arrival_port_code = event_start_port_code(remain_schedule[0]) if remain_schedule else None

            # depart_time에 depart_port_code에서 출발하여 start_port_code에 start_time까지 20 knot 이내로 도착 가능해야 함
            if depart_port_code != start_port_code:
                distance = lookup_distance(depart_port_code, start_port_code)
                sea_time = (start_time - depart_time).total_seconds() / 3600
                if sea_time <= 0 or distance / (sea_time + 1e-5) > 20:
                    continue

            # end_time에 end_port_code에서 출발하여 arrival_port_code에 arrival_time까지 20 knot 이내로 도착 가능해야 함 (arrival_time이 None인 경우는 end_time 이후 스케줄이 없다는 뜻이므로 패스)
            if arrival_time is not None and arrival_port_code != end_port_code:
                distance = lookup_distance(end_port_code, arrival_port_code)
                sea_time = (arrival_time - end_time).total_seconds() / 3600
                if sea_time <= 0 or distance / (sea_time + 1e-5) > 20:
                    continue

            # required_capacity와 required_reefer_plug 만족하는지 체크
            vessel = lookup_vessel(vessel_code)
            tolerance = 0.05
            if not (
                required_capacity_teu * (1 - tolerance)
                <= vessel["capacity_teu"]
                <= required_capacity_teu * (1 + tolerance)
                and required_reefer_plug <= vessel["reefer_plug"]  # TODO
            ):
                continue

            distance = lookup_distance(depart_port_code, start_port_code)
            sea_time = (start_time - depart_time).total_seconds() / 3600
            candidate_vessels.append((vessel_code, sea_time))
        return min(candidate_vessels, key=lambda x: x[1])[0] if candidate_vessels else None

    for declared_position in declared_positions:
        lane_code = declared_position.lane_code
        proforma_name = declared_position.proforma_name
        declared_position_no = declared_position.declared_position_no
        version = lookup_version(lane_code, proforma_name)

        required_capacity_teu = version["required_capacity_teu"]
        required_reefer_plug = version["required_reefer_plug"]

        schedule = make_inlane_schedule(lane_code, proforma_name, declared_position_no, PLANNING_START, PLANNING_END)
        vessel_code = _find_surplus_vessel(
            event_start_time(schedule[0]),
            event_start_port_code(schedule[0]),
            event_end_time(schedule[-1]),
            event_end_port_code(schedule[-1]),
            required_capacity_teu,
            required_reefer_plug,
        )

        if vessel_code is not None:
            old_schedule = actual_vessel_schedules[vessel_code]
            actual_vessel_schedules[vessel_code] = _build_surplus_replacement_schedule(
                old_schedule,
                schedule,
                include_boundary=False,
            )
        else:
            print(
                "No surplus vessel found for declared position: "
                f"lane={lane_code}, proforma={proforma_name}, position={declared_position_no}, "
                f"window={event_start_time(schedule[0])} @ {event_start_port_code(schedule[0])}"
                f" -> {event_end_time(schedule[-1])} @ {event_end_port_code(schedule[-1])}, "
                f"required_capacity_teu={required_capacity_teu}, required_reefer_plug={required_reefer_plug}"
            )
            virtual_vessel_code = get_new_virtual_vessel_code()
            virtual_vessel_schedules[virtual_vessel_code] = schedule

    # endregion

    # region 현행 LRS에서 가상 선박이 투입된 스케줄을 최대한 남은 보유 선박으로 커버하도록 솔루션 수정

    virtual_vessels_to_remove = []
    for virtual_vessel_code, schedule in virtual_vessel_schedules.items():
        first_event = first_inlane_event(schedule)
        lane_code = first_event.lane_code
        proforma_name = first_event.proforma_name
        version = lookup_version(lane_code, proforma_name)

        required_capacity_teu = version["required_capacity_teu"]
        required_reefer_plug = version["required_reefer_plug"]

        vessel_code = _find_surplus_vessel(
            event_start_time(schedule[0]),
            event_start_port_code(schedule[0]),
            event_end_time(schedule[-1]),
            event_end_port_code(schedule[-1]),
            required_capacity_teu,
            required_reefer_plug,
        )

        if vessel_code is not None:
            old_schedule = actual_vessel_schedules[vessel_code]
            new_schedule = _build_surplus_replacement_schedule(
                old_schedule,
                schedule,
                include_boundary=True,
            )
            if event_end_time(new_schedule[-1]) < PLANNING_END:
                new_schedule.append(
                    Idle(
                        port_code=event_end_port_code(new_schedule[-1]),
                        idle_start=event_end_time(new_schedule[-1]),
                        idle_end=PLANNING_END,
                    )
                )
            actual_vessel_schedules[vessel_code] = new_schedule
            virtual_vessels_to_remove.append(virtual_vessel_code)

        else:
            print(
                "No surplus vessel found for cascading: "
                f"virtual_vessel={virtual_vessel_code}, "
                f"lane={lane_code}, proforma={proforma_name}, "
                f"window={event_start_time(schedule[0])} @ {event_start_port_code(schedule[0])}"
                f" -> {event_end_time(schedule[-1])} @ {event_end_port_code(schedule[-1])}, "
                f"required_capacity_teu={required_capacity_teu}, required_reefer_plug={required_reefer_plug}"
            )
    # endregion

    # Remove virtual vessels that have been cascaded
    for virtual_vessel_code in virtual_vessels_to_remove:
        del virtual_vessel_schedules[virtual_vessel_code]
        NUM_VIRTUAL_VESSELS_USED -= 1

    if len(instance_data.vessels) != len(actual_vessel_schedules):
        raise ValueError("Mismatch between actual and scheduled vessels")

    print(f"""계산 완료
- 실제 선박 스케줄 수: {len(actual_vessel_schedules)}
- 사용된 가상 선박 수: {NUM_VIRTUAL_VESSELS_USED}
""")

    return CascadingSolution(
        declared_positions=declared_positions,
        vessel_schedules=actual_vessel_schedules,
        virtual_vessel_schedules=virtual_vessel_schedules,
        num_virtual_vessels_used=NUM_VIRTUAL_VESSELS_USED,
    )

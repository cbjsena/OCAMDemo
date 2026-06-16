from __future__ import annotations

import os
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any, Sequence

from ocam.models import InstanceData

from .config import ENABLE_NUMPY_SCREENING, NUMPY_SCREENING_VERBOSE

STATUS_INCOMPATIBLE = 0
STATUS_COMPATIBLE_REJECTED = 1
STATUS_PASSED = 2


@dataclass(frozen=True)
class ScreenConnection:
    from_port_code: str
    from_time_seconds: float
    to_port_code: str
    to_time_seconds: float


@dataclass(frozen=True)
class ScreenRow:
    vessel_capacity: float
    vessel_reefer: float
    required_capacity: float
    required_reefer: float
    base_valid: bool
    connections: tuple[ScreenConnection, ...]


@dataclass
class NumpyScreeningStats:
    enabled: bool = ENABLE_NUMPY_SCREENING
    load_status: str = "not_attempted"
    fallback_reason: str = ""
    batch_calls: int = 0
    fallback_calls: int = 0
    rows_screened: int = 0
    rows_passed: int = 0
    rows_compatible_rejected: int = 0
    rows_incompatible: int = 0
    elapsed_seconds: float = 0.0
    labels: Counter[str] | None = None

    def __post_init__(self) -> None:
        if self.labels is None:
            self.labels = Counter()


_NUMPY_STATS = NumpyScreeningStats()
_NUMPY_MODULE: Any | None = None
_LOAD_ATTEMPTED = False


def _numpy_screening_enabled() -> bool:
    if os.environ.get("OCAM_FORCE_NUMPY_SCREENING") == "1":
        return True
    return ENABLE_NUMPY_SCREENING


def reset_numpy_screening_stats() -> None:
    global _NUMPY_STATS
    _NUMPY_STATS = NumpyScreeningStats(enabled=_numpy_screening_enabled())


def format_numpy_screening_stats() -> str:
    stats = _NUMPY_STATS
    labels = ", ".join(f"{key}={value}" for key, value in sorted((stats.labels or Counter()).items()))
    if not labels:
        labels = "(none)"
    return (
        f"enabled={stats.enabled}, load_status={stats.load_status}, "
        f"fallback_reason={stats.fallback_reason or '-'}, batch_calls={stats.batch_calls}, "
        f"fallback_calls={stats.fallback_calls}, rows={stats.rows_screened}, "
        f"passed={stats.rows_passed}, compatible_rejected={stats.rows_compatible_rejected}, "
        f"incompatible={stats.rows_incompatible}, elapsed_seconds={stats.elapsed_seconds:.3f}, "
        f"labels={labels}"
    )


def _get_numpy():
    global _LOAD_ATTEMPTED, _NUMPY_MODULE
    _NUMPY_STATS.enabled = _numpy_screening_enabled()
    if not _NUMPY_STATS.enabled:
        _NUMPY_STATS.load_status = "disabled"
        _NUMPY_STATS.fallback_reason = "disabled by config"
        return None
    if os.environ.get("OCAM_DISABLE_NUMPY_SCREENING") == "1":
        _NUMPY_STATS.load_status = "disabled"
        _NUMPY_STATS.fallback_reason = "disabled by OCAM_DISABLE_NUMPY_SCREENING"
        return None
    if _NUMPY_MODULE is not None:
        return _NUMPY_MODULE
    if _LOAD_ATTEMPTED:
        return None
    _LOAD_ATTEMPTED = True
    try:
        import numpy as np
    except Exception as exc:
        _NUMPY_STATS.load_status = "fallback"
        _NUMPY_STATS.fallback_reason = f"numpy import failed: {type(exc).__name__}: {exc}"
        if NUMPY_SCREENING_VERBOSE:
            print(
                f"wsgoh/mip_twostage_v10_numpy screening fallback: {_NUMPY_STATS.fallback_reason}",
                flush=True,
            )
        return None
    _NUMPY_MODULE = np
    _NUMPY_STATS.load_status = "ready"
    return np


def numpy_screening_may_run() -> bool:
    return _get_numpy() is not None


def _port_ids_and_distances(instance_data: InstanceData, rows: Sequence[ScreenRow]) -> tuple[dict[str, int], list[list[float]]]:
    port_codes: set[str] = set()
    for row in rows:
        for connection in row.connections:
            port_codes.add(connection.from_port_code)
            port_codes.add(connection.to_port_code)
    for distance in instance_data.distances:
        from_port = distance.get("from_port_code")
        to_port = distance.get("to_port_code")
        if from_port in port_codes:
            port_codes.add(from_port)
        if to_port in port_codes:
            port_codes.add(to_port)

    ordered_ports = sorted(port_codes)
    port_to_id = {port_code: index for index, port_code in enumerate(ordered_ports)}
    port_count = max(1, len(ordered_ports))
    matrix = [[-1.0 for _ in range(port_count)] for _ in range(port_count)]
    for index in range(port_count):
        matrix[index][index] = 0.0
    for distance in instance_data.distances:
        from_port = distance.get("from_port_code")
        to_port = distance.get("to_port_code")
        if from_port in port_to_id and to_port in port_to_id:
            matrix[port_to_id[from_port]][port_to_id[to_port]] = float(distance["distance"])
    return port_to_id, matrix


def _connection_ok_scalar(
    from_port: int,
    from_time: float,
    to_port: int,
    to_time: float,
    distance_matrix,
) -> bool:
    port_count = int(distance_matrix.shape[0])
    if from_port < 0 or to_port < 0 or from_port >= port_count or to_port >= port_count:
        return False
    if from_port == to_port:
        return from_time <= to_time
    if from_time >= to_time:
        return False
    distance = float(distance_matrix[from_port, to_port])
    duration_hours = (to_time - from_time) / 3600.0
    return distance >= 0.0 and duration_hours > 0.0 and distance / (duration_hours + 1e-5) <= 20.0


def _connection_ok_vector(np, from_ports, from_times, to_ports, to_times, distance_matrix):
    port_count = int(distance_matrix.shape[0])
    valid = (from_ports >= 0) & (to_ports >= 0) & (from_ports < port_count) & (to_ports < port_count)
    ok = np.zeros(from_ports.shape[0], dtype=bool)

    same = valid & (from_ports == to_ports)
    ok[same] = from_times[same] <= to_times[same]

    different = valid & (from_ports != to_ports) & (from_times < to_times)
    different_indices = np.where(different)[0]
    if different_indices.size:
        distances = distance_matrix[from_ports[different_indices], to_ports[different_indices]]
        durations = (to_times[different_indices] - from_times[different_indices]) / 3600.0
        ok[different_indices] = (distances >= 0.0) & (durations > 0.0) & (
            distances / (durations + 1e-5) <= 20.0
        )
    return ok


def _record_output(label: str, elapsed: float, statuses: Sequence[int]) -> None:
    output = [int(value) for value in statuses]
    _NUMPY_STATS.batch_calls += 1
    _NUMPY_STATS.rows_screened += len(output)
    _NUMPY_STATS.rows_passed += sum(1 for status in output if status == STATUS_PASSED)
    _NUMPY_STATS.rows_compatible_rejected += sum(1 for status in output if status == STATUS_COMPATIBLE_REJECTED)
    _NUMPY_STATS.rows_incompatible += sum(1 for status in output if status == STATUS_INCOMPATIBLE)
    _NUMPY_STATS.elapsed_seconds += elapsed
    _NUMPY_STATS.labels[label] += 1
    _NUMPY_STATS.load_status = "ready"


def _screen_rows_numpy(instance_data: InstanceData, rows: Sequence[ScreenRow], *, label: str) -> list[int] | None:
    np = _get_numpy()
    if np is None:
        _NUMPY_STATS.fallback_calls += 1
        return None
    if not rows:
        return []

    started = time.monotonic()
    try:
        port_to_id, distance_matrix_payload = _port_ids_and_distances(instance_data, rows)
        distance_matrix = np.asarray(distance_matrix_payload, dtype=np.float64)
        statuses: list[int] = []
        for row in rows:
            if (
                row.required_capacity <= 0.0
                or row.vessel_capacity < row.required_capacity * 0.95
                or row.vessel_capacity > row.required_capacity * 1.05
                or row.vessel_reefer < row.required_reefer
            ):
                statuses.append(STATUS_INCOMPATIBLE)
                continue
            if not row.base_valid:
                statuses.append(STATUS_COMPATIBLE_REJECTED)
                continue
            ok = True
            for connection in row.connections:
                if not _connection_ok_scalar(
                    port_to_id.get(connection.from_port_code, -1),
                    connection.from_time_seconds,
                    port_to_id.get(connection.to_port_code, -1),
                    connection.to_time_seconds,
                    distance_matrix,
                ):
                    ok = False
                    break
            statuses.append(STATUS_PASSED if ok else STATUS_COMPATIBLE_REJECTED)
    except Exception as exc:
        _NUMPY_STATS.load_status = "fallback"
        _NUMPY_STATS.fallback_reason = f"numpy row screening failed: {type(exc).__name__}: {exc}"
        _NUMPY_STATS.fallback_calls += 1
        if NUMPY_SCREENING_VERBOSE:
            print(
                f"wsgoh/mip_twostage_v10_numpy screening fallback: {_NUMPY_STATS.fallback_reason}",
                flush=True,
            )
        return None

    _record_output(label, time.monotonic() - started, statuses)
    return statuses


def screen_actual_candidate_compact_batch(
    *,
    vessel_capacity,
    vessel_reefer,
    event_count,
    event_start_seconds,
    event_end_seconds,
    event_start_port_ids,
    event_end_port_ids,
    event_insertable,
    target_required_capacity,
    target_required_reefer,
    target_start_seconds,
    target_end_seconds,
    target_start_port_ids,
    target_end_port_ids,
    distance_matrix,
) -> tuple[list[int], list[int], list[int]] | None:
    np = _get_numpy()
    if np is None:
        _NUMPY_STATS.fallback_calls += 1
        return None

    vessel_count = int(vessel_capacity.shape[0])
    target_count = int(target_required_capacity.shape[0])
    row_count = vessel_count * target_count
    if row_count == 0:
        return [], [], []

    started = time.monotonic()
    try:
        statuses = np.full(row_count, STATUS_COMPATIBLE_REJECTED, dtype=np.uint8)
        first_indices = np.full(row_count, -1, dtype=np.int64)
        last_indices = np.full(row_count, -1, dtype=np.int64)
        max_events = int(event_start_seconds.shape[1])
        vessel_indices = np.arange(vessel_count)
        event_indices = np.arange(max_events)
        clipped_event_count = np.minimum(event_count, max_events)
        valid_event_mask = event_indices[None, :] < clipped_event_count[:, None]

        for target_index in range(target_count):
            row_start = target_index * vessel_count
            row_end = row_start + vessel_count
            required_capacity = float(target_required_capacity[target_index])
            required_reefer = float(target_required_reefer[target_index])
            service_start = float(target_start_seconds[target_index])
            service_end = float(target_end_seconds[target_index])
            service_start_port = int(target_start_port_ids[target_index])
            service_end_port = int(target_end_port_ids[target_index])

            compatible = (
                (required_capacity > 0.0)
                & (vessel_capacity >= required_capacity * 0.95)
                & (vessel_capacity <= required_capacity * 1.05)
                & (vessel_reefer >= required_reefer)
            )
            statuses[row_start:row_end] = np.where(
                compatible,
                STATUS_COMPATIBLE_REJECTED,
                STATUS_INCOMPATIBLE,
            )
            if not bool(np.any(compatible)):
                continue

            overlap = valid_event_mask & (event_end_seconds >= service_start) & (event_start_seconds <= service_end)
            any_overlap = np.any(overlap, axis=1)
            first = np.where(any_overlap, np.argmax(overlap, axis=1), -1)
            last = np.where(any_overlap, max_events - 1 - np.argmax(overlap[:, ::-1], axis=1), -1)
            insertable_ok = np.all((~overlap) | (event_insertable != 0), axis=1)
            base_valid = any_overlap & insertable_ok & (first > 0)

            depart_indices = np.maximum(first - 1, 0)
            depart_ports = event_end_port_ids[vessel_indices, depart_indices]
            depart_times = event_end_seconds[vessel_indices, depart_indices]
            service_start_ports = np.full(vessel_count, service_start_port, dtype=np.int64)
            service_start_times = np.full(vessel_count, service_start, dtype=np.float64)
            connection_in_ok = _connection_ok_vector(
                np,
                depart_ports,
                depart_times,
                service_start_ports,
                service_start_times,
                distance_matrix,
            )

            suffix_indices = last + 1
            has_suffix = suffix_indices < clipped_event_count
            suffix_indices = np.minimum(np.maximum(suffix_indices, 0), max_events - 1)
            return_ports = event_start_port_ids[vessel_indices, suffix_indices]
            return_times = event_start_seconds[vessel_indices, suffix_indices]
            service_end_ports = np.full(vessel_count, service_end_port, dtype=np.int64)
            service_end_times = np.full(vessel_count, service_end, dtype=np.float64)
            connection_out_ok = _connection_ok_vector(
                np,
                service_end_ports,
                service_end_times,
                return_ports,
                return_times,
                distance_matrix,
            )
            connection_out_ok = (~has_suffix) | connection_out_ok

            passed = compatible & base_valid & connection_in_ok & connection_out_ok
            row_statuses = statuses[row_start:row_end]
            row_first = first_indices[row_start:row_end]
            row_last = last_indices[row_start:row_end]
            row_statuses[passed] = STATUS_PASSED
            row_first[passed] = first[passed]
            row_last[passed] = last[passed]
    except Exception as exc:
        _NUMPY_STATS.load_status = "fallback"
        _NUMPY_STATS.fallback_reason = f"numpy compact candidate failed: {type(exc).__name__}: {exc}"
        _NUMPY_STATS.fallback_calls += 1
        if NUMPY_SCREENING_VERBOSE:
            print(
                f"wsgoh/mip_twostage_v10_numpy screening fallback: {_NUMPY_STATS.fallback_reason}",
                flush=True,
            )
        return None

    _record_output("actual_candidate_compact", time.monotonic() - started, statuses.tolist())
    return (
        [int(value) for value in statuses.tolist()],
        [int(value) for value in first_indices.tolist()],
        [int(value) for value in last_indices.tolist()],
    )


def screen_actual_primary_compact_batch(
    *,
    vessel_capacity,
    vessel_reefer,
    has_fixed_coverage,
    protected_count,
    protected_start_seconds,
    protected_end_seconds,
    protected_start_port_ids,
    protected_end_port_ids,
    protected_type_codes,
    target_required_capacity,
    target_required_reefer,
    target_start_seconds,
    target_end_seconds,
    target_start_port_ids,
    target_end_port_ids,
    distance_matrix,
) -> list[int] | None:
    np = _get_numpy()
    if np is None:
        _NUMPY_STATS.fallback_calls += 1
        return None

    vessel_count = int(vessel_capacity.shape[0])
    target_count = int(target_required_capacity.shape[0])
    row_count = vessel_count * target_count
    if row_count == 0:
        return []

    started = time.monotonic()
    try:
        statuses = np.full(row_count, STATUS_COMPATIBLE_REJECTED, dtype=np.uint8)
        max_protected = int(protected_start_seconds.shape[1])
        protected_slots = np.arange(max_protected)
        valid_protected = protected_slots[None, :] < np.minimum(protected_count, max_protected)[:, None]

        for target_index in range(target_count):
            row_start = target_index * vessel_count
            row_end = row_start + vessel_count
            required_capacity = float(target_required_capacity[target_index])
            required_reefer = float(target_required_reefer[target_index])
            service_start = float(target_start_seconds[target_index])
            service_end = float(target_end_seconds[target_index])
            service_start_port = int(target_start_port_ids[target_index])
            service_end_port = int(target_end_port_ids[target_index])

            compatible = (
                (required_capacity > 0.0)
                & (vessel_capacity >= required_capacity * 0.95)
                & (vessel_capacity <= required_capacity * 1.05)
                & (vessel_reefer >= required_reefer)
                & (has_fixed_coverage == 0)
            )
            statuses[row_start:row_end] = np.where(
                compatible,
                STATUS_COMPATIBLE_REJECTED,
                STATUS_INCOMPATIBLE,
            )
            if not bool(np.any(compatible)):
                continue

            has_protected = protected_count > 0
            delivery_bad = np.any(
                valid_protected
                & (protected_type_codes == 1)
                & (service_start < protected_end_seconds),
                axis=1,
            )
            drydock_bad = np.any(
                valid_protected
                & (protected_type_codes == 2)
                & (protected_start_seconds < service_end)
                & (service_start < protected_end_seconds),
                axis=1,
            )
            redelivery_bad = np.any(
                valid_protected
                & (protected_type_codes == 3)
                & (service_end > protected_start_seconds),
                axis=1,
            )
            base_valid = compatible & has_protected & (~delivery_bad) & (~drydock_bad) & (~redelivery_bad)

            for vessel_index in np.where(base_valid)[0].tolist():
                count = int(min(protected_count[vessel_index], max_protected))
                blocks: list[tuple[float, float, int, int]] = []
                for protected_index in range(count):
                    blocks.append(
                        (
                            float(protected_start_seconds[vessel_index, protected_index]),
                            float(protected_end_seconds[vessel_index, protected_index]),
                            int(protected_start_port_ids[vessel_index, protected_index]),
                            int(protected_end_port_ids[vessel_index, protected_index]),
                        )
                    )
                blocks.append((service_start, service_end, service_start_port, service_end_port))
                blocks.sort(key=lambda item: item[0])
                ok = True
                for previous, next_block in zip(blocks, blocks[1:]):
                    if not _connection_ok_scalar(
                        previous[3],
                        previous[1],
                        next_block[2],
                        next_block[0],
                        distance_matrix,
                    ):
                        ok = False
                        break
                if ok:
                    statuses[row_start + vessel_index] = STATUS_PASSED
    except Exception as exc:
        _NUMPY_STATS.load_status = "fallback"
        _NUMPY_STATS.fallback_reason = f"numpy compact primary failed: {type(exc).__name__}: {exc}"
        _NUMPY_STATS.fallback_calls += 1
        if NUMPY_SCREENING_VERBOSE:
            print(
                f"wsgoh/mip_twostage_v10_numpy screening fallback: {_NUMPY_STATS.fallback_reason}",
                flush=True,
            )
        return None

    output = [int(value) for value in statuses.tolist()]
    _record_output("actual_primary_compact", time.monotonic() - started, output)
    return output


def screen_actual_candidate_batch(instance_data: InstanceData, rows: Sequence[ScreenRow]) -> list[int] | None:
    return _screen_rows_numpy(instance_data, rows, label="actual_candidate")


def screen_actual_primary_batch(instance_data: InstanceData, rows: Sequence[ScreenRow]) -> list[int] | None:
    return _screen_rows_numpy(instance_data, rows, label="actual_primary_virtual")

from __future__ import annotations

import ctypes
import csv
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import networkx as nx

from ocam.models import DeclaredPosition, Delivery, Idle, InLaneSail, InstanceData, PortStay, Redelivery
from .network.arcs import Arc, CanalSailArc, HorizonSailArc, SailArc
from .network.nodes import D, I, R, SE, SS, T, HorizonSailNode, Node, NodeGroup, PS
from .network.network import _to_networkx

CPP_DIR = Path(__file__).with_name("cpp")
LIB_PATH = CPP_DIR / "libocam_v6_lb_cpp.so"


def _epoch(dt: datetime | None) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        return str(int(dt.replace(tzinfo=timezone.utc).timestamp()))
    return str(int(dt.timestamp()))


def _write_tsv(path: Path, header: list[str], rows: list[list[object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write("\t".join(header) + "\n")
        for row in rows:
            f.write("\t".join("" if value is None else str(value) for value in row) + "\n")


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def _dt(epoch_seconds: str) -> datetime:
    return datetime.fromtimestamp(int(epoch_seconds), tz=timezone.utc).replace(tzinfo=None)


def _bool(value: str) -> bool:
    return value == "1"


def _node_id(value: str) -> str:
    return f"N{int(value)}"


def _group_id(value: str) -> str:
    return f"G{int(value)}"


def _ensure_cpp_library() -> None:
    source = CPP_DIR / "ocam_v6_lb_cpp.cpp"
    if LIB_PATH.exists() and LIB_PATH.stat().st_mtime >= source.stat().st_mtime:
        return
    subprocess.run(["bash", str(CPP_DIR / "build.sh")], check=True)


def _export_network_input_bundle(
    instance_data: InstanceData,
    network_positions: list[DeclaredPosition],
    bundle_dir: Path,
    dd_couplings: list[dict[str, object]] | None = None,
    model_name: str = "mcf_v6_lb",
) -> None:
    _write_tsv(
        bundle_dir / "meta.tsv",
        ["key", "value"],
        [
            ["planning_start", _epoch(instance_data.planning_horizon["start"])],
            ["planning_end", _epoch(instance_data.planning_horizon["end"])],
            ["model_name", model_name],
        ],
    )

    versions = []
    rotations = []
    version_positions = []
    for lane in instance_data.service_lanes:
        lane_code = lane["lane_code"]
        for version in lane["versions"]:
            proforma_name = version["proforma_name"]
            versions.append(
                [
                    lane_code,
                    proforma_name,
                    version["service_duration"],
                    _epoch(version["anchor_date"]),
                    _epoch(version.get("effective_to")),
                    version["required_capacity_teu"],
                    version["required_reefer_plug"],
                    version.get("own_vessel_count", ""),
                ]
            )
            declared_position_numbers = set(version.get("declared_positions") or [])
            available_position_numbers = set(version.get("available_positions") or [])
            for position_no in sorted(declared_position_numbers | available_position_numbers):
                version_positions.append(
                    [
                        lane_code,
                        proforma_name,
                        position_no,
                        int(position_no in declared_position_numbers),
                        int(position_no in available_position_numbers),
                    ]
                )
            for order, rotation in enumerate(version["port_rotation"]):
                rotations.append(
                    [
                        lane_code,
                        proforma_name,
                        order,
                        rotation["port_code"],
                        rotation["port_seq"],
                        rotation["eta_offset_minutes"],
                        rotation["etb_offset_minutes"],
                        rotation["etd_offset_minutes"],
                        rotation["pilot_out_minutes"],
                        rotation.get("direction", ""),
                    ]
                )
    _write_tsv(
        bundle_dir / "versions.tsv",
        [
            "lane_code",
            "proforma_name",
            "service_duration",
            "anchor_time",
            "effective_to",
            "required_capacity_teu",
            "required_reefer_plug",
            "own_vessel_count",
        ],
        versions,
    )
    _write_tsv(
        bundle_dir / "version_positions.tsv",
        ["lane_code", "proforma_name", "position_no", "is_declared", "is_available"],
        version_positions,
    )
    _write_tsv(
        bundle_dir / "rotations.tsv",
        [
            "lane_code",
            "proforma_name",
            "order",
            "port_code",
            "port_seq",
            "eta_offset_minutes",
            "etb_offset_minutes",
            "etd_offset_minutes",
            "pilot_out_minutes",
            "direction",
        ],
        rotations,
    )

    _write_tsv(
        bundle_dir / "vessels.tsv",
        [
            "vessel_code",
            "capacity_teu",
            "reefer_plug",
            "available_from",
            "available_from_port",
            "available_to",
            "available_to_port",
        ],
        [
            [
                vessel["vessel_code"],
                vessel["capacity_teu"],
                vessel["reefer_plug"],
                _epoch(vessel.get("available_from")),
                vessel.get("available_from_port_code") or "",
                _epoch(vessel.get("available_to")),
                vessel.get("available_to_port_code") or "",
            ]
            for vessel in instance_data.vessels
        ],
    )

    assignments = []
    for lane in instance_data.service_lanes:
        lane_code = lane["lane_code"]
        for version in lane["versions"]:
            for assignment in version["vessel_assignments"]:
                assignments.append(
                    [
                        lane_code,
                        version["proforma_name"],
                        assignment["position_no"],
                        assignment["vessel_code"],
                    ]
                )
    _write_tsv(
        bundle_dir / "assignments.tsv",
        ["lane_code", "proforma_name", "position_no", "vessel_code"],
        assignments,
    )

    _write_tsv(
        bundle_dir / "positions.tsv",
        ["lane_code", "proforma_name", "position_no"],
        [
            [position.lane_code, position.proforma_name, position.declared_position_no]
            for position in network_positions
        ],
    )

    _write_tsv(
        bundle_dir / "distances.tsv",
        ["from_port_code", "to_port_code", "distance", "eca_distance"],
        [
            [row["from_port_code"], row["to_port_code"], row["distance"], row["eca_distance"]]
            for row in instance_data.distances
        ],
    )

    _write_tsv(bundle_dir / "eca_ports.tsv", ["port_code"], [[port_code] for port_code in sorted(instance_data.eca_ports)])

    _write_tsv(
        bundle_dir / "bunker_consumption_sea.tsv",
        ["capacity_teu", "speed", "consumption_for_sailing"],
        [
            [row["capacity_teu"], consumption["speed"], consumption["consumption_for_sailing"]]
            for row in instance_data.bunker_consumption_sea
            for consumption in row["consumption"]
        ],
    )
    _write_tsv(
        bundle_dir / "bunker_consumption_port.tsv",
        ["capacity_teu", "consumption_for_pilot"],
        [
            [row["capacity_teu"], row["consumption"]["consumption_for_pilot"]]
            for row in instance_data.bunker_consumption_port
        ],
    )
    _write_tsv(
        bundle_dir / "bunker_price.tsv",
        ["year_month", "lane_code", "bunker_type", "price"],
        [
            [row["year_month"], row["lane_code"], row["bunker_type"], row["price"]]
            for row in instance_data.bunker_price
        ],
    )
    _write_tsv(
        bundle_dir / "transshipment_cost.tsv",
        ["year_month", "lane_code", "port_code", "ts_cost"],
        [
            [row["year_month"], row["lane_code"], port["port_code"], port["ts_cost"]]
            for row in instance_data.transshipment_cost
            for port in row["ports"]
        ],
    )
    _write_tsv(
        bundle_dir / "canal_fee.tsv",
        ["vessel_code", "direction", "port_code", "fee"],
        [[row["vessel_code"], row["direction"], row["port_code"], row["fee"]] for row in instance_data.canal_fee],
    )
    _write_tsv(
        bundle_dir / "canal_passage_time.tsv",
        ["port_code", "direction", "passage_hours"],
        [[row["port_code"], row["direction"], row["passage_hours"]] for row in instance_data.canal_passage_time],
    )
    _write_tsv(
        bundle_dir / "canal_direction.tsv",
        ["from_port_code", "canal_port_code", "to_port_code", "direction"],
        [
            [row["from_port_code"], row["canal_port_code"], row["to_port_code"], row["direction"]]
            for row in instance_data.canal_direction
        ],
    )
    _write_tsv(
        bundle_dir / "opportunity_cost.tsv",
        ["lane_code", "proforma_name", "direction", "opportunity_cost"],
        [
            [row["lane_code"], row["proforma_name"], row["direction"], row["opportunity_cost"]]
            for row in instance_data.opportunity_cost
        ],
    )
    _write_tsv(
        bundle_dir / "dd_couplings.tsv",
        ["coupling_index", "original_vessel_code", "before_vessel_code", "after_vessel_code"],
        [
            [
                index,
                row["original_vessel_code"],
                row["before_vessel_code"],
                row["after_vessel_code"],
            ]
            for index, row in enumerate(dd_couplings or [])
        ],
    )


def _portstay_from_group_row(row: dict[str, str]) -> PortStay:
    return PortStay(
        lane_code=row["lane_code"],
        proforma_name=row["proforma_name"],
        position_no=int(row["position_no"]),
        port_code=row["port_code"],
        port_seq=int(row["port_seq"]),
        pilot_in_start=_dt(row["pilot_in_start"]),
        berthing_start=_dt(row["berthing_start"]),
        berthing_end=_dt(row["berthing_end"]),
        pilot_out_end=_dt(row["pilot_out_end"]),
    )


def _make_group(row: dict[str, str]) -> NodeGroup:
    group_class = {"PS": PS, "SS": SS, "SE": SE}[row["kind"]]
    group = group_class.__new__(group_class)
    group._id = _group_id(row["group_id"])
    group.event = _portstay_from_group_row(row)
    group.is_canal = _bool(row["is_canal"])
    group.direction = row["direction"] or None
    group._ts_cost = None
    if isinstance(group, PS):
        group.is_last = _bool(row["is_last"])
        group.is_first = _bool(row["is_first"])
    if isinstance(group, SS):
        group.service_start = group.event.pilot_in_start
    if isinstance(group, SE):
        group.service_end = group.event.pilot_in_start
        group.is_first = _bool(row["is_first"])
    return group


def _make_node(row: dict[str, str], event: Any) -> Node:
    label = row["label"]
    if row["event_kind"] == "Delivery":
        node = D(row["vessel_code"], Delivery(delivery_time=_dt(row["event_start"]), delivery_port_code=row["start_port"]))
    elif row["event_kind"] == "Redelivery":
        node = R(
            row["vessel_code"],
            Redelivery(redelivery_time=_dt(row["event_start"]), redelivery_port_code=row["start_port"]),
        )
    elif label == "idle":
        node = I(Idle(port_code=row["start_port"], idle_start=_dt(row["event_start"]), idle_end=_dt(row["event_end"])))
    elif label == "target":
        node = T(Idle(port_code=row["start_port"], idle_start=_dt(row["event_start"]), idle_end=_dt(row["event_end"])))
    elif _bool(row["is_horizon"]):
        sail_hours = (int(row["event_end"]) - int(row["event_start"])) / 3600
        distance = float(row["distance"])
        node = HorizonSailNode(
            InLaneSail(
                lane_code=row["lane_code"],
                proforma_name=row["proforma_name"],
                position_no=int(row["position_no"]),
                from_port_code=row["start_port"],
                from_port_seq=int(row["start_port_seq"]),
                sea_sail_start=_dt(row["event_start"]),
                to_port_code=row["end_port"],
                to_port_seq=int(row["end_port_seq"]),
                sea_sail_end=_dt(row["event_end"]),
                distance=distance,
                avg_speed=distance / sail_hours if sail_hours else None,
            ),
            row["horizon_side"],
        )
    else:
        node = Node(event=event, label=label, node_in_time=_dt(row["node_in"]), node_out_time=_dt(row["node_out"]))
    node._id = _node_id(row["node_id"])
    node.node_in_time = _dt(row["node_in"])
    node.node_out_time = _dt(row["node_out"])
    return node


def _construct_network_from_cpp_bundle(bundle_dir: Path) -> nx.MultiDiGraph:
    item_rows = _read_tsv(bundle_dir / "cpp_items.tsv")
    group_rows = _read_tsv(bundle_dir / "cpp_groups.tsv")
    node_rows = _read_tsv(bundle_dir / "cpp_nodes.tsv")
    arc_rows = _read_tsv(bundle_dir / "cpp_arcs.tsv")

    group_by_index = {int(row["group_id"]): _make_group(row) for row in group_rows}
    item_by_index = {int(row["item_index"]): row for row in item_rows}
    node_by_index: dict[int, Node] = {}
    for row in node_rows:
        owner_item = int(row["owner_item"])
        event = None
        if owner_item >= 0 and item_by_index[owner_item]["kind"] == "Group":
            event = group_by_index[int(item_by_index[owner_item]["group_index"])].event
        node_by_index[int(row["node_id"])] = _make_node(row, event)

    for row in group_rows:
        group = group_by_index[int(row["group_id"])]
        if isinstance(group, SS):
            attrs = ("ss", "pilot_in", "pilot_out", "ts_in_after", "ts_out_after")
        elif isinstance(group, SE):
            attrs = ("se", "pilot_in", "pilot_out", "ts_in_before", "ts_out_before")
        else:
            attrs = ("pilot_in", "pilot_out", "ts_in_before", "ts_out_before", "ts_in_after", "ts_out_after")
        for attr in attrs:
            node_index = int(row[attr])
            if node_index >= 0:
                setattr(group, attr, node_by_index[node_index])
        if getattr(group, "is_first", False) and hasattr(group, "pilot_in"):
            group.pilot_in.is_first = True

    network_items: list[Node | NodeGroup] = []
    for row in item_rows:
        if row["kind"] == "Group":
            network_items.append(group_by_index[int(row["group_index"])])
        else:
            network_items.append(node_by_index[int(row["node_id"])])

    arcs = []
    for row in arc_rows:
        from_node = node_by_index[int(row["from_node"])]
        to_node = node_by_index[int(row["to_node"])]
        if row["type"] == "SailArc":
            arc = SailArc(from_node, to_node, float(row["distance"]), float(row["sail_time"]))
        elif row["type"] == "CanalSailArc":
            arc = CanalSailArc(
                from_node,
                to_node,
                float(row["distance"]),
                float(row["sail_time"]),
                row["canal_port_code"],
                row["canal_direction"],
                float(row["canal_leg1_distance"]),
                float(row["canal_leg1_eca_distance"]),
                float(row["canal_leg2_distance"]),
                float(row["canal_leg2_eca_distance"]),
                float(row["canal_passage_hours"]),
            )
        elif row["type"] == "HorizonSailArc":
            arc = HorizonSailArc(from_node, to_node, float(row["distance"]), float(row["sail_time"]))
        else:
            cost = float(row["cost"]) if _bool(row["has_cost"]) else None
            arc = Arc(from_node, to_node, cost=cost)
        arc._id = f"A{int(row['arc_index'])}"
        arcs.append(arc)

    graph = _to_networkx(network_items, arcs)
    graph.graph["related_lane_keys"] = {
        (row["lane_code"], row["proforma_name"], int(row["position_no"]))
        for row in _read_tsv(bundle_dir / "cpp_related_lane_keys.tsv")
    }
    graph.graph["current_assignment_source_node_id_by_vessel"] = {
        row["vessel_code"]: _node_id(row["node_id"])
        for row in _read_tsv(bundle_dir / "cpp_current_assignment_sources.tsv")
    }
    return graph


def _read_key_value_tsv(path: Path) -> dict[str, str]:
    return {row["key"]: row["value"] for row in _read_tsv(path)}


def _construct_flow_result_from_cpp_bundle(bundle_dir: Path, network: nx.MultiDiGraph) -> dict[str, Any]:
    meta = _read_key_value_tsv(bundle_dir / "flow_meta.tsv")
    declared_positions = [
        DeclaredPosition(
            lane_code=row["lane_code"],
            proforma_name=row["proforma_name"],
            declared_position_no=int(row["position_no"]),
        )
        for row in _read_tsv(bundle_dir / "flow_declared_positions.tsv")
    ]

    nodes_by_path: dict[int, list[tuple[int, str]]] = {}
    for row in _read_tsv(bundle_dir / "flow_path_nodes.tsv"):
        nodes_by_path.setdefault(int(row["path_index"]), []).append((int(row["order"]), row["node_id"]))

    edges_by_path: dict[int, list[tuple[int, dict[str, Any]]]] = {}
    for row in _read_tsv(bundle_dir / "flow_path_edges.tsv"):
        edge_payload: dict[str, Any] = {
            "edge_id": row["edge_id"],
            "from_node_id": row["from_node_id"],
            "to_node_id": row["to_node_id"],
            "arc_id": row["arc_id"] or None,
            "arc_type": row["arc_type"],
            "profit": float(row["profit"]),
        }
        if row.get("canal_port_code"):
            edge_payload.update(
                {
                    "canal_port_code": row["canal_port_code"],
                    "canal_direction": row["canal_direction"],
                    "canal_leg1_distance": float(row["canal_leg1_distance"]),
                    "canal_leg1_eca_distance": float(row["canal_leg1_eca_distance"]),
                    "canal_leg2_distance": float(row["canal_leg2_distance"]),
                    "canal_leg2_eca_distance": float(row["canal_leg2_eca_distance"]),
                    "canal_passage_hours": float(row["canal_passage_hours"]),
                    "canal_leg1_speed": float(row["canal_leg1_speed"]),
                    "canal_leg2_speed": float(row["canal_leg2_speed"]),
                    "canal_leg1_hours": float(row["canal_leg1_hours"]),
                    "canal_leg2_hours": float(row["canal_leg2_hours"]),
                }
            )
        edges_by_path.setdefault(int(row["path_index"]), []).append(
            (
                int(row["order"]),
                edge_payload,
            )
        )

    paths = []
    for row in _read_tsv(bundle_dir / "flow_paths.tsv"):
        path_index = int(row["path_index"])
        paths.append(
            {
                "vessel_code": row["vessel_code"],
                "source_node_id": row["source_node_id"],
                "sink_node_id": row["sink_node_id"],
                "node_path": [node_id for _, node_id in sorted(nodes_by_path.get(path_index, []))],
                "edge_path": [edge for _, edge in sorted(edges_by_path.get(path_index, []))],
                "total_profit": float(row["total_profit"]),
                **({"is_virtual": True} if _bool(row["is_virtual"]) else {}),
            }
        )

    return {
        "objective_value": float(meta["objective_value"]),
        "objective_bound": float(meta.get("objective_bound", meta["objective_value"])),
        "mip_gap": float(meta.get("mip_gap", 0.0)),
        "status": int(meta["status"]),
        "status_name": meta.get("status_name", ""),
        "paths": paths,
        "network": network,
        "declared_positions": declared_positions,
    }


def construct_network_cpp(
    instance_data: InstanceData,
    network_positions: list[DeclaredPosition],
) -> nx.MultiDiGraph:
    _ensure_cpp_library()
    with tempfile.TemporaryDirectory(prefix="ocam_v6_lb_") as temp_dir:
        bundle_dir = Path(temp_dir)
        _export_network_input_bundle(instance_data, network_positions, bundle_dir)
        lib = ctypes.CDLL(str(LIB_PATH))
        lib.ocam_v6_lb_construct_network.argtypes = [
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_size_t,
            ctypes.c_char_p,
            ctypes.c_size_t,
        ]
        lib.ocam_v6_lb_construct_network.restype = ctypes.c_int
        out = ctypes.create_string_buffer(4096)
        err = ctypes.create_string_buffer(4096)
        code = lib.ocam_v6_lb_construct_network(str(bundle_dir).encode(), out, len(out), err, len(err))
        if code != 0:
            raise RuntimeError(err.value.decode())
        return _construct_network_from_cpp_bundle(bundle_dir)


def solve_flow_cpp(
    instance_data: InstanceData,
    network_positions: list[DeclaredPosition],
    output_path: str = "multicommodity_mip_paths.html",
    model_name: str = "mcf_v6_lb",
    dd_couplings: list[dict[str, object]] | None = None,
) -> dict[str, Any]:
    _ensure_cpp_library()
    with tempfile.TemporaryDirectory(prefix="ocam_v6_lb_solve_") as temp_dir:
        bundle_dir = Path(temp_dir)
        _export_network_input_bundle(instance_data, network_positions, bundle_dir, dd_couplings, model_name)
        lib = ctypes.CDLL(str(LIB_PATH))
        lib.ocam_v6_lb_solve_flow.argtypes = [
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_size_t,
            ctypes.c_char_p,
            ctypes.c_size_t,
        ]
        lib.ocam_v6_lb_solve_flow.restype = ctypes.c_int
        out = ctypes.create_string_buffer(4096)
        err = ctypes.create_string_buffer(4096)
        code = lib.ocam_v6_lb_solve_flow(str(bundle_dir).encode(), out, len(out), err, len(err))
        if code != 0:
            raise RuntimeError(err.value.decode())
        network = _construct_network_from_cpp_bundle(bundle_dir)
        return _construct_flow_result_from_cpp_bundle(bundle_dir, network)

from __future__ import annotations

import networkx as nx

from .arcs import *
from .nodes import *


def lane_info(event: InLaneEvent) -> tuple[str, str, int]:
    return (event.lane_code, event.proforma_name, event.position_no)


def _viz_impl(
    network_items: list[Node | NodeGroup],
    arcs: list[Arc],
    primary_lane_key: tuple[str, str, int] | None,
    secondary_lane_keys: (
        set[tuple[str, str, int]] | list[tuple[str, str, int]] | tuple[tuple[str, str, int], ...] | None
    ),
    output_path: str = "network_viz.html",
    interactive: bool = False,
    show_incoming_external_arcs: bool = True,
    show_outgoing_external_arcs: bool = True,
    show_idle_arcs: bool = True,
    only_zero_capacity_arcs: bool = False,
    use_lane_filtering: bool = True,
    arc_color_by_node_pair: dict[tuple[str, str], str] | None = None,
    arc_group_by_node_pair: dict[tuple[str, str], str] | None = None,
    arc_tooltip_suffix_by_node_pair: dict[tuple[str, str], str] | None = None,
    arc_flow_value_by_node_pair: dict[tuple[str, str], float] | None = None,
    force_foreground_arcs: bool = False,
    interaction_mode: str = "default",
    ts_state_by_unit_id: dict[str, dict[str, bool]] | None = None,
    node_label_by_id: dict[str, str] | None = None,
):
    import html
    import json
    from pathlib import Path
    import subprocess
    import sys
    import webbrowser

    def lane_item_key(item: Node | NodeGroup | None) -> tuple[str, str, int] | None:
        if isinstance(item, NodeGroup):
            return lane_info(item.event)
        if isinstance(item, HorizonSailNode):
            return lane_info(item.event)
        return None

    def is_idle_node(item: Node | NodeGroup | None) -> bool:
        return isinstance(item, I)

    def node_mid_time(node: Node) -> datetime:
        return node.node_in_time + (node.node_out_time - node.node_in_time) / 2

    def item_time(item: Node | NodeGroup) -> datetime:
        if isinstance(item, NodeGroup):
            return node_mid_time(item.pilot_in)
        if isinstance(item, HorizonSailNode):
            return node_mid_time(item)
        return node_mid_time(item)

    def node_label(node: Node) -> str:
        label_map = {
            "ts_in_before": "TSIB",
            "ts_in_after": "TSIA",
            "ts_out_before": "TSOB",
            "ts_out_after": "TSOA",
            "pilot_in": "PI",
            "pilot_out": "PO",
            "horizon_sail": "HS",
        }
        return label_map.get(node.label, node.label or node.get_id()).upper()

    def lane_key_label(lane_key: tuple[str, str, int]) -> str:
        return f"{lane_key[0]}/{lane_key[1]}/{lane_key[2]}"

    def dt_ms(value: datetime) -> int:
        return int(value.timestamp() * 1000)

    available_lane_keys = sorted({lane_key for item in network_items if (lane_key := lane_item_key(item)) is not None})
    secondary_lane_keys = set(secondary_lane_keys or [])
    selected_lane_keys = (
        set(available_lane_keys) if not use_lane_filtering else {primary_lane_key, *secondary_lane_keys}
    )
    if use_lane_filtering and primary_lane_key not in available_lane_keys:
        raise ValueError(
            "viz: lane_key is not present in network_items. "
            f"lane_key={primary_lane_key!r}, available={available_lane_keys!r}."
        )
    missing_secondary_lane_keys = sorted(secondary_lane_keys - set(available_lane_keys))
    if use_lane_filtering and missing_secondary_lane_keys:
        raise ValueError(
            "viz: some secondary lane keys are not present in network_items. "
            f"missing={missing_secondary_lane_keys!r}, available={available_lane_keys!r}."
        )

    node_owner: dict[str, Node | NodeGroup] = {}
    node_positions: dict[str, tuple[int, int]] = {}
    lane_keys = available_lane_keys
    margin_left = 280
    margin_top = 88
    row_gap = 92
    group_height = 62
    row_y_offsets = {
        "top": -20,
        "middle": 0,
        "bottom": 20,
    }
    lane_y = {lane_key: margin_top + index * row_gap for index, lane_key in enumerate(lane_keys)}
    vessel_row_y = margin_top + len(lane_keys) * row_gap + 105
    vessel_items = [item for item in network_items if isinstance(item, Node) and not isinstance(item, HorizonSailNode)]

    all_times: list[datetime] = []
    for item in network_items:
        if isinstance(item, NodeGroup):
            for node in item.get_nodes():
                all_times.extend([node.node_in_time, node.node_out_time])
        elif isinstance(item, HorizonSailNode):
            item_lane_key = lane_info(item.event)
            if not use_lane_filtering:
                lane_role = "other"
            else:
                lane_role = (
                    "primary"
                    if item_lane_key == primary_lane_key
                    else "secondary" if item_lane_key in secondary_lane_keys else "other"
                )
            x = scale_time(node_mid_time(item))
            y = lane_y[item_lane_key]
            node_owner[item.get_id()] = item
            node_positions[item.get_id()] = (x, y)
            if item_lane_key in selected_lane_keys:
                node_payloads.append(
                    {
                        "id": item.get_id(),
                        "x": x,
                        "y": y,
                        "label": (node_label_by_id or {}).get(item.get_id(), f"{item.get_id()} HS"),
                        "kind": "horizon",
                        "role": lane_role,
                        "tooltip": (
                            f"{item.get_id()} horizon_sail\\n"
                            f"{lane_key_label(item_lane_key)}\\n"
                            f"{item.from_port_code}#{item.from_port_seq} -> "
                            f"{item.to_port_code}#{item.to_port_seq}\\n"
                            f"{item.sea_sail_start} -> {item.sea_sail_end}\\n"
                            f"side={item.horizon_side}"
                        ),
                    }
                )
        else:
            all_times.extend([item.node_in_time, item.node_out_time])
    if not all_times:
        raise ValueError("viz: network_items must include at least one drawable node.")

    min_time = min(all_times)
    max_time = max(all_times)
    time_span_days = max((max_time - min_time).total_seconds() / 86400, 1.0)
    plot_width = int(max(1600, min(9000, time_span_days * 130)))
    svg_width = margin_left + plot_width + 220
    group_x_padding = 18

    def scale_time(value: datetime) -> int:
        elapsed = (value - min_time).total_seconds()
        span = max((max_time - min_time).total_seconds(), 1.0)
        return int(margin_left + elapsed / span * plot_width)

    vessel_row_gap = 38
    vessel_node_padding = 72
    vessel_row_right_edges: list[int] = []
    vessel_y: dict[str, int] = {}
    for item in sorted(vessel_items, key=lambda node: (node.node_in_time, node.node_out_time, node.get_id())):
        start_x = scale_time(item.node_in_time)
        end_x = scale_time(item.node_out_time)
        left_x = min(start_x, end_x) - vessel_node_padding
        right_x = max(start_x, end_x) + vessel_node_padding
        row_index = None
        for candidate_index, row_right_x in enumerate(vessel_row_right_edges):
            if left_x > row_right_x:
                row_index = candidate_index
                break
        if row_index is None:
            row_index = len(vessel_row_right_edges)
            vessel_row_right_edges.append(right_x)
        else:
            vessel_row_right_edges[row_index] = right_x
        vessel_y[item.get_id()] = vessel_row_y + row_index * vessel_row_gap
    vessel_row_count = max(1, len(vessel_row_right_edges)) if vessel_items else 0
    svg_height = vessel_row_y + max(1, vessel_row_count) * vessel_row_gap + 120

    def group_node_slot(group: NodeGroup, node: Node) -> tuple[str, str] | None:
        if isinstance(group, SS):
            return {
                "ss": ("left", "top"),
                "pilot_in": ("left", "middle"),
                "ts_in_after": ("right", "top"),
                "pilot_out": ("right", "middle"),
                "ts_out_after": ("right", "bottom"),
            }.get(node.label)
        if isinstance(group, SE):
            return {
                "ts_in_before": ("left", "top"),
                "pilot_in": ("left", "middle"),
                "ts_out_before": ("left", "bottom"),
                "pilot_out": ("right", "middle"),
                "se": ("right", "bottom"),
            }.get(node.label)
        return {
            "ts_in_before": ("left", "top"),
            "pilot_in": ("left", "middle"),
            "ts_out_before": ("left", "bottom"),
            "ts_in_after": ("right", "top"),
            "pilot_out": ("right", "middle"),
            "ts_out_after": ("right", "bottom"),
        }.get(node.label)

    group_payloads = []
    node_payloads = []
    for item in network_items:
        if isinstance(item, NodeGroup):
            item_lane_key = lane_info(item.event)
            y_base = lane_y[item_lane_key]
            nodes = item.get_nodes()
            left_col_x = scale_time(event_start_time(item.event))
            right_col_x = scale_time(event_end_time(item.event))
            if right_col_x <= left_col_x + 50:
                right_col_x = left_col_x + 50
            left = left_col_x - group_x_padding
            right = right_col_x + group_x_padding
            inner_left_col_x = int(left + (right - left) / 3)
            inner_right_col_x = int(left + 2 * (right - left) / 3)
            if not use_lane_filtering:
                lane_role = "other"
            else:
                lane_role = (
                    "primary"
                    if item_lane_key == primary_lane_key
                    else "secondary" if item_lane_key in secondary_lane_keys else "other"
                )
            service_arc_pair = (item.pilot_in.get_id(), item.pilot_out.get_id())
            service_covered = (
                interaction_mode != "mip"
                or (arc_flow_value_by_node_pair or {}).get(service_arc_pair, 0.0) >= 1.0 - 1e-9
            )
            group_payloads.append(
                {
                    "id": item.get_id(),
                    "x": left,
                    "y": y_base - group_height / 2,
                    "width": max(right - left, 82),
                    "height": group_height,
                    "label": f"{item.get_id()} {type(item).__name__} {item.event.port_code}",
                    "laneKey": lane_key_label(item_lane_key),
                    "role": lane_role,
                    "serviceCovered": service_covered,
                    "tsStatus": {
                        "before": {
                            "unitId": f"{item.get_id()}:before",
                            "y": bool((ts_state_by_unit_id or {}).get(f"{item.get_id()}:before", {}).get("y", False)),
                            "s": bool((ts_state_by_unit_id or {}).get(f"{item.get_id()}:before", {}).get("s", False)),
                        },
                        "after": {
                            "unitId": f"{item.get_id()}:after",
                            "y": bool((ts_state_by_unit_id or {}).get(f"{item.get_id()}:after", {}).get("y", False)),
                            "s": bool((ts_state_by_unit_id or {}).get(f"{item.get_id()}:after", {}).get("s", False)),
                        },
                    },
                }
            )
            for node in nodes:
                slot = group_node_slot(item, node)
                if slot is None:
                    x = scale_time(node_mid_time(node))
                    y = y_base
                else:
                    column, row = slot
                    x = inner_left_col_x if column == "left" else inner_right_col_x
                    y = y_base + row_y_offsets[row]
                node_owner[node.get_id()] = item
                node_positions[node.get_id()] = (x, y)
                if item_lane_key in selected_lane_keys:
                    node_payloads.append(
                        {
                            "id": node.get_id(),
                            "x": x,
                            "y": y,
                            "label": (node_label_by_id or {}).get(node.get_id(), node_label(node)),
                            "kind": "group",
                            "role": lane_role,
                            "tooltip": (
                                f"{node.get_id()} {node.label}\\n"
                                f"{type(item).__name__} {lane_key_label(item_lane_key)}\\n"
                                f"{item.event.port_code}"
                            ),
                        }
                    )
        else:
            x = scale_time(node_mid_time(item))
            y = vessel_y[item.get_id()]
            node_owner[item.get_id()] = item
            node_positions[item.get_id()] = (x, y)
            vessel_code = getattr(item, "vessel_code", None)
            node_kind = "vessel"
            if isinstance(item, I):
                node_kind = "idle"
            elif isinstance(item, T):
                node_kind = "target"
            if node_kind == "idle":
                x -= 18
                node_positions[item.get_id()] = (x, y)
            elif node_kind == "target":
                x += 72
                node_positions[item.get_id()] = (x, y)
            vessel_label = (
                f"{item.get_id()} {node_label(item)} {vessel_code}"
                if vessel_code is not None
                else f"{item.get_id()} {node_label(item)}"
            )
            vessel_tooltip = (
                f"{item.get_id()} {node_label(item)}\\n{type(item.event).__name__}\\nvessel_code={vessel_code}"
                if vessel_code is not None
                else f"{item.get_id()} {node_label(item)}\\n{type(item.event).__name__}"
            )
            node_payloads.append(
                {
                    "id": item.get_id(),
                    "x": x,
                    "y": y,
                    "label": (node_label_by_id or {}).get(item.get_id(), vessel_label),
                    "kind": node_kind,
                    "role": node_kind,
                    "tooltip": vessel_tooltip,
                }
            )

    consecutive_node_group_pairs: set[tuple[str, str]] = set()
    node_groups_by_lane: dict[tuple[str, str, int], list[NodeGroup]] = {}
    for item in network_items:
        if isinstance(item, NodeGroup):
            node_groups_by_lane.setdefault(lane_info(item.event), []).append(item)
    for groups in node_groups_by_lane.values():
        groups.sort(key=lambda group: event_start_time(group.event))
        for left_group, right_group in zip(groups, groups[1:]):
            consecutive_node_group_pairs.add((left_group.get_id(), right_group.get_id()))

    def arc_is_related_to_target_lane(arc: Arc) -> bool:
        if not use_lane_filtering:
            return True
        left_owner = node_owner.get(arc.from_node.get_id())
        right_owner = node_owner.get(arc.to_node.get_id())
        left_lane = lane_item_key(left_owner)
        right_lane = lane_item_key(right_owner)

        if is_idle_node(left_owner) or is_idle_node(right_owner):
            if not show_idle_arcs:
                return False
            if left_lane is None and right_lane is None:
                return True
            return (left_lane in selected_lane_keys) or (right_lane in selected_lane_keys)

        if left_lane == right_lane:
            return left_lane == primary_lane_key
        if left_lane != primary_lane_key and right_lane == primary_lane_key:
            if left_lane is not None and left_lane not in secondary_lane_keys:
                return False
            return show_incoming_external_arcs
        if left_lane == primary_lane_key and right_lane != primary_lane_key:
            if right_lane is not None and right_lane not in secondary_lane_keys:
                return False
            return show_outgoing_external_arcs
        return False

    def is_background_arc(arc: Arc) -> bool:
        left_owner = node_owner.get(arc.from_node.get_id())
        right_owner = node_owner.get(arc.to_node.get_id())
        if left_lane is None or right_lane is None:
            return True
        return left_lane != right_lane

    def is_same_lane_ps_to_ps_arc(arc: Arc) -> bool:
        left_owner = node_owner.get(arc.from_node.get_id())
        right_owner = node_owner.get(arc.to_node.get_id())
        return (
            isinstance(left_owner, PS)
            and isinstance(right_owner, PS)
            and lane_info(left_owner.event) == lane_info(right_owner.event)
        )

    def is_straight_same_lane_ps_to_ps_arc(arc: Arc) -> bool:
        left_owner = node_owner.get(arc.from_node.get_id())
        right_owner = node_owner.get(arc.to_node.get_id())
        return (
            is_same_lane_ps_to_ps_arc(arc)
            and isinstance(left_owner, PS)
            and isinstance(right_owner, PS)
            and (left_owner.get_id(), right_owner.get_id()) in consecutive_node_group_pairs
            and arc.from_node.label == "pilot_out"
            and arc.to_node.label in {"pilot_in", "ts_out_before"}
        )

    def is_same_lane_ts_in_after_arc(arc: Arc) -> bool:
        left_owner = node_owner.get(arc.from_node.get_id())
        right_owner = node_owner.get(arc.to_node.get_id())
        return (
            isinstance(left_owner, NodeGroup)
            and isinstance(right_owner, NodeGroup)
            and left_owner is not right_owner
            and lane_info(left_owner.event) == lane_info(right_owner.event)
            and arc.from_node.label == "ts_in_after"
            and arc.to_node.label in {"pilot_in", "ts_out_before"}
        )

    def is_internal_arc(arc: Arc) -> bool:
        left_owner = node_owner.get(arc.from_node.get_id())
        right_owner = node_owner.get(arc.to_node.get_id())
        return isinstance(left_owner, NodeGroup) and left_owner is right_owner

    def is_curved_internal_arc(arc: Arc) -> bool:
        return is_internal_arc(arc) and (
            (arc.from_node.label, arc.to_node.label)
            in {
                ("ts_in_before", "pilot_in"),
            }
        )

    def is_straight_internal_arc(arc: Arc) -> bool:
        return is_internal_arc(arc) and (
            (arc.from_node.label, arc.to_node.label)
            in {
                ("pilot_in", "pilot_out"),
                ("pilot_out", "ts_out_after"),
            }
        )

    def arc_path(arc: Arc, start: tuple[int, int], end: tuple[int, int]) -> str:
        if is_straight_internal_arc(arc):
            return f"M {start[0]} {start[1]} L {end[0]} {end[1]}"
        if is_curved_internal_arc(arc):
            control_x = int((start[0] + end[0]) / 2)
            control_y = int((start[1] + end[1]) / 2 - 24)
            return f"M {start[0]} {start[1]} Q {control_x} {control_y} {end[0]} {end[1]}"
        if is_straight_same_lane_ps_to_ps_arc(arc):
            return f"M {start[0]} {start[1]} L {end[0]} {end[1]}"
        if is_same_lane_ts_in_after_arc(arc):
            control_x = int((start[0] + end[0]) / 2)
            control_y = min(start[1], end[1]) - max(56, abs(end[0] - start[0]) * 0.12)
            return f"M {start[0]} {start[1]} Q {control_x} {control_y} {end[0]} {end[1]}"
        if is_same_lane_ps_to_ps_arc(arc):
            control_x = int((start[0] + end[0]) / 2)
            control_y = int((start[1] + end[1]) / 2 - max(42, abs(end[0] - start[0]) * 0.10))
            return f"M {start[0]} {start[1]} Q {control_x} {control_y} {end[0]} {end[1]}"
        dx = abs(end[0] - start[0])
        dy = abs(end[1] - start[1])
        if dx < 0.02:
            return f"M {start[0]} {start[1]} L {end[0]} {end[1]}"
        if dx >= 95 or dy >= 115:
            return f"M {start[0]} {start[1]} L {end[0]} {end[1]}"
        control_x = int((start[0] + end[0]) / 2)
        control_y = int((start[1] + end[1]) / 2 + (-28 if start[1] <= end[1] else 28))
        return f"M {start[0]} {start[1]} Q {control_x} {control_y} {end[0]} {end[1]}"

    arc_payloads = []
    visible_arcs = [arc for arc in arcs if arc_is_related_to_target_lane(arc)]
    for arc in visible_arcs:
        if only_zero_capacity_arcs and arc.capacity != 0:
            continue
        start = node_positions.get(arc.from_node.get_id())
        end = node_positions.get(arc.to_node.get_id())
        if start is None or end is None:
            continue
        background = is_background_arc(arc)
        if force_foreground_arcs:
            background = False
        from_owner = node_owner.get(arc.from_node.get_id())
        to_owner = node_owner.get(arc.to_node.get_id())
        from_owner_id = from_owner.get_id() if hasattr(from_owner, "get_id") else "?"
        to_owner_id = to_owner.get_id() if hasattr(to_owner, "get_id") else "?"
        node_pair = (arc.from_node.get_id(), arc.to_node.get_id())
        flow_value = (arc_flow_value_by_node_pair or {}).get(node_pair)
        flow_class = ""
        if interaction_mode == "mip" and flow_value is not None:
            flow_class = " flow-full" if flow_value >= 1.0 - 1e-9 else " flow-fractional"
        arc_payloads.append(
            {
                "from": arc.from_node.get_id(),
                "to": arc.to_node.get_id(),
                "mipGroup": (
                    arc_group_by_node_pair[node_pair]
                    if arc_group_by_node_pair is not None and node_pair in arc_group_by_node_pair
                    else None
                ),
                "path": arc_path(arc, start, end),
                "background": background,
                "className": ("arc background" if background else "arc foreground") + flow_class,
                "layer": "background" if background else "inlane",
                "flowValue": flow_value,
                "isFractionalFlow": bool(flow_value is not None and 1e-9 < flow_value < 1.0 - 1e-9),
                "tooltip": (
                    f"{arc.from_node.get_id()} ({from_owner_id}:{arc.from_node.label}) -> "
                    f"{arc.to_node.get_id()} ({to_owner_id}:{arc.to_node.label})\\n"
                    f"cost={arc.cost}"
                )
                + (
                    f"\\n{arc_tooltip_suffix_by_node_pair[node_pair]}"
                    if arc_tooltip_suffix_by_node_pair is not None and node_pair in arc_tooltip_suffix_by_node_pair
                    else ""
                ),
                "stroke": (
                    arc_color_by_node_pair[node_pair]
                    if arc_color_by_node_pair is not None and node_pair in arc_color_by_node_pair
                    else None
                ),
            }
        )

    visual_degree_by_node_id = {node["id"]: {"in": 0, "out": 0} for node in node_payloads}
    for arc_payload in arc_payloads:
        from_node_id = arc_payload["from"]
        to_node_id = arc_payload["to"]
        if from_node_id in visual_degree_by_node_id:
            visual_degree_by_node_id[from_node_id]["out"] += 1
        if to_node_id in visual_degree_by_node_id:
            visual_degree_by_node_id[to_node_id]["in"] += 1
    for node_payload in node_payloads:
        visual_degree = visual_degree_by_node_id[node_payload["id"]]
        in_degree = visual_degree["in"]
        out_degree = visual_degree["out"]
        node_payload["tooltip"] += f"\nvisual degree={in_degree + out_degree} (in={in_degree}, out={out_degree})"

    lane_labels = [
        {
            "x": 14,
            "y": lane_y[lane_key] + 4,
            "label": lane_key_label(lane_key),
            "role": (
                "other"
                if not use_lane_filtering
                else (
                    "primary"
                    if lane_key == primary_lane_key
                    else "secondary" if lane_key in secondary_lane_keys else "other"
                )
            ),
        }
        for lane_key in lane_keys
    ]
    if vessel_items:
        lane_labels.append({"x": 14, "y": vessel_row_y + 4, "label": "vessel nodes", "role": "vessel"})

    tick_step_days = max(1, int(time_span_days // 14) or 1)
    ticks = []
    tick_time = min_time
    while tick_time <= max_time:
        x = scale_time(tick_time)
        ticks.append({"x": x, "label": tick_time.strftime("%m-%d")})
        tick_time = tick_time + timedelta(days=tick_step_days)

    data = {
        "width": svg_width,
        "height": svg_height,
        "groups": group_payloads,
        "nodes": node_payloads,
        "arcs": arc_payloads,
        "laneLabels": lane_labels,
        "ticks": ticks,
        "primaryLane": "all lanes" if primary_lane_key is None else lane_key_label(primary_lane_key),
        "secondaryLanes": [lane_key_label(lane_key) for lane_key in sorted(secondary_lane_keys)],
        "counts": {
            "groups": len(group_payloads),
            "nodes": len(node_payloads),
            "arcs": len(arc_payloads),
        },
    }

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data)
    document = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>multicommodity network</title>
<style>
  body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f8fafc; color: #0f172a; }}
  header {{ position: sticky; top: 0; z-index: 10; display: flex; gap: 16px; align-items: center; padding: 10px 14px; border-bottom: 1px solid #dbe3ef; background: rgba(248, 250, 252, 0.96); }}
  header strong {{ font-size: 14px; }}
  header span {{ font-size: 12px; color: #475569; }}
  button {{ border: 1px solid #cbd5e1; background: white; border-radius: 6px; padding: 5px 9px; cursor: pointer; }}
  #stage {{ width: 100vw; height: calc(100vh - 47px); overflow: hidden; background: #f8fafc; cursor: grab; }}
  #stage.dragging {{ cursor: grabbing; }}
  svg {{ width: 100%; height: 100%; display: block; }}
  .grid-major {{ stroke: #dbe3ef; stroke-width: 1; }}
  .grid-minor {{ stroke: #eef2f7; stroke-width: 1; }}
  .lane-label {{ font-size: 12px; fill: #334155; }}
  .lane-label.primary {{ fill: #1d4ed8; font-weight: 700; }}
  .lane-label.secondary {{ fill: #475569; font-weight: 600; }}
  .box {{ fill: white; stroke: #334155; stroke-width: 1; rx: 4; }}
  .box.primary {{ fill: #eff6ff; stroke: #2563eb; stroke-width: 2; }}
  .box.secondary {{ fill: #f8fafc; stroke: #64748b; stroke-width: 1.4; }}
  .box.uncovered, .box.primary.uncovered, .box.secondary.uncovered {{ fill: #fff1f2; stroke: #fca5a5; }}
  .ts-rail {{ stroke-width: 1.2; }}
  .ts-rail.before {{ fill: #dbeafe; stroke: #2563eb; }}
  .ts-rail.after {{ fill: #dcfce7; stroke: #16a34a; }}
  .ts-rail.warn {{ fill: url(#ts-warning-hatch); stroke: #ea580c; }}
  .ts-cap-label {{ font-size: 7px; font-weight: 900; fill: #0f172a; text-anchor: middle; dominant-baseline: middle; }}
  .ts-chip-circle {{ stroke-width: 1.2; }}
  .ts-chip-circle.y {{ fill: #0f766e; stroke: #115e59; }}
  .ts-chip-circle.s {{ fill: #fff7ed; stroke: #ea580c; }}
  .ts-chip-letter {{ font-size: 7px; font-weight: 900; text-anchor: middle; dominant-baseline: middle; }}
  .ts-chip-letter.y {{ fill: white; }}
  .ts-chip-letter.s {{ fill: #9a3412; }}
  .group-label {{ font-size: 10px; fill: #334155; }}
  .node {{ stroke: white; stroke-width: 1.5; cursor: pointer; }}
  .node.group {{ fill: #111827; }}
  .node.horizon {{ fill: #0ea5e9; stroke: #075985; stroke-width: 2.2; }}
  .node.vessel {{ fill: #b91c1c; }}
  .node.idle {{ fill: #0f766e; stroke: #99f6e4; stroke-width: 2.8; }}
  .node.target {{ fill: #d97706; stroke: #fde68a; stroke-width: 2.8; }}
  .node-label {{ font-size: 9px; fill: #0f172a; pointer-events: none; }}
  .node-glyph {{ font-size: 8px; font-weight: 800; fill: white; text-anchor: middle; dominant-baseline: middle; pointer-events: none; }}
  .arc {{ fill: none; marker-end: url(#arrow); }}
  .arc.background {{ stroke: #6b7280; stroke-width: 1.2; stroke-dasharray: 6 5; opacity: 0.5; }}
  .arc.foreground {{ stroke: #2563eb; stroke-width: 1.7; opacity: 0.92; }}
  .arc.mip.flow-fractional {{ stroke-dasharray: 7 5; opacity: 0.5; }}
  .arc:hover {{ stroke: #dc2626; opacity: 1; stroke-width: 3; }}
  .arc.mip:hover {{ opacity: 0.92; stroke-width: 1.7; }}
  .arc.mip.flow-fractional:hover {{ opacity: 0.5; stroke-width: 2.4; }}
  .highlight-arc {{ fill: none; stroke: #dc2626; opacity: 1; stroke-width: 3.2; marker-end: url(#arrow); pointer-events: none; }}
  .highlight-arc-outline {{ fill: none; stroke: #0f172a; opacity: 0.72; stroke-width: 7.4; marker-end: url(#arrow); pointer-events: none; }}
  .highlight-arc-emphasis {{ fill: none; opacity: 1; stroke-width: 4.6; marker-end: url(#arrow); pointer-events: none; }}
  .highlight-node {{ fill: #f59e0b; stroke: #111827; stroke-width: 3; pointer-events: none; }}
  .highlight-node.focused {{ fill: #dc2626; stroke-width: 3.5; }}
  .highlight-label {{ font-size: 10px; fill: #111827; font-weight: 700; pointer-events: none; }}
  .tooltip {{ position: fixed; display: none; max-width: 360px; padding: 7px 9px; border: 1px solid #cbd5e1; border-radius: 6px; background: white; box-shadow: 0 10px 25px rgba(15, 23, 42, 0.14); white-space: pre-wrap; font-size: 12px; pointer-events: none; z-index: 20; }}
</style>
</head>
<body>
<header>
  <strong>multicommodity network</strong>
  <span>primary: {html.escape(data["primaryLane"])}</span>
  <span>secondary: {html.escape(", ".join(data["secondaryLanes"]) or "none")}</span>
  <span id="counts"></span>
  <button id="zoom-in">+</button>
  <button id="zoom-out">-</button>
  <button id="reset">Reset view</button>
</header>
<div id="stage">
  <svg id="plot" viewBox="0 0 {svg_width} {svg_height}" xmlns="http://www.w3.org/2000/svg">
    <defs>
      <marker id="arrow" markerWidth="6" markerHeight="5" refX="5.5" refY="2.5" orient="auto" markerUnits="strokeWidth">
        <path d="M 0 0 L 6 2.5 L 0 5 z" fill="#475569"></path>
      </marker>
      <pattern id="ts-warning-hatch" patternUnits="userSpaceOnUse" width="8" height="8" patternTransform="rotate(45)">
        <rect width="8" height="8" fill="#fff7ed"></rect>
        <line x1="0" y1="0" x2="0" y2="8" stroke="#ea580c" stroke-width="3"></line>
      </pattern>
    </defs>
    <g id="viewport"></g>
  </svg>
</div>
<div id="tooltip" class="tooltip"></div>
<script>
const data = {payload};
const interactionMode = {json.dumps(interaction_mode)};
const svg = document.getElementById("plot");
const viewport = document.getElementById("viewport");
const stage = document.getElementById("stage");
const tooltip = document.getElementById("tooltip");
document.getElementById("counts").textContent = `${{data.counts.groups}} groups, ${{data.counts.nodes}} nodes, ${{data.counts.arcs}} arcs`;
const nodeById = new Map(data.nodes.map(node => [node.id, node]));
const arcsByNode = new Map();
const arcsByMIPGroup = new Map();
for (const arc of data.arcs) {{
  if (!arcsByNode.has(arc.from)) arcsByNode.set(arc.from, []);
  if (!arcsByNode.has(arc.to)) arcsByNode.set(arc.to, []);
  arcsByNode.get(arc.from).push(arc);
  arcsByNode.get(arc.to).push(arc);
  if (arc.mipGroup) {{
    if (!arcsByMIPGroup.has(arc.mipGroup)) arcsByMIPGroup.set(arc.mipGroup, []);
    arcsByMIPGroup.get(arc.mipGroup).push(arc);
  }}
}}

let transform = {{ x: 0, y: 0, k: 1 }};
function applyTransform() {{
  viewport.setAttribute("transform", `translate(${{transform.x}} ${{transform.y}}) scale(${{transform.k}})`);
}}
function zoomAt(factor, centerX, centerY) {{
  const point = svg.createSVGPoint();
  point.x = centerX;
  point.y = centerY;
  const cursor = point.matrixTransform(svg.getScreenCTM().inverse());
  const before = {{ x: (cursor.x - transform.x) / transform.k, y: (cursor.y - transform.y) / transform.k }};
  transform.k = Math.max(0.08, Math.min(20, transform.k * factor));
  transform.x = cursor.x - before.x * transform.k;
  transform.y = cursor.y - before.y * transform.k;
  applyTransform();
}}
function el(name, attrs = {{}}, text = null) {{
  const node = document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  if (text !== null) node.textContent = text;
  return node;
}}
function attachTip(node, text) {{
  node.addEventListener("mousemove", event => {{
    tooltip.style.display = "block";
    tooltip.style.left = `${{event.clientX + 12}}px`;
    tooltip.style.top = `${{event.clientY + 12}}px`;
    tooltip.textContent = text;
  }});
  node.addEventListener("mouseleave", () => tooltip.style.display = "none");
}}
function render() {{
  viewport.innerHTML = "";
  const background = el("g");
  const backgroundArcs = el("g", {{ id: "background-arcs-layer" }});
  const boxes = el("g");
  const inlaneArcs = el("g", {{ id: "inlane-arcs-layer" }});
  const nodes = el("g", {{ id: "nodes-layer" }});
  const highlight = el("g", {{ id: "highlight-layer" }});
  viewport.append(background, backgroundArcs, boxes, inlaneArcs, nodes, highlight);

  for (const tick of data.ticks) {{
    background.append(el("line", {{ x1: tick.x, y1: 32, x2: tick.x, y2: data.height - 45, class: "grid-minor" }}));
    background.append(el("text", {{ x: tick.x + 3, y: 24, "font-size": 10, fill: "#64748b" }}, tick.label));
  }}
  for (const lane of data.laneLabels) {{
    background.append(el("line", {{ x1: 0, y1: lane.y - 4, x2: data.width, y2: lane.y - 4, class: "grid-major" }}));
    background.append(el("text", {{ x: lane.x, y: lane.y, class: `lane-label ${{lane.role}}` }}, lane.label));
  }}
  for (const arc of data.arcs) {{
    const attrs = {{
      d: arc.path,
      class: interactionMode === "mip" ? `${{arc.className}} mip` : arc.className,
      "data-from": arc.from,
      "data-to": arc.to
    }};
    if (arc.mipGroup) attrs["data-mip-group"] = arc.mipGroup;
    if (arc.stroke) attrs.style = `stroke: ${{arc.stroke}};`;
    const path = el("path", attrs);
    attachTip(path, arc.tooltip);
    path.addEventListener("mouseenter", () => highlightArc(arc));
    path.addEventListener("mouseleave", clearHighlight);
    if (arc.layer === "inlane") {{
      inlaneArcs.append(path);
    }} else {{
      backgroundArcs.append(path);
    }}
  }}
  for (const group of data.groups) {{
    const boxClass = `box ${{group.role}}${{group.serviceCovered ? "" : " uncovered"}}`;
    const rect = el("rect", {{ x: group.x, y: group.y, width: group.width, height: group.height, class: boxClass }});
    attachTip(rect, `${{group.id}}\\n${{group.laneKey}}\\npilot_in->pilot_out covered=${{group.serviceCovered ? "yes" : "no"}}`);
    boxes.append(rect);
      const railHeight = Math.max(24, group.height - 12);
      const railWidth = 9;
      const railY = group.y + (group.height - railHeight) / 2;
      const beforePanel = group.tsStatus?.before;
      const afterPanel = group.tsStatus?.after;
      for (const panel of [
      {{ key: "before", data: beforePanel, x: group.x - railWidth - 6 }},
      {{ key: "after", data: afterPanel, x: group.x + group.width + 6 }},
    ]) {{
      if (!panel.data || (!panel.data.y && !panel.data.s)) continue;
      boxes.append(el("rect", {{
        x: panel.x,
        y: railY,
        width: railWidth,
        height: railHeight,
        rx: 4,
        class: `ts-rail ${{panel.key}}`
      }}));
      if (panel.data.s) {{
        boxes.append(el("rect", {{
          x: panel.x,
          y: railY,
          width: railWidth,
          height: railHeight,
          rx: 4,
          class: "ts-rail warn"
        }}));
      }}
      boxes.append(el("text", {{
        x: panel.x + railWidth / 2,
        y: railY - 5,
        class: "ts-cap-label"
      }}, panel.key === "before" ? "B" : "A"));
      if (panel.data.y) {{
        boxes.append(el("circle", {{
          cx: panel.x + railWidth / 2,
          cy: railY + 8,
          r: 6,
          class: "ts-chip-circle y"
        }}));
        boxes.append(el("text", {{
          x: panel.x + railWidth / 2,
          y: railY + 8.5,
          class: "ts-chip-letter y"
        }}, "Y"));
      }}
      if (panel.data.s) {{
        boxes.append(el("circle", {{
          cx: panel.x + railWidth / 2,
          cy: railY + railHeight - 8,
          r: 6,
          class: "ts-chip-circle s"
        }}));
        boxes.append(el("text", {{
          x: panel.x + railWidth / 2,
          y: railY + railHeight - 7.5,
          class: "ts-chip-letter s"
        }}, "S"));
      }}
      const tip = [
        group.id,
        group.laneKey,
        `${{panel.key}}: y=${{panel.data.y ? 1 : 0}}, s=${{panel.data.s ? 1 : 0}}`,
        `unit=${{panel.data.unitId}}`
      ].join("\\n");
      const panelHit = el("rect", {{
        x: panel.x,
        y: railY - 10,
        width: railWidth,
        height: railHeight + 20,
        fill: "transparent"
      }});
      attachTip(panelHit, tip);
      boxes.append(panelHit);
    }}
    boxes.append(el("text", {{ x: group.x + 4, y: group.y - 5, class: "group-label" }}, group.label));
  }}
  for (const node of data.nodes) {{
    const nodeGroup = el("g", {{ transform: `translate(${{node.x}} ${{node.y}})`, "data-node-id": node.id }});
    let shape;
    if (node.kind === "target") {{
      shape = el("rect", {{
        x: -6.5,
        y: -6.5,
        width: 13,
        height: 13,
        rx: 2,
        class: `node ${{node.kind}}`,
        transform: "rotate(45)",
      }});
    }} else {{
      const radius = node.kind === "vessel" ? 6 : node.kind === "idle" ? 7 : 5;
      shape = el("circle", {{ cx: 0, cy: 0, r: radius, class: `node ${{node.kind}}` }});
    }}
    nodeGroup.append(shape);
    if (node.kind === "idle" || node.kind === "target") {{
      nodeGroup.append(el("text", {{ x: 0, y: 0.5, class: "node-glyph" }}, node.kind === "idle" ? "I" : "T"));
    }}
    attachTip(nodeGroup, node.tooltip);
    nodeGroup.addEventListener("mouseenter", () => highlightConnected(node.id));
    nodeGroup.addEventListener("mouseleave", clearHighlight);
    nodes.append(nodeGroup);
    nodes.append(el("text", {{ x: node.x, y: node.y - 8, "text-anchor": "middle", class: "node-label", "data-node-label": node.id }}, node.label));
  }}
}}
function highlightConnected(nodeId) {{
  if (interactionMode === "mip") {{
    highlightMIP(arcsByNode.get(nodeId) || [], nodeId);
    return;
  }}
  clearHighlight();
  const highlight = viewport.querySelector("#highlight-layer");
  const incident = arcsByNode.get(nodeId) || [];
  const connectedIds = new Set([nodeId]);
  for (const arc of incident) {{
    connectedIds.add(arc.from);
    connectedIds.add(arc.to);
  }}
  for (const arc of incident) {{
    const path = el("path", {{ d: arc.path, class: "highlight-arc" }});
    attachTip(path, arc.tooltip);
    highlight.appendChild(path);
  }}
  for (const connectedId of connectedIds) {{
    const node = nodeById.get(connectedId);
    if (!node) continue;
    const circle = el("circle", {{
      cx: node.x,
      cy: node.y,
      r: node.kind === "vessel" ? 8 : 7,
      class: connectedId === nodeId ? "highlight-node focused" : "highlight-node",
    }});
    attachTip(circle, node.tooltip);
    highlight.appendChild(circle);
    highlight.appendChild(
      el("text", {{
        x: node.x,
        y: node.y - 11,
        "text-anchor": "middle",
        class: "highlight-label",
      }}, node.label)
    );
  }}
}}
function highlightArc(arc) {{
  if (interactionMode === "mip") {{
    highlightMIP(arcsByMIPGroup.get(arc.mipGroup) || [arc], null);
  }}
}}
function highlightMIP(arcs, focusedNodeId) {{
  clearHighlight();
  const highlight = viewport.querySelector("#highlight-layer");
  const connectedIds = new Set();
  for (const arc of arcs) {{
    connectedIds.add(arc.from);
    connectedIds.add(arc.to);
    const outline = el("path", {{ d: arc.path, class: "highlight-arc-outline" }});
    const emphasisAttrs = {{ d: arc.path, class: "highlight-arc-emphasis" }};
    if (arc.stroke) emphasisAttrs.style = `stroke: ${{arc.stroke}};`;
    if (arc.isFractionalFlow) {{
      outline.setAttribute("stroke-dasharray", "7 5");
      outline.setAttribute("opacity", "0.35");
      emphasisAttrs["stroke-dasharray"] = "7 5";
      emphasisAttrs.opacity = "0.5";
    }}
    const emphasis = el("path", emphasisAttrs);
    attachTip(outline, arc.tooltip);
    attachTip(emphasis, arc.tooltip);
    highlight.appendChild(outline);
    highlight.appendChild(emphasis);
  }}
  for (const connectedId of connectedIds) {{
    const node = nodeById.get(connectedId);
    if (!node) continue;
    const circle = el("circle", {{
      cx: node.x,
      cy: node.y,
      r: (node.kind === "vessel" ? 8 : 7),
      class: connectedId === focusedNodeId ? "highlight-node focused" : "highlight-node",
    }});
    attachTip(circle, node.tooltip);
    highlight.appendChild(circle);
    highlight.appendChild(
      el("text", {{
        x: node.x,
        y: node.y - 11,
        "text-anchor": "middle",
        class: "highlight-label",
      }}, node.label)
    );
  }}
}}
function clearHighlight() {{
  const highlight = viewport.querySelector("#highlight-layer");
  if (highlight) highlight.innerHTML = "";
}}
render();
applyTransform();

let dragging = false;
let last = null;
stage.addEventListener("mousedown", event => {{
  dragging = true;
  last = {{ x: event.clientX, y: event.clientY }};
  stage.classList.add("dragging");
}});
window.addEventListener("mousemove", event => {{
  if (!dragging) return;
  transform.x += event.clientX - last.x;
  transform.y += event.clientY - last.y;
  last = {{ x: event.clientX, y: event.clientY }};
  applyTransform();
}});
window.addEventListener("mouseup", () => {{
  dragging = false;
  stage.classList.remove("dragging");
}});
stage.addEventListener("wheel", event => {{
  event.preventDefault();
  if (event.ctrlKey || event.metaKey) {{
    const factor = event.deltaY < 0 ? 1.12 : 0.89;
    zoomAt(factor, event.clientX, event.clientY);
  }} else {{
    transform.x -= event.deltaX;
    transform.y -= event.deltaY;
    applyTransform();
  }}
}}, {{ passive: false }});
document.getElementById("zoom-in").addEventListener("click", () => {{
  const rect = svg.getBoundingClientRect();
  zoomAt(1.18, rect.left + rect.width / 2, rect.top + rect.height / 2);
}});
document.getElementById("zoom-out").addEventListener("click", () => {{
  const rect = svg.getBoundingClientRect();
  zoomAt(1 / 1.18, rect.left + rect.width / 2, rect.top + rect.height / 2);
}});
document.getElementById("reset").addEventListener("click", () => {{
  transform = {{ x: 0, y: 0, k: 1 }};
  applyTransform();
}});
</script>
</body>
    </html>
"""
    output.write_text(document, encoding="utf-8")
    if interactive:
        if sys.platform == "darwin":
            subprocess.run(
                ["open", str(output.resolve())],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            webbrowser.open(output.resolve().as_uri())


def viz(
    network_items: list[Node | NodeGroup],
    arcs: list[Arc],
    primary_lane_key: tuple[str, str, int],
    secondary_lane_keys: (
        set[tuple[str, str, int]] | list[tuple[str, str, int]] | tuple[tuple[str, str, int], ...] | None
    ) = None,
    output_path: str = "//multicommodity_network.html",
    interactive: bool = True,
    show_incoming_external_arcs: bool = True,
    show_outgoing_external_arcs: bool = True,
    show_idle_arcs: bool = True,
):
    _viz_impl(
        network_items=network_items,
        arcs=arcs,
        primary_lane_key=primary_lane_key,
        secondary_lane_keys=secondary_lane_keys,
        output_path=output_path,
        interactive=interactive,
        show_incoming_external_arcs=show_incoming_external_arcs,
        show_outgoing_external_arcs=show_outgoing_external_arcs,
        show_idle_arcs=show_idle_arcs,
    )


def viz_zero_arcs(
    network_items: list[Node | NodeGroup],
    arcs: list[Arc],
):
    _viz_impl(
        network_items=network_items,
        arcs=arcs,
        primary_lane_key=None,
        secondary_lane_keys=None,
        show_incoming_external_arcs=True,
        show_outgoing_external_arcs=True,
        show_idle_arcs=True,
        only_zero_capacity_arcs=True,
        use_lane_filtering=False,
    )
    return "network_viz.html"


def viz_graph(
    graph: nx.DiGraph,
    output_path: str = "//multicommodity_selected_paths.html",
    interactive: bool = True,
):
    network_items = []
    seen_item_ids = set()
    for _, data in graph.nodes(data=True):
        item = data.get("owner")
        if item is None or item.get_id() in seen_item_ids:
            continue
        seen_item_ids.add(item.get_id())
        network_items.append(item)

    arcs = []
    for _, _, data in graph.edges(data=True):
        arc = data.get("arc")
        if arc is not None:
            arcs.append(arc)

    _viz_impl(
        network_items=network_items,
        arcs=arcs,
        primary_lane_key=None,
        secondary_lane_keys=None,
        output_path=output_path,
        interactive=interactive,
        show_incoming_external_arcs=True,
        show_outgoing_external_arcs=True,
        show_idle_arcs=True,
        only_zero_capacity_arcs=False,
        use_lane_filtering=False,
    )


def viz_model_network(
    graph: nx.DiGraph,
    output_path: str = "//multicommodity_model_network.html",
    interactive: bool = False,
):
    network_items = []
    seen_item_ids = set()
    for _, data in graph.nodes(data=True):
        owner = data.get("owner")
        if owner is None or owner.get_id() in seen_item_ids:
            continue
        seen_item_ids.add(owner.get_id())
        network_items.append(owner)

    def owner_lane_key(owner: Node | NodeGroup | None) -> tuple[str, str, int] | None:
        if isinstance(owner, NodeGroup):
            return lane_info(owner.event)
        if isinstance(owner, HorizonSailNode):
            return lane_info(owner.event)
        return None

    arcs = []
    for from_node_id, to_node_id, data in graph.edges(data=True):
        arc = data.get("arc")
        if arc is None:
            continue

        from_owner = graph.nodes[from_node_id].get("owner")
        to_owner = graph.nodes[to_node_id].get("owner")
        from_lane_key = owner_lane_key(from_owner)
        to_lane_key = owner_lane_key(to_owner)

        if not isinstance(from_owner, NodeGroup) or not isinstance(to_owner, NodeGroup):
            arcs.append(arc)
        elif from_owner is to_owner:
            arcs.append(arc)
        elif from_lane_key == to_lane_key:
            arcs.append(arc)

    node_label_by_id = {}
    for node_id, data in graph.nodes(data=True):
        node = data.get("node")
        if node is None:
            continue
        label = getattr(node, "label", "") or type(node).__name__
        node_label_by_id[node_id] = f"{node_id} {label}".upper()

    _viz_impl(
        network_items=network_items,
        arcs=arcs,
        primary_lane_key=None,
        secondary_lane_keys=None,
        output_path=output_path,
        interactive=interactive,
        show_incoming_external_arcs=True,
        show_outgoing_external_arcs=True,
        show_idle_arcs=True,
        only_zero_capacity_arcs=False,
        use_lane_filtering=False,
        node_label_by_id=node_label_by_id,
    )


def viz_mip_result(
    network: nx.DiGraph,
    selected_edge_ids_by_vessel: dict[str, set[str]],
    base_edge_by_id: dict[str, dict[str, object]],
    ts_state_by_unit_id: dict[str, dict[str, bool]] | None = None,
    flow_value_by_vessel_edge: dict[str, dict[str, float]] | None = None,
    output_path: str = "//multicommodity_mip_paths.html",
    interactive: bool = True,
):
    palette = [
        "#2563eb",
        "#dc2626",
        "#16a34a",
        "#d97706",
        "#7c3aed",
        "#0891b2",
        "#db2777",
        "#4f46e5",
        "#65a30d",
        "#ea580c",
    ]

    network_items = []
    seen_item_ids = set()
    for _, data in network.nodes(data=True):
        owner = data.get("owner")
        if owner is None or owner.get_id() in seen_item_ids:
            continue
        seen_item_ids.add(owner.get_id())
        network_items.append(owner)

    arc_by_node_pair = {}
    arc_color_by_node_pair = {}
    arc_group_by_node_pair = {}
    arc_tooltip_lines_by_node_pair: dict[tuple[str, str], list[str]] = {}
    arc_flow_value_by_node_pair: dict[tuple[str, str], float] = {}
    edge_values_by_vessel = flow_value_by_vessel_edge or {
        vessel_code: {edge_id: 1.0 for edge_id in edge_ids}
        for vessel_code, edge_ids in selected_edge_ids_by_vessel.items()
    }
    for vessel_index, vessel_code in enumerate(sorted(edge_values_by_vessel)):
        color = palette[vessel_index % len(palette)]
        for edge_id, flow_value in edge_values_by_vessel[vessel_code].items():
            if flow_value <= 1e-9:
                continue
            edge = base_edge_by_id[edge_id]
            node_pair = (edge["from_node_id"], edge["to_node_id"])
            arc = network.edges[node_pair]["arc"]
            arc_by_node_pair[node_pair] = arc
            arc_color_by_node_pair.setdefault(node_pair, color)
            arc_group_by_node_pair.setdefault(node_pair, vessel_code)
            if arc_group_by_node_pair[node_pair] != vessel_code:
                arc_group_by_node_pair[node_pair] = "multiple"
            arc_flow_value_by_node_pair[node_pair] = arc_flow_value_by_node_pair.get(node_pair, 0.0) + flow_value
            arc_tooltip_lines_by_node_pair.setdefault(node_pair, []).append(
                f"vessel={vessel_code}, flow={flow_value:.2f}"
            )

    arc_tooltip_suffix_by_node_pair = {
        node_pair: f"flow={min(flow_value, 1.0):.2f}\n" + "\n".join(arc_tooltip_lines_by_node_pair[node_pair])
        for node_pair, flow_value in arc_flow_value_by_node_pair.items()
    }
    arc_flow_value_by_node_pair = {
        node_pair: min(flow_value, 1.0) for node_pair, flow_value in arc_flow_value_by_node_pair.items()
    }

    _viz_impl(
        network_items=network_items,
        arcs=list(arc_by_node_pair.values()),
        primary_lane_key=None,
        secondary_lane_keys=None,
        output_path=output_path,
        interactive=interactive,
        show_incoming_external_arcs=True,
        show_outgoing_external_arcs=True,
        show_idle_arcs=True,
        only_zero_capacity_arcs=False,
        use_lane_filtering=False,
        force_foreground_arcs=True,
        interaction_mode="mip",
        arc_color_by_node_pair=arc_color_by_node_pair,
        arc_group_by_node_pair=arc_group_by_node_pair,
        arc_tooltip_suffix_by_node_pair=arc_tooltip_suffix_by_node_pair,
        arc_flow_value_by_node_pair=arc_flow_value_by_node_pair,
        ts_state_by_unit_id=ts_state_by_unit_id,
    )

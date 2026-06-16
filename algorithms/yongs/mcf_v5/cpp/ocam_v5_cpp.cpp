#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <exception>
#include <fstream>
#include <functional>
#include <iomanip>
#include <limits>
#include <map>
#include <queue>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <tuple>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

#include "gurobi_c++.h"

namespace {

constexpr long long HOUR = 3600;
constexpr long long DAY = 24 * HOUR;
constexpr double CAPACITY_TOLERANCE = 0.05;
constexpr double UNATTRIBUTED_BUNKER_PRICE_PER_TON = 0.0;

std::vector<std::string> split_tab(const std::string& line) {
    std::vector<std::string> out;
    std::string cell;
    std::stringstream ss(line);
    while (std::getline(ss, cell, '\t')) out.push_back(cell);
    if (!line.empty() && line.back() == '\t') out.emplace_back();
    return out;
}

std::vector<std::vector<std::string>> read_tsv(const std::string& path) {
    std::ifstream in(path);
    if (!in) throw std::runtime_error("failed to open " + path);
    std::vector<std::vector<std::string>> rows;
    std::string line;
    bool first = true;
    while (std::getline(in, line)) {
        if (first) {
            first = false;
            continue;
        }
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.empty()) continue;
        rows.push_back(split_tab(line));
    }
    return rows;
}

long long to_i64(const std::string& value) {
    if (value.empty()) return 0;
    return std::stoll(value);
}

double to_f64(const std::string& value) {
    if (value.empty()) return 0.0;
    return std::stod(value);
}

bool is_canal_port(const std::string& port_code) {
    return port_code == "EGSUZ" || port_code == "EGSCA" || port_code == "PAPCA";
}

std::string canonical_canal_port(const std::string& port_code) {
    return port_code == "EGSUZ" ? "EGSCA" : port_code;
}

std::string node_id_text(int node_id) {
    return "N" + std::to_string(node_id);
}

std::string arc_id_text(int arc_id) {
    return "A" + std::to_string(arc_id);
}

std::string year_month(long long epoch_seconds) {
    std::time_t raw = static_cast<std::time_t>(epoch_seconds);
    std::tm tm_value{};
#if defined(_WIN32)
    gmtime_s(&tm_value, &raw);
#else
    gmtime_r(&raw, &tm_value);
#endif
    char buffer[16];
    std::strftime(buffer, sizeof(buffer), "%Y%m", &tm_value);
    return buffer;
}

struct Rotation {
    std::string port_code;
    int port_seq = 0;
    long long eta_offset_minutes = 0;
    long long etb_offset_minutes = 0;
    long long etd_offset_minutes = 0;
    long long pilot_out_minutes = 0;
    std::string direction;
};

struct Version {
    std::string lane_code;
    std::string proforma_name;
    double service_duration_days = 0.0;
    long long anchor_time = 0;
    bool has_effective_to = false;
    long long effective_to = 0;
    double required_capacity_teu = 0.0;
    double required_reefer_plug = 0.0;
    int own_vessel_count = 0;
    std::set<int> declared_positions;
    std::set<int> available_positions;
    std::vector<Rotation> rotations;
};

struct Vessel {
    std::string vessel_code;
    int capacity_teu = 0;
    int reefer_plug = 0;
    bool has_available_from = false;
    long long available_from = 0;
    std::string available_from_port;
    bool has_available_to = false;
    long long available_to = 0;
    std::string available_to_port;
};

struct PositionKey {
    std::string lane_code;
    std::string proforma_name;
    int position_no = 0;

    bool operator<(const PositionKey& other) const {
        return std::tie(lane_code, proforma_name, position_no) <
               std::tie(other.lane_code, other.proforma_name, other.position_no);
    }

    bool operator==(const PositionKey& other) const {
        return lane_code == other.lane_code && proforma_name == other.proforma_name && position_no == other.position_no;
    }
};

std::string key_string(const PositionKey& key) {
    return key.lane_code + "\t" + key.proforma_name + "\t" + std::to_string(key.position_no);
}

struct PortStay {
    PositionKey lane_key;
    std::string port_code;
    int port_seq = 0;
    long long pilot_in_start = 0;
    long long berthing_start = 0;
    long long berthing_end = 0;
    long long pilot_out_end = 0;
    std::string direction;
};

enum class ItemKind { Node, Group };
enum class GroupKind { PS, SS, SE };

struct Node {
    int id = -1;
    int owner_item = -1;
    std::string label;
    std::string event_kind;
    std::string vessel_code;
    PositionKey lane_key;
    bool has_lane_key = false;
    std::string start_port;
    std::string end_port;
    int start_port_seq = 0;
    int end_port_seq = 0;
    long long event_start = 0;
    long long event_end = 0;
    long long node_in = 0;
    long long node_out = 0;
    bool is_horizon = false;
    std::string horizon_side;
    double distance = 0.0;
};

struct Group {
    int group_id = -1;
    GroupKind kind = GroupKind::PS;
    PortStay event;
    bool is_canal = false;
    bool is_first = false;
    bool is_last = false;
    int pilot_in = -1;
    int pilot_out = -1;
    int ss = -1;
    int se = -1;
    int ts_in_before = -1;
    int ts_out_before = -1;
    int ts_in_after = -1;
    int ts_out_after = -1;
};

struct Item {
    ItemKind kind = ItemKind::Node;
    int node_id = -1;
    int group_index = -1;
};

struct Arc {
    int arc_id = -1;
    int from_node = -1;
    int to_node = -1;
    std::string type;
    double distance = 0.0;
    double sail_time = 0.0;
    bool has_cost = false;
    double cost = 0.0;
    std::string canal_port_code;
    std::string canal_direction;
    double canal_leg1_distance = 0.0;
    double canal_leg1_eca_distance = 0.0;
    double canal_leg2_distance = 0.0;
    double canal_leg2_eca_distance = 0.0;
    double canal_passage_hours = 0.0;
};

struct DistanceInfo {
    double distance = 0.0;
    double eca_distance = 0.0;
};

struct DdCoupling {
    int coupling_index = 0;
    std::string original_vessel_code;
    std::string before_vessel_code;
    std::string after_vessel_code;
};

struct Data {
    long long planning_start = 0;
    long long planning_end = 0;
    std::string model_name = "mcf_v5";
    std::vector<Version> versions;
    std::map<std::pair<std::string, std::string>, int> version_index;
    std::vector<Vessel> vessels;
    std::vector<PositionKey> positions;
    std::map<PositionKey, std::string> current_assignment_by_lane_key;
    std::map<std::pair<std::string, std::string>, double> distance_matrix;
    std::map<std::pair<std::string, std::string>, DistanceInfo> distance_info_by_port_pair;
    std::map<std::string, std::vector<std::pair<std::string, double>>> distance_adjacency;
    std::map<std::pair<std::string, std::string>, std::pair<double, double>> capacity_interval_by_version;
    std::set<std::string> eca_ports;
    std::map<int, std::map<double, double>> bunker_sea_by_capacity;
    std::map<int, double> bunker_port_pilot_by_capacity;
    std::map<std::tuple<std::string, std::string, std::string>, double> bunker_price_by_key;
    std::map<std::tuple<std::string, std::string, std::string>, double> transshipment_cost_by_key;
    std::map<std::tuple<std::string, std::string, std::string>, double> canal_fee_by_key;
    std::map<std::tuple<std::string, std::string, std::string>, std::string> canal_direction_by_key;
    std::map<std::pair<std::string, std::string>, double> canal_passage_hours_by_key;
    std::map<std::tuple<std::string, std::string, std::string>, double> opportunity_cost_by_key;
    std::map<std::tuple<std::string, std::string, int>, std::string> direction_by_lane_version_seq;
    std::vector<DdCoupling> dd_couplings;
};

struct Builder {
    Data data;
    int next_node_id = 0;
    int next_group_id = 0;
    mutable int next_arc_id = 0;
    std::vector<Node> nodes;
    std::vector<Group> groups;
    std::vector<Item> items;
    std::vector<Arc> arcs;
    std::map<PositionKey, std::vector<int>> lane_items;

    explicit Builder(Data input) : data(std::move(input)) {}

    int add_node(Node node) {
        node.id = next_node_id++;
        nodes.push_back(std::move(node));
        return nodes.back().id;
    }

    int add_node_item(Node node) {
        int node_id = add_node(std::move(node));
        int item_index = static_cast<int>(items.size());
        nodes[node_id].owner_item = item_index;
        items.push_back(Item{ItemKind::Node, node_id, -1});
        return item_index;
    }

    int add_group(Group group) {
        group.group_id = next_group_id++;
        int group_index = static_cast<int>(groups.size());
        groups.push_back(std::move(group));
        int item_index = static_cast<int>(items.size());
        items.push_back(Item{ItemKind::Group, -1, group_index});
        for (int node_id : group_nodes(groups[group_index])) nodes[node_id].owner_item = item_index;
        return item_index;
    }

    std::vector<int> group_nodes(const Group& group) const {
        std::vector<int> out;
        auto push = [&](int node_id) {
            if (node_id >= 0) out.push_back(node_id);
        };
        if (group.kind == GroupKind::SS) push(group.ss);
        if (group.kind == GroupKind::SE) push(group.se);
        push(group.pilot_in);
        push(group.pilot_out);
        push(group.ts_in_before);
        push(group.ts_out_before);
        push(group.ts_in_after);
        push(group.ts_out_after);
        return out;
    }

    const Version& version_for(const PositionKey& key) const {
        auto it = data.version_index.find({key.lane_code, key.proforma_name});
        if (it == data.version_index.end()) throw std::runtime_error("unknown version " + key_string(key));
        return data.versions[it->second];
    }

    long long service_start(const PositionKey& key) const {
        const Version& version = version_for(key);
        return version.anchor_time + static_cast<long long>(7 * (key.position_no - 1)) * DAY;
    }

    long long service_end(const PositionKey& key) const {
        const Version& version = version_for(key);
        if (!version.has_effective_to) return data.planning_end;
        long long start = service_start(key);
        long long duration = static_cast<long long>(std::llround(version.service_duration_days * DAY));
        long long offset = 0;
        while (start + offset < version.effective_to) offset += duration;
        return start + offset;
    }

    double lookup_distance(const std::string& from_port, const std::string& to_port) const {
        if (from_port == to_port) return 0.0;
        auto direct = data.distance_matrix.find({from_port, to_port});
        if (direct != data.distance_matrix.end()) return direct->second;
        auto alias = [](const std::string& port) {
            if (port == "EGSUZ") return std::string("EGSCA");
            if (port == "EGSCA") return std::string("EGSUZ");
            return port;
        };
        std::set<std::pair<std::string, std::string>> candidates;
        candidates.insert({from_port, to_port});
        candidates.insert({alias(from_port), to_port});
        candidates.insert({from_port, alias(to_port)});
        candidates.insert({alias(from_port), alias(to_port)});
        for (const auto& pair : candidates) {
            if (pair.first == pair.second) return 0.0;
            auto it = data.distance_matrix.find(pair);
            if (it != data.distance_matrix.end()) return it->second;
        }

        using State = std::pair<double, std::string>;
        std::priority_queue<State, std::vector<State>, std::greater<State>> q;
        std::unordered_map<std::string, double> best;
        std::set<std::string> targets = {to_port, alias(to_port)};
        for (const auto& start : std::set<std::string>{from_port, alias(from_port)}) {
            best[start] = 0.0;
            q.push({0.0, start});
        }
        while (!q.empty()) {
            auto [distance, port] = q.top();
            q.pop();
            if (distance > best[port]) continue;
            if (targets.count(port)) return distance * 0.8;
            auto adj = data.distance_adjacency.find(port);
            if (adj == data.distance_adjacency.end()) continue;
            for (const auto& [next_port, leg_distance] : adj->second) {
                double next_distance = distance + leg_distance;
                if (!best.count(next_port) || next_distance < best[next_port]) {
                    best[next_port] = next_distance;
                    q.push({next_distance, next_port});
                }
            }
        }
        throw std::runtime_error("distance lookup failed " + from_port + " -> " + to_port);
    }

    std::string canonical_for_canal_lookup(const std::string& port_code) const {
        return canonical_canal_port(port_code);
    }

    DistanceInfo exact_canal_distance_info(const std::string& from_port, const std::string& to_port) const {
        std::string from_key = canonical_for_canal_lookup(from_port);
        std::string to_key = canonical_for_canal_lookup(to_port);
        if (from_key == to_key) return DistanceInfo{0.0, 0.0};
        auto it = data.distance_info_by_port_pair.find({from_key, to_key});
        if (it == data.distance_info_by_port_pair.end()) {
            throw std::runtime_error(
                "missing canal route distance for from_port_code=" + from_key + ", to_port_code=" + to_key
            );
        }
        return it->second;
    }

    std::string canal_direction_for_route(
        const std::string& from_port, const std::string& canal_port, const std::string& to_port
    ) const {
        std::string from_key = canonical_for_canal_lookup(from_port);
        std::string canal_key = canonical_for_canal_lookup(canal_port);
        std::string to_key = canonical_for_canal_lookup(to_port);
        auto it = data.canal_direction_by_key.find({from_key, canal_key, to_key});
        if (it == data.canal_direction_by_key.end()) {
            // cost_canal_direction is the explicit admissibility table for out-lane canal detours.
            return "";
        }
        return it->second;
    }

    double canal_passage_hours(const std::string& canal_port, const std::string& direction) const {
        std::string canal_key = canonical_for_canal_lookup(canal_port);
        auto it = data.canal_passage_hours_by_key.find({canal_key, direction});
        if (it == data.canal_passage_hours_by_key.end()) {
            throw std::runtime_error(
                "missing canal passage time for port_code=" + canal_key + ", direction=" + direction
            );
        }
        return it->second;
    }

    std::vector<PortStay> generate_port_stays(
        const PositionKey& key, long long start, long long end, bool filter_to_planning
    ) const {
        const Version& version = version_for(key);
        std::vector<PortStay> stays;
        long long duration = static_cast<long long>(std::llround(version.service_duration_days * DAY));
        int trip = 0;
        while (start + static_cast<long long>(trip) * duration < end) {
            long long trip_offset = start + static_cast<long long>(trip) * duration;
            long long next_offset = trip_offset + duration;
            int rotation_count = static_cast<int>(version.rotations.size());
            if (next_offset < end) rotation_count -= 1;
            for (int i = 0; i < rotation_count; ++i) {
                const Rotation& rotation = version.rotations[i];
                PortStay stay;
                stay.lane_key = key;
                stay.port_code = rotation.port_code;
                stay.port_seq = rotation.port_seq;
                stay.pilot_in_start = trip_offset + rotation.eta_offset_minutes * 60;
                stay.berthing_start = trip_offset + rotation.etb_offset_minutes * 60;
                stay.berthing_end = trip_offset + rotation.etd_offset_minutes * 60;
                stay.pilot_out_end = trip_offset + (rotation.etd_offset_minutes + rotation.pilot_out_minutes) * 60;
                stay.direction = rotation.direction;
                if (!filter_to_planning ||
                    (stay.pilot_in_start <= data.planning_end && stay.pilot_out_end >= data.planning_start)) {
                    stays.push_back(std::move(stay));
                }
            }
            ++trip;
        }
        return stays;
    }

    Node port_node(const PortStay& event, const std::string& label, long long node_in, long long node_out) {
        Node node;
        node.label = label;
        node.event_kind = "PortStay";
        node.lane_key = event.lane_key;
        node.has_lane_key = true;
        node.start_port = event.port_code;
        node.end_port = event.port_code;
        node.start_port_seq = event.port_seq;
        node.end_port_seq = event.port_seq;
        node.event_start = event.pilot_in_start;
        node.event_end = event.pilot_out_end;
        node.node_in = node_in;
        node.node_out = node_out;
        return node;
    }

    int make_port_node(const PortStay& event, const std::string& label, long long node_in, long long node_out) {
        return add_node(port_node(event, label, node_in, node_out));
    }

    Group make_ps(const PortStay& event, bool is_last, bool is_first) {
        Group group;
        group.kind = GroupKind::PS;
        group.event = event;
        group.is_canal = is_canal_port(event.port_code);
        group.is_first = is_first;
        group.is_last = is_last;
        group.pilot_in = make_port_node(event, "pilot_in", event.pilot_in_start, event.pilot_in_start);
        group.pilot_out = make_port_node(event, "pilot_out", event.pilot_out_end, event.pilot_out_end);
        if (!group.is_canal && !group.is_first) {
            group.ts_in_before = make_port_node(event, "ts_in_before", event.pilot_in_start - 6 * HOUR, event.pilot_in_start);
            group.ts_out_before =
                make_port_node(event, "ts_out_before", event.pilot_in_start - 36 * HOUR, event.pilot_in_start - 30 * HOUR);
        }
        if (!group.is_canal && !group.is_last) {
            group.ts_in_after =
                make_port_node(event, "ts_in_after", event.pilot_out_end + 30 * HOUR, event.pilot_out_end + 36 * HOUR);
            group.ts_out_after =
                make_port_node(event, "ts_out_after", event.pilot_out_end, event.pilot_out_end + 6 * HOUR);
        }
        return group;
    }

    Group make_ss(long long service_start_time, const PortStay& event) {
        Group group;
        group.kind = GroupKind::SS;
        group.event = event;
        group.is_canal = is_canal_port(event.port_code);
        group.ss = make_port_node(event, "ss", event.pilot_in_start, event.pilot_in_start);
        group.pilot_in = make_port_node(event, "pilot_in", event.pilot_in_start, event.pilot_in_start);
        group.pilot_out = make_port_node(event, "pilot_out", event.pilot_out_end, event.pilot_out_end);
        (void)service_start_time;
        if (!group.is_canal) {
            group.ts_in_after =
                make_port_node(event, "ts_in_after", event.pilot_out_end + 30 * HOUR, event.pilot_out_end + 36 * HOUR);
            group.ts_out_after =
                make_port_node(event, "ts_out_after", event.pilot_out_end, event.pilot_out_end + 6 * HOUR);
        }
        return group;
    }

    Group make_se(long long service_end_time, const PortStay& event, bool is_first) {
        Group group;
        group.kind = GroupKind::SE;
        group.event = event;
        group.is_canal = is_canal_port(event.port_code);
        group.is_first = is_first;
        group.se = make_port_node(event, "se", event.pilot_out_end, event.pilot_out_end);
        group.pilot_in = make_port_node(event, "pilot_in", event.pilot_in_start, event.pilot_in_start);
        group.pilot_out = make_port_node(event, "pilot_out", event.pilot_out_end, event.pilot_out_end);
        (void)service_end_time;
        if (!group.is_canal && !group.is_first) {
            group.ts_in_before = make_port_node(event, "ts_in_before", event.pilot_in_start - 6 * HOUR, event.pilot_in_start);
            group.ts_out_before =
                make_port_node(event, "ts_out_before", event.pilot_in_start - 36 * HOUR, event.pilot_in_start - 30 * HOUR);
        }
        return group;
    }

    Node horizon_node(const PortStay& left, const PortStay& right, const std::string& side) {
        double distance = lookup_distance(left.port_code, right.port_code);
        double hours = static_cast<double>(right.pilot_in_start - left.pilot_out_end) / 3600.0;
        Node node;
        node.label = "horizon_sail";
        node.event_kind = "InLaneSail";
        node.lane_key = left.lane_key;
        node.has_lane_key = true;
        node.start_port = left.port_code;
        node.end_port = right.port_code;
        node.start_port_seq = left.port_seq;
        node.end_port_seq = right.port_seq;
        node.event_start = left.pilot_out_end;
        node.event_end = right.pilot_in_start;
        node.node_in = node.event_start;
        node.node_out = node.event_end;
        node.is_horizon = true;
        node.horizon_side = side;
        node.distance = distance;
        (void)hours;
        return node;
    }

    std::vector<int> inbound_nodes(const Group& group) const {
        if (group.kind == GroupKind::SS) {
            if (group.is_canal) return {group.ss};
            return {group.ts_in_after, group.ss};
        }
        if (group.kind == GroupKind::SE) {
            if (group.ts_in_before >= 0) return {group.ts_in_before};
            return {};
        }
        if (group.is_canal) return {};
        std::vector<int> out;
        if (group.ts_in_before >= 0) out.push_back(group.ts_in_before);
        if (group.ts_in_after >= 0) out.push_back(group.ts_in_after);
        return out;
    }

    std::vector<int> outbound_nodes(const Group& group) const {
        if (group.kind == GroupKind::SS) {
            if (group.is_canal) return {};
            return {group.ts_out_after};
        }
        if (group.kind == GroupKind::SE) {
            std::vector<int> out;
            if (group.ts_out_before >= 0) out.push_back(group.ts_out_before);
            out.push_back(group.se);
            return out;
        }
        std::vector<int> out;
        if (group.ts_out_before >= 0) out.push_back(group.ts_out_before);
        if (group.is_last) {
            out.push_back(group.pilot_out);
        } else if (group.ts_out_after >= 0) {
            out.push_back(group.ts_out_after);
        }
        return out;
    }

    Arc make_arc(
        int from_node,
        int to_node,
        const std::string& type = "Arc",
        double distance = 0.0,
        double sail_time = 0.0,
        bool has_cost = false,
        double cost = 0.0
    ) const {
        Arc arc;
        arc.arc_id = next_arc_id++;
        arc.from_node = from_node;
        arc.to_node = to_node;
        arc.type = type;
        arc.distance = distance;
        arc.sail_time = sail_time;
        arc.has_cost = has_cost;
        arc.cost = cost;
        return arc;
    }

    Arc make_canal_arc(
        int from_node,
        int to_node,
        const std::string& canal_port_code,
        const std::string& direction,
        const DistanceInfo& first_leg,
        const DistanceInfo& second_leg,
        double passage_hours,
        double available_hours
    ) const {
        Arc arc = make_arc(
            from_node,
            to_node,
            "CanalSailArc",
            first_leg.distance + second_leg.distance,
            available_hours
        );
        arc.canal_port_code = canal_port_code;
        arc.canal_direction = direction;
        arc.canal_leg1_distance = first_leg.distance;
        arc.canal_leg1_eca_distance = first_leg.eca_distance;
        arc.canal_leg2_distance = second_leg.distance;
        arc.canal_leg2_eca_distance = second_leg.eca_distance;
        arc.canal_passage_hours = passage_hours;
        return arc;
    }

    void add_arc(
        int from_node,
        int to_node,
        const std::string& type = "Arc",
        double distance = 0.0,
        double sail_time = 0.0,
        bool has_cost = false,
        double cost = 0.0
    ) {
        arcs.push_back(make_arc(from_node, to_node, type, distance, sail_time, has_cost, cost));
    }

    std::vector<Arc> build_arc_between_nodes(int from_node_id, int to_node_id) const {
        const Node& from_node = nodes[from_node_id];
        const Node& to_node = nodes[to_node_id];
        if (from_node.end_port == to_node.start_port) {
            return {make_arc(from_node_id, to_node_id, "Arc", 0.0, 0.0, true, 0.0)};
        }
        std::vector<Arc> out;
        double sail_time = static_cast<double>(to_node.node_in - from_node.node_out) / 3600.0;
        double distance = lookup_distance(from_node.end_port, to_node.start_port);
        if (!(sail_time < 0 || distance / (sail_time + 1e-5) > 20.0 ||
              (std::abs(sail_time) < 1e-5 && distance > 1e-5))) {
            out.push_back(make_arc(from_node_id, to_node_id, "SailArc", distance, sail_time));
        }
        if (sail_time < 0.0) return out;
        if (from_node.has_lane_key && to_node.has_lane_key && from_node.lane_key == to_node.lane_key) return out;
        if (from_node.end_port.size() >= 2 && to_node.start_port.size() >= 2 &&
            from_node.end_port.substr(0, 2) == to_node.start_port.substr(0, 2)) {
            return out;
        }
        for (const std::string& canal_port : std::vector<std::string>{"EGSCA", "PAPCA"}) {
            std::string direction = canal_direction_for_route(from_node.end_port, canal_port, to_node.start_port);
            // A missing or blank direction means this detour route is not allowed by the input data.
            if (direction.empty()) continue;
            DistanceInfo first_leg = exact_canal_distance_info(from_node.end_port, canal_port);
            DistanceInfo second_leg = exact_canal_distance_info(canal_port, to_node.start_port);
            if (first_leg.distance / 20.0 + second_leg.distance / 20.0 > sail_time + 1e-9) continue;
            double passage_hours = canal_passage_hours(canal_port, direction);
            double min_hours = first_leg.distance / 20.0 + second_leg.distance / 20.0 + passage_hours;
            if (min_hours > sail_time + 1e-9) continue;
            out.push_back(
                make_canal_arc(from_node_id, to_node_id, canal_port, direction, first_leg, second_leg, passage_hours, sail_time)
            );
        }
        return out;
    }

    bool has_overlapping_capacity(const Group& left, const Group& right) const {
        auto left_version = std::make_pair(left.event.lane_key.lane_code, left.event.lane_key.proforma_name);
        auto right_version = std::make_pair(right.event.lane_key.lane_code, right.event.lane_key.proforma_name);
        if (left_version == right_version) return true;
        auto left_interval = data.capacity_interval_by_version.at(left_version);
        auto right_interval = data.capacity_interval_by_version.at(right_version);
        return std::max(left_interval.first, right_interval.first) <= std::min(left_interval.second, right_interval.second);
    }

    std::vector<Arc> build_arcs_between_items(const Item& left_item, const Item& right_item) const {
        if (left_item.kind == ItemKind::Node && nodes[left_item.node_id].event_kind == "Redelivery") return {};
        if (right_item.kind == ItemKind::Node && nodes[right_item.node_id].event_kind == "Delivery") return {};
        std::vector<Arc> out;
        if (left_item.kind == ItemKind::Group && right_item.kind == ItemKind::Group) {
            const Group& left = groups[left_item.group_index];
            const Group& right = groups[right_item.group_index];
            if (left.event.lane_key == right.event.lane_key) return {};
            if (!has_overlapping_capacity(left, right)) return {};
            for (int start : outbound_nodes(left)) {
                for (int end : inbound_nodes(right)) {
                    auto next = build_arc_between_nodes(start, end);
                    out.insert(out.end(), next.begin(), next.end());
                }
            }
        } else if (left_item.kind == ItemKind::Group && right_item.kind == ItemKind::Node) {
            const Group& left = groups[left_item.group_index];
            for (int start : outbound_nodes(left)) {
                auto next = build_arc_between_nodes(start, right_item.node_id);
                out.insert(out.end(), next.begin(), next.end());
            }
        } else if (left_item.kind == ItemKind::Node && right_item.kind == ItemKind::Group) {
            const Group& right = groups[right_item.group_index];
            for (int end : inbound_nodes(right)) {
                auto next = build_arc_between_nodes(left_item.node_id, end);
                out.insert(out.end(), next.begin(), next.end());
            }
        } else {
            auto next = build_arc_between_nodes(left_item.node_id, right_item.node_id);
            out.insert(out.end(), next.begin(), next.end());
        }
        return out;
    }

    long long item_start_time(const Item& item) const {
        if (item.kind == ItemKind::Node) return nodes[item.node_id].event_start;
        return groups[item.group_index].event.pilot_in_start;
    }

    long long item_end_time(const Item& item) const {
        if (item.kind == ItemKind::Node) return nodes[item.node_id].event_end;
        return groups[item.group_index].event.pilot_out_end;
    }

    std::set<PositionKey> horizon_start_lane_keys() const {
        std::set<PositionKey> out;
        for (const PositionKey& key : data.positions) {
            if (!data.current_assignment_by_lane_key.count(key)) continue;
            long long start = service_start(key);
            long long end = service_end(key);
            auto all_stays = generate_port_stays(key, start, end, false);
            for (size_t i = 0; i + 1 < all_stays.size(); ++i) {
                const auto& left = all_stays[i];
                const auto& right = all_stays[i + 1];
                if (!(left.pilot_out_end < data.planning_start && data.planning_start < right.pilot_in_start)) continue;
                if (!(right.pilot_in_start < data.planning_end && right.pilot_out_end > data.planning_start)) continue;
                out.insert(key);
                break;
            }
        }
        return out;
    }

    void build_vessel_nodes() {
        std::vector<Vessel> sorted_vessels = data.vessels;
        std::sort(sorted_vessels.begin(), sorted_vessels.end(), [](const Vessel& a, const Vessel& b) {
            return a.vessel_code < b.vessel_code;
        });
        for (const Vessel& vessel : sorted_vessels) {
            if (vessel.has_available_from) {
                Node node;
                node.label = "delivery";
                node.event_kind = "Delivery";
                node.vessel_code = vessel.vessel_code;
                node.start_port = vessel.available_from_port;
                node.end_port = vessel.available_from_port;
                node.event_start = vessel.available_from;
                node.event_end = vessel.available_from;
                node.node_in = vessel.available_from;
                node.node_out = vessel.available_from;
                add_node_item(std::move(node));
            }
            if (vessel.has_available_to && vessel.available_to <= data.planning_end) {
                Node node;
                node.label = "redelivery";
                node.event_kind = "Redelivery";
                node.vessel_code = vessel.vessel_code;
                node.start_port = vessel.available_to_port;
                node.end_port = vessel.available_to_port;
                node.event_start = vessel.available_to;
                node.event_end = vessel.available_to;
                node.node_in = vessel.available_to;
                node.node_out = vessel.available_to;
                add_node_item(std::move(node));
            }
        }
    }

    void build_lane_nodes() {
        auto horizon_starts = horizon_start_lane_keys();
        std::map<std::pair<std::string, std::string>, std::vector<int>> positions_by_version;
        for (const auto& key : data.positions) positions_by_version[{key.lane_code, key.proforma_name}].push_back(key.position_no);
        for (const Version& version : data.versions) {
            auto vp = std::make_pair(version.lane_code, version.proforma_name);
            auto pos_it = positions_by_version.find(vp);
            if (pos_it == positions_by_version.end()) continue;
            std::sort(pos_it->second.begin(), pos_it->second.end());
            for (int position_no : pos_it->second) {
                PositionKey key{version.lane_code, version.proforma_name, position_no};
                long long start = service_start(key);
                long long end = service_end(key);
                std::vector<int>& key_items = lane_items[key];
                auto all_stays = generate_port_stays(key, start, end, false);
                std::vector<PortStay> stays;
                for (const auto& stay : all_stays) {
                    if (stay.pilot_in_start <= data.planning_end && stay.pilot_out_end >= data.planning_start &&
                        stay.pilot_in_start < data.planning_end && stay.pilot_out_end > data.planning_start) {
                        stays.push_back(stay);
                    }
                }
                if (horizon_starts.count(key)) {
                    for (size_t i = 0; i + 1 < all_stays.size(); ++i) {
                        const auto& left = all_stays[i];
                        const auto& right = all_stays[i + 1];
                        if (!(left.pilot_out_end < data.planning_start && data.planning_start < right.pilot_in_start)) continue;
                        if (!(right.pilot_in_start < data.planning_end && right.pilot_out_end > data.planning_start)) continue;
                        key_items.push_back(add_node_item(horizon_node(left, right, "start")));
                        break;
                    }
                }
                for (size_t index = 0; index < stays.size(); ++index) {
                    const PortStay& stay = stays[index];
                    bool is_last = index == stays.size() - 1;
                    bool is_first = data.current_assignment_by_lane_key.count(key) && index == 0;
                    Group group;
                    if (stay.pilot_in_start == start) {
                        group = make_ss(start, stay);
                    } else if (stay.pilot_in_start == end) {
                        group = make_se(end, stay, is_first);
                    } else {
                        group = make_ps(stay, is_last, is_first);
                    }
                    key_items.push_back(add_group(std::move(group)));
                }
                if (!key_items.empty()) {
                    int last_group_item = -1;
                    for (auto it = key_items.rbegin(); it != key_items.rend(); ++it) {
                        if (items[*it].kind == ItemKind::Group) {
                            last_group_item = *it;
                            break;
                        }
                    }
                    if (last_group_item >= 0) {
                        const Group& last_group = groups[items[last_group_item].group_index];
                        const PortStay& last_event = last_group.event;
                        if (last_event.pilot_out_end < data.planning_end) {
                            for (const auto& next_stay : all_stays) {
                                if (last_event.pilot_out_end < data.planning_end &&
                                    data.planning_end < next_stay.pilot_in_start) {
                                    key_items.push_back(add_node_item(horizon_node(last_event, next_stay, "end")));
                                    break;
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    std::map<std::string, int> current_assignment_source_by_vessel() const {
        std::map<std::string, int> out;
        for (const auto& entry : data.current_assignment_by_lane_key) {
            auto nodes_it = lane_items.find(entry.first);
            if (nodes_it == lane_items.end() || nodes_it->second.empty()) continue;
            const Item& first_item = items[nodes_it->second[0]];
            if (first_item.kind == ItemKind::Node) {
                out[entry.second] = first_item.node_id;
            } else {
                out[entry.second] = groups[first_item.group_index].pilot_in;
            }
        }
        return out;
    }

    void build_internal_arcs_for_group(const Group& group) {
        if (group.kind == GroupKind::SS) {
            add_arc(group.ss, group.pilot_in, "Arc", 0.0, 0.0, true, 0.0);
            add_arc(group.pilot_in, group.pilot_out, "Arc", 0.0, 0.0, true, 0.0);
            if (!group.is_canal) add_arc(group.pilot_out, group.ts_out_after);
            return;
        }
        if (group.kind == GroupKind::SE) {
            add_arc(group.pilot_in, group.pilot_out, "Arc", 0.0, 0.0, true, 0.0);
            add_arc(group.pilot_out, group.se, "Arc", 0.0, 0.0, true, 0.0);
            if (group.ts_in_before >= 0) add_arc(group.ts_in_before, group.pilot_in);
            return;
        }
        add_arc(group.pilot_in, group.pilot_out, "Arc", 0.0, 0.0, true, 0.0);
        if (group.ts_in_before >= 0) add_arc(group.ts_in_before, group.pilot_in);
        if (group.ts_out_after >= 0) add_arc(group.pilot_out, group.ts_out_after);
    }

    void build_arcs() {
        std::set<std::string> committed_vessels;
        std::set<int> committed_delivery_node_ids;
        auto source_by_vessel = current_assignment_source_by_vessel();
        std::map<std::string, int> delivery_node_by_vessel;
        for (const auto& item : items) {
            if (item.kind != ItemKind::Node) continue;
            const Node& node = nodes[item.node_id];
            if (node.event_kind == "Delivery") delivery_node_by_vessel[node.vessel_code] = item.node_id;
        }
        std::vector<std::pair<int, int>> committed_delivery_arcs;
        for (const auto& entry : delivery_node_by_vessel) {
            auto source_it = source_by_vessel.find(entry.first);
            if (source_it == source_by_vessel.end()) continue;
            const Node& delivery = nodes[entry.second];
            const Node& source = nodes[source_it->second];
            if (!(data.planning_start <= delivery.node_out && delivery.node_out <= source.node_in &&
                  source.node_in <= data.planning_end)) {
                throw std::runtime_error("invalid delivery-to-current-assignment timing for " + entry.first);
            }
            committed_vessels.insert(entry.first);
            committed_delivery_node_ids.insert(entry.second);
            committed_delivery_arcs.push_back({entry.second, source_it->second});
        }

        for (const auto& lane_entry : lane_items) {
            const auto& item_indices = lane_entry.second;
            for (size_t i = 0; i + 1 < item_indices.size(); ++i) {
                const Item& left_item = items[item_indices[i]];
                const Item& right_item = items[item_indices[i + 1]];
                if (left_item.kind == ItemKind::Node && right_item.kind == ItemKind::Group) {
                    const Node& left = nodes[left_item.node_id];
                    if (left.is_horizon) {
                        const Group& right = groups[right_item.group_index];
                        double sail_time = static_cast<double>(left.node_out - left.node_in) / 3600.0;
                        add_arc(left.id, right.pilot_in, "HorizonSailArc", left.distance, sail_time);
                        continue;
                    }
                }
                if (left_item.kind == ItemKind::Group && right_item.kind == ItemKind::Node) {
                    const Node& right = nodes[right_item.node_id];
                    if (right.is_horizon) {
                        const Group& left = groups[left_item.group_index];
                        double sail_time = static_cast<double>(right.node_out - nodes[left.pilot_out].node_out) / 3600.0;
                        add_arc(left.pilot_out, right.id, "HorizonSailArc", right.distance, sail_time);
                        continue;
                    }
                }
                if (left_item.kind == ItemKind::Group && right_item.kind == ItemKind::Group) {
                    const Group& left = groups[left_item.group_index];
                    const Group& right = groups[right_item.group_index];
                    auto add_between = [&](int from_id, int to_id) {
                        auto next = build_arc_between_nodes(from_id, to_id);
                        arcs.insert(arcs.end(), next.begin(), next.end());
                    };
                    add_between(left.pilot_out, right.pilot_in);
                    bool has_ts_in_after = left.ts_in_after >= 0;
                    bool has_ts_out_before = right.ts_out_before >= 0;
                    if (has_ts_in_after) {
                        add_between(left.ts_in_after, right.pilot_in);
                        if (has_ts_out_before) add_between(left.ts_in_after, right.ts_out_before);
                    }
                    if (has_ts_out_before) add_between(left.pilot_out, right.ts_out_before);
                    continue;
                }
                throw std::runtime_error("invalid lane item sequence");
            }
        }

        for (const auto& group : groups) build_internal_arcs_for_group(group);

        for (const auto& [from_id, to_id] : committed_delivery_arcs) {
            auto next = build_arc_between_nodes(from_id, to_id);
            if (next.empty()) throw std::runtime_error("unable to build committed delivery arc");
            arcs.insert(arcs.end(), next.begin(), next.end());
        }

        std::set<int> horizon_node_ids;
        for (const auto& node : nodes) {
            if (node.is_horizon) horizon_node_ids.insert(node.id);
        }
        std::set<int> horizon_tail_pilot_out_ids;
        for (const auto& lane_entry : lane_items) {
            const auto& xs = lane_entry.second;
            if (xs.size() >= 2 && items[xs.back()].kind == ItemKind::Node && nodes[items[xs.back()].node_id].is_horizon &&
                items[xs[xs.size() - 2]].kind == ItemKind::Group) {
                horizon_tail_pilot_out_ids.insert(groups[items[xs[xs.size() - 2]].group_index].pilot_out);
            }
        }

        size_t item_count_before_target = items.size();
        for (size_t i = 0; i < item_count_before_target; ++i) {
            const Item& left = items[i];
            if (left.kind == ItemKind::Node) {
                const Node& left_node = nodes[left.node_id];
                if (committed_delivery_node_ids.count(left.node_id)) continue;
                if (horizon_node_ids.count(left.node_id)) continue;
            }
            for (size_t j = 0; j < item_count_before_target; ++j) {
                const Item& right = items[j];
                if (right.kind == ItemKind::Node && horizon_node_ids.count(right.node_id)) continue;
                if (i == j || item_end_time(left) > item_start_time(right)) continue;
                auto next = build_arcs_between_items(left, right);
                if (left.kind == ItemKind::Group) {
                    std::vector<Arc> filtered;
                    for (const auto& arc : next) {
                        if (!horizon_tail_pilot_out_ids.count(groups[left.group_index].pilot_out) ||
                            !horizon_tail_pilot_out_ids.count(arc.from_node)) {
                            filtered.push_back(arc);
                        }
                    }
                    next.swap(filtered);
                }
                arcs.insert(arcs.end(), next.begin(), next.end());
            }
        }

        Node target;
        target.label = "target";
        target.event_kind = "Idle";
        target.start_port = "TARGET";
        target.end_port = "TARGET";
        target.event_start = data.planning_end;
        target.event_end = data.planning_end;
        target.node_in = data.planning_end;
        target.node_out = data.planning_end;
        int target_item = add_node_item(std::move(target));
        int target_node_id = items[target_item].node_id;

        std::set<int> target_source_node_ids;
        for (const auto& lane_entry : lane_items) {
            const auto& xs = lane_entry.second;
            if (xs.empty()) continue;
            const Item& last = items[xs.back()];
            if (last.kind == ItemKind::Node && nodes[last.node_id].is_horizon) {
                if (nodes[last.node_id].horizon_side != "end") throw std::runtime_error("invalid horizon at lane tail");
                add_arc(last.node_id, target_node_id, "Arc", 0.0, 0.0, true, 0.0);
                target_source_node_ids.insert(last.node_id);
                if (xs.size() >= 2 && items[xs[xs.size() - 2]].kind == ItemKind::Group) {
                    target_source_node_ids.insert(groups[items[xs[xs.size() - 2]].group_index].pilot_out);
                }
                continue;
            }
            if (last.kind != ItemKind::Group) throw std::runtime_error("invalid lane tail item");
            const Group& group = groups[last.group_index];
            auto candidates = outbound_nodes(group);
            if (candidates.empty()) throw std::runtime_error("unable to find last node candidate");
            int last_node = *std::max_element(candidates.begin(), candidates.end(), [&](int a, int b) {
                return nodes[a].node_out < nodes[b].node_out;
            });
            add_arc(last_node, target_node_id, "Arc", 0.0, 0.0, true, 0.0);
            target_source_node_ids.insert(last_node);
        }

        Node idle;
        idle.label = "idle";
        idle.event_kind = "Idle";
        idle.start_port = "IDLE";
        idle.end_port = "IDLE";
        idle.event_start = data.planning_end;
        idle.event_end = data.planning_end;
        idle.node_in = data.planning_end;
        idle.node_out = data.planning_end;
        int idle_item = add_node_item(std::move(idle));
        int idle_node_id = items[idle_item].node_id;

        for (size_t idx = 0; idx < items.size(); ++idx) {
            if (static_cast<int>(idx) == idle_item || static_cast<int>(idx) == target_item) continue;
            const Item& item = items[idx];
            if (item.kind == ItemKind::Group) {
                for (int node_id : outbound_nodes(groups[item.group_index])) {
                    if (!target_source_node_ids.count(node_id)) add_arc(node_id, idle_node_id, "Arc", 0.0, 0.0, true, 0.0);
                }
            } else {
                int node_id = item.node_id;
                if (horizon_node_ids.count(node_id)) continue;
                if (target_source_node_ids.count(node_id)) continue;
                if (nodes[node_id].event_kind == "Delivery" && committed_delivery_node_ids.count(node_id)) continue;
                add_arc(node_id, idle_node_id, "Arc", 0.0, 0.0, true, 0.0);
            }
        }
        add_arc(idle_node_id, target_node_id, "Arc", 0.0, 0.0, true, 0.0);
    }

    std::string fingerprint() const {
        std::vector<std::string> node_lines;
        for (const auto& node : nodes) {
            std::ostringstream ss;
            ss << "N" << node.id << "|" << node.label << "|" << node.event_kind << "|" << node.start_port << "|"
               << node.end_port << "|" << node.event_start << "|" << node.event_end << "|" << node.node_in << "|"
               << node.node_out;
            if (node.has_lane_key) ss << "|" << key_string(node.lane_key);
            if (!node.vessel_code.empty()) ss << "|" << node.vessel_code;
            if (node.is_horizon) ss << "|" << node.horizon_side;
            node_lines.push_back(ss.str());
        }
        std::sort(node_lines.begin(), node_lines.end());

        std::vector<std::string> edge_lines;
        for (const auto& arc : arcs) {
            std::ostringstream ss;
            ss << "A" << arc.arc_id << "|N" << arc.from_node << "->N" << arc.to_node << "|" << arc.type;
            if (arc.type == "CanalSailArc") ss << "|" << arc.canal_port_code << "|" << arc.canal_direction;
            edge_lines.push_back(ss.str());
        }
        std::sort(edge_lines.begin(), edge_lines.end());

        std::hash<std::string> hasher;
        size_t h = 1469598103934665603ull;
        auto mix = [&](const std::string& line) {
            h ^= hasher(line);
            h *= 1099511628211ull;
        };
        for (const auto& line : node_lines) mix(line);
        for (const auto& line : edge_lines) mix(line);
        std::ostringstream out;
        out << std::hex << h;
        return out.str();
    }

    std::string summary_json() const {
        int ps = 0, ss = 0, se = 0;
        for (const auto& group : groups) {
            if (group.kind == GroupKind::PS) ++ps;
            if (group.kind == GroupKind::SS) ++ss;
            if (group.kind == GroupKind::SE) ++se;
        }
        int d = 0, r = 0, idle = 0, target = 0, horizon_start = 0, horizon_end = 0;
        for (const auto& node : nodes) {
            if (node.event_kind == "Delivery") ++d;
            if (node.event_kind == "Redelivery") ++r;
            if (node.label == "idle") ++idle;
            if (node.label == "target") ++target;
            if (node.is_horizon && node.horizon_side == "start") ++horizon_start;
            if (node.is_horizon && node.horizon_side == "end") ++horizon_end;
        }
        int sail = 0, canal_sail = 0, horizon_sail = 0, canal_service = 0;
        for (const auto& arc : arcs) {
            if (arc.type == "SailArc") ++sail;
            if (arc.type == "CanalSailArc") ++canal_sail;
            if (arc.type == "HorizonSailArc") ++horizon_sail;
            const Node& from = nodes[arc.from_node];
            const Node& to = nodes[arc.to_node];
            if (from.label == "pilot_in" && to.label == "pilot_out" && from.owner_item == to.owner_item &&
                from.owner_item >= 0 && items[from.owner_item].kind == ItemKind::Group &&
                groups[items[from.owner_item].group_index].is_canal) {
                ++canal_service;
            }
        }
        std::ostringstream out;
        out << "{";
        out << "\"nodes\":" << nodes.size() << ",";
        out << "\"edges\":" << arcs.size() << ",";
        out << "\"network_items\":" << items.size() << ",";
        out << "\"node_groups\":" << groups.size() << ",";
        out << "\"PS\":" << ps << ",";
        out << "\"SS\":" << ss << ",";
        out << "\"SE\":" << se << ",";
        out << "\"D\":" << d << ",";
        out << "\"R\":" << r << ",";
        out << "\"I\":" << idle << ",";
        out << "\"T\":" << target << ",";
        out << "\"sail_arcs\":" << sail << ",";
        out << "\"canal_sail_arcs\":" << canal_sail << ",";
        out << "\"horizon_sail_arcs\":" << horizon_sail << ",";
        out << "\"canal_service_arcs\":" << canal_service << ",";
        out << "\"related_lane_keys\":" << data.positions.size() << ",";
        out << "\"horizon_start_sails\":" << horizon_start << ",";
        out << "\"horizon_end_sails\":" << horizon_end << ",";
        out << "\"fingerprint\":\"" << fingerprint() << "\"";
        out << "}";
        return out.str();
    }

    void write_network_tsv(const std::string& bundle_dir) const {
        auto open_out = [](const std::string& path) {
            std::ofstream out(path);
            if (!out) throw std::runtime_error("failed to open output " + path);
            return out;
        };
        auto bool_text = [](bool value) { return value ? "1" : "0"; };
        auto group_kind_text = [](GroupKind kind) {
            if (kind == GroupKind::SS) return std::string("SS");
            if (kind == GroupKind::SE) return std::string("SE");
            return std::string("PS");
        };

        {
            auto out = open_out(bundle_dir + "/cpp_nodes.tsv");
            out << "node_id\tlabel\tevent_kind\tvessel_code\towner_item\tlane_code\tproforma_name\tposition_no\t"
                   "start_port\tend_port\tstart_port_seq\tend_port_seq\tevent_start\tevent_end\tnode_in\tnode_out\t"
                   "is_horizon\thorizon_side\tdistance\n";
            out << std::setprecision(17);
            for (const auto& node : nodes) {
                out << node.id << '\t' << node.label << '\t' << node.event_kind << '\t' << node.vessel_code << '\t'
                    << node.owner_item << '\t' << (node.has_lane_key ? node.lane_key.lane_code : "") << '\t'
                    << (node.has_lane_key ? node.lane_key.proforma_name : "") << '\t'
                    << (node.has_lane_key ? std::to_string(node.lane_key.position_no) : "") << '\t' << node.start_port
                    << '\t' << node.end_port << '\t' << node.start_port_seq << '\t' << node.end_port_seq << '\t'
                    << node.event_start << '\t' << node.event_end << '\t' << node.node_in << '\t' << node.node_out
                    << '\t' << bool_text(node.is_horizon) << '\t' << node.horizon_side << '\t' << node.distance << '\n';
            }
        }

        {
            auto out = open_out(bundle_dir + "/cpp_groups.tsv");
            out << "group_id\tkind\tlane_code\tproforma_name\tposition_no\tport_code\tport_seq\tpilot_in_start\t"
                   "berthing_start\tberthing_end\tpilot_out_end\tdirection\tis_canal\tis_first\tis_last\tpilot_in\t"
                   "pilot_out\tss\tse\tts_in_before\tts_out_before\tts_in_after\tts_out_after\n";
            for (const auto& group : groups) {
                const auto& event = group.event;
                out << group.group_id << '\t' << group_kind_text(group.kind) << '\t' << event.lane_key.lane_code << '\t'
                    << event.lane_key.proforma_name << '\t' << event.lane_key.position_no << '\t' << event.port_code
                    << '\t' << event.port_seq << '\t' << event.pilot_in_start << '\t' << event.berthing_start << '\t'
                    << event.berthing_end << '\t' << event.pilot_out_end << '\t' << event.direction << '\t'
                    << bool_text(group.is_canal) << '\t' << bool_text(group.is_first) << '\t' << bool_text(group.is_last)
                    << '\t' << group.pilot_in << '\t' << group.pilot_out << '\t' << group.ss << '\t' << group.se << '\t'
                    << group.ts_in_before << '\t' << group.ts_out_before << '\t' << group.ts_in_after << '\t'
                    << group.ts_out_after << '\n';
            }
        }

        {
            auto out = open_out(bundle_dir + "/cpp_items.tsv");
            out << "item_index\tkind\tnode_id\tgroup_index\n";
            for (size_t index = 0; index < items.size(); ++index) {
                const auto& item = items[index];
                out << index << '\t' << (item.kind == ItemKind::Group ? "Group" : "Node") << '\t' << item.node_id << '\t'
                    << item.group_index << '\n';
            }
        }

        {
            auto out = open_out(bundle_dir + "/cpp_arcs.tsv");
            out << "arc_index\tfrom_node\tto_node\ttype\tdistance\tsail_time\thas_cost\tcost\t"
                   "canal_port_code\tcanal_direction\tcanal_leg1_distance\tcanal_leg1_eca_distance\t"
                   "canal_leg2_distance\tcanal_leg2_eca_distance\tcanal_passage_hours\n";
            out << std::setprecision(17);
            for (const auto& arc : arcs) {
                out << arc.arc_id << '\t' << arc.from_node << '\t' << arc.to_node << '\t' << arc.type << '\t'
                    << arc.distance << '\t' << arc.sail_time << '\t' << bool_text(arc.has_cost) << '\t' << arc.cost
                    << '\t' << arc.canal_port_code << '\t' << arc.canal_direction << '\t' << arc.canal_leg1_distance
                    << '\t' << arc.canal_leg1_eca_distance << '\t' << arc.canal_leg2_distance << '\t'
                    << arc.canal_leg2_eca_distance << '\t' << arc.canal_passage_hours << '\n';
            }
        }

        {
            auto out = open_out(bundle_dir + "/cpp_related_lane_keys.tsv");
            out << "lane_code\tproforma_name\tposition_no\n";
            for (const auto& key : data.positions) {
                out << key.lane_code << '\t' << key.proforma_name << '\t' << key.position_no << '\n';
            }
        }

        {
            auto out = open_out(bundle_dir + "/cpp_current_assignment_sources.tsv");
            out << "vessel_code\tnode_id\n";
            for (const auto& [vessel_code, node_id] : current_assignment_source_by_vessel()) {
                out << vessel_code << '\t' << node_id << '\n';
            }
        }
    }

    std::string run(const std::string& bundle_dir) {
        build_vessel_nodes();
        build_lane_nodes();
        build_arcs();
        write_network_tsv(bundle_dir);
        return summary_json();
    }
};

constexpr const char* VIRTUAL_VESSEL_CODE = "v_0";

struct ModelEdge {
    std::string edge_id;
    std::string from_node_id;
    std::string to_node_id;
    std::string arc_id;
    std::string arc_type;
    bool has_sail_cost = false;
    std::string sail_lane_code;
    std::string sail_year_month;
    std::string sail_from_port;
    std::string sail_to_port;
    double sail_distance = 0.0;
    double sail_time = 0.0;
    bool has_canal_route_cost = false;
    double canal_leg1_distance = 0.0;
    double canal_leg1_eca_distance = 0.0;
    double canal_leg2_distance = 0.0;
    double canal_leg2_eca_distance = 0.0;
    double canal_passage_hours = 0.0;
    int service_group_index = -1;
    bool has_canal_cost = false;
    std::string canal_port_code;
    std::string canal_direction;
    double virtual_opportunity_cost = 0.0;
    std::set<PositionKey> touched_position_keys;
};

struct TsUnit {
    std::string unit_id;
    std::string suffix;
    std::string tsi_node_id;
    std::string tso_node_id;
    double cost = 0.0;
    int owner_group_index = -1;
    std::string lane_code;
    std::string proforma_name;
    int position_no = 0;
    std::string port_code;
};

struct DdCostInfo {
    int coupling_index = 0;
    std::string before_vessel_code;
    std::string after_vessel_code;
    std::vector<std::string> dd_in_sail_edge_ids;
    std::map<std::string, std::vector<std::string>> out_edge_ids_by_lane_code;
};

struct PathEdge {
    std::string edge_id;
    std::string from_node_id;
    std::string to_node_id;
    std::string arc_id;
    std::string arc_type;
    double profit = 0.0;
    std::string canal_port_code;
    std::string canal_direction;
    double canal_leg1_distance = 0.0;
    double canal_leg1_eca_distance = 0.0;
    double canal_leg2_distance = 0.0;
    double canal_leg2_eca_distance = 0.0;
    double canal_passage_hours = 0.0;
    double canal_leg1_speed = 0.0;
    double canal_leg2_speed = 0.0;
    double canal_leg1_hours = 0.0;
    double canal_leg2_hours = 0.0;
};

struct CanalRouteChoice {
    double total_cost = 0.0;
    double leg1_speed = 0.0;
    double leg2_speed = 0.0;
    double leg1_hours = 0.0;
    double leg2_hours = 0.0;
};

struct SelectedPath {
    std::string vessel_code;
    std::string source_node_id;
    std::string sink_node_id;
    std::vector<std::string> node_path;
    std::vector<PathEdge> edge_path;
    double total_profit = 0.0;
    bool is_virtual = false;
};

std::string status_name(int status) {
    if (status == GRB_OPTIMAL) return "OPTIMAL";
    if (status == GRB_INFEASIBLE) return "INFEASIBLE";
    if (status == GRB_INF_OR_UNBD) return "INF_OR_UNBD";
    if (status == GRB_UNBOUNDED) return "UNBOUNDED";
    if (status == GRB_TIME_LIMIT) return "TIME_LIMIT";
    if (status == GRB_INTERRUPTED) return "INTERRUPTED";
    if (status == GRB_SUBOPTIMAL) return "SUBOPTIMAL";
    return std::to_string(status);
}

struct Optimizer {
    Builder& builder;
    Data& data;
    std::vector<std::string> vessel_codes;
    std::vector<std::string> model_vessel_codes;
    std::string target_node_id;
    std::string idle_node_id;
    std::string virtual_source_node_id = std::string("virtual_source:") + VIRTUAL_VESSEL_CODE;
    std::map<std::string, std::string> source_node_id_by_vessel;
    std::map<std::string, std::string> sink_node_id_by_vessel;
    std::map<std::string, int> capacity_by_vessel;
    std::map<std::string, int> reefer_by_vessel;
    std::map<std::string, std::set<std::pair<std::string, std::string>>> compatible_versions_by_vessel;
    std::map<std::string, ModelEdge> model_edge_by_id;
    std::map<std::string, ModelEdge> base_edge_by_id;
    std::map<std::string, std::vector<std::string>> base_outgoing_by_node;
    std::map<std::string, std::vector<std::string>> base_incoming_by_node;
    std::map<std::string, std::vector<std::string>> model_edge_ids_by_vessel;
    std::map<std::string, std::set<std::string>> model_edge_id_set_by_vessel;
    std::map<std::string, std::map<std::string, std::vector<std::string>>> model_outgoing_by_vessel_node;
    std::map<std::string, std::map<std::string, std::vector<std::string>>> model_incoming_by_vessel_node;
    std::map<std::string, std::set<std::string>> reachable_nodes_by_vessel;
    std::map<std::string, std::string> wrap_edge_ids_by_vessel;
    std::vector<TsUnit> ts_units;
    std::vector<PositionKey> position_keys;
    std::set<PositionKey> fixed_position_keys;
    std::set<PositionKey> output_declared_position_keys;
    std::map<std::pair<std::string, std::string>, std::vector<PositionKey>> position_keys_by_version;
    std::map<std::pair<std::string, std::string>, int> required_position_count_by_version;
    std::vector<DdCostInfo> dd_cost_infos;
    std::set<std::pair<std::string, std::string>> dd_skip_sail_cost_keys;
    std::map<std::pair<std::string, std::string>, double> dd_out_sail_cost_by_key;
    mutable std::map<std::tuple<int, std::string, std::string>, CanalRouteChoice> canal_bunker_choice_cache;

    explicit Optimizer(Builder& input_builder) : builder(input_builder), data(builder.data) {}

    static bool is_real_node_id(const std::string& id) {
        return id.size() > 1 && id[0] == 'N';
    }

    static int node_index(const std::string& id) {
        if (!is_real_node_id(id)) throw std::runtime_error("not a real node id: " + id);
        return std::stoi(id.substr(1));
    }

    const Node& node(const std::string& id) const {
        return builder.nodes.at(node_index(id));
    }

    int owner_group_index(int node_id) const {
        int owner_item = builder.nodes[node_id].owner_item;
        if (owner_item < 0) return -1;
        const Item& item = builder.items[owner_item];
        return item.kind == ItemKind::Group ? item.group_index : -1;
    }

    int owner_group_index(const std::string& node_id) const {
        if (!is_real_node_id(node_id)) return -1;
        return owner_group_index(node_index(node_id));
    }

    bool same_lane_position(const Group& left, const Group& right) const {
        return left.event.lane_key == right.event.lane_key;
    }

    bool same_lane_position(int left_group, int right_group) const {
        if (left_group < 0 || right_group < 0) return false;
        return same_lane_position(builder.groups[left_group], builder.groups[right_group]);
    }

    std::string lookup_lane_direction(const PositionKey& key, int port_seq) const {
        auto it = data.direction_by_lane_version_seq.find({key.lane_code, key.proforma_name, port_seq});
        if (it == data.direction_by_lane_version_seq.end()) {
            throw std::runtime_error("missing direction for " + key_string(key));
        }
        return it->second;
    }

    double opportunity_cost(const PositionKey& key, const std::string& direction) const {
        auto it = data.opportunity_cost_by_key.find({key.lane_code, key.proforma_name, direction});
        if (it == data.opportunity_cost_by_key.end()) {
            throw std::runtime_error("missing opportunity cost for " + key_string(key) + " direction=" + direction);
        }
        return it->second;
    }

    double opportunity_cost_for_group_interval(const Group& group, long long start, long long end) const {
        std::string direction = lookup_lane_direction(group.event.lane_key, group.event.port_seq);
        return opportunity_cost(group.event.lane_key, direction) * (static_cast<double>(end - start) / DAY);
    }

    double inlane_event_opportunity_cost(const Node& event_node) const {
        if (!event_node.has_lane_key || event_node.event_end <= event_node.event_start) return 0.0;
        int port_seq = event_node.event_kind == "InLaneSail" ? event_node.start_port_seq : event_node.start_port_seq;
        std::string direction = lookup_lane_direction(event_node.lane_key, port_seq);
        return opportunity_cost(event_node.lane_key, direction) *
               (static_cast<double>(event_node.event_end - event_node.event_start) / DAY);
    }

    int service_owner_for_edge(const std::string& from_node_id, const std::string& to_node_id) const {
        if (!is_real_node_id(from_node_id) || !is_real_node_id(to_node_id)) return -1;
        int from = node_index(from_node_id);
        int to = node_index(to_node_id);
        int owner = owner_group_index(from);
        if (owner >= 0 && owner == owner_group_index(to) && builder.nodes[from].label == "pilot_in" &&
            builder.nodes[to].label == "pilot_out") {
            return owner;
        }
        return -1;
    }

    void add_touched_position_keys(ModelEdge& edge) const {
        for (const std::string& node_id : {edge.from_node_id, edge.to_node_id}) {
            if (!is_real_node_id(node_id)) continue;
            const Node& next = node(node_id);
            int owner = owner_group_index(node_id);
            if (owner >= 0) {
                edge.touched_position_keys.insert(builder.groups[owner].event.lane_key);
            } else if (next.has_lane_key) {
                edge.touched_position_keys.insert(next.lane_key);
            }
        }
    }

    std::string edge_cost_lane_like_evaluation(const std::string& from_node_id, const std::string& to_node_id) const {
        int left_owner = owner_group_index(from_node_id);
        int right_owner = owner_group_index(to_node_id);
        if (left_owner >= 0 && is_real_node_id(to_node_id) && node(to_node_id).event_kind == "InLaneSail") {
            return node(to_node_id).lane_key.lane_code;
        }
        if (left_owner >= 0 && right_owner >= 0) {
            if (same_lane_position(left_owner, right_owner)) return builder.groups[left_owner].event.lane_key.lane_code;
            return builder.groups[right_owner].event.lane_key.lane_code;
        }
        if (right_owner >= 0) return builder.groups[right_owner].event.lane_key.lane_code;
        return "";
    }

    bool is_sequential_edge(const std::string& from_node_id, const std::string& to_node_id) const {
        int left_owner = owner_group_index(from_node_id);
        int right_owner = owner_group_index(to_node_id);
        if (left_owner < 0 || right_owner < 0) return false;
        if (left_owner == right_owner || !same_lane_position(left_owner, right_owner)) return false;
        std::string labels = node(from_node_id).label + "->" + node(to_node_id).label;
        return labels == "pilot_out->pilot_in" || labels == "pilot_out->ts_out_before" ||
               labels == "ts_in_after->pilot_in" || labels == "ts_in_after->ts_out_before";
    }

    bool is_v0_allowed_base_edge(const ModelEdge& edge) const {
        int from_owner = owner_group_index(edge.from_node_id);
        int to_owner = owner_group_index(edge.to_node_id);
        if (from_owner >= 0 && to_owner >= 0 && from_owner != to_owner && !same_lane_position(from_owner, to_owner)) {
            return false;
        }
        if (from_owner >= 0 && from_owner == to_owner) return true;
        return is_sequential_edge(edge.from_node_id, edge.to_node_id);
    }

    bool base_edge_allowed_for_vessel(const std::string& vessel_code, const ModelEdge& edge) const {
        if (vessel_code == VIRTUAL_VESSEL_CODE) return is_v0_allowed_base_edge(edge);
        if (!edge.has_canal_route_cost) return true;
        return has_feasible_canal_speed_pair(capacity_by_vessel.at(vessel_code), edge);
    }

    double virtual_opportunity_cost(const std::string& from_node_id, const std::string& to_node_id) const {
        if (!is_real_node_id(from_node_id) || !is_real_node_id(to_node_id)) return 0.0;
        const Node& from_node = node(from_node_id);
        const Node& to_node = node(to_node_id);
        int service_owner = service_owner_for_edge(from_node_id, to_node_id);
        if (service_owner >= 0) {
            return opportunity_cost_for_group_interval(builder.groups[service_owner], from_node.node_in, to_node.node_out);
        }
        int from_owner = owner_group_index(from_node_id);
        int to_owner = owner_group_index(to_node_id);
        if (from_owner >= 0 && to_node.event_kind == "InLaneSail") {
            return inlane_event_opportunity_cost(to_node);
        }
        if (from_owner >= 0 && from_owner == to_owner) {
            const Group& group = builder.groups[from_owner];
            std::string labels = from_node.label + "->" + to_node.label;
            if (labels == "ts_in_before->pilot_in") {
                return opportunity_cost_for_group_interval(group, from_node.node_in, to_node.node_in);
            }
            if (labels == "pilot_out->ts_out_after") {
                return opportunity_cost_for_group_interval(group, from_node.node_out, to_node.node_out);
            }
            return 0.0;
        }
        if (from_owner < 0 || to_owner < 0 || !same_lane_position(from_owner, to_owner)) return 0.0;
        const Group& from_group = builder.groups[from_owner];
        const Group& to_group = builder.groups[to_owner];
        std::string labels = from_node.label + "->" + to_node.label;
        if (labels == "pilot_out->pilot_in") {
            return opportunity_cost_for_group_interval(from_group, from_node.node_out, to_node.node_in);
        }
        if (labels == "pilot_out->ts_out_before") {
            return opportunity_cost_for_group_interval(from_group, from_node.node_out, to_node.node_in) +
                   opportunity_cost_for_group_interval(to_group, to_node.node_in, to_node.node_out);
        }
        if (labels == "ts_in_after->pilot_in") {
            return opportunity_cost_for_group_interval(from_group, from_node.node_in, from_node.node_out) +
                   opportunity_cost_for_group_interval(from_group, from_node.node_out, to_node.node_in);
        }
        if (labels == "ts_in_after->ts_out_before") {
            return opportunity_cost_for_group_interval(from_group, from_node.node_in, from_node.node_out) +
                   opportunity_cost_for_group_interval(from_group, from_node.node_out, to_node.node_in) +
                   opportunity_cost_for_group_interval(to_group, to_node.node_in, to_node.node_out);
        }
        return 0.0;
    }

    std::string canonical_canal_port(const std::string& port_code) const {
        return port_code == "EGSUZ" ? "EGSCA" : port_code;
    }

    bool service_canal_cost_key(int group_index, std::string& port_code, std::string& direction) const {
        const Group& group = builder.groups[group_index];
        if (!group.is_canal) return false;
        port_code = canonical_canal_port(group.event.port_code);
        direction = group.event.direction;
        const Version& version = builder.version_for(group.event.lane_key);
        int index = -1;
        for (size_t i = 0; i < version.rotations.size(); ++i) {
            if (version.rotations[i].port_seq == group.event.port_seq) {
                index = static_cast<int>(i);
                break;
            }
        }
        if (index >= 0 && !version.rotations.empty()) {
            const std::string& prev_port = version.rotations[(index + version.rotations.size() - 1) % version.rotations.size()].port_code;
            const std::string& next_port = version.rotations[(index + 1) % version.rotations.size()].port_code;
            auto it = data.canal_direction_by_key.find({prev_port, port_code, next_port});
            if (it != data.canal_direction_by_key.end()) direction = it->second;
        }
        if (direction.empty()) throw std::runtime_error("missing canal direction for " + group.event.port_code);
        return true;
    }

    bool adjacent_inlane_sail_for_canal(
        const std::string& edge_id, int service_group_index, const std::string& side
    ) const {
        const ModelEdge& edge = base_edge_by_id.at(edge_id);
        if (edge.arc_type != "SailArc" && edge.arc_type != "HorizonSailArc") return false;
        const PositionKey& lane_key = builder.groups[service_group_index].event.lane_key;
        int from_owner = owner_group_index(edge.from_node_id);
        int to_owner = owner_group_index(edge.to_node_id);
        if (side == "previous") {
            if (from_owner >= 0 && to_owner == service_group_index) {
                return builder.groups[from_owner].event.lane_key == lane_key && node(edge.from_node_id).label == "pilot_out";
            }
            if (is_real_node_id(edge.from_node_id)) {
                const Node& from_node = node(edge.from_node_id);
                return from_node.is_horizon && from_node.horizon_side == "start" && from_node.lane_key == lane_key;
            }
        } else {
            if (from_owner == service_group_index && to_owner >= 0) {
                return builder.groups[to_owner].event.lane_key == lane_key && node(edge.to_node_id).label == "pilot_in";
            }
            if (is_real_node_id(edge.to_node_id)) {
                const Node& to_node = node(edge.to_node_id);
                return to_node.is_horizon && to_node.horizon_side == "end" && to_node.lane_key == lane_key;
            }
        }
        return false;
    }

    std::pair<double, double> bunker_prices(const std::string& ym, const std::string& lane_code) const {
        auto lookup = [&](const std::string& type) {
            if (lane_code.empty()) return UNATTRIBUTED_BUNKER_PRICE_PER_TON;
            auto direct = data.bunker_price_by_key.find({ym, lane_code, type});
            if (direct == data.bunker_price_by_key.end()) {
                throw std::runtime_error(
                    "missing bunker price for year_month=" + ym + ", lane_code=" + lane_code +
                    ", bunker_type=" + type
                );
            }
            return direct->second;
        };
        return {lookup("LSFO"), lookup("MGO")};
    }

    double sail_cost_for_capacity(
        int capacity, const std::string& lane_code, const std::string& ym, const std::string& from_port,
        const std::string& to_port, double distance, double sail_time
    ) const {
        if (sail_time <= 0.0 || distance <= 0.0) throw std::runtime_error("invalid sail cost input");
        double avg_speed = distance / sail_time;
        double rounded_speed = std::min(20.0, std::max(14.0, std::ceil(avg_speed * 2.0) / 2.0));
        auto cap_it = data.bunker_sea_by_capacity.find(capacity);
        if (cap_it == data.bunker_sea_by_capacity.end() || !cap_it->second.count(rounded_speed)) {
            throw std::runtime_error("missing sea bunker consumption");
        }
        auto dist_it = data.distance_info_by_port_pair.find({from_port, to_port});
        if (dist_it == data.distance_info_by_port_pair.end()) throw std::runtime_error("missing distance info");
        double eca_rate = std::min(1.0, std::max(0.0, dist_it->second.eca_distance / distance));
        double sail_hours = distance / rounded_speed;
        double bunker_tons = (cap_it->second.at(rounded_speed) / 24.0) * sail_hours;
        auto [lsfo_price, mgo_price] = bunker_prices(ym, lane_code);
        return (bunker_tons * (1.0 - eca_rate) * lsfo_price) + (bunker_tons * eca_rate * mgo_price);
    }

    double canal_leg_bunker_cost_for_capacity(
        int capacity,
        const std::string& lane_code,
        const std::string& ym,
        double distance,
        double eca_distance,
        double speed
    ) const {
        if (distance <= 1e-9) return 0.0;
        auto cap_it = data.bunker_sea_by_capacity.find(capacity);
        if (cap_it == data.bunker_sea_by_capacity.end() || !cap_it->second.count(speed)) {
            throw std::runtime_error(
                "missing sea bunker consumption for capacity_teu=" + std::to_string(capacity) +
                ", speed=" + std::to_string(speed)
            );
        }
        double eca_rate = std::min(1.0, std::max(0.0, eca_distance / distance));
        double sail_hours = distance / speed;
        double bunker_tons = (cap_it->second.at(speed) / 24.0) * sail_hours;
        auto [lsfo_price, mgo_price] = bunker_prices(ym, lane_code);
        return (bunker_tons * (1.0 - eca_rate) * lsfo_price) + (bunker_tons * eca_rate * mgo_price);
    }

    double canal_passage_bunker_cost_for_capacity(
        int capacity,
        const std::string& lane_code,
        const std::string& ym,
        const std::string& canal_port_code,
        double passage_hours
    ) const {
        if (passage_hours <= 1e-9) return 0.0;
        auto cons_it = data.bunker_port_pilot_by_capacity.find(capacity);
        if (cons_it == data.bunker_port_pilot_by_capacity.end()) {
            throw std::runtime_error("missing port bunker consumption for capacity_teu=" + std::to_string(capacity));
        }
        double bunker_tons = cons_it->second * passage_hours;
        auto [lsfo_price, mgo_price] = bunker_prices(ym, lane_code);
        return bunker_tons * (data.eca_ports.count(canonical_canal_port(canal_port_code)) ? mgo_price : lsfo_price);
    }

    bool has_feasible_canal_speed_pair(int capacity, const ModelEdge& edge) const {
        auto cap_it = data.bunker_sea_by_capacity.find(capacity);
        if (cap_it == data.bunker_sea_by_capacity.end()) {
            throw std::runtime_error("missing sea bunker consumption for capacity_teu=" + std::to_string(capacity));
        }
        double max_speed = 0.0;
        for (const auto& speed_entry : cap_it->second) {
            if (speed_entry.first <= 20.0 + 1e-9 && speed_entry.first > max_speed) max_speed = speed_entry.first;
        }
        if (max_speed <= 0.0) {
            throw std::runtime_error("missing positive sea speed up to 20 knots for capacity_teu=" + std::to_string(capacity));
        }
        // The two canal sea legs use the same discrete speed set, capped at the model-wide 20 knot sailing limit;
        // the fastest allowed speed therefore gives the exact minimum possible duration for this pre-check.
        double min_hours =
            edge.canal_leg1_distance / max_speed + edge.canal_leg2_distance / max_speed + edge.canal_passage_hours;
        if (min_hours <= edge.sail_time + 1e-9) return true;
        return false;
    }

    CanalRouteChoice canal_bunker_choice_for_capacity(
        const ModelEdge& edge,
        int capacity,
        const std::string& lane_code
    ) const {
        auto cache_key = std::make_tuple(capacity, lane_code, edge.edge_id);
        auto cache_it = canal_bunker_choice_cache.find(cache_key);
        if (cache_it != canal_bunker_choice_cache.end()) return cache_it->second;

        auto cap_it = data.bunker_sea_by_capacity.find(capacity);
        if (cap_it == data.bunker_sea_by_capacity.end()) {
            throw std::runtime_error("missing sea bunker consumption for capacity_teu=" + std::to_string(capacity));
        }
        CanalRouteChoice best;
        best.total_cost = std::numeric_limits<double>::infinity();
        for (const auto& first_speed : cap_it->second) {
            double v1 = first_speed.first;
            if (v1 <= 0.0 || v1 > 20.0 + 1e-9) continue;
            double leg1_hours = edge.canal_leg1_distance / v1;
            double remaining_hours = edge.sail_time - edge.canal_passage_hours - leg1_hours;
            if (remaining_hours < -1e-9) continue;

            auto second_speed_it = cap_it->second.end();
            if (edge.canal_leg2_distance <= 1e-9) {
                second_speed_it = cap_it->second.upper_bound(0.0);
            } else {
                if (remaining_hours <= -1e-9) continue;
                double required_v2 = edge.canal_leg2_distance / (remaining_hours + 1e-9);
                second_speed_it = cap_it->second.lower_bound(required_v2);
                while (second_speed_it != cap_it->second.end() && second_speed_it->first <= 0.0) ++second_speed_it;
            }
            if (second_speed_it == cap_it->second.end()) continue;

            double v2 = second_speed_it->first;
            if (v2 > 20.0 + 1e-9) continue;
            double leg2_hours = edge.canal_leg2_distance <= 1e-9 ? 0.0 : edge.canal_leg2_distance / v2;
            double total_hours = leg1_hours + leg2_hours + edge.canal_passage_hours;
            if (total_hours > edge.sail_time + 1e-9) continue;
            // For a fixed first-leg speed, the second-leg bunker cost is minimized by the slowest discrete speed
            // that still satisfies the route time limit.
            double cost =
                canal_leg_bunker_cost_for_capacity(
                    capacity,
                    lane_code,
                    edge.sail_year_month,
                    edge.canal_leg1_distance,
                    edge.canal_leg1_eca_distance,
                    v1
                ) +
                canal_leg_bunker_cost_for_capacity(
                    capacity,
                    lane_code,
                    edge.sail_year_month,
                    edge.canal_leg2_distance,
                    edge.canal_leg2_eca_distance,
                    v2
                );
            if (cost < best.total_cost - 1e-9) {
                best.total_cost = cost;
                best.leg1_speed = v1;
                best.leg2_speed = v2;
                best.leg1_hours = leg1_hours;
                best.leg2_hours = leg2_hours;
            }
        }
        if (!std::isfinite(best.total_cost)) {
            throw std::runtime_error(
                "no feasible canal speed pair for edge_id=" + edge.edge_id +
                ", capacity_teu=" + std::to_string(capacity)
            );
        }
        best.total_cost += canal_passage_bunker_cost_for_capacity(
            capacity,
            lane_code,
            edge.sail_year_month,
            edge.canal_port_code,
            edge.canal_passage_hours
        );
        // The fee is vessel-specific but independent of speed, so capacity/lane/edge bunker cost is cached here.
        canal_bunker_choice_cache.emplace(cache_key, best);
        return best;
    }

    CanalRouteChoice canal_route_choice_for_vessel(
        const ModelEdge& edge,
        const std::string& vessel_code,
        const std::string& lane_code_override = ""
    ) const {
        int capacity = capacity_by_vessel.at(vessel_code);
        std::string lane_code = lane_code_override.empty() ? edge.sail_lane_code : lane_code_override;
        CanalRouteChoice choice = canal_bunker_choice_for_capacity(edge, capacity, lane_code);
        choice.total_cost += canal_cost_for_vessel(vessel_code, edge.canal_port_code, edge.canal_direction);
        return choice;
    }

    double service_cost_for_capacity(int capacity, int group_index) const {
        const Group& group = builder.groups[group_index];
        auto cons_it = data.bunker_port_pilot_by_capacity.find(capacity);
        if (cons_it == data.bunker_port_pilot_by_capacity.end()) {
            throw std::runtime_error("missing port bunker consumption");
        }
        double pilot_hours = static_cast<double>(group.event.berthing_start - group.event.pilot_in_start) / 3600.0;
        pilot_hours += static_cast<double>(group.event.pilot_out_end - group.event.berthing_end) / 3600.0;
        double bunker_tons = cons_it->second * pilot_hours;
        auto [lsfo_price, mgo_price] = bunker_prices(year_month(group.event.pilot_out_end), group.event.lane_key.lane_code);
        return bunker_tons * (data.eca_ports.count(group.event.port_code) ? mgo_price : lsfo_price);
    }

    double canal_cost_for_vessel(const std::string& vessel_code, const std::string& port_code, const std::string& direction) const {
        auto it = data.canal_fee_by_key.find({vessel_code, direction, canonical_canal_port(port_code)});
        if (it == data.canal_fee_by_key.end()) {
            throw std::runtime_error(
                "missing canal fee for vessel_code=" + vessel_code + ", direction=" + direction +
                ", port_code=" + canonical_canal_port(port_code)
            );
        }
        return it->second;
    }

    double edge_cost_for_vessel(const ModelEdge& edge, const std::string& vessel_code) const {
        if (vessel_code == VIRTUAL_VESSEL_CODE) return edge.virtual_opportunity_cost;
        int capacity = capacity_by_vessel.at(vessel_code);
        double cost = 0.0;
        if (edge.has_sail_cost) {
            cost += sail_cost_for_capacity(
                capacity,
                edge.sail_lane_code,
                edge.sail_year_month,
                edge.sail_from_port,
                edge.sail_to_port,
                edge.sail_distance,
                edge.sail_time
            );
        }
        if (edge.has_canal_route_cost) {
            cost += canal_route_choice_for_vessel(edge, vessel_code).total_cost;
        }
        if (edge.service_group_index >= 0) cost += service_cost_for_capacity(capacity, edge.service_group_index);
        if (edge.has_canal_cost) cost += canal_cost_for_vessel(vessel_code, edge.canal_port_code, edge.canal_direction);
        return cost;
    }

    double dd_adjusted_sail_cost(const std::string& edge_id, const std::string& vessel_code, const std::string& lane_code) const {
        const ModelEdge& edge = model_edge_by_id.at(edge_id);
        if (edge.has_canal_route_cost) {
            return canal_route_choice_for_vessel(edge, vessel_code, lane_code).total_cost;
        }
        return sail_cost_for_capacity(
            capacity_by_vessel.at(vessel_code),
            lane_code,
            edge.sail_year_month,
            edge.sail_from_port,
            edge.sail_to_port,
            edge.sail_distance,
            edge.sail_time
        );
    }

    void prepare_vessels() {
        for (const Vessel& vessel : data.vessels) {
            vessel_codes.push_back(vessel.vessel_code);
            capacity_by_vessel[vessel.vessel_code] = vessel.capacity_teu;
            reefer_by_vessel[vessel.vessel_code] = vessel.reefer_plug;
        }
        std::sort(vessel_codes.begin(), vessel_codes.end());
        for (const std::string& vessel_code : vessel_codes) compatible_versions_by_vessel[vessel_code] = {};
        for (const Version& version : data.versions) {
            for (const std::string& vessel_code : vessel_codes) {
                double capacity_tolerance = version.required_capacity_teu * CAPACITY_TOLERANCE;
                double reefer_tolerance = version.required_reefer_plug * CAPACITY_TOLERANCE + 1000000000.0;
                if (std::abs(capacity_by_vessel[vessel_code] - version.required_capacity_teu) > capacity_tolerance) continue;
                if (std::abs(reefer_by_vessel[vessel_code] - version.required_reefer_plug) > reefer_tolerance) continue;
                compatible_versions_by_vessel[vessel_code].insert({version.lane_code, version.proforma_name});
            }
        }
    }

    std::string find_unique_node(const std::string& label) const {
        std::vector<std::string> matches;
        for (const Node& next : builder.nodes) {
            if (next.label == label) matches.push_back(node_id_text(next.id));
        }
        if (matches.size() != 1) throw std::runtime_error("expected exactly one " + label + " node");
        return matches.front();
    }

    std::string source_node_for_vessel(const std::string& vessel_code, const std::map<std::string, int>& current_sources) const {
        std::vector<std::string> deliveries;
        for (const Node& next : builder.nodes) {
            if (next.event_kind == "Delivery" && next.vessel_code == vessel_code) deliveries.push_back(node_id_text(next.id));
        }
        if (deliveries.size() > 1) throw std::runtime_error("multiple delivery nodes for " + vessel_code);
        if (deliveries.size() == 1) return deliveries.front();
        auto it = current_sources.find(vessel_code);
        if (it != current_sources.end()) return node_id_text(it->second);
        throw std::runtime_error("unable to find source for " + vessel_code);
    }

    std::string sink_node_for_vessel(const std::string& vessel_code) const {
        std::vector<std::string> redeliveries;
        for (const Node& next : builder.nodes) {
            if (next.event_kind == "Redelivery" && next.vessel_code == vessel_code) redeliveries.push_back(node_id_text(next.id));
        }
        if (redeliveries.size() > 1) throw std::runtime_error("multiple redelivery nodes for " + vessel_code);
        if (redeliveries.size() == 1) return redeliveries.front();
        return target_node_id;
    }

    void prepare_sources() {
        target_node_id = find_unique_node("target");
        idle_node_id = find_unique_node("idle");
        auto current_sources = builder.current_assignment_source_by_vessel();
        for (const std::string& vessel_code : vessel_codes) {
            source_node_id_by_vessel[vessel_code] = source_node_for_vessel(vessel_code, current_sources);
            sink_node_id_by_vessel[vessel_code] = sink_node_for_vessel(vessel_code);
        }
    }

    void prepare_base_edges() {
        struct BaseEdgeBuildResult {
            bool keep = false;
            ModelEdge edge;
        };
        std::vector<BaseEdgeBuildResult> results(builder.arcs.size());
        size_t worker_count = std::min<size_t>(12, std::max<size_t>(1, builder.arcs.size()));
        std::vector<std::thread> workers;
        std::vector<std::exception_ptr> errors(worker_count);
        size_t chunk_size = (builder.arcs.size() + worker_count - 1) / worker_count;
        for (size_t worker_index = 0; worker_index < worker_count; ++worker_index) {
            size_t begin = worker_index * chunk_size;
            size_t end = std::min(builder.arcs.size(), begin + chunk_size);
            workers.emplace_back([&, worker_index, begin, end]() {
                try {
                    for (size_t arc_index = begin; arc_index < end; ++arc_index) {
                        const Arc& arc = builder.arcs[arc_index];
                        std::string from_id = node_id_text(arc.from_node);
                        std::string to_id = node_id_text(arc.to_node);
                        ModelEdge edge;
                        edge.edge_id = "base:" + arc_id_text(arc.arc_id);
                        edge.from_node_id = from_id;
                        edge.to_node_id = to_id;
                        edge.arc_id = arc_id_text(arc.arc_id);
                        edge.arc_type = arc.type;
                        if (arc.type == "SailArc" || arc.type == "HorizonSailArc" || arc.type == "CanalSailArc") {
                            const Node& from_node = builder.nodes[arc.from_node];
                            const Node& to_node = builder.nodes[arc.to_node];
                            if (arc.type == "HorizonSailArc" && from_node.is_horizon) {
                                edge.sail_from_port = from_node.start_port;
                                edge.sail_to_port = from_node.end_port;
                                edge.sail_year_month = year_month(from_node.node_out);
                            } else if (arc.type == "HorizonSailArc" && to_node.is_horizon) {
                                edge.sail_from_port = from_node.end_port;
                                edge.sail_to_port = to_node.end_port;
                                edge.sail_year_month = year_month(to_node.node_out);
                            } else {
                                edge.sail_from_port = from_node.end_port;
                                edge.sail_to_port = to_node.start_port;
                                edge.sail_year_month = year_month(to_node.node_in);
                            }
                            if (arc.type != "CanalSailArc") {
                                if (!data.distance_info_by_port_pair.count({edge.sail_from_port, edge.sail_to_port})) continue;
                                edge.has_sail_cost = true;
                            } else {
                                edge.has_canal_route_cost = true;
                                edge.canal_port_code = arc.canal_port_code;
                                edge.canal_direction = arc.canal_direction;
                                edge.canal_leg1_distance = arc.canal_leg1_distance;
                                edge.canal_leg1_eca_distance = arc.canal_leg1_eca_distance;
                                edge.canal_leg2_distance = arc.canal_leg2_distance;
                                edge.canal_leg2_eca_distance = arc.canal_leg2_eca_distance;
                                edge.canal_passage_hours = arc.canal_passage_hours;
                            }
                            edge.sail_lane_code = edge_cost_lane_like_evaluation(from_id, to_id);
                            edge.sail_distance = arc.distance;
                            edge.sail_time = arc.sail_time;
                        }
                        edge.service_group_index = service_owner_for_edge(from_id, to_id);
                        edge.virtual_opportunity_cost = virtual_opportunity_cost(from_id, to_id);
                        add_touched_position_keys(edge);
                        results[arc_index].keep = true;
                        results[arc_index].edge = std::move(edge);
                    }
                } catch (...) {
                    errors[worker_index] = std::current_exception();
                }
            });
        }
        for (std::thread& worker : workers) worker.join();
        for (const auto& error : errors) {
            if (error) std::rethrow_exception(error);
        }
        for (const BaseEdgeBuildResult& result : results) {
            if (!result.keep) continue;
            const ModelEdge& edge = result.edge;
            base_edge_by_id[edge.edge_id] = edge;
            model_edge_by_id[edge.edge_id] = edge;
            base_outgoing_by_node[edge.from_node_id].push_back(edge.edge_id);
            base_incoming_by_node[edge.to_node_id].push_back(edge.edge_id);
        }
    }

    void prepare_ts_units() {
        for (const Group& group : builder.groups) {
            auto add_unit = [&](const std::string& suffix, int tsi, int tso) {
                if (tsi < 0 || tso < 0) return;
                TsUnit unit;
                unit.unit_id = "G" + std::to_string(group.group_id) + ":" + suffix;
                unit.suffix = suffix;
                unit.tsi_node_id = node_id_text(tsi);
                unit.tso_node_id = node_id_text(tso);
                unit.owner_group_index = group.group_id;
                unit.lane_code = group.event.lane_key.lane_code;
                unit.proforma_name = group.event.lane_key.proforma_name;
                unit.position_no = group.event.lane_key.position_no;
                unit.port_code = group.event.port_code;
                auto it = data.transshipment_cost_by_key.find({year_month(builder.nodes[tso].node_in), unit.lane_code, unit.port_code});
                if (it == data.transshipment_cost_by_key.end()) {
                    throw std::runtime_error("missing transshipment cost");
                }
                unit.cost = it->second;
                ts_units.push_back(unit);
            };
            add_unit("before", group.ts_in_before, group.ts_out_before);
            add_unit("after", group.ts_in_after, group.ts_out_after);
        }
    }

    std::pair<std::set<std::string>, std::vector<std::string>> reachable_subgraph_for_vessel(
        const std::string& vessel_code,
        std::map<std::string, std::vector<std::string>>& outgoing_by_node,
        std::map<std::string, std::vector<std::string>>& incoming_by_node
    ) const {
        const std::string& source = source_node_id_by_vessel.at(vessel_code);
        const std::string& sink = sink_node_id_by_vessel.at(vessel_code);
        std::set<std::string> forward{source};
        std::vector<std::string> stack{source};
        while (!stack.empty()) {
            std::string current = stack.back();
            stack.pop_back();
            auto it = base_outgoing_by_node.find(current);
            if (it == base_outgoing_by_node.end()) continue;
            for (const std::string& edge_id : it->second) {
                const ModelEdge& edge = base_edge_by_id.at(edge_id);
                if (!base_edge_allowed_for_vessel(vessel_code, edge)) continue;
                std::string next = edge.to_node_id;
                if (forward.insert(next).second) stack.push_back(next);
            }
        }
        std::set<std::string> backward{sink};
        stack = {sink};
        while (!stack.empty()) {
            std::string current = stack.back();
            stack.pop_back();
            auto it = base_incoming_by_node.find(current);
            if (it == base_incoming_by_node.end()) continue;
            for (const std::string& edge_id : it->second) {
                const ModelEdge& edge = base_edge_by_id.at(edge_id);
                if (!base_edge_allowed_for_vessel(vessel_code, edge)) continue;
                std::string prev = edge.from_node_id;
                if (backward.insert(prev).second) stack.push_back(prev);
            }
        }
        std::set<std::string> relevant;
        for (const std::string& node_id : forward) {
            if (!backward.count(node_id)) continue;
            if (is_real_node_id(node_id)) {
                const Node& next = node(node_id);
                if ((next.event_kind == "Delivery" || next.event_kind == "Redelivery") && next.vessel_code != vessel_code) {
                    continue;
                }
                int owner = owner_group_index(node_id);
                std::pair<std::string, std::string> version_key;
                bool has_version = false;
                if (owner >= 0) {
                    version_key = {
                        builder.groups[owner].event.lane_key.lane_code,
                        builder.groups[owner].event.lane_key.proforma_name,
                    };
                    has_version = true;
                } else if (next.has_lane_key) {
                    version_key = {next.lane_key.lane_code, next.lane_key.proforma_name};
                    has_version = true;
                }
                if (has_version && !compatible_versions_by_vessel.at(vessel_code).count(version_key)) continue;
            }
            relevant.insert(node_id);
        }
        if (!relevant.count(source) || !relevant.count(sink)) {
            throw std::runtime_error("no source-to-sink subgraph for " + vessel_code);
        }
        std::vector<std::string> edge_ids;
        for (const std::string& node_id : relevant) {
            outgoing_by_node[node_id] = {};
            incoming_by_node[node_id] = {};
        }
        for (const std::string& node_id : relevant) {
            auto out_it = base_outgoing_by_node.find(node_id);
            if (out_it == base_outgoing_by_node.end()) continue;
            for (const std::string& edge_id : out_it->second) {
                const ModelEdge& edge = base_edge_by_id.at(edge_id);
                if (!base_edge_allowed_for_vessel(vessel_code, edge)) continue;
                if (!relevant.count(edge.to_node_id)) continue;
                edge_ids.push_back(edge_id);
                outgoing_by_node[node_id].push_back(edge_id);
                incoming_by_node[edge.to_node_id].push_back(edge_id);
            }
        }
        return {relevant, edge_ids};
    }

    void prepare_actual_model_edges() {
        struct VesselSubgraphResult {
            std::string vessel_code;
            std::set<std::string> nodes;
            std::vector<std::string> edge_ids;
            std::map<std::string, std::vector<std::string>> outgoing;
            std::map<std::string, std::vector<std::string>> incoming;
        };
        std::vector<VesselSubgraphResult> results(vessel_codes.size());
        size_t worker_count = std::min<size_t>(12, std::max<size_t>(1, vessel_codes.size()));
        std::vector<std::thread> workers;
        std::vector<std::exception_ptr> errors(worker_count);
        size_t chunk_size = (vessel_codes.size() + worker_count - 1) / worker_count;
        for (size_t worker_index = 0; worker_index < worker_count; ++worker_index) {
            size_t begin = worker_index * chunk_size;
            size_t end = std::min(vessel_codes.size(), begin + chunk_size);
            workers.emplace_back([&, worker_index, begin, end]() {
                try {
                    for (size_t vessel_index = begin; vessel_index < end; ++vessel_index) {
                        const std::string& vessel_code = vessel_codes[vessel_index];
                        std::map<std::string, std::vector<std::string>> outgoing;
                        std::map<std::string, std::vector<std::string>> incoming;
                        auto [nodes, edge_ids] = reachable_subgraph_for_vessel(vessel_code, outgoing, incoming);
                        results[vessel_index].vessel_code = vessel_code;
                        results[vessel_index].nodes = std::move(nodes);
                        results[vessel_index].edge_ids = std::move(edge_ids);
                        results[vessel_index].outgoing = std::move(outgoing);
                        results[vessel_index].incoming = std::move(incoming);
                    }
                } catch (...) {
                    errors[worker_index] = std::current_exception();
                }
            });
        }
        for (std::thread& worker : workers) worker.join();
        for (const auto& error : errors) {
            if (error) std::rethrow_exception(error);
        }

        for (VesselSubgraphResult& result : results) {
            const std::string& vessel_code = result.vessel_code;
            model_outgoing_by_vessel_node[vessel_code] = std::move(result.outgoing);
            model_incoming_by_vessel_node[vessel_code] = std::move(result.incoming);
            reachable_nodes_by_vessel[vessel_code] = std::move(result.nodes);
            model_edge_ids_by_vessel[vessel_code] = std::move(result.edge_ids);
            model_edge_id_set_by_vessel[vessel_code] =
                std::set<std::string>(model_edge_ids_by_vessel[vessel_code].begin(), model_edge_ids_by_vessel[vessel_code].end());
            auto& outgoing = model_outgoing_by_vessel_node[vessel_code];
            auto& incoming = model_incoming_by_vessel_node[vessel_code];
            std::string wrap_edge_id =
                "wrap:" + vessel_code + ":" + sink_node_id_by_vessel[vessel_code] + "->" + source_node_id_by_vessel[vessel_code];
            ModelEdge wrap;
            wrap.edge_id = wrap_edge_id;
            wrap.from_node_id = sink_node_id_by_vessel[vessel_code];
            wrap.to_node_id = source_node_id_by_vessel[vessel_code];
            wrap.arc_type = "WrapArc";
            add_touched_position_keys(wrap);
            model_edge_by_id[wrap_edge_id] = wrap;
            model_edge_ids_by_vessel[vessel_code].push_back(wrap_edge_id);
            model_edge_id_set_by_vessel[vessel_code].insert(wrap_edge_id);
            wrap_edge_ids_by_vessel[vessel_code] = wrap_edge_id;
            outgoing[wrap.from_node_id].push_back(wrap_edge_id);
            incoming[wrap.to_node_id].push_back(wrap_edge_id);
        }
    }

    void prepare_virtual_model_edges() {
        std::vector<std::string> virtual_edge_ids;
        std::set<std::string> virtual_node_ids{virtual_source_node_id, target_node_id};
        auto& outgoing = model_outgoing_by_vessel_node[VIRTUAL_VESSEL_CODE];
        auto& incoming = model_incoming_by_vessel_node[VIRTUAL_VESSEL_CODE];
        for (const auto& entry : base_edge_by_id) {
            if (!is_v0_allowed_base_edge(entry.second)) continue;
            virtual_edge_ids.push_back(entry.first);
            virtual_node_ids.insert(entry.second.from_node_id);
            virtual_node_ids.insert(entry.second.to_node_id);
            outgoing[entry.second.from_node_id].push_back(entry.first);
            incoming[entry.second.to_node_id].push_back(entry.first);
        }
        std::vector<int> group_indices(builder.groups.size());
        for (size_t i = 0; i < builder.groups.size(); ++i) group_indices[i] = static_cast<int>(i);
        std::sort(group_indices.begin(), group_indices.end(), [&](int a, int b) {
            return builder.groups[a].group_id < builder.groups[b].group_id;
        });
        for (int group_index : group_indices) {
            const Group& group = builder.groups[group_index];
            auto inbound = builder.inbound_nodes(group);
            std::set<int> entry_nodes(inbound.begin(), inbound.end());
            std::vector<int> exit_vector = {group.pilot_out};
            auto outbound = builder.outbound_nodes(group);
            exit_vector.insert(exit_vector.end(), outbound.begin(), outbound.end());
            std::set<int> exit_nodes(exit_vector.begin(), exit_vector.end());
            for (int node_id : entry_nodes) {
                std::string to_id = node_id_text(node_id);
                std::string edge_id = "v0_source:" + virtual_source_node_id + "->" + to_id;
                ModelEdge edge;
                edge.edge_id = edge_id;
                edge.from_node_id = virtual_source_node_id;
                edge.to_node_id = to_id;
                edge.arc_type = "VirtualSourceArc";
                add_touched_position_keys(edge);
                model_edge_by_id[edge_id] = edge;
                virtual_edge_ids.push_back(edge_id);
                virtual_node_ids.insert(to_id);
                outgoing[virtual_source_node_id].push_back(edge_id);
                incoming[to_id].push_back(edge_id);
            }
            for (int node_id : exit_nodes) {
                std::string from_id = node_id_text(node_id);
                std::string edge_id = "v0_target:" + from_id + "->" + target_node_id;
                ModelEdge edge;
                edge.edge_id = edge_id;
                edge.from_node_id = from_id;
                edge.to_node_id = target_node_id;
                edge.arc_type = "VirtualTargetArc";
                add_touched_position_keys(edge);
                model_edge_by_id[edge_id] = edge;
                virtual_edge_ids.push_back(edge_id);
                virtual_node_ids.insert(from_id);
                outgoing[from_id].push_back(edge_id);
                incoming[target_node_id].push_back(edge_id);
            }
        }
        std::string wrap_edge_id = std::string("wrap:") + VIRTUAL_VESSEL_CODE + ":" + target_node_id + "->" + virtual_source_node_id;
        ModelEdge wrap;
        wrap.edge_id = wrap_edge_id;
        wrap.from_node_id = target_node_id;
        wrap.to_node_id = virtual_source_node_id;
        wrap.arc_type = "WrapArc";
        add_touched_position_keys(wrap);
        model_edge_by_id[wrap_edge_id] = wrap;
        virtual_edge_ids.push_back(wrap_edge_id);
        wrap_edge_ids_by_vessel[VIRTUAL_VESSEL_CODE] = wrap_edge_id;
        outgoing[target_node_id].push_back(wrap_edge_id);
        incoming[virtual_source_node_id].push_back(wrap_edge_id);
        model_edge_ids_by_vessel[VIRTUAL_VESSEL_CODE] = virtual_edge_ids;
        model_edge_id_set_by_vessel[VIRTUAL_VESSEL_CODE] = std::set<std::string>(virtual_edge_ids.begin(), virtual_edge_ids.end());
        reachable_nodes_by_vessel[VIRTUAL_VESSEL_CODE] = virtual_node_ids;
    }

    void prepare_positions() {
        position_keys = data.positions;
        std::set<PositionKey> position_key_set(position_keys.begin(), position_keys.end());
        for (const Version& version : data.versions) {
            auto version_key = std::make_pair(version.lane_code, version.proforma_name);
            std::set<int> candidate_numbers;
            candidate_numbers.insert(version.declared_positions.begin(), version.declared_positions.end());
            candidate_numbers.insert(version.available_positions.begin(), version.available_positions.end());
            for (const auto& assignment : data.current_assignment_by_lane_key) {
                if (assignment.first.lane_code == version.lane_code && assignment.first.proforma_name == version.proforma_name) {
                    fixed_position_keys.insert(assignment.first);
                    candidate_numbers.insert(assignment.first.position_no);
                }
            }
            if (!version.declared_positions.empty()) {
                for (int position_no : version.declared_positions) {
                    fixed_position_keys.insert(PositionKey{version.lane_code, version.proforma_name, position_no});
                }
                required_position_count_by_version[version_key] = static_cast<int>(version.declared_positions.size());
            } else if (!version.available_positions.empty()) {
                required_position_count_by_version[version_key] = version.own_vessel_count;
            }
            for (int position_no : candidate_numbers) {
                PositionKey key{version.lane_code, version.proforma_name, position_no};
                if (!position_key_set.count(key)) continue;
                position_keys_by_version[version_key].push_back(key);
                if (version.available_positions.count(position_no)) output_declared_position_keys.insert(key);
            }
        }
    }

    std::string node_arrival_lane_code(const std::string& node_id) const {
        int owner = owner_group_index(node_id);
        if (owner >= 0) return builder.groups[owner].event.lane_key.lane_code;
        if (is_real_node_id(node_id) && node(node_id).is_horizon) return node(node_id).lane_key.lane_code;
        return "";
    }

    void prepare_dry_dock_costs() {
        for (const DdCoupling& coupling : data.dd_couplings) {
            if (!model_edge_ids_by_vessel.count(coupling.before_vessel_code) ||
                !model_edge_ids_by_vessel.count(coupling.after_vessel_code)) {
                continue;
            }
            DdCostInfo info;
            info.coupling_index = coupling.coupling_index;
            info.before_vessel_code = coupling.before_vessel_code;
            info.after_vessel_code = coupling.after_vessel_code;
            const std::string& before_sink = sink_node_id_by_vessel[coupling.before_vessel_code];
            const std::string& after_source = source_node_id_by_vessel[coupling.after_vessel_code];
            for (const std::string& edge_id : model_incoming_by_vessel_node[coupling.before_vessel_code][before_sink]) {
                const ModelEdge& edge = model_edge_by_id[edge_id];
                if (edge.arc_type == "SailArc" || edge.arc_type == "HorizonSailArc" || edge.arc_type == "CanalSailArc") {
                    info.dd_in_sail_edge_ids.push_back(edge_id);
                    dd_skip_sail_cost_keys.insert({coupling.before_vessel_code, edge_id});
                }
            }
            for (const std::string& edge_id : model_outgoing_by_vessel_node[coupling.after_vessel_code][after_source]) {
                const ModelEdge& edge = model_edge_by_id[edge_id];
                std::string lane_code = node_arrival_lane_code(edge.to_node_id);
                info.out_edge_ids_by_lane_code[lane_code].push_back(edge_id);
                if (edge.arc_type == "SailArc" || edge.arc_type == "HorizonSailArc" || edge.arc_type == "CanalSailArc") {
                    dd_skip_sail_cost_keys.insert({coupling.after_vessel_code, edge_id});
                    dd_out_sail_cost_by_key[{coupling.after_vessel_code, edge_id}] =
                        dd_adjusted_sail_cost(edge_id, coupling.after_vessel_code, lane_code);
                }
            }
            dd_cost_infos.push_back(info);
        }
    }

    void prepare_canal_contexts() {
        for (auto& entry : base_edge_by_id) {
            ModelEdge& edge = entry.second;
            if (edge.service_group_index < 0) continue;
            std::string port_code;
            std::string direction;
            if (!service_canal_cost_key(edge.service_group_index, port_code, direction)) continue;
            std::vector<std::string> previous_edges;
            for (const std::string& prev_edge_id : base_incoming_by_node[edge.from_node_id]) {
                if (adjacent_inlane_sail_for_canal(prev_edge_id, edge.service_group_index, "previous")) {
                    previous_edges.push_back(prev_edge_id);
                }
            }
            std::vector<std::string> next_edges;
            for (const std::string& next_edge_id : base_outgoing_by_node[edge.to_node_id]) {
                if (adjacent_inlane_sail_for_canal(next_edge_id, edge.service_group_index, "next")) {
                    next_edges.push_back(next_edge_id);
                }
            }
            if (previous_edges.empty() || next_edges.empty()) continue;
            edge.has_canal_cost = true;
            edge.canal_port_code = port_code;
            edge.canal_direction = direction;
            model_edge_by_id[entry.first] = edge;
        }
    }

    void prepare_model() {
        prepare_vessels();
        prepare_sources();
        prepare_base_edges();
        prepare_ts_units();
        prepare_actual_model_edges();
        prepare_virtual_model_edges();
        model_vessel_codes = vessel_codes;
        model_vessel_codes.push_back(VIRTUAL_VESSEL_CODE);
        prepare_positions();
        prepare_dry_dock_costs();
        prepare_canal_contexts();
    }

    SelectedPath build_selected_path(
        const std::string& vessel_code,
        const std::set<std::string>& selected_edge_ids
    ) const {
        std::map<std::string, std::string> outgoing_by_node;
        for (const std::string& edge_id : selected_edge_ids) {
            const ModelEdge& edge = model_edge_by_id.at(edge_id);
            if (outgoing_by_node.count(edge.from_node_id)) {
                throw std::runtime_error("multiple selected outgoing edges for " + vessel_code);
            }
            outgoing_by_node[edge.from_node_id] = edge_id;
        }
        SelectedPath path;
        path.vessel_code = vessel_code;
        path.source_node_id = source_node_id_by_vessel.at(vessel_code);
        path.sink_node_id = sink_node_id_by_vessel.at(vessel_code);
        path.node_path.push_back(path.source_node_id);
        std::set<std::string> visited{path.source_node_id};
        std::string current = path.source_node_id;
        while (current != path.sink_node_id) {
            auto it = outgoing_by_node.find(current);
            if (it == outgoing_by_node.end()) throw std::runtime_error("selected edges do not form a path for " + vessel_code);
            const ModelEdge& edge = model_edge_by_id.at(it->second);
            PathEdge path_edge;
            path_edge.edge_id = edge.edge_id;
            path_edge.from_node_id = edge.from_node_id;
            path_edge.to_node_id = edge.to_node_id;
            path_edge.arc_id = edge.arc_id;
            path_edge.arc_type = edge.arc_type;
            path_edge.profit = edge_cost_for_vessel(edge, vessel_code);
            if (edge.has_canal_route_cost) {
                CanalRouteChoice choice = canal_route_choice_for_vessel(edge, vessel_code);
                path_edge.canal_port_code = edge.canal_port_code;
                path_edge.canal_direction = edge.canal_direction;
                path_edge.canal_leg1_distance = edge.canal_leg1_distance;
                path_edge.canal_leg1_eca_distance = edge.canal_leg1_eca_distance;
                path_edge.canal_leg2_distance = edge.canal_leg2_distance;
                path_edge.canal_leg2_eca_distance = edge.canal_leg2_eca_distance;
                path_edge.canal_passage_hours = edge.canal_passage_hours;
                path_edge.canal_leg1_speed = choice.leg1_speed;
                path_edge.canal_leg2_speed = choice.leg2_speed;
                path_edge.canal_leg1_hours = choice.leg1_hours;
                path_edge.canal_leg2_hours = choice.leg2_hours;
            }
            path.edge_path.push_back(path_edge);
            path.total_profit += path_edge.profit;
            current = edge.to_node_id;
            if (visited.count(current) && current != path.sink_node_id) throw std::runtime_error("cycle in selected path");
            visited.insert(current);
            path.node_path.push_back(current);
        }
        return path;
    }

    std::vector<SelectedPath> build_virtual_selected_paths(const std::set<std::string>& selected_edge_ids) const {
        std::map<std::string, std::vector<std::string>> outgoing_by_node;
        for (const std::string& edge_id : selected_edge_ids) {
            const ModelEdge& edge = model_edge_by_id.at(edge_id);
            outgoing_by_node[edge.from_node_id].push_back(edge_id);
        }
        std::vector<SelectedPath> paths;
        auto starts = outgoing_by_node[virtual_source_node_id];
        std::sort(starts.begin(), starts.end());
        int path_index = 1;
        for (const std::string& first_edge_id : starts) {
            std::vector<std::string> full_nodes{virtual_source_node_id};
            std::vector<PathEdge> full_edges;
            std::set<std::string> visited{virtual_source_node_id};
            std::string current = virtual_source_node_id;
            while (current != target_node_id) {
                std::string edge_id;
                if (current == virtual_source_node_id) {
                    edge_id = first_edge_id;
                } else {
                    auto outgoing = outgoing_by_node[current];
                    if (outgoing.size() != 1) throw std::runtime_error("virtual selected edges are not disjoint paths");
                    edge_id = outgoing.front();
                }
                const ModelEdge& edge = model_edge_by_id.at(edge_id);
                PathEdge path_edge;
                path_edge.edge_id = edge.edge_id;
                path_edge.from_node_id = edge.from_node_id;
                path_edge.to_node_id = edge.to_node_id;
                path_edge.arc_id = edge.arc_id;
                path_edge.arc_type = edge.arc_type;
                path_edge.profit = edge.virtual_opportunity_cost;
                full_edges.push_back(path_edge);
                current = edge.to_node_id;
                if (visited.count(current) && current != target_node_id) throw std::runtime_error("cycle in virtual path");
                visited.insert(current);
                full_nodes.push_back(current);
            }
            SelectedPath path;
            std::ostringstream code;
            code << "VIRTUAL" << std::setw(3) << std::setfill('0') << path_index++;
            path.vessel_code = code.str();
            path.is_virtual = true;
            for (const std::string& node_id : full_nodes) {
                if (is_real_node_id(node_id)) path.node_path.push_back(node_id);
            }
            for (const PathEdge& edge : full_edges) {
                if (is_real_node_id(edge.from_node_id) && is_real_node_id(edge.to_node_id)) {
                    path.edge_path.push_back(edge);
                    path.total_profit += edge.profit;
                }
            }
            if (path.node_path.empty()) continue;
            path.source_node_id = path.node_path.front();
            path.sink_node_id = path.node_path.back();
            paths.push_back(path);
        }
        return paths;
    }

    void write_solution(
        const std::string& bundle_dir,
        double objective_value,
        double objective_bound,
        double mip_gap,
        int status,
        const std::vector<PositionKey>& declared_positions,
        const std::vector<SelectedPath>& paths
    ) const {
        auto open_out = [](const std::string& path) {
            std::ofstream out(path);
            if (!out) throw std::runtime_error("failed to open output " + path);
            return out;
        };
        auto bool_text = [](bool value) { return value ? "1" : "0"; };
        {
            auto out = open_out(bundle_dir + "/flow_meta.tsv");
            out << "key\tvalue\n";
            out << std::setprecision(17);
            out << "objective_value\t" << objective_value << "\n";
            out << "objective_bound\t" << objective_bound << "\n";
            out << "mip_gap\t" << mip_gap << "\n";
            out << "status\t" << status << "\n";
            out << "status_name\t" << status_name(status) << "\n";
        }
        {
            auto out = open_out(bundle_dir + "/flow_declared_positions.tsv");
            out << "lane_code\tproforma_name\tposition_no\n";
            for (const PositionKey& key : declared_positions) {
                out << key.lane_code << '\t' << key.proforma_name << '\t' << key.position_no << '\n';
            }
        }
        {
            auto out = open_out(bundle_dir + "/flow_paths.tsv");
            out << "path_index\tvessel_code\tsource_node_id\tsink_node_id\ttotal_profit\tis_virtual\n";
            out << std::setprecision(17);
            for (size_t i = 0; i < paths.size(); ++i) {
                const SelectedPath& path = paths[i];
                out << i << '\t' << path.vessel_code << '\t' << path.source_node_id << '\t' << path.sink_node_id << '\t'
                    << path.total_profit << '\t' << bool_text(path.is_virtual) << '\n';
            }
        }
        {
            auto out = open_out(bundle_dir + "/flow_path_nodes.tsv");
            out << "path_index\torder\tnode_id\n";
            for (size_t i = 0; i < paths.size(); ++i) {
                for (size_t j = 0; j < paths[i].node_path.size(); ++j) {
                    out << i << '\t' << j << '\t' << paths[i].node_path[j] << '\n';
                }
            }
        }
        {
            auto out = open_out(bundle_dir + "/flow_path_edges.tsv");
            out << "path_index\torder\tedge_id\tfrom_node_id\tto_node_id\tarc_id\tarc_type\tprofit\t"
                   "canal_port_code\tcanal_direction\tcanal_leg1_distance\tcanal_leg1_eca_distance\t"
                   "canal_leg2_distance\tcanal_leg2_eca_distance\tcanal_passage_hours\t"
                   "canal_leg1_speed\tcanal_leg2_speed\tcanal_leg1_hours\tcanal_leg2_hours\n";
            out << std::setprecision(17);
            for (size_t i = 0; i < paths.size(); ++i) {
                for (size_t j = 0; j < paths[i].edge_path.size(); ++j) {
                    const PathEdge& edge = paths[i].edge_path[j];
                    out << i << '\t' << j << '\t' << edge.edge_id << '\t' << edge.from_node_id << '\t'
                        << edge.to_node_id << '\t' << edge.arc_id << '\t' << edge.arc_type << '\t' << edge.profit
                        << '\t' << edge.canal_port_code << '\t' << edge.canal_direction << '\t'
                        << edge.canal_leg1_distance << '\t' << edge.canal_leg1_eca_distance << '\t'
                        << edge.canal_leg2_distance << '\t' << edge.canal_leg2_eca_distance << '\t'
                        << edge.canal_passage_hours << '\t' << edge.canal_leg1_speed << '\t' << edge.canal_leg2_speed
                        << '\t' << edge.canal_leg1_hours << '\t' << edge.canal_leg2_hours
                        << '\n';
                }
            }
        }
    }

    std::string solve(const std::string& bundle_dir) {
        prepare_model();
        GRBEnv env(true);
        env.set(GRB_IntParam_OutputFlag, 1);
        env.start();
        GRBModel model(env);
        model.set(GRB_StringAttr_ModelName, data.model_name);
        model.set(GRB_IntParam_LogToConsole, 1);
        model.set(GRB_IntParam_Threads, 12);

        std::map<std::pair<std::string, std::string>, GRBVar> x;
        for (const std::string& vessel_code : model_vessel_codes) {
            for (const std::string& edge_id : model_edge_ids_by_vessel[vessel_code]) {
                char vtype = GRB_BINARY;
                double ub = 1.0;
                if (vessel_code == VIRTUAL_VESSEL_CODE && edge_id == wrap_edge_ids_by_vessel[VIRTUAL_VESSEL_CODE]) {
                    vtype = GRB_CONTINUOUS;
                    ub = GRB_INFINITY;
                }
                x.emplace(
                    std::make_pair(vessel_code, edge_id),
                    model.addVar(0.0, ub, 0.0, vtype, "x[" + vessel_code + "," + edge_id + "]")
                );
            }
        }
        std::map<std::string, GRBVar> y;
        for (const TsUnit& unit : ts_units) {
            y.emplace(unit.unit_id, model.addVar(0.0, 1.0, 0.0, GRB_BINARY, "y[" + unit.unit_id + "]"));
        }
        std::map<PositionKey, GRBVar> position_active;
        for (const PositionKey& key : position_keys) {
            double lb = fixed_position_keys.count(key) ? 1.0 : 0.0;
            position_active.emplace(
                key,
                model.addVar(
                    lb,
                    1.0,
                    0.0,
                    GRB_BINARY,
                    "p[" + key.lane_code + "," + key.proforma_name + "," + std::to_string(key.position_no) + "]"
                )
            );
        }
        std::map<std::pair<int, std::string>, GRBVar> dd_category_active;
        std::map<std::tuple<int, std::string, std::string>, GRBVar> dd_in_cost_active;
        for (const DdCostInfo& info : dd_cost_infos) {
            for (const auto& lane_entry : info.out_edge_ids_by_lane_code) {
                dd_category_active.emplace(
                    std::make_pair(info.coupling_index, lane_entry.first),
                    model.addVar(0.0, 1.0, 0.0, GRB_BINARY, "dd_q")
                );
                for (const std::string& edge_id : info.dd_in_sail_edge_ids) {
                    dd_in_cost_active.emplace(
                        std::make_tuple(info.coupling_index, edge_id, lane_entry.first),
                        model.addVar(0.0, 1.0, 0.0, GRB_BINARY, "dd_w")
                    );
                }
            }
        }

        GRBLinExpr objective = 0.0;
        for (const std::string& vessel_code : model_vessel_codes) {
            for (const std::string& edge_id : model_edge_ids_by_vessel[vessel_code]) {
                const ModelEdge& edge = model_edge_by_id.at(edge_id);
                double coeff = 0.0;
                if (vessel_code == VIRTUAL_VESSEL_CODE) {
                    coeff += edge_cost_for_vessel(edge, vessel_code);
                } else if (edge.has_sail_cost || edge.has_canal_route_cost || edge.service_group_index >= 0 || edge.has_canal_cost) {
                    if (!dd_skip_sail_cost_keys.count({vessel_code, edge_id})) {
                        coeff += edge_cost_for_vessel(edge, vessel_code);
                    } else {
                        if (edge.service_group_index >= 0) {
                            coeff += service_cost_for_capacity(capacity_by_vessel.at(vessel_code), edge.service_group_index);
                        }
                        if (edge.has_canal_cost) {
                            coeff += canal_cost_for_vessel(vessel_code, edge.canal_port_code, edge.canal_direction);
                        }
                        auto out_it = dd_out_sail_cost_by_key.find({vessel_code, edge_id});
                        if (out_it != dd_out_sail_cost_by_key.end()) coeff += out_it->second;
                    }
                }
                if (coeff != 0.0) objective += coeff * x.at({vessel_code, edge_id});
            }
        }
        for (const DdCostInfo& info : dd_cost_infos) {
            for (const std::string& edge_id : info.dd_in_sail_edge_ids) {
                for (const auto& lane_entry : info.out_edge_ids_by_lane_code) {
                    double coeff = dd_adjusted_sail_cost(edge_id, info.before_vessel_code, lane_entry.first);
                    if (coeff != 0.0) {
                        objective += coeff * dd_in_cost_active.at({info.coupling_index, edge_id, lane_entry.first});
                    }
                }
            }
        }
        for (const TsUnit& unit : ts_units) objective += unit.cost * y.at(unit.unit_id);
        model.setObjective(objective, GRB_MINIMIZE);

        for (const std::string& vessel_code : vessel_codes) {
            GRBLinExpr expr = 0.0;
            for (const std::string& edge_id : model_outgoing_by_vessel_node[vessel_code][source_node_id_by_vessel[vessel_code]]) {
                expr += x.at({vessel_code, edge_id});
            }
            model.addConstr(expr == 1.0, "source_out[" + vessel_code + "]");
        }
        for (const DdCostInfo& info : dd_cost_infos) {
            GRBLinExpr q_sum = 0.0;
            for (const auto& lane_entry : info.out_edge_ids_by_lane_code) {
                GRBVar q = dd_category_active.at({info.coupling_index, lane_entry.first});
                q_sum += q;
                GRBLinExpr category_expr = q;
                for (const std::string& edge_id : lane_entry.second) {
                    category_expr -= x.at({info.after_vessel_code, edge_id});
                }
                model.addConstr(category_expr == 0.0, "dd_out_category");
                for (const std::string& edge_id : info.dd_in_sail_edge_ids) {
                    GRBVar xv = x.at({info.before_vessel_code, edge_id});
                    GRBVar w = dd_in_cost_active.at({info.coupling_index, edge_id, lane_entry.first});
                    model.addConstr(w - xv <= 0.0, "dd_w_le_x");
                    model.addConstr(w - q <= 0.0, "dd_w_le_q");
                    model.addConstr(w - xv - q >= -1.0, "dd_w_ge");
                }
            }
            if (!info.out_edge_ids_by_lane_code.empty()) model.addConstr(q_sum == 1.0, "dd_out_category_one");
        }
        for (const std::string& vessel_code : model_vessel_codes) {
            for (const std::string& node_id : reachable_nodes_by_vessel[vessel_code]) {
                GRBLinExpr expr = 0.0;
                for (const std::string& edge_id : model_incoming_by_vessel_node[vessel_code][node_id]) {
                    expr += x.at({vessel_code, edge_id});
                }
                for (const std::string& edge_id : model_outgoing_by_vessel_node[vessel_code][node_id]) {
                    expr -= x.at({vessel_code, edge_id});
                }
                model.addConstr(expr == 0.0, "flow");
            }
        }
        int service_cover_index = 0;
        for (const auto& entry : base_edge_by_id) {
            const ModelEdge& edge = entry.second;
            if (edge.service_group_index < 0) continue;
            const PositionKey& key = builder.groups[edge.service_group_index].event.lane_key;
            GRBLinExpr expr = 0.0;
            int count = 0;
            for (const std::string& vessel_code : model_vessel_codes) {
                if (!model_edge_id_set_by_vessel[vessel_code].count(edge.edge_id)) continue;
                expr += x.at({vessel_code, edge.edge_id});
                ++count;
            }
            if (count == 0) throw std::runtime_error("no service coverage variable");
            expr -= position_active.at(key);
            model.addConstr(expr == 0.0, "service_cover[" + std::to_string(service_cover_index++) + "]");
        }
        for (const auto& entry : position_keys_by_version) {
            auto count_it = required_position_count_by_version.find(entry.first);
            if (count_it == required_position_count_by_version.end()) continue;
            GRBLinExpr expr = 0.0;
            for (const PositionKey& key : entry.second) expr += position_active.at(key);
            model.addConstr(expr == static_cast<double>(count_it->second), "position_count");
        }
        std::map<PositionKey, std::vector<GRBVar>> activation_vars_by_position;
        for (const auto& entry : model_edge_by_id) {
            const ModelEdge& edge = entry.second;
            if (edge.touched_position_keys.empty()) continue;
            std::vector<GRBVar> edge_vars;
            for (const std::string& vessel_code : model_vessel_codes) {
                if (model_edge_id_set_by_vessel[vessel_code].count(edge.edge_id)) edge_vars.push_back(x.at({vessel_code, edge.edge_id}));
            }
            if (edge_vars.empty()) continue;
            for (const PositionKey& key : edge.touched_position_keys) {
                auto& vars = activation_vars_by_position[key];
                vars.insert(vars.end(), edge_vars.begin(), edge_vars.end());
            }
        }
        for (const auto& entry : activation_vars_by_position) {
            GRBLinExpr expr = 0.0;
            for (const GRBVar& var : entry.second) expr += var;
            expr -= static_cast<double>(entry.second.size()) * position_active.at(entry.first);
            model.addConstr(expr <= 0.0, "position_edge_activation");
        }
        std::set<std::string> wrap_edge_ids;
        for (const auto& entry : wrap_edge_ids_by_vessel) wrap_edge_ids.insert(entry.second);
        for (const auto& entry : model_edge_by_id) {
            const ModelEdge& edge = entry.second;
            if (wrap_edge_ids.count(edge.edge_id)) continue;
            if (edge.from_node_id == idle_node_id && edge.to_node_id == target_node_id) continue;
            GRBLinExpr expr = 0.0;
            int count = 0;
            for (const std::string& vessel_code : model_vessel_codes) {
                if (!model_edge_id_set_by_vessel[vessel_code].count(edge.edge_id)) continue;
                expr += x.at({vessel_code, edge.edge_id});
                ++count;
            }
            if (count > 1) model.addConstr(expr <= 1.0, "arc_capacity");
        }
        for (const TsUnit& unit : ts_units) {
            GRBLinExpr incoming = 0.0;
            GRBLinExpr outgoing = 0.0;
            for (const std::string& vessel_code : model_vessel_codes) {
                for (const std::string& edge_id : model_incoming_by_vessel_node[vessel_code][unit.tsi_node_id]) {
                    if (model_edge_id_set_by_vessel[vessel_code].count(edge_id)) incoming += x.at({vessel_code, edge_id});
                }
                for (const std::string& edge_id : model_outgoing_by_vessel_node[vessel_code][unit.tso_node_id]) {
                    if (model_edge_id_set_by_vessel[vessel_code].count(edge_id)) outgoing += x.at({vessel_code, edge_id});
                }
            }
            model.addConstr(2.0 * y.at(unit.unit_id) - incoming - outgoing >= 0.0, "ts_activation");
            model.addConstr(outgoing - incoming == 0.0, "ts_balance");
        }

        model.set(GRB_DoubleParam_MIPGap, 0.0);
        model.optimize();
        int status = model.get(GRB_IntAttr_Status);
        int sol_count = model.get(GRB_IntAttr_SolCount);
        if (status != GRB_OPTIMAL || sol_count <= 0) {
            throw std::runtime_error("optimization did not finish optimally. status=" + std::to_string(status));
        }

        std::vector<PositionKey> declared_positions;
        for (const PositionKey& key : position_keys) {
            if (!output_declared_position_keys.count(key)) continue;
            if (position_active.at(key).get(GRB_DoubleAttr_X) > 0.5) declared_positions.push_back(key);
        }
        std::map<std::string, std::set<std::string>> selected_edge_ids_by_vessel;
        for (const std::string& vessel_code : model_vessel_codes) {
            selected_edge_ids_by_vessel[vessel_code] = {};
            for (const std::string& edge_id : model_edge_ids_by_vessel[vessel_code]) {
                double value = x.at({vessel_code, edge_id}).get(GRB_DoubleAttr_X);
                if (value <= 0.5 || edge_id.rfind("wrap:", 0) == 0) continue;
                selected_edge_ids_by_vessel[vessel_code].insert(edge_id);
            }
        }
        std::vector<SelectedPath> paths;
        for (const std::string& vessel_code : vessel_codes) {
            paths.push_back(build_selected_path(vessel_code, selected_edge_ids_by_vessel[vessel_code]));
        }
        auto virtual_paths = build_virtual_selected_paths(selected_edge_ids_by_vessel[VIRTUAL_VESSEL_CODE]);
        paths.insert(paths.end(), virtual_paths.begin(), virtual_paths.end());

        double objective_value = model.get(GRB_DoubleAttr_ObjVal);
        double objective_bound = model.get(GRB_DoubleAttr_ObjBound);
        double mip_gap = model.get(GRB_DoubleAttr_MIPGap);
        write_solution(bundle_dir, objective_value, objective_bound, mip_gap, status, declared_positions, paths);
        std::ostringstream summary;
        summary << "{\"status\":" << status << ",\"objective_value\":" << std::setprecision(17) << objective_value
                << ",\"paths\":" << paths.size() << "}";
        return summary.str();
    }
};

Data load_data(const std::string& bundle_dir) {
    Data data;
    auto meta = read_tsv(bundle_dir + "/meta.tsv");
    for (const auto& row : meta) {
        if (row.size() < 2) continue;
        if (row[0] == "planning_start") data.planning_start = to_i64(row[1]);
        if (row[0] == "planning_end") data.planning_end = to_i64(row[1]);
        if (row[0] == "model_name") data.model_name = row[1];
    }

    for (const auto& row : read_tsv(bundle_dir + "/versions.tsv")) {
        if (row.size() < 8) throw std::runtime_error("bad versions.tsv row");
        Version version;
        version.lane_code = row[0];
        version.proforma_name = row[1];
        version.service_duration_days = to_f64(row[2]);
        version.anchor_time = to_i64(row[3]);
        version.has_effective_to = !row[4].empty();
        version.effective_to = to_i64(row[4]);
        version.required_capacity_teu = to_f64(row[5]);
        version.required_reefer_plug = to_f64(row[6]);
        version.own_vessel_count = static_cast<int>(to_i64(row[7]));
        data.version_index[{version.lane_code, version.proforma_name}] = static_cast<int>(data.versions.size());
        double tolerance = version.required_capacity_teu * CAPACITY_TOLERANCE;
        data.capacity_interval_by_version[{version.lane_code, version.proforma_name}] = {
            version.required_capacity_teu - tolerance,
            version.required_capacity_teu + tolerance,
        };
        data.versions.push_back(std::move(version));
    }

    for (const auto& row : read_tsv(bundle_dir + "/rotations.tsv")) {
        if (row.size() < 9) throw std::runtime_error("bad rotations.tsv row");
        auto it = data.version_index.find({row[0], row[1]});
        if (it == data.version_index.end()) throw std::runtime_error("rotation references unknown version");
        Rotation rotation;
        rotation.port_code = row[3];
        rotation.port_seq = static_cast<int>(to_i64(row[4]));
        rotation.eta_offset_minutes = to_i64(row[5]);
        rotation.etb_offset_minutes = to_i64(row[6]);
        rotation.etd_offset_minutes = to_i64(row[7]);
        rotation.pilot_out_minutes = to_i64(row[8]);
        rotation.direction = row.size() > 9 ? row[9] : "";
        data.versions[it->second].rotations.push_back(std::move(rotation));
    }

    for (const auto& row : read_tsv(bundle_dir + "/version_positions.tsv")) {
        if (row.size() < 5) throw std::runtime_error("bad version_positions.tsv row");
        auto it = data.version_index.find({row[0], row[1]});
        if (it == data.version_index.end()) throw std::runtime_error("version position references unknown version");
        int position_no = static_cast<int>(to_i64(row[2]));
        if (row[3] == "1") data.versions[it->second].declared_positions.insert(position_no);
        if (row[4] == "1") data.versions[it->second].available_positions.insert(position_no);
    }

    for (const Version& version : data.versions) {
        for (const Rotation& rotation : version.rotations) {
            data.direction_by_lane_version_seq[{version.lane_code, version.proforma_name, rotation.port_seq}] =
                rotation.direction;
        }
    }

    for (const auto& row : read_tsv(bundle_dir + "/vessels.tsv")) {
        if (row.size() < 7) throw std::runtime_error("bad vessels.tsv row");
        Vessel vessel;
        vessel.vessel_code = row[0];
        vessel.capacity_teu = static_cast<int>(to_i64(row[1]));
        vessel.reefer_plug = static_cast<int>(to_i64(row[2]));
        vessel.has_available_from = !row[3].empty();
        vessel.available_from = to_i64(row[3]);
        vessel.available_from_port = row[4];
        vessel.has_available_to = !row[5].empty();
        vessel.available_to = to_i64(row[5]);
        vessel.available_to_port = row[6];
        data.vessels.push_back(std::move(vessel));
    }

    for (const auto& row : read_tsv(bundle_dir + "/positions.tsv")) {
        if (row.size() < 3) throw std::runtime_error("bad positions.tsv row");
        data.positions.push_back(PositionKey{row[0], row[1], static_cast<int>(to_i64(row[2]))});
    }
    std::sort(data.positions.begin(), data.positions.end());

    for (const auto& row : read_tsv(bundle_dir + "/assignments.tsv")) {
        if (row.size() < 4) throw std::runtime_error("bad assignments.tsv row");
        PositionKey key{row[0], row[1], static_cast<int>(to_i64(row[2]))};
        if (std::binary_search(data.positions.begin(), data.positions.end(), key)) {
            data.current_assignment_by_lane_key[key] = row[3];
        }
    }

    for (const auto& row : read_tsv(bundle_dir + "/distances.tsv")) {
        if (row.size() < 4) throw std::runtime_error("bad distances.tsv row");
        double distance = to_f64(row[2]);
        double eca_distance = to_f64(row[3]);
        data.distance_matrix[{row[0], row[1]}] = distance;
        data.distance_info_by_port_pair[{row[0], row[1]}] = DistanceInfo{distance, eca_distance};
        data.distance_adjacency[row[0]].push_back({row[1], distance});
        data.distance_matrix[{canonical_canal_port(row[0]), canonical_canal_port(row[1])}] = distance;
        data.distance_info_by_port_pair[{canonical_canal_port(row[0]), canonical_canal_port(row[1])}] =
            DistanceInfo{distance, eca_distance};
        data.distance_adjacency[canonical_canal_port(row[0])].push_back({canonical_canal_port(row[1]), distance});
    }

    for (const auto& row : read_tsv(bundle_dir + "/eca_ports.tsv")) {
        if (!row.empty()) data.eca_ports.insert(row[0]);
    }

    for (const auto& row : read_tsv(bundle_dir + "/bunker_consumption_sea.tsv")) {
        if (row.size() < 3) throw std::runtime_error("bad bunker_consumption_sea.tsv row");
        data.bunker_sea_by_capacity[static_cast<int>(to_i64(row[0]))][to_f64(row[1])] = to_f64(row[2]);
    }

    for (const auto& row : read_tsv(bundle_dir + "/bunker_consumption_port.tsv")) {
        if (row.size() < 2) throw std::runtime_error("bad bunker_consumption_port.tsv row");
        data.bunker_port_pilot_by_capacity[static_cast<int>(to_i64(row[0]))] = to_f64(row[1]);
    }

    for (const auto& row : read_tsv(bundle_dir + "/bunker_price.tsv")) {
        if (row.size() < 4) throw std::runtime_error("bad bunker_price.tsv row");
        double price = to_f64(row[3]);
        data.bunker_price_by_key[{row[0], row[1], row[2]}] = price;
    }

    for (const auto& row : read_tsv(bundle_dir + "/transshipment_cost.tsv")) {
        if (row.size() < 4) throw std::runtime_error("bad transshipment_cost.tsv row");
        data.transshipment_cost_by_key[{row[0], row[1], row[2]}] = to_f64(row[3]);
    }

    for (const auto& row : read_tsv(bundle_dir + "/canal_fee.tsv")) {
        if (row.size() < 4) throw std::runtime_error("bad canal_fee.tsv row");
        data.canal_fee_by_key[{row[0], row[1], canonical_canal_port(row[2])}] = to_f64(row[3]);
    }

    for (const auto& row : read_tsv(bundle_dir + "/canal_passage_time.tsv")) {
        if (row.size() < 3) throw std::runtime_error("bad canal_passage_time.tsv row");
        data.canal_passage_hours_by_key[{canonical_canal_port(row[0]), row[1]}] = to_f64(row[2]);
    }

    for (const auto& row : read_tsv(bundle_dir + "/canal_direction.tsv")) {
        if (row.size() < 4) throw std::runtime_error("bad canal_direction.tsv row");
        data.canal_direction_by_key[
            {canonical_canal_port(row[0]), canonical_canal_port(row[1]), canonical_canal_port(row[2])}
        ] = row[3];
    }

    for (const auto& row : read_tsv(bundle_dir + "/opportunity_cost.tsv")) {
        if (row.size() < 4) throw std::runtime_error("bad opportunity_cost.tsv row");
        data.opportunity_cost_by_key[{row[0], row[1], row[2]}] = to_f64(row[3]);
    }

    for (const auto& row : read_tsv(bundle_dir + "/dd_couplings.tsv")) {
        if (row.size() < 4) throw std::runtime_error("bad dd_couplings.tsv row");
        data.dd_couplings.push_back(DdCoupling{static_cast<int>(to_i64(row[0])), row[1], row[2], row[3]});
    }

    return data;
}

void copy_result(const std::string& text, char* buffer, std::size_t capacity) {
    if (capacity == 0) return;
    std::size_t n = std::min(capacity - 1, text.size());
    std::memcpy(buffer, text.data(), n);
    buffer[n] = '\0';
}

}  // namespace

extern "C" int ocam_v5_construct_network(const char* bundle_dir, char* out, std::size_t out_cap, char* err, std::size_t err_cap) {
    try {
        std::string bundle_path(bundle_dir);
        Builder builder(load_data(bundle_path));
        copy_result(builder.run(bundle_path), out, out_cap);
        if (err_cap > 0) err[0] = '\0';
        return 0;
    } catch (const std::exception& exc) {
        copy_result(exc.what(), err, err_cap);
        if (out_cap > 0) out[0] = '\0';
        return 1;
    }
}

extern "C" int ocam_v5_solve_flow(const char* bundle_dir, char* out, std::size_t out_cap, char* err, std::size_t err_cap) {
    try {
        std::string bundle_path(bundle_dir);
        Builder builder(load_data(bundle_path));
        builder.build_vessel_nodes();
        builder.build_lane_nodes();
        builder.build_arcs();
        builder.write_network_tsv(bundle_path);
        Optimizer optimizer(builder);
        copy_result(optimizer.solve(bundle_path), out, out_cap);
        if (err_cap > 0) err[0] = '\0';
        return 0;
    } catch (const GRBException& exc) {
        copy_result(exc.getMessage(), err, err_cap);
        if (out_cap > 0) out[0] = '\0';
        return 1;
    } catch (const std::exception& exc) {
        copy_result(exc.what(), err, err_cap);
        if (out_cap > 0) out[0] = '\0';
        return 1;
    }
}

extern "C" int ocam_v5_gurobi_smoke(char* out, std::size_t out_cap, char* err, std::size_t err_cap) {
    try {
        GRBEnv env(true);
        env.set(GRB_IntParam_OutputFlag, 0);
        env.start();
        GRBModel model(env);
        GRBVar x = model.addVar(0.0, 1.0, 1.0, GRB_BINARY, "x");
        model.addConstr(x == 1.0, "fix_x");
        model.set(GRB_IntAttr_ModelSense, GRB_MINIMIZE);
        model.optimize();
        std::ostringstream ss;
        ss << "{\"status\":" << model.get(GRB_IntAttr_Status)
           << ",\"objective\":" << model.get(GRB_DoubleAttr_ObjVal)
           << ",\"x\":" << x.get(GRB_DoubleAttr_X) << "}";
        copy_result(ss.str(), out, out_cap);
        if (err_cap > 0) err[0] = '\0';
        return 0;
    } catch (const GRBException& exc) {
        copy_result(exc.getMessage(), err, err_cap);
        if (out_cap > 0) out[0] = '\0';
        return 1;
    } catch (const std::exception& exc) {
        copy_result(exc.what(), err, err_cap);
        if (out_cap > 0) out[0] = '\0';
        return 1;
    }
}

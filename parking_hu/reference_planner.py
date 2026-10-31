"""Replaceable integration reference for Liang Ke's rules and A* module.

Uses NetworkX's existing A* implementation rather than a custom search engine.
This module consumes perception only and has no access to scene truth.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from math import isfinite

import networkx as nx
from shapely.geometry import LineString, Point

from .geometry import TraversalGeometry, as_point, distance, path_length
from .map_data import validate_map


def failure(status: str, reason: str, message: str) -> dict:
    return {"status": status, "reason_code": reason, "message": message,
            "target_spot_id": None, "path_m": [], "distances_m": None, "routes": {}}


def _edge_id(a, b) -> str:
    ends = sorted((a, b))
    return "|".join(f"{p[0]:08.3f},{p[1]:08.3f}" for p in ends)


def _add_edge(graph, a, b):
    a, b = as_point(a), as_point(b)
    if a != b:
        graph.add_edge(a, b, weight=distance(a, b), edge_id=_edge_id(a, b))


def _chain(graph, points):
    for a, b in zip(points, points[1:]):
        _add_edge(graph, a, b)


def build_car_graph(map_data: dict) -> nx.DiGraph:
    """Directed perimeter, with no parking-space shortcuts."""
    graph = nx.DiGraph()
    for y, reverse in ((8.35, True), (23.65, False)):
        xs = {2.25, 45.75}
        xs.update(s["car_access_m"][0] for s in map_data["spots"] if s["car_access_m"][1] == y)
        _chain(graph, [(x, y) for x in sorted(xs, reverse=reverse)])
    _chain(graph, [(2.25, 8.35), (2.25, 16.0), (2.25, 23.65)])
    _chain(graph, [(45.75, 23.65), (45.75, 16.0), (45.75, 8.35)])
    _add_edge(graph, map_data["entry_m"], (2.25, 16.0))
    _add_edge(graph, (45.75, 16.0), map_data["exit_m"])
    return graph


def build_ped_graph(map_data: dict) -> nx.Graph:
    graph = nx.Graph()
    for y in (0.7, 16.0, 31.3):
        xs = {5.0, 42.6}
        xs.update(s["ped_access_m"][0] for s in map_data["spots"] if s["ped_access_m"][1] == y)
        _chain(graph, [(x, y) for x in sorted(xs)])
    _chain(graph, [(42.6, y) for y in sorted({0.7, 16.0, 31.3, map_data["destination_m"][1]})])
    return graph


def _filter_graph(graph, geometry):
    filtered = graph.copy()
    filtered.remove_nodes_from([n for n in filtered if not geometry.point_allowed(n)])
    filtered.remove_edges_from([(a, b) for a, b in filtered.edges if not geometry.segment_allowed(a, b)])
    return filtered


def _astar(graph, start, end, geometry):
    start, end = as_point(start), as_point(end)
    try:
        path = nx.astar_path(graph, start, end, heuristic=distance, weight="weight")
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None
    if not geometry.path_allowed(path):
        return None
    if any(not graph.has_edge(a, b) for a, b in zip(path, path[1:])):
        return None
    return [[float(x), float(y)] for x, y in path]


def _cm(value):
    return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _insert_pedestrian_start(graph, start, geometry):
    """Insert the unique deterministic nearest projection; does not search a route."""
    point = Point(start)
    projections = []
    for a, b, attr in graph.edges(data=True):
        line = LineString([a, b])
        projected = line.interpolate(line.project(point))
        projection = as_point((projected.x, projected.y))
        projections.append((distance(start, projection), attr["edge_id"], a, b, projection))
    nearest_distance = min(p[0] for p in projections)
    chosen = min((p for p in projections if abs(p[0] - nearest_distance) <= 1e-9), key=lambda p: p[1])
    delta, edge_id, a, b, projected = chosen
    if delta > 0.6 + 1e-9 or not geometry.segment_allowed(start, projected):
        raise ValueError("起点距最近人行边超过 0.6 米，或连接线穿过障碍。")
    if projected not in (a, b):
        graph.remove_edge(a, b)
        _add_edge(graph, a, projected)
        _add_edge(graph, projected, b)
    if start != projected:
        _add_edge(graph, start, projected)
    return delta, edge_id


def _snapshot_error(context):
    snapshot, map_data = context.get("snapshot", {}), context["map"]
    if (not snapshot.get("valid") or snapshot.get("map_id") != map_data["map_id"]
            or not isinstance(snapshot.get("frame_id"), str) or not snapshot["frame_id"]
            or type(snapshot.get("scene_version")) is not int):
        return failure("perception_unavailable", "invalid_frame", "当前感知帧不可用，请更新场景并重新检测。")
    occupancy = snapshot.get("occupancy", {})
    if any(occupancy.get(s["spot_id"], {}).get("state") not in {"free", "occupied", "unknown"}
           for s in map_data["spots"]):
        return failure("perception_unavailable", "incomplete_occupancy", "当前帧车位状态不完整，无法分配。")
    return None


class Planner:
    """Stateless plug-in; Hu's service owns sessions and actual parking updates."""

    def plan(self, context: dict, request: dict) -> dict:
        map_data, vehicle = context["map"], context["vehicle"]
        validate_map(map_data)
        for field in ("needs_accessible", "needs_charging"):
            if type(request.get(field)) is not bool:
                return failure("invalid_request", "invalid_field", "请明确填写无障碍与充电需求。")
        if request.get("prefer_mode") not in {"near_destination", "near_exit"}:
            return failure("invalid_request", "invalid_preference", "请选择少走路或近出口模式。")
        if request.get("destination_id") != "elevator_1":
            return failure("invalid_request", "unknown_destination", "本地图的目的地为电梯。")
        accessible, charging = request["needs_accessible"], request["needs_charging"]
        if charging and vehicle.get("power_type") != "new_energy":
            return failure("invalid_request", "fuel_cannot_charge", "燃油车不能申请必须充电，请修改需求。")
        if accessible:
            permission = vehicle.get("accessible_permission")
            if permission is False:
                return failure("not_authorized", "accessible_not_authorized", "该登记车辆没有无障碍车位资格。")
            if permission is not True:
                return failure("needs_clarification", "accessible_permission_unknown", "无障碍资格未知，请先明确模拟登记。")
        if accessible and charging:
            return failure("no_eligible_spot", "no_combined_spot", "本地图没有同时满足无障碍和充电需求的复合车位。")
        kind = "accessible" if accessible else "charging" if charging else "normal"
        eligible = [s for s in map_data["spots"] if s["spot_type"] == kind]
        if not eligible:
            return failure("no_eligible_spot", "no_spot_type", "地图没有符合本次需求的车位类型。")
        unavailable = _snapshot_error(context)
        if unavailable:
            return unavailable
        snapshot = context["snapshot"]
        free = [s for s in eligible if snapshot["occupancy"][s["spot_id"]]["state"] == "free"]
        if not free:
            if any(snapshot["occupancy"][s["spot_id"]]["state"] == "unknown" for s in eligible):
                return failure("occupancy_uncertain", "no_observed_free_spot", "符合类型的车位暂无观测空闲，部分状态不确定。")
            return failure("no_available_spot", "all_eligible_occupied", "符合本次需求的车位均已占用。")
        obstacles = map_data["static_obstacles_m"] + snapshot["obstacles_m"]
        car_geometry = TraversalGeometry(map_data["car_areas_m"], obstacles)
        car_graph = _filter_graph(build_car_graph(map_data), car_geometry)
        ped_geometry = TraversalGeometry(map_data["ped_areas_m"], obstacles)
        ped_graph = _filter_graph(build_ped_graph(map_data), ped_geometry)
        candidates = []
        for spot in free:
            routes = self._spot_routes(map_data, spot, car_graph, ped_graph, obstacles, ped_geometry)
            if routes is None:
                continue
            raw_distances = {name: path_length(path) for name, path in routes.items()}
            ranking = ("walk_to_destination", "drive_in", "drive_out") if request["prefer_mode"] == "near_destination" else ("drive_out", "walk_to_destination", "drive_in")
            rank_key = tuple(_cm(raw_distances[name]) for name in ranking) + (int(spot["spot_id"][1:]),)
            candidates.append((rank_key, spot, routes, raw_distances))
        if not candidates:
            return failure("no_route", "no_reachable_candidate", "观测空闲车位的入场、出场或步行路线受阻，暂无可达车位。")
        candidates.sort(key=lambda item: item[0])
        _, target, routes, raw = candidates[0]
        return {
            "status": "ok", "reason_code": "recommended", "message": f"已推荐 {target['spot_id']}，三条所需路线均可达。",
            "target_spot_id": target["spot_id"], "path_m": routes["drive_in"],
            "distances_m": {key: round(value, 2) for key, value in raw.items()}, "routes": routes,
            "candidate_count": len(candidates), "unknown_excluded_count": sum(
                snapshot["occupancy"][s["spot_id"]]["state"] == "unknown" for s in eligible),
            "ranking": [{"spot_id": spot["spot_id"], "distances_m": {k: round(v, 2) for k, v in distances.items()}}
                        for _, spot, _, distances in candidates],
        }

    @staticmethod
    def _spot_routes(map_data, spot, car_base, ped_graph, obstacles, ped_geometry):
        # Only this candidate's terminal branch is added, so no parking-space shortcut exists.
        car_graph = car_base.copy()
        branch_geometry = TraversalGeometry(map_data["car_areas_m"] + [spot["rect_m"]], obstacles)
        access, center = as_point(spot["car_access_m"]), as_point(spot["center_m"])
        if access not in car_graph or not branch_geometry.segment_allowed(access, center):
            return None
        _add_edge(car_graph, access, center)
        _add_edge(car_graph, center, access)
        drive_in = _astar(car_graph, map_data["entry_m"], center, branch_geometry)
        drive_out = _astar(car_graph, center, map_data["exit_m"], branch_geometry)
        walk = _astar(ped_graph, spot["ped_access_m"], map_data["destination_m"], ped_geometry)
        if drive_in is None or drive_out is None or walk is None:
            return None
        return {"drive_in": drive_in, "drive_out": drive_out, "walk_to_destination": walk}

    def find_vehicle(self, context: dict, request: dict) -> dict:
        map_data = context["map"]
        validate_map(map_data)
        record = context.get("session") or {}
        target = next((s for s in map_data["spots"] if s["spot_id"] == record.get("actual_spot_id")), None)
        if target is None or record.get("state") != "parked":
            return failure("invalid_request", "no_parking_record", "没有匹配的实际停车记录。")
        unavailable = _snapshot_error(context)
        if unavailable:
            return unavailable
        try:
            start = as_point(request.get("pedestrian_start_m"))
        except (ValueError, TypeError):
            return failure("invalid_start", "invalid_coordinate", "请在地图人行通道内选择有效起点。")
        obstacles = map_data["static_obstacles_m"] + context["snapshot"]["obstacles_m"]
        geometry = TraversalGeometry(map_data["ped_areas_m"], obstacles)
        if not geometry.point_allowed(start):
            return failure("invalid_start", "outside_walkable_area", "起点须在人行通道内，并避开墙柱及检测到的车辆。")
        graph = build_ped_graph(map_data)
        try:
            delta, edge_id = _insert_pedestrian_start(graph, start, geometry)
        except ValueError as exc:
            return failure("invalid_start", "projection_not_allowed", str(exc))
        graph = _filter_graph(graph, geometry)
        path = _astar(graph, start, target["ped_access_m"], geometry)
        if path is None:
            return failure("no_route", "actual_spot_unreachable", "实际车位外的接近点或必经人行通路被阻挡，请等待或更新场景。")
        return {"status": "ok", "reason_code": "vehicle_route", "message": f"已规划至实际车位 {target['spot_id']} 外的接近点。",
                "target_spot_id": target["spot_id"], "path_m": path,
                "distances_m": {"walk_to_vehicle": round(path_length(path), 2)}, "routes": {"walk_to_vehicle": path},
                "projection_distance_m": delta, "projection_edge_id": edge_id}


def validate_returned_routes(context: dict, request: dict, result: dict) -> None:
    """Validate a plug-in's success without solving or choosing another target.

    The v1 contract requires all graph vertices, including the projected pedestrian
    start vertex. A plug-in must not simplify or interpolate its route polyline.
    This checks endpoints, edges/direction, complete geometry and reported length.
    """
    if result.get("status") != "ok":
        return
    if _snapshot_error(context):
        raise ValueError("不能在无效感知帧上接受成功路线")
    m = context["map"]
    target = next((s for s in m["spots"] if s["spot_id"] == result.get("target_spot_id")), None)
    if target is None:
        raise ValueError("路线目标车位不存在")
    obstacles = m["static_obstacles_m"] + context["snapshot"]["obstacles_m"]
    operation = request.get("operation")
    distances = result.get("distances_m") or {}

    def check(path, start, end, graph, geometry, metric_name):
        if not isinstance(path, list) or not path:
            raise ValueError(f"缺少 {metric_name} 路线")
        points = [as_point(p) for p in path]
        if points[0] != as_point(start) or points[-1] != as_point(end):
            raise ValueError(f"{metric_name} 路线端点不符合接口")
        if any(p not in graph for p in points) or any(not graph.has_edge(a, b) for a, b in zip(points, points[1:])):
            raise ValueError(f"{metric_name} 路线不遵循路网边、方向或完整节点序列")
        if not geometry.path_allowed(points):
            raise ValueError(f"{metric_name} 路线穿过障碍或禁行区域")
        reported = distances.get(metric_name)
        if (not isinstance(reported, (int, float)) or isinstance(reported, bool)
                or not isfinite(reported) or abs(reported - path_length(points)) > 0.005000001):
            raise ValueError(f"{metric_name} 距离与路线长度不一致")

    if operation in ("allocate", "replan"):
        routes = result.get("routes")
        if not isinstance(routes, dict) or any(k not in routes for k in ("drive_in", "drive_out", "walk_to_destination")):
            raise ValueError("成功推荐必须提供入场、出场、到电梯三条完整路线")
        if result.get("path_m") != routes["drive_in"]:
            raise ValueError("主路线必须与入场路线一致")
        car = build_car_graph(m)
        _add_edge(car, target["car_access_m"], target["center_m"])
        _add_edge(car, target["center_m"], target["car_access_m"])
        car_geometry = TraversalGeometry(m["car_areas_m"] + [target["rect_m"]], obstacles)
        ped_geometry = TraversalGeometry(m["ped_areas_m"], obstacles)
        check(routes["drive_in"], m["entry_m"], target["center_m"], car, car_geometry, "drive_in")
        check(routes["drive_out"], target["center_m"], m["exit_m"], car, car_geometry, "drive_out")
        check(routes["walk_to_destination"], target["ped_access_m"], m["destination_m"],
              build_ped_graph(m), ped_geometry, "walk_to_destination")
    elif operation == "find_vehicle":
        if target["spot_id"] != (context.get("session") or {}).get("actual_spot_id"):
            raise ValueError("寻车目标必须为实际停车记录")
        start = as_point(request.get("pedestrian_start_m"))
        geometry = TraversalGeometry(m["ped_areas_m"], obstacles)
        if not geometry.point_allowed(start):
            raise ValueError("寻车起点不在人行可行域或被阻挡")
        graph = build_ped_graph(m)
        _insert_pedestrian_start(graph, start, geometry)
        check(result.get("path_m"), start, target["ped_access_m"], graph, geometry, "walk_to_vehicle")
        routes = result.get("routes", {})
        if "walk_to_vehicle" in routes and routes["walk_to_vehicle"] != result["path_m"]:
            raise ValueError("寻车主路线与展示路线不一致")
    else:
        raise ValueError("路线校验不支持该操作")


def validate_networks(map_data: dict) -> None:
    """Structural map-load check: all 52 empty-map in/out/walk routes exist."""
    validate_map(map_data)
    obstacles = map_data["static_obstacles_m"]
    car_geometry = TraversalGeometry(map_data["car_areas_m"], obstacles)
    ped_geometry = TraversalGeometry(map_data["ped_areas_m"], obstacles)
    car = build_car_graph(map_data)
    ped = build_ped_graph(map_data)
    if any(not car_geometry.segment_allowed(a, b) for a, b in car.edges):
        raise ValueError("车行路网穿过墙柱或离开车道")
    if any(not ped_geometry.segment_allowed(a, b) for a, b in ped.edges):
        raise ValueError("人行路网穿过墙柱或离开通道")
    for spot in map_data["spots"]:
        if Planner._spot_routes(map_data, spot, car, ped, obstacles, ped_geometry) is None:
            raise ValueError(f"车位 {spot['spot_id']} 在空地图中不可达")

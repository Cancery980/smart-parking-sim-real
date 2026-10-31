"""Versioned, JSON-only seam between Hu's application and Liang's planner."""
from __future__ import annotations

from copy import deepcopy
from importlib import import_module
import math
from typing import Protocol, Any

CONTRACT_VERSION = "1.0"


class PlannerProtocol(Protocol):
    def plan(self, context: dict, request: dict) -> dict: ...
    def find_vehicle(self, context: dict, request: dict) -> dict: ...


def load_planner(name: str) -> PlannerProtocol:
    module_name, sep, class_name = name.partition(":")
    if not sep or not module_name or not class_name:
        raise ValueError("规划器格式应为 package.module:ClassName")
    instance = getattr(import_module(module_name), class_name)()
    if not callable(getattr(instance, "plan", None)) or not callable(getattr(instance, "find_vehicle", None)):
        raise TypeError("规划器必须实现 plan 和 find_vehicle")
    return instance


def planning_context(map_data: dict, snapshot: dict, vehicle: dict, session: dict) -> dict:
    # This explicit allowlist is the truth/perception boundary. Never pass Engine.__dict__.
    return deepcopy({"contract_version": CONTRACT_VERSION, "map": map_data,
                     "snapshot": snapshot, "vehicle": vehicle, "session": session})


def validate_planner_result(value: Any, map_data: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError("规划返回值不是对象")
    required = ("status", "reason_code", "message", "target_spot_id", "path_m", "distances_m")
    if any(k not in value for k in required):
        raise ValueError("规划返回值缺少必需字段")
    if not all(isinstance(value[k], str) for k in ("status", "reason_code", "message")):
        raise ValueError("规划返回状态、原因、消息必须为字符串")
    if value["status"] != "ok":
        if value["target_spot_id"] is not None or value["path_m"] != [] or value["distances_m"] is not None:
            raise ValueError("失败结果不能带目标、路线或距离")
        return deepcopy(value)
    if value["target_spot_id"] not in {s["spot_id"] for s in map_data["spots"]}:
        raise ValueError("规划返回了未知车位")
    _validate_path(value["path_m"], map_data)
    routes = value.get("routes", {})
    if not isinstance(routes, dict):
        raise ValueError("routes 必须是路线对象")
    for route in routes.values():
        _validate_path(route, map_data)
    distances = value["distances_m"]
    if not isinstance(distances, dict) or not distances or not all(
        isinstance(n, (int, float)) and not isinstance(n, bool) and math.isfinite(n) and n >= 0
        for n in distances.values()
    ):
        raise ValueError("成功规划的距离必须是非负有限数值")
    return deepcopy(value)


def _validate_path(path, map_data):
    if not isinstance(path, list) or len(path) < 1:
        raise ValueError("成功规划必须含路线")
    for point in path:
        if not isinstance(point, (list, tuple)) or len(point) != 2 or not all(
            isinstance(n, (int, float)) and not isinstance(n, bool) and math.isfinite(n) for n in point
        ):
            raise ValueError("路线必须是有限坐标数组")
        if not (0 <= point[0] <= map_data["width_m"] and 0 <= point[1] <= map_data["height_m"]):
            raise ValueError("路线越出地图")


def failure(status: str, reason: str, message: str) -> dict:
    return {"status": status, "reason_code": reason, "message": message,
            "target_spot_id": None, "path_m": [], "distances_m": None}

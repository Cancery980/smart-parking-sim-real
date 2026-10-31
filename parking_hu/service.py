"""Single-session application. Simulation truth never enters the planner context."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import threading
import uuid

from .contracts import failure, load_planner, planning_context, validate_planner_result
from .map_data import build_map
from .scenes import make_scene, render_scene
from .occupancy import estimate_occupancy


DEFAULT_PLANNER = "parking_hu.reference_planner:Planner"


def default_registry() -> dict:
    return {
        "HU_FUEL": {"vehicle_id": "HU_FUEL", "label": "模拟车辆 A · 燃油车", "power_type": "fuel", "accessible_permission": False, "role": "applicant"},
        "HU_EV": {"vehicle_id": "HU_EV", "label": "模拟车辆 B · 新能源车", "power_type": "new_energy", "accessible_permission": False, "role": "applicant"},
        "HU_ACCESS": {"vehicle_id": "HU_ACCESS", "label": "模拟车辆 C · 有无障碍资格", "power_type": "new_energy", "accessible_permission": True, "role": "applicant"},
        "HU_UNKNOWN": {"vehicle_id": "HU_UNKNOWN", "label": "模拟车辆 D · 资格待确认", "power_type": "fuel", "accessible_permission": "unknown", "role": "applicant"},
    }


def _overlap(a: list, b: list) -> bool:
    return min(a[2], b[2]) - max(a[0], b[0]) > 1e-9 and min(a[3], b[3]) - max(a[1], b[1]) > 1e-9


class ParkingService:
    def __init__(self, runtime_dir: str | Path, detector=None, *, weights_path: str | Path | None = None,
                 planner=None, planner_name=DEFAULT_PLANNER, initial_seed=42, initial_scenario="normal"):
        self.lock = threading.RLock()
        self.runtime_dir = Path(runtime_dir)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.map = build_map()
        from .reference_planner import validate_networks
        validate_networks(self.map)
        self.registry = default_registry()
        self.initial_seed = initial_seed
        self.initial_scenario = initial_scenario
        self.scene_version = 1
        self.frame_counter = 0
        self.frame_id = None
        self.image_path = self.runtime_dir / "current_frame.png"
        self.image_available = False
        self._scene = make_scene(self.map, seed=initial_seed, scenario=initial_scenario)
        self._validate_entities(self._scene["vehicles"])
        self.planner_name = planner_name
        self.planner = planner if planner is not None else load_planner(planner_name)
        self.detector = detector
        self.detector_load_error = None
        if detector is None:
            from .detector import YoloDetector
            try:
                self.detector = YoloDetector(weights_path or Path(__file__).resolve().parents[1] / "models" / "parking_yolov8n.pt")
            except Exception as exc:
                self.detector_load_error = f"模型加载失败：{type(exc).__name__}: {exc}"
        self.session = self._idle()
        self.last_request = None
        self.last_result = None
        self.route_result = None
        self.events = []
        self.snapshot = self._unknown("等待首次检测")
        self._render_and_detect()

    @staticmethod
    def _idle():
        return {"state": "idle", "session_id": None, "vehicle_id": None, "recommended_spot_id": None,
                "actual_spot_id": None, "confirmed_at": None, "scene_version": None,
                "frame_id": None, "route_valid": False, "plan_result": None, "request": None}

    def _unknown(self, error):
        return estimate_occupancy(self.map, [], self.scene_version, self.frame_id or "no_frame", valid=False, error=error)

    def _invalidate_routes(self):
        self.route_result = None
        self.session["route_valid"] = False

    def _render_and_detect(self):
        self._invalidate_routes()
        self.frame_counter += 1
        self.frame_id = f"v{self.scene_version:05d}_f{self.frame_counter:05d}"
        self.snapshot = self._unknown("正在生成新帧并检测")
        self.image_available = False
        try:
            render_scene(self.map, self._scene, self.image_path)
            self.image_available = True
            if self.detector is None:
                raise RuntimeError(self.detector_load_error or "模型不可用")
            detections = self.detector.detect(self.image_path, self.scene_version, self.frame_id)
            candidate = estimate_occupancy(self.map, detections, self.scene_version, self.frame_id)
            if not candidate["valid"]:
                candidate["error_code"] = candidate.get("error")
                candidate["error"] = "当前检测结果的字段、坐标或版本校验失败，请重新检测。"
            self.snapshot = candidate
        except Exception as exc:
            self.snapshot = self._unknown(f"感知失败：{type(exc).__name__}: {exc}")
        if self.session["state"] == "parked":
            actual = self.session["actual_spot_id"]
            observed = self.snapshot["occupancy"].get(actual, {}).get("state")
            self.session["perception_consistent"] = bool(self.snapshot["valid"] and observed == "occupied")

    def _validate_entities(self, entities):
        seen = set()
        for entity in entities:
            vid, rect = entity.get("vehicle_id"), entity.get("rect_m")
            if not isinstance(vid, str) or not vid or vid in seen:
                raise ValueError("场景车辆 ID 缺失或重复")
            if vid in self.registry and (not hasattr(self, "session") or self.session.get("state") != "parked" or self.session.get("vehicle_id") != vid):
                raise ValueError("背景车辆 ID 与可申请车辆 ID 冲突")
            seen.add(vid)
            if not isinstance(rect, (list, tuple)) or len(rect) != 4 or not all(
                isinstance(n, (int, float)) and not isinstance(n, bool) and math.isfinite(n) for n in rect
            ):
                raise ValueError("车辆矩形格式不合法")
            if not (0 <= rect[0] < rect[2] <= self.map["width_m"] and 0 <= rect[1] < rect[3] <= self.map["height_m"]):
                raise ValueError("车辆矩形越界")
        for i, a in enumerate(entities):
            if any(_overlap(a["rect_m"], b["rect_m"]) for b in entities[i+1:]):
                raise ValueError("场景车辆实体重叠")

    def _decorate(self, result, operation):
        result = deepcopy(result)
        result.update(operation=operation, session_id=self.session["session_id"], map_id=self.map["map_id"],
                      scene_version=self.scene_version, frame_id=self.frame_id)
        return result

    def _finish(self, result, operation):
        result = self._decorate(result, operation)
        self.last_result = result
        self.events.append({"time": datetime.now().astimezone().isoformat(timespec="seconds"),
                            "operation": operation, "status": result["status"], "message": result["message"],
                            "scene_version": self.scene_version, "frame_id": self.frame_id})
        self.events = self.events[-20:]
        return result

    def state(self):
        with self.lock:
            return deepcopy({"map": self.map, "scene_version": self.scene_version, "frame_id": self.frame_id,
                             "snapshot": self.snapshot, "registry": self.registry, "session": self.session,
                             "last_request": self.last_request, "last_result": self.last_result,
                             "route_result": self.route_result, "events": self.events,
                             "image_url": f"/api/frame?frame_id={self.frame_id}" if self.image_available else None,
                             "planner_name": self.planner_name, "scene_id": self._scene["scene_id"],
                             "seed": self._scene["seed"], "scenario": self._scene["scenario"]})

    def export_context(self):
        with self.lock:
            vid = self.session["vehicle_id"] or (self.last_request or {}).get("vehicle_id") or "HU_FUEL"
            if vid not in self.registry:
                vid = "HU_FUEL"
            return {"context": planning_context(self.map, self.snapshot, self.registry[vid], self.session),
                    "request": deepcopy(self.last_request), "result": deepcopy(self.last_result),
                    "planner": self.planner_name}

    def _validate_request(self, request, operation):
        vid = request.get("vehicle_id")
        if not isinstance(vid, str) or vid not in self.registry:
            return failure("invalid_request", "unknown_vehicle", "请选择已登记的模拟车辆。")
        if operation in ("allocate", "replan"):
            for name in ("needs_accessible", "needs_charging"):
                if name not in request or request[name] is None:
                    return failure("needs_clarification", "missing_field", "请明确填写无障碍与充电需求。")
                if type(request[name]) is not bool:
                    return failure("invalid_request", "invalid_field", "需求字段必须是布尔值。")
            if request.get("prefer_mode") not in ("near_destination", "near_exit") or request.get("destination_id") != "elevator_1":
                return failure("invalid_request", "invalid_preference", "请选择少走路或近出口，目的地应为电梯。")
            if request.get("pedestrian_start_m") is not None:
                return failure("invalid_request", "unexpected_start", "申请车位时不需要行人起点。")
            if operation == "allocate" and request.get("session_id") is not None:
                return failure("invalid_request", "unexpected_session", "新申请不能携带旧会话。")
        if operation == "find_vehicle":
            point = request.get("pedestrian_start_m")
            if not isinstance(point, (list, tuple)) or len(point) != 2 or not all(
                isinstance(n, (int, float)) and not isinstance(n, bool) and math.isfinite(n) for n in point
            ):
                return failure("invalid_request", "invalid_start_format", "请在地图点选一个有效的行人起点。")
        return None

    def _matching_session(self, request, state):
        if self.session["state"] != state:
            reason = "no_parking_record" if state == "parked" else "invalid_session_state"
            return failure("invalid_request", reason, "当前没有可执行该操作的停车会话。")
        if request.get("session_id") != self.session["session_id"] or request.get("vehicle_id") != self.session["vehicle_id"]:
            return failure("invalid_request", "session_mismatch", "会话或车辆不匹配，原记录保持不变。")
        return None

    def _planner_call(self, operation, request):
        context = planning_context(self.map, self.snapshot, self.registry[request["vehicle_id"]], self.session)
        try:
            fn = self.planner.find_vehicle if operation == "find_vehicle" else self.planner.plan
            result = validate_planner_result(fn(deepcopy(context), deepcopy(request)), self.map)
            if result["status"] == "ok":
                if not self.snapshot["valid"] or self.snapshot["scene_version"] != self.scene_version or self.snapshot["frame_id"] != self.frame_id:
                    raise ValueError("规划模块不能在无效或过期感知帧上返回成功")
                required_distances = {"walk_to_vehicle"} if operation == "find_vehicle" else {"drive_in", "drive_out", "walk_to_destination"}
                if not required_distances.issubset(result["distances_m"]):
                    raise ValueError("规划距离字段不完整")
                if operation != "find_vehicle" and self.snapshot["occupancy"][result["target_spot_id"]]["state"] != "free":
                    raise ValueError("规划模块返回的目标并非观测空闲")
                from .reference_planner import validate_returned_routes
                validate_returned_routes(context, request, result)
            return result
        except Exception as exc:
            return failure("planner_unavailable", "planner_contract_error", f"规划模块暂不可用：{type(exc).__name__}: {exc}")

    def action(self, request: dict):
        with self.lock:
            if not isinstance(request, dict):
                return self._finish(failure("invalid_request", "invalid_payload", "请求应为 JSON 对象。"), "unknown")
            operation = request.get("operation", "")
            if not isinstance(operation, str) or operation not in {"allocate", "replan", "find_vehicle", "confirm_park", "cancel", "depart", "reset", "refresh", "change_scene"}:
                return self._finish(failure("invalid_request", "unknown_operation", "不支持该操作。"), str(operation))
            if operation in ("allocate", "replan", "find_vehicle"):
                error = self._validate_request(request, operation)
                if error:
                    return self._finish(error, operation)
                if operation == "allocate":
                    if self.session["state"] != "idle":
                        return self._finish(failure("session_busy", "active_session", "已有活动会话，请重规划、取消或离场后再申请。"), operation)
                    vehicle = self.registry[request["vehicle_id"]]
                    if vehicle["role"] != "applicant":
                        return self._finish(failure("invalid_request", "invalid_role", "背景车辆不能申请入场。"), operation)
                    if any(v["vehicle_id"] == request["vehicle_id"] for v in self._scene["vehicles"]):
                        return self._finish(failure("invalid_request", "vehicle_already_present", "该车辆已在场，不能再次入场。"), operation)
                else:
                    error = self._matching_session(request, "parked" if operation == "find_vehicle" else "planned")
                    if error:
                        return self._finish(error, operation)
                self.last_request = deepcopy(request)
                result = self._planner_call(operation, request)
                if operation == "find_vehicle":
                    self.route_result = self._decorate(result, operation) if result["status"] == "ok" else None
                    self.session["route_valid"] = result["status"] == "ok"
                elif result["status"] == "ok":
                    sid = self.session["session_id"] or uuid.uuid4().hex[:12]
                    self.session = {**self._idle(), "state": "planned", "session_id": sid,
                                    "vehicle_id": request["vehicle_id"], "recommended_spot_id": result["target_spot_id"],
                                    "map_id": self.map["map_id"], "scene_version": self.scene_version,
                                    "frame_id": self.frame_id, "route_valid": True, "request": deepcopy(request)}
                    self.route_result = self._decorate(result, operation)
                    self.session["plan_result"] = deepcopy(self.route_result)
                else:
                    self.session = self._idle()
                    self.route_result = None
                return self._finish(result, operation)
            if operation == "confirm_park":
                return self._finish(self._confirm(request), operation)
            if operation in ("cancel", "depart"):
                error = self._matching_session(request, "planned" if operation == "cancel" else "parked")
                if error:
                    return self._finish(error, operation)
                if operation == "depart":
                    vid = self.session["vehicle_id"]
                    self._scene["vehicles"] = [v for v in self._scene["vehicles"] if v["vehicle_id"] != vid]
                    self.scene_version += 1
                self.session = self._idle()
                self.route_result = None
                if operation == "depart":
                    self._render_and_detect()
                return self._finish(self._event_ok("已取消推荐。" if operation == "cancel" else "已记录离场并更新感知。"), operation)
            if operation == "refresh":
                self._render_and_detect()
                result = self._event_ok("已更新检测；原路线失效，请重新规划或寻车。") if self.snapshot["valid"] else failure("perception_unavailable", "inference_failed", self.snapshot["error"])
                return self._finish(result, operation)
            if operation == "reset":
                self._scene = make_scene(self.map, seed=self.initial_seed, scenario=self.initial_scenario)
                self.session = self._idle()
                self.last_request = None
                self.scene_version += 1
                self._render_and_detect()
                return self._finish(self._event_ok("已显式重置会话及场景。"), operation)
            if operation == "change_scene":
                seed = request.get("seed", 42)
                scenario = request.get("scenario", "normal")
                if type(seed) is not int or not 0 <= seed <= 2**31-1 or not isinstance(scenario, str) or scenario not in {"empty", "normal", "full", "occluded", "boundary", "blocked"}:
                    return self._finish(failure("invalid_request", "invalid_scene", "场景类型或随机种子不合法。"), operation)
                try:
                    new_scene = make_scene(self.map, seed=seed, scenario=scenario)
                    if self.session["state"] == "parked":
                        own = next(deepcopy(v) for v in self._scene["vehicles"] if v["vehicle_id"] == self.session["vehicle_id"])
                        # Scene generation may exclude conflicts; allocation never sees this truth.
                        new_scene["vehicles"] = [v for v in new_scene["vehicles"] if not _overlap(v["rect_m"], own["rect_m"])] + [own]
                    self._validate_entities(new_scene["vehicles"])
                    self._scene = new_scene
                    self.scene_version += 1
                    self._render_and_detect()
                    result = self._event_ok("场景已更新，旧路线失效；已确认的实际停车位置保持不变。")
                except Exception as exc:
                    result = failure("invalid_request", "invalid_scene", f"场景更新被拒绝：{exc}")
                return self._finish(result, operation)

    @staticmethod
    def _event_ok(message):
        return {"status": "ok", "reason_code": "ok", "message": message, "target_spot_id": None, "path_m": [], "distances_m": None}

    def _confirm(self, request):
        error = self._matching_session(request, "planned")
        if error:
            return error
        if type(request.get("scene_version")) is not int or request["scene_version"] != self.scene_version or self.session["scene_version"] != self.scene_version:
            return failure("invalid_request", "stale_scene", "场景版本已变化，请重新规划后再确认。")
        spot = next((s for s in self.map["spots"] if s["spot_id"] == request.get("actual_spot_id")), None)
        if spot is None:
            return failure("invalid_request", "unknown_spot", "实际车位不存在。")
        vid = self.session["vehicle_id"]
        if any(v["vehicle_id"] == vid for v in self._scene["vehicles"]):
            return failure("invalid_request", "vehicle_already_present", "该车辆已有实体，未写入重复记录。")
        cx, cy = spot["center_m"]
        rect = [cx - 0.86, cy - 2.10, cx + 0.86, cy + 2.10]
        # Confirm checks physical entities, including a missed background car; it never repairs perception.
        if any(_overlap(rect, v["rect_m"]) for v in self._scene["vehicles"]):
            return failure("invalid_request", "spot_conflict", "目标位置与已有车辆冲突，实际停车记录未改变。")
        entity = {"vehicle_id": vid, "rect_m": rect, "color": "#2089c4", "heading": 0, "style": 0}
        alerts = []
        if spot["spot_id"] != self.session["recommended_spot_id"]:
            alerts.append({"code": "deviated_from_recommendation", "message": "实际停车位置与推荐不同，寻车将使用实际位置。", "evidence_source": "confirmation_event"})
        vehicle = self.registry[vid]
        if spot["spot_type"] == "charging" and vehicle["power_type"] == "fuel":
            alerts.append({"code": "fuel_in_charging", "message": "登记为燃油车，实际停在充电位。", "evidence_source": "confirmation_event"})
        if spot["spot_type"] == "accessible" and vehicle["accessible_permission"] is not True:
            code = "unauthorized_accessible" if vehicle["accessible_permission"] is False else "qualification_unknown"
            alerts.append({"code": code, "message": "实际停在无障碍位，登记资格不符合或尚待确认。", "evidence_source": "confirmation_event"})
        self._scene["vehicles"].append(entity)
        self.scene_version += 1
        self.session.update(state="parked", actual_spot_id=spot["spot_id"],
                            confirmed_at=datetime.now(timezone.utc).isoformat(), scene_version=self.scene_version,
                            alerts=alerts, route_valid=False)
        self._render_and_detect()
        self.session["frame_id"] = self.frame_id
        msg = f"已确认实际停在 {spot['spot_id']}。"
        if alerts:
            msg += " ".join(a["message"] for a in alerts)
        if not self.snapshot["valid"]:
            msg += " 感知暂不可用，停车事实仍已保存；恢复检测后可寻车。"
        elif not self.session.get("perception_consistent"):
            msg += " 本次检测与实际记录不一致，已保留实际记录；该次不计为识别成功。"
        result = self._event_ok(msg)
        result["alerts"] = alerts
        result["actual_spot_id"] = spot["spot_id"]
        result["perception_valid"] = self.snapshot["valid"]
        return result

    def set_scene_for_evaluation(self, scene: dict):
        """Offline fixture entry; never exposed over HTTP. Used for frozen E scenarios."""
        with self.lock:
            self._validate_entities(scene["vehicles"])
            if self.session["state"] == "parked":
                old_own = next(v for v in self._scene["vehicles"] if v["vehicle_id"] == self.session["vehicle_id"])
                new_own = [v for v in scene["vehicles"] if v["vehicle_id"] == self.session["vehicle_id"]]
                if new_own != [old_own]:
                    raise ValueError("评测场景不得移动或删除已停车车辆")
            self._scene = deepcopy(scene)
            self.scene_version += 1
            self._render_and_detect()

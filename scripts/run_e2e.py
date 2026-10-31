"""Frozen, real-YOLO E01—E08 checks. No detector stubs or truth-based recommendations."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from parking_hu.detector import YoloDetector
from parking_hu.geometry import TraversalGeometry
from parking_hu.scenes import make_scene, truth_occupancy
from parking_hu.service import DEFAULT_PLANNER, ParkingService, _overlap


def request(**extra):
    return dict(operation="allocate", vehicle_id=extra.pop("vehicle_id", "HU_FUEL"), session_id=None,
                needs_accessible=extra.pop("needs_accessible", False), needs_charging=extra.pop("needs_charging", False),
                prefer_mode=extra.pop("prefer_mode", "near_destination"), destination_id="elevator_1",
                pedestrian_start_m=None, **extra)


def event(service, operation, **extra):
    return service.action(dict(operation=operation, session_id=service.session["session_id"],
                               vehicle_id=service.session["vehicle_id"], **extra))


def validate_success(service, result, kind="normal"):
    assert result["status"] == "ok", result
    assert service.snapshot["valid"], service.snapshot
    spot = next(s for s in service.map["spots"] if s["spot_id"] == result["target_spot_id"])
    assert spot["spot_type"] == kind
    assert truth_occupancy(service.map, service._scene)[spot["spot_id"]] == "free", "wrongly recommended occupied spot"
    obstacles = service.map["static_obstacles_m"] + [v["rect_m"] for v in service._scene["vehicles"]]
    car = TraversalGeometry(service.map["car_areas_m"] + [spot["rect_m"]], obstacles)
    ped = TraversalGeometry(service.map["ped_areas_m"], obstacles)
    for name, path in result["routes"].items():
        assert (ped if name == "walk_to_destination" else car).path_allowed(path), f"truth-obstructed route: {name}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--weights", default=str(ROOT / "models" / "parking_yolov8n.pt"))
    parser.add_argument("--out", default=str(ROOT / "reports" / "e2e"))
    parser.add_argument("--planner", default=DEFAULT_PLANNER, help="规划插件，格式 module:Class")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    detector = YoloDetector(args.weights)
    manifest = [
        {"id": "E01", "seed": 91001, "scenario": "empty", "expectation": "双模式均推荐合法真实空闲可达普通位"},
        {"id": "E02", "seed": 91002, "scenario": "normal", "expectation": "背景占用下正常推荐"},
        {"id": "E03", "seed": 91003, "scenario": "normal", "expectation": "新能源充电需求仅推荐充电位"},
        {"id": "E04", "seed": 91004, "scenario": "normal", "expectation": "有资格无障碍需求仅推荐无障碍位"},
        {"id": "E05", "seed": 91005, "scenario": "full", "expectation": "普通位全满不得错荐占用位"},
        {"id": "E06", "seed": 91006, "scenario": "normal", "expectation": "实际P002不同于推荐，寻车终点为其外接近点"},
        {"id": "E07", "seed": 91007, "scenario": "normal", "expectation": "实际P036的必经人行通道被水平车阻断时no_route"},
        {"id": "E08", "seed": 91008, "scenario": "normal", "expectation": "更新场景立即撤销旧路线且拒绝过期确认"},
    ]
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    results = []
    with tempfile.TemporaryDirectory(prefix="parking_e2e_") as temp:
        for case in manifest:
            t0 = time.perf_counter()
            service = ParkingService(Path(temp) / case["id"], detector=detector,
                                     planner_name=args.planner, initial_seed=case["seed"], initial_scenario=case["scenario"])
            operations = []
            snapshots = []
            def capture(label):
                prefix = f"{case['id']}_{label}"
                if service.image_available:
                    shutil.copy2(service.image_path, out / f"{prefix}.png")
                evidence = {"snapshot": deepcopy(service.snapshot), "scene_truth": deepcopy(service._scene),
                            "session": deepcopy(service.session), "last_result": deepcopy(service.last_result)}
                (out / f"{prefix}.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
                snapshots.append(prefix)
            try:
                capture("input")
                if case["id"] == "E01":
                    for mode in ("near_destination", "near_exit"):
                        result = service.action(request(prefer_mode=mode)); operations.append(result)
                        validate_success(service, result)
                        event(service, "cancel")
                elif case["id"] in ("E02", "E03", "E04"):
                    req = request()
                    kind = "normal"
                    if case["id"] == "E03":
                        req.update(vehicle_id="HU_EV", needs_charging=True); kind = "charging"
                    elif case["id"] == "E04":
                        req.update(vehicle_id="HU_ACCESS", needs_accessible=True); kind = "accessible"
                    result = service.action(req); operations.append(result)
                    validate_success(service, result, kind)
                elif case["id"] == "E05":
                    result = service.action(request()); operations.append(result)
                    assert service.snapshot["valid"]
                    assert result["status"] in ("no_available_spot", "occupancy_uncertain", "no_route"), result
                elif case["id"] in ("E06", "E07"):
                    actual = "P002" if case["id"] == "E06" else "P036"
                    spot = next(s for s in service.map["spots"] if s["spot_id"] == actual)
                    scene = deepcopy(service._scene)
                    # A predeclared scenario constraint, not feedback from predictions.
                    scene["vehicles"] = [v for v in scene["vehicles"] if not _overlap(v["rect_m"], spot["rect_m"])]
                    service.set_scene_for_evaluation(scene)
                    result = service.action(request()); operations.append(result)
                    validate_success(service, result)
                    assert result["target_spot_id"] != actual
                    result = event(service, "confirm_park", actual_spot_id=actual, scene_version=service.scene_version)
                    operations.append(result)
                    assert result["status"] == "ok" and service.session["state"] == "parked"
                    capture("parked")
                    if case["id"] == "E07":
                        scene = deepcopy(service._scene)
                        rect = [40.25, 12.05, 44.75, 13.95]
                        scene["vehicles"] = [v for v in scene["vehicles"] if not _overlap(rect, v["rect_m"])]
                        scene["vehicles"].append({"vehicle_id": "BG_E07_BLOCK", "rect_m": rect, "color": "#c74e3e", "heading": 90, "style": 0})
                        service.set_scene_for_evaluation(scene)
                        capture("blocked")
                    result = event(service, "find_vehicle", pedestrian_start_m=[42.6, 4]); operations.append(result)
                    if case["id"] == "E07":
                        assert result["status"] == "no_route", result
                    else:
                        assert result["status"] == "ok" and result["target_spot_id"] == actual
                        assert result["path_m"][-1] == spot["ped_access_m"]
                        geometry = TraversalGeometry(service.map["ped_areas_m"], service.map["static_obstacles_m"] + [v["rect_m"] for v in service._scene["vehicles"]])
                        assert geometry.path_allowed(result["path_m"])
                elif case["id"] == "E08":
                    result = service.action(request()); operations.append(result)
                    validate_success(service, result)
                    old = service.scene_version
                    changed = service.action({"operation": "change_scene", "seed": 92008, "scenario": "normal"}); operations.append(changed)
                    assert service.scene_version != old and service.route_result is None and not service.session["route_valid"]
                    result = event(service, "confirm_park", actual_spot_id="P001", scene_version=old); operations.append(result)
                    assert result["reason_code"] == "stale_scene" and service.session["state"] == "planned"
                capture("final")
                outcome, error = "passed", None
            except Exception as exc:
                capture("failure")
                outcome, error = "failed", f"{type(exc).__name__}: {exc}"
            row = {**case, "outcome": outcome, "error": error, "operations": operations,
                   "evidence": snapshots, "elapsed_seconds": round(time.perf_counter() - t0, 3)}
            results.append(row)
            print(case["id"], outcome, error or "", flush=True)
    report = {"planner": args.planner, "model_sha256": hashlib.sha256(Path(args.weights).read_bytes()).hexdigest(),
              "real_model": True, "passed": sum(x["outcome"] == "passed" for x in results),
              "total": len(results), "cases": results}
    (out / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if report["passed"] != report["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

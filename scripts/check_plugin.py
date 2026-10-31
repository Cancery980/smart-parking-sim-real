"""Check a replacement planner's interface with explicit deterministic fixtures."""
from __future__ import annotations
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from parking_hu.contracts import load_planner, planning_context, validate_planner_result
from parking_hu.map_data import build_map
from parking_hu.occupancy import estimate_occupancy
from parking_hu.reference_planner import validate_returned_routes
from parking_hu.service import default_registry


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--planner", default="examples.liang_adapter:Planner")
    args = parser.parse_args()
    planner = load_planner(args.planner)
    map_data = build_map()
    snapshot = estimate_occupancy(map_data, [], 1, "contract_fixture")
    registry = default_registry()
    checks = []
    base = dict(operation="allocate", vehicle_id="HU_FUEL", session_id=None, needs_accessible=False,
                needs_charging=False, prefer_mode="near_destination", destination_id="elevator_1", pedestrian_start_m=None)
    cases = [({}, "ok", "normal"), ({"prefer_mode": "near_exit"}, "ok", "normal"),
             ({"needs_charging": True}, "invalid_request", None),
             ({"vehicle_id": "HU_EV", "needs_charging": True}, "ok", "charging"),
             ({"vehicle_id": "HU_ACCESS", "needs_accessible": True}, "ok", "accessible"),
             ({"needs_accessible": True}, "not_authorized", None),
             ({"vehicle_id": "HU_UNKNOWN", "needs_accessible": True}, "needs_clarification", None),
             ({"vehicle_id": "HU_ACCESS", "needs_accessible": True, "needs_charging": True}, "no_eligible_spot", None)]
    for i, (extra, expected, kind) in enumerate(cases):
        req = {**base, **extra}
        ctx = planning_context(map_data, snapshot, registry[req["vehicle_id"]], {"state": "idle"})
        result = validate_planner_result(planner.plan(deepcopy(ctx), deepcopy(req)), map_data)
        assert result["status"] == expected, (i, result)
        if expected == "ok":
            validate_returned_routes(ctx, req, result)
            assert next(s for s in map_data["spots"] if s["spot_id"] == result["target_spot_id"])["spot_type"] == kind
        checks.append({"case": i+1, "status": "passed", "result": result})
    record = {"state": "parked", "actual_spot_id": "P002", "vehicle_id": "HU_FUEL", "session_id": "plugin_test"}
    ctx = planning_context(map_data, snapshot, registry["HU_FUEL"], record)
    req = {"operation": "find_vehicle", "vehicle_id": "HU_FUEL", "session_id": "plugin_test", "pedestrian_start_m": [42.6, 4]}
    result = validate_planner_result(planner.find_vehicle(deepcopy(ctx), deepcopy(req)), map_data)
    assert result["status"] == "ok" and result["target_spot_id"] == "P002"
    validate_returned_routes(ctx, req, result)
    checks.append({"case": 9, "status": "passed", "result": result})
    report = {"planner": args.planner, "fixture_based": True, "note": "Interface checks do not replace real-YOLO E01—E08 or performance evaluation.", "passed": len(checks), "checks": checks}
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / "plugin_contract_check.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT / "examples" / "planner_context.json").write_text(json.dumps({"context": ctx, "request": req, "expected_result": result}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{args.planner}: {len(checks)} contract checks passed")


if __name__ == "__main__":
    main()

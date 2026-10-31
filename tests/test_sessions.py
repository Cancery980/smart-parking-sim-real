"""Deterministic session tests use explicit test doubles, never the UI detector."""
from copy import deepcopy
import json

import pytest

from parking_hu.service import ParkingService


class FixedDetector:
    def __init__(self):
        self.rows = []
        self.error = False

    def detect(self, image_path, scene_version, frame_id):
        if self.error:
            raise RuntimeError("injected inference failure")
        return deepcopy(self.rows)


@pytest.fixture
def app(tmp_path):
    return ParkingService(tmp_path, detector=FixedDetector(), initial_scenario="empty")


def allocate(app, **overrides):
    request = dict(operation="allocate", vehicle_id="HU_FUEL", session_id=None,
                   needs_accessible=False, needs_charging=False, prefer_mode="near_destination",
                   destination_id="elevator_1", pedestrian_start_m=None)
    request.update(overrides)
    return app.action(request)


def event(app, operation, **extra):
    return app.action(dict(operation=operation, session_id=app.session["session_id"],
                           vehicle_id=app.session["vehicle_id"], **extra))


def test_T12_actual_deviation_and_find_target(app):
    assert allocate(app)["status"] == "ok"
    result = event(app, "confirm_park", actual_spot_id="P002", scene_version=app.scene_version)
    assert result["status"] == "ok"
    assert app.session["actual_spot_id"] == "P002"
    assert app.session["state"] == "parked"
    assert any(a["code"] == "deviated_from_recommendation" for a in result["alerts"])
    found = event(app, "find_vehicle", pedestrian_start_m=[42.6, 4])
    assert found["status"] == "ok" and found["target_spot_id"] == "P002"
    assert found["path_m"][-1] == [8.75, 0.7]


def test_T15_truth_conflict_does_not_repair_missed_detection(app):
    app._scene["vehicles"] = [{"vehicle_id": "BG_TEST", "rect_m": [35.4, 17.2, 37.1, 21.4], "color": "#5a6478"}]
    assert allocate(app)["target_spot_id"] == "P035"  # Empty test detection still claims free.
    before = deepcopy((app._scene, app.session, app.scene_version))
    result = event(app, "confirm_park", actual_spot_id="P035", scene_version=app.scene_version)
    assert result["reason_code"] == "spot_conflict"
    assert (app._scene, app.session, app.scene_version) == before
    assert app.snapshot["occupancy"]["P035"]["state"] == "free"


def test_T15_stale_confirmation_atomic(app):
    allocate(app)
    app.action({"operation": "change_scene", "seed": 17, "scenario": "empty"})
    assert app.session["state"] == "planned" and not app.session["route_valid"]
    assert app.route_result is None
    before = deepcopy((app._scene, app.session, app.scene_version))
    result = event(app, "confirm_park", actual_spot_id="P001", scene_version=app.scene_version)
    assert result["reason_code"] == "stale_scene"
    assert (app._scene, app.session, app.scene_version) == before


def test_T16_roles_duplicates_and_parked_preservation(app):
    invalid = {"scene_id": "dup", "seed": 0, "scenario": "normal", "vehicles": [
        {"vehicle_id": "BG_DUP", "rect_m": [5.3, 2, 7, 6]},
        {"vehicle_id": "BG_DUP", "rect_m": [8, 2, 9.7, 6]}]}
    with pytest.raises(ValueError, match="重复"):
        app.set_scene_for_evaluation(invalid)
    app.registry["BACKGROUND"] = {"vehicle_id": "BACKGROUND", "role": "background", "power_type": "fuel", "accessible_permission": False}
    assert allocate(app, vehicle_id="BACKGROUND")["reason_code"] == "invalid_role"
    allocate(app)
    event(app, "confirm_park", actual_spot_id="P001", scene_version=app.scene_version)
    own = deepcopy([v for v in app._scene["vehicles"] if v["vehicle_id"] == "HU_FUEL"])
    assert allocate(app)["status"] == "session_busy"
    app.action({"operation": "change_scene", "seed": 7, "scenario": "normal"})
    assert [v for v in app._scene["vehicles"] if v["vehicle_id"] == "HU_FUEL"] == own
    assert app.session["actual_spot_id"] == "P001"


def test_T17_replan_precheck_preserves_and_solver_failure_clears(app):
    allocate(app)
    before = deepcopy(app.session)
    assert allocate(app)["status"] == "session_busy"
    bad = {**app.session["request"], "operation": "replan", "session_id": "wrong"}
    assert app.action(bad)["reason_code"] == "session_mismatch"
    assert app.session == before
    good_id_bad_business = {**app.session["request"], "operation": "replan", "session_id": app.session["session_id"], "needs_charging": True}
    assert app.action(good_id_bad_business)["status"] == "invalid_request"
    assert app.session["state"] == "idle" and app.route_result is None
    assert app.last_request["needs_charging"] is True


def test_T17_successful_replan_and_perception_failure(app):
    allocate(app)
    sid = app.session["session_id"]
    result = app.action({**app.session["request"], "operation": "replan", "session_id": sid, "prefer_mode": "near_exit"})
    assert result["status"] == "ok" and app.session["session_id"] == sid
    app.detector.error = True
    app.action({"operation": "refresh"})
    result = app.action({**app.session["request"], "operation": "replan", "session_id": sid})
    assert result["status"] == "perception_unavailable" and app.session["state"] == "idle"


@pytest.mark.parametrize("failure_mode", ["detection_error", "missed_detection", "render_error"])
def test_T18_confirmation_fact_survives_perception_failure(app, monkeypatch, failure_mode):
    allocate(app)
    if failure_mode == "detection_error":
        app.detector.error = True
    if failure_mode == "render_error":
        def fail(*a, **k):
            raise RuntimeError("injected render failure")
        monkeypatch.setattr("parking_hu.service.render_scene", fail)
    result = event(app, "confirm_park", actual_spot_id="P001", scene_version=app.scene_version)
    assert result["status"] == "ok" and app.session["state"] == "parked"
    assert app.session["actual_spot_id"] == "P001"
    assert any(v["vehicle_id"] == "HU_FUEL" for v in app._scene["vehicles"])
    assert app.session["perception_consistent"] is False
    if failure_mode != "missed_detection":
        assert not app.snapshot["valid"]
        assert {x["state"] for x in app.snapshot["occupancy"].values()} == {"unknown"}


def test_T19_cancel_depart_reset_and_wrong_events(app):
    initial_version = app.scene_version
    assert event(app, "depart")["status"] == "invalid_request"
    allocate(app)
    assert event(app, "depart")["status"] == "invalid_request"
    assert event(app, "cancel")["status"] == "ok"
    assert app.scene_version == initial_version and app.session["state"] == "idle"
    allocate(app)
    event(app, "confirm_park", actual_spot_id="P001", scene_version=app.scene_version)
    assert event(app, "cancel")["status"] == "invalid_request"
    result = event(app, "depart")
    assert result["status"] == "ok" and app.session["state"] == "idle"
    assert not any(v["vehicle_id"] == "HU_FUEL" for v in app._scene["vehicles"])
    allocate(app)
    assert app.action({"operation": "reset"})["status"] == "ok"
    assert app.session["state"] == "idle" and app.route_result is None


def test_planner_context_has_no_truth_and_no_shared_mutability(app):
    from parking_hu.reference_planner import Planner
    class SpyPlanner(Planner):
        def plan(self, context, request):
            assert set(context) == {"contract_version", "map", "snapshot", "vehicle", "session"}
            serialized = json.dumps(context)
            assert "scene_truth" not in serialized and '"vehicles"' not in serialized
            out = super().plan(context, request)
            context["vehicle"]["power_type"] = "forged"
            return out
    app.planner = SpyPlanner()
    assert allocate(app)["status"] == "ok"
    assert app.registry["HU_FUEL"]["power_type"] == "fuel"


def test_confirmation_advisory_is_event_evidence(app):
    allocate(app)
    result = event(app, "confirm_park", actual_spot_id="P045", scene_version=app.scene_version)
    assert result["status"] == "ok"
    alert = next(a for a in result["alerts"] if a["code"] == "fuel_in_charging")
    assert alert["evidence_source"] == "confirmation_event"
    assert app.session["actual_spot_id"] == "P045"


def test_failed_request_export_keeps_same_registered_vehicle(app):
    result = allocate(app, vehicle_id="HU_ACCESS", needs_accessible=True, needs_charging=True)
    assert result["status"] == "no_eligible_spot" and app.session["state"] == "idle"
    exported = app.export_context()
    assert exported["context"]["vehicle"]["vehicle_id"] == exported["request"]["vehicle_id"] == "HU_ACCESS"


def test_malformed_detection_gives_chinese_failure_and_invalidates_routes(app):
    allocate(app)
    app.detector.rows = [{"detection_id": "bad", "bbox_xyxy_px": [-1, 2, 4, 5], "confidence": .8, "class_name": "car"}]
    result = app.action({"operation": "refresh"})
    assert result["status"] == "perception_unavailable" and "校验失败" in result["message"]
    assert app.snapshot["error_code"] == "bbox_outside_frame" and app.route_result is None


def test_api_real_payload_and_truth_free_export(app):
    from fastapi.testclient import TestClient
    from parking_hu.api import create_app
    with TestClient(create_app(app)) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/frame").headers["content-type"] == "image/png"
        assert client.get("/api/state").json()["session"]["state"] == "idle"
        payload = dict(operation="allocate", vehicle_id="HU_FUEL", session_id=None, needs_accessible=False,
                       needs_charging=False, prefer_mode="near_destination", destination_id="elevator_1")
        response = client.post("/api/action", json=payload).json()
        assert response["result"]["status"] == "ok"
        assert response["state"]["session"]["state"] == "planned"
        assert "scene_truth" not in client.get("/api/export").text
        assert client.post("/api/action", json=[]).status_code == 422


@pytest.mark.parametrize("payload", [
    {"operation": []}, {"operation": {}},
    {"operation": "change_scene", "scenario": []},
    {"operation": "change_scene", "scenario": {}},
])
def test_api_wrong_field_types_return_chinese_failure_without_losing_session(app, payload):
    from fastapi.testclient import TestClient
    from parking_hu.api import create_app
    assert allocate(app)["status"] == "ok"
    before = deepcopy((app.session, app.scene_version, app.frame_id, app._scene))
    with TestClient(create_app(app)) as client:
        response = client.post("/api/action", json=payload)
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["status"] == "invalid_request"
    assert result["reason_code"] in {"unknown_operation", "invalid_scene"}
    assert any('\u4e00' <= char <= '\u9fff' for char in result["message"])
    assert (app.session, app.scene_version, app.frame_id, app._scene) == before

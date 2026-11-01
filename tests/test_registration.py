"""Vehicle registration changes records, never perception or parking facts."""
from copy import deepcopy

from fastapi.testclient import TestClient
import pytest

from parking_hu.api import create_app
from parking_hu.reference_planner import Planner
from parking_hu.service import ParkingService


class CountingDetector:
    def __init__(self):
        self.calls = 0

    def detect(self, image_path, scene_version, frame_id):
        self.calls += 1
        return []


class RecordingPlanner:
    def __init__(self):
        self.contexts = []
        self.delegate = Planner()

    def plan(self, context, request):
        self.contexts.append(deepcopy(context))
        return self.delegate.plan(context, request)

    def find_vehicle(self, context, request):
        return self.delegate.find_vehicle(context, request)


@pytest.fixture
def app(tmp_path):
    return ParkingService(tmp_path, detector=CountingDetector(), planner=RecordingPlanner(), initial_scenario="empty")


def registration(**changes):
    return {"operation": "register_vehicle", "label": "我的模拟车", "power_type": "new_energy",
            "accessible_permission": False, **changes}


def allocate(vehicle_id, **changes):
    return {"operation": "allocate", "vehicle_id": vehicle_id, "session_id": None,
            "needs_accessible": False, "needs_charging": False, "prefer_mode": "near_destination",
            "destination_id": "elevator_1", "pedestrian_start_m": None, **changes}


def parking_facts(app):
    # A diagnostic result/event is expected; domain records and inference are not.
    return deepcopy((app.session, app._scene, app.snapshot, app.scene_version, app.frame_id,
                     app.route_result, app.last_request, app.detector.calls, app.planner.contexts))


def test_register_idle_creates_unique_applicant_without_perception_or_planning(app):
    defaults = deepcopy(app.registry)
    before = parking_facts(app)
    result = app.action(registration(label="  我的车  ", vehicle_id="HU_FUEL", role="background"))
    assert result["status"] == "ok"
    vehicle = result["registered_vehicle"]
    assert vehicle["vehicle_id"].startswith("USER_") and vehicle["vehicle_id"] not in defaults
    assert vehicle == {"vehicle_id": vehicle["vehicle_id"], "label": "我的车", "power_type": "new_energy",
                       "accessible_permission": False, "role": "applicant"}
    assert app.registry[vehicle["vehicle_id"]] == vehicle
    assert {key: app.registry[key] for key in defaults} == defaults
    assert parking_facts(app) == before
    assert result["operation"] == "register_vehicle" and result["session_id"] is None
    assert result["scene_version"] == app.scene_version and result["frame_id"] == app.frame_id
    second = app.action(registration(label="车" * 40))
    assert second["status"] == "ok" and second["registered_vehicle"]["vehicle_id"] != vehicle["vehicle_id"]
    assert parking_facts(app) == before
    result["registered_vehicle"]["role"] = "background"
    assert app.registry[vehicle["vehicle_id"]]["role"] == "applicant"


@pytest.mark.parametrize("request_fields,spot_type", [({"needs_charging": True}, "charging"),
                                                     ({"needs_accessible": True}, "accessible")])
def test_new_registered_vehicle_is_sent_to_existing_planner(app, request_fields, spot_type):
    registration_result = app.action(registration(accessible_permission=True))
    assert registration_result["status"] == "ok"
    vehicle = registration_result["registered_vehicle"]
    result = app.action(allocate(vehicle["vehicle_id"], **request_fields))
    assert result["status"] == "ok"
    assert app.planner.contexts[-1]["vehicle"] == vehicle
    assert "scene_truth" not in app.planner.contexts[-1]
    target = next(s for s in app.map["spots"] if s["spot_id"] == result["target_spot_id"])
    assert target["spot_type"] == spot_type


@pytest.mark.parametrize("permission,expected", [(False, "not_authorized"), ("unknown", "needs_clarification")])
def test_registration_does_not_relax_accessible_qualification(app, permission, expected):
    registered = app.action(registration(accessible_permission=permission))
    assert registered["status"] == "ok"
    result = app.action(allocate(registered["registered_vehicle"]["vehicle_id"], needs_accessible=True))
    assert result["status"] == expected and result["target_spot_id"] is None
    assert app.session["state"] == "idle"


def test_registered_fuel_vehicle_cannot_request_charging(app):
    registered = app.action(registration(power_type="fuel"))
    assert registered["status"] == "ok"
    result = app.action(allocate(registered["registered_vehicle"]["vehicle_id"], needs_charging=True))
    assert result["reason_code"] == "fuel_cannot_charge" and result["target_spot_id"] is None


@pytest.mark.parametrize("change,reason", [
    ({"label": None}, "invalid_registration_label"), ({"label": ""}, "invalid_registration_label"),
    ({"label": "   "}, "invalid_registration_label"), ({"label": "车" * 41}, "invalid_registration_label"),
    ({"label": "车\nA"}, "invalid_registration_label"), ({"label": "车\x00A"}, "invalid_registration_label"),
    ({"label": "车\u200bA"}, "invalid_registration_label"),
    ({"power_type": "hybrid"}, "invalid_power_type"), ({"power_type": []}, "invalid_power_type"),
    ({"accessible_permission": 1}, "invalid_accessible_permission"),
    ({"accessible_permission": "true"}, "invalid_accessible_permission"),
    ({"accessible_permission": None}, "invalid_accessible_permission"),
    ({"accessible_permission": {}}, "invalid_accessible_permission"),
])
def test_invalid_registration_has_no_domain_side_effects(app, change, reason):
    before = deepcopy(app.registry), parking_facts(app)
    result = app.action(registration(**change))
    assert result["status"] == "invalid_request" and result["reason_code"] == reason
    assert (app.registry, parking_facts(app)) == before
    assert "registered_vehicle" not in result


@pytest.mark.parametrize("missing", ["label", "power_type", "accessible_permission"])
def test_registration_requires_all_fields(app, missing):
    request = registration(); del request[missing]
    before = deepcopy(app.registry), parking_facts(app)
    result = app.action(request)
    assert result["status"] == "invalid_request" and result["reason_code"] != "unknown_operation"
    assert (app.registry, parking_facts(app)) == before


@pytest.mark.parametrize("state", ["planned", "parked"])
def test_active_session_rejects_registration_without_changing_records(app, state):
    assert app.action(allocate("HU_FUEL"))["status"] == "ok"
    if state == "parked":
        assert app.action({"operation": "confirm_park", "vehicle_id": "HU_FUEL",
                           "session_id": app.session["session_id"], "scene_version": app.scene_version,
                           "actual_spot_id": "P001"})["status"] == "ok"
    before = deepcopy(app.registry), parking_facts(app)
    result = app.action(registration())
    assert result["status"] == "session_busy" and result["reason_code"] == "active_session"
    assert (app.registry, parking_facts(app)) == before


def test_reset_preserves_registration_but_another_process_has_only_defaults(app, tmp_path):
    registered = app.action(registration())
    assert registered["status"] == "ok"
    vehicle = registered["registered_vehicle"]
    assert app.action(allocate(vehicle["vehicle_id"]))["status"] == "ok"
    assert app.action({"operation": "reset"})["status"] == "ok"
    assert app.registry[vehicle["vehicle_id"]] == vehicle and app.session["state"] == "idle"
    assert app.action(allocate(vehicle["vehicle_id"]))["status"] == "ok"
    fresh = ParkingService(tmp_path / "fresh", detector=CountingDetector(), initial_scenario="empty")
    assert vehicle["vehicle_id"] not in fresh.registry


def test_registration_api_exposes_new_record_without_changing_frame(app):
    with TestClient(create_app(app)) as client:
        before = client.get("/api/state").json()
        response = client.post("/api/action", json=registration())
        assert response.status_code == 200 and response.json()["result"]["status"] == "ok"
        vehicle = response.json()["result"]["registered_vehicle"]
        after = client.get("/api/state").json()
        assert after["registry"][vehicle["vehicle_id"]] == vehicle
        assert after["frame_id"] == before["frame_id"] and after["snapshot"] == before["snapshot"]
        assert after["session"] == before["session"]
        assert client.post("/api/action", json=allocate(vehicle["vehicle_id"])).json()["result"]["status"] == "ok"

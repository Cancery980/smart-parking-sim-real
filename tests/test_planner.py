"""Deterministic acceptance of the replaceable planning boundary.

Fixed perception is intentional here; end-to-end YOLO evidence is separate.
"""
from copy import deepcopy

import pytest
from shapely.geometry import LineString, box

from parking_hu.geometry import TraversalGeometry, m_to_px, px_to_m, path_length
from parking_hu.map_data import build_map, validate_map
from parking_hu.reference_planner import (
    Planner, build_car_graph, build_ped_graph, validate_networks, _cm,
    validate_returned_routes,
)


def context(*, power="fuel", permission=False, obstacles=None, only_free=None):
    m = build_map()
    return {
        "contract_version": "1.0", "map": m,
        "snapshot": {"map_id": m["map_id"], "scene_version": 1, "frame_id": "test-frame-1", "valid": True,
                     "occupancy": {s["spot_id"]: {"state": "free" if only_free is None or s["spot_id"] in only_free else "occupied"}
                                   for s in m["spots"]}, "obstacles_m": obstacles or []},
        "vehicle": {"vehicle_id": "APP_TEST", "power_type": power, "accessible_permission": permission, "role": "applicant"},
        "session": None,
    }


def request(**kwargs):
    result = {"operation": "allocate", "vehicle_id": "APP_TEST", "session_id": None,
              "needs_accessible": False, "needs_charging": False,
              "prefer_mode": "near_destination", "destination_id": "elevator_1", "pedestrian_start_m": None}
    result.update(kwargs)
    return result


def parked_context(spot="P001", **kwargs):
    ctx = context(**kwargs)
    ctx["session"] = {"state": "parked", "session_id": "S_TEST", "vehicle_id": "APP_TEST", "actual_spot_id": spot}
    return ctx


def test_T01_map_counts_and_malformed_layout_rejected():
    m = build_map()
    assert len(m["spots"]) == 52
    assert [sum(s["spot_type"] == kind for s in m["spots"]) for kind in ("normal", "accessible", "charging")] == [40, 4, 8]
    changed = deepcopy(m)
    changed["spots"][1]["rect_m"] = changed["spots"][0]["rect_m"]
    with pytest.raises(ValueError, match="重叠"):
        validate_map(changed)
    changed = deepcopy(m)
    changed["spots"][1]["spot_id"] = "P001"
    with pytest.raises(ValueError, match="唯一"):
        validate_map(changed)
    validate_networks(m)


@pytest.mark.parametrize("spot_id", [f"P{i:03}" for i in range(1, 53)])
def test_T02_every_spot_has_three_legal_directed_routes(spot_id):
    ctx = context(power="new_energy", permission=True, only_free={spot_id})
    spot = next(s for s in ctx["map"]["spots"] if s["spot_id"] == spot_id)
    req = request(needs_charging=spot["spot_type"] == "charging", needs_accessible=spot["spot_type"] == "accessible")
    result = Planner().plan(ctx, req)
    assert result["status"] == "ok"
    assert result["target_spot_id"] == spot_id
    assert result["path_m"][0] == ctx["map"]["entry_m"]
    assert result["path_m"][-1] == spot["center_m"]
    assert result["routes"]["drive_out"][-1] == ctx["map"]["exit_m"]
    assert result["routes"]["walk_to_destination"][0] == spot["ped_access_m"]
    assert result["routes"]["walk_to_destination"][-1] == ctx["map"]["destination_m"]
    geometry = TraversalGeometry(ctx["map"]["car_areas_m"] + [spot["rect_m"]], ctx["map"]["static_obstacles_m"])
    assert geometry.path_allowed(result["routes"]["drive_in"])
    assert geometry.path_allowed(result["routes"]["drive_out"])
    for name, path in result["routes"].items():
        assert result["distances_m"][name] == pytest.approx(path_length(path), abs=0.005)


@pytest.mark.parametrize("spot_id,distances", [
    ("P001", (110.60, 79.10, 39.65)),
    ("P041", (87.60, 102.10, 16.65)),
    ("P045", (28.80, 43.30, 51.15)),
])
def test_T02_reference_distances(spot_id, distances):
    ctx = context(power="new_energy", permission=True, only_free={spot_id})
    result = Planner().plan(ctx, request(needs_accessible=spot_id == "P041", needs_charging=spot_id == "P045"))
    assert tuple(result["distances_m"][key] for key in ("drive_in", "drive_out", "walk_to_destination")) == distances


def test_T03_two_preferences_are_distinct_and_deterministic():
    ctx = context()
    for number in range(23, 36):
        ctx["snapshot"]["occupancy"][f"P{number:03}"]["state"] = "occupied"
    assert Planner().plan(ctx, request())["target_spot_id"] == "P022"
    assert Planner().plan(ctx, request(prefer_mode="near_exit"))["target_spot_id"] == "P040"


def test_T02_T11_driving_directions_and_network_separation():
    m = build_map()
    car, ped = build_car_graph(m), build_ped_graph(m)
    assert car.is_directed() and not ped.is_directed()
    for a, b in car.edges:
        if a[1] == b[1] == 8.35:
            assert b[0] < a[0]
        elif a[1] == b[1] == 23.65:
            assert b[0] > a[0]
        elif a[0] == b[0] == 2.25:
            assert b[1] > a[1]
        elif a[0] == b[0] == 45.75:
            assert b[1] < a[1]
    # Neither base graph contains an internal parking-space centre.
    assert all(tuple(s["center_m"]) not in car and tuple(s["center_m"]) not in ped for s in m["spots"])


def test_T04_T05_special_types_are_hard_constraints():
    p = Planner()
    ctx = context(power="new_energy", permission=True)
    assert p.plan(ctx, request(needs_charging=True))["target_spot_id"] in {f"P{i:03}" for i in range(45, 53)}
    assert p.plan(ctx, request(needs_accessible=True))["target_spot_id"] in {f"P{i:03}" for i in range(41, 45)}
    for number in range(45, 53):
        ctx["snapshot"]["occupancy"][f"P{number:03}"]["state"] = "occupied"
    assert p.plan(ctx, request(needs_charging=True))["status"] == "no_available_spot"
    assert p.plan(context(), request(needs_charging=True))["reason_code"] == "fuel_cannot_charge"


@pytest.mark.parametrize("permission,status", [(False, "not_authorized"), ("unknown", "needs_clarification")])
def test_T06_T14_qualification_only_applies_when_requested(permission, status):
    ctx = context(permission=permission)
    assert Planner().plan(ctx, request(needs_accessible=True))["status"] == status
    normal = Planner().plan(ctx, request())
    assert normal["status"] == "ok"
    assert int(normal["target_spot_id"][1:]) <= 40


def test_T07_no_implicit_relaxation_for_combined_requirements():
    result = Planner().plan(context(power="new_energy", permission=True), request(needs_accessible=True, needs_charging=True))
    assert result["status"] == "no_eligible_spot"
    assert result["target_spot_id"] is None and result["path_m"] == [] and result["distances_m"] is None


def test_T08_occupied_and_unknown_failures_differ():
    ctx = context(only_free=set())
    assert Planner().plan(ctx, request())["status"] == "no_available_spot"
    ctx["snapshot"]["occupancy"]["P001"]["state"] = "unknown"
    assert Planner().plan(ctx, request())["status"] == "occupancy_uncertain"


@pytest.mark.parametrize("change", [{"valid": False}, {"map_id": "wrong"}, {"frame_id": None}, {"scene_version": None}])
def test_T09_invalid_frame_never_uses_apparently_free_spaces(change):
    ctx = context()
    ctx["snapshot"].update(change)
    result = Planner().plan(ctx, request())
    assert result["status"] == "perception_unavailable"
    assert not result["path_m"]


def test_T10_unassigned_box_blocks_route_even_with_free_occupancy():
    ctx = context(obstacles=[[1.95, 15.95, 2.15, 16.05]])
    assert Planner().plan(ctx, request())["status"] == "no_route"


def test_T11_no_non_target_spot_shortcuts_and_full_line_obstacle_checks():
    ctx = context(only_free={"P001"})
    result = Planner().plan(ctx, request())
    route = LineString(result["path_m"])
    for spot in ctx["map"]["spots"]:
        if spot["spot_id"] != "P001":
            assert not route.intersects(box(*spot["rect_m"]).buffer(-1e-8))
    ctx = parked_context("P001", obstacles=[[24.5, 0.6, 24.7, 0.8]])
    # This box lies between graph vertices; endpoint-only checks would miss it.
    result = Planner().find_vehicle(ctx, request(operation="find_vehicle", pedestrian_start_m=[42.6, 4]))
    assert result["status"] == "no_route"


def test_T12_find_vehicle_targets_actual_spot_outside_parking_area():
    ctx = parked_context("P002")
    ctx["session"]["recommended_spot_id"] = "P001"
    result = Planner().find_vehicle(ctx, request(operation="find_vehicle", pedestrian_start_m=[42.9, 4]))
    assert result["status"] == "ok"
    assert result["target_spot_id"] == "P002"
    assert result["path_m"][0] == [42.9, 4.0]
    assert result["path_m"][-1] == [8.75, 0.7]
    assert result["projection_distance_m"] == pytest.approx(0.3)


def test_T13_target_vehicle_box_is_not_removed_to_make_a_path():
    ctx = parked_context("P001", obstacles=[[5.3, 0.5, 7.2, 6.4]])
    assert Planner().find_vehicle(ctx, request(pedestrian_start_m=[42.6, 4]))["status"] == "no_route"
    assert Planner().find_vehicle(ctx, request(pedestrian_start_m=[2, 16]))["status"] == "invalid_start"
    # Start is in the walkway, but its projection connector crosses a box.
    ctx = parked_context("P001", obstacles=[[42.7, 3.9, 42.8, 4.1]])
    assert Planner().find_vehicle(ctx, request(pedestrian_start_m=[42.9, 4]))["reason_code"] == "projection_not_allowed"


@pytest.mark.parametrize("start", [[float("nan"), 4], [True, 4], [42.6], [43.21, 4], [6.25, 3.95]])
def test_T13_bad_manual_start_never_snaps_across_forbidden_area(start):
    assert Planner().find_vehicle(parked_context(), request(pedestrian_start_m=start))["status"] == "invalid_start"


def test_T20_continuous_coordinates_and_drawing_rounding():
    for x in (0, 1 / 3, 6.25, 42.6, 52):
        for y in (0, 0.7, 8.35, 31.3, 32):
            pixels = m_to_px([x, y])
            restored = px_to_m(pixels)
            assert max(abs(restored[0] - x), abs(restored[1] - y)) < 1e-6
            assert all(abs(round(p) - p) <= 0.5 for p in pixels)
    assert _cm(1.005) == 101 and _cm(1.0049) == 100


def test_context_is_not_mutated_and_truth_is_not_needed():
    ctx = context()
    before = deepcopy(ctx)
    assert Planner().plan(ctx, request())["status"] == "ok"
    assert ctx == before
    assert "scene_truth" not in ctx


def test_plugin_route_validation_accepts_reference_and_rejects_shortcut():
    ctx, req = context(), request()
    result = Planner().plan(ctx, req)
    validate_returned_routes(ctx, req, result)
    broken = deepcopy(result)
    # A finite, in-bounds two-point line still crosses non-target parking spaces.
    broken["path_m"] = [result["path_m"][0], result["path_m"][-1]]
    broken["routes"]["drive_in"] = broken["path_m"]
    broken["distances_m"]["drive_in"] = round(path_length(broken["path_m"]), 2)
    with pytest.raises(ValueError, match="路网"):
        validate_returned_routes(ctx, req, broken)
    broken = deepcopy(result)
    broken["routes"]["drive_out"] = list(reversed(broken["routes"]["drive_out"]))
    with pytest.raises(ValueError, match="端点"):
        validate_returned_routes(ctx, req, broken)


def test_plugin_find_validation_checks_projection_obstacles_and_distance():
    ctx = parked_context("P002")
    req = request(operation="find_vehicle", pedestrian_start_m=[42.9, 4])
    result = Planner().find_vehicle(ctx, req)
    validate_returned_routes(ctx, req, result)
    broken = deepcopy(result)
    broken["distances_m"]["walk_to_vehicle"] += 1
    with pytest.raises(ValueError, match="距离"):
        validate_returned_routes(ctx, req, broken)
    obstructed = deepcopy(ctx)
    obstructed["snapshot"]["obstacles_m"] = [[20.0, 0.6, 20.1, 0.8]]
    with pytest.raises(ValueError, match="障碍"):
        validate_returned_routes(obstructed, req, result)

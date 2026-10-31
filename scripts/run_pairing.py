"""Frozen I01 B/S comparison over real YOLO caches, using a swappable planner.

This is an integration/reference experiment, not evidence about an unseen
implementation by Liang Ke. Truth is used only after each decision, for scoring.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import csv
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import networkx as nx

from parking_hu.contracts import failure, load_planner, planning_context, validate_planner_result
from parking_hu.geometry import TraversalGeometry, as_point, distance, rects_overlap
from parking_hu.map_data import build_map
from parking_hu.occupancy import HIGH_CONFIDENCE, LOW_CONFIDENCE, OVERLAP_FRACTION, estimate_occupancy
from parking_hu.reference_planner import build_car_graph, build_ped_graph, validate_returned_routes


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def freeze_requests(manifest_path: Path, destination: Path) -> tuple[list, dict]:
    """Create the request protocol before inspecting any detection caches."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if len(manifest) != 30 or len({r["scene"]["scene_id"] for r in manifest}) != 30:
        raise ValueError("I01 必须包含 30 个独立场景")
    if len({r["sha256"] for r in manifest}) != 30:
        raise ValueError("测试图像存在重复，不满足 30 个独立场景")
    if Counter(r["scene"]["scenario"] for r in manifest) != {"normal": 10, "occluded": 10, "boundary": 10}:
        raise ValueError("I01 分组必须为常规、遮挡、边界各 10 景")
    requests = []
    for i, row in enumerate(manifest):
        kind = ("normal", "charging", "accessible")[i % 3]
        vehicle = {"vehicle_id": f"I01_APP_{i:02}", "power_type": "fuel" if kind == "normal" else "new_energy",
                   "accessible_permission": kind == "accessible", "role": "applicant"}
        for mode in ("near_destination", "near_exit"):
            requests.append({
                "request_id": f"I01_{i + 1:02}_{'walk' if mode == 'near_destination' else 'exit'}",
                "scene_id": row["scene"]["scene_id"], "group": row["scene"]["scenario"],
                "image_sha256": row["sha256"], "eligible_type": kind, "vehicle": vehicle,
                "request": {"operation": "allocate", "vehicle_id": vehicle["vehicle_id"], "session_id": None,
                            "needs_accessible": kind == "accessible", "needs_charging": kind == "charging",
                            "prefer_mode": mode, "destination_id": "elevator_1", "pedestrian_start_m": None},
            })
    frozen = {
        "protocol_version": "I01-v1", "test_manifest_sha256": sha256(manifest_path),
        "independent_scenes": 30, "paired_requests": 60,
        "request_assignment": "scene index modulo 3: normal / charging / accessible; both preferences",
        "thresholds": {"high": HIGH_CONFIDENCE, "low": LOW_CONFIDENCE, "overlap_fraction": OVERLAP_FRACTION},
        "policies": {"B": "binary", "S": "three_state"},
        "requests": requests,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        previous = json.loads(destination.read_text(encoding="utf-8"))
        if previous != frozen:
            raise ValueError("冻结请求或参数与本次不同；请使用新的 --output 目录，保留旧实验")
    else:
        destination.write_text(json.dumps(frozen, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest, frozen


def routes_reachable(map_data, spot, dynamic_rectangles):
    """Offline reachability for scoring/explanation; never used to select a spot."""
    obstacles = map_data["static_obstacles_m"] + dynamic_rectangles
    car_geometry = TraversalGeometry(map_data["car_areas_m"] + [spot["rect_m"]], obstacles)
    ped_geometry = TraversalGeometry(map_data["ped_areas_m"], obstacles)
    car, ped = build_car_graph(map_data), build_ped_graph(map_data)
    center, access = as_point(spot["center_m"]), as_point(spot["car_access_m"])
    car.add_edge(access, center, weight=distance(access, center))
    car.add_edge(center, access, weight=distance(access, center))
    for graph, geometry in ((car, car_geometry), (ped, ped_geometry)):
        graph.remove_nodes_from([node for node in graph if not geometry.point_allowed(node)])
        graph.remove_edges_from([(a, b) for a, b in graph.edges if not geometry.segment_allowed(a, b)])
    pairs = ((car, map_data["entry_m"], center), (car, center, map_data["exit_m"]),
             (ped, spot["ped_access_m"], map_data["destination_m"]))
    return all(as_point(a) in graph and as_point(b) in graph and nx.has_path(graph, as_point(a), as_point(b))
               for graph, a, b in pairs)


def aggregate(rows):
    n = len(rows)
    r = sum(row["recommended"] for row in rows)
    f = sum(row["F"] for row in rows)
    v = sum(row["V"] for row in rows)
    assert 0 <= f <= r <= n and 0 <= v <= r
    return {"N": n, "R": r, "F": f, "V": v,
            "false_recommendation_rate": f / r if r else None,
            "effective_recommendation_rate": v / n if n else None,
            "unrecommended_rate": (n - r) / n if n else None,
            "failure_reasons": dict(Counter(row["reason_code"] for row in rows if not row["recommended"])),
            "requests_with_unknown": sum(row["unknown_count"] > 0 for row in rows),
            "requests_with_unknown_eligible": sum(row["unknown_excluded_count"] > 0 for row in rows)}


def conclusion(overall):
    b, s = overall["B"], overall["S"]
    if b["R"] == 0 or s["R"] == 0:
        return "至少一种策略无成功推荐，假空位推荐率为 N/A；不能据此宣称可靠性提高。"
    f_delta = s["false_recommendation_rate"] - b["false_recommendation_rate"]
    v_delta = s["effective_recommendation_rate"] - b["effective_recommendation_rate"]
    if abs(f_delta) < 1e-12 and abs(v_delta) < 1e-12:
        return "本组数据的假空位推荐率和有效推荐率相同，没有观察到三态策略的独立收益。"
    if f_delta < 0 and v_delta < 0:
        return "本组数据中三态策略降低假空位推荐率，同时降低有效推荐率，存在服务覆盖代价。"
    if f_delta < 0:
        return "本组数据中三态策略降低假空位推荐率；结论仅限于此次合成场景，不证明现实泛化。"
    if f_delta > 0:
        return "本组数据中三态策略的假空位推荐率更高，不能宣称改进。"
    return "本组数据中假空位推荐率未变化；有效推荐率变化需结合未推荐原因理解。"


def run(args):
    output = (ROOT / args.output).resolve()
    manifest_path = ROOT / "data/synthetic/test_manifest.json"
    # This file is committed before any real detection cache is opened below.
    manifest, frozen = freeze_requests(manifest_path, output / "frozen_requests.json")
    if args.freeze_only:
        print(f"已冻结 30 景 / 60 请求：{output / 'frozen_requests.json'}")
        return
    m = build_map()
    by_scene = {row["scene"]["scene_id"]: row for row in manifest}
    caches, cache_hashes = {}, {}
    for scene_id, row in by_scene.items():
        path = ROOT / "data/detection_cache" / f"{scene_id}.json"
        cache = json.loads(path.read_text(encoding="utf-8"))
        if (cache["scene_id"] != scene_id or cache["image_sha256"] != row["sha256"]
                or sha256(ROOT / row["image"]) != row["sha256"]):
            raise ValueError(f"图像或缓存身份不匹配：{scene_id}")
        if cache["snapshot"]["map_id"] != m["map_id"]:
            raise ValueError(f"缓存地图版本不匹配：{scene_id}")
        caches[scene_id], cache_hashes[scene_id] = cache, sha256(path)
    weights_hashes = {c["weights_sha256"] for c in caches.values()}
    if len(weights_hashes) != 1:
        raise ValueError("检测缓存混用了不同模型权重")
    rows, responses = [], []
    for task in frozen["requests"]:
        scene_id = task["scene_id"]
        original = caches[scene_id]["snapshot"]
        vehicles = by_scene[scene_id]["scene"]["vehicles"]
        snapshots = {strategy: estimate_occupancy(
            m, deepcopy(original["detections"]), original["scene_version"], original["frame_id"],
            valid=original["valid"], error=original.get("error"), policy=policy)
            for strategy, policy in frozen["policies"].items()}
        assert snapshots["B"]["obstacles_m"] == snapshots["S"]["obstacles_m"]
        for strategy in ("B", "S"):
            snapshot = snapshots[strategy]
            # A new plug-in instance and an idle session for every strategy/request.
            ctx = planning_context(m, snapshot, task["vehicle"], {"state": "idle", "session_id": None})
            req = deepcopy(task["request"])
            try:
                result = validate_planner_result(load_planner(args.planner).plan(deepcopy(ctx), deepcopy(req)), m)
                validate_returned_routes(ctx, req, result)
            except Exception as exc:
                result = failure("planner_unavailable", "planner_contract_error", f"{type(exc).__name__}: {exc}")
            recommended = result["status"] == "ok"
            spot = next((s for s in m["spots"] if s["spot_id"] == result["target_spot_id"]), None)
            # All truth access below is scoring after the frozen decision.
            occupied = recommended and any(rects_overlap(spot["rect_m"], v["rect_m"]) for v in vehicles)
            eligible = recommended and spot["spot_type"] == task["eligible_type"]
            reachable = bool(recommended and routes_reachable(m, spot, [v["rect_m"] for v in vehicles]))
            F = int(bool(recommended and occupied))
            V = int(bool(recommended and eligible and not occupied and reachable))
            unknown = sum(value["state"] == "unknown" for value in snapshot["occupancy"].values())
            excluded = sum(snapshot["occupancy"][s["spot_id"]]["state"] == "unknown"
                           for s in m["spots"] if s["spot_type"] == task["eligible_type"])
            unknown_candidates = [s for s in m["spots"] if snapshot["valid"] and s["spot_type"] == task["eligible_type"]
                                  and snapshot["occupancy"][s["spot_id"]]["state"] == "unknown"]
            unknown_blocked = sum(not routes_reachable(m, candidate, snapshot["obstacles_m"])
                                  for candidate in unknown_candidates)
            row = {"request_id": task["request_id"], "scene_id": scene_id, "group": task["group"],
                   "mode": req["prefer_mode"], "eligible_type": task["eligible_type"], "strategy": strategy,
                   "target_spot_id": result["target_spot_id"], "status": result["status"],
                   "reason_code": result["reason_code"], "recommended": int(recommended), "F": F, "V": V,
                   "truth_eligible": int(bool(eligible)), "truth_routes_reachable": int(reachable),
                   "unknown_count": unknown, "unknown_excluded_count": excluded,
                   "unknown_candidates_blocked_by_common_obstacles": unknown_blocked,
                   "unknown_candidates_reachable": len(unknown_candidates) - unknown_blocked,
                   "frame_valid": int(snapshot["valid"]),
                   "scene_version": snapshot["scene_version"], "frame_id": snapshot["frame_id"]}
            rows.append(row)
            responses.append({"request_id": task["request_id"], "strategy": strategy, "result": result})
    overall = {strategy: aggregate([r for r in rows if r["strategy"] == strategy]) for strategy in ("B", "S")}
    assert all(value["N"] == 60 for value in overall.values())
    subgroups = {}
    for dimension in ("group", "mode", "eligible_type"):
        subgroups[dimension] = {name: {strategy: aggregate([r for r in rows if r[dimension] == name and r["strategy"] == strategy])
                                     for strategy in ("B", "S")}
                               for name in sorted({r[dimension] for r in rows})}
    paired = defaultdict(dict)
    for row in rows:
        paired[row["request_id"]][row["strategy"]] = row
    trigger_analysis = {
        "S_requests_with_eligible_unknown": sum(pair["S"]["unknown_excluded_count"] > 0 and pair["S"]["frame_valid"]
                                               for pair in paired.values()),
        "invalid_frame_requests_per_strategy": sum(not pair["S"]["frame_valid"] for pair in paired.values()),
        "same_status_and_target_pairs": sum((p["B"]["status"], p["B"]["target_spot_id"]) ==
                                            (p["S"]["status"], p["S"]["target_spot_id"]) for p in paired.values()),
        "changed_status_or_target_pairs": sum((p["B"]["status"], p["B"]["target_spot_id"]) !=
                                              (p["S"]["status"], p["S"]["target_spot_id"]) for p in paired.values()),
        "eligible_unknown_candidates_already_blocked_by_common_obstacles": sum(
            p["S"]["unknown_candidates_blocked_by_common_obstacles"] for p in paired.values()),
        "eligible_unknown_candidates_with_three_routes": sum(
            p["S"]["unknown_candidates_reachable"] for p in paired.values()),
    }
    planner_module = importlib.util.find_spec(args.planner.partition(":")[0])
    planner_source = Path(planner_module.origin) if planner_module and planner_module.origin else None
    versions = {name: importlib.metadata.version(name) for name in ("networkx", "shapely", "ultralytics", "torch")}
    summary = {"completed_at_utc": datetime.now(timezone.utc).isoformat(), "planner": args.planner,
               "planner_source_sha256": sha256(planner_source) if planner_source and planner_source.is_file() else None,
               "python_version": sys.version, "package_versions": versions,
               "scope": "Reference adapter integration comparison; rerun after replacing Liang Ke's planner. Synthetic renderer only.",
               "independent_scenes": 30, "paired_requests": 60, "strategy_rows": 120,
               "test_manifest_sha256": frozen["test_manifest_sha256"],
               "frozen_requests_sha256": sha256(output / "frozen_requests.json"),
               "weights_sha256": next(iter(weights_hashes)), "cache_sha256": cache_hashes,
               "thresholds": frozen["thresholds"], "overall": overall, "subgroups": subgroups,
               "trigger_analysis": trigger_analysis, "conclusion": conclusion(overall)}
    with (output / "paired_requests.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "responses.json").write_text(json.dumps(responses, ensure_ascii=False, indent=2), encoding="utf-8")
    def ratio(value):
        return "N/A" if value is None else f"{value:.2%}"
    table = "\n".join(f"| {s} | {a['N']} | {a['R']} | {a['F']} | {a['V']} | {ratio(a['false_recommendation_rate'])} | {ratio(a['effective_recommendation_rate'])} | {ratio(a['unrecommended_rate'])} |"
                      for s, a in overall.items())
    report = f"""# I01 参考适配器配对对照实测

{summary['conclusion']}

本次使用 `{args.planner}` 与真实 YOLO 检测缓存，30 个独立合成场景，每景两种偏好，共 60 个配对请求。B/S 分别计算 60 次，共 120 行结果。此为可移交的参考适配器联调结果；梁可替换规划模块后须重新运行，不代表未提供的代码已经通过验收。

| 策略 | N | R | F | V | 假空位 F/R | 有效 V/N | 未推荐 |
|---|---:|---:|---:|---:|---:|---:|---:|
{table}

请求先写入 `frozen_requests.json`，然后读取检测缓存。两组共用权重、图像、全部低阈值以上车框障碍、车辆登记、资格规则、双模式排序和路网；唯一变化为二态或三态占用。每次使用新的 idle 上下文，不提交停车。无推荐结果仍计入 N；R=0 时 F/R 为 N/A。

F 为成功推荐中目标实际占用次数；V 为成功推荐中类型满足、真实空闲且三条所需路径在真实车体障碍下可达的次数。真值在决策完成后离线计分，没有提供给规划器。无效缓存帧对两策略均不可用，不转为空场。

S 中合法类型存在 unknown 的请求数为 {trigger_analysis['S_requests_with_eligible_unknown']}；状态与目标均相同的配对为 {trigger_analysis['same_status_and_target_pairs']}，有变化的为 {trigger_analysis['changed_status_or_target_pairs']}。按请求累计，unknown 候选中已有 {trigger_analysis['eligible_unknown_candidates_already_blocked_by_common_obstacles']} 个也被两组共同的路径障碍规则排除，仍具三条通路的有 {trigger_analysis['eligible_unknown_candidates_with_three_routes']} 个。这样区分策略没有触发、触发后被共同路径检查抵消的情况。逐请求未知数与筛除数在 CSV；分组、模式和需求类型统计在 `summary.json`。unknown 不能发现完全漏检。

结论仅覆盖固定地图、同一程序绘图器的独立种子，不证明真实照片泛化、显著性或现实停车效率。复跑：`python scripts/run_pairing.py`；接梁可模块后使用 `python scripts/run_pairing.py --planner liang_planner:Planner --output reports/i01_liang`。
"""
    (output / "RESULTS.md").write_text(report, encoding="utf-8")
    print(json.dumps({"output": str(output), "overall": overall, "conclusion": summary["conclusion"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="I01 paired B/S experiment using cached real YOLO detections")
    parser.add_argument("--planner", default="parking_hu.reference_planner:Planner")
    parser.add_argument("--output", default="reports/i01_pairing")
    parser.add_argument("--freeze-only", action="store_true")
    run(parser.parse_args())

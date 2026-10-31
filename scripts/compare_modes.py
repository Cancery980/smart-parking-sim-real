"""Compare two preferences with a nearest-entry baseline on identical candidates.

Offline reference-adapter evaluation only. No training or runtime policy changes.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from parking_hu.contracts import planning_context, validate_planner_result
from parking_hu.geometry import TraversalGeometry, path_length, rects_overlap
from parking_hu.map_data import build_map
from parking_hu.occupancy import estimate_occupancy
from parking_hu.reference_planner import (Planner, _cm, _filter_graph, build_car_graph,
                                         build_ped_graph, validate_returned_routes)
from run_pairing import routes_reachable

METHODS = ("near_destination", "near_exit", "nearest_entry")
LABELS = {"near_destination": "少走路", "near_exit": "近出口", "nearest_entry": "最近入口基线"}
METRICS = ("drive_in", "drive_out", "walk_to_destination")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def selected_routes(context, spot_id):
    m, snapshot = context["map"], context["snapshot"]
    spot = next(s for s in m["spots"] if s["spot_id"] == spot_id)
    obstacles = m["static_obstacles_m"] + snapshot["obstacles_m"]
    car_geo = TraversalGeometry(m["car_areas_m"], obstacles)
    ped_geo = TraversalGeometry(m["ped_areas_m"], obstacles)
    return Planner._spot_routes(m, spot, _filter_graph(build_car_graph(m), car_geo),
                                _filter_graph(build_ped_graph(m), ped_geo), obstacles, ped_geo)


def eligible(spot, vehicle, req):
    if req["needs_charging"] and vehicle["power_type"] != "new_energy":
        return False
    if req["needs_accessible"] and vehicle["accessible_permission"] is not True:
        return False
    if req["needs_charging"] and req["needs_accessible"]:
        return False
    kind = "accessible" if req["needs_accessible"] else "charging" if req["needs_charging"] else "normal"
    return spot["spot_type"] == kind


def aggregate(rows, common_ids):
    success = [r for r in rows if r["recommended"]]
    common = [r for r in success if r["request_id"] in common_ids]
    return {"N": len(rows), "recommended": len(success), "unrecommended": len(rows) - len(success),
            "distance_sample_count": len(common),
            "mean_distances_m": {k: statistics.mean(r[k] for r in common) if common else None for k in METRICS},
            "median_distances_m": {k: statistics.median(r[k] for r in common) if common else None for k in METRICS},
            "constraint_violation_requests": sum(r["constraint_violation"] for r in success),
            "constraint_violations_by_type": {k: sum(r[k] for r in success) for k in
                                             ("eligibility_violation", "observed_free_violation", "perceived_route_violation")},
            "truth_violation_requests": sum(r["truth_violation"] for r in success),
            "truth_occupied_recommendations": sum(r["truth_occupied"] for r in success),
            "truth_route_violation_requests": sum(r["truth_route_violation"] for r in success),
            "failure_reasons": dict(Counter(r["reason_code"] for r in rows if not r["recommended"]))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="reports/mode_comparison")
    args = parser.parse_args()
    output = ROOT / args.output
    output.mkdir(parents=True, exist_ok=True)
    frozen_path = ROOT / "reports/i01_pairing/frozen_requests.json"
    manifest_path = ROOT / "data/synthetic/test_manifest.json"
    frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert frozen["test_manifest_sha256"] == digest(manifest_path)
    tasks = frozen["requests"]
    assert len(tasks) == 60 and len({t["scene_id"] for t in tasks}) == 30
    manifest_by_scene = {row["scene"]["scene_id"]: row for row in manifest}
    protocol = {
        "version": "mode-comparison-v1", "frozen_requests_sha256": digest(frozen_path),
        "test_manifest_sha256": digest(manifest_path), "policy": "three_state",
        "strategies": {
            "near_destination": ["walk_to_destination", "drive_in", "drive_out", "numeric_spot_id"],
            "near_exit": ["drive_out", "walk_to_destination", "drive_in", "numeric_spot_id"],
            "nearest_entry": ["drive_in", "walk_to_destination", "drive_out", "numeric_spot_id"],
        },
        "distance": "Directed/undirected graph path length in metres; round to integer centimetres for ranking",
        "fairness": "Same three-state observed-free, eligible, three-route-reachable candidate set. Truth only scores decisions.",
        "sample": "60 inherited requests evaluated under all 3 rankings; 30 independent scenes. Also report one context per scene.",
    }
    protocol_path = output / "protocol.json"
    if protocol_path.exists():
        assert json.loads(protocol_path.read_text(encoding="utf-8")) == protocol, "Use a new output for changed protocol"
    else:
        protocol_path.write_text(json.dumps(protocol, ensure_ascii=False, indent=2), encoding="utf-8")
    m = build_map()
    rows, decisions, candidate_evidence = [], [], {}
    weights, cache_hashes = set(), {}
    for task in tasks:
        scene_id = task["scene_id"]
        cache_path = ROOT / "data/detection_cache" / (scene_id + ".json")
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        source = manifest_by_scene[scene_id]
        assert cache["image_sha256"] == task["image_sha256"] == source["sha256"] == digest(ROOT / source["image"])
        weights.add(cache["weights_sha256"])
        cache_hashes[scene_id] = digest(cache_path)
        old = cache["snapshot"]
        snapshot = estimate_occupancy(m, deepcopy(old["detections"]), old["scene_version"], old["frame_id"],
                                      valid=old["valid"], error=old.get("error"), policy="three_state")
        assert snapshot["thresholds"] == frozen["thresholds"], "Do not silently change the frozen perception configuration"
        ctx = planning_context(m, snapshot, task["vehicle"], {"state": "idle", "session_id": None})
        # Real public reference adapter establishes the exact lawful/reachable set.
        outputs = {}
        for method in ("near_destination", "near_exit"):
            req = {**task["request"], "prefer_mode": method}
            result = validate_planner_result(Planner().plan(deepcopy(ctx), req), m)
            validate_returned_routes(ctx, req, result)
            outputs[method] = result
        left, right = outputs["near_destination"], outputs["near_exit"]
        assert left["status"] == right["status"]
        if left["status"] == "ok":
            left_ids = {r["spot_id"] for r in left["ranking"]}
            assert left_ids == {r["spot_id"] for r in right["ranking"]}
            # Recompute raw lengths from the same graph, without touching occupancy.
            candidates = {}
            for sid in sorted(left_ids):
                routes = selected_routes(ctx, sid)
                assert routes is not None
                candidates[sid] = {"routes": routes, "distances": {k: path_length(v) for k, v in routes.items()}}
            selections = {}
            for method in METHODS:
                order = protocol["strategies"][method][:-1]
                selections[method] = min(candidates, key=lambda sid: tuple(_cm(candidates[sid]["distances"][k]) for k in order) + (int(sid[1:]),))
            assert left["target_spot_id"] == selections["near_destination"]
            assert right["target_spot_id"] == selections["near_exit"]
            sid = selections["nearest_entry"]
            chosen = candidates[sid]
            outputs["nearest_entry"] = {"status": "ok", "reason_code": "nearest_entry_recommended",
                "message": "离线最近入口基线", "target_spot_id": sid, "path_m": chosen["routes"]["drive_in"],
                "routes": chosen["routes"], "distances_m": {k: round(v, 2) for k, v in chosen["distances"].items()}}
            validate_returned_routes(ctx, task["request"], outputs["nearest_entry"])
            candidate_record = {sid: {k: round(v, 8) for k, v in record["distances"].items()} for sid, record in candidates.items()}
        else:
            outputs["nearest_entry"] = deepcopy(left)
            candidate_record = {}
        candidate_key = fingerprint(candidate_record)
        candidate_evidence[scene_id] = {"frame_id": snapshot["frame_id"], "candidate_set_sha256": candidate_key,
                                        "candidates": candidate_record}
        # Decisions are complete before reading geometry truth for scoring below.
        truth_rects = [v["rect_m"] for v in source["scene"]["vehicles"]]
        for method, result in outputs.items():
            ok = result["status"] == "ok"
            violations = dict(eligibility_violation=0, observed_free_violation=0, perceived_route_violation=0,
                              truth_occupied=0, truth_route_violation=0)
            if ok:
                spot = next(s for s in m["spots"] if s["spot_id"] == result["target_spot_id"])
                violations["eligibility_violation"] = int(not eligible(spot, task["vehicle"], task["request"]))
                violations["observed_free_violation"] = int(snapshot["occupancy"][spot["spot_id"]]["state"] != "free")
                try:
                    validate_returned_routes(ctx, task["request"], result)
                except ValueError:
                    violations["perceived_route_violation"] = 1
                violations["truth_occupied"] = int(any(rects_overlap(spot["rect_m"], r) for r in truth_rects))
                violations["truth_route_violation"] = int(not routes_reachable(m, spot, truth_rects))
            row = {"request_id": task["request_id"], "scene_id": scene_id, "group": task["group"],
                   "original_preference": task["request"]["prefer_mode"], "strategy": method,
                   "status": result["status"], "reason_code": result["reason_code"],
                   "target_spot_id": result["target_spot_id"], "recommended": int(ok),
                   "candidate_count": len(candidate_record), "candidate_set_sha256": candidate_key,
                   **{k: result["distances_m"][k] if ok else None for k in METRICS}, **violations,
                   "constraint_violation": int(any(violations[k] for k in ("eligibility_violation", "observed_free_violation", "perceived_route_violation"))),
                   "truth_violation": int(any(violations[k] for k in ("eligibility_violation", "truth_occupied", "truth_route_violation")))}
            rows.append(row)
            decisions.append({"request_id": task["request_id"], "strategy": method,
                              "result": {k: v for k, v in result.items() if k != "ranking"}})
    assert len(weights) == 1
    by_request = defaultdict(dict)
    for row in rows:
        by_request[row["request_id"]][row["strategy"]] = row
    common = {rid for rid, outcomes in by_request.items() if all(outcomes[m]["recommended"] for m in METHODS)}
    assert all(len({r["candidate_set_sha256"] for r in result.values()}) == 1 for result in by_request.values())
    overall = {method: aggregate([r for r in rows if r["strategy"] == method], common) for method in METHODS}
    # One of the two inherited request IDs per scene: prevents pseudoreplication.
    unique_ids = {next(t["request_id"] for t in tasks if t["scene_id"] == sid) for sid in sorted(manifest_by_scene)}
    unique_rows = [r for r in rows if r["request_id"] in unique_ids]
    unique = {method: aggregate([r for r in unique_rows if r["strategy"] == method], common & unique_ids) for method in METHODS}
    paired = {}
    for method in METHODS[:2]:
        sample = [by_request[rid] for rid in sorted(common & unique_ids)]
        paired[method] = {"independent_common_scenes": len(sample), "delta_vs_nearest_entry_m": {
            k: {"mean": statistics.mean(r[method][k] - r["nearest_entry"][k] for r in sample) if sample else None,
                "better": sum(r[method][k] < r["nearest_entry"][k] - .005 for r in sample),
                "equal": sum(abs(r[method][k] - r["nearest_entry"][k]) <= .005 for r in sample),
                "worse": sum(r[method][k] > r["nearest_entry"][k] + .005 for r in sample)} for k in METRICS}}
    summary = {"completed_at_utc": datetime.now(timezone.utc).isoformat(),
               "scope": "Reference-adapter comparison, not Liang Ke's implementation; same synthetic test scenes, no new training.",
               "independent_scenes": 30, "frozen_requests": 60, "strategy_rows": len(rows),
               "common_recommended_requests": len(common), "common_recommended_independent_scenes": len(common & unique_ids),
               "baseline_definition": protocol["strategies"]["nearest_entry"], "policy": "three_state",
               "thresholds": frozen["thresholds"], "script_sha256": digest(Path(__file__)),
               "weights_sha256": next(iter(weights)), "cache_sha256": cache_hashes,
               "protocol_sha256": digest(protocol_path), "frozen_requests_sha256": digest(frozen_path),
               "overall_60_requests": overall, "independent_30_scenes": unique, "paired_vs_baseline": paired,
               "by_group": {group: {method: aggregate([r for r in unique_rows if r["group"] == group and r["strategy"] == method], common & unique_ids)
                                     for method in METHODS} for group in ("normal", "occluded", "boundary")},
               "limitations": ["Distances exclude unrecommended requests; their counts are always reported.",
                               "Each scene's original two preferences are repeated comparisons, not independent samples.",
                               "Results do not establish real-world congestion reduction or statistical significance."]}
    for filename, value in (("summary.json", summary), ("candidate_sets.json", candidate_evidence), ("decisions.json", decisions)):
        (output / filename).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    with (output / "requests.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    table = "\n".join(f"| {LABELS[m]} | {a['recommended']}/{a['N']} | {a['unrecommended']} | {a['mean_distances_m']['drive_in']:.2f} | {a['mean_distances_m']['drive_out']:.2f} | {a['mean_distances_m']['walk_to_destination']:.2f} | {a['constraint_violation_requests']} | {a['truth_violation_requests']} |" for m, a in unique.items())
    text = f"""# 双模式与最近入口基线的路网对照

已有 I01 对照比较二态与三态占用，没有覆盖最近入口排序。本次补充规格书规定的距离和约束指标，复用真实 YOLO 检测缓存与三态观测空闲，使用对接参考适配器，没有改动应用核心，也不属于梁可尚未接入的代码验收。

三个策略在每个请求使用同一资格、需求、观测空闲和三条通路均可达的候选集合。最近入口定义为车行入场路网距离最小，随后按步行、出场、车位数字 ID 破除并列；距离排序前四舍五入为厘米。它不是入口欧氏直线距离。

保留 60 个既有冻结请求，每个评估三个排序，共 {len(rows)} 行。只有 30 个独立场景；同景两种原始偏好不计作独立样本。下表按每景一个上下文去重，三策略共同可推荐的 {len(common & unique_ids)} 景比较平均距离，全部 30 景仍用于推荐与未推荐计数。

| 策略 | 推荐/场景 | 未推荐 | 入场均值 m | 出场均值 m | 步行均值 m | 感知规则违反数 | 真值核对违反数 |
|---|---:|---:|---:|---:|---:|---:|---:|
{table}

少走路相对最近入口，平均步行距离变化 {paired['near_destination']['delta_vs_nearest_entry_m']['walk_to_destination']['mean']:.2f} m，入场距离变化 {paired['near_destination']['delta_vs_nearest_entry_m']['drive_in']['mean']:.2f} m。近出口相对最近入口，平均出场距离变化 {paired['near_exit']['delta_vs_nearest_entry_m']['drive_out']['mean']:.2f} m，入场距离变化 {paired['near_exit']['delta_vs_nearest_entry_m']['drive_in']['mean']:.2f} m。负数表示距离减少，正数表示增加；各模式优化自己的目标，并非所有距离同时更短。

感知规则违反数按成功推荐中出现资格/类型不符、非观测空闲、感知障碍或路网非法的请求计数；同一请求多种问题仅记一次，并另存分项。真值核对另外统计真实占用、真实障碍不可达或资格不符，仅在决策后评分，未用真值修补推荐。

`requests.csv` 保存逐请求目标、三个距离、失败原因、候选集合哈希和各类违反；`candidate_sets.json` 可核对共享候选；`decisions.json` 保留实际路线；`summary.json` 同时给出 60 请求口径、30 场景去重口径、分组和配对变化。复跑命令为 `python scripts/compare_modes.py`。

结论只适用于当前固定路网和合成场景，不推断现实拥堵改善，不做显著性或独立泛化声明。最终与梁可模块合并后应重新运行对照。
"""
    (output / "RESULTS.md").write_text(text, encoding="utf-8")
    if common & unique_ids:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "DejaVu Sans"]
        plt.rcParams["axes.unicode_minus"] = False
        order = ("nearest_entry", "near_destination", "near_exit")
        fig, axes = plt.subplots(1, 3, figsize=(11, 4.0))
        for ax, metric, title in zip(axes, METRICS, ("入场距离", "出场距离", "步行至电梯")):
            values = [unique[k]["mean_distances_m"][metric] for k in order]
            bars = ax.barh([LABELS[k] for k in order], values, color=["#718096", "#167c80", "#4265af"])
            ax.invert_yaxis(); ax.set_xlim(0, max(values) * 1.23)
            ax.set_xlabel("平均路网距离 / 米"); ax.set_title(title, fontsize=12)
            ax.spines[["top", "right"]].set_visible(False)
            for bar, value in zip(bars, values):
                ax.text(value + max(values) * .025, bar.get_y() + bar.get_height() / 2,
                        f"{value:.2f}", va="center", fontsize=9)
        fig.suptitle("同一合法可达候选集合下的三种排序", fontsize=14)
        fig.text(.5, .025, f"30 个独立合成场景；共同可推荐 {len(common & unique_ids)} 景；三策略未推荐及约束违反另行统计", ha="center", fontsize=9)
        fig.tight_layout(rect=(0, .07, 1, .92))
        fig.savefig(output / "distance_comparison.png", dpi=160)
        plt.close(fig)
        with (output / "RESULTS.md").open("a", encoding="utf-8") as stream:
            stream.write("\n![三种排序的平均路网距离](distance_comparison.png)\n")
    print(json.dumps({"overall_60_requests": overall, "independent_30_scenes": unique, "paired_vs_baseline": paired}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

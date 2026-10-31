"""Strict frame validation and pure three-state occupancy estimation."""
from __future__ import annotations

import math

HIGH_CONFIDENCE = 0.50
LOW_CONFIDENCE = 0.20
OVERLAP_FRACTION = 0.05
BOUNDARY_EPS_M = 1e-6


def _intersection(a, b):
    return max(0.0, min(a[2],b[2])-max(a[0],b[0])) * max(0.0,min(a[3],b[3])-max(a[1],b[1]))


def estimate_occupancy(map_data, detections, scene_version, frame_id, *, valid=True, error=None, policy="three_state"):
    """Return a complete immutable-by-convention snapshot, never scene truth.

    A malformed detection invalidates the whole frame, including low-confidence
    detections. Both comparison policies preserve the same >=.20 obstacles.
    """
    if policy not in {"three_state", "binary"}:
        raise ValueError("policy must be three_state or binary")
    snapshot = {"map_id": map_data["map_id"], "scene_version": scene_version,
                "frame_id": frame_id, "valid": False, "error": error,
                "occupancy": {s["spot_id"]:{"state":"unknown","reason":"frame_invalid","detection_ids":[]}
                              for s in map_data["spots"]}, "detections": [], "obstacles_m": [],
                "policy": policy,
                "thresholds": {"high": HIGH_CONFIDENCE, "low": LOW_CONFIDENCE,
                               "overlap_fraction": OVERLAP_FRACTION}}
    if not valid:
        snapshot["error"] = error or "perception_unavailable"
        return snapshot
    if isinstance(scene_version, bool) or not isinstance(scene_version,int) or scene_version < 0 or not isinstance(frame_id,str) or not frame_id:
        snapshot["error"] = "invalid_frame_metadata"
        return snapshot
    if not isinstance(detections, list):
        snapshot["error"] = "invalid_detections"
        return snapshot
    width,height = map_data["image_size"]
    ppm = map_data["px_per_m"]
    ids = set()
    clean = []
    for det in detections:
        if not isinstance(det, dict):
            snapshot["error"] = "invalid_detection"
            return snapshot
        did,bbox,confidence = det.get("detection_id"),det.get("bbox_xyxy_px"),det.get("confidence")
        if not isinstance(did,str) or not did or did in ids or det.get("class_name") != "car":
            snapshot["error"] = "invalid_detection_identity"
            return snapshot
        if (not isinstance(bbox,(list,tuple)) or len(bbox)!=4 or
            any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in bbox) or
            isinstance(confidence,bool) or not isinstance(confidence,(int,float)) or
            not math.isfinite(confidence) or not 0 <= confidence <= 1):
            snapshot["error"] = "invalid_detection_fields"
            return snapshot
        if not (0 <= bbox[0] < bbox[2] <= width and 0 <= bbox[1] < bbox[3] <= height):
            snapshot["error"] = "bbox_outside_frame"
            return snapshot
        if det.get("frame_id",frame_id) != frame_id or det.get("scene_version",scene_version) != scene_version:
            snapshot["error"] = "detection_version_mismatch"
            return snapshot
        ids.add(did)
        clean.append({**det,"bbox_xyxy_px":list(bbox),"bbox_m":[float(v)/ppm for v in bbox]})
    retained = [d for d in clean if d["confidence"] >= LOW_CONFIDENCE]
    occupancy = {}
    for spot in map_data["spots"]:
        r = spot["rect_m"]
        occupied_ids, unknown_ids = [], []
        for det in retained:
            b = det["bbox_m"]
            cx,cy = (b[0]+b[2])/2,(b[1]+b[3])/2
            if det["confidence"] >= HIGH_CONFIDENCE and r[0]+BOUNDARY_EPS_M < cx < r[2]-BOUNDARY_EPS_M and r[1]+BOUNDARY_EPS_M < cy < r[3]-BOUNDARY_EPS_M:
                occupied_ids.append(det["detection_id"])
            if _intersection(r,b) + 1e-12 >= OVERLAP_FRACTION * (r[2]-r[0])*(r[3]-r[1]):
                unknown_ids.append(det["detection_id"])
        if occupied_ids:
            state,reason,evidence = "occupied","high_confidence_center",occupied_ids
        elif unknown_ids and policy == "three_state":
            state,reason,evidence = "unknown","ambiguous_overlap",unknown_ids
        else:
            state,reason,evidence = "free","observed_free",[]
        occupancy[spot["spot_id"]] = {"state":state,"reason":reason,"detection_ids":evidence}
    snapshot.update(valid=True,error=None,occupancy=occupancy,detections=clean,
                    obstacles_m=[d["bbox_m"][:] for d in retained])
    return snapshot

"""Offline metrics only. This module may read scene truth; runtime may not."""
from __future__ import annotations

from collections import Counter

from .scenes import truth_occupancy


def bbox_iou(a, b):
    intersection = max(0,min(a[2],b[2])-max(a[0],b[0])) * max(0,min(a[3],b[3])-max(a[1],b[1]))
    union = (a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-intersection
    return intersection/union if union > 0 else 0.0


def detection_counts(detections, truth_vehicles, px_per_m=32, conf=.20, iou=.50):
    """Confidence-ordered one-to-one greedy matches at fixed IoU, not mAP."""
    gt = [[v*px_per_m for v in vehicle["rect_m"]] for vehicle in truth_vehicles]
    used = set()
    tp = fp = 0
    for det in sorted(detections,key=lambda d:d["confidence"],reverse=True):
        if det["confidence"] < conf:
            continue
        candidates = [(bbox_iou(det["bbox_xyxy_px"],box),i) for i,box in enumerate(gt) if i not in used]
        match = max(candidates,default=(0,None))
        if match[0] >= iou:
            tp += 1
            used.add(match[1])
        else:
            fp += 1
    return {"tp":tp,"fp":fp,"fn":len(gt)-len(used)}


def occupancy_counts(map_data, scene, snapshot):
    truth = truth_occupancy(map_data,scene)
    counts = Counter()
    for sid,expected in truth.items():
        counts[f"{expected}->{snapshot['occupancy'][sid]['state']}"] += 1
    return dict(counts)


def summarize_counts(counts):
    tp,fp,fn = counts["tp"],counts["fp"],counts["fn"]
    return {**dict(counts),"precision":tp/(tp+fp) if tp+fp else None,
            "recall":tp/(tp+fn) if tp+fn else None}

"""Immutable-layout generator for the course specification demo52_v12.

Coordinates are metres, origin at top left. This module has no model dependency.
"""
from __future__ import annotations

from collections import Counter
from math import isfinite


def _contains(rect, point, eps=1e-9):
    x1, y1, x2, y2 = rect
    return x1 - eps <= point[0] <= x2 + eps and y1 - eps <= point[1] <= y2 + eps


def _overlap(a, b):
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0.0, min(a[3], b[3]) - max(a[1], b[1])
    )


def validate_map(data: dict) -> None:
    """Reject malformed or inconsistent static layouts, independently of perception."""
    if data.get("map_id") != "demo52_v12":
        raise ValueError("未知地图版本")
    if (data.get("width_m"), data.get("height_m"), data.get("px_per_m")) != (52.0, 32.0, 32):
        raise ValueError("地图尺寸或比例不符合 demo52_v12")
    if data.get("image_size") != [1664, 1024]:
        raise ValueError("输入图像尺寸必须为 1664×1024")
    spots = data.get("spots", [])
    if len(spots) != 52 or len({s["spot_id"] for s in spots}) != 52:
        raise ValueError("地图必须包含 52 个唯一车位")
    if Counter(s["spot_type"] for s in spots) != {"normal": 40, "accessible": 4, "charging": 8}:
        raise ValueError("车位类型数量必须为 40/4/8")
    if {s["spot_id"] for s in spots} != {f"P{i:03}" for i in range(1, 53)}:
        raise ValueError("车位编号不完整")
    for i, spot in enumerate(spots):
        rect = spot["rect_m"]
        if len(rect) != 4 or not all(isfinite(v) for v in rect):
            raise ValueError("车位矩形含无效坐标")
        x1, y1, x2, y2 = rect
        if not (0 <= x1 < x2 <= 52 and 0 <= y1 < y2 <= 32):
            raise ValueError("车位矩形超出地图")
        if any(_overlap(rect, other["rect_m"]) > 1e-9 for other in spots[i + 1:]):
            raise ValueError("车位内部重叠")
        if any(_overlap(rect, obstacle) > 1e-9 for obstacle in data["static_obstacles_m"]):
            raise ValueError("车位与墙柱重叠")
        if not any(_contains(area, spot["car_access_m"]) for area in data["car_areas_m"]):
            raise ValueError("车行接入点不在车道")
        if not any(_contains(area, spot["ped_access_m"]) for area in data["ped_areas_m"]):
            raise ValueError("人行接近点不在人行可行域")
        if _contains(rect, spot["ped_access_m"]):
            raise ValueError("人行接近点不得位于车位内")
        for endpoint in (spot["car_access_m"], spot["ped_access_m"]):
            if any(_contains(obstacle, endpoint, eps=0) for obstacle in data["static_obstacles_m"]):
                raise ValueError("接入点被墙柱阻挡")


def build_map() -> dict:
    """Return a fresh, JSON-serializable copy of the exact prescribed layout."""
    # (first ID, number, type, x0, y0, width, pedestrian y, driving y)
    groups = [
        (1, 9, "normal", 5.0, 1.3, 2.5, 0.7, 8.35),
        (41, 4, "accessible", 27.5, 1.3, 3.5, 0.7, 8.35),
        (10, 13, "normal", 5.0, 10.1, 2.5, 16.0, 8.35),
        (23, 13, "normal", 5.0, 16.6, 2.5, 16.0, 23.65),
        (36, 5, "normal", 5.0, 25.4, 2.5, 31.3, 23.65),
        (45, 8, "charging", 17.5, 25.4, 2.5, 31.3, 23.65),
    ]
    spots = []
    for first, count, kind, x0, y0, width, ped_y, car_y in groups:
        for index in range(count):
            x1, x2, y2 = x0 + width * index, x0 + width * (index + 1), round(y0 + 5.3, 9)
            center_x = (x1 + x2) / 2
            spots.append({
                "spot_id": f"P{first + index:03}", "spot_type": kind,
                "rect_m": [x1, y0, x2, y2],
                "polygon_m": [[x1, y0], [x2, y0], [x2, y2], [x1, y2]],
                "center_m": [center_x, round((y0 + y2) / 2, 9)],
                "car_access_m": [center_x, car_y],
                "ped_access_m": [center_x, ped_y],
            })
    result = {
        "map_id": "demo52_v12", "width_m": 52.0, "height_m": 32.0,
        "px_per_m": 32, "image_size": [1664, 1024],
        "spots": sorted(spots, key=lambda s: s["spot_id"]),
        "car_areas_m": [
            [0.5, 6.6, 47.5, 10.1], [0.5, 21.9, 47.5, 25.4],
            [0.5, 6.6, 4.0, 25.4], [44.0, 6.6, 47.5, 25.4],
            [0.0, 14.25, 4.0, 17.75], [44.0, 14.25, 52.0, 17.75],
        ],
        "ped_areas_m": [
            [5.0, 0.1, 43.2, 1.3], [5.0, 15.4, 43.2, 16.6],
            [5.0, 30.7, 43.2, 31.9], [42.0, 0.1, 43.2, 31.9],
        ],
        "static_obstacles_m": [
            [0.0, 0.0, 52.0, 0.1], [0.0, 31.9, 52.0, 32.0],
            [0.0, 0.0, 0.1, 14.25], [0.0, 17.75, 0.1, 32.0],
            [51.9, 0.0, 52.0, 14.25], [51.9, 17.75, 52.0, 32.0],
            [49.0, 8.0, 50.0, 9.0], [49.0, 23.0, 50.0, 24.0],
        ],
        "entry_m": [2.0, 16.0], "exit_m": [50.0, 16.0],
        "destination_m": [42.6, 4.0], "destination_id": "elevator_1",
        "crossings_m": [[42.0, 6.6, 43.2, 10.1], [42.0, 21.9, 43.2, 25.4]],
    }
    validate_map(result)
    return result

"""Small geometry helpers shared by the reference adapter and its checks."""
from __future__ import annotations

from math import hypot, isfinite

from shapely.geometry import LineString, Point, box
from shapely.ops import unary_union

EPS = 1e-9


def as_point(value) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("坐标必须为二元数组")
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and isfinite(v) for v in value):
        raise ValueError("坐标必须为有限数值")
    return round(float(value[0]), 9), round(float(value[1]), 9)


def as_rect(value) -> tuple[float, float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError("矩形必须包含四个坐标")
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and isfinite(v) for v in value):
        raise ValueError("矩形必须为有限数值")
    x1, y1, x2, y2 = map(float, value)
    if x1 >= x2 or y1 >= y2:
        raise ValueError("矩形尺寸必须为正")
    return x1, y1, x2, y2


def rect_intersection_area(a, b) -> float:
    a, b = as_rect(a), as_rect(b)
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0.0, min(a[3], b[3]) - max(a[1], b[1])
    )


def rects_overlap(a, b) -> bool:
    """Positive-area overlap: boundary contact alone is not occupancy."""
    return rect_intersection_area(a, b) > 0.0


def m_to_px(point, px_per_m=32) -> list[float]:
    return [float(v) * px_per_m for v in point]


def px_to_m(point, px_per_m=32) -> list[float]:
    return [float(v) / px_per_m for v in point]


def distance(a, b) -> float:
    return hypot(b[0] - a[0], b[1] - a[1])


def path_length(path) -> float:
    return sum(distance(a, b) for a, b in zip(path, path[1:]))


def rect_union(rectangles):
    return unary_union([box(*as_rect(rect)) for rect in rectangles])


class TraversalGeometry:
    """Validate whole lines against an allowed region and closed obstacles.

    A tiny numerical tolerance is applied only to allowed-region containment.
    Obstacle boundary contact is rejected, including at route endpoints.
    """

    def __init__(self, areas, obstacles):
        self.allowed = rect_union(areas).buffer(EPS)
        self.obstacles = rect_union(obstacles)

    def point_allowed(self, point) -> bool:
        p = Point(as_point(point))
        return self.allowed.covers(p) and not self.obstacles.intersects(p)

    def segment_allowed(self, a, b) -> bool:
        a, b = as_point(a), as_point(b)
        shape = Point(a) if a == b else LineString([a, b])
        return self.allowed.covers(shape) and not self.obstacles.intersects(shape)

    def path_allowed(self, path) -> bool:
        if not path:
            return False
        if len(path) == 1:
            return self.point_allowed(path[0])
        return all(self.segment_allowed(a, b) for a, b in zip(path, path[1:]))

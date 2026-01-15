from typing import List, Dict, Tuple, Optional
import math

Point2D = Tuple[float, float]

def _point_on_segment(p: Point2D, a: Point2D, b: Point2D, eps: float = 1e-9) -> bool:
    (px, py), (ax, ay), (bx, by) = p, a, b
    cross = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
    if abs(cross) > eps:
        return False
    dot = (px - ax) * (px - bx) + (py - ay) * (py - by)
    return dot <= eps

def point_in_polygon(p: Point2D, poly: List[Point2D]) -> bool:
    """Ray casting; inside OR on boundary -> True"""
    x, y = p
    n = len(poly)
    if n < 3:
        return False

    # boundary
    for i in range(n):
        a = poly[i]
        b = poly[(i + 1) % n]
        if _point_on_segment(p, a, b):
            return True

    inside = False
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xinters = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if xinters >= x:
                inside = not inside
    return inside

def _dist_point_to_segment(p: Point2D, a: Point2D, b: Point2D) -> float:
    (px, py), (ax, ay), (bx, by) = p, a, b
    vx, vy = bx - ax, by - ay
    wx, wy = px - ax, py - ay
    vv = vx * vx + vy * vy
    if vv == 0:
        return math.hypot(px - ax, py - ay)
    t = (wx * vx + wy * vy) / vv
    t = max(0.0, min(1.0, t))
    cx, cy = ax + t * vx, ay + t * vy
    return math.hypot(px - cx, py - cy)

def distance_point_to_polygon(p: Point2D, poly: List[Point2D]) -> float:
    """Shortest distance to polygon boundary. If inside/on boundary -> 0"""
    if point_in_polygon(p, poly):
        return 0.0
    n = len(poly)
    best = float("inf")
    for i in range(n):
        a = poly[i]
        b = poly[(i + 1) % n]
        best = min(best, _dist_point_to_segment(p, a, b))
    return best

def bearing_to_dir8(bearing_deg: float) -> str:
    """
    bearing: 北=0°, 顺时针：东=90°, 南=180°, 西=270°
    """
    dirs = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"]
    # 每个方向45度，偏移22.5度做四舍五入
    idx = int((bearing_deg + 22.5) // 45) % 8
    return dirs[idx]

def direction_from_center(point: Point2D, center: Point2D) -> str:
    """
    返回 point 相对 center 的八方向。
    坐标系：x向东增大，y向南增大（0,0在西北角）。
    """
    px, py = point
    cx, cy = center
    dx = px - cx               # +东, -西
    dy = py - cy               # +南, -北

    if dx == 0 and dy == 0:
        return "重合"

    # 计算bearing：北=0°, 顺时针增加
    # 在该坐标系下：北方向向量是(0,-1)，所以用 atan2(dx, -dy)
    bearing = (math.degrees(math.atan2(dx, -dy)) + 360) % 360
    return bearing_to_dir8(bearing)

def locate_point_with_direction(p: Point2D, areas: List[Dict]) -> str:
    inside = []
    best_name: Optional[str] = None
    best_dist = float("inf")
    best_dir: Optional[str] = None

    for a in areas:
        name = a["areaName"]
        poly = [(float(pt[0]), float(pt[1])) for pt in a["points"]]
        if point_in_polygon(p, poly):
            inside.append(name)

        d = distance_point_to_polygon(p, poly)
        if d < best_dist:
            best_dist = d
            best_name = name
            c = (float(a["center"][0]), float(a["center"][1]))
            best_dir = direction_from_center(p, c)

    if inside:
        return inside[0]
        # 如果在某区域内，最近距离必为0；方向对“最近区域”意义不大，这里给一个更明确的结果
        # return {
        #     "point": p,
        #     "inside_areas": inside,
        #     "nearest_area": inside[0],   # 若存在重叠，这里取第一个；也可改成返回全部
        #     "distance": 0.0,
        #     "direction": "区域内部",
        # }

    return best_name + best_dir
    # return {
    #     "point": p,
    #     "inside_areas": [],
    #     "nearest_area": best_name,
    #     "distance": best_dist,
    #     "direction": best_dir,  # 例如：东北、西南…
    # }

# 用法：
# res = locate_point_with_direction((80000, 50000), areas)
# print(res)

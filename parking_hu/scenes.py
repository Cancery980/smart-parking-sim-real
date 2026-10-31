"""Seeded, original procedural parking scenes; truth never enters the detector.

Images are classroom simulation graphics, not photographs. All vehicle assets
are drawn here and require no external image license. Coordinates are meters.
"""
from __future__ import annotations

import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PALETTE = ["#e8ebed", "#cc443f", "#2e759c", "#d1ae43", "#4c5968", "#7b8894", "#a1b2ae", "#764e70"]


def _overlap(a, b):
    return max(a[0], b[0]) < min(a[2], b[2]) and max(a[1], b[1]) < min(a[3], b[3])


def make_scene(map_data, seed=42, scenario="normal", count=None):
    """Create non-overlapping background entities with deterministic graphics.

    Full fills every space; boundary reserves neighbouring spaces before
    shifting selected cars. Occlusion is rendering-only and never erases truth.
    """
    if scenario not in {"normal", "empty", "full", "occluded", "boundary", "blocked"}:
        raise ValueError("unknown scenario")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    rng = random.Random(seed)
    spots = map_data["spots"]
    if scenario == "empty":
        number = 0
    elif scenario == "full":
        number = len(spots)
    else:
        number = rng.randint(10, 30) if count is None else int(count)
    if not 0 <= number <= len(spots):
        raise ValueError("count outside 0..52")
    selected = rng.sample(spots, number)
    vehicles = []
    for spot in selected:
        cx, cy = spot["center_m"]
        width = rng.uniform(1.65, 1.90)
        length = rng.uniform(3.95, 4.55)
        cx += rng.uniform(-0.10, 0.10)
        cy += rng.uniform(-0.12, 0.12)
        rect = [cx-width/2, cy-length/2, cx+width/2, cy+length/2]
        if scenario == "boundary" and rng.random() < 0.35:
            shift = rng.choice([-1, 1]) * rng.uniform(0.95, 1.35)
            rect = [rect[0]+shift, rect[1], rect[2]+shift, rect[3]]
        # Boundary cases may occupy two spaces; ensure physical entities do
        # not overlap each other. Skipping does not consult model predictions.
        if any(_overlap(rect, v["rect_m"]) for v in vehicles):
            continue
        vehicle = {"vehicle_id": f"BG_{seed}_{spot['spot_id']}",
                   "rect_m": [round(v, 6) for v in rect],
                   "color": rng.choice(PALETTE), "heading": rng.choice([0, 180]),
                   "style": rng.randrange(3)}
        if scenario == "occluded" and rng.random() < 0.40:
            vehicle["occlusion_fraction"] = rng.uniform(0.15, 0.32)
        vehicles.append(vehicle)
    if scenario == "blocked":
        rect = [40.25, 12.05, 44.75, 13.95]
        vehicles = [v for v in vehicles if not _overlap(rect, v["rect_m"])]
        vehicles.append({"vehicle_id": f"BG_{seed}_BLOCK", "rect_m": rect,
                         "color": "#c74e3e", "heading": 90, "style": 0})
    return {"scene_id": f"{scenario}_{seed}", "seed": seed,
            "scenario": scenario, "vehicles": vehicles}


def vehicle_at_spot(spot, vehicle_id, color="#2e759c"):
    cx, cy = spot["center_m"]
    return {"vehicle_id": vehicle_id, "rect_m": [cx-.9, cy-2.15, cx+.9, cy+2.15],
            "color": color, "heading": 0, "style": 0}


def _font(size):
    for filename in ["C:/Windows/Fonts/arial.ttf", "DejaVuSans.ttf"]:
        try:
            return ImageFont.truetype(filename, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _vehicle_sprite(width, height, vehicle):
    """Draw inside the annotated rectangle, including tyres and mirrors."""
    horizontal = width > height
    w, h = (height, width) if horizontal else (width, height)
    w, h = max(16, w), max(28, h)
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    color = vehicle.get("color", "#2e759c")
    margin = max(2, round(w*.09))
    body = (margin, 1, w-margin-1, h-2)
    radius = max(3, round(w*.22))
    # Tyres and mirrors are included in the ground-truth bounding rectangle.
    for side in [1, w-margin]:
        for y in [h*.21, h*.73]:
            d.rounded_rectangle((side, y, side+margin-2, y+h*.14), 2, fill="#101721")
    d.rounded_rectangle(body, radius, fill=color, outline="#182531", width=1)
    d.line((margin+2, h*.17, margin+2, h*.83), fill="#d5dee4", width=1)
    d.rounded_rectangle((w*.17, h*.27, w*.83, h*.67), max(2, int(w*.10)), fill="#203944")
    d.polygon([(w*.18,h*.28),(w*.82,h*.28),(w*.75,h*.40),(w*.25,h*.40)], fill="#87aab7")
    d.rectangle((w*.24,h*.42,w*.76,h*.57), fill=color)
    d.polygon([(w*.25,h*.59),(w*.75,h*.59),(w*.82,h*.67),(w*.18,h*.67)], fill="#486978")
    for x in [w*.19, w*.66]:
        d.rounded_rectangle((x,h*.06,x+w*.15,h*.095), 1, fill="#f7f1c4")
        d.rectangle((x,h*.92,x+w*.15,h*.95), fill="#a51e23")
    d.rectangle((w*.39,h*.96,w*.61,h*.985), fill="#c9d7db")
    d.line((w*.24,h*.20,w*.76,h*.20), fill="#a0afb6", width=1)
    if vehicle.get("style", 0) == 1:
        d.line((w*.30,h*.43,w*.30,h*.56), fill="#d3dce0", width=2)
        d.line((w*.70,h*.43,w*.70,h*.56), fill="#d3dce0", width=2)
    elif vehicle.get("style", 0) == 2:
        d.rectangle((w*.32,h*.44,w*.68,h*.54), fill="#314c57")
    if vehicle.get("heading") == 180:
        layer = layer.transpose(Image.Transpose.ROTATE_180)
    if horizontal:
        layer = layer.transpose(Image.Transpose.ROTATE_90)
    return layer.resize((width, height), Image.Resampling.LANCZOS)


def render_scene(map_data, scene, output_path):
    """Save only the simulated camera image, without truth/detection overlays."""
    width, height = map_data["image_size"]
    ppm = map_data["px_per_m"]
    im = Image.new("RGB", (width, height), "#d8dcdb")
    d = ImageDraw.Draw(im)
    def box(rect):
        return tuple(round(float(x)*ppm) for x in rect)
    for rect in map_data["car_areas_m"]:
        d.rectangle(box(rect), fill="#4d565e")
    for rect in map_data["ped_areas_m"]:
        d.rectangle(box(rect), fill="#a6b8b2")
    # The two pedestrian crossings belong to the fixed map, not inference.
    for y_start, y_end in [(6.6, 10.1), (21.9, 25.4)]:
        y = y_start
        while y < y_end:
            d.rectangle(box([42, y, 43.2, min(y+.22, y_end)]), fill="#e8e5d2")
            y += .44
    fonts = _font(13)
    for spot in map_data["spots"]:
        x1,y1,x2,y2 = box(spot["rect_m"])
        d.rectangle((x1,y1,x2,y2), fill="#6e777d", outline="#ece6cc", width=2)
        cx, cy = spot["center_m"]
        label_y = y1+5 if cy < 16 else y2-18
        d.text((x1+5, label_y), spot["spot_id"], font=fonts, fill="#ece6cc")
        if spot["spot_type"] != "normal":
            marker_color = "#76b9d2" if spot["spot_type"] == "accessible" else "#88bfa0"
            d.rectangle((x1+3,y1+3,x1+8,y2-3), fill=marker_color)
    for rect in map_data["static_obstacles_m"]:
        d.rectangle(box(rect), fill="#253b44")
    # Fixed road arrows, preserved across every train/validation/test image.
    for cx,cy,direction in [(12,8.35,-1),(30,8.35,-1),(12,23.65,1),(30,23.65,1)]:
        x,y = cx*ppm,cy*ppm
        d.line((x-direction*20,y,x+direction*20,y),fill="#b1b7b9",width=3)
        d.polygon([(x+direction*20,y),(x+direction*8,y-7),(x+direction*8,y+7)],fill="#b1b7b9")
    for label, point in [("IN",map_data["entry_m"]),("OUT",map_data["exit_m"])]:
        d.text((point[0]*ppm-15,point[1]*ppm-26),label,font=_font(18),fill="#f1edd7")
    ex,ey = map_data["destination_m"]
    d.rectangle(box([43.6,2.5,46.3,5.2]),fill="#d4dddc",outline="#3e6470",width=3)
    d.text((43.9*ppm,3.4*ppm),"LIFT",font=_font(20),fill="#2b5461")
    for vehicle in scene["vehicles"]:
        coords = box(vehicle["rect_m"])
        x1,y1,x2,y2 = coords
        if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
            raise ValueError(f"vehicle outside image: {vehicle['vehicle_id']}")
        sprite = _vehicle_sprite(x2-x1, y2-y1, vehicle)
        im.paste(sprite, (x1,y1), sprite)
        fraction = float(vehicle.get("occlusion_fraction", 0))
        if fraction:
            # Explicit artificial top-slice mask, assessed separately in report.
            d.rectangle((x1,y1,x2,round(y1+(y2-y1)*fraction)),fill="#636b6e")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    im.save(output_path)
    return output_path


def truth_occupancy(map_data, scene):
    """Offline/confirmation truth only: positive-area body/space intersection."""
    return {s["spot_id"]: ("occupied" if any(_overlap(s["rect_m"],v["rect_m"]) for v in scene["vehicles"]) else "free")
            for s in map_data["spots"]}

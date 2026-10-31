"""Freeze independent scene seeds and create labelled synthetic image splits."""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from parking_hu.map_data import build_map
from parking_hu.scenes import make_scene, render_scene, truth_occupancy


def prepare():
    data = ROOT/"data"/"synthetic"
    map_data = build_map()
    definitions = []
    # Frozen before any inference: no test-based choice or threshold adjustment.
    for i in range(24):
        scenario = ["normal","normal","occluded","boundary","empty","full"][i%6]
        definitions.append(("exploration",1000+i,scenario))
    for i in range(180):
        scenario = "normal"
        if i%12 == 0: scenario = "full"
        elif i%13 == 0: scenario = "empty"
        elif i%10 == 0: scenario = "blocked"
        elif i%5 == 0: scenario = "occluded"
        elif i%7 == 0: scenario = "boundary"
        definitions.append(("train",10000+i,scenario))
    for i in range(30):
        scenario = ["normal","normal","normal","occluded","boundary","blocked"][i%6]
        definitions.append(("val",20000+i,scenario))
    for group,base in [("normal",30000),("occluded",40000),("boundary",50000)]:
        for i in range(10):
            definitions.append(("test",base+i,group))
    specification = {"created_utc":datetime.now(timezone.utc).isoformat(),
                     "schema_version":"1.0","map_id":map_data["map_id"],
                     "splits":[{"split":sp,"seed":seed,"scenario":scenario} for sp,seed,scenario in definitions],
                     "frozen_thresholds":{"low":.20,"high":.50,"overlap_fraction":.05},
                     "test_policy":"30 independent seeds, 10 per predefined generation condition; no tuning on test",
                     "assets":"Original procedural vehicle graphics in parking_hu/scenes.py; synthetic domain only"}
    data.mkdir(parents=True,exist_ok=True)
    freeze = data/"frozen_design.json"
    if freeze.exists():
        existing=json.loads(freeze.read_text(encoding="utf-8"))
        if existing["splits"] != specification["splits"]:
            raise RuntimeError("Frozen split definition changed; create a new experiment instead")
    else:
        freeze.write_text(json.dumps(specification,ensure_ascii=False,indent=2),encoding="utf-8")
    manifests = {name:[] for name in ["exploration","train","val","test"]}
    hashes = set()
    duplicates = []
    for split,seed,scenario in definitions:
        scene = make_scene(map_data,seed=seed,scenario=scenario)
        stem=scene["scene_id"]
        image = data/"images"/split/f"{stem}.png"
        label = data/"labels"/split/f"{stem}.txt"
        label.parent.mkdir(parents=True,exist_ok=True)
        render_scene(map_data,scene,image)
        labels=[]
        for vehicle in scene["vehicles"]:
            x1,y1,x2,y2=vehicle["rect_m"]
            labels.append(f"0 {(x1+x2)/2/52:.9f} {(y1+y2)/2/32:.9f} {(x2-x1)/52:.9f} {(y2-y1)/32:.9f}")
        label.write_text("\n".join(labels),encoding="utf-8")
        digest=hashlib.sha256(image.read_bytes()).hexdigest()
        # Empty images intentionally repeat the single fixed empty map, but
        # final test has no empty image and no duplicates with other splits.
        if digest in hashes: duplicates.append({"split":split,"scene_id":stem,"empty":not scene["vehicles"]})
        hashes.add(digest)
        manifests[split].append({"scene":scene,"image":str(image.relative_to(ROOT)).replace("\\","/"),
                                 "sha256":digest,"truth_occupancy":truth_occupancy(map_data,scene)})
    for split,rows in manifests.items():
        (data/f"{split}_manifest.json").write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding="utf-8")
    yaml = "path: "+str(data.resolve()).replace("\\","/")+"\ntrain: images/train\nval: images/val\nnames:\n  0: car\n"
    (data/"dataset.yaml").write_text(yaml,encoding="utf-8")
    summary={"split_counts":{k:len(v) for k,v in manifests.items()},
             "vehicle_counts":{k:sum(len(r["scene"]["vehicles"]) for r in v) for k,v in manifests.items()},
             "duplicate_images":duplicates,
             "frozen_design_sha256":hashlib.sha256(freeze.read_bytes()).hexdigest()}
    (data/"preparation_summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False))


if __name__ == "__main__":
    prepare()

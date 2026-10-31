"""Evaluate frozen 30-scene holdout once; export detections for Liang Ke."""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from collections import Counter,defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from parking_hu.map_data import build_map
from parking_hu.detector import YoloDetector
from parking_hu.occupancy import estimate_occupancy
from parking_hu.vision_metrics import detection_counts,occupancy_counts,summarize_counts


def main():
    from PIL import Image,ImageDraw,ImageFont
    map_data=build_map()
    model=YoloDetector(ROOT/"models/parking_yolov8n.pt")
    manifest_path=ROOT/"data/synthetic/test_manifest.json"
    manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
    output=ROOT/"data/detection_cache"
    output.mkdir(parents=True,exist_ok=True)
    reports=ROOT/"reports"
    (reports/"vision_cases").mkdir(exist_ok=True)
    aggregate=Counter(tp=0,fp=0,fn=0)
    high_aggregate=Counter(tp=0,fp=0,fn=0)
    confusion=Counter()
    groups=defaultdict(lambda:Counter(tp=0,fp=0,fn=0))
    rows=[]
    for i,row in enumerate(manifest):
        scene=row["scene"]
        frame_id=f"holdout_{i:02}_{row['sha256'][:8]}"
        detections=model.detect(ROOT/row["image"],1,frame_id)
        snapshot=estimate_occupancy(map_data,detections,1,frame_id)
        counts=detection_counts(detections,scene["vehicles"])
        high_counts=detection_counts(detections,scene["vehicles"],conf=.50)
        occupancy=occupancy_counts(map_data,scene,snapshot)
        aggregate.update(counts); high_aggregate.update(high_counts)
        confusion.update(occupancy);groups[scene["scenario"]].update(counts)
        cache={"scene_id":scene["scene_id"],"group":scene["scenario"],"image":row["image"],
               "image_sha256":row["sha256"],"weights_sha256":model.weights_sha256,
               "snapshot":snapshot,"truth":{"vehicles":scene["vehicles"],"occupancy":row["truth_occupancy"]}}
        (output/f"{scene['scene_id']}.json").write_text(json.dumps(cache,ensure_ascii=False,indent=2),encoding="utf-8")
        item={"scene_id":scene["scene_id"],"group":scene["scenario"],"vehicles":len(scene["vehicles"]),
              **counts,"observed_free":sum(v["state"]=="free" for v in snapshot["occupancy"].values()),
              "unknown":sum(v["state"]=="unknown" for v in snapshot["occupancy"].values()),
              "false_free_spaces":occupancy.get("occupied->free",0)}
        rows.append(item)
        # Every group gets a sample, and every failure gets preserved.
        if i in [0,10,20] or counts["fp"] or counts["fn"] or item["false_free_spaces"]:
            image=Image.open(ROOT/row["image"]).convert("RGB")
            draw=ImageDraw.Draw(image)
            for det in detections:
                box=det["bbox_xyxy_px"]
                draw.rectangle(box,outline="#00e7a6" if det["confidence"]>=.5 else "#ffb000",width=3)
                draw.text((box[0],max(0,box[1]-13)),f"{det['confidence']:.2f}",fill="#faffff")
            image.save(reports/"vision_cases"/f"{scene['scene_id']}.png")
    with (reports/"vision_per_scene.csv").open("w",encoding="utf-8-sig",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    stats={"test_scenes":len(manifest),"test_manifest_sha256":hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
           "weights_sha256":model.weights_sha256,"imgsz":model.imgsz,"iou_match":.50,
           "detection_conf_020":summarize_counts(aggregate),"detection_conf_050":summarize_counts(high_aggregate),
           "group_detection_conf_020":{k:summarize_counts(v) for k,v in groups.items()},
           "occupancy_confusion":dict(confusion),
           "false_free_spaces":sum(r["false_free_spaces"] for r in rows),
           "unknown_spaces":sum(r["unknown"] for r in rows),
           "scope":"Independent scene seeds from the same procedural renderer and fixed map, not real-image generalization",
           "three_state_claim":"Unknown catches retained ambiguous evidence; completely missed cars may still be observed free"}
    (reports/"vision_metrics.json").write_text(json.dumps(stats,ensure_ascii=False,indent=2),encoding="utf-8")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"]=["Microsoft YaHei","DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"]=False
    matrix=[[confusion[f"{truth}->{prediction}"] for prediction in ["free","occupied","unknown"]]
            for truth in ["free","occupied"]]
    fig,ax=plt.subplots(figsize=(8,3.8),layout="constrained")
    ax.imshow(matrix,cmap="Blues",aspect="auto")
    for y,row_values in enumerate(matrix):
        for x,value in enumerate(row_values):
            ax.text(x,y,str(value),ha="center",va="center",fontsize=19,
                    color="white" if value>max(max(r) for r in matrix)/2 else "#183b50")
    ax.set_xticks([0,1,2],["观测空闲 free","占用 occupied","不确定 unknown"])
    ax.set_yticks([0,1],["真实空闲","真实占用"])
    ax.set_title("三态占用混淆矩阵  30 个独立合成场景 / 1560 个车位判断",pad=14)
    fig.savefig(reports/"vision_confusion.png",dpi=160)
    plt.close(fig)
    print(json.dumps(stats,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()

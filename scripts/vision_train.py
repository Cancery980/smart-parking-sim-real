"""Explore pretrained weights, then fine-tune using train/validation only."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ.setdefault("YOLO_CONFIG_DIR",str(ROOT/"runtime"/"ultralytics"))
Path(os.environ["YOLO_CONFIG_DIR"]).mkdir(parents=True,exist_ok=True)
from parking_hu.detector import YoloDetector
from parking_hu.map_data import build_map
from parking_hu.occupancy import estimate_occupancy
from parking_hu.vision_metrics import detection_counts,occupancy_counts,summarize_counts


def main():
    from ultralytics import YOLO
    import ultralytics
    import torch
    os.chdir(ROOT)
    # Make the supplied frozen images trainable after moving the project.
    # This relocates only the training configuration, never changes images/seeds.
    dataset=ROOT/"data/synthetic/dataset.yaml"
    dataset.write_text("path: "+str(ROOT/"data/synthetic").replace("\\","/")+
                       "\ntrain: images/train\nval: images/val\nnames:\n  0: car\n",encoding="utf-8")
    models=ROOT/"models"
    reports=ROOT/"reports"
    models.mkdir(exist_ok=True)
    reports.mkdir(exist_ok=True)
    pretrained=models/"yolov8n.pt"
    if not pretrained.exists():
        initial=YOLO("yolov8n.pt")
        downloaded=ROOT/"yolov8n.pt"
        if downloaded.exists(): shutil.move(str(downloaded),str(pretrained))
    detector=YoloDetector(pretrained)
    exploration=json.loads((ROOT/"data/synthetic/exploration_manifest.json").read_text(encoding="utf-8"))
    aggregate=Counter(tp=0,fp=0,fn=0)
    occupancy_aggregate=Counter()
    map_data=build_map()
    rows=[]
    for i,row in enumerate(exploration):
        detections=detector.detect(ROOT/row["image"],1,f"explore_{i:02}")
        counts=detection_counts(detections,row["scene"]["vehicles"])
        occupancy=occupancy_counts(map_data,row["scene"],estimate_occupancy(map_data,detections,1,f"explore_{i:02}"))
        aggregate.update(counts)
        occupancy_aggregate.update(occupancy)
        rows.append({"scene_id":row["scene"]["scene_id"],"scenario":row["scene"]["scenario"],
                     "detections":detections,"counts":counts,"occupancy_confusion":occupancy})
    baseline={"weights":"models/yolov8n.pt","weights_sha256":detector.weights_sha256,
              "ultralytics":ultralytics.__version__,"torch":torch.__version__,
              "imgsz":832,"confidence":.20,"iou_match":.50,
              "metrics":summarize_counts(aggregate),"occupancy_confusion":dict(occupancy_aggregate),"scenes":rows}
    (reports/"vision_pretrained_exploration.json").write_text(json.dumps(baseline,ensure_ascii=False,indent=2),encoding="utf-8")
    print("EXPLORATION",json.dumps(baseline["metrics"]),flush=True)
    del detector
    torch.cuda.empty_cache()
    # One declared run, checkpoint selection uses validation mAP only.
    args=dict(data=str(ROOT/"data/synthetic/dataset.yaml"),epochs=35,imgsz=832,batch=8,
              device=0 if torch.cuda.is_available() else "cpu",workers=0,seed=20261031,
              deterministic=True,patience=12,optimizer="AdamW",lr0=.003,lrf=.05,amp=False,
              mosaic=.5,close_mosaic=8,scale=.25,translate=.05,degrees=0.0,
              fliplr=.5,flipud=.5,hsv_h=.015,hsv_s=.25,hsv_v=.25,
              cache=False,plots=True,project=str(ROOT/"data/training_runs"),name="synthetic_v1",
              exist_ok=False,save=True,verbose=False)
    (reports/"vision_training_config.json").write_text(json.dumps({
        "train_args":args,"pretrained_exploration_metrics":baseline["metrics"],
        "purpose":"Adapt COCO pretrained detector to the declared procedural simulation domain; test split not used"},
        ensure_ascii=False,indent=2),encoding="utf-8")
    model=YOLO(str(pretrained))
    model.train(**args)
    run=Path(model.trainer.save_dir)
    best=run/"weights/best.pt"
    target=models/"parking_yolov8n.pt"
    shutil.copy2(best,target)
    shutil.copy2(run/"results.csv",reports/"vision_training_results.csv")
    metadata={"architecture":"YOLOv8n","pretrained":"Ultralytics official yolov8n.pt",
              "pretrained_url":"https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8n.pt",
              "pretrained_sha256":hashlib.sha256(pretrained.read_bytes()).hexdigest(),
              "weights_sha256":hashlib.sha256(target.read_bytes()).hexdigest(),
              "ultralytics":ultralytics.__version__,"torch":torch.__version__,
              "training_seed":20261031,"selection":"best validation checkpoint from single declared run",
              "completed_utc":datetime.now(timezone.utc).isoformat(),
              "actual_epochs":len((run/"results.csv").read_text(encoding="utf-8").strip().splitlines())-1,
              "training_images":180,"training_unique_images":169,"validation_scenes":30,"test_scenes":30,
              "exploration_images":24,"exploration_unique_images":21,
              "duplicate_note":"Repeated empty map images only within development data; train/val/test image hash intersections are empty",
              "scope":"Synthetic procedural top-down vehicle assets only; no real-camera generalization claim",
              "license":"AGPL-3.0 for Ultralytics/model integration; assets original procedural graphics"}
    (models/"model_card.json").write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding="utf-8")
    print("MODEL_READY",target,metadata["weights_sha256"],flush=True)


if __name__ == "__main__":
    main()

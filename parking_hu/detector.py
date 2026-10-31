"""Ultralytics wrapper that accepts image pixels only, never scene truth."""
from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path


class YoloDetector:
    def __init__(self, weights_path, device=None, imgsz=832):
        config_dir=Path(os.environ.get("YOLO_CONFIG_DIR",str(Path(weights_path).resolve().parents[1]/"runtime"/"ultralytics")))
        config_dir.mkdir(parents=True,exist_ok=True)
        os.environ.setdefault("YOLO_CONFIG_DIR",str(config_dir))
        from ultralytics import YOLO
        import torch
        self.weights_path = Path(weights_path).resolve()
        if not self.weights_path.is_file():
            raise FileNotFoundError(f"YOLO 权重不存在: {self.weights_path}")
        self.weights_sha256 = hashlib.sha256(self.weights_path.read_bytes()).hexdigest()
        self.model = YOLO(str(self.weights_path))
        self.device = device if device is not None else (0 if torch.cuda.is_available() else "cpu")
        self.imgsz = imgsz
        self._lock = threading.Lock()

    def detect(self, image_path, scene_version, frame_id):
        from PIL import Image
        with Image.open(image_path) as image:
            if image.size != (1664,1024):
                raise ValueError("输入图像必须为1664×1024，不能直接套用缩放图坐标")
            image.verify()
        # Class selection supports both official COCO weights and our one-class
        # fine-tuned model. Results.xyxy already refer to original image size.
        car_classes = [int(i) for i,name in self.model.names.items() if name == "car"]
        if not car_classes:
            raise ValueError("模型没有 car 类")
        with self._lock:
            result = self.model.predict(source=str(image_path),conf=.20,iou=.70,
                imgsz=self.imgsz,device=self.device,classes=car_classes,
                max_det=100,verbose=False,save=False)[0]
        if tuple(result.orig_shape) != (1024,1664):
            raise ValueError("模型返回图像尺寸不匹配")
        boxes = result.boxes.xyxy.detach().cpu().tolist()
        confidences = result.boxes.conf.detach().cpu().tolist()
        return [{"detection_id": f"{frame_id}:d{i:03d}","bbox_xyxy_px":[float(v) for v in b],
                 "class_name":"car","confidence":float(c),"frame_id":frame_id,
                 "scene_version":scene_version}
                for i,(b,c) in enumerate(zip(boxes,confidences))]

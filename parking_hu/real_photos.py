"""Native-pixel perception for sourced real photographs.

Only the photo manifest's geometry is read here. Offline vehicle/occupancy
annotations live in a separate file that this module never opens.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import threading
import time

from PIL import Image
from shapely.geometry import Point, Polygon, box


class RealPhotoError(Exception):
    def __init__(self, code: str, message: str, status: int = 503):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _number(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def estimate_photo_occupancy(spots, detections, *, high=.50, low=.20, overlap_fraction=.05,
                             observed_roi_xywh=None):
    """Apply the same evidence policy to calibrated polygons in native pixels.

    A center exactly on a polygon boundary is not strictly inside. Unlike an
    axis-aligned bounding rectangle, the polygon respects perspective geometry.
    Truth labels are neither accepted nor inferred from input metadata.
    """
    retained = [d for d in detections if d['confidence'] >= low]
    observation = None
    if observed_roi_xywh is not None:
        x,y,width,height = observed_roi_xywh
        observation = box(x,y,x+width,y+height)
    result = []
    for spot in spots:
        polygon = Polygon(spot['polygon_px'])
        fully_observed = observation is None or observation.covers(polygon)
        observed_fraction = 1.0 if observation is None else observation.intersection(polygon).area / polygon.area
        occupied_ids, ambiguous_ids, evidence = [], [], []
        for detection in retained:
            x1, y1, x2, y2 = detection['bbox_xyxy_px']
            area = polygon.intersection(box(x1, y1, x2, y2)).area
            fraction = area / polygon.area
            center_inside = polygon.contains(Point((x1+x2)/2, (y1+y2)/2))
            did = detection['detection_id']
            if center_inside and detection['confidence'] >= high:
                occupied_ids.append(did)
            if area + 1e-12 >= overlap_fraction * polygon.area:
                ambiguous_ids.append(did)
            if area > 0:
                evidence.append({'detection_id':did, 'confidence':detection['confidence'],
                                 'center_inside':center_inside, 'overlap_fraction':fraction})
        if occupied_ids:
            state, reason, used = 'occupied', 'high_confidence_center', occupied_ids
        elif ambiguous_ids:
            state, reason, used = 'unknown', 'ambiguous_overlap', ambiguous_ids
        elif not fully_observed:
            state, reason, used = 'unknown', 'incomplete_observation', []
        else:
            state, reason, used = 'free', 'no_evidence', []
        for item in evidence:
            item['used'] = item['detection_id'] in used
        result.append({'spot_id':spot['spot_id'], 'polygon_px':deepcopy(spot['polygon_px']),
                       'state':state, 'reason':reason, 'detection_ids':used, 'evidence':evidence,
                       'fully_observed':fully_observed,'observation_fraction':observed_fraction})
    return result


class PhotoDetector:
    """Reuse Ultralytics with arbitrary photo dimensions; output original pixels."""
    def __init__(self, weights_path, *, imgsz=1280, conf=.20, classes=None, device=None,
                 nms_iou=.70, augment=False):
        self.weights_path = Path(weights_path).resolve()
        if not self.weights_path.is_file():
            raise FileNotFoundError('真实照片模型权重不存在')
        self.weights_sha256 = hashlib.sha256(self.weights_path.read_bytes()).hexdigest()
        os.environ.setdefault('YOLO_CONFIG_DIR', str(self.weights_path.parent.parent/'runtime/ultralytics'))
        from ultralytics import YOLO
        import torch
        self.model = YOLO(str(self.weights_path))
        self.imgsz, self.conf = imgsz, conf
        if not _number(nms_iou) or not 0 < nms_iou <= 1 or not isinstance(augment, bool):
            raise ValueError('真实照片推理参数无效')
        self.nms_iou, self.augment = float(nms_iou), augment
        self.device = device if device is not None else (0 if torch.cuda.is_available() else 'cpu')
        names = self.model.names
        self.classes = list(classes) if classes is not None else [int(k) for k,v in names.items() if v == 'car']
        if not self.classes or any(names.get(k) != 'car' for k in self.classes):
            raise ValueError('真实照片模型的类别配置必须指向 car')
        self._lock = threading.Lock()

    def detect(self, image_path, *, roi_xywh=None):
        with Image.open(image_path) as image:
            width, height = image.size
            image.verify()
        source, offset_x, offset_y = str(image_path), 0, 0
        expected_shape = (height, width)
        if roi_xywh is not None:
            if (not isinstance(roi_xywh, (list, tuple)) or len(roi_xywh) != 4
                    or any(isinstance(v, bool) or not isinstance(v, int) for v in roi_xywh)):
                raise ValueError('真实照片分析区域必须为整数像素矩形')
            offset_x, offset_y, roi_width, roi_height = roi_xywh
            if (offset_x < 0 or offset_y < 0 or roi_width <= 0 or roi_height <= 0
                    or offset_x + roi_width > width or offset_y + roi_height > height):
                raise ValueError('真实照片分析区域超出原图')
            with Image.open(image_path) as image:
                source = image.convert('RGB').crop((offset_x, offset_y, offset_x + roi_width, offset_y + roi_height))
            expected_shape = (roi_height, roi_width)
        with self._lock:
            prediction = self.model.predict(source=source, conf=self.conf, iou=self.nms_iou,
                                            imgsz=self.imgsz, device=self.device, classes=self.classes,
                                            max_det=500, augment=self.augment, verbose=False, save=False)[0]
        if tuple(prediction.orig_shape) != expected_shape:
            raise ValueError('真实照片推理返回的原图尺寸不一致')
        coordinates = prediction.boxes.xyxy.detach().cpu().tolist()
        scores = prediction.boxes.conf.detach().cpu().tolist()
        return [{'detection_id':f'd{i:03d}', 'bbox_xyxy_px':[float(v) + (offset_x if j % 2 == 0 else offset_y) for j, v in enumerate(bounds)],
                 'confidence':float(score), 'class_name':'car'}
                for i, (bounds,score) in enumerate(zip(coordinates,scores))]


class RealPhotoService:
    """Lazy, isolated photo inference; no modification of a parking session."""
    def __init__(self, root, detector=None):
        self.root = Path(root).resolve()
        self.detector = detector
        self.lock = threading.RLock()
        self._photos = None
        self._sequence = 0

    def _inside(self, relative):
        if not isinstance(relative, str) or not relative:
            raise ValueError('资源路径缺失')
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root) or Path(relative).is_absolute():
            raise ValueError('资源路径超出项目目录')
        return path

    def _load(self):
        if self._photos is not None:
            return
        try:
            manifest = json.loads((self.root/'data/real/manifest.json').read_text(encoding='utf-8'))
            config = json.loads((self.root/'models/real_photo_config.json').read_text(encoding='utf-8'))
            if not isinstance(config,dict) or not isinstance(manifest,dict) or manifest.get('schema_version') != 1 or not isinstance(manifest.get('photos'), list):
                raise ValueError('真实照片清单格式错误')
            extra_manifests = config.get('extra_manifests', [])
            if not isinstance(extra_manifests,list):
                raise ValueError('外部照片清单配置错误')
            source_keys = ('name','repository','commit','upstream_commit','attribution','license','url')
            sources = [{k:manifest.get('source',{}).get(k) for k in source_keys if k in manifest.get('source',{})}]
            for relative in extra_manifests:
                extra = json.loads(self._inside(relative).read_text(encoding='utf-8'))
                if not isinstance(extra,dict) or extra.get('schema_version') != 1 or not isinstance(extra.get('photos'),list):
                    raise ValueError('外部照片清单格式错误')
                sources.append({k:extra.get('source',{}).get(k) for k in source_keys if k in extra.get('source',{})})
                manifest['photos'].extend(extra['photos'])
            weights_path = self._inside(config['weights'])
            weights_sha256 = hashlib.sha256(weights_path.read_bytes()).hexdigest()
            if config.get('weights_sha256', weights_sha256) != weights_sha256:
                raise ValueError('真实照片模型校验失败')
            low, high, overlap = config.get('conf', .20), config.get('high', .50), config.get('overlap_fraction', .05)
            if not all(_number(n) for n in [low, high, overlap]) or not 0 < low <= high <= 1 or not 0 < overlap <= 1:
                raise ValueError('真实照片阈值配置错误')
            detection_threshold = config.get('detection_threshold', low)
            if not _number(detection_threshold) or not low <= detection_threshold <= 1:
                raise ValueError('车辆检测门槛不得低于占用证据保留门槛')
            nms_iou, augment = config.get('nms_iou',.70), config.get('augment',False)
            if not _number(nms_iou) or not 0 < nms_iou <= 1 or not isinstance(augment,bool):
                raise ValueError('真实照片推理参数无效')
            roi_policy, roi_by_view = config.get('roi_policy','full'), config.get('roi_by_view',{})
            if roi_policy not in ('full','calibrated') or not isinstance(roi_by_view,dict):
                raise ValueError('真实照片分析区域策略无效')
            imgsz, classes = config.get('imgsz',1280), config.get('classes',[2])
            if isinstance(imgsz,bool) or not isinstance(imgsz,int) or imgsz < 32:
                raise ValueError('真实照片模型输入尺寸错误')
            if (not isinstance(classes,list) or not classes or len(set(classes)) != len(classes)
                or any(isinstance(c,bool) or not isinstance(c,int) or c < 0 for c in classes)):
                raise ValueError('真实照片类别配置错误')
            photos = {}
            for row in manifest['photos']:
                pid = row['photo_id']
                if not isinstance(pid,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,160}',pid) or pid in photos:
                    raise ValueError('真实照片编号无效或重复')
                width, height = row['width'], row['height']
                if any(isinstance(n,bool) or not isinstance(n,int) or n <= 0 for n in [width,height]):
                    raise ValueError('真实照片尺寸无效')
                self._inside(row['image'])
                if not isinstance(row['sha256'],str) or not re.fullmatch('[0-9a-f]{64}',row['sha256']):
                    raise ValueError('真实照片缺少文件校验')
                ids, clean_spots = set(), []
                for spot in row['spots']:
                    sid, points = spot['spot_id'], spot['polygon_px']
                    if not isinstance(sid,str) or not sid or sid in ids:
                        raise ValueError('真实车位编号无效或重复')
                    if (not isinstance(points,list) or len(points) < 3 or any(
                        not isinstance(p,(list,tuple)) or len(p)!=2 or not all(_number(v) for v in p)
                        or not (0<=p[0]<=width and 0<=p[1]<=height) for p in points)):
                        raise ValueError('真实车位多边形坐标无效')
                    polygon = Polygon(points)
                    if not polygon.is_valid or polygon.area <= 0:
                        raise ValueError('真实车位多边形无效')
                    ids.add(sid)
                    clean_spots.append({'spot_id':sid,'polygon_px':deepcopy(points)})
                photos[pid] = {**self._metadata(row), 'image':row['image'], 'spots':clean_spots}
                photos[pid].pop('inference_roi_xywh', None)
                if roi_policy == 'calibrated':
                    roi = roi_by_view.get(row.get('view'))
                    if (not isinstance(roi,list) or len(roi)!=4 or any(isinstance(v,bool) or not isinstance(v,int) for v in roi)
                            or roi[0]<0 or roi[1]<0 or roi[2]<=0 or roi[3]<=0
                            or roi[0]+roi[2]>width or roi[1]+roi[3]>height):
                        raise ValueError('缺少有效的固定视角分析区域')
                    photos[pid]['inference_roi_xywh'] = list(roi)
            if not photos:
                raise ValueError('真实照片清单为空')
            self.config = {'weights':config['weights'],'imgsz':imgsz,'conf':low,'high':high,
                           'overlap_fraction':overlap,'classes':classes,
                           'detection_threshold':detection_threshold,
                           'nms_iou':nms_iou,'augment':augment,'roi_policy':roi_policy}
            self.weights_path = weights_path
            self.model_info = {**self.config, 'sha256':weights_sha256,
                               'device':str(getattr(self.detector,'device','按本机可用设备选择'))}
            self.source = sources[0] if len(sources)==1 else {'name':'多个真实停车场数据集','components':sources}
            self._photos = photos
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise RealPhotoError('real_photo_data_unavailable', '真实照片数据或模型配置不可用，请检查完整安装包。') from exc

    @staticmethod
    def _metadata(row):
        # Allowlist excludes file-system locations, truth fields and extra metadata.
        keys = ('photo_id','dataset','view','weather','date','split','width','height','sha256','source_url','inference_roi_xywh')
        value = {k:deepcopy(row[k]) for k in keys if k in row}
        value['image_url'] = '/api/real/image/' + row['photo_id']
        return value

    def catalog(self):
        with self.lock:
            self._load()
            return {'photos':[self._metadata(row) for row in self._photos.values()],
                    'model':deepcopy(self.model_info),'source':deepcopy(self.source)}

    def _photo(self, photo_id):
        if not isinstance(photo_id,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,160}',photo_id):
            raise RealPhotoError('invalid_photo_id','请选择有效的真实照片编号。',422)
        self._load()
        if photo_id not in self._photos:
            raise RealPhotoError('photo_not_found','没有找到这张真实照片。',404)
        return self._photos[photo_id]

    def _image_path(self, row):
        try:
            path = self._inside(row['image'])
            if hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256']:
                raise ValueError('真实照片校验失败')
            with Image.open(path) as image:
                if image.size != (row['width'],row['height']):
                    raise ValueError('真实照片尺寸与标定不一致')
                image.verify()
            return path
        except (OSError, ValueError) as exc:
            raise RealPhotoError('photo_file_invalid','真实照片文件缺失、损坏或与车位标定不一致。') from exc

    def image_path(self, photo_id):
        with self.lock:
            return self._image_path(self._photo(photo_id))

    @staticmethod
    def _clean_detections(rows, width, height, frame_id):
        if not isinstance(rows,list):
            raise ValueError('检测结果不是列表')
        clean = []
        for i,row in enumerate(rows):
            b, c = row.get('bbox_xyxy_px'), row.get('confidence')
            if (row.get('class_name') != 'car' or not isinstance(b,(list,tuple)) or len(b)!=4
                or not all(_number(v) for v in b) or not _number(c) or not 0<=c<=1
                or not (0<=b[0]<b[2]<=width and 0<=b[1]<b[3]<=height)):
                raise ValueError('车辆框或置信度无效')
            clean.append({'detection_id':f'{frame_id}:d{i:03d}', 'bbox_xyxy_px':[float(v) for v in b],
                          'confidence':float(c),'class_name':'car'})
        return clean

    def analyze(self, photo_id):
        with self.lock:
            row = self._photo(photo_id)
            image_path = self._image_path(row)
            started = time.perf_counter()
            try:
                if self.detector is None:
                    self.detector = PhotoDetector(self.weights_path, imgsz=self.config['imgsz'],
                                                  conf=self.config['conf'], classes=self.config['classes'],
                                                  nms_iou=self.config['nms_iou'],augment=self.config['augment'])
                self._sequence += 1
                frame_id = f'real_{self._sequence:06d}'
                raw = (self.detector.detect(image_path,roi_xywh=row['inference_roi_xywh'])
                       if 'inference_roi_xywh' in row else self.detector.detect(image_path))
                detections = self._clean_detections(raw,row['width'],row['height'],frame_id)
                detections = [d for d in detections if d['confidence'] >= self.config['conf']]
                for detection in detections:
                    detection['accepted'] = detection['confidence'] >= self.config['detection_threshold']
                spots = estimate_photo_occupancy(row['spots'],detections, high=self.config['high'],
                                                 low=self.config['conf'],overlap_fraction=self.config['overlap_fraction'],
                                                 observed_roi_xywh=row.get('inference_roi_xywh'))
                self.model_info['device'] = str(getattr(self.detector,'device','unknown'))
            except Exception as exc:
                logging.getLogger(__name__).exception('真实照片推理失败')
                raise RealPhotoError('photo_inference_failed','真实照片检测未完成，请检查模型文件和运行日志后重试。') from exc
            accepted = sum(d['accepted'] for d in detections)
            return {'status':'ok', 'frame_id':frame_id, 'photo':self._metadata(row),
                    'detection_counts':{'accepted':accepted,'evidence_only':len(detections)-accepted,
                                        'retained':len(detections)},
                    'detections':detections,'spots':spots,
                    'counts':{state:sum(s['state']==state for s in spots) for state in ('free','occupied','unknown')},
                    'model':deepcopy(self.model_info),'elapsed_ms':round((time.perf_counter()-started)*1000,2),
                    'thresholds':{'low':self.config['conf'],'high':self.config['high'],
                                  'detection':self.config['detection_threshold'],
                                  'overlap_fraction':self.config['overlap_fraction']}}

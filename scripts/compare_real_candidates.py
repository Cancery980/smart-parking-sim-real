"""Measure a candidate on development photos only; never select using test data."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'scripts'))
from parking_hu.real_photos import PhotoDetector, estimate_photo_occupancy
from evaluate_real_photos import score_detections, summarize_detection, summarize_occupancy


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def select_threshold(table, baseline=None):
    defined = [r for r in table if r['precision'] is not None and r['recall'] is not None and r['f1'] is not None]
    if baseline is not None:
        defined = [r for r in defined if r['precision'] + 1e-12 >= baseline['precision']
                   and r['recall'] + 1e-12 >= baseline['recall']]
    return max(defined, key=lambda r:(min(r['precision'],r['recall']),r['f1'],-r['confidence']), default=None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--weights', type=Path, required=True)
    parser.add_argument('--id', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--device', default='cpu')
    parser.add_argument('--roi', choices=['full','calibrated'], default='full')
    parser.add_argument('--imgsz', type=int, default=1280)
    parser.add_argument('--nms-iou', type=float, default=.70)
    parser.add_argument('--augment', action='store_true')
    args = parser.parse_args()
    output = args.output.resolve() / args.id
    if output.exists():
        raise FileExistsError('Keep existing candidate evidence; choose a new output/id')
    manifest_path = ROOT/'data/real/manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    truths = json.loads((ROOT/'data/real/annotations.json').read_text(encoding='utf-8'))['photos']
    photos = [p for p in manifest['photos'] if p['split'] == 'dev']
    assert len(photos) == 18
    import torch
    torch.set_num_threads(4)
    detector = PhotoDetector(args.weights, imgsz=args.imgsz, conf=.20, classes=[0],
                             device=args.device, nms_iou=args.nms_iou, augment=args.augment)
    thresholds = [round(.20 + i*.05, 2) for i in range(13)]
    totals = {t:Counter() for t in thresholds}
    by_view = {t:defaultdict(Counter) for t in thresholds}
    matrix = Counter()
    elapsed = []
    for p in photos:
        image_path = ROOT/p['image']
        assert hashlib.sha256(image_path.read_bytes()).hexdigest() == p['sha256']
        truth = truths[p['photo_id']]
        roi = [int(v) for v in truth['roi_xywh']] if args.roi == 'calibrated' else None
        started = time.perf_counter()
        detections = detector.detect(image_path, roi_xywh=roi)
        elapsed.append(1000*(time.perf_counter()-started))
        per_threshold = {}
        for t in thresholds:
            counts, _ = score_detections(detections, truth, conf=t)
            totals[t].update(counts)
            by_view[t][p['view']].update(counts)
            per_threshold[str(t)] = counts
        spots = estimate_photo_occupancy(p['spots'], detections, observed_roi_xywh=roi)
        matrix.update(truth['occupancy'][s['spot_id']]+'->'+s['state'] for s in spots)
        write(output/'predictions'/f'{p["photo_id"]}.json',
              {'photo_id':p['photo_id'],'split':'dev','image_sha256':p['sha256'],
               'weights_sha256':detector.weights_sha256,'detections':detections,
               'inference_roi_xywh':roi,'threshold_counts':per_threshold,'elapsed_ms':elapsed[-1]})
        print(f'{args.id} {len(elapsed)}/{len(photos)} {p["photo_id"]}', flush=True)
    table = [{'confidence':t, **summarize_detection(totals[t]),
              'views':{v:summarize_detection(c) for v,c in by_view[t].items()}} for t in thresholds]
    balanced = select_threshold(table)
    baseline = json.loads((ROOT/'reports/real_photos/dev/pklot_yolov8n_1280/summary.json').read_text(encoding='utf-8'))['detection_conf_020']
    best = select_threshold(table, baseline)
    report = {'id':args.id,'evaluated_at_utc':datetime.now(timezone.utc).isoformat(),
              'weights':str(args.weights.resolve()),'weights_sha256':detector.weights_sha256,
              'manifest_sha256':hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
              'images':len(photos),'split':'dev','imgsz':args.imgsz,'roi_policy':args.roi,
              'nms_iou':args.nms_iou,'augment':args.augment,'device':str(args.device),
              'threshold_grid':table,'selected_development_threshold':best,
              'unconstrained_balanced_threshold':balanced,'baseline_development_metrics':baseline,
              'selection_rule':'Require both P and R >= v1.1 conf .20 development baseline; then maximize min(P,R), ties F1 then lower threshold',
              'occupancy_with_all_conf020_evidence':summarize_occupancy(matrix),
              'mean_inference_ms':sum(elapsed)/len(elapsed),
              'timing_note':'one call per image; includes image load and first-call warmup, excludes occupancy; same-host CPU comparisons only'}
    write(output/'summary.json', report)
    print(json.dumps({'id':args.id,'best':best,'mean_inference_ms':report['mean_inference_ms']}),flush=True)


if __name__ == '__main__':
    main()

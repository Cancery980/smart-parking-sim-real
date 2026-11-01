"""Evaluate a frozen real-photo configuration without model/threshold selection."""
from __future__ import annotations
import argparse
from collections import Counter,defaultdict
from datetime import datetime,timezone
import csv
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'scripts'))
from parking_hu.real_photos import PhotoDetector,estimate_photo_occupancy
from evaluate_real_photos import score_detections,summarize_detection,summarize_occupancy


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def read(path):return json.loads(path.read_text(encoding='utf-8'))
def write(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--selection',type=Path,default=ROOT/'reports/real_optimization/frozen_selection.json')
    parser.add_argument('--output',type=Path,default=ROOT/'reports/real_optimization/final')
    parser.add_argument('--device',default='cpu')
    args=parser.parse_args()
    frozen=read(args.selection)
    if args.output.exists():raise FileExistsError('Keep earlier results; use a fresh output folder for an explicit rerun')
    import torch
    torch.set_num_threads(4)
    cache={};detectors={};combined={}
    configs=[*frozen['baselines'],frozen['selected_config']]
    for dataset_name, spec in frozen['datasets'].items():
        manifest_path, truth_path=ROOT/spec['manifest'],ROOT/spec['annotations']
        assert sha(manifest_path)==spec['manifest_sha256'] and sha(truth_path)==spec['annotations_sha256']
        photos=[p for p in read(manifest_path)['photos'] if p['split']==spec['split']]
        truths=read(truth_path)['photos'];assert len(photos)==spec['images']
        comparisons={}
        for config in configs:
            assert sha(ROOT/config['weights'])==config['weights_sha256']
            settings=(config['weights_sha256'],config['imgsz'],config.get('nms_iou',.70),config.get('augment',False))
            if settings not in detectors:
                detectors[settings]=PhotoDetector(ROOT/config['weights'],imgsz=config['imgsz'],conf=.20,classes=[0],
                    device=args.device,nms_iou=settings[2],augment=settings[3])
            detector=detectors[settings]
            total=Counter();low_total=Counter();matrix=Counter();groups=defaultdict(Counter);rows=[]
            for index,p in enumerate(photos):
                image_path=ROOT/p['image'];assert sha(image_path)==p['sha256']
                truth=truths[p['photo_id']]
                roi=config.get('roi_by_view',{}).get(p['view']) if config.get('roi_policy')=='calibrated' else None
                if config.get('roi_policy')=='calibrated':assert roi is not None
                key=(settings,p['sha256'],tuple(roi) if roi else None)
                reused=key in cache
                if not reused:
                    started=time.perf_counter();detections=detector.detect(image_path,roi_xywh=roi)
                    cache[key]=(detections,1000*(time.perf_counter()-started))
                detections,ms=cache[key]
                score,matches=score_detections(detections,truth,conf=config['detection_threshold'])
                low,_=score_detections(detections,truth,conf=.20)
                spots=estimate_photo_occupancy(p['spots'],detections,high=.50,low=.20,overlap_fraction=.05,
                                              observed_roi_xywh=roi)
                confusion=Counter(truth['occupancy'][s['spot_id']]+'->'+s['state'] for s in spots)
                total.update(score);low_total.update(low);matrix.update(confusion)
                for group in (p['view'],p['weather']):groups[group].update(score)
                row={'photo_id':p['photo_id'],'view':p['view'],'weather':p['weather'],'date':p['date'],**score,
                     'false_free':confusion.get('occupied->free',0),'unknown':sum(v for k,v in confusion.items() if k.endswith('->unknown')),
                     'inference_ms':ms,'inference_reused':reused}
                rows.append(row)
                write(args.output/dataset_name/config['id']/'predictions'/f'{p["photo_id"]}.json',
                      {'photo_id':p['photo_id'],'image_sha256':p['sha256'],'weights_sha256':config['weights_sha256'],
                       'detections':detections,'score':score,'matches':matches,'occupancy':dict(confusion),
                       'inference_roi_xywh':roi,'detection_threshold':config['detection_threshold'],
                       'inference_ms':ms,'inference_reused':reused})
                print(f'{dataset_name} {config["id"]} {index+1}/{len(photos)}',flush=True)
            output=args.output/dataset_name/config['id']
            with (output/'per_photo.csv').open('w',encoding='utf-8-sig',newline='') as file:
                writer=csv.DictWriter(file,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
            summary={'config':config,'images':len(photos),'dates':len({p['date'] for p in photos}),
                     'detection':summarize_detection(total),'detection_conf_020':summarize_detection(low_total),
                     'occupancy':summarize_occupancy(matrix),'groups':{g:summarize_detection(c) for g,c in groups.items()},
                     'device':str(args.device),'mean_inference_ms':sum(r['inference_ms'] for r in rows)/len(rows)}
            write(output/'summary.json',summary);comparisons[config['id']]=summary
        combined[dataset_name]={'dataset':spec,'comparisons':comparisons}
    result={'evaluated_at_utc':datetime.now(timezone.utc).isoformat(),'frozen_selection_sha256':sha(args.selection),
            'selected_id':frozen['selected_config']['id'],'datasets':combined,
            'metric':'Confidence threshold selected on dev only; fixed IoU .50, author ROI, confidence-ordered one-to-one matches. Not mAP.',
            'occupancy_policy':'All retained conf>=.20 boxes; high .50, overlap .05. Display threshold never removes evidence. If an inference ROI does not cover a whole spot, lack of vehicle evidence means unknown, not free; all spots remain in the denominator.'}
    write(args.output/'summary.json',result)
    print(json.dumps({'selected':result['selected_id'],'datasets':list(combined)}))


if __name__=='__main__':main()

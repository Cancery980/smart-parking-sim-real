"""Development-only model choice, then frozen real-photo holdout evaluation.

Vehicle IoU is measured against vehicle boxes, not parking-space rectangles.
Ground truth is used only here, never by the runtime photo service.
"""
from __future__ import annotations
import argparse
from collections import Counter,defaultdict
import csv
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from parking_hu.real_photos import PhotoDetector,estimate_photo_occupancy
from parking_hu.vision_metrics import bbox_iou

CANDIDATES=[
    {'id':'coco_yolov8n_832','weights':'models/yolov8n.pt','imgsz':832,'classes':[2]},
    {'id':'coco_yolov8n_1280','weights':'models/yolov8n.pt','imgsz':1280,'classes':[2]},
    {'id':'coco_yolov8s_1280','weights':'models/yolov8s.pt','imgsz':1280,'classes':[2]},
]
BASELINE={'id':'synthetic_yolov8n_832','weights':'models/parking_yolov8n.pt','imgsz':832,'classes':[0]}
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def read(path):return json.loads(path.read_text(encoding='utf-8'))
def write(path,data):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
def summarize_detection(counts):
    tp,fp,fn=[counts.get(k,0) for k in ('tp','fp','fn')]
    p=tp/(tp+fp) if tp+fp else None;r=tp/(tp+fn) if tp+fn else None
    return {'tp':tp,'fp':fp,'fn':fn,'precision':p,'recall':r,'f1':2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None}
def score_detections(detections,truth,conf=.20):
    x,y,w,h=truth['roi_xywh'];roi=[x,y,x+w,y+h]
    def inside(b):return roi[0]<=(b[0]+b[2])/2<=roi[2] and roi[1]<=(b[1]+b[3])/2<=roi[3]
    def clip(b):return [max(roi[0],b[0]),max(roi[1],b[1]),min(roi[2],b[2]),min(roi[3],b[3])]
    boxes=[[a,b,a+c,b+d] for a,b,c,d in [v['bbox_xywh'] for v in truth['vehicles']]]
    gt=[clip(b) for b in boxes if inside(b)]
    predictions=[d for d in detections if d['confidence']>=conf and inside(d['bbox_xyxy_px'])]
    used=set();tp=fp=0;match_ids=[];fp_ids=[]
    for d in sorted(predictions,key=lambda d:d['confidence'],reverse=True):
        b=clip(d['bbox_xyxy_px'])
        best=max([(bbox_iou(b,box),i) for i,box in enumerate(gt) if i not in used],default=(0,None))
        if best[0]>=.50:tp+=1;used.add(best[1]);match_ids.append(d['detection_id'])
        else:fp+=1;fp_ids.append(d['detection_id'])
    return {'tp':tp,'fp':fp,'fn':len(gt)-len(used)}, {'matched_detection_ids':match_ids,'false_positive_ids':fp_ids,'missed_boxes':[box for i,box in enumerate(gt) if i not in used],'ignored_predictions':sum(d['confidence']>=conf for d in detections)-len(predictions),'evaluated_roi_xywh':truth['roi_xywh']}
def summarize_occupancy(c):
    n=sum(c.values());classified=sum(v for k,v in c.items() if not k.endswith('->unknown'))
    correct=c.get('free->free',0)+c.get('occupied->occupied',0)
    predicted_free=c.get('free->free',0)+c.get('occupied->free',0)
    occupied=sum(v for k,v in c.items() if k.startswith('occupied->'))
    return {'confusion':dict(c),'total':n,'unknown':n-classified,'coverage':classified/n if n else None,
            'correct_over_all':correct/n if n else None,'accuracy_when_decided':correct/classified if classified else None,
            'false_free':c.get('occupied->free',0),'false_free_among_observed_free':c.get('occupied->free',0)/predicted_free if predicted_free else None,
            'false_free_among_true_occupied':c.get('occupied->free',0)/occupied if occupied else None}
def evaluate(config,photos,truths,output,device):
    detector=PhotoDetector(ROOT/config['weights'],imgsz=config['imgsz'],classes=config['classes'],device=device)
    total=Counter();high_total=Counter();occupancy=Counter();groups=defaultdict(Counter);rows=[]
    for index,p in enumerate(photos):
        assert sha(ROOT/p['image'])==p['sha256']
        start=time.perf_counter();detections=detector.detect(ROOT/p['image']);elapsed=(time.perf_counter()-start)*1000
        spots=estimate_photo_occupancy(p['spots'],detections)
        counts,matches=score_detections(detections,truths[p['photo_id']]);high,_=score_detections(detections,truths[p['photo_id']],.5)
        matrix=Counter(truths[p['photo_id']]['occupancy'][s['spot_id']]+'->'+s['state'] for s in spots)
        total.update(counts);high_total.update(high);occupancy.update(matrix)
        for name in [p['view'],p['weather']]:groups[name].update(counts)
        row={'photo_id':p['photo_id'],'view':p['view'],'weather':p['weather'],'date':p['date'],'split':p['split'],**counts,'false_free':matrix.get('occupied->free',0),'unknown':sum(s['state']=='unknown' for s in spots),'spots':len(spots),'inference_ms':round(elapsed,3)}
        rows.append(row)
        write(output/'predictions'/f'{p["photo_id"]}.json',{'photo_id':p['photo_id'],'image_sha256':p['sha256'],'weights_sha256':detector.weights_sha256,
               'detections':detections,'spots':spots,'score':counts,'high_conf_score':high,'occupancy_confusion':dict(matrix),'matches':matches,'inference_ms':elapsed})
        print(f'{config["id"]} {index+1}/{len(photos)} TP={counts["tp"]} FP={counts["fp"]} FN={counts["fn"]}',flush=True)
    with (output/'per_photo.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    result={'config':config,'weights_sha256':detector.weights_sha256,'images':len(photos),'date_count':len({p['date'] for p in photos}),
            'detection_conf_020':summarize_detection(total),'detection_conf_050':summarize_detection(high_total),
            'groups':{k:summarize_detection(v) for k,v in groups.items()},'occupancy':summarize_occupancy(occupancy),
            'mean_inference_ms':sum(r['inference_ms'] for r in rows)/len(rows),'device':str(detector.device),
            'timing_scope':'model.predict including preprocess/postprocess; first call includes warm-up; occupancy time excluded',
            'measured_at_utc':datetime.now(timezone.utc).isoformat()}
    write(output/'summary.json',result)
    return result
def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--stage',choices=['dev','trained-dev','test'],required=True);parser.add_argument('--device',default='0')
    parser.add_argument('--rerun',action='store_true',help='Re-run actual inference instead of reusing matching test caches; back up reports first')
    args=parser.parse_args();device='cpu' if args.device=='cpu' else int(args.device)
    manifest=read(ROOT/'data/real/manifest.json');truth=read(ROOT/'data/real/annotations.json')['photos'];photos=[p for p in manifest['photos'] if p['split']==('dev' if args.stage=='trained-dev' else args.stage)]
    output=ROOT/'reports/real_photos';output.mkdir(parents=True,exist_ok=True)
    common={'confidence':.2,'high_confidence':.5,'iou_nms':.7,'max_det':500,'iou_match':.5,'overlap_fraction':.05,
            'roi_policy':'Only detection/GT centers within author annotationsRectangle; boxes clipped to that region; confidence-ordered one-to-one IoU matches. Outside ROI ignored.',
            'selection_metric':'Highest aggregate vehicle F1 at conf .20 on 18 development images; ties prefer smaller candidate order. Thresholds unchanged from simulation.',
            'detection_metric':'Fixed IoU=.50 precision/recall/F1, not mAP. No ground-truth boxes/labels supplied to inference.',
            'manifest_sha256':sha(ROOT/'data/real/manifest.json'),'annotations_sha256':sha(ROOT/'data/real/annotations.json'),
            'scope':'PKLot subset: two physical lots and three existing camera views, date-disjoint dev/test. Pretrained COCO model; not trained on these 54 photos. Not an official MetaPKLot cross-dataset benchmark.'}
    if args.stage=='dev':
        if (output/'test').exists():raise RuntimeError('Test evidence exists; preserve the frozen selection rather than tune against the holdout.')
        write(output/'evaluation_protocol.json',common)
        results=[evaluate(c,photos,truth,output/'dev'/c['id'],device) for c in CANDIDATES]
        selected=max(results,key=lambda r:r['detection_conf_020']['f1'])
        config={**selected['config'],'conf':.2,'high':.5,'overlap_fraction':.05,'max_det':500,
                'weights_sha256':selected['weights_sha256'],'selected_from':'reports/real_photos/development_selection.json',
                'training':'Official COCO pretrained weights; no training on this PKLot subset','dataset_manifest_sha256':common['manifest_sha256']}
        write(output/'development_selection.json',{'protocol':common,'candidates':results,'selected_id':config['id'],'frozen_at_utc':datetime.now(timezone.utc).isoformat()})
        write(ROOT/'models/real_photo_config.json',config)
        print(json.dumps({'selected':config,'development':selected['detection_conf_020']}))
    elif args.stage=='trained-dev':
        training=read(ROOT/'data/real/training_manifest.json')
        assert not {p['date'] for p in training['train']}&{p['date'] for p in manifest['photos']}
        candidate={'id':'pklot_yolov8n_1280','weights':'models/parking_real_yolov8n.pt','imgsz':1280,'classes':[0]}
        trained=evaluate(candidate,photos,truth,output/'dev'/candidate['id'],device)
        previous=read(output/'development_selection.json')
        results=previous['candidates']+[trained]
        selected=max(results,key=lambda r:r['detection_conf_020']['f1'])
        config={**selected['config'],'conf':.2,'high':.5,'overlap_fraction':.05,'max_det':500,
                'weights_sha256':selected['weights_sha256'],'selected_from':'reports/real_photos/trained_development_selection.json',
                'training':'270 other-date PKLot photos for training; the 18 dev photos select checkpoints and model; 36 test photos held out from this selection',
                'dataset_manifest_sha256':common['manifest_sha256']}
        write(output/'trained_development_selection.json',{'protocol':common,'candidates':results,'selected_id':config['id'],
              'training_manifest_sha256':sha(ROOT/'data/real/training_manifest.json'),'frozen_at_utc':datetime.now(timezone.utc).isoformat(),
              'decision_basis':'Finetuning was initiated after pretrained development recall was 0.6134. Fixed 40 epoch budget and unchanged occupancy thresholds. No test result used to choose weights or thresholds.'})
        write(ROOT/'models/real_photo_config.json',config)
        print(json.dumps({'selected':config['id'],'development':selected['detection_conf_020']}))
    else:
        config=read(ROOT/'models/real_photo_config.json');selected=read(ROOT/config['selected_from'])
        assert selected['selected_id']==config['id'] and config['dataset_manifest_sha256']==common['manifest_sha256']
        assert sha(ROOT/config['weights'])==config['weights_sha256']
        results=[]
        for c in [BASELINE,config]:
            summary_path=output/'test'/c['id']/'summary.json'
            if summary_path.exists() and not args.rerun:
                cached=read(summary_path);assert cached['weights_sha256']==sha(ROOT/c['weights'])
                results.append(cached)
            else:results.append(evaluate(c,photos,truth,output/'test'/c['id'],device))
        if args.rerun and config['id']!='coco_yolov8n_1280':
            evaluate(CANDIDATES[1],photos,truth,output/'test/coco_yolov8n_1280',device)
        if config['id']=='pklot_yolov8n_1280':
            common['scope']='PKLot subset: 270 training photos from other dates, 18 dev photos for checkpoint/model selection, 36 test photos on 32 dates. Two physical lots/three known views; not unseen-site generalization. No thresholds tuned on test.'
            common['selection_metric']='Pretrained candidates and one real-data finetune compared using dev-only vehicle F1. Fixed confidence/occupancy thresholds retained.'
        write(output/'test_summary.json',{'protocol':common,'selected':results[1],'synthetic_baseline':results[0],
              'pretrained_baseline':read(output/'test/coco_yolov8n_1280/summary.json'),'config_sha256':sha(ROOT/'models/real_photo_config.json')})
        print(json.dumps({r['config']['id']:r['detection_conf_020'] for r in results}))
if __name__=='__main__':main()

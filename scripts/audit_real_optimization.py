"""Independent cache audit. Never imports the production scoring/occupancy code.

Uses only Python and Shapely geometric primitives; no neural model is loaded.
Run after the frozen 3-config x (36 legacy + 54 external) evaluation is complete.
"""
from __future__ import annotations

import argparse
from collections import Counter,defaultdict
import csv
from datetime import datetime,timezone
import hashlib
import json
import math
from pathlib import Path
import sys

from shapely.geometry import Point,Polygon,box as rectangle

DEFAULT_ROOT=Path(__file__).resolve().parents[1]
STATE_KEYS=[truth+'->'+state for truth in ('free','occupied') for state in ('free','occupied','unknown')]


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def plain(value):
    # Preserve failed checks in the report even when compared values are sets
    # or a damaged cache contains a non-finite number.
    if isinstance(value,dict):
        return {str(key):plain(item) for key,item in value.items()}
    if isinstance(value,(list,tuple)):
        return [plain(item) for item in value]
    if isinstance(value,(set,frozenset)):
        return [plain(item) for item in sorted(value,key=repr)]
    if isinstance(value,Path):
        return str(value)
    if isinstance(value,float) and not math.isfinite(value):
        return repr(value)
    return value


class Findings:
    def __init__(self):
        self.checks=0
        self.failures=[]

    def equal(self,scope,field,actual,expected):
        self.checks+=1
        if actual!=expected:
            self.failures.append({'scope':scope,'field':field,'actual':actual,'expected':expected})

    def close(self,scope,field,actual,expected):
        self.checks+=1
        correct=(actual is None and expected is None)
        if expected is not None and isinstance(actual,(int,float)) and not isinstance(actual,bool):
            correct=math.isfinite(actual) and math.isclose(float(actual),float(expected),rel_tol=1e-12,abs_tol=1e-12)
        if not correct:
            self.failures.append({'scope':scope,'field':field,'actual':actual,'expected':expected})

    def condition(self,scope,field,value):
        self.equal(scope,field,bool(value),True)


def positive_box(bounds):
    return (isinstance(bounds,(list,tuple)) and len(bounds)==4
            and all(isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x) for x in bounds)
            and bounds[2]>bounds[0] and bounds[3]>bounds[1])


def in_roi(bounds,roi):
    x,y,w,h=roi
    cx=(bounds[0]+bounds[2])/2
    cy=(bounds[1]+bounds[3])/2
    return x<=cx<=x+w and y<=cy<=y+h


def clipped(bounds,roi):
    x,y,w,h=roi
    return [max(x,bounds[0]),max(y,bounds[1]),min(x+w,bounds[2]),min(y+h,bounds[3])]


def independent_iou(first,second):
    left=max(first[0],second[0]);right=min(first[2],second[2])
    top=max(first[1],second[1]);bottom=min(first[3],second[3])
    intersect=max(0,right-left)*max(0,bottom-top)
    denominator=((first[2]-first[0])*(first[3]-first[1])
                 +(second[2]-second[0])*(second[3]-second[1])-intersect)
    return intersect/denominator if denominator>0 else 0.0


def independent_detection(detections,truth,threshold):
    roi=truth['roi_xywh']
    ground=[]
    for annotation in truth['vehicles']:
        x,y,w,h=annotation['bbox_xywh']
        full=[x,y,x+w,y+h]
        if in_roi(full,roi):
            ground.append(clipped(full,roi))
    eligible=[(index,d) for index,d in enumerate(detections) if d['confidence']>=threshold and in_roi(d['bbox_xyxy_px'],roi)]
    # Explicit original index preserves the production stable ordering for equal confidence.
    eligible.sort(key=lambda pair:(-pair[1]['confidence'],pair[0]))
    available=set(range(len(ground)))
    matched=[]
    false_positives=[]
    for _,detection in eligible:
        current=clipped(detection['bbox_xyxy_px'],roi)
        best_index=None
        best_overlap=0.0
        for index in sorted(available):
            overlap=independent_iou(current,ground[index])
            if best_index is None or overlap>=best_overlap:
                # Larger GT index wins an exact IoU tie, matching the declared tuple-max convention.
                best_overlap,best_index=overlap,index
        if best_index is not None and best_overlap>=.50:
            available.remove(best_index)
            matched.append(detection['detection_id'])
        else:
            false_positives.append(detection['detection_id'])
    score={'tp':len(matched),'fp':len(false_positives),'fn':len(available)}
    matches={'matched_detection_ids':matched,'false_positive_ids':false_positives,
             'missed_boxes':[ground[i] for i in sorted(available)],
             'ignored_predictions':sum(d['confidence']>=threshold for d in detections)-len(eligible),
             'evaluated_roi_xywh':roi}
    return score,matches


def independent_occupancy(photo,detections,truth,observed_roi_xywh=None):
    # All retained boxes remain evidence regardless of display/detection threshold.
    observed_region=None
    if observed_roi_xywh is not None:
        x,y,width,height=observed_roi_xywh
        observed_region=rectangle(x,y,x+width,y+height)
    boxes=[]
    for detection in detections:
        if detection['confidence']<.20:
            continue
        left,top,right,bottom=detection['bbox_xyxy_px']
        boxes.append((detection['confidence'],Point((left+right)/2,(top+bottom)/2),
                      rectangle(left,top,right,bottom),(left,top,right,bottom)))
    confusion=Counter()
    by_spot={}
    for spot in photo['spots']:
        polygon=Polygon(spot['polygon_px'])
        fully_observed=observed_region is None or observed_region.covers(polygon)
        observation_fraction=(1.0 if observed_region is None
                              else observed_region.intersection(polygon).area/polygon.area)
        bounds=polygon.bounds
        definite=False
        ambiguous=False
        for confidence,center,detected,db in boxes:
            # A disjoint bounding rectangle cannot contain a center or overlap the polygon.
            if db[2]<bounds[0] or db[0]>bounds[2] or db[3]<bounds[1] or db[1]>bounds[3]:
                continue
            if confidence>=.50 and center.within(polygon):
                definite=True
            area=detected.intersection(polygon).area
            if area+1e-12>=polygon.area*.05:
                ambiguous=True
        if definite:
            predicted,reason='occupied','high_confidence_center'
        elif ambiguous:
            predicted,reason='unknown','ambiguous_overlap'
        elif not fully_observed:
            predicted,reason='unknown','incomplete_observation'
        else:
            predicted,reason='free','no_evidence'
        expected=truth['occupancy'][spot['spot_id']]
        if expected not in ('free','occupied'):
            raise ValueError('Unknown ground-truth occupancy label')
        confusion[expected+'->'+predicted]+=1
        by_spot[spot['spot_id']]={'state':predicted,'reason':reason,
                                  'observation_fraction':observation_fraction,
                                  'fully_observed':fully_observed}
    return confusion,by_spot


def detection_summary(counts):
    tp,fp,fn=(counts.get(k,0) for k in ('tp','fp','fn'))
    return {'tp':tp,'fp':fp,'fn':fn,
            'precision':tp/(tp+fp) if tp+fp else None,
            'recall':tp/(tp+fn) if tp+fn else None,
            'f1':2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None}


def occupancy_summary(matrix):
    total=sum(matrix.values())
    unknown=sum(matrix.get(truth+'->unknown',0) for truth in ('free','occupied'))
    decided=total-unknown
    correct=matrix.get('free->free',0)+matrix.get('occupied->occupied',0)
    false_free=matrix.get('occupied->free',0)
    observed_free=matrix.get('free->free',0)+false_free
    true_occupied=sum(matrix.get('occupied->'+state,0) for state in ('free','occupied','unknown'))
    return {'confusion':dict(matrix),'total':total,'unknown':unknown,
            'coverage':decided/total if total else None,
            'correct_over_all':correct/total if total else None,
            'accuracy_when_decided':correct/decided if decided else None,
            'false_free':false_free,
            'false_free_among_observed_free':false_free/observed_free if observed_free else None,
            'false_free_among_true_occupied':false_free/true_occupied if true_occupied else None}, {
                'all_spot_judgments':total,'decided_spot_judgments':decided,'observed_free':observed_free,
                'true_occupied':true_occupied,'unknown_numerator':unknown,'unknown_rate_denominator':total,
                'unknown_rate':unknown/total if total else None}


def verify_detection_summary(findings,scope,actual,expected):
    for field in ('tp','fp','fn'):
        findings.equal(scope,field,actual.get(field),expected[field])
    for field in ('precision','recall','f1'):
        findings.close(scope,field,actual.get(field),expected[field])


def verify_occupancy_summary(findings,scope,actual,expected):
    actual_matrix=actual.get('confusion',{})
    findings.equal(scope,'matrix',Counter(actual_matrix),Counter(expected['confusion']))
    findings.condition(scope,'matrix_keys_valid',set(actual_matrix)<=set(STATE_KEYS))
    for field in ('total','unknown','false_free'):
        findings.equal(scope,field,actual.get(field),expected[field])
    for field in ('coverage','correct_over_all','accuracy_when_decided','false_free_among_observed_free','false_free_among_true_occupied'):
        findings.close(scope,field,actual.get(field),expected[field])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=DEFAULT_ROOT)
    parser.add_argument('--selection',type=Path)
    parser.add_argument('--final',type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    root=args.root.resolve()
    selection_path=(args.selection or root/'reports/real_optimization/frozen_selection.json').resolve()
    final_dir=(args.final or root/'reports/real_optimization/final').resolve()
    output=(args.output or root/'reports/real_optimization/final_scoring_audit.json').resolve()
    summary_path=final_dir/'summary.json'
    for required in (selection_path,summary_path):
        if not required.is_file():
            raise FileNotFoundError('Final evaluation is not ready: '+str(required))
    frozen=read(selection_path)
    combined=read(summary_path)
    findings=Findings()
    source_hashes={str(selection_path):sha(selection_path),str(summary_path):sha(summary_path)}
    for path in ('parking_hu/real_photos.py','scripts/evaluate_real_photos.py','scripts/evaluate_real_optimization.py'):
        source_hashes[path]=sha(root/path)
    findings.equal('frozen','summary_selection_hash',combined.get('frozen_selection_sha256'),sha(selection_path))
    findings.equal('frozen','selected_id',combined.get('selected_id'),frozen['selected_config']['id'])
    configs=[*frozen['baselines'],frozen['selected_config']]
    config_ids=[c['id'] for c in configs]
    findings.equal('protocol','configuration_count',len(configs),3)
    findings.equal('protocol','unique_configuration_count',len(set(config_ids)),3)
    findings.equal('protocol','datasets',set(frozen['datasets']),{'legacy_test','external_test'})
    findings.equal('protocol','reported_datasets',set(combined.get('datasets',{})),set(frozen['datasets']))
    for config in configs:
        weight=root/config['weights']
        findings.equal(config['id'],'actual_model_sha256',sha(weight),config['weights_sha256'])
        findings.condition(config['id'],'detection_threshold_valid',.20<=config['detection_threshold']<=1)
        source_hashes[config['weights']]=sha(weight)
    all_photo_ids=set()
    all_photo_hashes=set()
    audited_files=0
    recomputed={}
    per_photo=[]
    for dataset_name,spec in frozen['datasets'].items():
        manifest_path,truth_path=root/spec['manifest'],root/spec['annotations']
        findings.equal(dataset_name,'manifest_sha256',sha(manifest_path),spec['manifest_sha256'])
        findings.equal(dataset_name,'annotations_sha256',sha(truth_path),spec['annotations_sha256'])
        source_hashes[spec['manifest']]=sha(manifest_path)
        source_hashes[spec['annotations']]=sha(truth_path)
        dataset_photos=[p for p in read(manifest_path)['photos'] if p['split']==spec['split']]
        truths=read(truth_path)['photos']
        photos={p['photo_id']:p for p in dataset_photos}
        expected_count=36 if dataset_name=='legacy_test' else 54
        findings.equal(dataset_name,'manifest_frames',len(dataset_photos),expected_count)
        findings.equal(dataset_name,'manifest_unique_photo_ids',len(photos),expected_count)
        findings.equal(dataset_name,'frozen_frame_count',spec['images'],expected_count)
        findings.condition(dataset_name,'no_duplicate_photo_ids_across_datasets',not all_photo_ids&set(photos))
        findings.condition(dataset_name,'no_duplicate_image_hashes_across_datasets',not all_photo_hashes&{p['sha256'] for p in photos.values()})
        all_photo_ids.update(photos)
        all_photo_hashes.update(p['sha256'] for p in photos.values())
        for photo in photos.values():
            findings.equal(photo['photo_id'],'actual_image_sha256',sha(root/photo['image']),photo['sha256'])
            findings.equal(photo['photo_id'],'occupancy_gt_matches_geometry',set(truths[photo['photo_id']]['occupancy']),{s['spot_id'] for s in photo['spots']})
        joined=combined['datasets'][dataset_name]
        findings.equal(dataset_name,'combined_dataset_spec',joined['dataset'],spec)
        findings.equal(dataset_name,'reported_configs',set(joined['comparisons']),set(config_ids))
        recomputed[dataset_name]={}
        records_by_config={}
        for config in configs:
            config_id=config['id']
            folder=final_dir/dataset_name/config_id
            prediction_files=list((folder/'predictions').glob('*.json'))
            filenames={p.stem for p in prediction_files}
            findings.equal(dataset_name+'/'+config_id,'cache_filename_set',filenames,set(photos))
            findings.equal(dataset_name+'/'+config_id,'cache_file_count',len(prediction_files),expected_count)
            single_summary=read(folder/'summary.json')
            reported=joined['comparisons'][config_id]
            findings.equal(dataset_name+'/'+config_id,'combined_and_local_summary',single_summary,reported)
            findings.equal(dataset_name+'/'+config_id,'summary_config',reported['config'],config)
            findings.equal(dataset_name+'/'+config_id,'summary_images',reported['images'],expected_count)
            findings.equal(dataset_name+'/'+config_id,'summary_dates',reported['dates'],len({p['date'] for p in photos.values()}))
            source_hashes[str((folder/'summary.json').relative_to(root))]=sha(folder/'summary.json')
            with (folder/'per_photo.csv').open(encoding='utf-8-sig',newline='') as stream:
                csv_rows=list(csv.DictReader(stream))
            csv_by_id={r['photo_id']:r for r in csv_rows}
            findings.equal(dataset_name+'/'+config_id,'csv_count',len(csv_rows),expected_count)
            findings.equal(dataset_name+'/'+config_id,'csv_unique_ids',set(csv_by_id),set(photos))
            aggregate=Counter()
            common_020=Counter()
            matrix=Counter()
            groups=defaultdict(Counter)
            timings=[]
            records={}
            for pid,photo in photos.items():
                path=folder/'predictions'/f'{pid}.json'
                scope=dataset_name+'/'+config_id+'/'+pid
                if not path.is_file():
                    continue
                record=read(path)
                records[pid]=record
                audited_files+=1
                findings.equal(scope,'cache_photo_id',record.get('photo_id'),pid)
                findings.equal(scope,'cache_image_hash',record.get('image_sha256'),photo['sha256'])
                findings.equal(scope,'cache_model_hash',record.get('weights_sha256'),config['weights_sha256'])
                findings.equal(scope,'detection_threshold',record.get('detection_threshold'),config['detection_threshold'])
                wanted_roi=config.get('roi_by_view',{}).get(photo['view']) if config.get('roi_policy')=='calibrated' else None
                findings.equal(scope,'inference_roi_xywh',record.get('inference_roi_xywh'),wanted_roi)
                detections=record['detections']
                ids=[d.get('detection_id') for d in detections]
                findings.equal(scope,'unique_detection_ids',len(set(ids)),len(ids))
                for detection in detections:
                    bounds=detection.get('bbox_xyxy_px')
                    confidence=detection.get('confidence')
                    findings.condition(scope,'native_box/'+str(detection.get('detection_id')),positive_box(bounds)
                                       and 0<=bounds[0]<bounds[2]<=photo['width'] and 0<=bounds[1]<bounds[3]<=photo['height'])
                    findings.condition(scope,'confidence/'+str(detection.get('detection_id')),
                                       isinstance(confidence,(int,float)) and not isinstance(confidence,bool)
                                       and math.isfinite(confidence) and .20<=confidence<=1)
                    findings.equal(scope,'class/'+str(detection.get('detection_id')),detection.get('class_name'),'car')
                score,matches=independent_detection(detections,truths[pid],config['detection_threshold'])
                low,_=independent_detection(detections,truths[pid],.20)
                occupancy,spot_results=independent_occupancy(photo,detections,truths[pid],
                                                            record.get('inference_roi_xywh'))
                findings.equal(scope,'per_photo_score',record['score'],score)
                findings.equal(scope,'match_ids_and_missed_boxes',record['matches'],matches)
                findings.equal(scope,'per_photo_occupancy',Counter(record['occupancy']),occupancy)
                findings.equal(scope,'occupancy_judgment_count',sum(occupancy.values()),len(photo['spots']))
                csv_row=csv_by_id.get(pid,{})
                for field in ('view','weather','date'):
                    findings.equal(scope,'csv_'+field,csv_row.get(field),photo[field])
                for field,value in [*score.items(),('false_free',occupancy.get('occupied->free',0)),
                                    ('unknown',sum(v for k,v in occupancy.items() if k.endswith('->unknown')))]:
                    findings.equal(scope,'csv_'+field,csv_row.get(field),str(value))
                findings.close(scope,'csv_inference_ms',float(csv_row.get('inference_ms','nan')),record['inference_ms'])
                findings.equal(scope,'csv_reused',csv_row.get('inference_reused'),str(record['inference_reused']))
                aggregate.update(score);common_020.update(low);matrix.update(occupancy)
                for group in (photo['view'],photo['weather']):groups[group].update(score)
                timings.append(record['inference_ms'])
                per_photo.append({'dataset':dataset_name,'config':config_id,'photo_id':pid,
                                  'cache_sha256':sha(path),'detection_threshold':config['detection_threshold'],
                                  'score':score,'common_conf_020':low,'occupancy_confusion':dict(occupancy),
                                  'observed_roi_xywh':record.get('inference_roi_xywh'),
                                  'fully_observed_spots':sum(s['fully_observed'] for s in spot_results.values()),
                                  'incomplete_observation_unknown':sum(s['reason']=='incomplete_observation' for s in spot_results.values()),
                                  'unknown_denominator':len(photo['spots']),'ignored_predictions':matches['ignored_predictions']})
            recomputed_detection=detection_summary(aggregate)
            recomputed_low=detection_summary(common_020)
            recomputed_occupancy,denominators=occupancy_summary(matrix)
            verify_detection_summary(findings,dataset_name+'/'+config_id+'/detection',reported['detection'],recomputed_detection)
            verify_detection_summary(findings,dataset_name+'/'+config_id+'/conf020',reported['detection_conf_020'],recomputed_low)
            verify_occupancy_summary(findings,dataset_name+'/'+config_id+'/occupancy',reported['occupancy'],recomputed_occupancy)
            findings.equal(dataset_name+'/'+config_id,'group_names',set(reported['groups']),set(groups))
            for group,counts in groups.items():
                verify_detection_summary(findings,dataset_name+'/'+config_id+'/'+group,reported['groups'][group],detection_summary(counts))
            if timings:
                findings.close(dataset_name+'/'+config_id,'mean_inference_ms',reported['mean_inference_ms'],sum(timings)/len(timings))
            recomputed[dataset_name][config_id]={'detection':recomputed_detection,'detection_conf_020':recomputed_low,
                                                'occupancy':recomputed_occupancy,'occupancy_denominators':denominators}
            records_by_config[config_id]=records
        first_id,second_id=[c['id'] for c in frozen['baselines']]
        first_config,second_config=frozen['baselines']
        for field in ('weights_sha256','imgsz','nms_iou','augment','roi_policy','roi_by_view'):
            findings.equal(dataset_name+'/baseline_reuse',field,first_config.get(field),second_config.get(field))
        for pid in photos:
            if pid not in records_by_config[first_id] or pid not in records_by_config[second_id]:
                continue
            first,second=records_by_config[first_id][pid],records_by_config[second_id][pid]
            findings.equal(dataset_name+'/'+pid,'baseline_raw_detections_exactly_identical',first['detections'],second['detections'])
            findings.equal(dataset_name+'/'+pid,'baseline_inference_ms_identical',first['inference_ms'],second['inference_ms'])
            findings.equal(dataset_name+'/'+pid,'calibrated_baseline_reuses_inference',second['inference_reused'],True)
        findings.equal(dataset_name+'/baseline_reuse','common_020_scores',recomputed[dataset_name][first_id]['detection_conf_020'],recomputed[dataset_name][second_id]['detection_conf_020'])
        findings.equal(dataset_name+'/baseline_reuse','occupancy_unchanged',recomputed[dataset_name][first_id]['occupancy'],recomputed[dataset_name][second_id]['occupancy'])
    findings.equal('complete','audited_prediction_files',audited_files,270)
    findings.equal('complete','unique_evaluation_photos',len(all_photo_ids),90)
    findings.equal('complete','unique_evaluation_image_hashes',len(all_photo_hashes),90)
    result={'created_at_utc':datetime.now(timezone.utc).isoformat(),'passed':not findings.failures,
            'audit_type':'Independent recomputation from saved native detections and separately stored author-derived annotations; no model inference.',
            'production_functions_imported':False,'checks':findings.checks,'failures':findings.failures,
            'prediction_files':audited_files,'unique_photos':len(all_photo_ids),'configs':config_ids,
            'boundary_rules':{'roi_center':'inclusive edges','iou_match':.50,'confidence_order':'descending; stable original order for ties',
                              'ground_truth_iou_tie':'largest unused index','occupancy_low':.20,'occupancy_high':.50,
                              'polygon_center':'strict interior, boundary excluded','overlap_fraction':.05,'overlap_area_epsilon_px2':1e-12,
                              'full_observation':'Inference ROI covers the entire polygon including boundaries; None means full image.',
                              'occupancy_priority':['high_confidence_center','ambiguous_overlap','incomplete_observation','no_evidence'],
                              'occupancy_denominator':'All manifest spots, including partially and fully unobserved spots.'},
            'source_hashes':source_hashes,'recomputed':recomputed,'per_photo':per_photo,
            'limitations':'This audit verifies cached scoring and integrity, not whether annotation labels are perfect or model inference is scientifically generalizable.'}
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(plain(result),ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({'passed':result['passed'],'checks':findings.checks,'failures':len(findings.failures),
                      'prediction_files':audited_files,'output':str(output)},ensure_ascii=False))
    return 0 if result['passed'] else 1


if __name__=='__main__':
    sys.exit(main())

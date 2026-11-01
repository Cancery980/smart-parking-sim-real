"""Freeze a development-selected candidate before either evaluation dataset is run."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def read(path): return json.loads(path.read_text(encoding='utf-8'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reports',type=Path,default=ROOT/'reports/real_optimization/dev')
    parser.add_argument('--output',type=Path,default=ROOT/'reports/real_optimization/frozen_selection.json')
    args = parser.parse_args()
    if args.output.exists(): raise FileExistsError('Frozen selection already exists; preserve the original protocol')
    baseline_metrics = read(ROOT/'reports/real_photos/dev/pklot_yolov8n_1280/summary.json')['detection_conf_020']
    candidates = []
    for path in sorted(args.reports.glob('*/summary.json')):
        row = read(path)
        assert row['split']=='dev' and row['images']==18
        assert row['manifest_sha256']==sha(ROOT/'data/real/manifest.json')
        eligible = row['selected_development_threshold']
        if eligible is not None:
            assert eligible['precision']+1e-12>=baseline_metrics['precision']
            assert eligible['recall']+1e-12>=baseline_metrics['recall']
            assert sha(Path(row['weights']))==row['weights_sha256']
        candidates.append({'report':path.relative_to(ROOT).as_posix(),'report_sha256':sha(path),**row})
    available = [r for r in candidates if r['selected_development_threshold'] is not None]
    if not available: raise ValueError('No development-qualified candidate; retain v1.1')
    def rank(row):
        m = row['selected_development_threshold']
        return min(m['precision'],m['recall']),m['f1'],-m['confidence'],-Path(row['weights']).stat().st_size
    selected = max(available,key=rank)
    selected_metrics = selected['selected_development_threshold']
    target = ROOT/'models/parking_real_optimized.pt'
    if target.exists(): raise FileExistsError('Selected weight already exists; preserve earlier artifact')
    shutil.copy2(selected['weights'],target)
    assert sha(target)==selected['weights_sha256']
    runtime = {'id':selected['id'],'weights':target.relative_to(ROOT).as_posix(),
               'weights_sha256':sha(target),'imgsz':selected['imgsz'],'classes':[0],
               'conf':.20,'high':.50,'overlap_fraction':.05,
               'detection_threshold':selected_metrics['confidence'],
               'nms_iou':selected['nms_iou'],'augment':selected['augment'],'roi_policy':selected['roi_policy'],
               'extra_manifests':['data/real_optimization/external/manifest.json'],
               'selected_from':args.output.relative_to(ROOT).as_posix()}
    datasets = {}
    for label,folder,split in [('legacy_test','data/real','test'),('external_test','data/real_optimization/external','external_test')]:
        manifest_path, annotation_path = ROOT/folder/'manifest.json', ROOT/folder/'annotations.json'
        manifest,truths = read(manifest_path),read(annotation_path)['photos']
        photos = [p for p in manifest['photos'] if p['split']==split]
        assert len(photos)==(36 if label=='legacy_test' else 54)
        datasets[label] = {'manifest':manifest_path.relative_to(ROOT).as_posix(),
                           'manifest_sha256':sha(manifest_path),'annotations':annotation_path.relative_to(ROOT).as_posix(),
                           'annotations_sha256':sha(annotation_path),'split':split,'images':len(photos)}
        if runtime['roi_policy']=='calibrated':
            rois = runtime.setdefault('roi_by_view',{})
            for p in manifest['photos']:
                roi = [int(v) for v in truths[p['photo_id']]['roi_xywh']]
                assert p['view'] not in rois or rois[p['view']]==roi
                rois[p['view']] = roi
    base = read(ROOT/'models/real_photo_config.json')
    assert base['weights_sha256']==sha(ROOT/base['weights'])
    base.update(id='v11_default',detection_threshold=.20,nms_iou=.70,augment=False,roi_policy='full')
    calibrated = {**base,'id':'v11_dev_calibrated'}
    base_development = next(r for r in candidates if r['id']=='v11_n_full_cpu')
    calibrated['detection_threshold'] = base_development['selected_development_threshold']['confidence']
    record = {'schema_version':1,'frozen_at_utc':datetime.now(timezone.utc).isoformat(),
              'selection_rule':'Both dev P/R >= v1.1 conf .20; maximize min(P,R), then F1, lower threshold, smaller weight',
              'baseline_development_metrics':baseline_metrics,'selected_development_metrics':selected_metrics,
              'occupancy_rule':'All conf>=.20 evidence; high .50 and overlap .05. High-confidence center wins; otherwise sufficient overlap is unknown. A spot not fully covered by the actual inference ROI cannot become free without observation; it remains unknown and in the denominator.',
              'selected_config':runtime,'baselines':[base,calibrated],'datasets':datasets,
              'candidate_reports':[{'id':r['id'],'report':r['report'],'sha256':r['report_sha256'],
                                    'selected_development_threshold':r['selected_development_threshold']} for r in candidates],
              'evaluation_rule':'No changes after viewing legacy/external results. Legacy36 is an already published regression set; external54 is unseen CNR and never used for training or selection.'}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'selected':runtime['id'],'metrics':selected_metrics,'frozen_file':str(args.output)},ensure_ascii=False))


if __name__=='__main__':main()

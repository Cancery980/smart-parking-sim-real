"""Fine-tune existing YOLO using other PKLot dates, never the frozen test set.

Downloads 30 frames per view/weather; original images stay in the build cache.
The 18 development photos provide validation. The 36 test photos are not loaded.
"""
from __future__ import annotations
import argparse
from collections import defaultdict,Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import json
from pathlib import Path
import shutil
import sys
import tarfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from prepare_real_photos import UPSTREAM,COMMIT,VIEWS,WEATHER,download,key,sha

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache',type=Path,default=ROOT/'.build/real_training')
    parser.add_argument('--annotations-cache',type=Path,default=ROOT/'.build/real_download')
    parser.add_argument('--prepare-only',action='store_true')
    parser.add_argument('--github-cli',type=Path,help='Optional authenticated gh executable when raw.githubusercontent.com is unavailable')
    parser.add_argument('--download-workers',type=int,choices=range(1,17),default=6)
    args=parser.parse_args();cache=args.cache.resolve();cache.mkdir(parents=True,exist_ok=True)
    manifest=json.loads((ROOT/'data/real/manifest.json').read_text(encoding='utf-8'))
    excluded_dates={p['date'] for p in manifest['photos']}
    metadata={};annotations={};chosen=[]
    for view in VIEWS:
        archive_path=args.annotations_cache/f'{view}_cars.tar.xz'
        download(UPSTREAM+f'annotations/original/cars/PKLot/{view}_cars.tar.xz',archive_path)
        with tarfile.open(archive_path,'r:xz') as archive:
            member=next(m for m in archive if m.isfile() and m.name.endswith('.json'))
            data=json.load(archive.extractfile(member))
        by_id=defaultdict(list)
        categories={c['id'] for c in data['categories'] if c['name']=='car'}
        for a in data['annotations']:
            if a['category_id'] in categories:by_id[a['image_id']].append(a)
        groups=defaultdict(lambda:defaultdict(list))
        for im in data['images']:
            date='-'.join(f'{v:02}' if i else str(v) for i,v in enumerate(im['date']))
            metadata[im['file_name']]={**im,'date_text':date}
            annotations[im['file_name']]=by_id[im['id']]
            if date not in excluded_dates and im['climate'] in WEATHER:groups[WEATHER[im['climate']]][date].append(im['file_name'])
        for weather,days in sorted(groups.items()):
            # Round-robin dates, then timestamp hash: metadata-only sampling.
            queues=[sorted(days[d],key=lambda n:key('real-train-v1/'+n)) for d in sorted(days,key=lambda d:key('train-day-v1/'+d))]
            selected=[]
            while len(selected)<30:
                assert any(queues)
                for queue in queues:
                    if queue and len(selected)<30:selected.append(queue.pop(0))
            chosen.extend(selected)
    assert len(chosen)==270 and len(set(chosen))==270
    def prepare(name,split='train'):
        im=metadata[name];photo_id=im['subset']+'_'+Path(name).stem
        original_path=cache/'original'/f'{photo_id}.jpg'
        original=download(UPSTREAM+name,original_path,github_cli=args.github_cli)
        x,y,w,h=im.get('annotationsRectangle',[0,0,im['width'],im['height']])
        from PIL import Image
        target=cache/'dataset/images'/split/f'{photo_id}.png';target.parent.mkdir(parents=True,exist_ok=True)
        with Image.open(original_path) as picture:
            assert picture.size==(im['width'],im['height'])
            picture.convert('RGB').crop((x,y,x+w,y+h)).save(target)
        labels=[]
        for a in annotations[name]:
            bx,by,bw,bh=a['bbox']
            if not (x<=bx+bw/2<=x+w and y<=by+bh/2<=y+h):continue
            x1,y1,x2,y2=max(x,bx)-x,max(y,by)-y,min(x+w,bx+bw)-x,min(y+h,by+bh)-y
            if x2>x1 and y2>y1:labels.append(f'0 {(x1+x2)/2/w:.9f} {(y1+y2)/2/h:.9f} {(x2-x1)/w:.9f} {(y2-y1)/h:.9f}')
        label_path=cache/'dataset/labels'/split/f'{photo_id}.txt';label_path.parent.mkdir(parents=True,exist_ok=True);label_path.write_text('\n'.join(labels)+'\n',encoding='utf-8')
        return {'photo_id':photo_id,'split':split,'view':im['subset'],'weather':WEATHER[im['climate']],'date':im['date_text'],
                'source_url':UPSTREAM+name,'source_sha256':sha(original),'roi_xywh':[x,y,w,h],
                'training_image_sha256':sha(target.read_bytes()),'label_sha256':sha(label_path.read_bytes()),'vehicles':len(labels)}
    with ThreadPoolExecutor(max_workers=args.download_workers) as pool:training=list(pool.map(prepare,chosen))
    # Development images are already frozen and reused as validation, never test.
    development=[]
    for p in manifest['photos']:
        if p['split']=='dev':
            name=p['source_url'].split(COMMIT+'/',1)[1]
            local=cache/'original'/f'{p["photo_id"]}.jpg'
            if not local.exists():
                assert sha((ROOT/p['image']).read_bytes())==p['sha256']
                shutil.copy2(ROOT/p['image'],local)
            development.append(prepare(name,'val'))
    train_dates={p['date'] for p in training};assert not train_dates & excluded_dates
    assert not {p['source_sha256'] for p in training}&{p['sha256'] for p in manifest['photos']}
    dataset={'path':str(cache/'dataset').replace('\\','/'),'train':'images/train','val':'images/val','names':{0:'car'}}
    import yaml
    (cache/'dataset.yaml').write_text(yaml.safe_dump(dataset),encoding='utf-8')
    record={'upstream_commit':COMMIT,'selection':'30 frames per view/weather from dates absent from both development and test; round-robin dates with hash-ordered timestamps; no detection-dependent selection',
            'train':training,'validation':development,'train_frames':270,'val_frames':18,'train_unique_dates':len(train_dates),'excluded_dates':sorted(excluded_dates),
            'train_dev_test_date_overlap':[],'train_dev_test_image_hash_overlap':[],
            'annotation_policy':'Crop to author annotationsRectangle; retain vehicles centered within ROI and clip their true vehicle boxes. No parking-space labels are used for training.',
            'created_at_utc':datetime.now(timezone.utc).isoformat()}
    (ROOT/'data/real/training_manifest.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'train':270,'val':18,'train_dates':len(train_dates),'date_overlap':[]}),flush=True)
    if args.prepare_only:return
    import os
    os.chdir(cache)  # Any auxiliary Ultralytics downloads stay in the build cache.
    from ultralytics import YOLO
    model=YOLO(str(ROOT/'models/yolov8n.pt'))
    model.train(data=str(cache/'dataset.yaml'),epochs=40,patience=12,imgsz=1280,batch=4,device=0,workers=0,
                project=str(cache/'runs'),name='pklot_real_v1',exist_ok=True,seed=20261101,deterministic=True,
                pretrained=True,cache=False,amp=True,plots=True,save=True,verbose=False,
                mosaic=.5,close_mosaic=10,fliplr=.5,translate=.1,scale=.3,degrees=0)
    run=Path(model.trainer.save_dir)
    shutil.copy2(run/'weights/best.pt',ROOT/'models/parking_real_yolov8n.pt')
    report=ROOT/'reports/real_photos/training';report.mkdir(parents=True,exist_ok=True)
    for name in ['args.yaml','results.csv','results.png','confusion_matrix.png','PR_curve.png']:
        if (run/name).exists():shutil.copy2(run/name,report/name)
    (report/'provenance.json').write_text(json.dumps({'weights_sha256':sha((ROOT/'models/parking_real_yolov8n.pt').read_bytes()),
        'starting_weights_sha256':sha((ROOT/'models/yolov8n.pt').read_bytes()),'training_manifest_sha256':sha((ROOT/'data/real/training_manifest.json').read_bytes()),
        'checkpoint_selection':'Ultralytics best fitness on the 18 development/validation images only; 36 test photos are never loaded for training',
        'epochs_requested':40,'finished_at_utc':datetime.now(timezone.utc).isoformat()},indent=2),encoding='utf-8')
if __name__=='__main__':main()

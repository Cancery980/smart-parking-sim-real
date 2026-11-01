"""Prepare isolated optimization data without altering any v1.1 split.

The 747-photo PKLot training extension contains the original 270 photos and
uses only their original 48 calendar dates. Selection uses metadata and hashes,
never occupancy labels or model results. The existing 18 dev images are reused.
"""
from __future__ import annotations

import argparse
from collections import defaultdict, Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import gc
import json
from pathlib import Path
import shutil
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from prepare_real_photos import COMMIT, UPSTREAM, VIEWS, WEATHER, download, key, sha


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def load_annotations(view, kind, cache, github_cli=None, dataset='PKLot'):
    cache.mkdir(parents=True,exist_ok=True)
    json_path = cache/f'{view}_{kind}.json'
    if json_path.is_file():
        return read(json_path)
    tar_path = cache/f'{view}_{kind}.tar.xz'
    download(UPSTREAM+f'annotations/original/{kind}/{dataset}/{view}_{kind}.tar.xz',tar_path,github_cli=github_cli)
    with tarfile.open(tar_path,'r:xz') as archive:
        member=next(m for m in archive if m.isfile() and m.name.endswith('.json'))
        record=json.load(archive.extractfile(member))
    return record


def source_name(row):
    return row['source_url'].split(COMMIT+'/',1)[1]


def write_baseline270_inputs(cache, original_training=None):
    """Reuse the preserved 270/18 files without rewriting the v1.1 manifest."""
    cache=Path(cache).resolve()
    original_training=original_training or read(ROOT/'data/real/training_manifest.json')
    images=[]
    for row in original_training['train']:
        image=cache/'dataset/images/train'/f'{row["photo_id"]}.png'
        label=cache/'dataset/labels/train'/f'{row["photo_id"]}.txt'
        assert sha(image.read_bytes())==row['training_image_sha256']
        assert sha(label.read_bytes())==row['label_sha256']
        images.append(image.as_posix())
    assert len(images)==len(set(images))==270
    for row in original_training['validation']:
        image=cache/'dataset/images/val'/f'{row["photo_id"]}.png'
        label=cache/'dataset/labels/val'/f'{row["photo_id"]}.txt'
        assert sha(image.read_bytes())==row['training_image_sha256']
        assert sha(label.read_bytes())==row['label_sha256']
    listing=cache/'baseline270.txt'
    listing.write_text('\n'.join(images)+'\n',encoding='utf-8')
    import yaml
    config={'path':str(cache/'dataset').replace('\\','/'),'train':listing.as_posix(),
            'val':'images/val','names':{0:'car'}}
    (cache/'baseline270.yaml').write_text(yaml.safe_dump(config),encoding='utf-8')


def audit_external_against_extension():
    """Complete the cross-dataset hash audit whichever preparation finishes last."""
    external=ROOT/'data/real_optimization/external'
    extended=ROOT/'data/real_optimization/pk_training_manifest.json'
    if not (external/'audit.json').is_file() or not extended.is_file():
        return
    photos=read(external/'manifest.json')['photos']
    training=read(extended)['train']
    old_evaluation=read(ROOT/'data/real/manifest.json')['photos']
    pk_hashes={r['source_sha256'] for r in training}|{r['sha256'] for r in old_evaluation}
    pk_dates={r['date'] for r in training}|{r['date'] for r in old_evaluation}
    assert not {r['sha256'] for r in photos}&pk_hashes
    assert not {r['date'] for r in photos}&pk_dates
    audit=read(external/'audit.json')
    audit['compared_with_completed_pk747_manifest']=True
    audit['pk747_training_manifest_sha256']=sha(extended.read_bytes())
    audit['pk747_train_dev_historical_test_unique_image_sha256']=len(pk_hashes)
    write(external/'audit.json',audit)


def prepare_pk_training(args):
    out=ROOT/'data/real_optimization'
    cache=args.cache.resolve()
    old_manifest=read(ROOT/'data/real/manifest.json')
    old_training=read(ROOT/'data/real/training_manifest.json')
    assert old_training['upstream_commit']==COMMIT
    originals=old_training['train']
    assert len(originals)==270
    original_by_name={source_name(row):row for row in originals}
    original_training_dates={row['date'] for row in originals}
    excluded_dates={row['date'] for row in old_manifest['photos']}
    assert len(original_training_dates)==48 and not original_training_dates & excluded_dates
    assert len(original_by_name)==270
    old_dev=[row for row in old_manifest['photos'] if row['split']=='dev']
    assert len(old_dev)==18
    old_dev_by_name={source_name(row):row for row in old_dev}
    metadata={}
    annotations={}
    chosen=[]
    stratum_audit=[]
    for view in VIEWS:
        data=load_annotations(view,'cars',args.annotations_cache,args.github_cli)
        car_ids={category['id'] for category in data['categories'] if category['name']=='car'}
        groups=defaultdict(lambda:defaultdict(list))
        for image in data['images']:
            day='-'.join(f'{v:02}' if i else str(v) for i,v in enumerate(image['date']))
            name=image['file_name']
            metadata[name]={**image,'date_text':day}
            if day in original_training_dates and image['climate'] in WEATHER:
                groups[WEATHER[image['climate']]][day].append(name)
        selected_for_view=[]
        for weather in sorted(WEATHER.values()):
            days=groups[weather]
            kept=[source_name(row) for row in originals if row['view'].lower()==view and row['weather']==weather]
            assert len(kept)==30
            used=set(kept)
            available=sum(len(names) for names in days.values())
            quota=min(90,available)
            assert quota>=len(kept)
            queues=[sorted((name for name in days[day] if name not in used),key=lambda n:key('real-opt-training-image-v1/'+n))
                    for day in sorted(days,key=lambda d:key('real-opt-training-day-v1/'+d))]
            selected=list(kept)
            while len(selected)<quota:
                if not any(queues):
                    raise ValueError(f'Only {len(selected)} unique permitted photos available for {view}/{weather}; expected {quota}')
                for queue in queues:
                    if queue and len(selected)<quota:
                        selected.append(queue.pop(0))
            selected_for_view.extend(selected)
            stratum_audit.append({'view':view,'weather':weather,'retained_original':30,'added':quota-30,
                                  'selected':quota,'requested_quota':90,'quota_shortfall':90-quota,
                                  'available_unique_photos_on_allowed_dates':available,
                                  'shortfall_reason':('Not enough unique photographs on original training dates; no duplicates or evaluation-date photos substituted.' if quota<90 else None),
                                  'permitted_dates':sorted(days),
                                  'selected_dates':sorted({metadata[name]['date_text'] for name in selected})})
        chosen.extend(selected_for_view)
        needed_names=set(selected_for_view)|{name for name in old_dev_by_name if metadata.get(name,{}).get('subset')==view}
        needed_ids={metadata[name]['id']:name for name in needed_names}
        for name in needed_names: annotations[name]=[]
        # Annotation data is read only after metadata-only selection is complete.
        for annotation in data['annotations']:
            if annotation['image_id'] in needed_ids and annotation['category_id'] in car_ids:
                annotations[needed_ids[annotation['image_id']]].append(annotation)
        del data
        gc.collect()
    assert len(chosen)==len(set(chosen))==747
    assert set(original_by_name)<=set(chosen)
    assert {metadata[name]['date_text'] for name in chosen}==original_training_dates
    selection={
        'schema_version':1,'upstream_commit':COMMIT,'created_at_utc':datetime.now(timezone.utc).isoformat(),
        'selection':'Up to 90 unique images per PKLot view/weather. Keep all 30 original training photos; add by round-robin date and SHA-256 filename order within original training dates only. UFPR04/Rainy has only 45, UFPR05/Rainy only 72 eligible images; use these complete pools without duplication.',
        'selection_uses_vehicle_counts':False,'selection_uses_occupancy':False,'selection_uses_model_outputs':False,
        'original_training_manifest_sha256':sha((ROOT/'data/real/training_manifest.json').read_bytes()),
        'original_photo_manifest_sha256':sha((ROOT/'data/real/manifest.json').read_bytes()),
        'requested_balanced_frames':810,'balanced_shortfall':63,
        'train_frames':747,'original_frames_retained':270,'additional_frames':477,
        'train_unique_dates':48,'permitted_training_dates':sorted(original_training_dates),'excluded_dates':sorted(excluded_dates),
        'train_selection':chosen,'validation_selection':[source_name(p) for p in old_dev],
        'strata':stratum_audit}
    selection_path=out/'pk_training_selection.json'
    if selection_path.exists():
        previous=read(selection_path)
        for field in ('train_selection','validation_selection','original_training_manifest_sha256','original_photo_manifest_sha256'):
            if previous[field]!=selection[field]:
                raise ValueError('Frozen selection mismatch: '+field)
        selection=previous
    else:
        write(selection_path,selection)
    print(json.dumps({'selection_frozen':str(selection_path),'train':747,'original_retained':270,'dev':18,'dates':48}),flush=True)
    if args.selection_only:
        return
    completed=0
    def prepare(item):
        split,name=item
        image=metadata[name]
        photo_id=image['subset']+'_'+Path(name).stem
        original_path=cache/'original'/f'{photo_id}.jpg'
        original_path.parent.mkdir(parents=True,exist_ok=True)
        expected=(original_by_name.get(name) or {}).get('source_sha256')
        if name in old_dev_by_name:
            expected=old_dev_by_name[name]['sha256']
            reuse=ROOT/old_dev_by_name[name]['image']
        else:
            reuse=args.reuse_originals/f'{photo_id}.jpg' if args.reuse_originals else None
        if not original_path.exists() and reuse and reuse.is_file():
            content=reuse.read_bytes()
            if expected and sha(content)!=expected:
                raise ValueError('Existing original hash mismatch: '+photo_id)
            shutil.copy2(reuse,original_path)
        original=download(UPSTREAM+name,original_path,github_cli=args.github_cli)
        original_hash=sha(original)
        if expected and original_hash!=expected:
            raise ValueError('Frozen image hash mismatch: '+photo_id)
        x,y,w,h=image.get('annotationsRectangle',[0,0,image['width'],image['height']])
        from PIL import Image
        target=cache/'dataset/images'/split/f'{photo_id}.png'
        target.parent.mkdir(parents=True,exist_ok=True)
        with Image.open(original_path) as picture:
            assert picture.size==(image['width'],image['height'])
            picture.convert('RGB').crop((x,y,x+w,y+h)).save(target)
        labels=[]
        for annotation in annotations[name]:
            bx,by,bw,bh=annotation['bbox']
            if not(x<=bx+bw/2<=x+w and y<=by+bh/2<=y+h): continue
            x1,y1,x2,y2=max(x,bx)-x,max(y,by)-y,min(x+w,bx+bw)-x,min(y+h,by+bh)-y
            if x2>x1 and y2>y1:
                labels.append(f'0 {(x1+x2)/2/w:.9f} {(y1+y2)/2/h:.9f} {(x2-x1)/w:.9f} {(y2-y1)/h:.9f}')
        label=cache/'dataset/labels'/split/f'{photo_id}.txt'
        label.parent.mkdir(parents=True,exist_ok=True)
        label.write_text('\n'.join(labels)+'\n',encoding='utf-8')
        row={'photo_id':photo_id,'split':split,'view':image['subset'],'weather':WEATHER[image['climate']],
             'date':image['date_text'],'source_url':UPSTREAM+name,'source_sha256':original_hash,
             'roi_xywh':[x,y,w,h],'training_image_sha256':sha(target.read_bytes()),'label_sha256':sha(label.read_bytes()),
             'vehicles':len(labels),'retained_from_v1_1_training':name in original_by_name}
        if name in original_by_name:
            old=original_by_name[name]
            assert row['training_image_sha256']==old['training_image_sha256']
            assert row['label_sha256']==old['label_sha256']
        return row
    entries=[('train',name) for name in chosen]+[('val',source_name(row)) for row in old_dev]
    prepared=[]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for row in pool.map(prepare,entries):
            prepared.append(row)
            completed+=1
            if completed%45==0 or completed==len(entries):
                print(json.dumps({'prepared':completed,'total':len(entries)}),flush=True)
    training_rows=[row for row in prepared if row['split']=='train']
    validation_rows=[row for row in prepared if row['split']=='val']
    training_hashes={p['source_sha256'] for p in training_rows}
    old_eval_hashes={p['sha256'] for p in old_manifest['photos']}
    assert not training_hashes & old_eval_hashes
    assert len(training_hashes)==747,'Duplicate source image bytes in training extension'
    assert {p['source_sha256'] for p in validation_rows}=={p['sha256'] for p in old_dev}
    old_validation={p['photo_id']:p for p in old_training['validation']}
    for row in validation_rows:
        previous=old_validation[row['photo_id']]
        assert row['training_image_sha256']==previous['training_image_sha256']
        assert row['label_sha256']==previous['label_sha256']
    import yaml
    dataset={'path':str(cache/'dataset').replace('\\','/'),'train':'images/train','val':'images/val','names':{0:'car'}}
    (cache/'dataset.yaml').write_text(yaml.safe_dump(dataset),encoding='utf-8')
    write_baseline270_inputs(cache,old_training)
    record={
        **{k:selection[k] for k in ['upstream_commit','selection','train_frames','original_frames_retained','additional_frames','train_unique_dates','excluded_dates']},
        'selection_manifest_sha256':sha(selection_path.read_bytes()),
        'train':training_rows,'validation':validation_rows,'val_frames':18,
        'unique_source_training_images':len(training_hashes),
        'train_dev_test_date_overlap':[],'train_dev_test_image_hash_overlap':[],
        'original_training_images_and_labels_preserved':True,'original_validation_images_and_labels_preserved':True,
        'annotation_policy':old_training['annotation_policy'],
        'created_at_utc':datetime.now(timezone.utc).isoformat()}
    write(out/'pk_training_manifest.json',record)
    audit_external_against_extension()
    print(json.dumps({'training_manifest':str(out/'pk_training_manifest.json'),'dataset_yaml':str(cache/'dataset.yaml'),
                      'train':747,'dev':18,'dates':48,'unique_source_training_images':len(training_hashes)}),flush=True)


def prepare_cnr_external(args):
    """Freeze all 54 external frames before downloading or processing labels."""
    output=ROOT/'data/real_optimization/external'
    photos_metadata={}
    loaded={}
    chosen=[]
    bundles=[]
    old_manifest=read(ROOT/'data/real/manifest.json')
    old_training=read(ROOT/'data/real/training_manifest.json')
    excluded_dates={p['date'] for p in old_manifest['photos']}|{p['date'] for p in old_training['train']}
    for camera in range(1,10):
        view=f'cnr-camera-{camera}'
        cars=load_annotations(view,'cars',args.annotations_cache,args.github_cli,'CNRPark-EXT')
        spots=load_annotations(view,'spots',args.annotations_cache,args.github_cli,'CNRPark-EXT')
        loaded[camera]=(cars,spots)
        for kind in ('cars','spots'):
            archive=args.annotations_cache/f'{view}_{kind}.tar.xz'
            bundles.append({'url':UPSTREAM+f'annotations/original/{kind}/CNRPark-EXT/{view}_{kind}.tar.xz',
                            'sha256':sha(archive.read_bytes()),'bytes':archive.stat().st_size})
        peers={im['file_name']:im for im in spots['images']}
        groups=defaultdict(lambda:defaultdict(list))
        for image in cars['images']:
            name=image['file_name']
            if name not in peers:
                continue
            day='-'.join(f'{v:02}' if i else str(v) for i,v in enumerate(image['date']))
            if day in excluded_dates:
                continue
            weather=Path(name).parts[2]
            if weather not in ('OVERCAST','RAINY','SUNNY'):
                continue
            photos_metadata[name]={**image,'date_text':day,'weather':weather,'camera':camera,
                                   'spot_image_id':peers[name]['id']}
            groups[weather][day].append(name)
        for weather in ('OVERCAST','RAINY','SUNNY'):
            days=groups[weather]
            if len(days)<2:
                raise ValueError(f'Fewer than two eligible dates for CNR camera {camera}/{weather}')
            selected_days=sorted(days,key=lambda day:key(f'cnr-external-v1/{camera}/{weather}/{day}'))[:2]
            for day in selected_days:
                name=min(days[day],key=lambda n:key('cnr-external-v1/image/'+n))
                chosen.append(name)
    assert len(chosen)==len(set(chosen))==54
    selection={
        'schema_version':1,'upstream_commit':COMMIT,'created_at_utc':datetime.now(timezone.utc).isoformat(),
        'role':'Frozen external holdout only; never training, model selection, threshold tuning or candidate ranking.',
        'selection':'All 9 CNR cameras x 3 weather conditions x 2 different dates per camera/weather; SHA-256 date order then SHA-256 timestamp filename order. No image inspection, labels or predictions used to choose samples.',
        'selection_names':chosen,'frames':54,'cameras':9,'weather':['OVERCAST','RAINY','SUNNY'],
        'selected_dates':sorted({photos_metadata[name]['date_text'] for name in chosen}),
        'excluded_pk_dates':sorted(excluded_dates),'date_overlap_with_pk_splits':[],
        'annotation_archives':bundles,
        'correlation_limit':'Nine cameras observe one physical parking lot; camera/date views can be correlated. 54 frames are not 54 independent parking lots or dates.',
        'license':'ODbL-1.0 for the CNRPark-EXT component under the author repository licensing statement.'}
    selection_path=output/'selection.json'
    if selection_path.exists():
        previous=read(selection_path)
        assert previous['selection_names']==chosen and previous['upstream_commit']==COMMIT
        selection=previous
    else:
        write(selection_path,selection)
    print(json.dumps({'external_selection_frozen':str(selection_path),'photos':54,
                      'dates':len(selection['selected_dates']),'model_runs':0}),flush=True)
    if args.selection_only:
        return
    annotations={}
    for camera,(cars,spots) in loaded.items():
        car_ids={category['id'] for category in cars['categories'] if category['name']=='car'}
        category_names={category['id']:category['name'] for category in spots['categories']}
        assert set(category_names.values())=={'empty','occupied'}
        status={cid:('free' if name=='empty' else 'occupied') for cid,name in category_names.items()}
        selected=[name for name in chosen if photos_metadata[name]['camera']==camera]
        car_image_ids={photos_metadata[name]['id']:name for name in selected}
        spot_image_ids={photos_metadata[name]['spot_image_id']:name for name in selected}
        for name in selected:
            annotations[name]={'cars':[],'spots':[],'status':status}
        for annotation in cars['annotations']:
            if annotation['image_id'] in car_image_ids and annotation['category_id'] in car_ids:
                annotations[car_image_ids[annotation['image_id']]]['cars'].append(annotation)
        for annotation in spots['annotations']:
            if annotation['image_id'] in spot_image_ids:
                annotations[spot_image_ids[annotation['image_id']]]['spots'].append(annotation)
    del loaded
    gc.collect()
    def prepare(name):
        metadata=photos_metadata[name]
        photo_id='cnr_'+metadata['subset']+'_'+Path(name).stem
        target=output/'images'/f'{photo_id}.jpg'
        raw=download(UPSTREAM+name,target,github_cli=args.github_cli)
        from PIL import Image
        from shapely.geometry import Polygon
        with Image.open(target) as image:
            assert image.size==(metadata['width'],metadata['height'])
            image.verify()
        geometry=[]
        truth={}
        ordered=sorted(annotations[name]['spots'],key=lambda a:(a['bbox'][1],a['bbox'][0]))
        for index,annotation in enumerate(ordered):
            segments=annotation['segmentation']
            assert len(segments)==1,('Multi-segment spot; retain selected image and inspect conversion',photo_id)
            poly=segments[0]
            points=[[poly[i],poly[i+1]] for i in range(0,len(poly),2)]
            polygon=Polygon(points)
            assert polygon.is_valid and polygon.area>0,('Invalid polygon; do not remove selected image',photo_id,index)
            assert all(0<=x<=metadata['width'] and 0<=y<=metadata['height'] for x,y in points),('Polygon outside native image',photo_id,index)
            sid=f'S{index+1:03d}'
            geometry.append({'spot_id':sid,'polygon_px':points})
            truth[sid]=annotations[name]['status'][annotation['category_id']]
        photo={'photo_id':photo_id,'dataset':'CNRPark-EXT / MetaPKLot','view':metadata['subset'].upper(),
               'weather':metadata['weather'],'date':metadata['date_text'],'capture_time':metadata['time'],
               'split':'external_test','image':target.relative_to(ROOT).as_posix(),
               'width':metadata['width'],'height':metadata['height'],'sha256':sha(raw),
               'source_url':UPSTREAM+name,'spots':geometry}
        offline={'roi_xywh':metadata.get('annotationsRectangle',[0,0,metadata['width'],metadata['height']]),
                 'vehicles':[{'bbox_xywh':a['bbox'],'car_id':a.get('car_id'),'annotation_id':a['id']} for a in annotations[name]['cars']],
                 'occupancy':truth,'source_image_id':metadata['id']}
        return photo,offline
    prepared=[]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for i,item in enumerate(pool.map(prepare,chosen),1):
            prepared.append(item)
            if i%9==0:
                print(json.dumps({'external_photos_prepared':i,'total':54}),flush=True)
    photos=[p for p,t in prepared]
    external_hashes={p['sha256'] for p in photos}
    pk_hashes={p['sha256'] for p in old_manifest['photos']}|{p['source_sha256'] for p in old_training['train']}
    extended=ROOT/'data/real_optimization/pk_training_manifest.json'
    compared_extension=False
    if extended.is_file():
        pk_hashes|={p['source_sha256'] for p in read(extended)['train']}
        compared_extension=True
    assert len(external_hashes)==54 and not external_hashes & pk_hashes
    dates={p['date'] for p in photos}
    assert not dates & excluded_dates
    assert Counter((p['view'],p['weather']) for p in photos)==Counter({(f'CAMERA{c}',w):2 for c in range(1,10) for w in ('OVERCAST','RAINY','SUNNY')})
    manifest={'schema_version':1,'source':{
        'name':'CNRPark-EXT photographs and MetaPKLot author annotations',
        'upstream_commit':COMMIT,'license':'ODbL-1.0 (CNRPark-EXT component)',
        'attribution':'Giuseppe Amato, Fabio Carrara, Fabrizio Falchi, Claudio Gennaro, Claudio Vairo (CNRPark); Paulo Lisboa de Almeida et al. (MetaPKLot)',
        'modifications':'Metadata-only external holdout; original JPEGs unchanged; native-pixel polygons and offline labels stored separately.'},'photos':photos}
    write(output/'manifest.json',manifest)
    write(output/'annotations.json',{'photos':{p['photo_id']:t for p,t in prepared}})
    audit={'frames':54,'unique_image_sha256':len(external_hashes),'unique_dates':len(dates),'dates':sorted(dates),
           'pk_split_date_intersection':[],'pk_split_image_sha256_intersection':[],
           'compared_with_completed_pk747_manifest':compared_extension,
           'manifest_sha256':sha((output/'manifest.json').read_bytes()),
           'annotations_sha256':sha((output/'annotations.json').read_bytes()),
           'selection_sha256':sha(selection_path.read_bytes()),
           'stratum_counts':{view+'/'+weather:count for (view,weather),count in sorted(Counter((p['view'],p['weather']) for p in photos).items())},
           'jpeg_bytes':sum((ROOT/p['image']).stat().st_size for p in photos),
           'native_dimensions_verified':True,'polygons_verified':True,'models_run':False,
           'excluded_selected_images':[]}
    write(output/'audit.json',audit)
    attribution=f'''# CNRPark-EXT 外部验证子集

本目录保存 54 张从未用于本项目训练或调参的 CNRPark-EXT 实拍照片。它与 PKLot 的训练、开发和历史测试文件分开保存。9 个相机来自同一个停车场，不能当作 9 个独立停车场。

照片原作者：Giuseppe Amato、Fabio Carrara、Fabrizio Falchi、Claudio Gennaro、Claudio Vairo。原始项目：[CNRPark](https://cnrpark.it/)。车辆与修订车位标注来自 [MetaPKLot 作者仓库](https://github.com/DSBD-Research/MetaPKLot-Dataset/tree/{COMMIT})，请同时引用 Paulo Lisboa de Almeida 等人的 [MetaPKLot 论文](https://doi.org/10.1007/s00521-026-12398-0)。

本 CNR 数据库部分按 [Open Data Commons Open Database License 1.0](https://opendatacommons.org/licenses/odbl/1-0/) 提供，参见固定提交的[作者许可说明](https://github.com/DSBD-Research/MetaPKLot-Dataset/blob/{COMMIT}/README.md#licensing-and-usage)。它不是 PKLot 的 CC BY 4.0 数据，也不随应用源码改为 AGPL。作者 JSON 未提供独立 licenses 字段，本目录沿用作者对 CNRPark-EXT 部分的说明。

修改内容：按事先固定的相机、天气、日期哈希选取子集；JPEG 字节未修改；标注转换为原图像素多边形与单独的离线评分文件。原始完整数据库、相对路径、提交号、输入标注包和每张图片 SHA 由 selection.json、manifest.json 和 audit.json 记录。公开传播本派生数据库时保留此来源、ODbL 声明及可取得的派生数据。

使用边界：manifest.json 只向运行时提供图像和无状态车位几何；annotations.json 保存车辆框、占用真值及作者完整标注 ROI，供离线评分。camera1/9 的 ROI 不能被误当成原始图片尺寸，原图均为 1000×750。
'''
    (output/'ATTRIBUTION.md').write_text(attribution,encoding='utf-8')
    (output/'ODbL-NOTICE.txt').write_text(
        'This CNRPark-EXT derived database is made available under the Open Database License (ODbL) v1.0.\n'
        'License: https://opendatacommons.org/licenses/odbl/1-0/\n'
        'Original database: https://cnrpark.it/\n'
        f'Author distribution and annotation revision: https://github.com/DSBD-Research/MetaPKLot-Dataset/tree/{COMMIT}\n'
        'See ATTRIBUTION.md, selection.json and manifest.json for attribution, changes and source identifiers.\n'
        'The ODbL notice applies to this data directory separately from the application source license.\n',encoding='utf-8')
    audit_external_against_extension()
    print(json.dumps(audit),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache',type=Path,default=ROOT/'.build/real_optimization/training')
    parser.add_argument('--annotations-cache',type=Path,default=ROOT/'.build/real_download')
    parser.add_argument('--reuse-originals',type=Path)
    parser.add_argument('--github-cli',type=Path)
    parser.add_argument('--workers',type=int,choices=range(1,9),default=4)
    parser.add_argument('--selection-only',action='store_true')
    parser.add_argument('--task',choices=['pk-training','cnr-external'],default='pk-training')
    args=parser.parse_args()
    if args.task=='pk-training':
        prepare_pk_training(args)
    else:
        prepare_cnr_external(args)


if __name__=='__main__':
    main()

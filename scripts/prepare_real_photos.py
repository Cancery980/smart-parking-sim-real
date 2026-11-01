"""Fetch a date-disjoint, metadata-selected PKLot subset with author annotations.

No model output or occupancy label is used to select images. The original images
are preserved byte-for-byte. Derived annotations are separately stored for scoring.
"""
from __future__ import annotations
import argparse
from collections import defaultdict, Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import tarfile
import time
import urllib.request
import base64
import subprocess

ROOT=Path(__file__).resolve().parents[1]
COMMIT='9218800c4eb2b7c1b17b51c8cdb07ad51686f8a2'
UPSTREAM='https://raw.githubusercontent.com/DSBD-Research/MetaPKLot-Dataset/'+COMMIT+'/'
VIEWS=['ufpr04','ufpr05','pucpr']
WEATHER={1:'Cloudy',2:'Sunny',3:'Rainy'}
def sha(data):return hashlib.sha256(data).hexdigest()
def key(value):return sha(str(value).encode())
def download(url,target,*,github_cli=None):
    if target.exists():return target.read_bytes()
    target.parent.mkdir(parents=True,exist_ok=True)
    for attempt in range(3):
        try:
            if github_cli:
                prefix='https://raw.githubusercontent.com/DSBD-Research/MetaPKLot-Dataset/'
                assert url.startswith(prefix)
                ref,name=url[len(prefix):].split('/',1)
                response=subprocess.run([str(github_cli),'api','repos/DSBD-Research/MetaPKLot-Dataset/contents/'+name+'?ref='+ref],capture_output=True,check=True,timeout=90)
                blob=json.loads(response.stdout)
                if blob.get('encoding')=='none':
                    response=subprocess.run([str(github_cli),'api','repos/DSBD-Research/MetaPKLot-Dataset/git/blobs/'+blob['sha']],capture_output=True,check=True,timeout=90)
                    blob=json.loads(response.stdout)
                assert blob['encoding']=='base64'
                data=base64.b64decode(blob['content'])
                assert hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()==blob['sha']
            else:
                with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'SmartParkingCourse/1.1'}),timeout=60) as r: data=r.read()
            temporary=target.with_suffix(target.suffix+'.part')
            temporary.write_bytes(data);temporary.replace(target)
            return data
        except (OSError,subprocess.SubprocessError):
            if attempt==2:raise
            time.sleep(attempt+1)
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache',type=Path,default=ROOT/'.build/real_download')
    args=parser.parse_args();cache=args.cache.resolve();cache.mkdir(parents=True,exist_ok=True)
    output=ROOT/'data/real';output.mkdir(parents=True,exist_ok=True)
    loaded={};bundles=[]
    def load(pair):
        view,kind=pair
        relative=f'annotations/original/{kind}/PKLot/{view}_{kind}.tar.xz'
        target=cache/f'{view}_{kind}.tar.xz'
        data=download(UPSTREAM+relative,target)
        with tarfile.open(target,'r:xz') as archive:
            member=next(m for m in archive if m.isfile() and m.name.endswith('.json'))
            record=json.load(archive.extractfile(member))
        return pair,record,{'url':UPSTREAM+relative,'sha256':sha(data),'bytes':len(data)}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for pair,data,bundle in pool.map(load,[(v,k) for v in VIEWS for k in ['cars','spots']]):
            loaded[pair]=data;bundles.append(bundle)
    groups=defaultdict(lambda:defaultdict(list))
    annotations={};metadata={}
    for view in VIEWS:
        cars,spots=loaded[view,'cars'],loaded[view,'spots']
        car_categories={x['id'] for x in cars['categories'] if x['name']=='car'}
        status_map={x['id']:('free' if x['name']=='empty' else 'occupied') for x in spots['categories']}
        assert set(status_map.values())=={'free','occupied'}
        car_by_image=defaultdict(list);spot_by_image=defaultdict(list)
        for a in cars['annotations']:
            if a['category_id'] in car_categories:car_by_image[a['image_id']].append(a)
        for a in spots['annotations']:spot_by_image[a['image_id']].append(a)
        spot_images={im['file_name']:im for im in spots['images']}
        for im in cars['images']:
            if im['climate'] not in WEATHER or im['file_name'] not in spot_images:continue
            si=spot_images[im['file_name']]
            date='-'.join(f'{part:02}' if index else str(part) for index,part in enumerate(im['date']))
            group=(view,WEATHER[im['climate']])
            groups[group][date].append(im['file_name'])
            metadata[im['file_name']]={**im,'date_text':date,'weather':group[1]}
            annotations[im['file_name']]={'cars':car_by_image[im['id']],'spots':spot_by_image[si['id']],'status_map':status_map}
    # Find a date-only assignment with enough days in every weather stratum.
    dates=sorted({date for days in groups.values() for date in days})
    assignment=None
    for seed in range(10000):
        candidate={date:('dev' if int(key(f'parking-real-v1-{seed}-{date}')[:8],16)%3==0 else 'test') for date in dates}
        if all(sum(candidate[d]=='dev' for d in days)>=2 and sum(candidate[d]=='test' for d in days)>=4 for days in groups.values()):
            assignment=candidate;break
    assert assignment is not None
    chosen=[]
    for (view,weather),days in sorted(groups.items()):
        for split,n in [('dev',2),('test',4)]:
            selected=sorted([d for d in days if assignment[d]==split],key=lambda d:key(f'{view}/{weather}/{d}'))[:n]
            for date in selected:
                name=min(days[date],key=lambda x:key('image-v1/'+x))
                chosen.append((split,name))
    # Freeze before downloading, viewing, or invoking any model.
    protocol={'schema_version':1,'upstream_commit':COMMIT,'selection':'2 development and 4 test frames per view/weather; one timestamp per selected date within stratum; hash ordering, no model or label filtering',
              'date_assignment_seed':seed,'date_assignment':assignment,'selection_names':chosen,
              'dev_frames':18,'test_frames':36,'views':VIEWS,'weather':list(WEATHER.values()),
              'physical_parking_lots':2,'training_on_this_subset':False,
              'split_boundary':'Entire calendar date. No date appears in both dev and test. Frames from one fixed camera/day are correlated; this is not the official cross-dataset MetaPKLot benchmark.',
              'upstream_annotation_bundles':bundles}
    (output/'protocol.json').write_text(json.dumps(protocol,ensure_ascii=False,indent=2),encoding='utf-8')
    def prepare(item):
        split,name=item;im=metadata[name];source_annotations=annotations[name]
        photo_id=im['subset']+'_'+Path(name).stem
        target=output/'images'/f'{photo_id}.jpg'
        original=download(UPSTREAM+name,target)
        from PIL import Image
        with Image.open(target) as image:assert image.size==(im['width'],im['height']);image.verify()
        geometry=[];truth={}
        # Stable spatial ordering; IDs do not encode occupied/empty status.
        for index,a in enumerate(sorted(source_annotations['spots'],key=lambda a:(a['bbox'][1],a['bbox'][0]))):
            segmentation=a['segmentation'];assert len(segmentation)==1
            poly=segmentation[0];assert len(poly)>=6 and len(poly)%2==0
            sid=f'S{index+1:03d}'
            geometry.append({'spot_id':sid,'polygon_px':[[poly[i],poly[i+1]] for i in range(0,len(poly),2)]})
            truth[sid]=source_annotations['status_map'][a['category_id']]
        photo={'photo_id':photo_id,'dataset':'PKLot / MetaPKLot','view':im['subset'].upper(),'weather':im['weather'],'date':im['date_text'],
               'capture_time':im['time'],'split':split,'image':target.relative_to(ROOT).as_posix(),'width':im['width'],'height':im['height'],
               'sha256':sha(original),'source_url':UPSTREAM+name,'spots':geometry}
        truth_record={'roi_xywh':im.get('annotationsRectangle',[0,0,im['width'],im['height']]),
                      'vehicles':[{'bbox_xywh':a['bbox'],'car_id':a.get('car_id'),'annotation_id':a['id']} for a in source_annotations['cars']],
                      'occupancy':truth,'source_image_id':im['id']}
        return photo,truth_record
    with ThreadPoolExecutor(max_workers=6) as pool:prepared=list(pool.map(prepare,chosen))
    photos=[p for p,t in prepared];truths={p['photo_id']:t for p,t in prepared}
    assert len({p['sha256'] for p in photos})==len(photos)
    split_dates={s:{p['date'] for p in photos if p['split']==s} for s in ['dev','test']}
    assert not split_dates['dev']&split_dates['test']
    manifest={'schema_version':1,'source':{'name':'PKLot photographs and MetaPKLot revised annotations','upstream_commit':COMMIT,'license':'CC BY 4.0 (PKLot component)','attribution':'Paulo de Almeida et al. (PKLot, 2015); DSBD Research / MetaPKLot authors (2026)','modifications':'Metadata-only stratified subset; unchanged JPEG bytes; polygons and labels converted to separate runtime geometry and offline ground truth'},'photos':photos}
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'annotations.json').write_text(json.dumps({'photos':truths},ensure_ascii=False,indent=2),encoding='utf-8')
    audit={'photos':len(photos),'unique_image_hashes':len({p['sha256'] for p in photos}),'split_counts':dict(Counter(p['split'] for p in photos)),
           'selected_dates':{s:sorted(d) for s,d in split_dates.items()},'cross_split_date_intersection':[],
           'manifest_sha256':sha((output/'manifest.json').read_bytes()),'annotations_sha256':sha((output/'annotations.json').read_bytes()),
           'protocol_sha256':sha((output/'protocol.json').read_bytes())}
    (output/'audit.json').write_text(json.dumps(audit,indent=2),encoding='utf-8')
    print(json.dumps(audit))
if __name__=='__main__':main()

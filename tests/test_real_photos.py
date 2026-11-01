"""Geometry/API checks with explicit detector doubles, not real-model accuracy."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from parking_hu.real_photos import RealPhotoError, RealPhotoService, estimate_photo_occupancy


def detection(bounds, confidence=.9, did='d0'):
    return {'detection_id':did, 'bbox_xyxy_px':bounds,'confidence':confidence,'class_name':'car'}


def spot(points=None):
    return {'spot_id':'S1','polygon_px':points or [[0,0],[10,0],[10,10],[0,10]]}


def test_polygon_geometry_excludes_bbox_corner():
    triangle = spot([[0,0],[10,0],[0,10]])
    result = estimate_photo_occupancy([triangle],[detection([7,7,9,9])])[0]
    assert result['state'] == 'free' and result['evidence'] == []


def test_boundary_center_is_ambiguous_and_overlap_measured_against_polygon():
    # Center (5,5) lies on the diagonal boundary, overlap is 2/50.
    triangle = spot([[0,0],[10,0],[0,10]])
    row = estimate_photo_occupancy([triangle],[detection([4,4,6,6])],overlap_fraction=.04)[0]
    assert row['state'] == 'unknown'
    assert row['evidence'][0]['center_inside'] is False
    assert row['evidence'][0]['overlap_fraction'] == pytest.approx(.04)
    assert row['evidence'][0]['used'] is True


def test_confidence_thresholds_and_used_evidence():
    rows = [detection([1,1,5,5],.199,'low'),detection([6,1,9,6],.20,'weak')]
    result = estimate_photo_occupancy([spot()],rows)[0]
    assert result['state'] == 'unknown' and result['detection_ids'] == ['weak']
    assert len(result['evidence']) == 1
    rows.append(detection([1,1,3,3],.50,'strong'))
    result = estimate_photo_occupancy([spot()],rows)[0]
    assert result['state'] == 'occupied' and result['detection_ids'] == ['strong']
    assert {d['detection_id']:d['used'] for d in result['evidence']} == {'weak':False,'strong':True}


class FixedPhotoDetector:
    device = 'test-double'
    def __init__(self):
        self.calls = 0
        self.rows = [detection([3,3,8,9])]
        self.error = False
    def detect(self, path):
        assert Path(path).is_file()
        self.calls += 1
        if self.error:
            raise RuntimeError('injected failure')
        return deepcopy(self.rows)


@pytest.fixture
def photo_root(tmp_path):
    (tmp_path/'data/real/images').mkdir(parents=True)
    (tmp_path/'models').mkdir()
    (tmp_path/'static').mkdir()
    (tmp_path/'reports').mkdir()
    image = tmp_path/'data/real/images/real1.jpg'
    Image.new('RGB',(32,24),'#817b6f').save(image)
    (tmp_path/'models/test.pt').write_bytes(b'explicit model placeholder for detector double')
    row = {'photo_id':'real1','dataset':'test fixture','view':'camera1','weather':'sunny','date':'2020-01-01',
           'split':'test','image':'data/real/images/real1.jpg','width':32,'height':24,
           'sha256':hashlib.sha256(image.read_bytes()).hexdigest(),'source_url':'https://example.org/photo',
           'spots':[spot()], 'truth':'must never be served'}
    (tmp_path/'data/real/manifest.json').write_text(json.dumps({'schema_version':1,'photos':[row],'source':{'name':'test fixture'}}),encoding='utf-8')
    (tmp_path/'data/real/annotations.json').write_text('NOT VALID JSON; inference must never open this',encoding='utf-8')
    (tmp_path/'models/real_photo_config.json').write_text(json.dumps({'weights':'models/test.pt','imgsz':640,'classes':[2]}),encoding='utf-8')
    (tmp_path/'static/real.html').write_text('<html>真实照片</html>',encoding='utf-8')
    (tmp_path/'static/index.html').write_text('<html>simulation</html>',encoding='utf-8')
    (tmp_path/'reports/real_photo_report.html').write_text('<html>measured report</html>',encoding='utf-8')
    return tmp_path


def test_every_call_infers_and_catalog_excludes_truth_and_paths(photo_root, monkeypatch):
    detector = FixedPhotoDetector()
    service = RealPhotoService(photo_root,detector)
    original_read = Path.read_text
    def guarded_read(path,*args,**kwargs):
        assert path.name != 'annotations.json','runtime read offline truth'
        return original_read(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',guarded_read)
    first, second = service.analyze('real1'),service.analyze('real1')
    assert detector.calls == 2 and first['frame_id'] != second['frame_id']
    assert first['spots'][0]['state'] == 'occupied'
    assert first['photo']['width'] == 32 and first['photo']['height'] == 24
    assert first['counts'] == {'free':0,'occupied':1,'unknown':0}
    catalog = service.catalog()
    assert all(k not in catalog['photos'][0] for k in ['spots','truth','image'])
    assert 'must never be served' not in json.dumps(first)
    catalog['photos'][0]['width'] = 100
    assert service.catalog()['photos'][0]['width'] == 32


@pytest.mark.parametrize('bad_id', [None,[],{},True,'../model','x/y',''])
def test_bad_photo_ids_rejected_before_file_access(photo_root,bad_id):
    with pytest.raises(RealPhotoError) as error:
        RealPhotoService(photo_root,FixedPhotoDetector()).analyze(bad_id)
    assert error.value.status == 422


def test_missing_id_is_404(photo_root):
    with pytest.raises(RealPhotoError) as error:
        RealPhotoService(photo_root,FixedPhotoDetector()).analyze('does-not-exist')
    assert error.value.status == 404


def test_wrong_image_dimensions_and_changed_bytes_are_rejected(photo_root):
    service = RealPhotoService(photo_root,FixedPhotoDetector())
    service.catalog()
    image = photo_root/'data/real/images/real1.jpg'
    Image.new('RGB',(32,24),'white').save(image)
    with pytest.raises(RealPhotoError,match='文件缺失、损坏'):
        service.analyze('real1')
    service._photos['real1']['sha256'] = hashlib.sha256(image.read_bytes()).hexdigest()
    service._photos['real1']['width'] = 31
    with pytest.raises(RealPhotoError,match='文件缺失、损坏'):
        service.analyze('real1')


def test_unsafe_manifest_path_is_not_opened(photo_root):
    manifest_path = photo_root/'data/real/manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest['photos'][0]['image'] = '../outside.jpg'
    manifest_path.write_text(json.dumps(manifest),encoding='utf-8')
    with pytest.raises(RealPhotoError) as error:
        RealPhotoService(photo_root,FixedPhotoDetector()).catalog()
    assert error.value.code == 'real_photo_data_unavailable'


def test_invalid_model_config_and_missing_model_are_service_errors(photo_root):
    config_path = photo_root/'models/real_photo_config.json'
    config_path.write_text(json.dumps({'weights':'models/test.pt','conf':.8,'high':.5}),encoding='utf-8')
    with pytest.raises(RealPhotoError):
        RealPhotoService(photo_root,FixedPhotoDetector()).catalog()
    config_path.write_text(json.dumps({'weights':'models/missing.pt'}),encoding='utf-8')
    with pytest.raises(RealPhotoError):
        RealPhotoService(photo_root,FixedPhotoDetector()).catalog()


@pytest.mark.parametrize('bad_row', [detection([1,1,33,12]), detection([1,1,3,4],float('nan')),{}])
def test_bad_model_output_cannot_claim_valid_frame(photo_root,bad_row):
    detector=FixedPhotoDetector()
    detector.rows=[bad_row]
    with pytest.raises(RealPhotoError) as error:
        RealPhotoService(photo_root,detector).analyze('real1')
    assert error.value.status==503 and error.value.code=='photo_inference_failed'


def test_api_failure_recovery_and_simulation_isolation(photo_root,monkeypatch):
    import parking_hu.api as api
    monkeypatch.setattr(api,'ROOT',photo_root)
    class OriginalSession:
        def state(self): return {'session':{'state':'parked','actual_spot_id':'P002'}}
    app=api.create_app(OriginalSession())
    detector=FixedPhotoDetector()
    app.state.real_photos=RealPhotoService(photo_root,detector)
    with TestClient(app) as client:
        before=client.get('/api/state').json()
        assert client.get('/real').status_code==200
        assert client.get('/api/real/report').status_code==200
        assert client.get('/api/real/photos').json()['photos'][0]['photo_id']=='real1'
        assert client.get('/api/real/image/real1').status_code==200
        assert client.get('/api/real/image/missing').status_code==404
        for payload in ([],{}, {'photo_id':[]},{'photo_id':False}):
            result=client.post('/api/real/analyze',json=payload)
            assert result.status_code==422 and result.json()['message']
        result=client.post('/api/real/analyze',content='{broken',headers={'Content-Type':'application/json'})
        assert result.status_code==422 and '请求格式' in result.json()['message']
        detector.error=True
        result=client.post('/api/real/analyze',json={'photo_id':'real1'})
        assert result.status_code==503 and result.json()['reason_code']=='photo_inference_failed'
        detector.error=False
        assert client.post('/api/real/analyze',json={'photo_id':'real1'}).json()['status']=='ok'
        assert client.get('/api/state').json()==before

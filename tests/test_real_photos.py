"""Geometry/API checks with explicit detector doubles, not real-model accuracy."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from parking_hu.real_photos import PhotoDetector, RealPhotoError, RealPhotoService, estimate_photo_occupancy


def detection(bounds, confidence=.9, did='d0'):
    return {'detection_id':did, 'bbox_xyxy_px':bounds,'confidence':confidence,'class_name':'car'}


def spot(points=None):
    return {'spot_id':'S1','polygon_px':points or [[0,0],[10,0],[10,10],[0,10]]}


def test_roi_detection_returns_original_image_coordinates(tmp_path):
    """A cropped model input must still align with full-photo parking polygons."""
    from types import SimpleNamespace
    import threading
    import torch
    path = tmp_path/'photo.png'
    Image.new('RGB', (32,24)).save(path)
    calls = []
    class CropModel:
        def predict(self, **kwargs):
            calls.append(kwargs)
            assert kwargs['source'].size == (20,16)
            return [SimpleNamespace(orig_shape=(16,20), boxes=SimpleNamespace(
                xyxy=torch.tensor([[2.,3.,12.,14.]]), conf=torch.tensor([.8])))]
    detector = PhotoDetector.__new__(PhotoDetector)
    detector.model, detector._lock = CropModel(), threading.Lock()
    detector.conf, detector.nms_iou, detector.augment = .2, .65, False
    detector.imgsz, detector.device, detector.classes = 1280, 'cpu', [0]
    result = detector.detect(path, roi_xywh=[5,6,20,16])
    assert result[0]['bbox_xyxy_px'] == [7.,9.,17.,20.]
    assert calls[0]['iou'] == .65 and calls[0]['max_det'] == 500
    assert estimate_photo_occupancy([spot([[6,8],[18,8],[18,21],[6,21]])],result)[0]['state'] == 'occupied'
    for invalid in ([5,6,30,16],[-1,0,20,16],[0,0,0,16],[0,0,20.5,16],[True,0,20,16]):
        with pytest.raises(ValueError):
            detector.detect(path, roi_xywh=invalid)
    assert len(calls) == 1


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


def test_partial_inference_region_cannot_establish_free_space():
    partial = estimate_photo_occupancy([spot()],[],observed_roi_xywh=[0,0,5,10])[0]
    assert partial['state'] == 'unknown' and partial['reason'] == 'incomplete_observation'
    assert partial['observation_fraction'] == pytest.approx(.5)
    assert partial['fully_observed'] is False
    occupied = estimate_photo_occupancy([spot()],[detection([1,1,3,3])],observed_roi_xywh=[0,0,5,10])[0]
    assert occupied['state'] == 'occupied' and occupied['fully_observed'] is False
    complete = estimate_photo_occupancy([spot()],[],observed_roi_xywh=[0,0,10,10])[0]
    assert complete['state'] == 'free' and complete['fully_observed'] is True
    assert complete['observation_fraction'] == 1


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


def test_detection_threshold_does_not_remove_uncertain_occupancy_evidence(photo_root):
    config_path = photo_root/'models/real_photo_config.json'
    config = json.loads(config_path.read_text(encoding='utf-8'))
    config['detection_threshold'] = .60
    config_path.write_text(json.dumps(config),encoding='utf-8')
    detector = FixedPhotoDetector()
    detector.rows = [detection([3,3,8,9], confidence=.35)]
    result = RealPhotoService(photo_root,detector).analyze('real1')
    assert result['detection_counts'] == {'accepted':0,'evidence_only':1,'retained':1}
    assert result['detections'][0]['accepted'] is False
    assert result['spots'][0]['state'] == 'unknown'
    assert result['spots'][0]['evidence'][0]['used'] is True
    assert result['thresholds'] == {'low':.20,'high':.50,'detection':.60,'overlap_fraction':.05}


def test_service_keeps_partly_unobserved_space_unknown(photo_root):
    config_path = photo_root/'models/real_photo_config.json'
    config = json.loads(config_path.read_text(encoding='utf-8'))
    config.update(roi_policy='calibrated',roi_by_view={'camera1':[0,0,5,24]})
    config_path.write_text(json.dumps(config),encoding='utf-8')
    class CroppedEmptyDetector(FixedPhotoDetector):
        def detect(self,path,*,roi_xywh):
            assert roi_xywh == [0,0,5,24]
            return []
    result = RealPhotoService(photo_root,CroppedEmptyDetector()).analyze('real1')
    assert result['counts'] == {'free':0,'occupied':0,'unknown':1}
    assert result['spots'][0]['observation_fraction'] == pytest.approx(.5)
    assert result['spots'][0]['reason'] == 'incomplete_observation'


def test_selected_inference_options_reach_runtime_without_truth(photo_root, monkeypatch):
    import parking_hu.real_photos as module
    config_path = photo_root/'models/real_photo_config.json'
    config = json.loads(config_path.read_text(encoding='utf-8'))
    config.update(nms_iou=.60,augment=True,roi_policy='calibrated',roi_by_view={'camera1':[1,2,30,20]})
    config_path.write_text(json.dumps(config),encoding='utf-8')
    calls = []
    class SelectedDetector(FixedPhotoDetector):
        def __init__(self, weights, **kwargs):
            super().__init__()
            calls.append(kwargs)
        def detect(self, path, *, roi_xywh):
            assert roi_xywh == [1,2,30,20]
            return super().detect(path)
    monkeypatch.setattr(module,'PhotoDetector',SelectedDetector)
    result = RealPhotoService(photo_root).analyze('real1')
    assert calls[0]['nms_iou'] == .60 and calls[0]['augment'] is True
    assert result['photo']['inference_roi_xywh'] == [1,2,30,20]
    assert result['model']['roi_policy'] == 'calibrated'


def test_extra_manifest_preserves_image_geometry_and_rejects_duplicate_ids(photo_root):
    manifest_path = photo_root/'data/real/manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest['source']['license'] = 'CC BY 4.0'
    manifest_path.write_text(json.dumps(manifest),encoding='utf-8')
    extra = deepcopy(manifest)
    extra['source'].update(name='external fixture',license='ODbL-1.0',truth='must not be served')
    extra['photos'][0].update(photo_id='external1',dataset='external fixture',split='external_test')
    extra_path = photo_root/'data/external.json'
    extra_path.write_text(json.dumps(extra),encoding='utf-8')
    config_path = photo_root/'models/real_photo_config.json'
    config = json.loads(config_path.read_text(encoding='utf-8'))
    config['extra_manifests'] = ['data/external.json']
    config_path.write_text(json.dumps(config),encoding='utf-8')
    service = RealPhotoService(photo_root,FixedPhotoDetector())
    assert len(service.catalog()['photos']) == 2
    sources = service.catalog()['source']['components']
    assert [source['license'] for source in sources] == ['CC BY 4.0','ODbL-1.0']
    assert all('truth' not in source for source in sources)
    assert service.analyze('external1')['photo']['split'] == 'external_test'
    extra['photos'][0]['photo_id'] = 'real1'
    extra_path.write_text(json.dumps(extra),encoding='utf-8')
    with pytest.raises(RealPhotoError):
        RealPhotoService(photo_root,FixedPhotoDetector()).catalog()


def test_full_image_policy_does_not_take_roi_from_manifest(photo_root):
    path = photo_root/'data/real/manifest.json'
    manifest = json.loads(path.read_text(encoding='utf-8'))
    manifest['photos'][0]['inference_roi_xywh'] = [5,5,10,10]
    path.write_text(json.dumps(manifest),encoding='utf-8')
    result = RealPhotoService(photo_root,FixedPhotoDetector()).analyze('real1')
    assert result['model']['roi_policy'] == 'full'
    assert 'inference_roi_xywh' not in result['photo']


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

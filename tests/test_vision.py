"""Behavioral tests for frame safety, boundary semantics and truth separation."""
import copy

import pytest

from parking_hu.map_data import build_map
from parking_hu.occupancy import estimate_occupancy
from parking_hu.scenes import make_scene,render_scene,truth_occupancy


def detection(rect_m, confidence=.9, did="d1", **kw):
    return {"detection_id":did,"bbox_xyxy_px":[x*32 for x in rect_m],
            "class_name":"car","confidence":confidence,**kw}


def test_high_center_and_weak_overlap_are_distinct():
    m=build_map()
    ds=[detection([5.5,1.8,7,6.1]),detection([8,1.8,9.5,6.1],.3,"d2")]
    result=estimate_occupancy(m,ds,1,"f")
    assert result["occupancy"]["P001"]["state"]=="occupied"
    assert result["occupancy"]["P002"]["state"]=="unknown"
    assert result["occupancy"]["P003"]["state"]=="free"
    assert len(result["obstacles_m"])==2
    assert "bbox_m" not in ds[0],"Estimator must not mutate the detector output"


def test_boundary_center_marks_both_spaces_unknown():
    m=build_map()
    result=estimate_occupancy(m,[detection([6.6,1.8,8.4,6.1])],1,"f")
    assert result["occupancy"]["P001"]["state"]=="unknown"
    assert result["occupancy"]["P002"]["state"]=="unknown"


def test_confidence_equality_and_clear_evidence_priority():
    m=build_map()
    result=estimate_occupancy(m,[detection([5.5,1.8,7,6.1],.50),
                               detection([6.6,1.8,8.4,6.1],.20,"d2"),
                               detection([10.5,1.8,12,6.1],.199,"d3")],1,"f")
    assert result["occupancy"]["P001"]["state"]=="occupied"
    assert result["occupancy"]["P002"]["state"]=="unknown"
    assert result["occupancy"]["P003"]["state"]=="free"
    assert len(result["obstacles_m"])==2


def test_overlap_threshold_is_inclusive():
    # 0.125 / 2.5 == 5 percent of this full-height space.
    result=estimate_occupancy(build_map(),[detection([5,1.3,5.125,6.6],.2)],1,"f")
    assert result["occupancy"]["P001"]["state"]=="unknown"


@pytest.mark.parametrize("bad",[
    detection([-1,2,1,3]),detection([2,2,1,3]),detection([0,0,53,1]),
    detection([1,1,2,2],float("nan")),detection([1,1,2,2],1.1),
    detection([1,1,float("nan"),2]),detection([1,1,2,2],.1,frame_id="other"),
    detection([1,1,2,2],.1,scene_version=99)])
def test_malformed_detection_invalidates_entire_frame(bad):
    result=estimate_occupancy(build_map(),[bad],1,"f")
    assert result["valid"] is False
    assert all(v["state"]=="unknown" for v in result["occupancy"].values())
    assert result["obstacles_m"]==[]


def test_duplicate_id_and_invalid_frame_have_no_stale_boxes():
    m=build_map(); d=detection([5.5,1.8,7,6.1])
    assert estimate_occupancy(m,[d,d],1,"f")["valid"] is False
    result=estimate_occupancy(m,[d],1,"f",valid=False,error="inference_failed")
    assert result["detections"]==[]
    assert result["error"]=="inference_failed"


def test_binary_comparison_preserves_all_obstacles():
    m=build_map(); ds=[detection([5.5,1.8,7,6.1],.3),detection([40,12,44,14],.25,"d2")]
    tri=estimate_occupancy(m,ds,1,"f")
    binary=estimate_occupancy(m,ds,1,"f",policy="binary")
    assert tri["occupancy"]["P001"]["state"]=="unknown"
    assert binary["occupancy"]["P001"]["state"]=="free"
    assert tri["obstacles_m"]==binary["obstacles_m"]


def test_synthetic_scene_determinism_and_truth_is_separate(tmp_path):
    m=build_map();s=make_scene(m,123,"normal")
    assert s==make_scene(m,123,"normal")
    assert len({v["vehicle_id"] for v in s["vehicles"]})==len(s["vehicles"])
    path=render_scene(m,s,tmp_path/"test.png")
    from PIL import Image
    with Image.open(path) as im: assert im.size==(1664,1024)
    truth=truth_occupancy(m,s)
    assert "occupied" in truth.values()
    # Complete misses remain observed-free, never repaired from rendering truth.
    snap=estimate_occupancy(m,[],1,"f")
    assert all(v["state"]=="free" for v in snap["occupancy"].values())


def test_truth_positive_area_boundary_crossing():
    m=build_map()
    s={"vehicles":[{"rect_m":[6.6,1.8,8.4,6.1]}]}
    truth=truth_occupancy(m,s)
    assert truth["P001"]==truth["P002"]=="occupied"
    assert truth["P003"]=="free"

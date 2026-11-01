from scripts.compare_real_candidates import select_threshold


def test_zero_detections_remain_unselectable_instead_of_crashing():
    empty = {'confidence':.8,'precision':None,'recall':0.,'f1':0.}
    useful = {'confidence':.3,'precision':.98,'recall':.99,'f1':.985}
    assert select_threshold([empty]) is None
    assert select_threshold([empty,useful]) == useful


def test_high_precision_cannot_hide_recall_regression():
    baseline = {'precision':.95,'recall':.99}
    sacrificing_recall = {'confidence':.8,'precision':1.,'recall':.98,'f1':.9899}
    joint_improvement = {'confidence':.3,'precision':.97,'recall':.995,'f1':.9823}
    assert select_threshold([sacrificing_recall],baseline) is None
    assert select_threshold([sacrificing_recall,joint_improvement],baseline) == joint_improvement

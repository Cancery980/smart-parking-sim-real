"""Build the v1.2.1 Hu-module course DOCX/HTML from recorded evidence.

Run after evaluate_real_optimization.py and report_real_optimization.py.
No inference or model selection runs here. Missing or mismatched final evidence
fails before either course deliverable is replaced.
"""
from pathlib import Path
import base64
import hashlib
import html
import json
import xml.etree.ElementTree as ET
from docx import Document
from docx.shared import Inches, Pt, Cm, RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

ROOT=Path(__file__).resolve().parents[1]
REPORT=ROOT/'reports'
def read(path): return json.loads((ROOT/path).read_text(encoding='utf-8'))
def sha(path): return hashlib.sha256((ROOT/path).read_bytes()).hexdigest()

real_selection_path='reports/real_optimization/frozen_selection.json'
real=read('reports/real_optimization/final/summary.json')
selection=read(real_selection_path)
real_model=read('models/real_photo_model_card.json')
real_config=selection['selected_config']
real_id=real['selected_id']
if real['frozen_selection_sha256']!=sha(real_selection_path) or real_id!=real_config['id']:
    raise ValueError('Final evaluation does not match the frozen selection')
if real_model['sha256']!=real_config['weights_sha256'] or sha(real_model['model'])!=real_model['sha256']:
    raise ValueError('Update the selected model card before building the v1.2 course report')
real_groups={}
for name in ['legacy_test','external_test']:
    dataset=real['datasets'][name]
    spec=dataset['dataset']
    if spec!=selection['datasets'][name] or sha(spec['manifest'])!=spec['manifest_sha256'] or sha(spec['annotations'])!=spec['annotations_sha256']:
        raise ValueError(f'{name}: frozen data provenance differs from final evaluation')
    manifest=read(spec['manifest'])
    photos=[photo for photo in manifest['photos'] if photo['split']==spec['split']]
    measured=dataset['comparisons'][real_id]
    dates={photo['date'] for photo in photos}
    if len(photos)!=spec['images'] or len(photos)!=measured['images'] or len(dates)!=measured['dates']:
        raise ValueError(f'{name}: manifest counts differ from measured results')
    if measured['config']!=real_config:
        raise ValueError(f'{name}: measured selected configuration differs from frozen selection')
    real_groups[name]={'photos':photos,'manifest':manifest,'result':measured,'comparisons':dataset['comparisons']}
pk_manifest=read(selection['datasets']['legacy_test']['manifest'])
dev_photos=[photo for photo in pk_manifest['photos'] if photo['split']=='dev']
training=read('data/real_optimization/pk_training_selection.json')
real_case_index=read('reports/real_optimization/case_index.json')
if real_case_index['selected_id']!=real_id:
    raise ValueError('Regenerate real_optimization case images for the selected configuration first')
report_date=real['evaluated_at_utc'].split('T')[0]
course_flow=read('reports/course_alignment/portable_flow_validation.json')
course_browser=read('reports/course_alignment/browser_validation.json')
course_registration=read('reports/course_alignment/registration_final_validation.json')
for record in (course_flow,course_browser,course_registration):
    if record.get('version')!='1.2.1' or record.get('passed') is not True or not record.get('checks') or not all(item.get('passed') is True for item in record['checks']):
        raise ValueError('v1.2.1 EXE evidence is missing or failed')
    for name,digest in record['compiled_code_sha256'].items():
        if sha(name)!=digest:
            raise ValueError('v1.2.1 tested source differs: '+name)
if len({record['application_sha256'] for record in (course_flow,course_browser,course_registration)})!=1:
    raise ValueError('v1.2.1 evidence refers to different executables')

vision=read('reports/vision_metrics.json')
explore=read('reports/vision_pretrained_exploration.json')
model=read('models/model_card.json')
if course_flow.get('weights_sha256')!=model['weights_sha256']:
    raise ValueError('v1.2.1 EXE used a different synthetic-scene detector')
e2e=read('reports/e2e/results.json')
pair=read('reports/i01_pairing/summary.json')
plugin=read('reports/plugin_contract_check.json')
ui=read('reports/ui_validation.json')
external_e2e=read('reports/e2e_external/results.json')
modes=read('reports/mode_comparison/summary.json')
suite=ET.parse(REPORT/'deterministic_tests.xml').getroot()
session_suite=ET.parse(REPORT/'session_tests.xml').getroot()
functional_suite=ET.parse(REPORT/'real_optimization/functional_tests.xml').getroot()
functional_cases=list(functional_suite.iter('testcase'))
functional_tests=len(functional_cases)
functional_failures=sum(case.find('failure') is not None or case.find('error') is not None for case in functional_cases)
functional_skipped=sum(case.find('skipped') is not None for case in functional_cases)
if functional_failures or any(int(item.attrib.get(key,0)) for item in functional_suite.iter() for key in ('failures','errors')):
    raise ValueError('The v1.2 functional test XML contains failures or errors')
if not functional_tests:
    raise ValueError('The v1.2 functional test XML has no testcase records')
functional_passed=functional_tests-functional_failures-functional_skipped
course_suite=ET.parse(REPORT/'course_alignment/functional_tests.xml').getroot()
course_cases=list(course_suite.iter('testcase'))
course_functional_tests=len(course_cases)
course_functional_failures=sum(item.find('failure') is not None or item.find('error') is not None for item in course_cases)
course_functional_skipped=sum(item.find('skipped') is not None for item in course_cases)
registration_tests=sum(item.attrib.get('classname')=='tests.test_registration' for item in course_cases)
if not course_functional_tests or course_functional_failures or any(int(item.attrib.get(key,0)) for item in course_suite.iter() for key in ('failures','errors')):
    raise ValueError('v1.2.1 full functional tests are missing or failed')
course_functional_passed=course_functional_tests-course_functional_failures-course_functional_skipped
cases=[x for x in suite.iter('testcase') if not x.attrib.get('classname','').endswith('test_sessions')]+list(session_suite.iter('testcase'))
tests=len(cases)
session_tests=len(list(session_suite.iter('testcase')))
failures=sum(x.find('failure') is not None or x.find('error') is not None for x in cases)
assert failures==0 and e2e['passed']==8 and ui['passed'] is True
def pct(x): return 'N/A' if x is None else f'{x:.2%}'

pages=[]
def page(title):
    part={'title':title,'blocks':[]};pages.append(part);return part['blocks']
def para(p,text): p.append(('p',text))
def heading(p,text): p.append(('h',text))
def table(p,heads,rows): p.append(('table',heads,[[str(c) for c in row] for row in rows]))
def picture(p,path,caption): p.append(('image',str(path),caption))

p=page('智慧停车仿真系统\n胡轩铭模块实现与验证报告')
para(p,f'创新性实验课程项目  |  胡轩铭模块  |  v1.2.1车辆登记与入口修订  |  沿用实拍评估 {report_date}（UTC）')
para(p,'本次以胡轩铭模块交付为主：在52×32米、52车位的二维场景中，对1664×1024像素仿真图运行真实YOLO，完成三态占用、车辆登记、中文界面、单会话、实际停车确认与起点点选。既有规则、双模式和A*参考功能全部保留，供完整演示与联调；梁可可按固定Python接口替换，参考联调结果不代表其正式代码已完成。')
pk_result=real_groups['legacy_test']['result'];cnr_result=real_groups['external_test']['result']
para(p,'完整解压Windows演示包后双击ParkingDemo.exe，默认打开本机 / 二维仿真页。课堂先展示胡模块与参考规划的停车闭环；实拍照片作为附录扩展，保留外部退步和失败案例，不用于真实场地米制导航。')
heading(p,'责任边界与实现')
table(p,['模块','本次交付','交接关系'],[
['场景与数据','52位地图、原图、标签、冻结划分','统一地图由双方共用'],
['视觉感知','真实模型推理、检测框、三态占用','只向规划器提供感知快照'],
['界面与会话','登记、申请、重规划、确认与寻车','消费规划结果并管理事实'],
['保留的参考规划','硬约束、双模式、车行及人行A*','梁可负责范围，可替换 Planner 类'],
['验证与报告','Hu功能、视觉及与参考规划的联调','提供缓存和对照复跑脚本']])
heading(p,'数据流与三个隔离边界')
para(p,'场景生成原图 → YOLO 检测 → 三态占用与全部保留车框 → 资格及路线约束 → 推荐显示 → 人工确认实际位置 → 从手选起点寻车。scene_truth 仅用于渲染、标注、确认冲突与离线评分，不进入规划器。动力类型、无障碍资格由登记表单录入，充电需求由需求表单确认，均不由YOLO外观推断。人工确认不覆写检测占用，确认后的漏检仍记为感知不一致。')
heading(p,'沿用证据与本次修订')
para(p,f'本次v1.2.1全量功能检查{course_functional_passed}项通过，含登记{registration_tests}项；功能夹具不等于模型精度。沿用{plugin["passed"]}项接线检查、真实YOLO的{e2e["passed"]}/{e2e["total"]}景联调及I01的30场景/60配对请求/120策略行，均明确参考规划归属。本次EXE流程{len(course_flow["checks"])}项、界面{len(course_browser["checks"])}项、登记实操{len(course_registration["checks"])}项通过；历史实验不计作本次重测。')

p=page('课程主体 场景与真实检测')
para(p,'地图采用需求规格书 demo52_v12：52 × 32 米，原点左上、X向右、Y向下；图像 1664 × 1024 像素，比例32像素/米。包含40个普通位、4个无障碍位和8个充电位。检测输入是未经占用颜色或真值框叠加的完整原图。坐标直接取 YOLO 返回的原图边界框，再除以32转换为米。')
table(p,['数据划分','张数','用途'],[
['探索',24,'官方已有权重可行性检查；21张唯一图片'],
['训练',180,'固定种子；169张唯一图片，重复仅空场负样本'],
['验证',30,'模型检查点选择，含横向阻断车辆'],
['独立测试',30,'常规、遮挡、边界/跨位各10景；未参与调参']])
para(p,'训练、验证与测试的图像哈希交集为空，测试图片30张均唯一。固定底图在各划分共用，独立性指场景与种子独立，不代表摄像机、场地或素材域独立。车辆由程序原创绘制，遮挡为明确记录的合成遮罩。')
heading(p,'探索失败与微调依据')
em=explore['metrics']
para(p,f'官方 COCO YOLOv8n 在探索集上的真阳性 TP={em["tp"]}、漏检 FN={em["fn"]}、召回率 {pct(em.get("recall"))}。这表明直接调用通用权重不适合这套绘图域。随后采用同一 YOLOv8n 架构在独立训练集微调，仅用验证集选择最佳检查点，保留探索失败证据。没有从场景真值补写检测框。')
para(p,f'实测软件：Ultralytics {model["ultralytics"]}，PyTorch {model["torch"]}；运行设备为 NVIDIA GeForce RTX 4070 Laptop GPU。推理 imgsz={vision["imgsz"]}，保留阈值0.20，高阈值0.50，车位相交比例阈值5%。模型文件：models/parking_yolov8n.pt。训练参数及日志见 vision_training_config.json 与 vision_training_results.csv。')
para(p,'模型 SHA-256：'+model['weights_sha256'])

p=page('胡轩铭模块 三态占用与视觉评测')
para(p,'先校验整帧图像、检测字段与版本。任一检测框无效或越界时整帧不可用，52位全部 unknown。高置信框中心严格在位内时该位 occupied；其余保留框与车位面积相交不少于5%时为 unknown；无上述证据时为观测 free。明确占用优先于模糊证据。中心距边界不超过1e-6米按边界处理。所有置信度不低于0.20的车框都进入路径障碍，包括没有归入车位的车框。')
heading(p,'独立30景合成图像的模型检测结果')
rows=[]
for name,key in [('保留阈值0.20','detection_conf_020'),('高阈值0.50','detection_conf_050')]:
    m=vision[key];rows.append([name,m['tp'],m['fp'],m['fn'],pct(m['precision']),pct(m['recall'])])
table(p,['统计口径','TP','FP','FN','精确率','召回率'],rows)
para(p,'检测匹配采用 IoU≥0.50、一对一匹配；计数在全部30景聚合。遮挡和跨位按预先生成条件分组，不能按模型置信度挑选。原始分景指标与失败图片保留在 vision_per_scene.csv 和 vision_cases。')
conf=vision['occupancy_confusion']
table(p,['真值 / 感知','free','occupied','unknown'],[[truth,*[conf.get(f'{truth}->{pred}',0) for pred in ['free','occupied','unknown']]] for truth in ['free','occupied']])
para(p,f'总计1560个车位观察，实际占用但观测为空闲的次数为 {vision["false_free_spaces"]}；unknown观察为 {vision["unknown_spaces"]}。上述计数来自真实推理，未用真值修复。完全漏检仍可成为假空位，unknown只能处理被检测器保留的弱证据或跨位证据。')
heading(p,'I01参考适配器配对对照')
table(p,['策略','N','R','F','V','F/R','V/N'],[[s,v['N'],v['R'],v['F'],v['V'],pct(v['false_recommendation_rate']),pct(v['effective_recommendation_rate'])] for s,v in pair['overall'].items()])
para(p,pair['conclusion'])
para(p,'失败抽核：boundary_50003 的P043车辆跨入P044约1.94平方米，检测框中心仍在P043。二态把P044视为空闲且导引线没撞到车框；三态按相交比例超过5%改判unknown。全部9次错荐改善来自跨位组，常规与遮挡组无差异。这组合成对照的保留框均≥0.50，弱置信分支仅有规则验证。')
para(p,'两组使用相同真实检测缓存、全部保留框障碍、资格、路网与排序，仅占用策略不同。每次请求使用独立idle上下文，未推荐仍计入N。真值仅在决策完成后计算F/V。该结果属于随包参考适配器；换梁可实现后应使用相同冻结请求重新运行。')

p=page('胡轩铭模块 会话交互与停车事实')
table(p,['事件','处理结果'],[
['车辆登记','空闲会话新增并选用；4个示例保留，重置不删档案，退出程序清空'],
['申请 / 重规划','最多一个活动会话；前置错误保留原记录，进入求解后的失败清除旧推荐'],
['实际停车确认','先完整校验归属、状态、版本和实体冲突，再一次写入实体、实际位置和新版本'],
['确认后检测失败','保留parked及实际位置；感知不可用，不伪造识别成功'],
['背景更新','清除旧路线有效性，保留已停车车辆；未重新规划不能用旧版本确认'],
['寻车 / 离场','寻车到实际车位外接近点；离场移除当前车辆并重新检测']])
para(p,'登记填写名称或代号、动力类型和资格（待确认/无/有），YOLO不推断身份或资格。胡模块提供手选起点交互并校验返回路线；当前参考规划检查人行可行域，投影至最近人行边，距离不超过0.6米且连线合法。用户车框保留为障碍。确认位置可偏离推荐，特殊位条件不符时记录事实并提示，不当作视觉身份识别。点击车位可查当前帧置信度、中心归属与相交比例。')
shot=REPORT/'ui_live.png'
if shot.exists():picture(p,shot,'图1 保留的v1.2完整联调界面；本版沿用检测模型，并新增车辆登记入口')

p=page('胡模块与参考规划的验收记录')
para(p,f'本次v1.2.1全量功能检查共{course_functional_tests}项：通过{course_functional_passed}项，跳过{course_functional_skipped}项，失败/错误{course_functional_failures}项，含车辆登记{registration_tests}项。统计读取reports/course_alignment/functional_tests.xml。登记覆盖字段校验、空闲会话限制、资格如实记录和重置保留；夹具检查不代表模型精度，实际EXE验收独立保存。')
para(p,f'历史{tests}项仿真/会话记录与v1.2全量{functional_passed}项记录分别保留；版本一致性、真值隔离、确认事务与错误路线拒绝均有检查。历史与本次全量不重复相加为新测试总数。')
table(p,['覆盖范围','对应规格'],[
['地图、52位三条路线、双模式与几何距离','T01-T03、T10-T11、T13、T20'],
['充电、无障碍、复合需求与无位原因','T04-T08、T14'],
['无效帧与旧路线','T09、T15、T17'],
['实际偏离、ID/实体、确认后失败、离场重置','T12、T15-T19']])
heading(p,'E01至E08 使用真实模型与参考规划')
table(p,['编号','验收内容','实测'],[[r['id'],r['expectation'],'通过' if r['outcome']=='passed' else r['outcome']] for r in e2e['cases']])
para(p,'每例保存固定场景、模型输出、请求响应与原始图像。E06、E07保留停车后和障碍更新后的快照；E08确认旧路线立即失效、旧版本不能写入。测试目录 reports/e2e 的 results.json 含模型哈希与逐步操作。未把全部拒绝当作E01-E04通过。')
para(p,f'外部样例{external_e2e["planner"]}委托参考规划，同组{external_e2e["passed"]}景结果保留于reports/e2e_external。既有浏览器28项记录继续保留。本次v1.2.1的EXE流程{len(course_flow["checks"])}项、界面{len(course_browser["checks"])}项、登记实操{len(course_registration["checks"])}项均通过，含CPU真实推理、新登记车辆实际停车与寻车、390px界面及跨位证据点击。记录于reports/course_alignment，绑定同一EXE与源码；不代表梁可正式实现已验收。')

p=page('与梁可模块的交接与复跑')
para(p,'规划插件是无参构造的Python类，提供 plan(context, request) 和 find_vehicle(context, request)。context仅包含contract_version、map、snapshot、vehicle、session；没有scene_truth或实体列表。返回status、reason_code、message、target_spot_id、path_m、distances_m；申请与重规划还须包含三条routes。主模块对返回路径进行独立检查，禁止错误端点、逆向、穿障或缺失节点。')
table(p,['步骤','执行内容'],[
['1 对齐契约','阅读 docs/CONTRACT_V1.md 和 docs/PLANNER_HANDOFF.md；固定字段与米制坐标'],
['2 替换插件','参考 examples/liang_adapter.py，将方法委托替换成梁可实现'],
['3 接线检查','python scripts/check_plugin.py --planner liang_planner:Planner'],
['4 启动联调','python -m parking_hu --planner liang_planner:Planner'],
['5 真实模型验收','python scripts/run_e2e.py --planner liang_planner:Planner --out reports/e2e_liang'],
['6 复跑对照','python scripts/run_pairing.py --planner liang_planner:Planner --output reports/i01_liang']])
para(p,'演示包完整解压后双击ParkingDemo.exe，默认进入 / 二维仿真页，运行环境和CPU模型随包提供。源码版先安装Python3.12并运行setup.ps1，再双击启动项目.cmd。服务仅监听127.0.0.1。README和docs/课程范围与验收.md说明一键启动、模块归属与扩展边界，requirements-lock.txt保留实测依赖。')
heading(p,'结论与限制')
para(p,'胡轩铭模块具备可运行实现、验证记录与稳定接口；参考适配器不能证明梁可尚未提供的代码已集成。前述仿真结论限于固定地图和绘图域；实拍优化及其限制见后续真实照片章节。真实照片未做米制场地标定，因此不生成真实场地导航。自然语言解析、自动定位、身份关联和车辆控制不在本次范围。')
heading(p,'来源与研究依据')
para(p,'需求依据：智慧停车仿真系统需求规格书1.2课堂创新验证修订版（2026-10-21）及本次选题申请表分工。开源实现先检索后复用，原文许可证与来源见 THIRD_PARTY_NOTICES.md 和 third_party。')
para(p,'Ultralytics YOLO：github.com/ultralytics/ultralytics；NetworkX A*：github.com/networkx/networkx；PythonRobotics教学参考：github.com/AtsushiSakai/PythonRobotics。论文核对记录与正式链接见 docs/PAPERS.md。论文说明方法来源，不替代本项目实测。')

p=page('参考规划联调 双模式与最近入口基线')
para(p,'这项参考规划联调实验对应申请表的路网距离及约束违反指标，使用同一30景真实检测缓存、同一三态规则和登记资格。每个请求先得到三条路线都可达的观测空闲候选集合，三种策略仅改变排序。最近入口按入场路网距离最小定义，不使用欧氏直线距离。')
para(p,'保留原60个冻结请求，每个比较三种排序，共180行。由于同景的两种原始偏好重复，本表按每景一个上下文去重：30个独立场景中三种策略都推荐29景、未推荐1景。平均距离只比较三者共同可推荐的29景，未推荐仍保留在推荐率分母中。')
mode_rows=[]
for key,label in [('nearest_entry','最近入口基线'),('near_destination','少走路'),('near_exit','近出口')]:
    item=modes['independent_30_scenes'][key]
    d=item['mean_distances_m']
    mode_rows.append([label,f'{item["recommended"]}/{item["N"]}',f'{d["drive_in"]:.2f}',f'{d["drive_out"]:.2f}',f'{d["walk_to_destination"]:.2f}',str(item['constraint_violation_requests'])+'/'+str(item['truth_violation_requests'])])
table(p,['策略','推荐','入场/m','出场/m','步行/m','规则/真值违反'],mode_rows)
picture(p,REPORT/'mode_comparison/distance_comparison.png','图2 三策略在相同合法可达候选集上的平均路网距离；共同样本29景')
heading(p,'目标收益与代价')
delta=modes['paired_vs_baseline']
walk=delta['near_destination']['delta_vs_nearest_entry_m']
out=delta['near_exit']['delta_vs_nearest_entry_m']
para(p,f'相对最近入口，少走路的平均步行距离减少{-walk["walk_to_destination"]["mean"]:.2f}米，入场距离增加{walk["drive_in"]["mean"]:.2f}米；近出口的平均出场距离减少{-out["drive_out"]["mean"]:.2f}米，入场距离增加{out["drive_in"]["mean"]:.2f}米。不同偏好优化不同目标，不是所有距离同时更短。')
para(p,'感知规则违反包括资格/类型不符、非观测空闲、感知障碍或非法路网；真值核对另计实际占用或真实车体障碍不可达，只在推荐完成后评分。两类违反按有问题的成功请求计数，同一请求多种问题不重复计入总数。本次三策略两类计数均为0。')
para(p,'脚本与证据：scripts/compare_modes.py；reports/mode_comparison中的protocol、requests.csv、candidate_sets、decisions及summary保存协议、共享候选、路线和分组结果。此结果属于随包参考适配器；梁可正式实现接入后须按同样协议重新核对。结论限于当前固定路网与合成数据，不推断现实拥堵或时间收益。')

real_names={'v11_default':'v1.1 默认','v11_dev_calibrated':'v1.1 开发校准',real_id:'本轮选定'}
real_order=list(dict.fromkeys(['v11_default','v11_dev_calibrated',real_id]))
real_titles={'legacy_test':'PKLot 历史回归','external_test':'CNRPark-EXT 外部验证'}
legacy_group=real_groups['legacy_test']
external_group=real_groups['external_test']

def real_detection_rows(group,metric):
    rows=[]
    for config_id in real_order:
        item=group['comparisons'][config_id]
        threshold=.20 if metric=='detection_conf_020' else item['config']['detection_threshold']
        m=item[metric]
        rows.append([real_names[config_id]+f' / {threshold:.2f}',*[m[k] for k in ('tp','fp','fn')],
                     *[pct(m[k]) for k in ('precision','recall','f1')]])
    return rows

def real_delta(after,before):
    changes=[];declines=[]
    for key,label in [('precision','精确率'),('recall','召回率'),('f1','F1')]:
        if after[key] is None or before[key] is None:
            changes.append(label+'不适用');continue
        delta=100*(after[key]-before[key])
        changes.append(f'{label}{delta:+.2f}个百分点')
        if delta < -1e-9:declines.append(f'{label}下降{-delta:.2f}个百分点')
    return '；'.join(changes),declines

def inference_settings(config):
    return (config['weights_sha256'],config['imgsz'],config.get('nms_iou',.70),config.get('augment',False),
            config.get('roi_policy','full_frame'),json.dumps(config.get('roi_by_view',{}),sort_keys=True))

def roi_label(config):
    policy=config.get('roi_policy','full_frame')
    return {'full':'整图','full_frame':'整图','calibrated':'标定区域'}.get(policy,policy)

def decline_note(declines):
    labels=[item.split('下降',1)[0] for item in declines]
    return ' 退步项：'+'、'.join(labels)+'。' if labels else ''

p=page('附录 实拍扩展 数据与冻结协议')
para(p,'以下为已完成的v1.2实拍扩展对照，本次不重新训练。它与课程仿真分开报告，不能替代真实场地导航验收。实验包含PKLot和CNRPark-EXT两套公开相机照片。车辆框用于检测计分；独立车位占用标签用于三态计分。原始 JPEG、固定来源提交、逐图哈希及像素多边形均保留，模型运行时不读取占用真值。')
table(p,['数据角色','张数 / 日期','本轮用途'],[
['候选训练数据',f'{training["train_frames"]} / {training["train_unique_dates"]}',f'原有{training["original_frames_retained"]}张加{training["additional_frames"]}张；仅原训练日期'],
['PKLot 开发集',f'{len(dev_photos)} / {len({photo["date"] for photo in dev_photos})}','选择候选模型、推理设置与车辆检测门槛'],
['PKLot 历史回归',f'{legacy_group["result"]["images"]} / {legacy_group["result"]["dates"]}','v1.1已公开并分析；本轮用于回归比较'],
['CNRPark-EXT 外部验证',f'{external_group["result"]["images"]} / {external_group["result"]["dates"]}','未参与训练、候选排序或阈值选择；冻结后评估']])
for key in ['legacy_test','external_test']:
    group=real_groups[key];photos=group['photos']
    views=sorted({photo['view'] for photo in photos});weather=sorted({photo['weather'] for photo in photos})
    sizes=sorted({(photo['width'],photo['height']) for photo in photos})
    para(p,f'{real_titles[key]}：{len(views)}个相机视角（{"/".join(views)}），{len(weather)}种天气（{"/".join(weather)}）；原图尺寸为'+ '、'.join(f'{w}×{h}' for w,h in sizes)+'像素。')
para(p,f'历史{legacy_group["result"]["images"]}张在v1.1阶段属于固定测试；被公开分析后，本轮准确称为历史回归。外部{external_group["result"]["images"]}张按元数据日期及文件名哈希规则预选；同一相机或日期内的照片仍可能相关，张数不等于独立停车事件数。')
para(p,'最终模型和操作门槛在外部评估前冻结。独立性限定为本次准备与训练过程；有限外部照片不能证明对任意真实停车场、夜间、雪天或更强遮挡都有效。')
heading(p,'来源与许可')
for key,label in [('legacy_test','PKLot'),('external_test','CNRPark-EXT')]:
    source=real_groups[key]['manifest']['source']
    para(p,f'{label}：{source["attribution"]}；许可 {source["license"]}；来源提交 {source["upstream_commit"]}。')
para(p,'PKLot组件适用CC BY 4.0，CNRPark-EXT组件适用ODbL 1.0。源码与模型许可不能覆盖这些数据的独立署名与使用条件。完整来源见docs/REAL_DATA_SOURCES.md、对应数据目录的ATTRIBUTION及THIRD_PARTY_NOTICES.md。')
para(p,'冻结记录：reports/real_optimization/frozen_selection.json；实际汇总：reports/real_optimization/final/summary.json。冻结文件SHA-256：'+real['frozen_selection_sha256'])

p=page('附录 实拍扩展 模型与阈值')
para(p,'以下是沿用的v1.2实拍模型与训练记录，本次仅修订课程说明。训练信息来自交付模型卡，检测成绩来自最终评估输出。候选训练照片总数与交付权重实际训练来源分别保留，避免把准备过的数据误写成所选模型都用过。')
table(p,['字段','记录'],[
['架构 / 文件',real_model['architecture']+' / '+real_model['model']],
['训练 / 开发照片',f'{real_model["training_images"]} / {real_model["validation_images"]}'],
['训练 / 开发日期',f'{real_model["training_dates"]} / {real_model["development_dates"]}'],
['完成轮数 / batch',f'{real_model["epochs_completed"]} / {real_model["batch"]}'],
['模型卡输入尺寸',real_model['imgsz']],
['模型使用范围',f'仅用PKLot开发集选型；{legacy_group["result"]["images"]}张历史回归与{external_group["result"]["images"]}张外部评估不改变冻结模型或门槛。'],
['模型许可','模型/软件：AGPL-3.0；PKLot：CC BY 4.0；CNR：ODbL 1.0。']])
for index,stage in enumerate(real_model.get('training_stages',[]),1):
    if isinstance(stage,dict) and all(key in stage for key in ('训练照片','训练完成轮数','起点')):
        description=f'{stage["训练照片"]}张照片，完成{stage["训练完成轮数"]}轮；起点为{stage["起点"]}。'
    else:
        description=json.dumps(stage,ensure_ascii=False,sort_keys=True) if isinstance(stage,(dict,list)) else str(stage)
    para(p,f'实际训练阶段{index}：{description}')
para(p,f'{real_model["epochs_completed"]}轮为各阶段实际执行轮数之和，各阶段均使用开发集选出的最佳检查点。完整阶段与来源记录见models/real_photo_model_card.json。')
para(p,'所选权重SHA-256：'+real_model['sha256'])
heading(p,'三项冻结对照')
table(p,['对照','权重 / 配置','推理设置'],[
[real_names[config_id],Path(legacy_group['comparisons'][config_id]['config']['weights']).name,
 f'输入{legacy_group["comparisons"][config_id]["config"]["imgsz"]}；检测≥{legacy_group["comparisons"][config_id]["config"]["detection_threshold"]:.2f}\n'
 f'NMS {legacy_group["comparisons"][config_id]["config"].get("nms_iou",.70):.2f}；'
 f'{"有增强" if legacy_group["comparisons"][config_id]["config"].get("augment",False) else "无增强"}；'
 +roi_label(legacy_group['comparisons'][config_id]['config'])]
for config_id in real_order])
default_config=legacy_group['comparisons']['v11_default']['config']
calibrated_config=legacy_group['comparisons']['v11_dev_calibrated']['config']
if inference_settings(default_config)==inference_settings(calibrated_config):
    para(p,'v1.1默认与开发校准两项使用同一权重和推理设置，仅改变检测门槛；两项差值反映操作点变化。所选配置与校准v1.1的比较反映候选模型及设置的整体变化。')
else:
    para(p,'注意：v1.1默认与开发校准两项还存在推理设置差异，不能把其全部差值归因于检测门槛。请结合冻结JSON中的权重、输入尺寸、增强与ROI设置解释。')
para(p,'每组同时报告开发阶段选定门槛与共同0.20门槛。前者对应交付操作点，后者在同一计分门槛下比较原始检测输出。车辆检测使用固定IoU≥0.50的一对一匹配P/R/F1，不是mAP。作者完整标注ROI内中心点参与评分，框裁剪到该ROI；区外未标注对象不直接当作误检。')

for key in ['legacy_test','external_test']:
    group=real_groups[key];measured=group['result'];comparisons=group['comparisons']
    p=page('附录 实拍扩展 '+real_titles[key]+'检测')
    para(p,f'本组{measured["images"]}张、{measured["dates"]}个拍摄日期。以下数值按全部图像汇总TP、FP和FN后计算，保留漏检与误检；未删除困难照片。')
    heading(p,'各配置在开发阶段选定的操作门槛')
    table(p,['配置 / 门槛','TP','FP','FN','精确率','召回率','F1'],real_detection_rows(group,'detection'))
    for baseline_id in ['v11_default','v11_dev_calibrated']:
        difference,declines=real_delta(measured['detection'],comparisons[baseline_id]['detection'])
        para(p,'选定配置相对'+real_names[baseline_id]+'：'+difference+'。'+decline_note(declines))
    heading(p,'共同置信门槛0.20')
    table(p,['配置 / 门槛','TP','FP','FN','精确率','召回率','F1'],real_detection_rows(group,'detection_conf_020'))
    difference,declines=real_delta(measured['detection_conf_020'],comparisons['v11_default']['detection_conf_020'])
    para(p,'共同0.20下相对v1.1默认：'+difference+'。'+decline_note(declines))
    heading(p,'选定配置分组观察')
    groups=sorted({photo['view' if key=='legacy_test' else 'weather'] for photo in group['photos']})
    table(p,['视角' if key=='legacy_test' else '天气','TP','FP','FN','精确率','召回率'],[
        [name,*[measured['groups'][name][k] for k in ('tp','fp','fn')],
         pct(measured['groups'][name]['precision']),pct(measured['groups'][name]['recall'])]
        for name in groups])
    if key=='external_test':
        difference,declines=real_delta(measured['detection'],legacy_group['result']['detection'])
        para(p,'外部相对历史回归：'+difference+'。'+decline_note(declines)+'场景与车辆构成不同，这一迁移差距不能单独归因于某一种因素。')
    para(p,f'设备{measured["device"]}，单图平均{measured["mean_inference_ms"]:.2f}ms，含预处理、推理、后处理和首轮预热，不含占用几何计算。复用预测由逐图inference_reused字段标记。')

p=page('附录 实拍扩展 三态占用与局限')
para(p,'三态占用始终使用置信度≥0.20的全部保留框，高置信占用门槛0.50、相交面积比例5%。提高可报告车辆检测的门槛不会删除低置信占用证据。unknown进入总观察分母，不计为正确。')
for key in ['legacy_test','external_test']:
    group=real_groups[key];o=group['result']['occupancy'];confusion=o['confusion']
    heading(p,real_titles[key])
    table(p,['真值 / 感知','观测空闲','检测占用','不确定'],[
        [label,*[confusion.get(truth+'->'+pred,0) for pred in ('free','occupied','unknown')]]
        for truth,label in [('free','真实空闲'),('occupied','真实占用')]])
    para(p,f'总计{o["total"]}次车位观察，unknown={o["unknown"]}；判断覆盖率{pct(o["coverage"])}，已判定正确率{pct(o["accuracy_when_decided"])}，全部观察正确率{pct(o["correct_over_all"])}。')
    para(p,f'实际占用却观测空闲为{o["false_free"]}次，占全部观测空闲{pct(o["false_free_among_observed_free"])}，占真实占用{pct(o["false_free_among_true_occupied"])}。这两个分母不同，均保留。')
    before=group['comparisons']['v11_default']['occupancy']
    if o['false_free']>before['false_free']:
        para(p,f'占用退步项：假空闲由v1.1默认的{before["false_free"]}次增加到{o["false_free"]}次。')
heading(p,'可靠性结论的范围')
para(p,'若配置采用裁剪推理，未完整进入检测区域的车位不能仅凭无框判为空闲；缺乏明确车辆证据时标为不确定并保留在分母。该规则在外部推理前依据无状态几何固定。CNR有48/2196次车位观察被作者ROI部分覆盖，PK车位均完整覆盖；完整照片模式不受裁剪限制。')
para(p,'本项目不承诺99.99%的检测率、召回率或车位正确率，也未以这批样本证明该保证。报告保留实际精确率、召回率、覆盖率与假空闲；真实停车场的视角、光照和遮挡变化仍可能造成失效。')
para(p,'弱证据和跨位证据可触发不确定；完全漏检仍可能形成假空闲。占用结论不能直接替代可用车位推荐率，也不代表真实停车场导航已验收。两套照片均保持原始像素坐标，没有套用二维仿真的32像素/米比例。')
para(p,'照片模式只验证视觉。实际停车确认、资格规则、A*路线及反向寻车在52×32米二维仿真中演示；动力类型、无障碍资格由登记表单录入，充电需求由需求表单确认。')

p=page('附录 实拍扩展 失败保留与复现演示')
external_cases=[case for case in real_case_index['cases'] if case['dataset']=='external_test']
if not external_cases:
    raise ValueError('Measured external diagnostic cases are required before building the course report')
case=external_cases[0]
score=case['score']
para(p,f'外部案例{case["photo_id"]}：'+ '；'.join(case['selection'])+
     f'。本图TP={score["tp"]}、FP={score["fp"]}、FN={score["fn"]}，检测门槛{case["detection_threshold"]:.2f}。该案例由全组规则选择，保留原始照片，不以成功图替代失败记录。')
picture(p,ROOT/case['image'],'图3 外部验证诊断叠加：绿色为匹配，橙色为误检，粉色为漏检真值，蓝框为作者计分ROI。照片 CNRPark-EXT / MetaPKLot，ODbL 1.0；报告新增评测叠加。')
para(p,'PKLot各视角最低召回及CNR外部最低召回/最多误检案例保存在reports/real_optimization/case_index.json，重复照片去重。图像说明定位误差；遮挡、透视或标注边界是否为具体原因需结合原图逐例核对，不根据框颜色自动归因。')
heading(p,'约2分钟实拍演示')
para(p,'打开真实照片页，先选CNRPark-EXT和一种天气，查看来源后运行真实检测。车辆数字只计达到检测门槛的已接受框；勾选“低置信证据”可看其他虚线待确认框，点击车位查看全部用于占用判定的证据。切换一张照片，确认旧结果清除后重新推理；再打开实拍报告查看错误和覆盖率。最后返回二维仿真继续约5分钟停车与寻车流程。')
heading(p,'复现与证据边界')
para(p,'重新评估：python scripts/evaluate_real_optimization.py --device cpu --output reports/real_optimization/rerun。使用新目录保留旧证据；指定对应冻结文件。先运行scripts/report_real_optimization.py生成真实评测报告与案例，再运行scripts/build_course_report.py生成本报告。默认读取final/summary.json。')
para(p,'本报告整理已记录证据，程序运行与模型精度分别验收。梁可正式实现接入后，仍须重新联调和复验。')

document=Document()
section=document.sections[0]
section.page_width=Cm(21);section.page_height=Cm(29.7)
section.top_margin=Cm(1.9);section.bottom_margin=Cm(1.8);section.left_margin=Cm(2);section.right_margin=Cm(2)
section.header_distance=Cm(.8);section.footer_distance=Cm(.8)
for name in ['Normal','Title','Heading 1','Heading 2']:
    s=document.styles[name]
    s.font.name='Microsoft YaHei';s.font.color.rgb=RGBColor(0,0,0)
    s._element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'),'Microsoft YaHei')
    s.paragraph_format.space_after=Pt(3)
for style in document.styles:
    for border in list(style._element.iter(qn('w:pBdr'))):
        border.getparent().remove(border)
document.styles['Normal'].font.size=Pt(10)
document.styles['Normal'].paragraph_format.line_spacing=1.05
document.styles['Title'].font.size=Pt(20)
document.styles['Heading 1'].font.size=Pt(16)
document.styles['Heading 2'].font.size=Pt(11.5)
document.styles['Heading 1'].paragraph_format.space_after=Pt(8)
document.styles['Heading 2'].paragraph_format.space_before=Pt(8)
document.styles['Heading 2'].paragraph_format.space_after=Pt(4)
footer=section.footer.paragraphs[0]
footer.alignment=2
run=footer.add_run('胡轩铭模块实现与验证  |  ');run.font.size=Pt(8)
fld=OxmlElement('w:fldSimple');fld.set(qn('w:instr'),'PAGE');footer._p.append(fld)
htmlparts=['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>胡轩铭模块实现与验证报告</title><style>body{max-width:1000px;margin:auto;padding:36px 24px;font:16px/1.75 system-ui,"Microsoft YaHei",sans-serif;color:#1c2e3d;background:#fff}h1{font-size:27px;line-height:1.45}h2{font-size:22px;margin-top:40px}h3{font-size:18px}table{border-collapse:collapse;width:100%;font-size:14px;margin:16px 0}td,th{border:1px solid #d9d9d9;padding:9px;text-align:left}th{background:#eaf0f4}tr:nth-child(even){background:#f8fafb}img{max-width:100%}figure{margin:20px 0}figcaption{font-size:13px;color:#536778}p{overflow-wrap:anywhere}section{margin-bottom:48px}@media print{section{break-before:page}section:first-child{break-before:auto}}</style></head><body><nav><a href="/">返回课程仿真</a> · <a href="/real">实拍扩展</a></nav>']
for i,part in enumerate(pages):
    title = document.add_paragraph(part['title'],style='Title' if i==0 else 'Heading 1')
    if i: title.paragraph_format.page_break_before = True
    htmlparts.append('<section><h1>'+html.escape(part['title']).replace('\n','<br>')+'</h1>')
    for block in part['blocks']:
        if block[0]=='p':
            document.add_paragraph(block[1]);htmlparts.append('<p>'+html.escape(block[1])+'</p>')
        elif block[0]=='h':
            document.add_paragraph(block[1],style='Heading 2');htmlparts.append('<h3>'+html.escape(block[1])+'</h3>')
        elif block[0]=='table':
            heads,rows=block[1:]
            t=document.add_table(rows=1,cols=len(heads));t.autofit=False
            widths={2:[4.2,12.8],3:[3,7,7],4:[5,4,4,4],6:[4.5,1.5,1.5,1.5,4,4],7:[3.2,1.3,1.3,1.3,1.3,4.3,4.3]}[len(heads)]
            if heads[0]=='数据划分': widths=[3.5,1.5,12]
            if heads[0]=='编号': widths=[1.5,13,2.5]
            if heads[0]=='划分': widths=[3,2,12]
            if heads[0]=='模型': widths=[3.2,1.4,1.4,1.4,3.2,3.2,3.2]
            if heads[0]=='配置 / 门槛': widths=[4.4,1.2,1.2,1.2,3,3,3]
            for c,v in zip(t.rows[0].cells,heads):c.text=v
            for row in rows:
                for c,v in zip(t.add_row().cells,row):c.text=v
            for ridx,row in enumerate(t.rows):
                for colidx,cell in enumerate(row.cells):
                    cell.width=Cm(widths[colidx])
                    cell.vertical_alignment=1
                    tcpr=cell._tc.get_or_add_tcPr()
                    borders=OxmlElement('w:tcBorders')
                    for edge in ('top','left','bottom','right'):
                        el=OxmlElement('w:'+edge);el.set(qn('w:val'),'single');el.set(qn('w:sz'),'4');el.set(qn('w:color'),'D9D9D9');borders.append(el)
                    tcpr.append(borders)
                    sh=OxmlElement('w:shd');sh.set(qn('w:fill'),'E8EEF3' if ridx==0 else ('F7F9FB' if ridx%2==0 else 'FFFFFF'));tcpr.append(sh)
                    margins=OxmlElement('w:tcMar')
                    for edge in ('top','left','bottom','right'):
                        el=OxmlElement('w:'+edge);el.set(qn('w:w'),'60' if edge in ('top','bottom') else '80');el.set(qn('w:type'),'dxa');margins.append(el)
                    tcpr.append(margins)
                    for pp in cell.paragraphs:
                        pp.paragraph_format.space_after=Pt(1);pp.paragraph_format.line_spacing=1.05
                        for rr in pp.runs:rr.font.size=Pt(9);rr.bold=(ridx==0)
                trpr=row._tr.get_or_add_trPr();cant=OxmlElement('w:cantSplit');trpr.append(cant)
            repeat=OxmlElement('w:tblHeader');t.rows[0]._tr.get_or_add_trPr().append(repeat)
            spacer=document.add_paragraph()
            spacer.paragraph_format.space_after=Pt(0)
            spacer.paragraph_format.space_before=Pt(0)
            spacer.paragraph_format.line_spacing=Pt(3)
            spacer.add_run().font.size=Pt(3)
            htmlparts.append('<table><tr>'+''.join('<th>'+html.escape(x)+'</th>' for x in heads)+'</tr>'+''.join('<tr>'+''.join('<td>'+html.escape(x)+'</td>' for x in row)+'</tr>' for row in rows)+'</table>')
        elif block[0]=='image':
            image_path=Path(block[1]);document.add_picture(str(image_path),width=Cm(12.5 if part['title'].startswith('附录 实拍扩展 ') else 16.7))
            document.paragraphs[-1].alignment=1
            document.paragraphs[-1].paragraph_format.keep_with_next=True
            cap=document.add_paragraph(block[2]);cap.paragraph_format.space_after=Pt(4)
            for rr in cap.runs:rr.font.size=Pt(8)
            data=base64.b64encode(image_path.read_bytes()).decode()
            htmlparts.append('<figure><img src="data:image/png;base64,'+data+'"><figcaption>'+html.escape(block[2])+'</figcaption></figure>')
    htmlparts.append('</section>')
htmlparts.append('</body></html>')
target=REPORT/'胡轩铭模块实现与验证报告.docx'
document.save(target)
(REPORT/'report.html').write_text(''.join(htmlparts),encoding='utf-8',newline='\n')
print(target)

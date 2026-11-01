"""Render only measured, frozen real-photo optimization results.

Does not run inference, alter labels, or overwrite reports/real_photos.
The HTML includes its case images and can be opened without a local server.
"""
from __future__ import annotations

import argparse
import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import re
from urllib.parse import quote, urlparse

ROOT = Path(__file__).resolve().parents[1]
GITHUB = 'https://github.com/Cancery980/smart-parking-sim-real/blob/main/'
LABELS = {'v11_default':'v1.1 原默认门槛', 'v11_dev_calibrated':'v1.1 开发集校准门槛'}
METRICS = {'precision':'精确率', 'recall':'召回率', 'f1':'F1'}
DATASETS = {
    'legacy_test': {'title':'PKLot 历史回归', 'name':'PKLot', 'license':'CC BY 4.0',
                    'license_url':'https://creativecommons.org/licenses/by/4.0/'},
    'external_test': {'title':'CNRPark-EXT 外部验证', 'name':'CNRPark-EXT', 'license':'ODbL 1.0',
                      'license_url':'https://opendatacommons.org/licenses/odbl/1-0/'},
}


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pct(value):
    return '不适用' if value is None else f'{100 * value:.2f}%'


def relative(path, start):
    return Path(os.path.relpath(path, start)).as_posix()


def inside_root(relative_path):
    path = (ROOT / relative_path).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError(f'Input path leaves project: {relative_path}')
    return path


def display_name(config_id, selected_id):
    return LABELS.get(config_id, '本轮选定配置' if config_id == selected_id else config_id)


def recall(record):
    score = record['score']
    denominator = score['tp'] + score['fn']
    return score['tp'] / denominator if denominator else 1.0


def comparison_table(comparisons, order, selected_id, metric_key):
    rows = ['|配置|检测门槛|TP|FP|FN|精确率|召回率|F1|',
            '|---|---:|---:|---:|---:|---:|---:|---:|']
    for config_id in order:
        result = comparisons[config_id]
        score = result[metric_key]
        threshold = .20 if metric_key == 'detection_conf_020' else result['config']['detection_threshold']
        values = [display_name(config_id, selected_id), f'{threshold:.2f}',
                  *[str(score[key]) for key in ('tp','fp','fn')],
                  *[pct(score[key]) for key in ('precision','recall','f1')]]
        rows.append('|'+'|'.join(values)+'|')
    return rows


def changes(selected, baseline):
    deltas, regressions = [], []
    for key, label in METRICS.items():
        before, after = baseline[key], selected[key]
        if before is None or after is None:
            deltas.append(f'{label}不适用')
            continue
        change = 100 * (after - before)
        deltas.append(f'{label} {change:+.2f} 个百分点')
        if change < -1e-9:
            regressions.append(f'{label}下降 {-change:.2f} 个百分点')
    return '，'.join(deltas), regressions


def inference_signature(config):
    return (config.get('weights_sha256'), config.get('imgsz'),
            config.get('nms_iou', .70), bool(config.get('augment', False)),
            config.get('roi_policy', 'full_frame'),
            json.dumps(config.get('roi_by_view', {}), sort_keys=True))


def load_records(summary_path, dataset_name, dataset_result, selected_id):
    spec = dataset_result['dataset']
    manifest_path = inside_root(spec['manifest'])
    if digest(manifest_path) != spec['manifest_sha256']:
        raise ValueError(f'{dataset_name}: manifest changed after evaluation')
    photos = {p['photo_id']:p for p in read(manifest_path)['photos'] if p['split'] == spec['split']}
    selected = dataset_result['comparisons'][selected_id]
    if len(photos) != selected['images'] or len(photos) != spec['images']:
        raise ValueError(f'{dataset_name}: image count does not match frozen summary')
    records = []
    for photo_id, photo in photos.items():
        path = summary_path.parent / dataset_name / selected_id / 'predictions' / f'{photo_id}.json'
        record = read(path)
        if record['photo_id'] != photo_id or record['image_sha256'] != photo['sha256']:
            raise ValueError(f'{photo_id}: prediction/image provenance mismatch')
        if record['weights_sha256'] != selected['config']['weights_sha256']:
            raise ValueError(f'{photo_id}: prediction weights differ from summary')
        if abs(record['detection_threshold'] - selected['config']['detection_threshold']) > 1e-12:
            raise ValueError(f'{photo_id}: operating threshold differs from summary')
        records.append({**record, 'photo':photo, 'prediction_path':path})
    for key in ('tp','fp','fn'):
        if sum(record['score'][key] for record in records) != selected['detection'][key]:
            raise ValueError(f'{dataset_name}: per-photo {key} does not sum to measured total')
    return records


def choose_cases(dataset_name, records):
    chosen = {}
    def add(record, reason):
        chosen.setdefault(record['photo_id'], {'record':record, 'reasons':[]})['reasons'].append(reason)
    if dataset_name == 'legacy_test':
        for view in sorted({r['photo']['view'] for r in records}):
            group = [r for r in records if r['photo']['view'] == view]
            add(min(group, key=lambda r:(recall(r),r['photo_id'])), f'{view} 视角内最低召回，平局按照片ID')
    else:
        add(min(records, key=lambda r:(recall(r),r['photo_id'])), '外部全集最低召回，平局按照片ID')
        add(min(records, key=lambda r:(-r['score']['fp'],r['photo_id'])), '外部全集最多误检，平局按照片ID')
    return list(chosen.values())


def render_case(item, dataset_name, output):
    from PIL import Image, ImageDraw, ImageFont
    record = item['record'];photo = record['photo'];info = DATASETS[dataset_name]
    source = inside_root(photo['image'])
    if digest(source) != photo['sha256']:
        raise ValueError(f'Original JPEG changed: {photo["photo_id"]}')
    with Image.open(source) as opened:
        original = opened.convert('RGB')
    width, height = original.size
    banner = 120
    canvas = Image.new('RGB', (width,height+banner), '#153344')
    canvas.paste(original, (0,banner))
    draw = ImageDraw.Draw(canvas)
    font_size = 17 if width < 1100 else 19
    font = None
    for candidate in ['C:/Windows/Fonts/arial.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']:
        if Path(candidate).is_file():
            font = ImageFont.truetype(candidate, font_size);break
    if font is None:
        font = ImageFont.load_default(size=font_size)
    x,y,w,h = record['matches']['evaluated_roi_xywh']
    roi = [x,y,x+w,y+h]
    matched = set(record['matches']['matched_detection_ids'])
    false_positives = set(record['matches']['false_positive_ids'])
    # Draw scored accepted boxes only. Retained low-confidence and ignored
    # outside-ROI boxes remain in JSON, but are not mislabelled as detections.
    for detection in record['detections']:
        did = detection['detection_id']
        if did not in matched and did not in false_positives:
            continue
        x1,y1,x2,y2 = detection['bbox_xyxy_px']
        box = (max(roi[0],x1),max(roi[1],y1)+banner,min(roi[2],x2),min(roi[3],y2)+banner)
        draw.rectangle(box, outline='#f3b847' if did in false_positives else '#03dda5', width=2)
    for x1,y1,x2,y2 in record['matches']['missed_boxes']:
        draw.rectangle((x1,y1+banner,x2,y2+banner), outline='#f55da7', width=3)
    draw.rectangle((roi[0],roi[1]+banner,min(width-1,roi[2]),min(height-1,roi[3])+banner), outline='#69bcff', width=2)
    score = record['score']
    lines = [f'{info["name"]} | {photo["view"]} | {photo["weather"]} | {photo["date"]}',
             f'{photo["photo_id"]} | TP {score["tp"]}  FP {score["fp"]}  FN {score["fn"]} | conf {record["detection_threshold"]:.2f}',
             'Green: matched | Orange: FP | Pink: missed GT | Blue: author scoring ROI',
             f'{info["license"]} | Photo: {info["name"]}; annotations: MetaPKLot | Overlay added for analysis']
    for i, line in enumerate(lines):
        line_font = font
        if hasattr(font, 'font_variant'):
            size = font_size
            while draw.textlength(line, font=line_font) > width - 24 and size > 10:
                size -= 1
                line_font = font.font_variant(size=size)
        draw.text((12,8+i*27),line,font=line_font,fill='white' if i<2 else '#c4dde8')
    filename = re.sub(r'[^A-Za-z0-9_.-]', '_', f'{dataset_name}_{photo["photo_id"]}.png')
    target = output / 'cases' / filename
    target.parent.mkdir(parents=True,exist_ok=True)
    canvas.save(target)
    return {'dataset':dataset_name,'photo_id':photo['photo_id'],'view':photo['view'],
            'selection':item['reasons'],'score':record['score'],'detection_threshold':record['detection_threshold'],
            'image':target,'source_url':photo.get('source_url'), 'source_sha256':photo['sha256'],
            'license':info['license'],'license_url':info['license_url'],
            'prediction_path':record['prediction_path']}


def github_url(target, markdown_directory):
    if urlparse(target).scheme in ('https','http'):
        return target
    resolved = (markdown_directory/target).resolve()
    if not resolved.is_relative_to(ROOT):
        raise ValueError(f'Report link leaves project: {target}')
    return GITHUB + quote(resolved.relative_to(ROOT).as_posix(), safe='/')


def inline_html(text, markdown_directory):
    pattern = r'\[([^\]]+)\]\(([^)]+)\)|`([^`]+)`|\*\*([^*]+)\*\*'
    result=[];offset=0
    for match in re.finditer(pattern,text):
        result.append(html.escape(text[offset:match.start()]))
        label,target,code,bold=match.groups()
        if label is not None:
            result.append(f'<a href="{html.escape(github_url(target,markdown_directory),quote=True)}">{html.escape(label)}</a>')
        elif code is not None:
            result.append('<code>'+html.escape(code)+'</code>')
        else:
            result.append('<strong>'+html.escape(bold)+'</strong>')
        offset=match.end()
    result.append(html.escape(text[offset:]))
    return ''.join(result)


def standalone_html(lines, markdown_directory):
    style = ('body{max-width:1100px;margin:auto;padding:28px;font:15px/1.8 system-ui,"Microsoft YaHei",sans-serif;color:#183647;background:#f2f6f8}'
             'article{background:#fff;padding:30px;border:1px solid #dce7eb;border-radius:14px}h1,h2,h3{line-height:1.4;color:#143647;overflow-wrap:anywhere}'
             'h2{margin-top:32px;padding-top:14px;border-top:1px solid #e3edef}table{border-collapse:collapse;width:100%;font-size:13px}'
             'td,th{border:1px solid #d9e2e5;padding:8px;text-align:left;vertical-align:top}th{background:#edf4f5}'
             'img{display:block;width:100%;height:auto;border:1px solid #dce7eb;border-radius:6px;margin:16px 0}a{color:#087e72}'
             '.table-wrap{overflow-x:auto}code{word-break:break-all;background:#f1f5f7;padding:1px 4px}strong{color:#8c4e32}'
             'nav{display:flex;gap:18px;flex-wrap:wrap}.offline-note{color:#728995;font-size:12px}li{margin:8px 0;overflow-wrap:anywhere}'
             '@media(max-width:620px){body{padding:10px}article{padding:16px}table{font-size:12px}h1{font-size:24px}}')
    output = ['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
              '<title>真实照片优化与外部验证</title><style>'+style+'</style></head><body><article>',
              '<nav><a href="/real">返回真实照片实验台</a><a href="/">返回二维仿真</a></nav>',
              '<p class="offline-note">本页包含全部案例图片，可离线阅读。返回按钮需通过运行中的程序打开；源码与原始记录链接指向公开 GitHub 仓库。</p>']
    in_table=False;in_list=False;header=False
    for line in lines:
        if line.startswith('|'):
            if in_list:output.append('</ul>');in_list=False
            if line.startswith('|---'):continue
            if not in_table:output.append('<div class="table-wrap"><table>');in_table=True;header=True
            tag='th' if header else 'td';header=False
            output.append('<tr>'+''.join(f'<{tag}>'+inline_html(cell,markdown_directory)+f'</{tag}>' for cell in line.strip('|').split('|'))+'</tr>')
            continue
        if in_table:output.append('</table></div>');in_table=False
        if not line:continue
        if line.startswith('- '):
            if not in_list:output.append('<ul>');in_list=True
            output.append('<li>'+inline_html(line[2:],markdown_directory)+'</li>');continue
        if in_list:output.append('</ul>');in_list=False
        image = re.fullmatch(r'!\[([^\]]*)\]\(([^)]+)\)',line)
        if image:
            alt,target=image.groups();image_path=(markdown_directory/target).resolve()
            encoded=base64.b64encode(image_path.read_bytes()).decode('ascii')
            output.append(f'<img alt="{html.escape(alt,quote=True)}" src="data:image/png;base64,{encoded}">')
        elif line.startswith('#'):
            level=len(line)-len(line.lstrip('#'))
            output.append(f'<h{level}>'+inline_html(line[level:].strip(),markdown_directory)+f'</h{level}>')
        else:
            output.append('<p>'+inline_html(line,markdown_directory)+'</p>')
    if in_table:output.append('</table></div>')
    if in_list:output.append('</ul>')
    output.append('</article></body></html>')
    return ''.join(output)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--summary',type=Path,default=ROOT/'reports/real_optimization/final/summary.json')
    parser.add_argument('--selection',type=Path,default=ROOT/'reports/real_optimization/frozen_selection.json')
    parser.add_argument('--output',type=Path,default=ROOT/'reports/real_optimization')
    parser.add_argument('--html',type=Path,default=ROOT/'reports/real_photo_report.html')
    args=parser.parse_args()
    summary_path=args.summary.resolve();selection_path=args.selection.resolve();output=args.output.resolve()
    result=read(summary_path);frozen=read(selection_path)
    if result['frozen_selection_sha256'] != digest(selection_path):
        raise ValueError('Frozen selection SHA does not match final evaluation')
    selected_id=result['selected_id']
    if selected_id != frozen['selected_config']['id']:
        raise ValueError('Selected configuration differs from frozen selection')
    if not {'legacy_test','external_test'}.issubset(result['datasets']):
        raise ValueError('Both historical regression and external validation results are required')
    for target in [output,args.html.resolve()]:
        if not target.is_relative_to(ROOT) or target.is_relative_to(ROOT/'reports/real_photos'):
            raise ValueError('Output must remain in the project and outside historical reports/real_photos')
    # Validate the complete measured inputs before creating deliverable files.
    all_records={name:load_records(summary_path,name,result['datasets'][name],selected_id) for name in DATASETS}
    output.mkdir(parents=True,exist_ok=True)
    cases=[]
    for name,records in all_records.items():
        for item in choose_cases(name,records):
            cases.append(render_case(item,name,output))
    legacy=result['datasets']['legacy_test'];external=result['datasets']['external_test']
    selected_legacy=legacy['comparisons'][selected_id];selected_external=external['comparisons'][selected_id]
    order=list(dict.fromkeys(['v11_default','v11_dev_calibrated',selected_id]))
    lines=['# 真实照片优化与外部验证','',
           '本报告由冻结配置后的实际预测生成。车辆检测使用车辆框真值；车位占用使用独立占用标签。两类指标分别报告，未知车位不算作正确判断。','',
           f'**外部验证实测：精确率 {pct(selected_external["detection"]["precision"])}，召回率 {pct(selected_external["detection"]["recall"])}；FP={selected_external["detection"]["fp"]}，FN={selected_external["detection"]["fn"]}。**','',
           '## 数据角色与冻结边界','',
           f'PKLot 历史回归为 {selected_legacy["images"]} 张、{selected_legacy["dates"]} 个拍摄日期。它们在 v1.1 已公开并被分析，不能再称为本轮未见测试集。', '',
           f'CNRPark-EXT 外部验证为 {selected_external["images"]} 张、{selected_external["dates"]} 个拍摄日期；本轮训练、模型选型和阈值选择均未使用这些照片。模型与阈值在评估前冻结，来源与数据哈希在冻结方案中记录。', '',
           '照片张数不等于独立车辆或停车事件数；同一场地、相机或日期内仍可能相关。外部成绩只代表本次选定数据与计分范围。', '',
           f'实际评估时间（UTC）：`{result["evaluated_at_utc"]}`；冻结文件 SHA-256：`{result["frozen_selection_sha256"]}`。', '',
           f'[冻结选择与开发协议]({relative(selection_path,output)}) · [实际汇总JSON]({relative(summary_path,output)}) · [评估脚本]({relative(ROOT/"scripts/evaluate_real_optimization.py",output)}) · [报告生成脚本]({relative(Path(__file__),output)})', '',
           '## 模型、推理设置与阈值','',
           '|配置|模型文件|输入尺寸|NMS IoU|增强推理|推理ROI|车辆检测门槛|',
           '|---|---|---:|---:|---|---|---:|']
    for config_id in order:
        config=legacy['comparisons'][config_id]['config']
        lines.append('|'+ '|'.join([display_name(config_id,selected_id),f'`{config["weights"]}`',str(config['imgsz']),
                                  f'{config.get("nms_iou",.70):.2f}','是' if config.get('augment',False) else '否',
                                  str(config.get('roi_policy','full_frame')),f'{config["detection_threshold"]:.2f}'])+'|')
    default=legacy['comparisons']['v11_default']['config'];calibrated=legacy['comparisons']['v11_dev_calibrated']['config']
    if inference_signature(default)==inference_signature(calibrated):
        lines += ['', 'v1.1 默认与开发集校准两项使用相同权重和推理配置，只改变可报告车辆检测的置信门槛；这一差值单独反映操作点变化。']
    else:
        lines += ['', '**两项 v1.1 对照的推理配置也存在差异，不能将其全部差值归因于置信门槛。** 具体设置以上表和冻结JSON为准。']
    lines += ['', '选定配置与校准后的 v1.1 再比较，衡量候选权重及推理设置的变化；同时给出共同0.20门槛的结果，避免把调高阈值当作模型原始检测能力的全部提升。', '',
              '车位占用始终使用全部置信度≥0.20的保留框，高置信占用门槛0.50、相交面积比例5%。车辆检测门槛只影响报告及“已接受检测”显示，不删除低置信占用证据。', '',
              '检测指标是固定IoU≥0.50的一对一匹配 P/R/F1，不是mAP。只计作者完整标注ROI内的中心点，框裁剪到计分ROI；区域外未计分目标不直接当成误检。', '']
    for dataset_name,info in DATASETS.items():
        dataset=result['datasets'][dataset_name];comparisons=dataset['comparisons'];selected=comparisons[selected_id]
        lines += [f'## {info["title"]}：车辆检测','',f'共 {selected["images"]} 张照片。先列各配置在开发阶段选定的操作门槛：','']
        lines += comparison_table(comparisons,order,selected_id,'detection')
        for baseline_id in ['v11_default','v11_dev_calibrated']:
            delta,degraded=changes(selected['detection'],comparisons[baseline_id]['detection'])
            lines += ['',f'相对{LABELS[baseline_id]}：{delta}。']
            if degraded:lines += ['', '**退步项：'+'；'.join(degraded)+'。**']
        lines += ['', '### 共同置信门槛0.20','', '以下三项均在0.20计分，保留相同的作者ROI与IoU匹配规则：','']
        lines += comparison_table(comparisons,order,selected_id,'detection_conf_020')
        raw_delta,raw_degraded=changes(selected['detection_conf_020'],comparisons['v11_default']['detection_conf_020'])
        lines += ['',f'共同0.20下相对v1.1：{raw_delta}。']
        if raw_degraded:lines += ['', '**共同门槛下的退步：'+'；'.join(raw_degraded)+'。**']
        lines += ['', '### 选定配置的视角与天气分组','', '|分组|TP|FP|FN|精确率|召回率|F1|','|---|---:|---:|---:|---:|---:|---:|']
        for group,score in sorted(selected['groups'].items()):
            lines.append('|'+ '|'.join([group,*[str(score[k]) for k in ('tp','fp','fn')],*[pct(score[k]) for k in METRICS]])+'|')
        lines += ['',f'设备 `{selected["device"]}`；平均单图推理耗时 {selected["mean_inference_ms"]:.2f} ms。计时含模型预处理/推理/后处理和首轮预热，不含占用几何计算；共享推理配置可能复用同一份预测，逐图记录有 `inference_reused` 标志。', '',
                  f'## {info["title"]}：三态占用','', '|配置|车位观察数|不确定|判断覆盖率|已判定正确率|全部观察正确率|假空闲次数|假空闲 / 观测空闲|假空闲 / 真实占用|',
                  '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
        for config_id in order:
            o=comparisons[config_id]['occupancy']
            lines.append('|'+ '|'.join([display_name(config_id,selected_id),str(o['total']),str(o['unknown']),pct(o['coverage']),pct(o['accuracy_when_decided']),pct(o['correct_over_all']),str(o['false_free']),pct(o['false_free_among_observed_free']),pct(o['false_free_among_true_occupied'])])+'|')
        o=selected['occupancy'];before=comparisons['v11_default']['occupancy']
        if o['false_free']>before['false_free']:
            lines += ['',f'**占用退步项：选定配置假空闲由 v1.1 的 {before["false_free"]} 次增加到 {o["false_free"]} 次。**']
        lines += ['', '选定配置的完整混淆矩阵：','', '|真值 / 预测|观测空闲|检测占用|不确定|','|---|---:|---:|---:|']
        for truth,label in [('free','真实空闲'),('occupied','真实占用')]:
            lines.append('|'+ '|'.join([label,*[str(o['confusion'].get(truth+'->'+p,0)) for p in ('free','occupied','unknown')]])+'|')
        lines += ['', 'unknown 进入总观察分母，但不计正确；“已判定正确率”只覆盖空闲/占用结论，必须同时看覆盖率和假空闲。若采用裁剪推理，未完整进入检测区域的车位在缺少明确车辆证据时保持unknown；完整照片模式不受此限制。该规则在首次外部推理前由几何检查确定，车位未从分母中删除。该结果不是实际选位推荐率，照片中没有米制路网或真实导航验收。','']
    gap,regressions=changes(selected_external['detection'],selected_legacy['detection'])
    lines += ['## 外部与历史表现的差距','', f'选定配置在外部验证相对历史回归：{gap}。两批图像的场景和目标构成不同，这一差值用于说明观察到的迁移表现，不是控制全部因素的因果实验。','']
    if regressions:lines += ['**外部下降项：'+'；'.join(regressions)+'。**','']
    lines += ['## 保留的误差检查案例','',
              'PKLot 每个视角取最低召回的一张；CNRPark-EXT 取全集最低召回和误检最多的照片，重复时只保留一张。没有挑选最好看的成功图。若某组错误数为零，仍显示按该规则选出的样例，不虚构失败。', '',
              '图中绿框为计分匹配、橙框为误检、粉框为漏检真值，蓝框为作者计分ROI。只叠加已接受且纳入计分的检测；原始JPEG未修改。未显示的低置信/区外框仍保存在逐图JSON中。','']
    for case in cases:
        score=case['score'];info=DATASETS[case['dataset']]
        lines += [f'### {info["title"]} / {case["view"]} / {case["photo_id"]}','',
                  '选择规则：'+'；'.join(case['selection'])+'。', '',
                  f'TP={score["tp"]}、FP={score["fp"]}、FN={score["fn"]}，检测门槛{case["detection_threshold"]:.2f}。', '',
                  f'![{info["name"]}实测误差叠加图]({relative(case["image"],output)})','',
                  f'照片：{info["name"]}；修订标注：MetaPKLot。许可：[{case["license"]}]({case["license_url"]})。本报告仅增加评测框和说明，未改原图文件。', '',
                  f'[逐图预测与匹配]({relative(case["prediction_path"],output)})'+(f' · [上游原始照片]({case["source_url"]})' if case['source_url'] else ''), '']
    lines += ['## 来源、许可与复现','',
              '- PKLot 照片与相应数据组件：Paulo de Almeida 等；[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)。',
              '- CNRPark-EXT 照片与相应数据组件：Giuseppe Amato、Fabio Carrara 等；[ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/)。两种数据许可分别适用，不能把整个数据包统一改写为项目源码许可。',
              '- 修订车辆与车位标注来自 [MetaPKLot 作者仓库](https://github.com/DSBD-Research/MetaPKLot-Dataset)。具体来源、变更及数据署名以各数据目录文件为准。','',
              f'[来源核对]({relative(ROOT/"docs/REAL_DATA_SOURCES.md",output)}) · [项目第三方说明]({relative(ROOT/"THIRD_PARTY_NOTICES.md",output)})','',
              '仅重建本报告：`python scripts/report_real_optimization.py`。重新评估会执行真实推理，必须指定新的输出目录，例如 `python scripts/evaluate_real_optimization.py --device cpu --output reports/real_optimization/rerun`，保留现有证据。随后用 `--summary` 指向新汇总，`--selection` 指向对应冻结文件。','',
              '修改模型或阈值后，旧结果不再代表新配置。开发集校准结果与历史回归都不代替未参与选型的外部验证；保留所有失败与低于预期的结果。','']
    markdown='\n'.join(lines)
    (output/'RESULTS.md').write_text(markdown,encoding='utf-8')
    args.html.parent.mkdir(parents=True,exist_ok=True)
    args.html.write_text(standalone_html(lines,output),encoding='utf-8')
    serializable=[]
    for case in cases:
        item=deepcopy(case)
        for key in ('image','prediction_path'):item[key]=item[key].relative_to(ROOT).as_posix()
        serializable.append(item)
    (output/'case_index.json').write_text(json.dumps({'selected_id':selected_id,'cases':serializable},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'generated_at_utc':datetime.now(timezone.utc).isoformat(),'selected_id':selected_id,'cases':len(cases),
                      'markdown':str(output/'RESULTS.md'),'html':str(args.html),'input_summary_sha256':digest(summary_path)},ensure_ascii=False))


if __name__=='__main__':
    main()

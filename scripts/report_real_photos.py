"""Render measured real-photo results, including failures and denominators."""
from pathlib import Path
import base64
import csv
import html
import json
import re
from collections import Counter

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'reports/real_photos'
def read(path):return json.loads(path.read_text(encoding='utf-8'))
def pct(value):return '不适用' if value is None else f'{100*value:.2f}%'
def main():
    result=read(OUT/'test_summary.json');selected=result['selected'];config=selected['config']
    data=read(ROOT/'data/real/manifest.json');photos={p['photo_id']:p for p in data['photos']}
    records=[]
    for path in sorted((OUT/'test'/config['id']/'predictions').glob('*.json')):
        record=read(path);record['photo']=photos[record['photo_id']];records.append(record)
    assert len(records)==36
    from PIL import Image,ImageDraw,ImageFont
    cases=OUT/'cases';cases.mkdir(exist_ok=True)
    font_path=Path('C:/Windows/Fonts/msyh.ttc')
    font=ImageFont.truetype(str(font_path),20) if font_path.exists() else ImageFont.load_default()
    # Preserve a deterministic worst-recall example for each view, including failures.
    case_list=[]
    for view in ['UFPR04','UFPR05','PUCPR']:
        group=[r for r in records if r['photo']['view']==view]
        worst=min(group,key=lambda r:(r['score']['tp']/(r['score']['tp']+r['score']['fn']) if r['score']['tp']+r['score']['fn'] else 1,r['photo_id']))
        original=Image.open(ROOT/worst['photo']['image']).convert('RGB')
        canvas=Image.new('RGB',(original.width,original.height+78),'#102a3b');canvas.paste(original,(0,78));draw=ImageDraw.Draw(canvas)
        fp=set(worst['matches']['false_positive_ids'])
        for d in worst['detections']:
            x1,y1,x2,y2=d['bbox_xyxy_px'];color='#f1b84b' if d['detection_id'] in fp else '#00e4ba'
            draw.rectangle((x1,y1+78,x2,y2+78),outline=color,width=2)
        for x1,y1,x2,y2 in worst['matches']['missed_boxes']:draw.rectangle((x1,y1+78,x2,y2+78),outline='#f45baf',width=3)
        counts=worst['score'];label=f'{view} | {worst["photo"]["weather"]} | {worst["photo"]["date"]} | TP {counts["tp"]}  FP {counts["fp"]}  FN {counts["fn"]}'
        draw.text((14,8),label,font=font,fill='white')
        draw.text((14,40),'Green: detection  |  Orange: false positive  |  Pink: missed GT vehicle  |  PKLot / MetaPKLot CC BY 4.0',font=font,fill='#d8e7ef')
        target=cases/f'{view.lower()}_worst_recall.png';canvas.save(target)
        case_list.append({'view':view,'photo_id':worst['photo_id'],'selection':'lowest vehicle recall within this view; tie by photo ID','metrics':counts,'image':target.relative_to(ROOT).as_posix(),'source_url':worst['photo']['source_url']})
    (OUT/'case_index.json').write_text(json.dumps(case_list,ensure_ascii=False,indent=2),encoding='utf-8')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams['font.sans-serif']=['Microsoft YaHei','DejaVu Sans'];plt.rcParams['axes.unicode_minus']=False
    confusion=selected['occupancy']['confusion'];matrix=[[confusion.get(t+'->'+p,0) for p in ['free','occupied','unknown']] for t in ['free','occupied']]
    fig,ax=plt.subplots(figsize=(8,3.6),layout='constrained');ax.imshow(matrix,cmap='Blues')
    for y,row in enumerate(matrix):
        for x,n in enumerate(row):ax.text(x,y,str(n),ha='center',va='center',fontsize=18,color='white' if n>max(map(max,matrix))*.5 else '#173b4a')
    ax.set_xticks([0,1,2],['观测空闲','检测占用','不确定']);ax.set_yticks([0,1],['真实空闲','真实占用']);ax.set_title(f'真实照片三态占用  36张照片 / {selected["occupancy"]["total"]}次车位观察')
    fig.savefig(OUT/'occupancy_confusion.png',dpi=160);plt.close(fig)
    rows=[]
    comparisons=[('原合成域模型',result['synthetic_baseline']),('通用COCO模型',result['pretrained_baseline']),('交付真实照片模型',selected)]
    for name,r in comparisons:
        m=r['detection_conf_020'];rows.append([name,str(m['tp']),str(m['fp']),str(m['fn']),pct(m['precision']),pct(m['recall']),pct(m['f1'])])
    md=['# 真实停车场照片实验','',
        '照片来自 PKLot 的两个物理停车场、三个拍摄视角，覆盖晴天、多云、雨天；车辆与修订车位标注来自 MetaPKLot。图像保持原始字节，抽样在推理前固定。',
        '', '## 划分与选型', '',
        '18 张开发照片用于选模型，36 张测试照片涉及 32 个拍摄日期。不同照片不等于独立停车事件。训练、开发和测试按整个拍摄日期隔离，仍共享相机场景，不能称为未知停车场泛化。', '',
        '通用模型在开发集召回率约 61%，因此增加 270 张其他日期的真实训练照片；所有候选与微调检查点都只由开发集选择。基线和新模型在同一测试集比较；测试结果不参与权重或阈值选择。训练图通过脚本下载，来源与哈希见 training_manifest.json。', '',
        '## 车辆检测', '',
        '置信度 ≥0.20，车辆框 IoU ≥0.50，一对一匹配；这是固定阈值精确率/召回率/F1，不是 mAP。只评作者标注完整 ROI 内中心点，预测与真值框裁到该 ROI；不把标注区外车辆计作误检。', '',
        '|模型|TP|FP|FN|精确率|召回率|F1|','|---|---:|---:|---:|---:|---:|---:|']
    md += ['|'+'|'.join(row)+'|' for row in rows]
    md += ['', '## 车位占用', '', '|真值 / 预测|空闲|占用|不确定|','|---|---:|---:|---:|']
    md += ['|'+'|'.join([label,*map(str,matrix[i])])+'|' for i,label in enumerate(['真实空闲','真实占用'])]
    o=selected['occupancy']
    md += ['',f'共 {o["total"]} 次车位观察，不确定 {o["unknown"]} 次；覆盖率（给出空闲/占用结论）{pct(o["coverage"])}，已判定部分正确率 {pct(o["accuracy_when_decided"])}，全部观察正确率 {pct(o["correct_over_all"])}。',
           '',f'实际占用却判为空闲 {o["false_free"]} 次，占所有“观测空闲” {pct(o["false_free_among_observed_free"])}，占全部真实占用 {pct(o["false_free_among_true_occupied"])}。不能把车辆检测精确率当作车位判定准确率，也不能把未知状态计作正确。',
           '', '沿用高/低置信度 0.50/0.20、相交面积比例 5%。真实照片按标定多边形及原始像素处理。透视、遮挡和检测框中心偏移仍会造成错误；照片模块验证视觉，不生成未经米制标定的真实场地导航。', '',
           '## 失败案例与复现', '', '每个视角保留召回率最低的测试照片；全部逐图预测与错误计数都在 test/ 下，未删除失败。']
    for case in case_list:md += ['',f'### {case["view"]} 低召回案例', '', f'照片 `{case["photo_id"]}`，TP={case["metrics"]["tp"]}、FP={case["metrics"]["fp"]}、FN={case["metrics"]["fn"]}。', '',f'![{case["view"]}](cases/{case["view"].lower()}_worst_recall.png)']
    md += ['', '车辆框来源与车位标签严格分离。运行时只读无状态几何清单，不读 annotations.json；后者仅供离线计分。', '',
           '复现：先备份 reports/real_photos，再执行 `python scripts/evaluate_real_photos.py --stage test --device cpu --rerun`，对三个模型重新执行实际推理；不加 --rerun 时复用已核验权重的缓存。重新训练见 `python scripts/train_real_photos.py --help`。', '',
           '来源、许可及变更：[数据署名](../../data/real/ATTRIBUTION.md)、[原作者与开源核对](../../docs/REAL_DATA_SOURCES.md)。模型参数、哈希、每图输出和开发集选择依据在同目录 JSON。']
    (OUT/'RESULTS.md').write_text('\n'.join(md)+'\n',encoding='utf-8')
    # Single self-contained page, served both from source and the offline EXE.
    h=['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>真实停车场照片实验</title><style>body{max-width:1050px;margin:auto;padding:28px;font:16px/1.75 system-ui;color:#183647;background:#f8fafb}article{background:white;padding:28px;border-radius:14px}h1,h2,h3{color:#102c3b}table{border-collapse:collapse;width:100%;font-size:14px}td,th{border:1px solid #d9e2e5;padding:8px;text-align:left}th{background:#edf4f5}img{width:100%;height:auto}a{color:#087e72}.table-wrap{overflow-x:auto}code{word-break:break-all}</style><article><p><a href="/real">返回真实照片实验台</a> · <a href="/">二维仿真流程</a></p>']
    in_table=False
    for line in md:
        if line.startswith('|'):
            if line.startswith('|---'):continue
            if not in_table:h.append('<div class="table-wrap"><table>');in_table=True
            h.append('<tr>'+''.join('<td>'+html.escape(c)+'</td>' for c in line.strip('|').split('|'))+'</tr>');continue
        if in_table:h.append('</table></div>');in_table=False
        if not line:continue
        if line.startswith('!['):
            relative=line.split('](',1)[1][:-1];encoded=base64.b64encode((OUT/relative).read_bytes()).decode();h.append('<img alt="保留的真实照片失败案例" src="data:image/png;base64,'+encoded+'">')
        elif line.startswith('#'):
            level=len(line)-len(line.lstrip('#'));h.append(f'<h{level}>'+html.escape(line[level:].strip())+f'</h{level}>')
        else:
            paragraph=html.escape(line)
            paragraph=re.sub(r'`([^`]+)`',r'<code>\1</code>',paragraph)
            def link(match):
                label,target=match.groups()
                if target.startswith('../../'):
                    target='https://github.com/Cancery980/smart-parking-sim-real/blob/main/'+target[6:]
                return '<a href="'+target+'">'+label+'</a>'
            paragraph=re.sub(r'\[([^\]]+)\]\(([^)]+)\)',link,paragraph)
            h.append('<p>'+paragraph+'</p>')
    if in_table:h.append('</table></div>')
    h.append('<p>PKLot / MetaPKLot 图片及标注采用 CC BY 4.0。<a href="https://github.com/DSBD-Research/MetaPKLot-Dataset">原作者数据仓库</a></p></article></html>')
    (ROOT/'reports/real_photo_report.html').write_text(''.join(h),encoding='utf-8')
    print(json.dumps({'selected':config['id'],'cases':len(case_list),'occupancy':o},ensure_ascii=False))
if __name__=='__main__':main()

# 复现实拍优化实验

先阅读 [实验计划](REAL_OPTIMIZATION_PLAN.md) 与 [数据来源](../data/real_optimization/README.md)。本轮复用 Ultralytics 的训练、预测和 NMS；没有重写检测网络。最终配置由 18 张开发照片决定，36 张原测试照片只作历史回归，CNR 的 54 张照片用于另一停车场的外部验证。

## 直接复算已交付模型

安装仓库要求的 Python 3.12 依赖后，在仓库根目录执行：

```powershell
python scripts/evaluate_real_optimization.py --device cpu --output .build/real_optimization/rerun
```

它读取已封存的 `reports/real_optimization/frozen_selection.json`，核对图片、权重和标注 SHA-256，然后重新执行 CPU 推理。输出目录必须不存在，防止覆盖原始实验。该命令固定比较原 v1.1、仅在开发集调整检测门槛的 v1.1、最终候选；不重新选模型或调整门槛。

`detection` 使用各方案的检测门槛；`detection_conf_020` 则统一使用 0.20。两列都采用作者标注 ROI、IoU≥0.50 和按置信度排序的一对一匹配，不能将其叫作 mAP。每张图的所有置信度≥0.20框仍用于三态占用，检测展示门槛不会删掉低置信占用证据。

若最终方案使用裁剪推理，车位只有在检测区域完整覆盖它时才能由“无车辆证据”判为空闲；未完整观测的车位标为不确定。明确占用证据仍优先，全部车位都进入占用统计分母。完整照片方案没有此裁剪边界问题。

## 独立审计已保存的预测

在仓库根目录执行以下命令，可直接审计交付的缓存，不加载模型或重新推理。新报告写入 `.build`，保留已交付的审计结果：

```powershell
python scripts/audit_real_optimization.py --output .build/real_optimization/cache_audit.json
```

审计覆盖 3 个配置 × 90 张唯一照片（36 张原测试图、54 张外部测试图），共 270 份预测缓存。脚本从作者转换标注独立重算各配置门槛和共同 0.20 门槛下的 ROI/IoU≥0.50 一对一匹配、TP/FP/FN、三态矩阵与分母，并核对冻结配置、模型与图片 SHA-256、缓存完整性、CSV/汇总以及两个 v1.1 门槛复用的原始检测。它不调用生产计分或占用函数；部分观测车位也保留在全部车位分母中。

若审计上一节重新推理生成的结果，另指定 `--final .build/real_optimization/rerun`。默认项目根目录根据脚本位置确定，也可通过 `--root` 指定完整实验副本；使用 `--help` 查看参数。

[已交付的独立审计报告](../reports/real_optimization/final_scoring_audit.json)通过只表示缓存完整、计数与规则一致，不能代表跨停车场泛化通过。本轮外部 CNR 结果的召回率下降和假空闲增加仍是有效实验结果，不能因为审计通过而删去或改写。

## 重新训练候选

完整训练需要相应 GPU 环境；免安装 CPU 演示包只用于推理。图片下载一次后保存在本地缓存。

```powershell
python scripts/prepare_real_optimization_data.py --task pk-training --cache .build/real_optimization/training --annotations-cache .build/real_download
python scripts/train_real_candidate.py --help
```

第一阶段沿用 `data/real/training_manifest.json` 的270张训练照片和18张开发照片，官方 `models/yolov8s.pt` 为起点，最多60轮、batch=2、imgsz=1280。第二阶段从第一阶段的开发最优检查点继续，用747张训练图和原18张开发图微调最多20轮，其他训练设置见脚本及保存的训练参数。扩充图像只来自原48个训练日期，保留原270张，未借用开发或测试日期。两个阶段各自保留最佳模型，分别参加开发集比较；不能把第二阶段结果预设为更好。

```powershell
python scripts/train_real_candidate.py --weights models/yolov8s.pt --data .build/real_optimization/training/baseline270.yaml --manifest data/real/training_manifest.json --output .build/real_optimization/runs --name yolov8s_270_e60 --epochs 60 --batch 2 --imgsz 1280
python scripts/train_real_candidate.py --weights .build/real_optimization/runs/yolov8s_270_e60/weights/best.pt --data .build/real_optimization/training/dataset.yaml --manifest data/real_optimization/pk_training_manifest.json --output .build/real_optimization/runs --name yolov8s_747_e20 --epochs 20 --batch 2 --imgsz 1280
```

训练是随机数种子固定的数值计算；不同设备和软件版本仍可能产生差异。需要逐项比对本次结果时使用交付权重与冻结配置，不能只凭复训模型的文件名判断一致。

## 开发选择和报告

使用新的、空的输出目录比较复训模型：

```powershell
python scripts/compare_real_candidates.py --weights 你的候选/best.pt --id rerun_candidate --output .build/real_optimization/dev --device cpu
```

选择规则是：精确率和召回率均不低于 v1.1 的开发基线，然后最大化两者的较小值；平局依次看 F1、较低门槛、较小权重。门槛网格为0.20至0.80、步长0.05。原始开发表与无效候选一并保留。`select_real_candidate.py` 只用于在新的实验副本中冻结一次方案，不能在看过 CNR 结果后覆盖本次封存文件。

最终评测记录在 `reports/real_optimization/final/`，逐图预测、TP/FP/FN、占用矩阵和耗时均保留。`scripts/report_real_optimization.py` 校验冻结文件和计数后生成 [结果](../reports/real_optimization/RESULTS.md) 与 HTML；课程总报告另由 `scripts/build_course_report.py` 汇总。实际发布成绩以这些已运行记录为准，命令示例本身不代表训练或评测已完成。

只重新生成报告时，不需要训练或推理。Word生成器额外使用`python-docx==1.2.0`（`pyproject.toml`的`report`可选依赖），普通演示不需要它：

```powershell
python -m pip install python-docx==1.2.0
python scripts/report_real_optimization.py
python scripts/build_course_report.py
```

DOCX生成于`reports/胡轩铭模块实现与验证报告.docx`，可用Word另存为PDF。本次交付的PDF已通过实际Word导出和逐页渲染检查，核验记录见`reports/real_optimization/course_report_qa.json`。

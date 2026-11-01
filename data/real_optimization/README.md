# 真实照片优化数据

本目录与 v1.1 的 `data/real/` 分开保存。原 270 张训练照片、18 张开发照片和 36 张历史测试照片的清单保持不变。

## PKLot 训练扩充

`pk_training_selection.json` 在下载新增图像前封存了文件名、日期和配额。保留原 270 张训练图，每个视角/天气层最多选 90 张，并且只使用原 48 个训练日期。UFPR04 雨天仅有 45 张、UFPR05 雨天仅有 72 张满足日期条件；因此总量为 **747 张唯一照片**，不通过重复样本或借用开发/测试日期凑到 810。新增 477 张的顺序仅由日期轮转和文件名 SHA-256 决定。

数据下载、ROI 裁剪和标签转换完成后，`pk_training_manifest.json` 记录每张原图、裁剪图、标签的 SHA-256。原 270 张的裁剪图与标签必须与 v1.1 一致；验证集继续使用原 18 张开发照片，像素与标签也保持一致。训练图片和 `dataset.yaml` 写入本地缓存，不放进本目录。

```powershell
python scripts/prepare_real_optimization_data.py --task pk-training --cache .build/real_optimization/training --annotations-cache .build/real_download
```

已有原图时可用 `--reuse-originals 原图文件夹`；需要 GitHub CLI 下载时传 `--github-cli gh.exe的实际路径`。`--selection-only` 只封存并核对选择，不下载图像。该脚本不运行模型。

准备完成后，同一缓存还会生成 `baseline270.txt` 和 `baseline270.yaml`。前者只列出原 270 张训练 PNG，后者引用这份清单并继续使用原 18 张开发图。重训第一阶段时使用该 YAML 即可，无需再调用会重写旧训练清单时间戳的旧准备脚本。生成前会核对这些裁剪图及标签与 v1.1 清单中的 SHA-256 完全一致。

## CNRPark-EXT 外部验证

`external/` 包含按元数据预先固定的 **54 张真实照片**：全部 9 个相机、3 种天气，每个相机/天气层来自 2 个不同日期，共 20 个拍摄日期。图片文件没有修改；同一停车场的多相机画面仍存在相关性，不能把它们解释为 54 个独立停车场。

- `selection.json`：在下载照片和运行模型前固定的抽样规则、文件名与上游提交。
- `manifest.json`：原图、来源、SHA-256 和无占用状态的车位几何。
- `annotations.json`：独立保存的车辆框、车位占用真值和作者完整标注 ROI，仅供离线评分。
- `audit.json`：唯一图像、日期、旧 PK 划分交集及文件校验结果。
- `ATTRIBUTION.md`、`ODbL-NOTICE.txt`：CNR 数据的独立来源与许可说明。

```powershell
python scripts/prepare_real_optimization_data.py --task cnr-external --annotations-cache .build/cnr_metadata
```

CNR 数据只用于最终外部验证，不加入训练、开发调参或候选模型排序。模型和参数应先根据 PK 开发集冻结，再在全部 54 张 CNR 图上报告结果，保留困难样本和失败案例。camera1、camera9 的作者 ROI 只限定评分范围；原图与运行几何始终采用 1000×750 原生像素坐标。

PKLot 数据沿用其 CC BY 4.0 来源说明，CNRPark-EXT 派生数据单独按 ODbL 1.0 声明；应用代码的 AGPL 许可不替代数据许可。

# Windows 演示包重建

演示包使用真实 CPU YOLO 推理和本地 FastAPI 界面。双击 `ParkingDemo.exe` 自动打开浏览器，地址为 `http://127.0.0.1:8766`；关闭控制台即停止服务。构建复用 [PyInstaller](https://github.com/pyinstaller/pyinstaller) 的目录式分发。

实测环境为 Windows 11 x64、Python 3.12、PyInstaller 6.22.0、torch 2.5.1+cpu、torchvision 0.20.1+cpu。CPU wheel 来自 [PyTorch 官方安装源](https://download.pytorch.org/whl/cpu)。应用其余依赖见 `requirements.txt`。

## 准备独立 CPU 环境

在 Windows x64 安装 Python 3.12，完整下载仓库后，在仓库根目录执行：

```powershell
python -m venv .build/windows/cpu-venv
.build/windows/cpu-venv/Scripts/python.exe -m pip install torch==2.5.1+cpu torchvision==0.20.1+cpu --extra-index-url https://download.pytorch.org/whl/cpu
.build/windows/cpu-venv/Scripts/python.exe -m pip install -r requirements.txt pyinstaller==6.22.0
.build/windows/cpu-venv/Scripts/python.exe scripts/build_windows.py
```

首次构建需要下载依赖。脚本检查 CPU 版本，避免误捆绑 CUDA；所有路径相对仓库定位，不含本机路径或账号凭据。输出为 `dist/windows/ParkingDemo/`，可用 `--output-dir` 改变位置。

## 随包源码和许可证

脚本自动复制当前完整项目到 `source/`，保留图片、配置和报告，排除 `.git`、虚拟环境、缓存、`data/training_runs` 和构建输出。真实训练照片及其中间数据使用单独缓存，不装入演示包；原270图与扩充747图的训练划分、下载来源、SHA-256记录及复现脚本仍随源码提供，见 `data/real/training_manifest.json`、`data/real_optimization/` 与 `scripts/train_real_candidate.py`。项目第三方说明及已安装 wheel 内的许可证复制到 `licenses/`。保留项目根目录的 AGPL-3.0 LICENSE；PKLot 与 CNR 照片分别保留各自目录的归属和许可说明。

PyInstaller 官方 torch hook 会收集所需 DLL 和数据；不要再额外添加 `--collect-data torch`，否则可能捆入数百 MB 的 C++ 静态开发库。构建脚本在启动 PyInstaller 前检查以下运行资源，缺失或模型校验不符会明确报错：

- 原仿真的 `static/`、`models/parking_yolov8n.pt`、`reports/report.html`。
- `models/real_photo_config.json` 及其中 `weights` 指定的真实照片模型。权重必须是项目内相对路径，并与配置中的 SHA-256 相符，不能指向项目外的文件。
- `data/real/manifest.json`、`data/real/images/` 中的 54 张原尺寸照片及 `data/real/ATTRIBUTION.md`。这 54 张开发与测试照片随包离线可用。
- 配置 `extra_manifests` 指定的 CNR 外部验证清单、同目录的 54 张原始照片、`ATTRIBUTION.md` 和 `ODbL-NOTICE.txt`。打包逐图检查 SHA-256，并限制照片路径位于各自清单的 `images/` 内。
- `reports/real_photo_report.html`。

模型路径按冻结配置读取，无需在脚本中手改模型文件名。运行资源中只放照片和无占用真值的车位几何；离线评测标注随 `source/` 保留，真实照片推理不读取它。冻结资源定位遵循 [PyInstaller 官方文档](https://pyinstaller.org/en/stable/runtime-information.html)，临时文件写在 EXE 旁的 `runtime/`。

## 实际检查与压缩

双击 EXE，验证以下流程：初始场景出现真实检测框；空场可申请；确认另一个实际空车位后重新检测；从电梯旁人行通道寻车至实际位外；离场后重新检测。进入“真实照片”页，至少切换不同视角、天气的照片，点击检测，核对原图、模型 SHA、车辆框和多边形三态结果。再次检测应产生新的帧编号；这个过程应能在断网状态下使用本地照片与权重运行。检查两个实验报告入口均能打开。已有演示实例占用 8766 时先关闭，或使用 `ParkingDemo.exe --port 8767`。

v1.0 的 `portable_validation.json` 记录原仿真流程和非法 operation/scenario 输入检查。新增真实照片功能后必须重新构建，并以最终压缩包解压后的 EXE 检查新流程；不能用旧版记录替代。最终验收记录随发布包和报告交付，这不代替每台目标电脑的兼容性验证。

通过检查后，删除自己测试产生的临时 `runtime/`（或压缩时排除），将整个 `ParkingDemo` 文件夹打成 `ParkingDemo-Windows.zip`，保留 `_internal`、`source` 和 `licenses`。使用 `Get-FileHash -Algorithm SHA256` 记录压缩包哈希。

修改运行源码后必须重建 EXE，不能仅复制 `source/`，因为可执行代码在 PYZ 内。只修改报告或说明时可同步资源和源码副本后重新打 ZIP。此脚本不自动发布 GitHub Release。

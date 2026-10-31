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

脚本自动复制当前完整项目到 `source/`，保留图片、配置和报告，排除 `.git`、虚拟环境、缓存、`data/training_runs` 和构建输出。项目第三方说明及已安装 wheel 内的许可证复制到 `licenses/`。保留项目根目录的 AGPL-3.0 LICENSE。

PyInstaller 官方 torch hook 会收集所需 DLL 和数据；不要再额外添加 `--collect-data torch`，否则可能捆入数百 MB 的 C++ 静态开发库。`static`、`models`、`reports/report.html` 为必须资源。冻结资源定位遵循 [PyInstaller 官方文档](https://pyinstaller.org/en/stable/runtime-information.html)，临时文件写在 EXE 旁的 `runtime/`。

## 实际检查与压缩

双击 EXE，验证以下流程：初始场景出现真实检测框；空场可申请；确认另一个实际空车位后重新检测；从电梯旁人行通道寻车至实际位外；离场后重新检测。检查“实验与交接报告”能打开。已有演示实例占用 8766 时先关闭，或使用 `ParkingDemo.exe --port 8767`。

本次交付的 EXE 已在 Windows 11 x64 完成上述真实模型检查，并验证非法 operation/scenario 输入返回错误而不使服务崩溃；记录在包内 `portable_validation.json`。这不代替每台目标电脑的兼容性验证。

通过检查后，删除自己测试产生的临时 `runtime/`（或压缩时排除），将整个 `ParkingDemo` 文件夹打成 `ParkingDemo-Windows.zip`，保留 `_internal`、`source` 和 `licenses`。使用 `Get-FileHash -Algorithm SHA256` 记录压缩包哈希。

修改运行源码后必须重建 EXE，不能仅复制 `source/`，因为可执行代码在 PYZ 内。只修改报告或说明时可同步资源和源码副本后重新打 ZIP。此脚本不自动发布 GitHub Release。

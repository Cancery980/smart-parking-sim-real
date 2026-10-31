# 开源复用与素材说明

实施前已检查 GitHub 的 Ultralytics ParkingManagement 与 PythonRobotics A* 示例。停车区域管理原例提供检测与区域统计思路，但没有本项目规定的三态证据规则、版本一致性、单会话与确认事务；这些部分按需求补充。PythonRobotics 的栅格 A* 示例与本项目明确有向图不完全一致，实际寻路选择复用 NetworkX 的 A* 实现。

| 项目 | 实际用途 | 来源与许可证 |
| --- | --- | --- |
| Ultralytics 8.3.235 | YOLOv8n 预训练、微调与实际推理 | https://github.com/ultralytics/ultralytics/tree/v8.3.235 · AGPL-3.0 |
| YOLOv8n 官方权重 | 预训练探索及微调起点 | https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8n.pt |
| NetworkX 3.4.2 | nx.astar_path 有向车行与无向人行图 | https://github.com/networkx/networkx/tree/networkx-3.4.2 · BSD-3-Clause |
| Shapely 2.1.1 | 完整线段、障碍及区域包含判断 | https://github.com/shapely/shapely/tree/2.1.1 · BSD-3-Clause |
| FastAPI 0.115.14 | 本地界面 HTTP 服务 | https://github.com/fastapi/fastapi/tree/0.115.14 · MIT |
| PyTorch 2.5.1 | 真实神经网络计算 | https://github.com/pytorch/pytorch/tree/v2.5.1 · BSD-style |
| Pillow | 原创程序车辆绘制 | https://github.com/python-pillow/Pillow · HPND |

引用但未复制算法源码：

- https://github.com/ultralytics/ultralytics/blob/v8.3.235/ultralytics/solutions/parking_management.py
- https://github.com/AtsushiSakai/PythonRobotics （MIT，寻路教学参考）
- https://docs.ultralytics.com/modes/predict/ （原图边界框、置信度、推理接口）
- https://docs.ultralytics.com/modes/train/ （训练与验证接口）

车辆图形与地图渲染由本项目程序生成，没有下载照片、商业车辆贴图或第三方车辆素材。程序字体使用运行系统自带字体，字体文件未复制分发。检测权重与模型软件遵循其上游许可证；第三方许可证原文放在 third_party/，哈希与下载来源记录在 sources.json。requirements-lock.txt 记录实测环境。

本项目源码以 AGPL-3.0 发布，完整条款见根目录 LICENSE；第三方软件保留各自许可证及归属。可运行演示包同时附本项目源码与依赖许可证，源码仓库为 https://github.com/Cancery980/smart-parking-sim-real 。

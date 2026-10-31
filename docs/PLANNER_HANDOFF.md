# 梁可规划模块对接说明

胡轩铭模块负责图片、检测、三态占用、用户交互与停车会话。规划模块通过两个 Python 方法接收同一帧的只读数据，返回推荐或寻车路线。当前附带的 `parking_hu.reference_planner.Planner` 是联调用参考实现；它不是梁可已有代码，未声称已与未提供的代码合并。

## 复用基础

已核查 GitHub 的 [NetworkX](https://github.com/networkx/networkx) 和 [PythonRobotics](https://github.com/AtsushiSakai/PythonRobotics)。参考适配器直接调用 NetworkX 的 [`astar_path`](https://networkx.org/documentation/stable/reference/algorithms/generated/networkx.algorithms.shortest_paths.astar.astar_path.html)，使用欧氏距离启发式、米制边长和有向车行图。PythonRobotics 为算法参考，未复制其网格实现，因为本项目要求固定的有向路网和严格分离的人行图。完整线段、区域和障碍判断使用 Shapely。

## 接入步骤

1. 将梁可模块放在项目内，例如 `liang_planner.py`，或安装为可导入的 Python 包。
2. 提供无参类 `Planner`，实现 `plan(context, request)` 和 `find_vehicle(context, request)`。字段约定见 `CONTRACT_V1.md`；返回普通字典即可，无需接触 FastAPI、界面或 YOLO。
3. 启动时把规划插件设置成 `liang_planner:Planner`。启动器的具体命令见项目 README。
4. 使用同一输入对照参考实现，检查首个失败原因、目标 ID、路线端点、方向、障碍、距离和场景版本。先用 `check_plugin.py --planner ...` 检查自己的插件，再用 `run_e2e.py --planner ...` 执行真实 YOLO 端到端验收，无需修改测试文件中的导入。

在已安装依赖的源码根目录执行：

```powershell
.\.venv\Scripts\python.exe scripts/check_plugin.py --planner liang_planner:Planner
.\.venv\Scripts\python.exe scripts/run_e2e.py --planner liang_planner:Planner --out reports/e2e_liang
```

`--planner` 的默认值为 `parking_hu.reference_planner:Planner`，检查自定义模块时须显式指定。`--out reports/e2e_liang` 将真实 E01—E08 的场景、快照和 `results.json` 写入独立目录，保留已有默认记录。随包接线样例可用 `--planner examples.liang_adapter:Planner --out reports/e2e_external` 运行。契约通过不代替真实端到端验收；两者也不代替冻结场景上的配对性能实验。

调用输入是 `contract_version,map,snapshot,vehicle,session`。`snapshot` 中的 `occupancy` 是推荐依据，`obstacles_m` 包含当前有效帧所有保留车框。输入不含场景真值或背景实体位置。每次调用使用深拷贝，修改它也不会改变正式场景，但插件应保持只读。成功和失败都需要完整的响应字段，失败时不得残留上一次路径。

成功推荐的 `routes` 必须含 `drive_in`、`drive_out`、`walk_to_destination` 三条路径，主 `path_m` 与 `drive_in` 相同。路径须列出经过的全部图节点，不省略共线中间节点、不添加插值点；寻车先列原始起点，再列投影点及后续节点，二者重合时只列一次。对应距离必须等于完整折线长度（允许展示两位小数的舍入误差）。主模块使用 `validate_returned_routes` 对给定结果做几何和方向检查，不重新搜索，也不替换插件选择。

## 必须保持的边界

车位和道路全部来自 `map_data.build_map()`。车行图只在当前候选车位添加末端支路，不能穿过其他车位。每个候选须同时满足入场、出场和到电梯可达；人行终点是 `ped_access_m`，不是车位中心。

资格与需求分开。燃油车必须充电首先失败；只有提出无障碍需求时才检查资格。专用类型没有空位时不静默改为普通位；无障碍和充电同时需要时，本图返回 `no_eligible_spot`。普通位申请不受无障碍资格未知影响。两种偏好的距离在比较前按四舍五入转为厘米整数，并以车位数字 ID 破除并列。

寻车起点先确认位于人行可行域，再投影到最近人行边。最大距离为 0.6 m，连接线完整位于人行域且不穿障。等距时取边 ID 字典序最小者。保留目标车辆的检测框；目标接近点被阻挡应返回 `no_route`，不能删除车辆框或改用车位中心。

会话归属、计划失效、确认、取消、离场和实际实体更新由胡轩铭模块负责。有效重规划开始后的失败将由主模块清空旧计划；请求格式或会话前置错误保留旧计划。规划模块只报告计算结果，不操作实体、不推断真值空位。

## 基准距离

空地图中 P001 入场 110.60 m、出场 79.10 m、步行 39.65 m；P041 分别为 87.60、102.10、16.65 m；P045 分别为 28.80、43.30、51.15 m。这些是固定路网的校验值。P023—P035 占用、其余空闲时，普通需求少走路选 P022，近出口选 P040。

`validate_networks(map_data)` 用于启动时验证 52 位的三条路线与静态几何。上述路线是抽象引导线，验证不包含车辆转弯半径、车身净空或自动驾驶控制。

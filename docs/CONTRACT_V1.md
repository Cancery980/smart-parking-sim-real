# 模块接口 1.0

Python 3.12（实测依赖中的 SciPy 1.18.1 要求此版本）；所有公开对象为可 JSON 序列化的 dict/list。坐标原点左上角，X 向右、Y 向下，米；图像 1664×1024、32 px/m。所有工作以 demo52_v12 为准。

## 地图模块

`parking_hu.map_data.build_map() -> dict` 返回 `map_id,width_m,height_m,px_per_m,image_size,spots,car_areas_m,ped_areas_m,static_obstacles_m,entry_m,exit_m,destination_m`。`spots` 为列表，每项 `spot_id,spot_type(normal/accessible/charging),rect_m,polygon_m,center_m,car_access_m,ped_access_m`。areas 和 obstacles 是矩形 `[x1,y1,x2,y2]` 列表。外围墙及柱子包含在 static_obstacles_m。

## 场景与检测

`parking_hu.scenes.make_scene(map_data, seed=42, scenario='normal', count=None) -> dict`，返回 `scene_id,seed,scenario,vehicles`；vehicles 每项 `vehicle_id,rect_m,color`（可含渲染属性）；背景 ID 以 BG_ 开头。`render_scene(map_data, scene, output_path) -> pathlib.Path` 保存原图，无额外留白，无真值框，禁止从状态着色后再检测。自车实体使用同一格式。

`parking_hu.detector.YoloDetector(weights_path).detect(image_path, scene_version, frame_id) -> list[dict]` 只输出 car，项含 `detection_id,bbox_xyxy_px,class_name,confidence`，坐标为原图连续边界。函数真实调用模型，不读 scene_truth。模型权重最终保存在 `models/parking_yolov8n.pt`。

`parking_hu.occupancy.estimate_occupancy(map_data, detections, scene_version, frame_id, *, valid=True, error=None, policy='three_state') -> dict` 返回 `map_id,scene_version,frame_id,valid,error,occupancy,detections,obstacles_m`。occupancy 是 spot_id -> `{state,reason,detection_ids}`；detections 保留检测项并添加 bbox_m；obstacles_m 为所有 >= 0.20 的 bbox_m 列表。严格校验坐标、置信度、重复 ID 等，失败整帧 unknown。二态仅离线对照使用。

## 梁可规划插件

默认插件 `parking_hu.reference_planner:Planner`。用户替换为 `liang_planner:Planner`，该类无参构造，实现：

```python
class Planner:
    def plan(self, context: dict, request: dict) -> dict: ...
    def find_vehicle(self, context: dict, request: dict) -> dict: ...
```

context 固定包含 `contract_version='1.0',map,snapshot,vehicle,session`。`vehicle` 为所选登记记录，包含 vehicle_id/power_type/accessible_permission/role；`session` 为当前会话摘要或 null。**不提供 scene_truth、实体车辆坐标或真值占用**。find_vehicle 的 session 包含实际停车 ID。

plan request 字段：`operation`(allocate/replan)、`vehicle_id`、`session_id`(allocate 为 null)、`needs_accessible` bool、`needs_charging` bool、`prefer_mode`(near_destination/near_exit)、`destination_id='elevator_1'`、`pedestrian_start_m=null`。会话前置校验由胡模块负责；需求业务规则及可达性由 Planner 负责。find_vehicle request 字段 operation/vehicle_id/session_id/pedestrian_start_m。

Planner result 必含 `status,reason_code,message,target_spot_id,path_m,distances_m`。allocate/replan 成功还须返回 `routes` 的 drive_in/drive_out/walk_to_destination 三条路径，path_m 等于 drive_in。find_vehicle 的 path_m 是寻车路线，routes 可带 walk_to_vehicle。**路线保留全部路网节点，不省略共线的中间节点**；合法手选起点与投影的连接段除外。主模块校验返回的端点、线段、方向、通行域、障碍和距离，错误插件返回 planner_unavailable/planner_contract_error。失败 target_spot_id=null、path_m=[]、distances_m=null。其余 status 使用需求规格书约定。主模块添加 operation/session_id/map_id/scene_version/frame_id。插件不得修改 context；主模块传入深拷贝。

排序和 A* 规则、资格失败顺序、投影上限 0.6 m、完整线段/障碍验证见 full_spec 摘要及参考适配器。插件不负责实体更新、确认、取消、离场、渲染或感知。

## 本地 HTTP 交互

GET `/api/state` 返回 `map,scene_version,frame_id,snapshot,registry,session,last_result,image_url,planner_name`。session.state 是 idle/planned/parked，空闲也返回对象。POST `/api/action` 接受 request，加上 operation 可为 allocate/replan/find_vehicle/confirm_park/cancel/depart/reset/refresh/change_scene。confirm_park 额外 actual_spot_id 和 scene_version。change_scene 额外 seed/scenario（normal/empty/full/occluded/boundary/blocked）。服务器只绑定本机。

POST 响应 `{result,state}`；失败同样返回结构化 JSON 和中文说明。业务失败用 HTTP 200，JSON 格式错误用 422。场景/确认/离场自动重跑真实检测。GET `/api/frame` 当前原始渲染 PNG。界面可读取报告链接 `/api/report`。

GET `/api/export` 用于当前状态诊断：`context` 是导出时的无真值状态，`request` 是最近一次规划请求，`result` 是最近一次操作结果。三者可能来自不同时间点，例如确认停车后结果是确认事件，上下文已经 parked；不能作为同一次规划调用的原样回放包。需要一致的接线输入/输出时，使用 `examples/planner_context.json` 或 `scripts/check_plugin.py` 生成的案例。

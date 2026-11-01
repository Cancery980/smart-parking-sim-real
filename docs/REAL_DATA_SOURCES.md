# 真实停车场数据与来源核对

核对日期：2026-11-01。真实照片选择 **PKLot 原始全景照片**，车辆和车位评测使用 **MetaPKLot 作者补充的人工标注**。这两类标注分开读取，车位矩形不能充当车辆框真值。

## 采用的数据

PKLot 来自巴西的两个物理停车场，包含 UFPR04、UFPR05、PUCPR 三个拍摄视角，覆盖晴天、阴天、雨天。原版图片为 1280 × 720，按视角、天气和采集日期组织；原版 XML 主要标记车位位置及是否占用。来源：[PKLot 原作者页面](https://web.inf.ufpr.br/vri/databases/parking-lot-database/)。原站本次直接打开多次超时，数据说明和许可通过原站搜索索引及作者维护的下游数据卡交叉核对。

MetaPKLot 增补了车辆分割、车辆框、车辆标识及修订后的车位多边形，可分别评估车辆检测与车位状态。论文于 2026-08-29 发表；此处使用数据及格式，不照搬论文的性能结论。[论文](https://doi.org/10.1007/s00521-026-12398-0)、[作者 GitHub 数据仓库](https://github.com/DSBD-Research/MetaPKLot-Dataset)。

数据版本固定到 Git 提交 `9218800c4eb2b7c1b17b51c8cdb07ad51686f8a2`。按所选图片单独下载，避免为了课程实验获取整个数 GB 数据集。原图保持不变；抽样清单、图像哈希、标注格式转换及实验划分由本项目另行记录。

## 精确下载入口

以下链接固定提交版本；文件大小为该版本 Git 树记录，单位为字节。

| 视角 | 车辆标注包 | 车位标注包 |
| --- | --- | --- |
| UFPR04 | [ufpr04_cars.tar.xz](https://raw.githubusercontent.com/DSBD-Research/MetaPKLot-Dataset/9218800c4eb2b7c1b17b51c8cdb07ad51686f8a2/annotations/original/cars/PKLot/ufpr04_cars.tar.xz)，1,426,408 | [ufpr04_spots.tar.xz](https://raw.githubusercontent.com/DSBD-Research/MetaPKLot-Dataset/9218800c4eb2b7c1b17b51c8cdb07ad51686f8a2/annotations/original/spots/PKLot/ufpr04_spots.tar.xz)，662,164 |
| UFPR05 | [ufpr05_cars.tar.xz](https://raw.githubusercontent.com/DSBD-Research/MetaPKLot-Dataset/9218800c4eb2b7c1b17b51c8cdb07ad51686f8a2/annotations/original/cars/PKLot/ufpr05_cars.tar.xz)，1,593,204 | [ufpr05_spots.tar.xz](https://raw.githubusercontent.com/DSBD-Research/MetaPKLot-Dataset/9218800c4eb2b7c1b17b51c8cdb07ad51686f8a2/annotations/original/spots/PKLot/ufpr05_spots.tar.xz)，929,244 |
| PUCPR | [pucpr_cars.tar.xz](https://raw.githubusercontent.com/DSBD-Research/MetaPKLot-Dataset/9218800c4eb2b7c1b17b51c8cdb07ad51686f8a2/annotations/original/cars/PKLot/pucpr_cars.tar.xz)，3,194,472 | [pucpr_spots.tar.xz](https://raw.githubusercontent.com/DSBD-Research/MetaPKLot-Dataset/9218800c4eb2b7c1b17b51c8cdb07ad51686f8a2/annotations/original/spots/PKLot/pucpr_spots.tar.xz)，4,709,984 |

图片按 JSON 的 `file_name` 拼接同一固定提交的 raw 根地址。例如：[UFPR04 阴天原始照片](https://raw.githubusercontent.com/DSBD-Research/MetaPKLot-Dataset/9218800c4eb2b7c1b17b51c8cdb07ad51686f8a2/PKLot/UFPR04/Cloudy/2012-12-12/2012-12-12_10_00_05.jpg)。该示例只说明下载方法，不代表测试样本选择或成功案例。

## 许可与署名

| 资源 | 核对结果与本项目处理 |
| --- | --- |
| PKLot 图片与原始数据库 | 原作者标为 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)。保留原作者、论文、来源、许可，明确本项目抽样及格式转换。 |
| MetaPKLot 中 PKLot 的修订/新增标注 | 作者 [Hugging Face 数据卡](https://huggingface.co/datasets/DSBD-Research/MetaPKLot-Dataset) 说明提供 PKLot 修订及新增标注和对应图片，并在 PKLot 部分列 CC BY 4.0。本项目依据该分项声明使用，增加 MetaPKLot 作者署名。所检车辆 JSON 不含独立 `licenses` 字段，GitHub 根目录也没有一份统一 LICENSE；不能将整库笼统标为一个许可。 |
| MetaPKLot 配套实现 | [MetaPKLot-Challenges](https://github.com/DSBD-Research/MetaPKLot-Challenges) 的代码许可为 MIT；这是代码许可，不用来覆盖图片或数据标注。 |
| CNRPark-EXT | 作者 [官网](https://cnrpark.it/) 标为 ODbL 1.0。本次研究比较了该候选，当前选取 PKLot 部分，避免混合数据库许可。 |

建议论文引用信息：

1. Almeida, P.; Oliveira, L. S.; Silva Jr., E.; Britto Jr., A.; Koerich, A. **PKLot – A robust dataset for parking lot classification.** Expert Systems with Applications, 42(11):4937–4949, 2015。题名、作者和卷页以 [原作者页面](https://web.inf.ufpr.br/vri/databases/parking-lot-database/) 为来源。
2. de Almeida, P. L.; Alves, J. H.; Kujavski, L. M.; Alves, P. L.; Oliveira, L. E. **MetaPKLot – new challenges and protocols for vision-based parking lot management.** Neural Computing and Applications, 38, Article 704, 2026。[DOI: 10.1007/s00521-026-12398-0](https://doi.org/10.1007/s00521-026-12398-0)。

## 读取与评测时必须区分的字段

以下事项来自作者格式说明，并通过固定版本 UFPR04 车辆 JSON 实读核对：

- `bbox` 使用像素坐标 `[左上 x, 左上 y, 宽, 高]`；模型输出若为 `xyxy`，须显式转换后比较。
- `images[].file_name` 在实际文件里已经包含 `PKLot/` 前缀；不能重复拼接。图片 `id` 与标注 `image_id` 关联。
- 类别号必须读取 `categories`。README 的示例 car 为 0，而实际 UFPR04 文件 car 为 **1**；示例不可以替代解析。
- `annotationsRectangle` 为完整标注区域。区外车辆不能直接当漏标负样本。实读 UFPR04 的 2,754 张有车辆标注图片该区域均为整图；其他文件仍须逐项检查，不能推广假设。
- `car_id` 在连续帧间可重复，用于同一实体关联；不能把每个相同 ID 当成新的独立车辆。车位标签中的 `car_id = -2` 表示被非汽车对象占用，不能擅自改成空闲。
- 车位占用真值用于离线评分；运行时允许使用车位几何标定，但不把标注的 occupied 值输入检测器或覆盖预测。

本项目按采集日期隔离开发集与测试集，选择分布于三个视角和三类天气的图片，先冻结清单再评测。这样的证据可支持“在这些真实拍摄样本上的效果”，不自动等同于跨停车场独立泛化。UFPR04 和 UFPR05 来自同一物理场地；相邻帧高度相关，不宜随机逐帧切分。MetaPKLot 论文的跨数据集协议更严格，本课程实验不得冒充已完成该论文全套挑战。[作者格式与协议说明](https://github.com/DSBD-Research/MetaPKLot-Dataset/blob/9218800c4eb2b7c1b17b51c8cdb07ad51686f8a2/README.md)。

## 已检查的现成项目与采用理由

复用 Ultralytics YOLO 推理、既有图像几何依赖和 MetaPKLot 人工标注。MetaPKLot-Challenges 提供占用分类、停车时长和车位提取基线；这些任务与当前“车辆检测后作三态占用”的课程接口不同，未为了复用而整体替换已有停车会话与规划系统。本次新增部分限定为真实照片加载、像素坐标车位标定适配、独立评测和展示。

尚不能由来源核对确认的事项：人工标注并不保证绝对无误；本文档没有逐张复核整库，实际评测应保留低分/错误样本供查看。真实照片没有本项目所需的米制地图和道路合法性标定，不能直接把照片像素当作 52 × 32 米停车场坐标进行导航。

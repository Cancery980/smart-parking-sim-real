# PKLot 真实照片与 MetaPKLot 标注

图片原作者：Paulo de Almeida、Luiz S. Oliveira、E. Silva Jr.、A. Britto Jr.、A. Koerich。PKLot – A robust dataset for parking lot classification，Expert Systems with Applications，2015。

车辆及修订车位标注来源：DSBD Research / MetaPKLot 作者。MetaPKLot – new challenges and protocols for vision-based parking lot management，2026，https://doi.org/10.1007/s00521-026-12398-0。

本目录使用 MetaPKLot 的 **PKLot 部分**，依据作者数据卡的分项许可说明按 **Creative Commons Attribution 4.0 International（CC BY 4.0）**提供。它不属于项目 AGPL 代码许可覆盖的数据。

- 原始数据库：https://web.inf.ufpr.br/vri/databases/parking-lot-database/
- 作者代码与数据：https://github.com/DSBD-Research/MetaPKLot-Dataset
- 分项许可说明：https://huggingface.co/datasets/DSBD-Research/MetaPKLot-Dataset
- 许可正文：https://creativecommons.org/licenses/by/4.0/legalcode
- 固定提交：9218800c4eb2b7c1b17b51c8cdb07ad51686f8a2

本项目进行了分层抽样、字段格式转换及运行时几何与离线真值分离；images 内 JPEG 未作画面编辑。reports 下的叠框评测图是在原图上的本项目可视化。每张原图的精确 URL 和 SHA-256 见 manifest.json；抽样规则见 protocol.json。训练原图通过脚本下载，不在此目录重复打包；其精确来源、哈希与 ROI 裁剪规则见 training_manifest.json。

引用或再分发这些照片/标注时，请保留本署名、许可及变更说明。作者未对本课程项目作认可或背书。

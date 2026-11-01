# CNRPark-EXT 外部验证子集

本目录保存 54 张从未用于本项目训练或调参的 CNRPark-EXT 实拍照片。它与 PKLot 的训练、开发和历史测试文件分开保存。9 个相机来自同一个停车场，不能当作 9 个独立停车场。

照片原作者：Giuseppe Amato、Fabio Carrara、Fabrizio Falchi、Claudio Gennaro、Claudio Vairo。原始项目：[CNRPark](https://cnrpark.it/)。车辆与修订车位标注来自 [MetaPKLot 作者仓库](https://github.com/DSBD-Research/MetaPKLot-Dataset/tree/9218800c4eb2b7c1b17b51c8cdb07ad51686f8a2)，请同时引用 Paulo Lisboa de Almeida 等人的 [MetaPKLot 论文](https://doi.org/10.1007/s00521-026-12398-0)。

本 CNR 数据库部分按 [Open Data Commons Open Database License 1.0](https://opendatacommons.org/licenses/odbl/1-0/) 提供，参见固定提交的[作者许可说明](https://github.com/DSBD-Research/MetaPKLot-Dataset/blob/9218800c4eb2b7c1b17b51c8cdb07ad51686f8a2/README.md#licensing-and-usage)。它不是 PKLot 的 CC BY 4.0 数据，也不随应用源码改为 AGPL。作者 JSON 未提供独立 licenses 字段，本目录沿用作者对 CNRPark-EXT 部分的说明。

修改内容：按事先固定的相机、天气、日期哈希选取子集；JPEG 字节未修改；标注转换为原图像素多边形与单独的离线评分文件。原始完整数据库、相对路径、提交号、输入标注包和每张图片 SHA 由 selection.json、manifest.json 和 audit.json 记录。公开传播本派生数据库时保留此来源、ODbL 声明及可取得的派生数据。

使用边界：manifest.json 只向运行时提供图像和无状态车位几何；annotations.json 保存车辆框、占用真值及作者完整标注 ROI，供离线评分。camera1/9 的 ROI 不能被误当成原始图片尺寸，原图均为 1000×750。

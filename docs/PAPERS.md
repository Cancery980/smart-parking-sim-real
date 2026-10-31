# 文献核对与本项目使用边界

核对日期：2026 年 10 月 31 日。本文供实验报告与答辩引用，依据原论文、出版商记录和作者维护的项目页，说明已有方法如何用于本项目。文献中的实验结果不作为本项目的实测结果。

## 1 YOLO 车辆检测

Joseph Redmon, Santosh Divvala, Ross Girshick, Ali Farhadi. **You Only Look Once: Unified, Real-Time Object Detection**. *CVPR*, 2016: 779–788. DOI：[10.1109/CVPR.2016.91](https://doi.org/10.1109/CVPR.2016.91)。[CVF 正式论文页](https://openaccess.thecvf.com/content_cvpr_2016/html/Redmon_You_Only_Look_CVPR_2016_paper.html)；[作者公开稿](https://arxiv.org/abs/1506.02640)。

论文将整幅图像的目标检测建模为边界框与类别输出，采用一次网络前向计算完成预测；同时讨论定位误差、小目标及未见构型的局限。它支持本项目“先检测车辆，再由几何和业务规则估计车位占用”的技术分工。[原论文第 1、2.3、2.4 节](https://arxiv.org/pdf/1506.02640)

本项目实际调用的是 Ultralytics YOLOv8n。2016 年论文用作 YOLO 方法的来源，不能写成 YOLOv8n 架构论文，也不能把其帧率或准确率写成当前程序成绩。**工程判断**：COCO 预训练权重能否识别本项目的俯视合成车辆，需要独立探索；原文的自然图像到艺术图像实验不验证这套素材。微调后也须单独报告未参与训练和调参场景的结果。

## 2 A* 图路径规划

Peter E. Hart, Nils J. Nilsson, Bertram Raphael. **A Formal Basis for the Heuristic Determination of Minimum Cost Paths**. *IEEE Transactions on Systems Science and Cybernetics*, 1968, 4(2): 100–107. DOI：[10.1109/TSSC.1968.300136](https://doi.org/10.1109/TSSC.1968.300136)。[IEEE 出版记录](https://ieeexplore.ieee.org/document/4082128)；[作者所在机构保存的原论文](https://ai.stanford.edu/~nilsson/OnlinePubs-Nils/PublishedPapers/astar.pdf)。

论文研究如何将启发信息用于图搜索，并讨论满足条件时的最小代价路径性质。本项目沿固定有向车行图、无向人行图调用已有 A* 实现；边长使用米，完整线段的通行域和障碍检查决定哪些边可用。路线最短性只针对传入的合法图与代价定义。[IEEE 原论文摘要](https://ieeexplore.ieee.org/abstract/document/4082128)

本项目的车辆资格、特殊车位约束、双模式排序、实际停车记录和场景版本管理属于业务层。A* 论文不能证明这些规则正确，也不能证明车辆转弯半径、车身净空、自动驾驶安全或实际道路拥堵改善；本项目只展示抽象路网引导线。

## 3 PKLot 停车占用基准

Paulo R. L. de Almeida, Luiz S. Oliveira, Alceu S. Britto Jr., Eunelson J. Silva Jr., Alessandro L. Koerich. **PKLot – A robust dataset for parking lot classification**. *Expert Systems with Applications*, 2015, 42(11): 4937–4949. DOI：[10.1016/j.eswa.2015.02.009](https://doi.org/10.1016/j.eswa.2015.02.009)。[出版商论文页](https://www.sciencedirect.com/science/article/pii/S0957417415001086)；[作者公开稿](https://www.inf.ufpr.br/lesoliveira/download/ESWA2015.pdf)；[作者实验室数据页](https://web.inf.ufpr.br/vri/databases/parking-lot-database/)。

PKLot 提供来自不同停车场、视角和天气条件的图像及车位占用标注，论文比较在已见与未见停车场上的分类表现。它适合作为数据划分和跨场景评测的参考：车位分类与整图车辆检测是不同任务，不能直接比较两者的准确率。[原论文](https://www.inf.ufpr.br/lesoliveira/download/ESWA2015.pdf)

本项目使用固定地图上的自制合成场景。训练、验证和测试种子分离可以减少场景泄漏；**这仍是合成域内的留出验证**，不能据此宣称通过 PKLot 基准或具备真实停车场泛化能力。

## 4 CNRPark-EXT 的复杂条件验证

Giuseppe Amato, Fabio Carrara, Fabrizio Falchi, Claudio Gennaro, Carlo Meghini, Claudio Vairo. **Deep learning for decentralized parking lot occupancy detection**. *Expert Systems with Applications*, 2017, 72: 327–334. DOI：[10.1016/j.eswa.2016.10.055](https://doi.org/10.1016/j.eswa.2016.10.055)。[出版商论文页](https://www.sciencedirect.com/science/article/pii/S095741741630598X)；[CNR 机构记录](https://openportal.isti.cnr.it/doc?id=people______%3A%3Aeb4ca20cf01aa9e6060a5e1db2d6b8e8)；[作者项目页](https://cnrpark.it/)。

论文研究面向智能摄像头的停车位占用 CNN，并在 PKLot 与 CNRPark-EXT 上评测。后者覆盖不同摄像头视角、光照、阴影和遮挡。对本项目的直接用途是按生成条件预先组织正常、遮挡和边界场景，并保留失败样例，避免只挑选检测成功的图片。[论文及实验设置](https://www.sciencedirect.com/science/article/pii/S095741741630598X)

本次没有运行该论文的模型或官方数据集，因此不复用其性能结论。合成遮挡也不等同于真实摄像头中的全部遮挡、反光或天气条件。

## 本项目可以如何表述创新

本项目复用目标检测与寻路组件，在检测证据之上增加 `free / occupied / unknown` 三态占用、资格和车位类型约束，以及人工停车确认后的寻车流程。**三态是本项目的占用与决策规则，不是新的检测网络，也没有由以上文献直接证明其收益。**

是否减少假空位推荐，只能由本项目冻结后的配对实验回答：二态与三态使用相同图像、真实检测缓存、资格、路网和全部保留车框障碍，仅改变占用规则；同时报告假空位推荐率、有效推荐率和未推荐率。无差异、覆盖下降或完全漏检都应如实保留。参考论文用于交代背景与设计依据，实际结论以 `reports/` 中可追溯运行记录为准。

核对说明：书目信息已与出版商或作者机构记录交叉核对；部分出版商网页/PDF存在访问限制，YOLO 使用作者公开稿，PKLot 与 A* 另保留作者原稿入口。没有以二手博客或检索网站的自动总结替代论文依据。

# 精确 512D ERes2Net 权重：训练分布证据边界

核查日期：2026-11-04 UTC。对象为已安装的 `iic/speech_eres2net_base_sv_zh-cn_3dspeaker_16k`，ONNX SHA-256：

`1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b`

## 结论

**可以证明当前文件声明的模型家族、512维输出和16 kHz输入契约；不能证明这份精确权重的完整训练语料分布。** 官方模型注册表、历史512维训练recipe及3D-Speaker语料论文共同支持“与3D-Speaker中文/方言训练路线相关”，但缺少 ONNX → 原始checkpoint摘要 → 训练配置/版本 → 实际训练样本清单的绑定。不能把语料集总量、架构论文实验或历史recipe默认值直接写成这份权重已经核实的训练事实。

## 分层证据矩阵

| 问题 | 已核实的一手证据 | 对精确已安装权重能说到哪一步 |
|---|---|---|
| 语言 | 文件元数据为 Chinese；官方导出脚本按模型名中的 zh-cn 自动生成该标签。 | 是用途/命名声明，不是训练语言占比、纯中文训练或跨语种能力证明。[导出脚本](https://github.com/k2-fsa/sherpa-onnx/blob/11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf/scripts/3dspeaker/export-onnx.py#L124-L144) |
| 模型与checkpoint身份 | 官方注册表将该家族映射至 ModelScope v1.0.1、eres2net_base_model.ckpt、512维、32基础通道。 | 注册表声明可核实，但未取得原始checkpoint摘要与训练记录。不要与另一个 base_200k 模型混同，也不能据此宣称20万训练说话人。[历史注册表](https://github.com/modelscope/3D-Speaker/blob/71258283ee6b64d1794b4a8636e78f3b276739a0/speakerlab/bin/infer_sv.py#L72-L118)、[当前固定版本注册表](https://github.com/modelscope/3D-Speaker/blob/065629c313eaf1a01c65c640c46d77e61e9607b4/speakerlab/bin/infer_sv.py) |
| ModelScope模型card | 官方页面身份与URL可确认；正文返回0行。README/configuration接口及版本化文本地址未成功取得正文。 | **本核查没有直接验证任何实质性card字段。** “约10k说话人训练”“模型性能”等即使出现在第三方转载，也不应冒充已核验的官方card内容。即便未来读到card，还须区分“发布者声明”与“精确权重绑定”。[官方模型页](https://www.modelscope.cn/models/iic/speech_eres2net_base_sv_zh-cn_3dspeaker_16k/summary) |
| 语料人数、段数、时长 | 3D-Speaker论文的训练语料为10,000人、579,013段、1124.52小时。 | 这是**公开语料集**的统计，不是已验证的权重实际入训统计。最终清洗、过滤、混合语料、权重采样及重复次数未知。[论文v3，§3及表3](https://arxiv.org/pdf/2306.15354v3) |
| 语种/方言构成 | 官方语料网站称14类方言；论文包括普通话及地方方言。论文§3.3/表4列1074名多方言说话人，而§3概述出现1200，存在表述差异。 | 不把任一数字或方言比例绑定到模型训练。不能推断English/Chinese混合比例，也不能证明训练不含其他来源。[官网](https://3dspeaker.github.io/)、[论文v3](https://arxiv.org/pdf/2306.15354v3) |
| 采样率 | ONNX元数据明确sample_rate=16000；历史recipe同为16k。语料论文列16k与48k，基线处理将阵列首通道降采样至16k。 | 16k是直接可核实的**运行输入契约**；它不证明所有原始录音均以16k采集，也不证明这份权重的实际重采样流程。[论文v3，§4](https://arxiv.org/pdf/2306.15354v3)、[历史recipe](https://github.com/modelscope/3D-Speaker/blob/71258283ee6b64d1794b4a8636e78f3b276739a0/egs/3dspeaker/sv-eres2net/conf/eres2net.yaml) |
| 录制条件 | 语料包含电话/平板、电脑、录音笔、定向麦克风及阵列，距离0.1–4m，同一发声可被多个设备同步录制。 | 是语料条件，不证明模型见过各条件的实际比例；训练输入中的设备、距离、SNR、房间、声道与时长分布仍未知。[论文v3，§3](https://arxiv.org/pdf/2306.15354v3) |
| 实际训练切段长度 | 历史512维recipe指定3秒；WavReader随机取3秒，短音频补零，启用0.9/1.0/1.1倍速扰动。 | 这是可复核的历史训练程序行为，**没有绑定到该checkpoint**。更不能将3秒当作推理必须长度。[recipe](https://github.com/modelscope/3D-Speaker/blob/71258283ee6b64d1794b4a8636e78f3b276739a0/egs/3dspeaker/sv-eres2net/conf/eres2net.yaml#L16-L38)、[处理代码](https://github.com/modelscope/3D-Speaker/blob/71258283ee6b64d1794b4a8636e78f3b276739a0/speakerlab/process/processor.py) |

本次没有获得精确权重的语言/方言比例、过滤后真实人数/小时数、各设备/距离比例、录音场景混合比例、数据集版本/训练清单或预训练/微调混合来源。上述空白应保留为未知。

## 新发现：历史recipe也不是完整语料的原样使用

在同一历史版本 `71258283ee6b64d1794b4a8636e78f3b276739a0`：

- [prepare_data.sh](https://github.com/modelscope/3D-Speaker/blob/71258283ee6b64d1794b4a8636e78f3b276739a0/egs/3dspeaker/sv-eres2net/local/prepare_data.sh) 对训练和测试索引都显式排除含 `Device09` 的记录。这里不猜测该编号对应什么设备。因此，**即使假定采用该recipe，论文全量统计也不能直接等同实际训练集合**。
- [prepare_data_csv.py](https://github.com/modelscope/3D-Speaker/blob/71258283ee6b64d1794b4a8636e78f3b276739a0/egs/3dspeaker/sv-eres2net/local/prepare_data_csv.py) 跳过读取失败、非16k或非单声道文件。实际过滤后的数量需运行清单证明，本次未取得。
- CSV准备默认的4秒分段用于生成条目；[WavSVDataset](https://github.com/modelscope/3D-Speaker/blob/71258283ee6b64d1794b4a8636e78f3b276739a0/speakerlab/dataset/dataset.py) 按path读取并交给WavReader，不把CSV的start/stop传入切音。因此不能将这个4秒字段误报成模型输入的训练切段长度；实际程序路径是recipe的3秒随机取样。相同源文件可能对应多个训练条目，也不能把条目数视为独立录音数。
- [train.py](https://github.com/modelscope/3D-Speaker/blob/71258283ee6b64d1794b4a8636e78f3b276739a0/speakerlab/bin/train.py) 按标签编码器人数重设分类数，倍速扰动时乘3。YAML里的5994既不是已核实真实人数，增广后的类别数也不是新增真实说话人数。
- [历史recipe](https://github.com/modelscope/3D-Speaker/blob/71258283ee6b64d1794b4a8636e78f3b276739a0/egs/3dspeaker/sv-eres2net/conf/eres2net.yaml) 设置aug_prob=0.8，预处理代码随机选噪声、混响或二者。它证明程序配置存在，不能证明该checkpoint的实际增广分布。

[ERes2Net架构论文](https://www.isca-archive.org/interspeech_2023/chen23o_interspeech.pdf) 的VoxCeleb2、5994人、192维、标准3秒及大间隔微调6秒等属于另一个论文实验配置，不能移植到这个中文512维checkpoint。当前公开recipe已改为192维，也不能代替历史512维训练证明。

## 对独立校准与完全独立留出的约束

1. **本次已看过的LibriSpeech 24 queries及相关参考样本只保留为暴露后的诊断证据，不再用于选择门槛、候选前处理、校准器或验收条件，也不重新包装成留出集。** 用清单排除这些样本及其同源裁剪/转换版本；严格独立设计还应排除此前暴露的同一说话人和录音会话，检查书籍/章节或录制来源重合。
2. 新校准集和最终留出集应在看分数前确定，并按真实说话人和原始会话/录音分开。不能仅按trial、短片段、通道或文件名随机切分。3D-Speaker存在同一发声多设备同步记录，跨设备文件不自动意味着独立语音内容。
3. **区分两种独立性**：“未参与本项目校准/选择”可以通过新冻结清单证明；“绝未进入模型原始训练”目前因权重训练清单缺失不能保证。使用官方3D-Speaker测试划分，至多继承语料发布者对其公开训练划分不重叠的声明，不能证明未知checkpoint训练过程没有使用过它。
4. [LibriSpeech官方说明](https://www.openslr.org/12) 是16k英语有声书朗读。与该模型家族关联的普通话/方言、多设备语料条件并不相同。新LibriSpeech校准/留出最多支持这一英语朗读场景，不能单独认证中文自然对话、会议远场、噪声、重叠或部署域。16k一致不等于分布一致。
5. 目标部署域应另行明确语言、设备、距离、噪声/混响、有效说话时长及重叠条件，并据此预定分层校准和留出。短片段/长片段分别报告；不能因历史3秒训练程序就预设任意时长都适用同一门槛。最终留出只在门槛、前处理和验收指标冻结后启封；若据其结果调整，需新的最终留出。
6. 本报告不推荐门槛、不调整生产默认，也不宣称当前结果具有部署精度保证。原始训练来源不确定性可以作为设计限制明确保留，不应靠反复看同一小样本来“补证明”。

## 核查记录

沿用 `backend-frontend-contract-20261104/actual_model_metadata.json` 的本日实测模型摘要与元数据，以及已核验Git blob的17份官方源文件。新增两个历史数据准备文件保存在本目录的 `TRAINING_DISTRIBUTION_SOURCE_ADDITIONS.json`。仅进行只读文献/源码核查和报告写入，未下载任何新模型或音频，未运行推理、特征提取或门槛搜索。


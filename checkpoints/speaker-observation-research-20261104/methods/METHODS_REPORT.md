# 保守多观测说话人身份研究：复用质心，先修观测契约

研究日期：2026-11-04 UTC。结论仅用于下一轮离线研究；本轮没有下载模型、下载语料音频、运行编码器、改动生产代码、自动入库或合并身份，也没有 Git 写入。

## 1. 决策摘要

推荐新增**离线观测清单校验/分组适配器**，直接复用已有 `verified_identity.centroid_score`，不再写新聚合器。先固定每人 3 个有来源和完整声学支持的参考，比较同一查询上的单参考与三参考质心。暂不同时增加查询融合、自动更新、PLDA、质量加权或多模型融合，否则无法定位收益来源。

当前研究没有证明多参考能够解决跨天识别。现有独立 pilot 的 0.46 只得到 T 主查询 3/4、额外日期 1/8，T 总计 4/12；不得在已看过的 T 上继续选门限。原始 2 秒中央裁剪没有 VAD 或纯净性证据。新增参考也可能把错误放大。

最小下一轮分为两个**不同问题、不同结论**的域：

- 会议内域：AliMeeting 新会议中，人工/参考标注确认的同一人不同发言，检验干净观测和多参考的效果。只能声称同一会议内多观测。
- 跨天域：SMIIP-TV 全新人物、未用于前轮的录音日期和可靠语音段，检验跨天身份。不能把会议内改善当作跨天效果。

完整冻结协议见 `NEXT_EXPERIMENT.md`。两域均需新的 C/T；当前没有开展任何新真实音频实验。

实际实现边界补充：本轮另行实现的纯JSON planner仅覆盖域D的声明式跨session/earlier-date选择，既不读音频也不含向量或调用centroid。域M同meeting多turn输入不符合该契约，需另一个明确within-meeting契约，不能给turn伪造新session。向量维度/有限非零范数/质心检查属于已有scorer或未来执行器，不是当前planner能力。

跨天规模可行性修正：每split16 known+16 unknown、共64人的原设想尚不可执行。官方SMIIP-TV eval 58人扣去前轮已申请32人，至多剩26名未用候选；不能未经新来源、人物身份和模型训练分布核查直接借其余training people凑数。应先无分数地确认可用人物/日期/支持，再预先冻结较小探索协议或经核查的新来源。

## 2. 已有产品能力和不能复活的路径

已读当前本地源码：

- `verified_identity.py`：每人最多 8 个 reviewed positives；`centroid_score` 用 f64 `math.fsum` 均值，再调用 `support_vectors.normalized_vector` 归一化；`select_reviewed_score` 对单参考、多参考使用分别声明的门限，并保留 margin/unknown。因此算法核心已在。
- `experimental_identity_memory.py`：已有 `centroid`、`verified_multi_anchor`、`anchored_auto`，且含旧的 .55/.56/.65 等实验常量。这些不是当前模型/任务校准结果；本建议不复活它们，也不让自动观测训练后续观测。
- `primary_crop_support.py`：已有 32,000 帧裁剪及两侧各 1,600 帧侵蚀检查。它证明的是同一原始帧坐标上 native turn 元数据满足覆盖/不相交，明确返回 `acoustic_purity_certified=False`。不要把它包装成声学单人证书。

本地源码 SHA256 写在 `local_source_pins.json`；远端固定源和 Git blob SHA 见 `source_pin_manifest.json`。本报告不修改这些源。

## 3. 可复现开源实现的实际行为

### WeSpeaker：多入注册均值确有依据，但原样实现不是“单位向量均值”

固定 commit：`9fecd6cb4f47475d01761d87c826298dff4ef18c`（研究时 master）。

- [CN-Celeb 提取配方，L55–61](https://github.com/wenet-e2e/wespeaker/blob/9fecd6cb4f47475d01761d87c826298dff4ef18c/examples/cnceleb/v2/local/extract_cnc.sh#L55-L61) 根据 `enroll.map` 调用 `tools/vector_mean.py`。
- [vector_mean.py，L40–53](https://github.com/wenet-e2e/wespeaker/blob/9fecd6cb4f47475d01761d87c826298dff4ef18c/tools/vector_mean.py#L40-L53) 对传入向量直接做算术均值；这个函数内部**没有**逐向量 L2 归一化。不能把它引用成“先归一化再平均”的直接证据。
- [score.py](https://github.com/wenet-e2e/wespeaker/blob/9fecd6cb4f47475d01761d87c826298dff4ef18c/wespeaker/bin/score.py) 可减训练均值，再用 sklearn cosine 评分。训练域均值、LDA/PLDA、score normalization 不是可无成本移植的后处理，改变它们就改变分数分布。
- [CLI，L188–213](https://github.com/wenet-e2e/wespeaker/blob/9fecd6cb4f47475d01761d87c826298dff4ef18c/wespeaker/cli/speaker.py#L188-L213) 把 cosine 映射成 `(cos+1)/2`，`recognize` 只取最佳人，没有 open-set 拒识门限。变量名 confidence 不是概率。现有 raw-cosine .46 不能与该 CLI 的 .46 比较。
- CLI 可用 Silero VAD 去静音，但会拼接所有检出 speech，并不证明单人；会议切窗还会重叠。只能借鉴组件拆分，不能把 VAD 输出当作身份授权。

### SpeechBrain：可比较的 cosine 基线，不是现成多观测安全策略

固定 tag v1.0.3 → commit `31c1e329048c0380dc7f2acbe680c44a036b6286`。

[SpeakerRecognition.verify_batch，L60–110](https://github.com/speechbrain/speechbrain/blob/31c1e329048c0380dc7f2acbe680c44a036b6286/speechbrain/inference/speaker.py#L60-L110) 对两个输入分别 `encode_batch(..., normalize=False)`，使用 `torch.nn.CosineSimilarity`，示例默认阈值 .25。该接口不做多入注册聚合，不识别重叠，不提供跨会话置信校准。`.25` 是另一个模型/接口的默认值，不能移植至本产品 ERes2Net。

### pyannote：有分段、重叠排除与质心，但默认行为不是严格身份门禁

固定 tag 3.3.2（annotated tag object `3ec2a34e69ad53398e29ec8968f630cc3866dd18`）→ commit `240a7f3ef60bc613169df860b536b10e338dbf3c`。

- [speaker_diarization.py，L271–320](https://github.com/pyannote/pyannote-audio/blob/240a7f3ef60bc613169df860b536b10e338dbf3c/pyannote/audio/pipelines/speaker_diarization.py#L271-L320) 先把同时活动人数≥2 的帧屏蔽；但若清洁帧不足模型最少样本数，会回退至原始 mask，重新包含重叠。身份参考入库应改为“证据不足则拒绝”，而不是照抄这个 diarization 可用性回退。
- [clustering.py，L79–117](https://github.com/pyannote/pyannote-audio/blob/240a7f3ef60bc613169df860b536b10e338dbf3c/pyannote/audio/pipelines/clustering.py#L79-L117) 检查活动与非 NaN，并可随机下采样；并不验证所有非有限值、声学纯净、跨会话独立性或参考标签。
- [L133–200](https://github.com/pyannote/pyannote-audio/blob/240a7f3ef60bc613169df860b536b10e338dbf3c/pyannote/audio/pipelines/clustering.py#L133-L200) 质心是训练 embedding 的平均，然后用距离分配最近簇。这是会议 diarization clustering，不是带未知身份的永久实名识别。
- [L304–366](https://github.com/pyannote/pyannote-audio/blob/240a7f3ef60bc613169df860b536b10e338dbf3c/pyannote/audio/pipelines/clustering.py#L304-L366) 特定 linkage 会单位化向量；AgglomerativeClustering 的 `max_num_embeddings` 默认是无穷，即使基类默认 1000 也不能据此宣称整个流程有界。

这三项支持“分段、embedding、聚合、决策分离”的可复现设计，但没有任何一项证明只平均几次便能安全地连续识别人。

## 4. 聚合规则比较及数学边界

令单位向量参考为 r_i，单位查询为 q，m=mean(r_i)，R=||m||。

- 现有 normalized-centroid score：`q·(m/R)`。简单、状态有界，避免单个原始向量的大范数支配。应作为唯一最小候选。
- mean-score：`mean(q·r_i)=q·m=R×centroid_score`。因此**平均 cosine 分数与归一化均值向量的 cosine 不等价**；不一致参考使 R 变小，而质心归一化会消去这个长度信息。记录 R 和两两相似度仅作诊断，未经新 C 校准不能拿来临时调门禁。
- max-score / 多 anchor：能覆盖不同通道，但增加“碰巧命中某一个”的机会；参考越多，未知人最大分数通常越大。要按相同参考数、gallery size 校准。不要用不等数量参考做未经控制的比较。
- median / trimmed mean / quality-weighted：可能抵御少量坏点，也可能丢掉真实跨域参考；K=3/8 太小且质量代理未校准。不是最小首轮。
- 多查询平均：只有先知道属于同一人的观测才可合并。相邻切窗、同一发言不同麦克风、重复编码是相关测量，不是独立投票。首轮不做查询聚合，以免把分段串人和参考改善混在一起。

R 接近零必须拒绝，不能归一化出任意方向。R 较高也不证明标签正确：多个同一错误人的向量可以高度一致。源帧去重可阻止重复计数，但不能证明两个不同文件来自不同物理会话。

## 5. 污染、投毒和开放集最低防线

1. **不可自证入库。** 查询匹配成功不等于真实身份；automatic observation 与 reviewed enrollment 分库，未知者永不入实名参考。首轮完全禁自动更新。
2. **参考标签也是证据。** 必须保留谁/什么标注确认完整支持区间、来源版本、音频/裁剪/向量 hash、会话和物理时间轴；一句文本纠正不能证明 pooled embedding 全程同人。
3. **污染不靠均值治愈。** 一条错人参考会拉偏质心；同一污染音频切成三份会变成多数。合成向量回归测试应覆盖错参、重复输入、交换角色、异空间、零/NaN/Inf、参考撤销后结果过期。它们检验软件拒绝行为，不算真实声学鲁棒性实验。
4. **保留 unknown。** 绝对门限、第二名 margin、输入/参考不合格、模型失败/超时、来源失效都必须可拒绝；不能把失败转换成空 embedding 后继续最佳匹配。单人 gallery 的 margin=2 是哨兵值，不是额外证据。
5. **不自动 merge。** 匹配结果只是可撤销候选。参考删除/替换必须沿既有版本和撤销机制失效，不能保留陈旧质心或跨代复用。
6. **试验不夸大统计。** T 的主统计单位是独立人（会议域还需按会议聚类），不是 clips、pairs 或同声源麦克风数。未知误接受 0/n 不等于误接受率零。gallery 扩张、持续多次尝试会增加整场会议出错机会，应另报每人至少一次误接纳、每会议误归属和拒识覆盖率。

## 6. 分段与质量：严格门禁应怎样表述

VAD 仅估计 speech/non-speech；overlap detector 仅估计重叠；单 speaker diarization label 是算法结论。它们都不是纯净性真值。弱声、远场串音、同声复述、电视/扬声器、极短语气词及漏检重叠仍可能通过。

参考采集分层记证据：`native_metadata_eligible`、`reference_annotation_single_speaker`、`human_audited_full_support`。不要把三者压成一个 clean=true。会议首轮用原始全说话人标注扣除 overlap、两端侵蚀至少沿用现有 100ms、固定完整连续 2 秒，不用拼接跨 turn 的“2秒有效语音”。再人工复核裁剪及边缘支持，记录“有其他可听声/静音过多/截断/不确定”的拒绝理由；听审者看不到匹配分数。

先用可靠参考标注回答“给到较好观测，多参考有没有帮助”，再用同一冻结片段集评估实际 native/VAD/overlap 门禁的保留率、误放行和短语音漏失。后一结果才反映自动观测获取能力，前者不能被称为端到端改善。

长录音的识别窗不应成为 ASR 硬切句边界。保留输入物理帧/逻辑帧映射，ASR 可保留有限上下文并在重叠边界去重；说话人变化、双人重叠和不确定边界分别标注，不因上下文拼接把两个说话人的支持混为一条参考。本轮没有实现或验证这些流式行为。

## 7. CPU、内存和保留预算

下列是建议研究限制，绝非已测生产容量：

- embedding 512维 float64 每条4096 bytes。研究每域 C/T 合计≤64人、每人3参考时，参考向量上界0.75MiB；若沿产品上限4096 positives，则纯 f64 向量16MiB。Python/数据库/元数据/模型 RSS 另计，不能把向量大小当进程内存。
- 16kHz mono PCM16 每2秒64,000 bytes；3参考每人192,000 bytes。每域288裁剪仅约18.4MB裸PCM，但完整会议、立体声和中间缓冲远大于它。选择固定声道、记录转换，不把全会持续复制到每次尝试。
- 未来真实实验可选择manifest≤1MiB、单次元数据读取≤2MiB、source files≤128、每人固定3参考、查询批次有界等更小执行上限。这些是尚待source-specific冻结的建议，不是当前实现值；当前纯JSON planner硬上限为4MiB、4096 observations、256 declared people、2–8参考。报告容器还需另设CPU线程、RSS与墙钟上限；到限标unavailable并留下计数，不扩大上限后继续同一试验。
- 比较打分复杂度 O(PKD)，可缓存不可变 gallery 的质心后降为 O(PD)。不构建随长会线性增长后再做 O(N²) 全局距离矩阵。
- 现有70次调用共10.678秒 wall/15.691秒CPU，只说明那台机器和那批输入；不是流式、冷启动、Android或满负载吞吐保证。新增门禁、解码和分段会有独立成本，需报告p50/p95、峰值RSS、最慢样本、失败和队列延迟。
- 固定保留上限、到期/撤销传播和落盘原子性；音频与向量不是匿名数据。只保留可审计必要支持，不将音频、声纹向量、人口/健康元信息写入公开 Git。不要因“便于复现”永远保留所有失败尝试。

## 8. 新数据候选与可访问性实查

### AliMeeting：首选会议内域，跨会话身份仍未获证明

[OpenSLR119](https://www.openslr.org/119/) 列118.75小时、240会话、每会话2–4人、近场耳麦与8通道远场，许可CC BY-SA 4.0。适合检验多人会议、重叠和声道条件。

固定 [官方 baseline](https://github.com/yufan-aslp/AliMeeting/tree/692a034fd510f1720f547c2c91e0fbadc9c24bf2) 没有 WAV/FLAC/TextGrid 小样本，只有评分 RTTM fixtures。其 [RTTM converter](https://github.com/yufan-aslp/AliMeeting/blob/692a034fd510f1720f547c2c91e0fbadc9c24bf2/speaker/local/make_textgrid_rttm.py) 每个输入重新编号；编号相等不代表跨会同人。

[Lhotse v1.25.0](https://github.com/lhotse-speech/lhotse/blob/18436e9a3461d611a0b738a659fa9dbe81d0ffa5/lhotse/recipes/ali_meeting.py#L136-L184) 会从原 near 文件名或 far TextGrid 解析 SPKxxxx，是后续跨会身份清单核查的入口；目前没有作者全局身份保证、重复人数或跨天日期证据，不能直接用于跨天结论。

官方只列整包下载。对 Eval 地址两次 HEAD（正文0 bytes）均成功：3,673,718,355 bytes，`Accept-Ranges: bytes`，最后修改2022-02-14。记录见 `ali_eval_head.json`。这不提供 gzip 内部 WAV 的随机访问索引；没开始归档下载。旧 MiCAS 站404，见 `micas_metadata_probe.json`。检索到 HF 第三方独立 WAV 镜像，但未核对原包字节、标注或授权范围，不列为已验证官方小样本。

### AISHELL-4：可做会议分段对照，不能按本地 speaker ID跨会并人

[OpenSLR111](https://www.openslr.org/111/) 列211场4–8人会议和说话人活动标注。固定 [Lhotse源码，L22–26](https://github.com/lhotse-speech/lhotse/blob/18436e9a3461d611a0b738a659fa9dbe81d0ffa5/lhotse/recipes/aishell4.py#L22-L26) 明确同一 local ID 可在不同会议代表不同人；其 global ID 只是 `(meeting_id,local_id)` 唯一键，不是真实跨会身份映射。OpenSLR 标CC BY-SA4.0，搜索索引中的作者站却出现CC BY-NC-SA4.0，本次作者页正文未展开；商业使用前需按实际发布包条款澄清，不替用户作法律判断。

### 3D-Speaker / CN-Celeb：有用的域差数据，但不是独立跨天会议替代

[3D-Speaker作者站](https://3dspeaker.github.io/) 提供多设备/距离/方言标签、约1.1G test以及小型trial/utterance metadata链接；FAQ明确多个设备**同时**录同一发言。必须按原发声事件分组，否则把同步声道当独立观测造成泄漏；该来源没有证明多天会话。站点许可语句明确指 metadata，音频使用需核对实际发布条款。

[CN-Celeb作者站](https://www.cnceleb.org/dataset) 为多风格中文身份数据，非会议，限制非商业研究；它也常作为 ERes2Net/WeSpeaker 训练来源。不能仅因新下载就称 model-training-independent。3D-Speaker仓库也同时发布多种200k及公开数据训练模型，不能把论文族/模型名代替当前ONNX权重训练清单。

### SMIIP-TV：目前最明确的跨天时间轴，但必须新冻结人物/日期

[OpenSLR156](https://www.openslr.org/156/) 直接描述373人、90天、每天多时段。它不是会议，也不是保证无污染的参考。可排除前轮全部接触/申请人物及文件后新选 cohort，但训练集独立、物理人物全局去重仍未知。当前49G归档的有界取得方式是实际成本阻塞，不应把数据不齐改成事后有利子集。

## 9. 精确状态与阻塞

完成：13个固定版本源码快照、方法比较、本地既有能力核查、官方数据页与HEAD/元数据访问核查、下一轮双域冻结草案。无新真实实验。

未完成/非拒绝：AliMeeting 官方独立 WAV/小样本入口未找到；官方最小 Eval 包约3.42GiB，不在此次范围内。MiCAS 是404，不是已下载成功。跨会全局人物映射、模型训练成员清单、全新跨天人物的足量干净支持均未核实。应先解决这些输入条件，再做正式冻结；不使用当前见过的 T 调参。

当前planner接受的是外部声明，不重算音频/裁剪hash、不核实物理录音别名、不证明语音或身份纯净。64名新人的提案超过现有官方eval剩余候选上限；域M对应契约也尚未实现。这些是实际阻塞，不应在报告中写成已完成的声学能力。

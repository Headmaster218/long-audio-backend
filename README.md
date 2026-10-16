# 长录音音频分析后端

本地单用户音频 API，提供增量转写、独立说话人活动、声音事件、来源回执和断点恢复。推理使用本地模型，不调用外部转写服务。

## 0.2.0rc3.dev14：sealed-page 与已定稿文件上传

这是 dev14 开发源码候选。它纳入按页发布的录音 capture 与显式启用的单个已定稿文件 uploader；依赖、模型选择和可选能力默认值不变。

- [dev14 中文操作指南与验证边界](docs/DEV14_SEALED_HOUR_GUIDE_ZH.md)
- [单文件 uploader 的参数、恢复与资源契约](docs/HOUR_FILE_UPLOADER.md)

客户端以一次连续规范化解码生成有界 PCM16 WAV 来源，逐页提交；只在确认真实 EOF、完整 PCM 帧、干净解码退出及输入未变后发布 EOS。输入和恢复 journal 必须保留不变。上传成功表示全部规范化 PCM 已被接收且 EOS 已发布或排队，分析仍可能尚未完成。非定稿、多文件续传和自动重叠对齐不在本次范围。

既有功能候选分别通过 33 个 uploader 用例、45 个 page/hour 回归；sealed-page 基线另有 219 个测试与 9 个 subtests 的独立记录。这些是不同运行，不能相加为一次 dev14 产品验收。逻辑一小时用例使用合成 PCM 与假 analyzer，验证有界上传、帧守恒、背压、恢复及 EOS，不代表一小时真实模型推理或准确率。本 dev14 产品树的选定组合回归已在一次运行中通过 252 个测试与 9 个 subtests；标准离线 wheel 构建、全新 `--target` 安装及 115 个包文件的源码/wheel/安装字节核对通过。默认关闭的导入与应用构造、服务端及上传器 CLI 帮助、安装包的单帧 ASGI API 用例也已通过。安装检查复用既有合资格依赖，不是新的依赖齐备环境；安装 API 用例使用假 analyzer fixture，不涉及真实模型推理。随后仅更新本说明与中文指南的结果文字，包文件、测试和构建配置保持已检验字节。

## 保留的 dev13 及更早历史

以下历史章节完整保留；“本版”“本轮”和既有构建、安装、模型检查均指对应旧版本，不替代 dev14 验收。当前范围以上方 dev14 说明和中文指南为准。

## 0.2.0rc3.dev13：默认关闭的 FrameMN 时间候选

这是 dev13 开发源码检查点，基于已发布 dev12 源码 `76175dfce9475fe1d04799b6cf2d7aeea2045de7`；未创建 release/tag。

新增显式选择的本地 FrameMN 旁路，仅向现有候选账本写入未验证的时间证据。默认关闭，不替换 Zipformer，不改变主事件、ASR 非语音门控或转写结果；没有新增 API 端点或数据库表。

- [dev13 源码安装、默认启动与本地 checkpoint 选择](docs/INSTALL_DEV13_ZH.md)
- [dev13 功能检查与验证边界](docs/DEV13_FUNCTIONAL_CHECK_ZH.md)
- [FrameMN 参数、候选字段与故障契约](docs/FRAMEMN_EXPERIMENTAL_CANDIDATES.md)

固定策略为 `knock-001-median9-experimental-v1`：447 类标签中的 Knock（索引 228），九帧中值滤波后严格 `score > 0.01`。阈值来自小规模五标签并集实验，单独用于 Knock 仍属描述性实验，不代表独立验证的准确率或生产能力。40 ms 是输出分数网格，不是起止精度或端到端延迟。

模型按 10 秒 tile 顺序运行，单次 analyzer 输入最多 60.01 秒。中值滤波跨内部 tile 接缝，只在实际 analyzer 两端反射；模型上下文仍按 tile 独立。结果取决于 analyzer 的输入与边界，不保证任意重分块结果一致。原始帧支持与 core 实际拥有的样本支持分开保留，读取方式见候选字段说明。

基础及 inference 依赖未改变。可选 Torch 系列运行时与 checkpoint 由部署者事先准备；不自动安装、下载或启用。缺少依赖、checkpoint 不符或候选验证失败会显式记录可选旁路失败，保留主路径；静音输入不运行 FrameMN。源码保留上游 MIT 通知和适配记录，但 checkpoint 专项再分发权尚未明确，包中不含权重。

本轮选定源码回归在一次完整运行中通过 224 个测试与 166 个 subtests；另一次独立选定复跑通过 153 个测试，两者不累计。已有分数的解码一致性检查及一次 30 秒真实模型/源码 API 功能检查已完成：主任务 done，五次 FrameMN 调用、五条时间候选、13 条原有候选、零失败窗口，HTTP、变化流与只读数据库结果一致，独立复核确认结果、存储一致性和进程退出。

随后已从精确发布源码完成标准离线 wheel 构建及全新 `--target` 安装，114 个包文件与源码、wheel、安装结果逐字节一致，默认关闭时的安装包导入和应用构造通过。安装检查复用了既有合资格依赖，不是全新依赖齐备虚拟环境验收。另一次安装包的 30 秒真实模型/API 检查已完成，五次 FrameMN 调用、五条时间候选、13 条原有候选及两条原有事件均与先前源码运行对应结果一致（仅 chunk UUID 不同）；独立复核确认 HTTP、变化流、不可变只读数据库一致及进程退出。两次真实运行分别记录，仍不构成新的质量、内存资格或长时运行结论，详见 [dev13 功能检查](docs/DEV13_FUNCTIONAL_CHECK_ZH.md)。

## 保留的 dev12 及更早历史

以下文字完整保留对应旧版本的功能与验证记录；其中“本产品树”“本版”等均指各自历史版本。旧版本构建、安装和模型测试不替代 dev13 验收。dev13 当前状态以上方说明和两篇 dev13 文档为准。

## 0.2.0rc3.dev12：严格绑定的 reset-replay core 诊断

沿用默认关闭的 `--native-owned-cells --native-owned-core-seams`，在同 epoch 诊断之外增加独立 reset-replay schema/policy。它只接受同一 fresh capture、连续观察到的 reset/epoch，以及边界两侧各 10 秒的精确来源绑定；不增加主音频、不改写主转写、不自动触发 MOSS。启用时请主动选择全新可选 store，最直接是新建独立数据目录，同时保留旧数据与全部旧 cells；不自动采用、覆盖或回填旧 store。

- [dev12 中文发布说明与验证边界](docs/DEV12_RELEASE_NOTES_ZH.md)
- [dev12 启用、API、迁移与重试说明](docs/DEV12_RESET_REPLAY_API_ZH.md)

dev12 产品树的选定 authority/store/API/multicore 回归已单次通过 184 个测试及 103 个 subtests。标准离线 wheel 构建、隔离 target 安装、合资格环境中的 target import/CLI 检查通过，103 个包文件与源码、wheel、target 逐字节一致；未对安装后的包重跑 HTTP 或模型。之后仅刷新本说明和两篇新文档，包文件、测试与 pyproject 保持原字节。4 个既有 multicore fixtures 的 11 个文件仍保留，不声明全部删除。

独立功能候选另有分开串行运行的 52 个模型无关用例覆盖：35 个既有、15 个负例、逻辑 300/3,600 秒各 1 例；一次 fixture KeyError 修正前的失败保留。该证据与 184 个产品回归分别计数，不是一次 236-case 通过。一小时用例是合成 native/ASR 的 122 次提交、13 个 epoch，不是一小时真实连续运行。

另一次固定 344.5 秒 fixture（合成静音加官方 AMI 音频）的真实模型/API 案例，经独立补充审计确认 [290,310) support、[270,300)/[300,327) 主 core、未结束的右查询、一个绑定 MOSS 诊断及清理/最终 idle。原 helper 仍为 exit 1、success=false：结束后沿用的 16 个 observer 文件上限断言失败，实际 35 个完整文件已另行审计。不能将补充接受写成原 helper 干净通过；也不构成准确率、全局身份、通用 EOS、全内容或长时可靠性结论。

## 保留的 dev11 历史背景

以下 dev11 与 dev10 文字仅记录对应旧版本；其中“本产品树”等表述均指该历史版本。dev12 的功能、迁移与验收状态以上方新说明为准，历史通过不能替代 dev12 验收。

## 0.2.0rc3.dev11：同一 epoch 的相邻 core 诊断

本开发源码增加默认关闭的 `--native-owned-core-seams`，须同时开启 `--native-owned-cells` 并使用新策略的可选 store。它为同一 capture run、reset 与 native epoch 内的相邻已提交 core 提供精确来源绑定的替代诊断 cell；不增加主音频、不改写主转写、不自动触发 MOSS。跨 300 秒 reset 与一小时长录音验收仍未完成。

- [dev11 中文发布说明与验证边界](docs/DEV11_RELEASE_NOTES_ZH.md)
- [dev11 core-seam 启用、API 与故障判读](docs/DEV11_CORE_SEAMS_API_ZH.md)

本产品树的选定回归经两次独立运行取得 219 个测试和 103 个 subtests 的通过覆盖并集，包括全部 39 个新增/基线模型无关用例。首轮有两项既有 AdmissionTests 因临时目录位于非资格文件系统而失败；仅将这两项的临时目录改到已具资格的文件系统后重跑通过，未修改源码或资格 registry。这不是一次全部通过的运行。标准离线 wheel 构建、隔离 target 安装、import 与 CLI 检查已通过，103 个包文件与源码及 wheel 精确一致；未对已安装 wheel 重跑 API 或模型。

此前对应功能源码还完成一次真实 40.5 秒 AMI 单上传：30 秒内部边界保留 20–40 秒 support，并完成一个显式 MOSS 诊断及清理确认；右 core 为 EOF-final。该证据与本产品树回归、安装检查分别保留，不构成准确率、全局人物身份、通用 EOS 或长时可靠性结论。详细范围和历史诊断见 dev11 发布说明。

以下保留 dev10 的功能背景、安装入口与历史证据；dev11 的构建、安装与验证范围以上述结果及 dev11 发布说明为准，旧测试不能替代本版本的同范围验收。

## 保留的 dev10 功能：可选 capture 与 cell 列表修正

这是开发分支，未创建 release/tag。它沿用 dev9 的有限 recording batch：客户端上传共享采样坐标已知的原始 PCM16 WAV，再明确 finish；只有重叠样本逐字节一致、坐标完整且并集连续，才生成去重后的工作片段。原始文件与来源映射保留，未知位置、不同 PCM 或空隙仍使批次 unresolved。

dev10 增加三项有界修正：

- 可选 capture 先组合已严格验证的整数帧坐标，避免已知帧网格的浮点加法造成额外拒绝；主路径原有浮点参数不变，真实非整帧、精度丢失和来源不符仍拒绝。
- 预期无重叠的 A–B seam 保留已验证的 B 尾部，供后续 B–C 检查；不会生成虚假的 seam cell，格式、来源和权威校验失败仍使状态失效。
- 单次 cell 列表操作复用一个 PrimaryAuthority 只读连接，每次读取仍新建并结束事务；无快照或值缓存，保留 2 秒、150,000 SQL VM 步数预算及最终权威校验。

- [中文安装、启动、升级和回退](docs/INSTALL_DEV10_ZH.md)
- [中文 API 操作指南](docs/TESTED_API_GUIDE_ZH.md)
- [录音批次契约与资源边界](docs/RECORDING_OVERLAP_BATCHES.md)
- [dev10 开发分支说明与验证范围](docs/DEV10_RELEASE_NOTES_ZH.md)
- [既有普通上传、结果与控制 API](docs/API.md)

从完整源码安装后，通过 `python -m audio_backend` 启动。依赖不变，继续使用原内容的 `requirements-runtime-dev9.lock.txt` 与 `requirements-build-dev9.lock.txt`。模型、native runtime、运行环境和用户数据不随源码分发。

## dev10 历史验证范围

dev10 产品源码的 205 个既有 authority/store/API 选定用例与 46 个可移植新增用例分两次运行通过；新增用例为帧坐标 12 个、静音 seam 18 个、列表连接 16 个。移植中先后发现两项过时或不适用的测试假设：仍按 0.5 秒等待既有 2 秒 owner 截止，以及比较微型 fixture 的回调计数步数。修正测试后得到上述覆盖并集；两次失败记录保留，不是一次连续 251-case 全通过。此前独立候选还有帧坐标 13 个、静音 seam 20 个和连接复用 16 个专项通过；它们与产品回归不能重复累计为一次测试。连接专项在复制的四-cell 数据上将默认列表的计数步数从旧实现的 150,100 步中断降至 15,000 步；一次主库连接中完成 134 个独立读取事务，原数据库未改动。这是复制数据与机制测试，不是 HTTP 或新环境资格验证。

真实 70 秒中文、21 秒 AMI 的前后对照中主结果等价，帧坐标拒绝均为 0，未复现待修正的实际帧失败。另一次 31.5 秒对照的主结果等价，但两侧都选中了 seam，未覆盖预期无重叠分支；其默认 cell 列表在上述连接修正前均遇到 SQL 预算拒绝。这些源码回归不能替代真实模型、安装环境或长期运行验收。构建、安装/import 与 API 检查的最新结果及具体范围见[开发分支说明](docs/DEV10_RELEASE_NOTES_ZH.md)。

历史 dev9 短样本的成功和失败继续保留，详见开发分支说明。`last_capture_reason` 是历史诊断；`handoffs_active_or_pending` 表示当前活动或等待中的 owner 工作，归零不证明所有过去的 publication 成功。

## 使用边界

- 一个 capture 声明一个共享 PCM 采样坐标域，并独占一个全新空 `legacy` session。多个麦克风不能仅凭时间戳视为同一坐标域。
- 输入为单声道、16 kHz、16-bit little-endian PCM WAV。每批最多 16 个来源、32 个派生片段、300 秒唯一音频、32 MiB 录音复制预算；全局预算 128 MiB。更严格的服务配置和磁盘余量优先。
- 只支持全部输入齐备后的 finalized batch；不支持连续 sealed-prefix、任意/未知坐标对齐或近似 PCM 合并，也没有 12–48 小时全语音和设备录音可靠性结论。
- 普通 `/chunks` 仍拒绝时间区间重叠。MOSS 是显式、proof 绑定的独立诊断，不自动替换主转写，不保证文字准确率、重叠内容完整性或全局人物身份。
- native-owned cells、seam cells、core seams 和 MOSS jobs 默认关闭。显式开启仍须匹配已验证的 runtime、SQLite、VFS 与文件系统条件；相同依赖版本不等于可选能力具备资格，不能绕过检查。
- 主要为静音的 30 分钟主路径通过不代表全语音或可选链路可靠；此前计划 12 小时的运行在约 9 小时 20 分钟后丢失原运行句柄，结束和清理未获确认，不能记为 12 小时通过。

## 部署原则

固定依赖安装路径针对 Linux x86-64、CPython 3.12、CPU。Windows、GPU、其他设备与新环境长期运行未验证。无参数启动保留兼容默认；请使用安装指南中的显式 SenseVoice/Nemotron 参数。

一个数据目录只运行一个服务进程，不使用多 worker 或 reload。默认绑定 `127.0.0.1`；跨设备暴露前须自行配置访问控制和安全传输。`/health` 表示存活和能力状态，不证明模型内容正确。

升级前正常停服并完整备份。含 recording 数据的目录不能直接交给旧版本或仅改写 `chunks.path` 的旧迁移工具。模型和 native runtime 的独立许可证及第三方 notices 必须保留；下载、使用和分发前审阅安装指南中的上游条款。

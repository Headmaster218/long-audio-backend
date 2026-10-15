# 长录音音频分析后端

本地单用户音频 API，提供增量转写、独立说话人活动、声音事件、来源回执和断点恢复。推理使用本地模型，不调用外部转写服务。

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

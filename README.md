# 长录音音频分析后端

本地单用户 API：中文优先的中英转写、独立说话人活动、跨录音声纹关联、环境声音事件、增量上传与断点恢复。推理不调用外部 API，不上传录音，不做 LLM 总结。

**当前仍是开发中的候选后端，整体质量尚未达标。** 两套冻结基础流程均完成同一批 12.25 小时真实录音，详见完整长录音结果（历史材料，未随公共源码提供）；其中重叠文字候选关闭，不能算作新 MOSS-TD 路径的长时验证。入口请看[中文安装与启动](docs/QUICKSTART_ZH.md)和当前验收目标（历史材料，未随公共源码提供）。`reports/FINAL_REPORT.md` 是历史 v0.1 的阶段证据，不是当前验收报告。这是可迁移源码，不是永久在线的托管服务。

## 先分清当前候选与兼容默认

当前实测 CPU 候选组合需要**显式选择**：

- ASR：SenseVoiceSmall int8，CPU 计算线程参数为 2
- 本地说话人分离：Nemotron-3 Q8 / NeMo-Speech.cpp 0.2.0，CPU wrapper 上限参数为 2；上游 native 内部仍请求 4 线程，wrapper 使用亲和性和 OpenMP 约束。不能称为整个服务只有 2 个 OS 线程
- 跨录音身份：ERes2Net + 模型绑定的 `eres2net-ami20-v1` 策略，单参考阈值 0.55，多个显式验证参考的质心阈值 0.56
- 环境事件：Zipformer-small AudioSet，事件与低置信度候选分开保存
- 重叠语音候选：`none`。原始混音、检测到的同时发声活动始终保留；这不等于已经分开转写每个人

新增的 [MOSS-TD BF16 文字候选](docs/MOSS_TD_SETUP.md) 在小规模中英重叠测试中有明显词句收益，可另行显式启用；时间戳、少数发言和三人重叠仍有实测失败。它不把匿名候选自动当作全局身份。另有显式选择的 [CPP/GGUF 路径](docs/MOSS_TD_CPP_SETUP.md)，固定七片段上 Q8 与同引擎 BF16 原始输出一致，CPU 子进程树峰值约 1.445 GiB。实际 API 短测（历史材料，未随公共源码提供）的完整进程树峰值为 2.10 GiB，恢复与子模型故障隔离通过；这些结果不代表长时质量验收，Windows/Vulkan 尚未执行。

直接运行 `python -m audio_backend` 仍保留兼容默认：Whisper small Q5_1、pyannote、4 线程和 0.75 身份匹配阈值。它不会自动切到以上候选；0.75 的大量弃判也不能解读为高识别准确率。

## 最小启动路径

要求 Python 3.12+、FFmpeg/ffprobe；当前 native 安装器固定支持 Linux/Windows x86_64。以下 Linux CPU 路径不需要构建 whisper.cpp，也不需要 Torch。首次依赖/模型准备联网，后续启动与推理使用本地文件。

先审阅 SenseVoice 的[定制 FunASR 模型许可证](https://github.com/modelscope/FunASR/blob/main/MODEL_LICENSE)和 Nemotron 的 [OpenMDW-1.1](https://raw.githubusercontent.com/OpenMDW/OpenMDW/main/1.1/LICENSE.OpenMDW-1.1)，保留各模型、runtime 和第三方 notices。

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[inference,test]'
python scripts/setup_models.py --sensevoice
python scripts/setup_native_diarization.py
python scripts/setup_models.py --sensevoice --verify-only
python scripts/setup_native_diarization.py --verify-only
python scripts/run_cpu_candidate.py --dry-run
python scripts/run_cpu_candidate.py --data-dir ./audio-data-cpu-candidate
```

当前通用模型安装器还会准备兼容默认用的 Whisper/pyannote 文件，但以上候选运行不加载它们，也不需要 `--build`。启动器校验本地模型和 receipt 中的 runtime 文件，打印完整命令后调用现有 CLI；不下载、不覆盖配置、不改兼容默认。`--dry-run` 不加载模型、不监听端口、不创建数据库。模型缺失会明确失败。

服务默认地址 `http://127.0.0.1:8765`；`/docs` 为交互接口，`/openapi.json` 为 OpenAPI。一个数据目录只允许一个服务进程；不要启用多 workers 或自动 reload。`/health` 是存活与配置查询，**不是模型质量或推理就绪证明**。

Windows PowerShell、手动 CLI、可选重叠后端、迁移和排错步骤见 [QUICKSTART_ZH.md](docs/QUICKSTART_ZH.md)。Windows 执行、Vulkan 和 GPU 显存尚未验证。即使下载了 native Vulkan runtime，当前 Nemotron provider 仍明确请求 CPU（`gpu=-1`）；它和 Whisper.cpp 的独立 Vulkan 路径不是同一回事。

**移动已有数据目录时，先停旧服务、完整复制，再做离线路径迁移；不要直接启动副本。**
旧数据库保存绝对音频路径，直接启动旧版服务可能把副本音频当作孤立文件清理。
当前启动保护会在清理前拒绝外部或不一致路径；操作步骤见[数据迁移](docs/DATA_MIGRATION.md)。

## API 与客户端读取

1. `POST /sessions`，JSON：`{"name":"今天的录音","identity_group":"me"}`。
2. `PUT /sessions/{id}/chunks` 上传音频文件原始字节，设置唯一的 `Idempotency-Key`。支持本地音频/带音轨视频容器，不支持 URL 导入。
3. `GET /chunks/{id}` 读取处理状态与持久化进度。`202` 只说明可靠入队。
4. 分别读取 `/sessions/{id}/segments`、`speaker-turns`、`events`、`event-candidates`、`overlap-candidates`。它们各自有游标；原始说话人活动与候选不能从转写文本反推。
5. 需要显示纠正、删除及更新时，用 `/sessions/{id}/changes` 重放，按实体 ID 更新客户端数据。

默认上传彼此独立。同一 session 或相邻时间戳不会自动建立音频连续性。只有同一连续录音的真实相邻分片才设置 `stream_id`；末片设置 `stream_final=true`，或显式调用 finish。`waiting_input` 可能是在等待真实后续上下文。Native 稳定输出边界比普通 ASR 的 2 秒尾部更长，不能靠等待时间或服务重启代替结束声明。

[API 契约](docs/API.md) · [队列和独立候选记录](docs/QUEUING_AND_CANDIDATE_LEDGERS.md) · 边界拼接（历史材料，未随公共源码提供）

## 实际 ASR 输入覆盖账本（实验 CPU 路径）

显式启用 finalized PCM16 CPU 路径后，私有 evidence 记录每次 ASR 实际输入的整数帧范围、started 与 completed 回执，以及当前 core 内成功完成输入的覆盖并集和补集。详见 [ASR 输入覆盖语义](docs/ASR_INPUT_COVERAGE.md)。此账本只揭示漏送、失败或完成状态不明的范围，不修复识别准确率，也不会自动补词。未送入 ASR 不等于静音；完成回执也不证明文字正确。它不新增公共 API 字段或改变 canonical/fragment 身份隔离。升级后需重建显式 CPU manifest，以固定新增模块与实际 provider/meter 代码。

## 质量边界

- `eres2net-ami20-v1` 是可运行的可选策略，不是通用准确率保证。绑定精确 ERes 模型哈希/版本与 512 维输出；候选接近、短/混杂语音等不确定证据会弃判。普通自动匹配不更新声纹参考。原始跨日期/远场和强烈表演风格仍有失败，普通感冒未验证。见声纹策略与证据（历史材料，未随公共源码提供）。
- Nemotron 每个有界 epoch 最多 8 个本地槽位，包括回放上下文；跨 epoch 的全局身份由独立声纹证据关联。本地编号不是永久身份。短促发言、稀疏对话和重叠仍可能漏检或混淆。见[native 安装与限制](docs/NATIVE_DIARIZATION_SETUP.md)。
- SenseVoice 时间是 CTC token 起始锚点，结束时间为估计，不是经真值验证的逐词强制对齐。普通混音 ASR 可能只识别较响的声音。
- MossFormer2 与英语 SE-DiCoW 均需另行安装并显式选择。Moss 新鲜中文确认未达到可靠重叠转写目标；SE-DiCoW 仅路由有英语证据和足够预测干净上下文的输入，不能当作中文/混语方案。候选保持未验证、身份未解析，不自动进入声纹库。见重叠语音（历史材料，未随公共源码提供）和 [SE-DiCoW](docs/DICOW_SETUP.md)。
- 环境标签是未验证的模型/规则推断。`driving` 表示车辆/引擎相关声类，不能据此断言人在开车；`door` 不等于确定关门。分类窗口支持范围不是真实事件起止点，分数不是校准概率。

## 资源、隐私与部署边界

目标是整个服务的 RAM + 独立 VRAM 合计不超过 6 GiB，部署上限 10 GiB。当前 watchdog 只测服务进程树 RAM；CPU 实测不能证明 Windows/Vulkan 的合计资源结果。轮询无法阻止瞬时超限，严格限制需操作系统进程组/容器等支持，并给宿主系统留余量。

默认单次上传最多 8 GiB、时长最多 7 天，单个 session 可继续追加。队列按 work unit 计数：一个独立上传或一条未结束的显式连续流各算一个；每条流最多 128 个未完成 receipt。分窗、磁盘队列和分页保持有界内存，原音频、SQLite 与历史结果仍占用磁盘。默认磁盘保留量为 2 GiB；扩展评测应保留至少 5 GiB 余量。

保持 localhost。若自行对外绑定，现有 CLI 要求 `AUDIO_API_KEY`，另需 TLS、访问控制与网关限流。`identity_group` 是逻辑分组，不是多租户权限隔离。音频、转写和声纹资料都应妥善保护；应用删除不是磁盘取证意义的安全擦除，也不删除外部备份。

## 内部离线分片实验

`0.2.0rc2.dev3` 加入[默认关闭的离线分片 API](docs/OFFLINE_FRAGMENTS_INTERNAL.md)：精确 PCM 规划与注入假 provider 的逻辑运行时。它们未接入生产 worker、HTTP API 或真实模型；音频相同不能证明同源，也不能排除真实重放。

`0.2.0rc2.dev4` 在独立暂存分支加入[已审阅的离线扩展](docs/FRAGMENT_EXTENSIONS_STAGING.md)：真实 provider 桥接、独立的 finalized single-island PCM16 规范化实验，以及正事件和 native 边界基准。全部默认关闭；规范化尚未接入运行时、模型桥接或 HTTP API。基础矩阵的事件为空，仅支持负事件不变性；正事件结果限于选定的合成开发样本。native P2 保留原始报告超限失败，后续分片补跑单独授权和计量。这不是发布或生产验收。

`0.2.0rc2.dev5` 独立暂存候选增加默认关闭的内部规范化运行时适配器，以及独立的压缩源 receipt/rolling emitted-PCM reader 实验。规范化路径保留 native 来源坐标与 canonical 16 kHz 模型时间的区别；压缩 reader 尚未接入 SourcePlan、Alias、自动裁剪或任何运行时。AAC endpoint 仍未解决，rolling chunk 在本次 session 完成前仅为 provisional。没有 HTTP/upload/生产 worker 接线；这是源码审阅候选，不是已发布版本或长期吞吐验收。

## 测试、证据与交付

仅从公共 Git 检出复核源码，请先看[公共历史与可复现检查](docs/PUBLIC_REPRODUCIBILITY.md)。历史材料的文字引用不表示该材料已公开。

```sh
python -m pytest -q
python scripts/run_cpu_candidate.py --help
python scripts/run_cpu_candidate.py --dry-run
```

单元测试含明确的假分析器；通过只证明所覆盖的程序行为，不能证明模型质量。真实 CPU 集成证据包括 21 个跨日期片段的 API 检查（历史材料，未随公共源码提供）和 native 分离试验（历史材料，未随公共源码提供），各自保留小样本、训练集关系和失败限制。`reports/endurance_v02/` 保存独立冻结运行的配置、进度和资源记录；必须检查该运行终态和评分，不能把启动器参数、短测试或部分进度说成 12–48 小时验收通过。

源码包不含模型、虚拟环境、运行数据库或第三方录音。各 `*-manifest.json` 固定下载源、大小、SHA256 和许可证；新安装要显式准备资源。保留模型和 runtime notices。部分研究素材含非商业条款，不能随着商业产品无条件分发。

### 实验性 finalized PCM16 HTTP 接线

独立 feature 候选新增[已结束 PCM16 流的 HTTP 集成、离线 manifest 生成命令与 CPU 启动步骤](docs/FRAGMENT_HTTP_EXPERIMENT.md)。仅接受声明连续的原始 16 kHz 单声道 PCM16 WAV；零裁剪，公共行、游标和 EOS 同事务提交。默认关闭；一次 12 秒真实 CPU HTTP 检查已独立审阅，覆盖输入、幂等、结果提交与清理，不代表通用准确率或长时验收。原始测试器因将正常 SIGTERM 退出视为失败而保留失败终态，独立解释与修正测试另存。原生重叠说话轨道保留，全局身份学习和独立候选公开投影不可用。新的 `scripts/build_fragment_cpu_manifest.py` 只校验已有本地资源并生成哈希，不下载、安装或构造模型；不需要私有实验文件或先跑一次推理。

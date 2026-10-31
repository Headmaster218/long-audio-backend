# 中文快速启动：显式 CPU 候选

这份指南对应当前源码的可选 CPU 路径，不修改兼容默认，也不表示项目已经通过全部验收。真实长时运行、复杂中英语音、跨日期身份、声音事件与重叠转写仍需分别看完整证据。[目标与验收](GOALS_AND_ACCEPTANCE.md)是当前判定入口；历史 `reports/FINAL_REPORT.md` 只记录 v0.1 阶段。

**已有数据换目录/换电脑：先停止旧服务并复制完整数据，再按[离线迁移](DATA_MIGRATION.md)做 dry-run/apply，成功前不要启动副本。**
仅复制源码并从空数据目录开始不需要迁移。旧版绝对路径与启动时孤立文件清理存在组合风险；
当前版会在任何临时/孤立音频清理之前检查并拒绝不一致的保留路径。

## 1. 选择与前提

本指南显式选择：

| 部分 | 当前候选 | 无参数 CLI 的兼容默认 |
| --- | --- | --- |
| ASR | SenseVoiceSmall int8，CPU，`--threads 2` | Whisper small Q5_1，CPU，4 线程 |
| 本地说话人活动 | Nemotron-3 Q8，native CPU wrapper 参数 2 | pyannote |
| 跨录音身份 | 精确模型绑定 `eres2net-ami20-v1` | 未启用该 profile，匹配阈值 0.75 |
| 独立声纹模型 | ERes2Net，512 维 | 相同模型 |
| 环境事件 | Zipformer-small AudioSet | 相同模型 |
| 重叠单独转写候选 | `none`，必须另行选择实验后端 | `none` |

“CPU2”指计算/wrapper 参数。Native C ABI 内部仍请求 4 线程，wrapper 尝试用 2 个 CPU 的亲和性和 OpenMP 限制；进程树含监控和服务线程，总 OS 线程数会更多。是否成功限制亲和性记录在推理 provenance 中，Windows 尚未执行验证。

需要：

- Python 3.12 或更新版本；已测依赖记录在 `requirements-lock-linux-py312.txt`，它不是 Windows 通用锁文件
- `ffmpeg` 和 `ffprobe` 已在 PATH，可分别运行 `ffmpeg -version`、`ffprobe -version`
- Linux 或 Windows x86_64；native 固定安装包不覆盖 macOS/ARM
- 首次下载安装依赖/模型时可访问清单中的官方来源；推理没有外部 API 或自动模型下载
- 足够的模型、上传音频和 SQLite 磁盘空间。服务默认保留 2 GiB 空闲；长评测另留至少 5 GiB，不要将磁盘容量等同于 RAM 预算

本候选不需要 Torch、Whisper 编译器或 Vulkan SDK。通用模型安装器仍会下载兼容路径使用的 Whisper/pyannote 等文件，候选推理不会加载它们。

## 2. 先审阅模型许可证

- SenseVoice 权重是[定制 FunASR Model Open Source License Agreement v1.1](https://github.com/modelscope/FunASR/blob/main/MODEL_LICENSE)，不是模型仓库代码的 MIT 许可证。保留模型名称、来源/作者及要求的 notices，使用或分发前阅读全文。本地校验副本是 `models/sensevoice/UPSTREAM_MODEL_LICENSE`。
- Nemotron-3 模型是 [OpenMDW-1.1](https://raw.githubusercontent.com/OpenMDW/OpenMDW/main/1.1/LICENSE.OpenMDW-1.1)；本地副本位于 `licenses/native-diarization/OpenMDW-1.1.txt`。Native runtime 是 Apache-2.0，并附带第三方 notices，迁移/分发不要只复制一个动态库而丢掉其依赖和声明。
- 其他下载的确切版本、来源与许可见 `model-manifest.json`、`native-runtime-manifest.json`。实验重叠后端有各自清单和说明。
- 第三方测试录音的使用/分发权与模型许可证不同。部分研究素材限制商业使用，源码交付不包含这些录音或私人声纹向量。

## 3. Linux 安装、校验与启动

在项目根目录运行：

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

`setup_models.py --sensevoice` 是明确的可选模型下载；不加 `--build`，本路径不编译 whisper.cpp。`setup_native_diarization.py` 默认安装本机 CPU 包，固定模型及 runtime 来源、大小和 SHA256，并生成 `vendor/native-diarization/<平台>-x86_64-cpu/installation.json`。

`run_cpu_candidate.py` 的行为：

- 只读取本机 CPU receipt 和现有本地清单，校验候选所需模型/许可证的 SHA256/大小，并按 installer receipt 校验 native runtime 文件
- 根据当前项目路径和 receipt 中的相对 inventory 重新定位动态库，不盲用旧机器的绝对 `settings` 路径；拒绝平台/版本不符、目录逃逸和缺失/损坏文件
- 使用当前 Python 环境调用现有 `python -m audio_backend`，打印 `command` 参数数组；没有 shell 拼接或任意命令透传
- 固定 localhost、CPU2、SenseVoice、Nemotron、模型绑定声纹 profile、`language=auto`、30 秒核心窗口、`event_threshold=0.25`、`overlap_backend=none`
- 默认数据目录为项目下 `audio-data-cpu-candidate`；不会自动使用已有 `audio-data`。可用 `--data-dir` 显式选择已备份的目录
- `--model-dir` 指定已经准备好的模型树，目录结构须保持一致；`--port` 可调整监听端口；`--memory-limit-gib` 默认 6，可在 0.25–10 之间调整
- `--dry-run` 不创建目录/数据库，不加载推理模型，不绑定端口，也不联网；它仍会读取本地文件完成校验。它不等于一次真实推理测试

启动器不把依赖存在、系统动态库可加载、识别质量或长时间运行标记为已通过。首次小段真实音频仍需检查完整处理终态和输出。它也不复现冻结耐久测试的全部配置：例如耐久测试使用更小的 RAM guard、不同的磁盘余量和队列限制，以其 `effective-settings.json` 为准。

默认服务地址：

- `http://127.0.0.1:8765/health`
- `http://127.0.0.1:8765/docs`
- `http://127.0.0.1:8765/openapi.json`

启动器只选 localhost；不要同时运行多个服务、使用多个 Uvicorn workers 或自动 reload。每个数据目录有独占锁。设置了 `AUDIO_API_KEY` 后，所有路由包括 health/docs 均要求 `Authorization: Bearer ...`。API key 不会出现在启动器打印的参数中。

## 4. 不用启动器的等价关键参数

以下 Linux 路径对应当前固定 CPU 包。安装器打印的 receipt 中也包含模型/动态库位置；迁移后核对为本机路径，不能在 Windows 加载 Linux `.so`。

```sh
python -m audio_backend \
  --host 127.0.0.1 --port 8765 \
  --data-dir ./audio-data-cpu-candidate --model-dir ./models \
  --threads 2 --memory-limit-gib 6 --device cpu \
  --asr-backend sensevoice --diarization-backend nemotron \
  --native-diarization-model ./models/native-diarization/Nemotron-3-Diarization.q8_0.gguf \
  --native-diarization-library ./vendor/native-diarization/linux-x86_64-cpu/nemo-speech-0.2.0-linux-x86_64-cpu/lib/libnemo_speech_asr_c.so \
  --native-diarization-threads 2 \
  --speaker-calibration eres2net-ami20-v1 \
  --overlap-backend none --language auto --window-seconds 30 --event-threshold 0.25
```

不要把 `--speaker-calibration` 与手动 `--speaker-threshold`、`--new-speaker-threshold`、`--speaker-margin`、`--speaker-profile-strategy` 混用，CLI 会拒绝。这个 profile 的单参考阈值是 0.55，多个人工确认参考的质心阈值是 0.56，novelty 是 0.45，歧义间隔是 0.10，匹配/新增都要求至少 2 秒实际干净语音。它还检查 ERes 模型版本、SHA256 和维数，不能对任意声纹模型套用 0.55。

自动匹配不更新参考；不确定证据保持 unknown 或 provisional。`speaker_match_score` 是余弦相似度，不是“识别概率”。[声纹文档](SPEAKER_REGISTRY.md)保留原始跨日/远场低召回、风格变化错误、小样本限制以及人工纠正接口。

## 5. 第一次上传与独立结果

以下 curl 示例在另一个终端执行，将 `SESSION_ID`/`CHUNK_ID` 替换为服务器返回的 ID。Body 是原始文件字节，不是 multipart：

```sh
curl http://127.0.0.1:8765/health
curl -X POST http://127.0.0.1:8765/sessions \
  -H 'Content-Type: application/json' \
  -d '{"name":"first-check","identity_group":"my-test"}'
curl -X PUT http://127.0.0.1:8765/sessions/SESSION_ID/chunks \
  -H 'Content-Type: application/octet-stream' \
  -H 'Idempotency-Key: first-audio-001' --data-binary @recording.flac
curl http://127.0.0.1:8765/chunks/CHUNK_ID
curl 'http://127.0.0.1:8765/sessions/SESSION_ID/segments?after=0&limit=500'
curl 'http://127.0.0.1:8765/sessions/SESSION_ID/speaker-turns?after=0&limit=500'
curl 'http://127.0.0.1:8765/sessions/SESSION_ID/events?after=0&limit=500'
curl 'http://127.0.0.1:8765/sessions/SESSION_ID/event-candidates?after=0&limit=500'
curl 'http://127.0.0.1:8765/sessions/SESSION_ID/overlap-candidates?after=0&limit=500'
```

Windows 使用 `curl.exe`，注意 PowerShell 的 JSON 引号规则；也可在 `/docs` 查看请求 schema。若开启 API key，每条请求都需相同 Bearer header。

检查 `/health` 的 `asr_backend=sensevoice`、`diarization_backend=nemotron`、`inference_device=cpu`、`speaker_policy.calibration_profile_id=eres2net-ami20-v1`、`overlap_backend=none`。Health 只报告存活/配置，`inference_ready` 为 null。上传返回 `202` 也不表示转写完成；查询 chunk 直至完成或明确错误。

客户端必须分开读取：

- `segments`：普通混音转写。没有文本不代表没有说话；时间锚点不是逐词真值
- `speaker-turns`：独立 raw activity，保留检测到的同时发声。局部 ID 有 chunk/window/epoch 范围，不能当永久人名
- `events`：主事件预测；`event-candidates`：独立、持久的未确认声类证据。support 窗口不是真实事件 onset
- `overlap-candidates`：实验分轨/条件转写与未解析原因。关闭时不会自动运行分离；也不能根据候选槽位认定是谁
- `changes`：纠正、更新和删除的可重放记录。追加型结果游标不能检测旧行变更。按返回游标继续分页，并按实体 ID upsert

## 6. 连续分片、恢复与队列

不同文件默认独立。只有同一未中断波形的相邻片段才用同一 `stream_id`；另一天录音即使属于同一人也不应拼成连续流。

```sh
curl -X PUT 'http://127.0.0.1:8765/sessions/SESSION_ID/chunks?stream_id=meeting-01' \
  -H 'Idempotency-Key: meeting-01-part-1' --data-binary @part1.flac
curl -X PUT 'http://127.0.0.1:8765/sessions/SESSION_ID/chunks?stream_id=meeting-01&stream_final=true' \
  -H 'Idempotency-Key: meeting-01-part-2' --data-binary @part2.flac
# 或者：最后一片已经作为非末片上传后，明确结束
curl -X POST http://127.0.0.1:8765/sessions/SESSION_ID/streams/meeting-01/finish
```

Native 使用 21.12 秒批处理和额外稳定边界保护，可能需要比普通 ASR 的 2 秒上下文更多的后续输入。`waiting_input` 是可恢复状态；必须上传真实相邻后续片段或结束流。不要通过重启、空音频或另一天录音“补齐”。完整来源结束才调用 native finish；上传边界不是来源结束。

每个窗口的结果与 checkpoint 事务提交。重启会重做尚未提交的有界窗口；它不是逐采样无损保证，词边界仍可能重复/丢失。默认队列是 64 个 work units，一条连续流算一个，最多 128 个未完成 receipts；非最终片段至少 0.5 秒真实音频。429、失败、暂停和等待原因见[队列说明](QUEUING_AND_CANDIDATE_LEDGERS.md)。Native 每个 epoch 最多 8 个本地说话人槽位；更多人或短促/稀疏发声仍有容量与质量风险。

## 7. 重叠实验要单独启用

基础启动器默认 `none`，不会自动安装 Torch 或加载重叠模型。新的 [MOSS-TD BF16](MOSS_TD_SETUP.md) 及 [CPP/GGUF 低内存候选](MOSS_TD_CPP_SETUP.md) 可通过启动器的 `--enable-moss-td` 与显式隔离解释器/模型路径启用；原始粗时间与匿名身份仍未验证。其他实验后端先完成对应独立安装，再使用第 4 节 CLI **替换** `--overlap-backend none`，不要同时启用多套后端。

MossFormer2：按[重叠安装说明](OVERLAPPING_SPEECH.md)准备独立环境和模型，再加：

```text
--overlap-backend mossformer2_experimental
--overlap-python /absolute/path/to/.venv-overlap/bin/python
--overlap-window-mode official_2s
```

这是两源 CPU 分离候选。新鲜中文确认未通过可靠重叠转写质量目标；少数声音漏识别、分段碎片、泄漏和跨窗口槽位不稳定仍存在。超容量或 support 含多于两人时保留 unresolved 原因；两条神经输出本身不证明有两个人。

英语 SE-DiCoW：按[独立安装说明](DICOW_SETUP.md)准备 BF16 目录与环境，再加：

```text
--overlap-backend dicow_en_experimental
--dicow-python /absolute/path/to/.venv-dicow/bin/python
--dicow-model-dir /absolute/path/to/models/dicow-bf16
```

它在原始混音上做目标条件 ASR，仅允许符合门控的英语、足够的预测干净说话人证据和实际可访问上下文；中文、混语、未知语言、证据不足会拒绝路由或保持未解析。不要靠强制 `--language en` 绕过真实非英语证据。输出是 native phrase，不能伪造逐词时间。Windows 上解释器用环境的 `Scripts/python.exe`；这只是路径说明，尚无 Windows 运行验证。

两条路径都保留原始混音、普通转写和 raw turns，候选 `speaker_id` 保持 null、质量未验证、不自动登记/更新声纹。单独的 conditioning 身份提示不是候选身份已确认。它们可能显著增加 CPU 时间；短样本 RAM 低于预算不代表长期实时性或全部质量达标。

## 8. Windows 迁移与 Vulkan 边界

以下 PowerShell 是待目标机器实测的部署步骤，不是已通过声明。先安装 Python 3.12+ 和官方 FFmpeg，将 FFmpeg/ffprobe 加入 PATH，然后在项目根目录运行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[inference,test]"
.\.venv\Scripts\python.exe scripts\setup_models.py --sensevoice
.\.venv\Scripts\python.exe scripts\setup_native_diarization.py
.\.venv\Scripts\python.exe scripts\setup_models.py --sensevoice --verify-only
.\.venv\Scripts\python.exe scripts\setup_native_diarization.py --verify-only
.\.venv\Scripts\python.exe scripts\run_cpu_candidate.py --dry-run
.\.venv\Scripts\python.exe scripts\run_cpu_candidate.py --data-dir .\audio-data-cpu-candidate
```

可以在准备机显式运行 `setup_native_diarization.py --platform windows-x86_64` 下载/校验 Windows 包，但不能在 Linux 加载它。正式目标上重新运行安装/校验以生成本机 receipt；启动器会自动选当前主机的 CPU 包。不要复制 Linux 虚拟环境或把 `.so` 当 `.dll`。移动已有数据前停止旧服务并备份整个数据目录，包含 SQLite 及仍存在的 WAL/SHM、原音频；不要复制正在写入的零散文件。

目前 Windows archive 下载、哈希和安全解包在 Linux 上验证过，Windows 实际加载/推理/进程清理没有验证。部分 Windows Python 3.12 wheel 的存在记录也不是整套 Windows 安装通过的证据。

Vulkan 是独立的未来硬件验证项：

- SenseVoice 当前 adapter 只支持 CPU
- Native 安装器虽然能准备上游 Vulkan archive，当前 provider 仍是 `gpu=-1`，不会因此启用 GPU
- Whisper.cpp 有另外的 `setup_models.py --build --vulkan` 和 `--device vulkan` 路径，需要兼容编译器、CMake、Vulkan SDK/驱动及 Whisper 模型；它会改变 ASR 路径，不能替代本候选的 CPU 证据
- Moss/SE-DiCoW 本项目路径是 CPU；没有已经验证的 Vulkan 分离/条件 ASR
- 必须在目标机器测整个服务进程树 RAM 与独立 VRAM 合计。目标 ≤6 GiB、部署上限 ≤10 GiB；现有 RAM watchdog 不计显存，也不是瞬时硬限制

## 9. 检查、排错与证据

```sh
python -m pytest -q
python scripts/run_cpu_candidate.py --help
python scripts/run_cpu_candidate.py --dry-run
```

- 缺 receipt/模型：运行明确的 setup，之后 verify-only；启动器不会偷偷联网补齐
- 哈希/版本错误：保留错误并显式重新校验安装，不跳过验证或借用另一个模型的 profile
- 本机 DLL/so 加载错误：检查平台、动态库依赖和完整 runtime 目录。receipt 校验不是系统 ABI 执行测试
- `waiting_input`：先查显式 stream 是否确实结束，按第 6 节 finalize
- 端口被占用：用 `--port` 选择空闲端口；不要让两实例写同一个数据目录
- `unknown`/低分：保留不确定状态，查看干净证据时长和模型 provenance，必要时用明确人工 API 纠正。不能仅为提高“有名输出率”无限降低阈值
- 资源超限：保留已提交进度并查看 chunk 错误，处理宿主可用内存/磁盘不足；不要为了继续执行自动超过 10 GiB 上限

单元测试可含假分析器；帮助和 dry-run 也不运行模型。这些检查只证明各自覆盖的行为。可追溯的真实证据入口：

- [固定 21 片段跨日期 API 检查](../reports/crossday-api-native-calibrated-21-v1/REPORT.md)：SenseVoice/native/profile 真实组合，复用过的小样本，不能称为新鲜人群泛化或长录音验收
- [native 分离研究](../reports/nemotron-trial/README.md)：包括 sparse-case 失败、训练/测试分区限制和单 provider 与整流水线的区别
- [声纹策略](SPEAKER_REGISTRY.md)、[边界拼接](BOUNDARY_ASSEMBLY.md)、[重叠结果](OVERLAPPING_SPEECH.md)、[SE-DiCoW](DICOW_SETUP.md)
- `reports/endurance_v02/`：各冻结运行的输入清单、有效配置、进度、重启与资源记录。完成前不能把部分已处理小时或进程存活写成完整 12–48 小时通过，冻结运行也不会自动包含之后的源码修正

不运行无关重型评测也能检查安装流程；要评估质量，请使用有明确来源、分母、真值和失败记录的独立实际音频测试。静音/重复短片只可检验相应稳定性，不能替代真实长对话质量。

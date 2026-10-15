# 本地音频 API：已测路径与验收边界

更新：2026-10-15。本文提供普通 localhost 服务的可移植使用路径，保留已测相邻上传的历史边界，不构成全面验收或发布声明。

本检查点另有 finalized recording batch 主路径/精确去重里程碑：20 个新用例和 12 个原有用例通过；3 个已知共享采样坐标的原始录音共 27 秒，形成 21 秒的精确 PCM 并集、2 个主工作片段和 7 条来源映射，原始文件完整保留。该次 MOSS 未运行，第二个 derived source 的 native receipt 返回 busy，optional dropped=1，只有首个 cell。以下历史 AMI MOSS 成功属于另一相邻上传用例，不可移用于此批次。详见 ../README.md 和 RECORDING_OVERLAP_BATCHES.md。

若要使用新增 recording 路由，须按检查点 README 组装完整候选包，并按 helper/README.md 的入口绑定启动；仅保存本文件不会改变已安装的 audio_backend。下面启动示例针对已正确安装的普通服务。

## 1. 当前已证实什么

- **真实 AMI 自然重叠片段，21 秒**：两个相邻的 10.5 秒上传完成，主转写产生 16 个 segments；自然形成的保留 seam cell 经显式请求，返回 **1 个 MOSS `returned_diagnostic` 结果**，该任务进程清理已验证。它证明这一短样本的主路径和诊断调用可完成，不证明重叠词句正确或完整。
- **真实 FunASR 片段，70.470625 秒**：7 个相邻上传（前 6 片各 10.5 秒，末片 7.470625 秒）完成；回执、提交进度、SenseVoice 调用及 native frontier 检查通过，产生 **36 个 segments**。该次没有 MOSS 请求，没有 optional warning 或 publication pending，保留 cell 数为 0。零 cell 不能证明原音频没有重叠。
- **项目原有 pytest 用例 12/12 通过**：completion 与 JUnit 一致，失败、错误、跳过均为 0。覆盖上传重试和 finish 幂等、change feed 重开、checkpoint 原子提交/重启、失败后游标、时间区间与重叠拒绝、等待不阻塞其他任务、finalize 竞争、作用域内 cancel、unlink 后实际进程退出的删除恢复、主处理不变及提交后 cell 发布、seam job 幂等、开关/重启后的 cell 恢复与旧 job 隔离。模型 provider 为假实现；这是服务机制测试。

**普通 chunks 路由仍拒绝区间重叠。** 新增 recordings 路由仅在显式共享采样坐标、精确 PCM 相等和 finalized batch 条件下形成去重工作。相同 key/字节的上传重试、不同来源录音的样本去重、同一波形内多人同时说话仍是不同问题；不能从有限批次成功推导出未知坐标对齐或任意多人语音恢复。

历史上出现过一次 optional publication warning。后续无模型的静态重放和有限并发重现未捕获原异常，不能据此认定已修复。本批次依赖 **v1 诊断**；v2 owner 诊断的隔离无模型测试属于另一检查点，不在这里的三个依赖覆盖中。诊断记录增强不等于行为修复。

## 2. 普通 localhost 启动

前提：已准备当前版本的 Python 3.12 runtime、推理依赖、固定模型及完整 native runtime；FFmpeg/ffprobe 在 PATH。先按项目 `docs/QUICKSTART_ZH.md` 校验本机模型、runtime receipt 和许可证。以下路径变量由部署者设置：

- `$RUNTIME`：安装了 `audio_backend` 的 Linux Python 环境
- `$ASSETS`：包含 `models/` 与 `vendor/` 的资源根目录
- `$DATA`：本次服务的持久数据目录；已有数据迁移前先停服务并完整备份

下面复用两次真实运行的关键服务参数。它是普通 `legacy` 服务，不是 finalized-only fragment 实验入口；普通 CPU 启动脚本不完整暴露这些 cell/job 参数。

```sh
: "${RUNTIME:?设置 Python runtime 根目录}"
: "${ASSETS:?设置已校验资源根目录}"
: "${DATA:?设置数据目录}"
export OMP_NUM_THREADS=2 OMP_THREAD_LIMIT=2
export OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2

"$RUNTIME/bin/python3.12" -I -B -m audio_backend \
  --host 127.0.0.1 --port 8765 \
  --data-dir "$DATA" --model-dir "$ASSETS/models" \
  --threads 2 --memory-limit-gib 6 --device cpu \
  --asr-backend sensevoice --diarization-backend nemotron \
  --native-diarization-model "$ASSETS/models/native-diarization/Nemotron-3-Diarization.q8_0.gguf" \
  --native-diarization-library "$ASSETS/vendor/native-diarization/linux-x86_64-cpu/nemo-speech-0.2.0-linux-x86_64-cpu/lib/libnemo_speech_asr_c.so" \
  --native-diarization-threads 2 \
  --speaker-calibration eres2net-ami20-v1 \
  --overlap-backend none --language auto \
  --window-seconds 30 --event-threshold 0.25 \
  --native-owned-support --native-owned-cells --native-owned-seam-cells \
  --moss-jobs --moss-jobs-model "$ASSETS/models/MOSS-Transcribe-Diarize-Q8_0.gguf"
```

- 只运行一个服务进程，不启用多 workers 或 reload；一个数据目录只能有一个实例。
- `--native-owned-support` 开启实验 support 能力，是此 MOSS job 配置的前提；`--native-owned-cells` 开启默认关闭的被动持久 cell；`--native-owned-seam-cells` 增加两个已提交 core 之间的 seam 替代覆盖，依赖 cells。cell/SQLite/VFS 的跨环境适用性没有全面验证。
- `--moss-jobs` 只开放显式任务服务，不会自动发起推理。此路径要求 Nemotron、CPU、`overlap-backend=none` 和普通 worker；不要混入自动 overlap backend 或 fragment provider。
- `--threads 2` 及 native wrapper 参数不是总 OS 线程数承诺；`--memory-limit-gib 6` 是采样防护，不是瞬时硬上限。
- `/health` 的存活、文件可读及 `ready` 字段不能证明模型识别正确。若设置了 `AUDIO_API_KEY`，包括 health 在内的所有请求都需加 `Authorization: Bearer ...`，以下示例省略该 header。

## 3. 两片相邻上传、明确结束、读取主结果

在另一终端设置 `API=http://127.0.0.1:8765`。所有 ID 都取本次服务器响应；下列名称是客户端示例，不是现有运行 ID。

```sh
API=http://127.0.0.1:8765
curl "$API/health"
curl -X POST "$API/sessions" \
  -H 'Content-Type: application/json' \
  -d '{"name":"local-check","identity_group":"local-check","processing_mode":"legacy"}'
```

保存返回的 `id` 为 `$SID`。准备来自**同一未中断波形**的原始相邻 PCM16 WAV：`$ASSETS/input/part0.wav` 为前 168000 帧，`part1.wav` 为后 168000 帧；均为单声道 16 kHz，各 10.5 秒。不要重复上下文、补静音或将不同录音拼成 stream。

```sh
curl -X PUT "$API/sessions/$SID/chunks?timeline_start=0&stream_id=continuous-demo" \
  -H 'Content-Type: application/octet-stream' \
  -H 'Idempotency-Key: part_0' \
  --data-binary "@$ASSETS/input/part0.wav"

curl -X PUT "$API/sessions/$SID/chunks?timeline_start=10.5&stream_id=continuous-demo&stream_final=true" \
  -H 'Content-Type: application/octet-stream' \
  -H 'Idempotency-Key: part_1' \
  --data-binary "@$ASSETS/input/part1.wav"
```

Body 是文件原始字节，不是 multipart。保存两次响应的 `chunk.id` 为 `$CHUNK_0`、`$CHUNK_1`。先按顺序提交真实后续片段，再等待全部处理完成；`202` 仅表示持久入队。

若最后一片最初没有带 `stream_final=true`，改用：

```sh
curl -X POST "$API/sessions/$SID/streams/continuous-demo/finish"
```

finish 可重复调用。不能通过更改原上传的 `stream_final` 重试来结束流，也不要上传空文件。结束后不能追加。精确重试必须使用同一 session、同一 key、相同字节及原始 stream/结束标志/时间元数据；成功会返回原 chunk。更换内容或这些元数据会得到 `409`。重试时省略 `timeline_start` 会沿用原区间，不会重新追加。

```sh
curl "$API/chunks/$CHUNK_0"
curl "$API/chunks/$CHUNK_1"
curl "$API/sessions/$SID/segments?after=0&limit=500"
curl "$API/sessions/$SID/speaker-turns?after=0&limit=500"
curl "$API/sessions/$SID/events?after=0&limit=500"
curl "$API/sessions/$SID/event-candidates?after=0&limit=500"
curl "$API/sessions/$SID/overlap-candidates?after=0&limit=100"
curl "$API/sessions/$SID/changes?after=0&limit=500"
```

建议每秒检查 chunk 一次，直到都为 `completed`；`failed`、`paused`、`cancelled` 应单独报告。`waiting_input` 表示需要真实后续输入或明确 finish，不是终态失败。Native 21.12 秒批次及稳定边界保护会让待提交尾部大于一般 ASR 的 2 秒上下文；重启不会代替 finish。

`next_offset` 是 chunk 本地已提交秒数；`metadata.native_diarization` 中的 `input_until`、`processed_until`、`stable_until`、`finished` 是处理/frontier 证据，缺失应保留为未知。它们不提供逐词正确时间真值。

每个 endpoint 独立保存 `next_cursor`，不可互换。支持 `has_more` 的分页应按该字段继续，即使当前页为空；其余追加页继续到空页。主文本来自 `segments`，raw speaker activity 来自 `speaker-turns`，事件和候选另读。展示时按时间排序；需要接收修改或删除时，按 `changes` 游标顺序和实体 ID 更新，处理完整页后再保存游标。

## 4. MOSS 前必须检查 idle 与 optional 发布状态

**主 chunk 完成不等于 optional cell 已发布。** cell 捕获/发布发生在主结果提交之后，可能仍在交接、清理或失败。先保存主结果，再轮询 `/health`。已测流程每秒检查，最多等待 60 秒；超时仅将 optional 步骤记为未运行/仍待处理，不把主路径改判失败，也不强行发起 MOSS。

以下条件必须同时成立；字段缺失不视为通过：

- `active_chunk == null`
- `moss_jobs.permit.state == "IDLE"`
- `moss_jobs.permit.primary_waiting == false`
- `moss_jobs.permit.optional_admission_unavailable` 为 `null` 或 `false`
- `native_owned_cells.ready == true`
- `native_owned_cells.handoffs_active_or_pending == 0`
- `native_owned_cells.cleanup_pending == false`
- `moss_jobs.cleanup_pending == false`

另外独立记录 `native_owned_cells.status`、`dropped`、`last_capture_reason`、`last_seam_reason`。`status` 非 `ready`、`dropped > 0`、清理未完成等属于 optional warning；`last_capture_reason=publication_pending` 需单列。**idle 与 warning 可以同时成立**，只看 `/health.status=ok` 不够。warning 不证明主文本丢失，idle 也不能消除历史 warning。

## 5. 仅对自然 eligible cell 显式运行诊断

先读取：

```sh
curl "$API/sessions/$SID/native-owned-cells?after=0&limit=20"
```

保存响应 `jobs_epoch`。后续页使用返回的 `next_cursor` 并带 `expected_jobs_epoch`；按 `has_more` 完成分页，epoch 改变则重新发现，不混用旧绑定。

只选自然生成、`state=retained` 且实际保留 PCM 的 eligible cell。已测 AMI 路径选用 `coverage.kind=seam_overlap_alternative` 的 cell。**没有符合条件的 cell 就不发起任务**；不能编造 overlap/cell、修改 proof、绕过门控或换另一入口强制运行。零 cell 和 optional 发布失败要分别记录。

```sh
curl -i "$API/sessions/$SID/native-owned-cells/$CELL_ID"
```

这里返回原始 JSON proof 和绑定 headers。提交前核对：

1. `X-Native-Cell-ID` 与所选 `cell_id` 一致；`X-Native-Cell-State=retained`、`X-Native-Cell-PCM-Retained=true`。
2. 对响应 **原始字节** 计算 SHA-256，与 `X-Native-Cell-Proof-SHA256` 和列表的 `proof_sha256` 三方一致。
3. `X-Native-Cell-Sequence`、`X-Native-Cell-Revision` 与列表一致；`X-MOSS-Jobs-Epoch` 与本轮 epoch 一致。
4. proof 中的 support 与所选 support 相符。`support_id` 是 support 的 canonical JSON（UTF-8、键排序、无多余空格、禁止非有限数）的 SHA-256，不能凭时间范围另造。
5. seam proof 为 `native-owned-seam-composite-v2`，与其 coverage/已提交来源绑定。它是替代诊断覆盖，不增加主音频，也不会自动提交任务。

把上述已验证值分别放入 `$EPOCH`、`$SEQ`、`$REV`、`$PROOF_SHA`、`$SUPPORT_ID`；再次检查第 4 节 idle 条件后提交一个任务：

```sh
curl -X POST "$API/sessions/$SID/native-owned-cells/$CELL_ID/moss-jobs" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: moss_cell_1' \
  -d "{\"jobs_epoch\":\"$EPOCH\",\"expected_cell_sequence\":$SEQ,\"expected_cell_revision\":$REV,\"expected_proof_sha256\":\"$PROOF_SHA\",\"support_id\":\"$SUPPORT_ID\",\"profile\":\"handy-moss-q8_0-cpu-experimental-v1\",\"coverage_intent\":\"overlapping_diagnostic\"}"
```

该 body 对应 seam cell；普通非 composite cell 不带 `coverage_intent`。严格 JSON body 最多 1024 字节，不能加文件路径、任意音频或自选模型。相同 session/key/target/body 的精确重试复用原 job；改变绑定会冲突，重启后的旧 epoch 不能继续使用。cells 可持久恢复，MOSS jobs/results 属于有期限的 volatile 状态，不能假定跨重启恢复。

保存响应的 `job_id` 为 `$JOB_ID`，并核对 `jobs_epoch`、`support_id`、`cell_binding` 中的 cell ID、sequence、revision、proof hash 与提交绑定一致。

```sh
curl "$API/sessions/$SID/moss-jobs/$JOB_ID"
curl "$API/sessions/$SID/moss-jobs/$JOB_ID/result"
curl "$API/sessions/$SID/moss-jobs/$JOB_ID/raw"
```

先轮询 job：`reserved`/`running` 仍在进行；只有达到明确终态且 `cleanup_pending=false` 后，才检查可用内容。成功诊断终态是 `returned_diagnostic`，还需记录 `process_cleanup_verified`、`result_available`、`raw_available`；`failed`、`cancelled`、`invalidated`、清理阻塞或内容不可用不得写成成功。取回 result 后再次核对 `cell_binding`；raw 若返回 200，按 `X-Content-SHA256` 验证原始 UTF-8 字节。

**MOSS result/raw 是独立诊断文本，不是主转写，也不是确认的人物身份或词时间。** 保留原始主 segments，不用 MOSS 自动替换、合并或补写。`returned_diagnostic` 不承诺内容完整；模型声道/说话人标签、时间、漏词和少数声音恢复均未获得质量验收。

## 6. 验收结论与后续边界

上述相邻上传历史用例可以报告“短录音的主 API 功能通过、一个自然 seam 的显式 MOSS 诊断返回、12 个原有机制测试通过”。本检查点的新 finalized batch 则只报告主处理、精确去重、来源映射和原始字节保留通过；MOSS 未运行，optional receipt sequencing 尚未解决。不能外推为 ML 准确率、跨日全局身份、任意多人同时发声恢复、未知坐标或近似音频的跨录音重叠去重、12–48 小时耐久、硬 RAM 上限、Windows 或 GPU 部署通过。

证据类型分别为真实运行 `summary.json`/终态与 frontier 回执、pytest `completion.json`/`junit.xml`、诊断安装和无模型重现完成记录；主成功、optional warning、未运行项目分别保留。本文路由与参数已对照 `audio_backend/app.py`、`audio_backend/moss_handy_http.py`、`audio_backend/native_owned_cell_jobs.py`、`audio_backend/moss_handy_jobs.py` 和 `audio_backend/__main__.py` 核对；一般上传与持续流契约见 `docs/API.md`。

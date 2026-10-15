# dev10 本地 API：录音批次、主结果与显式 MOSS 诊断

先按 [安装与启动](INSTALL_DEV10_ZH.md) 安装完整 dev10 产品。示例假设服务在 `http://127.0.0.1:8765`，使用 `legacy` Worker；若设置 API key，每次请求都要自行加入 Bearer header。所有服务 ID、revision 和 proof 绑定值均来自你本次实际响应，不能复用示例值。

本页的路由和请求格式沿用 dev9。历史 dev9 固定短样本已完成主路径、两个持久 receipt 和一个自然 MOSS 诊断，并通过保存结果的独立复核；这不是 dev10 的重新验收。dev10 为 `0.2.0rc3.dev10` 开发分支；205 个既有选定用例和 46 个可移植新增用例已分两次源码回归通过。标准离线构建、安装/import 与一次已安装 wheel 的真实 HTTP smoke 通过，默认列表一页返回四个 retained cell；该次没有 dispatch MOSS，产物和结果已完成独立复核；retained PCM 是 API 报告状态，未另行下载或直接读取数据库核验。已完成的专项、真实样本观察和历史失败见[开发分支说明](DEV10_RELEASE_NOTES_ZH.md)。这些结果不保证准确率、全局人物身份或 MOSS 完整内容。

## 1. 先确定一个采样坐标域

一个 `capture_id` 明确声明一套共享 PCM 采样坐标。输入必须是同一坐标域中的原始单声道、16 kHz、16-bit little-endian PCM WAV。不能仅凭墙钟时间、文件名、相同文字或录音设备相近就认为可以对齐。不同麦克风或不确定的坐标域使用不同 session/capture。

下例假设你已有同一原始 capture 的三份真实片段：

- `source-a.wav`：帧区间 `[0,168000)`，10.5 秒。
- `source-b.wav`：帧区间 `[120000,288000)`，10.5 秒。
- `source-c.wav`：帧区间 `[240000,336000)`，6 秒。

上传累计 27 秒；若两处重叠的 PCM 逐字节相同，就形成 21 秒唯一并集。示例坐标不是对任意文件的推测；请按自己的真实输入填写。若不知道位置，省略 `source_start_frame`，系统会保留原始来源但不会自动推断或安排 ASR。不同 PCM、空隙、未知位置均使整个批次 unresolved，不会丢弃冲突音频。

每批上限为 16 个来源、32 个派生片段、300 秒唯一音频、32 MiB 录音复制预算；全局 128 MiB。每来源最多 120 秒、3,905,536 编码字节。更严格的服务参数和磁盘余量仍优先。只支持全部输入齐备后的 finalized batch，不是连续 sealed-prefix 或 12–48 小时全语音录音验收。

## 2. 建立空 session 并上传全部来源

```sh
API=http://127.0.0.1:8765
curl --fail-with-body "$API/health"
curl --fail-with-body -X POST "$API/sessions" \
  -H 'Content-Type: application/json' \
  -d '{"name":"recording-demo","identity_group":"recording-demo","processing_mode":"legacy"}'
```

保存返回 `id` 为 `$SID`，设置 `CAPTURE=capture-demo`。capture 名可用 1–128 个 ASCII 字母、数字、下划线、点或连字符。第一份来源会独占该空 session；之后不能向其中调用普通 `/chunks` 上传，也不能更换 capture。

```sh
CAPTURE=capture-demo
curl --fail-with-body -X PUT \
  "$API/sessions/$SID/recordings/$CAPTURE/sources?source_start_frame=0" \
  -H 'Content-Type: application/octet-stream' -H 'Idempotency-Key: source-a' \
  --data-binary @source-a.wav
curl --fail-with-body -X PUT \
  "$API/sessions/$SID/recordings/$CAPTURE/sources?source_start_frame=120000" \
  -H 'Content-Type: application/octet-stream' -H 'Idempotency-Key: source-b' \
  --data-binary @source-b.wav
curl --fail-with-body -X PUT \
  "$API/sessions/$SID/recordings/$CAPTURE/sources?source_start_frame=240000" \
  -H 'Content-Type: application/octet-stream' -H 'Idempotency-Key: source-c' \
  --data-binary @source-c.wav
curl --fail-with-body "$API/sessions/$SID/recordings/$CAPTURE"
```

Body 是原始文件字节，不是 multipart。每次保存响应中的 `source_id`、`duplicate`、sources、revision。精确重试使用同一 capture、同一 key、相同编码字节和相同声明坐标；返回原 ID、`duplicate=true`，revision 不增加。相同 key 更换字节或位置会冲突。不同 key 的相同输入仍保留独立原始回执，只有 finish 时才做 PCM 别名映射。

原始文件完整保留；接口返回摘要与映射，不提供原始文件下载路由。请自行保留独立原录音副本。接收 HTTP 202 不表示 ASR 已完成；在 finish 前还不会安排该批次的 ASR。

## 3. 明确 finish，再轮询 pending 与工作片段

检查所有 sources 都已接收，取最后 GET 的实际 `revision` 为 `$REVISION`。三次全新上传通常是 3；不要盲写 3 来覆盖变化。

```sh
curl --fail-with-body -X POST "$API/sessions/$SID/recordings/$CAPTURE/finish" \
  -H 'Content-Type: application/json' \
  -d "{\"expected_revision\":$REVISION,\"idempotency_key\":\"finish-demo-1\"}"
curl --fail-with-body "$API/sessions/$SID/recordings/$CAPTURE"
```

finish body 为严格 JSON，只含这两个字段。首个有效请求会冻结输入，即使随后 unresolved/interrupted 也不能补来源、改坐标或改用另一个 finish key。过期 revision 会被拒绝，不冻结。重复原 finish body 返回同一工作 ID 和当前状态，不产生重复工作；网络超时先 GET，再精确重试，不更换 key。

建议每秒 GET 一次 recording 状态，并分清：

- `open`：还未 finish；`preparing`：已冻结并在准备。
- `pending_receipts`：工作仍是不可领取的 staging，真实 receipt 正等待 owner 持久确认。维护循环自动推进，其他 session 可继续工作；不要手工把 staging 改成 queued。
- `queued`：该批次已发布普通工作，继续轮询每个 `work_chunks[].chunk_id`；queued 不是 completed。
- `unresolved`：查看 blockers，所有原始来源仍保留，没有 ASR 工作。保留本 session，用新 session 重新提交正确输入。
- `interrupted`：准备/权威校验中断，不代表允许重新开放。保留输入和状态供检查。
- `cancelled`：pending 批次已取消，不能重新排队。

`optional_receipts` 的 pending/durable/gaps/ready 与每个 work 的 `receipt_state`、`native_receipt_status`、`receipt_id`、`receipt_attempts` 一起读取。只有全部 receipt durable 才是 ready；关闭 cells 的 receipt 不会伪装成 durable。明确的可选 gap 可伴随主工作 queued，必须单独显示。ready 不等于已有 cell 或 MOSS 结果。若 observed receipt 持续等待 unavailable owner，先检查健康和环境，或按第 5 节显式取消；轮询超时不是自动失败或自动重提依据。

成功示例会产生两个 168000 帧的工作片段。`capture_origin_frame` 是最小原始坐标，并映射到 session 时间零。`ranges` 中的 source-local、capture 和 work-local 偏移不能混用；`relation=exact_alias` 表示已证明的相等样本，不表示来源被删除。

```sh
curl --fail-with-body "$API/chunks/$CHUNK_ID"
curl --fail-with-body "$API/sessions/$SID/segments?after=0&limit=500"
curl --fail-with-body "$API/sessions/$SID/speaker-turns?after=0&limit=500"
curl --fail-with-body "$API/sessions/$SID/events?after=0&limit=500"
curl --fail-with-body "$API/sessions/$SID/event-candidates?after=0&limit=500"
curl --fail-with-body "$API/sessions/$SID/overlap-candidates?after=0&limit=100"
curl --fail-with-body "$API/sessions/$SID/changes?after=0&limit=500"
```

对每个工作片段轮询到 completed。failed、paused、cancelled 单独处理；普通 continuous stream 的 waiting_input 表示仍需输入或明确 finish。recording 成功生成的内部 stream 已由服务器标记 final，不要再通过普通上传补上下文。

主转写取 segments，说话人活动取 speaker-turns；两者不是逐词身份真值。每类 endpoint 独立保存 `next_cursor`。有 `has_more` 的响应按其继续，即使当前页为空；其他追加列表继续到空页。处理修改/删除时按 changes 游标顺序和实体 ID 更新，处理完整页后再保存游标。

## 4. 普通 chunks 与 recording 的区别

既有 `/sessions/{sid}/chunks` 仍拒绝时间区间重叠。来自同一连续波形的相邻普通上传使用 stream_id 和准确 timeline_start，最后一片带 stream_final，或调用 `/sessions/{sid}/streams/{stream_id}/finish`。不要把重叠上下文伪装成相邻区间，不能把不同录音随意拼接。

普通上传 key 绑定原字节和 stream/结束/时间元数据；不要改变原上传的 stream_final 做重试。recording finish 有自己的 JSON key/revision，不能拿普通 stream finish 替代它。完整既有接口见 [API.md](API.md)。

## 5. pending cancel 与其他控制

只有 `pending_receipts` 可使用 recording cancel；重复取消已取消批次会返回当前状态。

```sh
curl --fail-with-body -X POST "$API/sessions/$SID/recordings/$CAPTURE/cancel"
```

它保留 originals、映射与 receipt payload，把尚未排队的工作和 pending outbox 标为取消；晚到的确认不能重新排队。它不是数据删除，也不会重新打开 batch。

已经 queued 的工作使用普通 `/chunks/{id}/pause`、`resume`、`retry`、`cancel` 控制，并遵守既有 stream 约束。这不提供按来源 undo 或撤销已完成结果。整 session 删除会影响原始文件、工作、cell 和关联状态，是破坏性操作；不是解决 unresolved 的必要步骤，不应拿删除当默认重试。

常见错误：404 为对象不存在；409 为状态/幂等/输入冲突；422 为请求字段校验错误；413 为上传字节超限；429 为上传/维护或容量限制；507 为资源/磁盘拒绝。应检查响应详情。只对暂时性拒绝按原请求重试，不能通过换 key、降低真实性校验或改数据库避开错误。

## 6. MOSS 前检查实际 idle、cell 与 optional 状态

必须事先显式启用 support、cells、seam-cells 和 moss-jobs，并满足安装指南中的环境资格。主工作完成不等于 cell 已发布。先保存主结果，再轮询 health；等待超时只标记 optional 未完成，不强行请求 MOSS。

同时检查以下条件；缺失字段不能当作通过：

- `active_chunk == null`
- `moss_jobs.permit.state == "IDLE"`
- `moss_jobs.permit.primary_waiting == false`
- `moss_jobs.permit.optional_admission_unavailable` 为 null 或 false
- `native_owned_cells.ready == true`
- `native_owned_cells.handoffs_active_or_pending == 0`
- `native_owned_cells.cleanup_pending == false`
- `moss_jobs.cleanup_pending == false`

另外保留 native_owned_cells 的 status、dropped、last_capture_reason、last_seam_reason。`publication_pending` 可能是最后一次 capture 的历史标记；不能覆盖后来已 durable 的 receipt/cell 证据，也不能因为 idle 就隐藏该标记、历史 dropped 或 publication 失败。`handoffs_active_or_pending` 才表示当前活动/等待中的 owner 工作；数值为 0 也不证明所有过去的 publication 都成功。主成功、可选 warning、等待和未运行应分别显示。

```sh
curl --fail-with-body "$API/sessions/$SID/native-owned-cells?after=0&limit=20"
```

dev10 在单次列表操作内复用 PrimaryAuthority 的只读连接，每次权威读取仍以新的 `BEGIN`/`ROLLBACK` 事务取得当时状态，不缓存快照或结果。每次操作仍受 2 秒、150,000 SQL VM 步数及原有字节预算限制，最终来源/提交/receipt/session 校验不省略。返回错误或预算拒绝不等于空列表；保留失败详情，不以放宽预算或跳过校验重试。

保存返回 jobs_epoch。后续页使用 next_cursor，并带 expected_jobs_epoch；按 has_more 完成分页，epoch 改变则重新发现。只选择自然生成、state=retained、实际保留 PCM 的 eligible cell。已测自然 seam 的 coverage.kind 为 `seam_overlap_alternative`。没有 eligible cell 就不发起任务；不能编造 cell/proof 或换入口绕过门控。

```sh
curl --fail-with-body -i "$API/sessions/$SID/native-owned-cells/$CELL_ID"
```

返回 proof 原始 JSON 字节和绑定 headers。提交前逐项检查：

1. X-Native-Cell-ID、X-Native-Cell-State、X-Native-Cell-PCM-Retained 与列表/选择一致。
2. 对响应 body 原始字节计算 SHA-256，与 X-Native-Cell-Proof-SHA256 和列表 proof_sha256 一致；不要重排 JSON 后再计算。
3. X-Native-Cell-Sequence、X-Native-Cell-Revision、X-MOSS-Jobs-Epoch 与本轮列表一致。
4. proof 中 support 与选择一致；support_id 是该 support 的 canonical JSON SHA-256（UTF-8、键排序、无多余空格、禁止非有限数），不能凭时间范围另造。
5. seam proof 为 native-owned-seam-composite-v2，与已提交来源/coverage 绑定，仅代表替代诊断覆盖。

## 7. 一个显式、绑定准确的 MOSS job

把已核实的响应值放入下列变量，再次检查 idle。下例对应 seam cell；普通非 composite cell 不带 coverage_intent。

```sh
curl --fail-with-body -X POST "$API/sessions/$SID/native-owned-cells/$CELL_ID/moss-jobs" \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: moss-demo-1' \
  -d "{\"jobs_epoch\":\"$EPOCH\",\"expected_cell_sequence\":$SEQ,\"expected_cell_revision\":$CELL_REV,\"expected_proof_sha256\":\"$PROOF_SHA\",\"support_id\":\"$SUPPORT_ID\",\"profile\":\"handy-moss-q8_0-cpu-experimental-v1\",\"coverage_intent\":\"overlapping_diagnostic\"}"
```

严格 body 最多 1024 字节，不能添加文件路径、任意音频或自选模型。相同 session/key/target/body 的精确重试复用原 job；更改绑定会冲突。重启后旧 epoch 失效，cells 可以持久恢复，但 job/result 是有期限的 volatile 状态，不能假定跨重启恢复。

保存返回 job_id，并核对 jobs_epoch、support_id 和 cell_binding 中的 ID、sequence、revision、proof hash。然后轮询：

```sh
curl --fail-with-body "$API/sessions/$SID/moss-jobs/$JOB_ID"
curl --fail-with-body "$API/sessions/$SID/moss-jobs/$JOB_ID/result"
curl --fail-with-body "$API/sessions/$SID/moss-jobs/$JOB_ID/raw"
```

reserved/running 仍在进行。达到明确终态、cleanup_pending=false 后检查内容是否可用。成功诊断状态为 returned_diagnostic，同时记录 process_cleanup_verified、result_available、raw_available。failed、cancelled、invalidated、清理阻塞和内容不可用不得写成成功。result 的 cell_binding 应再次吻合；raw 若 HTTP 200，按 X-Content-SHA256 校验原始 UTF-8 字节。

dev10 的精确帧坐标观察与静音 seam 尾部保留只影响可选 capture；客户端不需要添加坐标容差、补造 proof 或改变主时间值。预期无重叠的 A–B seam 可以保留已验证 B 尾部供后续 B–C 使用，不会凭空生成 seam cell；格式、来源或权威校验失败仍拒绝。

保留原主 segments。MOSS result/raw 是独立诊断，不自动替换、补齐或合并主文本；本次返回包含的局部说话人标签不是真实人物或全局身份。returned_diagnostic 不保证内容完整，时间戳、词句准确率和所有重叠声音恢复均需独立评估。

# 已定稿音频文件分页与连续追加指南

适用范围：dev14 连续追加源码后续候选，包含暂存工作隔离、不可撤回的页放行、原有单文件 `--final` 和实验性 `--append` / `--finalize`。版本与依赖不变；本文不表示本后续候选已发布。本轮标准离线 wheel 构建、全新 target 安装及所选有界包检查已完成，旧快照和本轮的证据仍分别记录，具体范围见第 11 节。示例要求确实包含这些参数的源码或安装包，不能只凭 dev14 版本字符串判断。

这条路径把已停止写入的本地音频文件规范化为 16 kHz、单声道、16-bit PCM，分成有界页面上传。每个原文件分别连续解码；已明确连续放置的多个原文件可共用一个 capture。每页验证、封存并放行后，普通 worker 就能分析已放行前缀，不必等整个 capture 结束。输入文件、服务端接收的来源副本、来源到工作 chunk 的映射和已提交结果分别保留。

三种模式互斥，均须显式声明同一规范化帧坐标原点：`--final` 上传一个文件并结束 capture；`--append` 每次接收一个带稳定 `--input-id` 的已定稿文件，不声明 EOS；`--finalize` 不接收输入文件、输入 ID 或输入起点，显式结束已有追加任务。不能混用 v1 单文件与 v2 追加 journal，也不能把正在增长的文件作为已定稿输入。

## 1 使用前确认

- 使用既有合资格 Linux CPU 环境及已准备好的依赖、模型和 native runtime。此功能不下载模型，不改变 ASR、说话人、FrameMN 或其他可选能力的默认开关；可选 cell 仍需自己的资格条件。
- 服务与上传器使用同一候选源码。一个数据目录只运行一个服务进程，不使用 reload 或多 worker。继续采用已验证的服务启动参数；`/health` 只说明服务存活，不能替代模型就绪检查。
- 准备一个非空、普通本地文件，停止录制并关闭写入端后再提交。不要使用符号链接、管道、远程 URL、播放列表或可变文件。
- 准备新的空 `legacy` session、尚未使用的 capture ID，以及该任务专用的状态目录。输入文件必须在状态目录之外。不要多个进程共用一个状态目录。
- 已授权 API token 通过 `AUDIO_API_KEY` 环境变量提供。服务和上传器须使用匹配的 token；不要把真实 token 写进命令示例、文件名、URL 或状态目录。上传器只在请求头中使用 token，不把它写进 journal。
- 为服务端保留来源、派生工作文件及元数据预算；上传器本地还需原文件、单页临时副本和状态空间。两端可能在不同磁盘，须分别检查。

以下命令在包含本功能的源码根目录执行；`python3.12` 应指向已准备好的运行时。若使用安装包，则先确认安装包确实包含本功能，不要仅凭版本字符串判断。

## 2 创建 session 并选择上传模式

### 创建空 session

以下使用本机 API。已有 token 应由当前环境安全提供，此处不设置或展示其值。

```sh
export API='http://127.0.0.1:8765'
: "${AUDIO_API_KEY:?请先配置已授权的 API token}"

curl --fail-with-body -sS \
  -H "Authorization: Bearer $AUDIO_API_KEY" \
  -H 'Content-Type: application/json' \
  --data '{"name":"hour file","processing_mode":"legacy"}' \
  "$API/sessions"
```

该请求为 `POST /sessions`，成功返回 HTTP 201。把响应中的 `id` 原样保存为下面的 `SESSION_ID`。不要填入 session 名称，也不要使用已有 chunk 的 session 或 `fragment_pcm16_v1` session。

```sh
export SESSION_ID='替换为刚返回的 session id'
export CAPTURE_ID='hour-file-001'
export INPUT='/absolute/path/to/finalized-recording.wav'
export STATE_DIR="$HOME/audio-upload-state/hour-file-001"
```

`CAPTURE_ID` 是调用方为同一规范化采样坐标域取的 ID，允许 1 至 128 个字母、数字、下划线、点或连字符。它不是由文件名、录音设备或墙上时钟自动推断的身份。

### 单文件完成模式：保留原有 --final

```sh
python3.12 -B -m audio_backend.recording_uploader \
  --api "$API" \
  --input "$INPUT" \
  --session-id "$SESSION_ID" \
  --capture-id "$CAPTURE_ID" \
  --capture-origin-frame 0 \
  --state-dir "$STATE_DIR" \
  --final
```

这里的 `0` 是明确选择的规范化帧原点。文件连续解码后第 n 个输出帧的 capture 坐标为 `origin + n`；16,000 帧等于一秒。API 的页面及来源坐标使用这个整数帧域，session 时间轴则从该 capture 的原点计为 0 秒。若采用非零原点，必须由调用方知道它的含义，并在恢复时保持完全相同；不能用毫秒、压缩包时间戳或估算的文件开始时间替代。

无需另发“创建 capture”请求。CLI 会先检查 session 为空、capture 不存在，然后通过第 1 页请求创建 capture。**不要先手动创建页再运行全新 CLI journal**；CLI 不接管已有 capture。下面第 5 节的直接 API 流程是另一种客户端实现方式，不是本命令的前置操作。

`--api` 只接受 HTTP(S) origin，不能包含凭据、路径、query 或 fragment。非 loopback 地址必须用 HTTPS。远程服务的证书、鉴权和安全部署由部署方准备。

### 成功意味着什么

单文件 `--final` 正常结束时打印 JSON，含 `complete: true`、`frames`、`pages`、`sources` 等字段，进程返回 0。其含义是该文件已完整摄取并声明真实 EOS；分析可能仍在排队。追加模式另用 `input_complete`、`awaiting_input` 和 `capture_finalized` 区分单个输入完成、等待后续输入与 capture 已结束；`frames` 包含暂存帧，`published_frames` 只计已放行帧，`staged_frames` 是二者之差。任何模式都不把上传成功等同于 ASR、说话人或事件分析完成。

中断或失败返回 2，并输出 `Incomplete ingestion`。已放行前缀保持有效；不要把非零退出当成“什么都没上传”，也不要立即另建任务重传。

### 多文件模式：连续追加后显式结束

为追加任务另建一个空 session、尚未使用的 capture ID 和独占状态目录，不复用上面已经 `--final` 的任务。沿用本节 session 创建方法和 API/token 设置；下面两份输入均须已定稿并保留在状态目录之外。

```sh
export CAPTURE_ID='contiguous-files-001'
export STATE_DIR="$HOME/audio-upload-state/contiguous-files-001"
export INPUT_A='/absolute/path/to/finalized-a.wav'
export INPUT_B='/absolute/path/to/finalized-b.wav'

python3.12 -B -m audio_backend.recording_uploader \
  --api "$API" --session-id "$SESSION_ID" --capture-id "$CAPTURE_ID" \
  --capture-origin-frame 0 --state-dir "$STATE_DIR" \
  --append --input "$INPUT_A" --input-id original-a

python3.12 -B -m audio_backend.recording_uploader \
  --api "$API" --session-id "$SESSION_ID" --capture-id "$CAPTURE_ID" \
  --capture-origin-frame 0 --state-dir "$STATE_DIR" \
  --append --input "$INPUT_B" --input-id original-b

python3.12 -B -m audio_backend.recording_uploader \
  --api "$API" --session-id "$SESSION_ID" --capture-id "$CAPTURE_ID" \
  --capture-origin-frame 0 --state-dir "$STATE_DIR" --finalize
```

新输入默认从当前下一规范化帧开始；已有 ID 重试默认沿用原保存位置。若给出 `--input-start-frame N`，N 必须精确等于该输入在 capture 规范化 PCM 轴上的绝对起点。不同原文件间的空隙和重叠在注册、解码和 HTTP 修改之前被拒绝；相同内容位于不同连续区间仍是不同音频，不能按内容相似度去重。这个轴不是物理时钟，也不证明压缩编码之间的无缝连接。

每个来源 WAV 只归属一个原文件，不拼接、扩写或改挂到另一个原文件。满页为 294 秒，以 `eos:false` 放行；不足一页的尾部保留在本地，即使其中有完整 10.5 秒工作单元也不提前封页。下一输入用自己的独立来源片段填充余下容量。第一次恰好追加 3,600 秒时，12 页已放行，72 秒尾部保留；第二个 3,600 秒输入结束后，24 页已放行，144 秒仍暂存；最后显式 finalize 才发布第 25 页和唯一 EOS。满页边界处 finalize 使用 capture finish，不创建空页或空来源。

同一 ID、原文件及坐标可安全重试，已结束后也可核对重放；不能换 ID 重复登记同一个物理原文件。若有未完成输入，先恢复该 ID，不能跳到新输入。Finalize 要求全部登记输入都有非空、干净 EOF；先保存结束意图并禁止新 ID，打断后仍只重试 finalize。完成后不得再添加新原文件。

## 3 查询进度和读取结果

```sh
curl --fail-with-body -sS \
  -H "Authorization: Bearer $AUDIO_API_KEY" \
  "$API/sessions/$SESSION_ID/recordings/$CAPTURE_ID"

curl --fail-with-body -sS \
  -H "Authorization: Bearer $AUDIO_API_KEY" \
  "$API/sessions/$SESSION_ID/recordings/$CAPTURE_ID/pages?after=0&limit=32"

curl --fail-with-body -sS \
  -H "Authorization: Bearer $AUDIO_API_KEY" \
  "$API/sessions/$SESSION_ID/chunks?after=0&limit=100"
```

- capture 状态提供 `capture_origin_frame`、`capture_end_frame`、`eos`、`released_pages`、`pending_page`、`blockers` 和实际 `limits`。首次创建页面之前 capture 尚不存在，查询返回 404 是正常情况。
- 页面列表按页号分页，`after` 为上一页响应的 `next_cursor`，`limit` 为 1 至 64。详情用 `GET /sessions/{sid}/recordings/{capture_id}/pages/{page_number}`。
- 页详情的 `sources`、`work_chunks`、`ranges` 用于核对来源和处理映射。状态常见顺序为 `open` → `sealed` / `preparing` → `pending_receipts` → `published`；最终放行以 `released: true` 为准。
- session chunk 列表按自己的 `next_cursor` 翻页，最多每次 500 条；`completed` 才是该 chunk 分析完成。`GET /chunks/{chunk_id}` 可读更详细的进度和错误。
- 已提交结果继续通过 `/sessions/{sid}/` 下的 `segments`、`events`、`speaker-turns`、`event-candidates` 和 `changes` 等原有路由读取，使用各路由自己的分页游标。

`pending_receipts` 表示该页暂存工作正在处理可选 receipt，不表示所有工作已对推理开放。`optional_receipts.ready` 只表达相应 receipt 状态，不能作为普通 ASR 的完成标志。可选能力未启用时出现 `disabled` / gap 也不表示主路径失败；应结合页是否已放行及具体 blocker 解读。

## 4 保留状态并恢复同一任务

保留原文件、原状态目录、同一服务数据目录，以及原命令的 API、session ID、capture ID 和 origin。恢复时重新执行完全相同的上传命令，无需 `--resume` 开关。

上传器会核对原文件路径、文件身份、大小、时间信息及完整摘要，核对解码方式和工具构建身份，再读取服务端页面、来源 key、摘要、坐标和放行状态。单文件模式恢复会从原文件开头重新连续解码；追加模式每次先重新校验全部登记原文件，但只从头重放尚未完成的当前原文件，已完成原文件不再次解码。已发布区间以流式摘要验证而不重建磁盘副本；只有核验匹配后，才可重建当前未完成输入丢失的片段。已完成原文件的尾部缺失或损坏会停止，不能悄悄重建或接管。已接收来源和已放行页通过原 key 与冻结请求核对，不生成重复工作。

以下情况应停止并调查，不能通过改 journal、换 key 或清空目录强行继续：

- 原文件被替换、移动、继续写入，或规范化输出不同。
- ffmpeg/ffprobe 文件、版本输出或所选音轨/解码配方改变。
- 服务端已接受的页、来源或已放行状态消失，ID、摘要、坐标与 journal 不符。
- journal 丢失，但同名 capture 已存在。新 journal 不具有接管该 capture 的依据。
- 页面成为 `unresolved`、`cancelled`，capture 已被终止，或分析 worker 的阻塞状态需要人工处理。

请求超时不等于服务端未接收。上传器会先 GET 对账，再精确重放原上传/封存/finish 请求。`Idempotency-Key`、`expected_revision`、`end_frame`、`eos` 都属于冻结身份，不能为了“重试成功”而改值。

暂停进程、Ctrl-C、网络失败、解码失败、达到资源限额或截止时间，都不会自动把录音声明为 EOS，也不会自动调用 capture cancel。原状态保留，先修复实际阻塞再恢复。成功后也应保留 journal 和全部原文件，作为各输入与 capture 的客户端对应记录；它不是服务端的权威来源证明。

## 5 直接使用页面 API

自行实现客户端时，需要新的空 legacy session。以下是实际方法、路径和字段；大括号代表调用方替换值。JSON 请求严格拒绝额外字段，坐标和 revision 必须为整数。

1. `POST /sessions/{sid}/recordings/{capture_id}/pages`

   第一页 body 为 `{"page_number":1,"capture_origin_frame":0}`。这一步同时创建 capture。下一页使用 `{"page_number":2}`，其余依次递增；原点省略时沿用已有原点，显式提供时必须相同。前一页必须已 `released`，同一 capture 同时只能有一页未放行。capture 不能在已有 legacy recording batch 上就地切换模式。

2. `PUT /sessions/{sid}/recordings/{capture_id}/pages/{page_number}/sources?source_start_frame={integer}`

   请求头含 `Idempotency-Key: {stable-key}`、`Content-Type: audio/wav` 和准确 `Content-Length`；body 是完整 WAV 字节，不是 multipart。来源须为 16 kHz、mono、PCM16 WAV，每个来源最多 120 秒。`source_start_frame` 是该来源第一个 PCM 帧在 capture 中的坐标。相同 key 重试必须保持同一页、同一文件字节和同一坐标；key 在 capture 范围内唯一。省略坐标虽可留下来源记录，但封页会成为 `unknown_placement`，不会猜测位置。

3. `GET /sessions/{sid}/recordings/{capture_id}/pages/{page_number}`

   上传完该页来源后读取当前页 `revision`，准备并持久保存封页请求。

4. `POST /sessions/{sid}/recordings/{capture_id}/pages/{page_number}/seal`

   body 为 `{"idempotency_key":"page-1-seal","expected_revision":R,"end_frame":E,"eos":false}`。其中 R、E 必须替换为 JSON 整数。第一次有效 seal 冻结来源集合、revision、结束坐标和 EOS；后续仅允许原请求精确重试。过期 revision 会被拒绝。封存后发现冲突或未知位置时，不能再向该页补来源或改坐标。

   `end_frame` 是半开区间的末端。非终结页末端相对于 capture origin 必须落在 168,000 帧的工作网格上，即 10.5 秒整数倍；每页新增部分最多 28 个工作 chunk，共 4,704,000 帧或 294 秒。只有真实最终页可用 `eos: true` 并带不足一个 chunk 的尾部。封存响应为 202 仍可能是等待状态，须检查 `released`，不能仅凭 HTTP 成功码开始下一页。

5. `POST /sessions/{sid}/recordings/{capture_id}/finish`

   若最后一页恰好在网格上，已作为非终结页放行，随后才确认输入 EOF，可用 `{"idempotency_key":"capture-finish","expected_revision":R}` 完成 capture。此处 R 来自 capture 状态，不能使用页 revision。必须没有未放行页；精确重试原请求。已经通过 `eos:true` 放行最终页时不需要再调用 finish。不能用普通 `/streams/{stream_id}/finish` 绕过 capture 协议。

这一版 CLI 自动执行以上步骤。它按每页 294 秒、每来源最多 120 秒组装，因此满页通常是 120 + 120 + 54 秒；工作 chunk 独立按 10.5 秒切分。页边界、上传来源边界和模型分析窗口不是同一层边界。

## 6 暂存隔离 已放行历史和真正结束

暂存 chunk 可能已出现在诊断接口中，但未放行页不得进入普通 worker 的领取、相邻上下文、连续性和结束判断。只检查 `status != staging` 不足以保证隔离，页面自己的 `released` 标记才是持续有效的放行依据。

来源和工作映射、暂存工作及 receipt 状态先持久化；页面放行与普通队列可见性在同一事务提交。事务回滚不能提前唤醒前缀，也不能让半页成为上下文。可选 receipt 尚未完成时，其他无关流仍可继续处理。

`released: true` 一旦提交就保持为真。后页失败、重启、暂停或终止 capture，不能把已放行页重新藏起来或改写已提交结果。这里的永久性指该页的放行事实；显式删除整个 session 仍会走原有破坏性删除和文件清理流程，并不是长期备份承诺。

EOS 只在确认录音真的结束时设置。普通页封存、单个追加输入 EOF、上传暂时中断、没有更多数据到达或 cancel 都不等于 capture 结束。CLI 需观察干净的解码 EOF、确认解码器成功退出并再次检查原文件完整性；单文件模式由 `--final` 声明结束，追加模式还必须显式 `--finalize`。恰好结束于满页时使用 capture finish。未声明 EOS 的末端可能保持 `waiting_input`，等待后续已放行输入提供右侧上下文。

`POST /sessions/{sid}/recordings/{capture_id}/cancel` 会终止后续延伸并取消未完成工作，保留已放行历史、来源和已提交结果，不伪造 EOS。它不是可恢复的临时暂停，也不是删除操作。正常网络恢复不需要调用它。

## 7 已知坐标的精确重复和无法判断的重叠

相同 capture ID 是调用方声明共享采样坐标的前提。系统只对已明确定位的重叠区逐段比较全部 PCM 样本；完全相同时记录 `exact_alias`，复用既有工作，不重复转写该段。新坐标部分记录 `owner` 并生成新工作。来源副本及别名映射仍保留，别名不等于删除副本。

- 同坐标但 PCM 不同：`pcm_conflict`，当前页不放行。
- 没有坐标：`unknown_placement`，不做模糊匹配或自动时移。
- 新增区间有空洞：`capture_gap`，不补静音、不压缩时间轴。
- 来源越过声明的封页边界或早于原点：`source_outside_seal`。
- 历史工作文件或其持久化身份无法核验：停止当前页，不凭旧结果文本推断音频一致。

本节的重叠比较是既有服务端 API 能力；实验性追加 CLI 仍拒绝不同原文件之间的重叠，不因服务端有此能力就自动猜测放置。

这不是按内容相似度去重，也不是跨设备录音对齐。两个压缩文件即使听起来相同，分别解码后也不必逐样本相等；没有明确的同一规范化坐标和精确 PCM 证据时，不能自动合并。已放行结果不会因后来的来源而重算或覆盖。

## 8 磁盘 内存和排队边界

### 服务端默认值

- 每个 sealed-page capture 的保留预算：1 GiB。
- 全部 recording 保留副本预算：4 GiB。
- recording 磁盘剩余空间安全下限：5.5 GiB，实际采用它与一般磁盘保留设置中的较大值。
- 每页文件预算：32 MiB，另有每 capture / 每页各 512 KiB 的元数据预留；最多 1,024 页、每页 16 个来源、28 个新增工作 chunk。系统 recording capture 数量也有上限。

预算包含接收的来源副本、派生工作文件、暂存/残留文件和预留元数据，文件收费按逻辑大小与实际分配字节的较大值计算。请求在写入前预留空间，写入过程中仍检查资源。它不是只计算音频时长，也不是整个数据目录的绝对大小限制：原输入若在服务之外，及其他存储/模型仍另占磁盘。

混合模式限制：旧 legacy recording batch 的准入仍使用 128 MiB 复制上限，并把已保留的 recording 文件计入其中。因此较大的 paged capture 可能阻止新建 legacy recording batch，即使分页路径的 4 GiB 总预算仍有空间。普通 `/chunks` 上传是另一条路径。

这些服务端值来自 `Settings.recording_capture_bytes`、`recording_total_bytes`、`recording_disk_reserve_bytes`。当前普通服务启动器没有对应的三个命令行开关，不要照名称杜撰参数或环境变量。实际值以 capture 状态中的 `limits` 为准。

服务端不自动删除来源或已处理录音来腾空间，不提供来源级删除接口。达到保留预算后需先决定保留、备份和显式清理哪些数据；不要删除 uploads 中的单个文件或手改数据库。含 recording 数据的目录迁移限制见第 10 节。

### 上传器默认值

- 单文件模式原输入最大 8 GiB；追加模式全部登记原文件合计最大 8 GiB。规范化总输出仍最多两小时、25 页和 105 个来源记录，属于临时实验上限，不表示可无限延伸。
- 追加模式 `--max-inputs M` 可设 1 至 32，首次默认 32；恢复时省略便沿用冻结值，修改值会冲突。每页最多 16 个原文件独占片段；新输入准入会预留填满当前页所需的来源槽位，已有片段加余下容量所需槽位不得超过 16。过多微小输入可能在达到 32 个输入之前被拒绝，此时重试已有输入或 finalize 当前尾部，不能自动合并来源。
- v2 journal 的每输入记录最多 8 KiB、每片段最多 1 KiB、每页控制元数据最多 2 KiB、全局元数据最多 16 KiB，全部 JSON 最多 512 KiB。ID 最多 128 个 ASCII 字符，路径最多 4,096 个编码字节。
- 本地 spool 预算 12 MiB，一次只保留一页派生 WAV；尾部、部分写入、清理残留、分配取整和 journal 替换均计入预算。单页最多 9,408,704 个 WAV 字节，保留已有尾部时只为缺少的容量继续预留。读取、PCM 与 HTTP 文件发送以最多 64 KiB 小块进行，不把整小时 PCM 装进内存。
- 上传器本地磁盘也保留 5.5 GiB 安全下限。`--disk-floor-bytes` 只调整上传器这一端，不能修改服务端下限。不要为通过测试而降低生产安全余量。
- 上传器进程树 RSS 采样保护为 512 MiB，检查间隔目标为 0.1 秒；这不是操作系统硬内存隔离，也不包含独立运行的服务端模型进程。
- 每次调用的整体协作式截止时间默认 12 小时，可用 `--deadline-seconds` 指定。HTTP socket timeout 默认 30 秒，并在分块操作间检查请求剩余时间；这不是独立于阻塞调用的硬 watchdog，也不是吞吐或完成时限承诺。

已放行页的本地派生 spool 可由上传器清理；中断恢复也只清理 journal 记录的自有临时副本。用户原文件、无关文件、服务端来源和客户端 journal 不会因此自动删除。

### 背压和失败

一个连续流最多积压 128 个 pending chunk/receipt，包含暂存、排队、运行、暂停、等待输入和可恢复失败；已完成 chunk 不继续占用这项 pending 上限。全局默认另有 64 个工作单元上限，一个连续流计一个单元，不能把这两项理解为“整个录音最多 128 块”或“默认并发 64 个分析器”。

当新增页会越过 128 pending 上限时，封页得到 429 背压，CLI 等待处理推进，不继续解码下一页。必须有实际工作的 worker 才能排空积压。CLI 对不确定网络结果、429 和 500 至 506 的响应按状态对账和等待重试；401、409、413、507 等会停止本次调用。磁盘条件修复后可用原状态恢复。

如果背压时发现 worker 已 `failed`、`paused` 或 `cancelled`，CLI 会停下并说明对应 chunk。先通过 chunk 状态查明原因，再按既有控制接口处理；不要靠换 session 或改上传 key 掩盖失败。`unresolved` 的页面内容冲突也不会靠无限重试自行消失。

## 9 格式和 Linux 解码范围

已经是 16 kHz、mono、PCM16 WAV 的输入走直接顺序读取。其他受支持输入对每个原文件使用一个连续 ffmpeg 解码进程产生规范化 PCM，再切来源与页面；不是对每页单独 seek/重启解码。跨原文件仍是独立的解码/重采样调用，不承诺任意编码组合的声学无缝连续性。

当前压缩/非规范 WAV 路径依赖 Linux 合资格环境的 POSIX 可继承描述符与 `/proc/self/fd`。工具默认位置为 `/usr/bin/ffmpeg` 和 `/usr/bin/ffprobe`。CLI 没有用于替换这两个路径的参数。Windows、macOS 或其他运行时不能仅凭文件格式相同推定支持。

支持的自包含容器/音轨组合为 WAV 的受支持 PCM、FLAC、MP3、M4A 中的 AAC，以及 Ogg/Opus；`.ogg` 不代表支持所有 Ogg 编码。必须恰有一个受支持音频流，不能靠默认“第一条音轨”选择不明确的输入。解码工具、构建输出、音轨及参数绑定到 journal；恢复时更换工具会被拒绝。

解码失败、截断 WAV、奇数尾字节或非零解码器退出都不能产生成功 EOS。既有已放行前缀仍保留。这些机制不证明任意压缩编码、任意音频质量、跨平台精度或真实模型小时吞吐。

## 10 升级和数据保留

### 同版本替换与 journal 隔离

此后续候选仍使用 `0.2.0rc3.dev14`。替换已有 dev14 时，必须选定并核对确实包含追加实现的精确源码 commit 或对应 wheel；普通的版本号升级未必替换旧的同版本安装。使用已核对的 wheel 和现有合资格依赖时，在对应运行时显式执行：

```sh
python3.12 -m pip --isolated install --no-index --no-deps --force-reinstall \
  /absolute/path/to/verified/long_audio_backend-0.2.0rc3.dev14-py3-none-any.whl
python3.12 -m audio_backend.recording_uploader --help
```

上面的路径是所选新 wheel 的占位路径，应先核对其来源与摘要；`--help` 应列出 `--append` 和 `--finalize`。该命令只替换包，不准备依赖、模型或可选资格。不要把旧 wheel 改名后视为新功能。若从源码部署，也应固定确切的新 commit，而不是只按相同版本字符串选择。

原有单文件 v1 journal 与对应输入完整保留，仍用原单文件恢复方式处理。追加必须使用独立全新的 v2 journal，不迁移、覆盖或重新打开 v1 状态；不能让新 journal 接管已有 capture。

正常升级前停止新输入，等工作到可控状态，正常停止服务并确认退出，完整备份数据目录和对应旧 runtime。保留数据库、上传文件、可选存储及仍存在的日志；不要手动删除 WAL/journal。

以下迁移是既有 sealed-page 基线的升级说明；本次追加只更改客户端，不再新增服务端 schema。既有分页功能在 Store 初始化事务中作增量 schema 迁移，无需手工执行 SQL：新增 `recording_pages`、相关索引和不可逆放行触发器；已有 `recording_batches` 增加 `mode` 与 `eos`，`recording_sources` 增加 `page_id`，`recording_work` 增加 `page_id` 与 `file_signature`。旧 recording 行保留 legacy 模式，旧来源/工作不凭空获得页归属；旧 batch 不能原地改成 sealed-page capture。已有结果不因迁移重新生成。

重启时，尚未完成持久化的页面准备状态可以回到 `sealed`，用原 seal 请求重试；完整的未放行 outbox 保留原工作 ID，并按当前 receipt 权威核对后继续。已放行页不重新封存或撤回。旧 legacy batch 的恢复限制仍保留，不能把页面恢复能力推广到旧批次。

首次升级保持数据目录原绝对路径。旧 `scripts/migrate_audio_data.py` 主要处理 `chunks.path`，不能完整迁移 recording 来源、文件身份和 manifest；含 recording 的目录目前没有通用跨路径迁移流程。不要绕过启动路径检查，也不要只改数据库路径。

降回旧源码或 wheel 不会回退 schema、文件或 journal。需要回退时，保留升级后的完整目录，使用升级前完整备份、匹配旧 runtime 和原路径恢复；升级后的新增记录不会自动合入旧备份。不要 drop 新表、删音频或修改状态来制造兼容。

## 11 当前追加证据与历史快照

### 追加候选：分别记录的模型无关与真实短样本证据

截至 2026-10-16，追加候选的专项运行通过 56 个模型无关用例，其中包含旧的小用例；另一独立的双小时逻辑运行通过 1 个用例。二者分别记录，不与历史 252-case 运行累计，不宣称新跑过整个仓库。

双小时用例使用两个各 57,600,000 帧、3,600 秒的独立合成原文件，经过真实 loopback HTTP/API 和假 analyzer，最终共 115,200,000 帧、25 页、74 个来源、686 个工作 chunk（685 个完整块与 7.5 秒尾块）及一次 EOS。通过断言核对流式 PCM 守恒、输入局部与服务端来源范围、72 秒和 144 秒中间尾部、背压、丢失响应后的重试、完成原文件不再解码，以及 finalize 重放不增添工作。这是逻辑输入长度，不是两小时真实模型运行或吞吐检查。

另一次真实 AMI 双文件短样本合计 336,000 帧、21 秒，六次实际 CLI 调用分别覆盖追加、重试与结束；两个主工作 chunk 完成，21 秒 native 输入保持在一个 epoch，三个 cell 持久化且 dropped 为 0。一个自然返回的 MOSS 诊断及其 raw 响应具有严格的 cell/support/job/result 绑定；独立复核确认两个原文件与派生来源的对应关系及字节一致，原调用的退出记录和已观察自有进程的等待退出。

该 MOSS 结果的 `source_current` 为 `not_checked`；完整性、人物身份与 profile 更新资格均为 false，EOS 和 token 数未知。它没有证明完整内容、准确率、全局人物身份或长时资源可靠性；短样本成功也不能推广到任意文件接缝或小时级声学推理。

本后续候选的八个有界步骤均以退出码 0 完成：标准离线 wheel 构建、全新 `--target` 安装、既有根目录 MOSS manifest 单独复制、包字节核对、默认关闭导入及应用构造、服务端 CLI 帮助、上传器 CLI 帮助，以及一个安装目标追加用例。Wheel 为 556,624 字节、119 个成员；115 个包文件在源码、wheel 与全新安装目标之间逐字节一致，版本与依赖元数据保持不变，随包许可证和适配记录也已核对。MOSS manifest 继续单独复制，不在 wheel 中。

安装目标用例通过 1 个测试，pytest 用时 1.34 秒；只使用两个合计 336,007 帧的合成原文件，通过 TestClient/ASGI 和假 analyzer 验证未完成片段恢复、已完成原文件不再解码以及显式 finalize。全部 44 个已加载产品模块来自新安装目标，未加载被禁止的模型模块。它不是 TCP 服务、实际 CLI 调用或模型检查，与上述六次真实 CLI 调用分别记录。安装继续复用既有合资格依赖，不是新的依赖齐备环境。

八个步骤均在 CPU 3、nice 19、每步最多 180 秒、1 GiB 采样进程树 RSS、2 GiB 地址空间、64 MiB 持久输出及 32 MiB fixture 上限下执行，没有记录资源违规。观测到的最大采样 RSS 为 87,400,448 字节，最大产品与 fixture 合计占用为 21,696,512 字节；这些仅是短时包检查数据，不代表模型运行或长期资源资格。

安装证据绑定检查时的精确源码。检查后只刷新 README 和本指南的完成文字；115 个包文件、测试、依赖和构建配置与已检验源码保持逐字节一致。最终说明刷新后没有重建 wheel，不能把它描述成另一个从最终文档树重新构建的 wheel。

### 前一 dev14 单文件产品快照：原记录保留

以下各段的“产品树”“本次”“检查后”仅指此前单文件源码快照，其包文件尚未包含当前追加实现；这些历史结果不能当作本后续候选的新回归或安装通过。


截至 2026-10-16，dev14 产品树的选定组合回归在一次运行中通过 252 个测试及 9 个 subtests。该运行覆盖暂存隔离、时间索引、边界、API、生命周期与存储、旧 recording、可选 capture、时长/EOS、page/hour 和 uploader 的 12 个测试文件；这是选定范围，不代表整个仓库的全部测试。

标准离线 wheel 构建与全新 `--target` 安装通过；115 个包文件在源码、wheel 与安装结果之间逐字节一致，版本、依赖元数据及随包许可证/适配记录已核对。默认关闭时的安装包导入、应用构造及两项 CLI 帮助检查通过；没有导入可选 Torch/FrameMN 路径或读取模型权重。另一个安装包单帧用例使用既有假 analyzer fixture，经 TestClient/ASGI API 验证上传与 EOS，已确认全部产品模块来自新安装目标；它不是新建 TCP 服务或模型推理检查。

安装检查复用既有合资格运行时与依赖，并非新建依赖齐备虚拟环境。根目录 MOSS manifest 继续按既有安装方式单独复制，不在 wheel 中。检查后只刷新 README 与本指南的结果文字；包文件、测试、依赖和构建配置均保持已检验字节。

以下是先前功能候选的分批证据，独立保留，不能与本次 252 个测试相加或视为更多独立样本。

- 页面及隔离能力的独立基线为 219 个测试和 9 个 subtests 通过；不是本次重新合并执行的结果，也不与后续批次相加。
- 上传器分阶段完成 33 个用例，包括原有 28 项和追加的 5 项边界用例；另有 45 个已有页面/逻辑小时回归通过。各批次保留自己的检查范围，不能相加宣称覆盖互不重叠。
- 逻辑小时检查实际经 HTTP 分块摄取 57,600,000 个规范化 PCM 帧，得到 13 页、37 个来源和 343 个工作 chunk；工作 PCM 与输入规范化 PCM 精确一致。覆盖背压、丢失接收/封页响应、保存状态重放以及不重复生成工作。
- 小规模检查包括满页精确 EOF、单帧尾部、改变原文件或解码配方的拒绝、网络/进程中断后的恢复，以及超大 HTTP JSON、磁盘 ENOSPC/EDQUOT 后保留前缀并恢复、缺少解码器的拒绝等边界。

逻辑小时使用合成 PCM 与假 analyzer，走真实摄取和普通提交机制。它说明帧守恒、分页、持久化与恢复行为；不说明一小时真实语音的识别准确率、声学接缝质量、ASR/说话人/事件吞吐、完整模型内存峰值或整小时真实模型可靠性。小规模资源保护检查不能替代目标机器的长期资源资格验证。

此前计划 12 小时的长测保留了约 9 小时 20 分钟已接收输入的记录，但原运行句柄未能恢复，没有完整终态和清理确认，仍不能记为 12 小时通过。旧长测、dev13 的短音频模型检查与本次逻辑小时摄取是不同证据，不能合并成已完成的新长测。

## 12 使用时的最小核对清单

1. 输入确已定稿，原文件在状态目录外；保存原文件，不边录边传。
2. 创建新空 legacy session；让 CLI 自己创建新 capture，显式给出规范化帧原点。
3. 两端磁盘均满足下限，服务端 worker 能正常处理；不临时降低安全边界换取通过。
4. 保留同一个 journal 和完整原命令；中断后先对账，以原状态恢复。
5. 区分页 `released`、capture `eos`、CLI `complete` 和 chunk `completed`，分别确认摄取与分析。
6. 多文件任务选择 `--append`，每个原文件使用稳定 ID 并保存全部原文件；只接受连续规范化坐标，不猜测重叠。
7. 未完成输入先按原 ID 恢复；保存尾部、journal 和冻结限额，不能混用 v1 / v2 状态。
8. 全部输入完成后显式 `--finalize`，确认 `capture_finalized`；不要用单文件 `--final` 提前结束仍要延伸的录音。

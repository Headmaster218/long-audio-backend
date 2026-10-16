# dev17：持久 MOSS 结构化历史与 API

适用版本：`0.2.0rc3.dev17`。本版基于 dev16 的显式 MOSS 任务与可选 cell 路径，新增主 SQLite 数据库中的有界历史账本。默认模型选择、依赖与可选执行开关不变；没有自动推理、后台重跑或权重下载。

## 保存的内容与解释边界

通过原有最终验证、正向进程回收及精确 copied-pin 清理后，任务可封存一个不可变历史快照；数据库提交成功才表示已保存。结构化 JSON 的局部说话人标签、`diagnostic_phrases[].raw_text`、`alternative.text` 与模型时间戳保持完整。详情保存原 `SourceSupport`、cell 绑定/证明哈希（如有）、观测到的 profile/model/runtime 标识、规范化 outcome 与清理事实；不保存完整 cell proof，不用哈希重建来源权限。

历史显式标记：

- `authority=historical_snapshot`
- `source_current=not_checked`
- `source_valid_at_commit=not_asserted`
- `retention=durable_until_explicit_session_deletion`
- `expires_at=null`
- `raw_retention=volatile`

它记录已完成的验证观察，不证明独立来源在封存或提交瞬间同时有效。时间戳为模型生成、未经验证且相对原 support；标签仅限本次调用/cell，不是跨录音人物身份。EOS/token 数仍未知，`complete_content=false`，不能当作完整转写、词级对齐或准确率结论，也不改写主转写。

独立 `/raw` 路由提供的完整原始 native raw 文本仍只在原易失槽中保留，最多 128 KiB；TTL、槽复用、取消或重启可能使它不可用。保存的 raw 哈希不能恢复该全文。结构化 JSON 中已含的短语文本仍随历史保存。

## 历史读取

沿用既有 API 身份认证与 session 隔离，成功与错误响应均为 private/no-store/nosniff。配置 Bearer key 时请继续携带它。历史读取不要求启用 MOSS、有效 cell owner、当前 `jobs_epoch` 或模型加载。

```text
GET /sessions/{sid}/moss-jobs/history?after=0&limit=20
GET /sessions/{sid}/moss-jobs/history/{job_id}
GET /sessions/{sid}/moss-jobs/history/{job_id}/result
```

- `after` 是排他的稳定 `archive_seq` 游标，初始为 0；`limit` 为 1–20。按返回的继续游标翻页，不发送 `expected_jobs_epoch`。
- 列表最多 20 行、32 KiB，不读取结果 BLOB。详情至多 16 KiB，结果为精确提交的结构化 JSON，至多 256 KiB。
- accepted、no_result、interrupted 等记录不是成功结果；未提交输出不能通过历史 result 读取。
- 沿用最多两个响应、合计 1.5 MiB 的共享响应预算。响应繁忙或存储不确定会明确失败，不返回未验证的替代内容。
- 原 live `/moss-jobs/{job_id}/result` 与 `/raw` 仍做新鲜来源授权；历史可读不意味着 live 授权恢复。

## 保存失败与精确 storage-only 重试

接受任务前先为一个账本行预留 294,912 字节（288 KiB），再返回 202 或交给 native 执行。202 只表示接受，不代表推理或保存成功。同一执行 epoch 的原幂等键可查回确认不明的接受记录，不会因此再次派发推理；原 64 个 live key/epoch 上限不变。

如果快照写入忙、失败、过晚或提交确认不明，live 摘要报告：

```text
storage_pending=true
durable_result_available=false
result_available=false
```

原槽位保留同一个有界不可变快照并阻止新任务复用；已正向清理的 native permit 释放。不要重新提交推理来替代保存。使用当前进程的 `jobs_epoch`：

```text
POST /sessions/{sid}/moss-jobs/{job_id}/retry-storage
Content-Type: application/json

{"jobs_epoch":"<当前 jobs_epoch>"}
```

每次显式请求只做一次相同字节的保存/已提交确认，不重新校验来源、不获取 native ownership、不运行模型，也不内部循环重试。仍 pending 时返回 503；成功确认后返回 200 并使 `durable_result_available=true`。若此前事务已提交但响应丢失，精确匹配可确认已有结果，不重复行或计费。live 可见性仍受取消、TTL 与来源授权约束。

取消先于封存会阻止快照；封存后的取消、过期或来源撤销使 live 访问失效，但不抹去历史/待保存字节。关机只尝试一次有界保存，失败时仍报告未排空，已完成的 native 清理与 permit 释放不回退。未提交快照仅在当前内存中，进程死亡可使其丢失；重启不会重跑模型或恢复旧执行 key。

## 有限配额与磁盘余量

启动时设置以下 CLI 参数；没有无限模式或在线配额修改 API：

- `--moss-result-max-rows`：默认 65,536；硬上限 1,048,576
- `--moss-result-max-rows-per-session`：默认 16,384；硬上限 262,144
- `--moss-result-max-bytes`：默认 2 GiB；硬上限 64 GiB
- `--moss-result-max-bytes-per-session`：默认 1 GiB；硬上限 16 GiB

session 限额不得超过全局限额，字节限额至少能容纳一个 288 KiB 预留。限额不预分配空间。提交行计费为精确结果字节、元数据字节与固定 16 KiB accounting allowance；终止无结果行保留元数据与该 allowance。它是逻辑计费约定，不是实测 SQLite/index 物理开销。

没有按年龄过期、LRU 淘汰或自动替换。降低配额不删除旧结果；不足时新 distinct 任务收到 `429 result_retention_capacity`。需要显式删除 session，或由操作者在硬上限内提高配置后重启。未知 schema 或局部可检测的计数矛盾会拒绝新接受；看似合理的正数少计不靠普通请求做全表审计，需另行审查。

可选写入是零 SQLite busy wait、单次尝试，接受阶段共享 250 ms 协作预算与最多 50,000 SQL VM steps。每次事务前采样 DB/WAL/SHM 与空闲空间，要求大于配置 reserve 与 5.5 GiB 二者中的较大值，再加 8 MiB 写入 allowance、2 MiB scratch、当前 WAL 逻辑大小，以及启用合资格 cell owner 时的 64 MiB 预留。提交后发现压力仍保留输出，并阻止后续可选写入。

这不是物理分配上界：主库的其他 writer 可以在采样后消耗空间，SQLite/checkpoint/OS 工作也可能超出 allowance。没有硬共享 WAL cap、强制 checkpoint/VACUUM、主写入限制或硬系统调用时限。历史读取与显式 session 删除不要求满足可选写入余量。

## 删除与崩溃后的有限协调

显式 `DELETE /sessions/{sid}` 在 session tombstone 的同一事务中删除历史行及用量计数，并否决之后的保存/重试。取消任务不等于删除 session。已经授权并持有不可变响应的请求可能在删除后完成；应用删除不是法证安全擦除，也不会删除外部备份。

实际实例锁与 ownership fence 成功后，恢复先冻结一个有索引的历史序号边界。启动时最多一批，之后每个 distinct 接受最多一批，每批至多把 32 个遗留 accepted 预留标记为 interrupted，只释放未使用的字节计费；元数据与行数保留。当前实例行和已提交结果不在回收范围。独立提交的恢复批次不会因随后配额拒绝而撤销，确认失败后进度/剩余工作可以未知。

该机制不重放推理，不回收原音频/cell，也不保证所有遗留预留在启动时一次处理完。耐久范围是已测试运行时/文件系统中的普通 SQLite 已提交恢复；没有新增断电、设备故障、Windows 或通用文件系统资格。

## 当前验证状态

冻结功能源码分别通过 90 个专项模型无关用例、257 个选定回归用例及 91 个子测试。早期回归中测试 mock 的构造签名不匹配；修正 fixture 后复跑，产品代码保持不变，失败记录不改记为成功。均使用新建合成状态，包括真实 SQLite reopen、HTTP 历史读取、配额、pending/重试与有限恢复；不访问历史私有数据库，不运行真实 MOSS 模型。

TTL 用例推进测试时钟超过 900 秒，并检查槽复用、raw 易失和重新打开结果库后的历史字节，不靠等待 900 秒或宣称墙钟耐久。

`0.2.0rc3.dev17` 已完成标准离线九阶段包检查：62 个源码包/隐私/CLI 检查通过，wheel 构建、全新 target 安装、包字节核对、执行关闭时的应用构造、历史路由及两个 CLI help 检查通过，92 个安装后模型无关用例通过。安装后范围包括结果库、历史 API、设置，以及两个 TTL/pending 合成管理器用例；没有扩大为全套测试。依赖使用既有合资格运行时，并非从零安装全部依赖。wheel 的 SHA-256 为 `979b733b8113a55ef27cd13965415f97fe6e742f44e23007da6914140a57fa8a`。

安装版在一次公开音频 API 检查中完成主路径与一个真实 MOSS 任务，产出 3 个持久 cell，并精确读回已提交的 5,188 字节结构化历史（9 条诊断短语、0 个 schema 错误）；native 进程清理得到正向确认。原验证程序把服务完成优雅 lifespan 关闭后的 SIGTERM 退出码 `-15` 判为失败，以退出码 1 结束，原失败记录保留。

随后单独进行的同库、模型关闭历史检查通过，没有重跑推理：历史列表、详情与结果均可读取，5,188 字节结果完全一致，原任务/epoch/请求及出处字段保持不变，翻页到末尾为空；关闭后的数据库账本也核对一致。live 列表、详情、result 与 raw 路由均返回 409，不借历史恢复 live 授权。续测在依次确认优雅关闭标记并等待服务退出后接受本次主动 SIGTERM 对应的 `-15`，自身以退出码 0 结束；未观察到本次拥有的遗留子进程、输出读取线程或监测线程。它验证已提交输出在这次普通进程重启后的读取，不抹去原验证程序的失败，也不提供完整 raw 文本的持久保存。

这些检查不能证明真实 MOSS 输出质量、长时执行、Windows、断电或通用文件系统耐久；后续执行结果须单独对应冻结字节。

更完整契约见 [MOSS_HANDY_JOBS.md](MOSS_HANDY_JOBS.md#durable-structured-history) 与 [MOSS_CELL_JOBS.md](MOSS_CELL_JOBS.md#retained-moss-results-from-cells)。dev16 compact 回收仅影响其授权范围，不提供 MOSS 结果归档，也不改变本版显式删除要求。

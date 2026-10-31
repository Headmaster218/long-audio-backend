# dev16 可选证据容量与 compact 回收 API 指南

适用版本为 `0.2.0rc3.dev16`。上传、主转写、cell 分页与显式 MOSS 路由不变；本次增加只读容量状态及默认关闭的 compact 回收。主任务完成、健康检查成功或回收过某一页，都不等于 overlap 内容完整、身份正确或可选证据可无限保留。

## 1. 显式启用

沿用已经合资格的普通启动配置，仅在需要时增加：

```sh
--native-owned-cells --native-owned-core-seams --native-owned-compact-gc
```

`--native-owned-compact-gc` 默认关闭；缺少任一前置开关时 CLI 拒绝启动。Settings 对应 `native_owned_compact_gc_enabled`，默认值为 `False`。该开关不隐式开启旧 seam-cells 或 MOSS，不加载额外模型，也不提高现有限额。

运行环境、SQLite/VFS/文件系统、Nemotron 与普通 worker 的原有兼容和资格检查仍有效。相同依赖版本不代表合资格；不要修改资格记录、直接修改数据库或删除旧 store 来绕过检查。初次启用可选能力仍按原指南使用新的独立数据目录，保留旧数据。仅支持正常应用绑定的一个 `Worker.loop`；直接调用 `Worker.process` 或 capture preparation 不具备此回收保证。缺失或不匹配的 worker/store/capture 绑定不能解释为空闲。

## 2. 读取健康状态

读取既有 `GET /health`，查看 `native_owned_cells`。未启用、注册失败或 owner 不可用时，部分子字段可能不存在；先检查 `ready`、`status`、`cleanup_pending`。不要仅凭配置开关判断能力已可用。读取新增 retention 与 compact_gc 投影只使用固定大小的内存记录，不增加 SQL 或文件扫描。整个 `/health` 端点仍执行既有进程、磁盘与配置就绪检查。

### `native_owned_cells.retention`

- `snapshot_state`、`sample_age_seconds`、`counts_are_last_observed`：区分最近观察、脏状态、过期和不可用。`observed_at_monotonic` 是进程内单调时间，不是日期或跨重启时间戳。
- `occupancy_complete` 只描述该次计数投影；两库分别观察，`consistency` 为 `separate_database_observations`，不代表跨库一致事务或完整引用闭包。
- `cells`、`sources`、`compacts` 含 `occupied`、`limit`、`referenced`、`referenced_is_lower_bound`、`unknown_protected`、`pending`。未观察的计数可为 `null`，不能当作零。
- 所有 retained cells 都是根，包括 `metadata_only_cells` 中已释放 PCM 的 cell。来源及 compact 引用数只是下界，其余未知行受保护。全库 `roots_complete`、`producer_frontier_known` 仍为 `false`，`eligible_orphan` 仍为零。
- `blocked_referenced_capacity` 表示该容量已被可证明的根占满；`optional_unavailable` 可能表示快照不可用或未知根占满，须同时看固定 `reason` 和快照状态。旧计数不能作为新的删除依据。

这里的 `reclamation_enabled: false` 是保守全库投影的属性；实际窄范围 collector 是否启用应读取下面独立的 `compact_gc.enabled`。不能把一次成功回收改写成全库根已完整。

### `native_owned_cells.compact_gc`

- `enabled`、`status`、`reason`：单独报告启用状态及最近一次尝试。`disabled`、`deferred`、`unknown` 或 `no_eligible_compacts` 都不是回收成功。
- `attempts`、`retired`：当前 owner 内存中的累计尝试与已确认回收数，不是跨重启持久统计。
- `last_scanned`、`last_retired`、`last_protected`、`last_body_bytes`：仅代表最近有界页，不能推断整个 store 都可回收或都被引用。
- `uncertain_ids`：待核对的提交结果不确定行数，不暴露 ID。后续只发现这些行已不存在时，`absent_after_unknown_commit` 不会把缺失归因为本次回收，也不会补记成功数。
- `scope` 为 `completed_stream_sources_only`；`max_batch` 为 8，`body_projection_limit` 为 32,768 字节。来源与 cell 上限也独立列出。

成功删除会立即使旧 occupancy 快照变脏，等待后续有界刷新。没有手动强制 GC 的 HTTP 路由；不要重复上传来触发维护或解决未知提交状态。

## 3. 回收边界与等待

只有已完成、属于已验证连续流的来源 chunk 才可能符合条件。同一范围必须有另一个位置更后的已完成来源提供精确最大持久 compact frontier；成员关系、坐标、完成 offset、来源摘要与提交字节都会复核。当前最新前驱、capture tail、provisional、待发布 proof/flight、pins、未释放 recording receipt 与 retained-cell 来源依赖均受到保护。任一 cell 引用来源时，保守地保留该来源的全部 compacts；proof 左右两侧及 metadata-only cell 不能漏掉。

维护沿用 owner 的单一工作槽，普通交付与协调优先。主库 `BEGIN IMMEDIATE` 排除下一次 claim；已 running 的行覆盖普通 claim 到 `current_chunk` 赋值的间隙，当前 worker chunk 覆盖完成后尚未发布的间隙。如果 API 恰在 claim 后、赋值前暂停该行，还依赖现有 `Worker.loop` 在 preparation 前的中断检查和最新前驱保护；单靠两个“空闲”检查不足以解释该子场景。

一次最多检查 8 个索引候选和 8 个 frontier body，全部 body 读取合计不超过 32 KiB，并计入原有 256 KiB 投影预算。2 秒 owner 工作期限、150,000 SQL 步数不变；SQLite busy timeout 为每次锁等待 5 ms，不是整次尝试仅耗时 5 ms。尝试从上次完成时刻至少间隔 1 秒，普通工作或未知根可使其继续推迟。进行中的查询可占用新请求的一部分期限，这是协作式预算，不是硬抢占或零延迟承诺。

删除仅涉及主库中精确匹配 ID 和字节的 compact 行，在一个主库事务内执行，不增加一般写重试。来源回执、cells、PCM、outbox、主结果和 MOSS 结果不属于回收目标，不新增按年龄删除或 `VACUUM`。

## 4. 容量与结果保存

实例级上限仍为 4,096 来源回执、1,024 cells、2,056 compacts；最多 4 个 pending 字段，每个最多 128 KiB。安静的已完成连续片段可能释放 compact 空间，但仍在处理的长上传和独立上传不适用。来源与 cell 的保留上限没有因此消失。

每个完整 10.5 秒 chunk 各占一个来源及 compact 时，上述来源容量换算为 11 小时 56 分 48 秒，未回收 compact 容量为 5 小时 59 分 48 秒。部分片段、cells 和字节预算可能先耗尽，不构成 48 小时可选证据保证。

来源容量 gap 可见 `source_receipt_capacity`；从 `GET /chunks/{chunk_id}` 的 `metadata.native_core_seam.status` 可读取 `core_receipt_row_capacity`。主工作可继续完成，但这些可选 gap 必须单独保留和展示。完整引用占满时不删除有用历史腾空间，也不通过提高限额掩盖失败。

MOSS 结果仍由易失执行管理器保存：TTL 到期或下一任务接受后占用槽位，都可能清理先前 result/raw。回收 compact 不会使已接受的结构化文本与 provenance 自动持久化。若业务需要长期保存，仍须另行实现并验证有界归档；proof hash 本身不是完整 proof，也不是内容、身份或准确率证明。

## 5. 已检查范围

2026-10-16 冻结 collector 源码通过 73 个专项用例，以及后续 280 个选定回归用例和 44 个子测试。覆盖实际计数上限、claim 竞争、暂停间隙、proof 两侧、未知提交、默认关闭及保留前驱后生成未来 seam。两次早期 fixture 失败（CLI 合成前提配置、公开视图私有路径查找）分别保留，修正后成功运行不覆盖它们。

专项耗时 19.99 秒，采样 RSS 峰值 96.2 MB、聚合存储 40.4 MB；回归耗时 84.45 秒，分别为 144.7 MB、130.0 MB，均为十进制近似值。原进程均已等待退出，未观察到剩余自有进程。采样值不是硬峰值上界。

这些测试仅用新建合成状态、模拟存储资格和假声学结果，不接触历史/用户数据库，也没有模型推理、一般 VFS/断电耐久或长时声学质量结论。dev16 副本只在其后修改版本与文档。

后续九阶段包检查通过 62 个源码打包/CLI 用例、标准离线 wheel 构建、全新 target 安装与 7 个已安装包用例（含未来 seam）。全部 117 个包文件与源码、wheel、安装目标逐字节一致；默认关闭的导入与 worker 关闭的应用构造、后端及上传器 CLI 帮助均通过。默认关闭探针未构造 analyzer 或启动服务，且无 Torch 或模型/checkpoint 读取；全部包检查均未加载真实模型。安装复用既有合资格依赖，不是全新依赖齐备环境。各包检查阶段最大采样 RSS 为 116,674,560 字节，最大采样聚合产物为 23,908,352 字节；这不证明硬峰值或长时运行表现。

此次补充结果在执行后只刷新三份文档，产品包文件、测试与构建配置保持已检验字节，未因此重建 wheel。英文细节见 [OPTIONAL_RETENTION](OPTIONAL_RETENTION.md)；上传器操作见[既有指南](HOUR_FILE_UPLOADER.md)，其历史执行结果不改记为 dev16 验收。

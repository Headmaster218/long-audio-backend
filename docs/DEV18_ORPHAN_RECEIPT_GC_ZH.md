# dev18 孤立来源回执回收实用指南

适用 `0.2.0rc3.dev18`。本功能释放有限的可选来源回执槽位，不提高容量、不补造证据、不改变转写。主任务完成不表示 overlap 完整、身份正确或可选历史无限可用。

## 启用与观察

在已经合资格的正常启动配置中按需增加以下开关；它们不代替原有模型、运行时、SQLite/VFS 与普通 worker 资格检查：

```sh
--native-owned-cells --native-owned-core-seams \
--native-owned-compact-gc --native-owned-receipt-gc
```

新开关默认关闭，Settings 对应 `native_owned_receipt_gc_enabled=False`。缺少 compact GC 时拒绝配置；compact GC 仍要求 cells 与 core-seams。只支持应用绑定的单个正常 `Worker.loop`，不新增模型、后台全库扫描、手动强制 GC 路由或自动迁移。初次采用可选 store 时遵循既有部署指南，保留原数据，不改资格记录或直接删库绕过保护。

读取 `GET /health` 的 `native_owned_cells`：

- 先确认 `ready`、`status` 与 `cleanup_pending`；仅有配置开关不代表维护可用。
- `receipt_gc.enabled/status/reason` 描述最近回收状态。`disabled`、`deferred`、`unknown`、`no_eligible_receipts` 不等于已经释放空间。
- `attempts/retired` 是当前进程内累计值；`last_scanned/last_retired/last_protected/last_body_bytes/last_prefix_rows` 仅描述最近有界尝试。重启后重新计数。
- `uncertain_ids` 表示待核对的提交不确定行数，不暴露具体 ID。事后发现行已不存在不补记为成功回收。
- `scope` 为 `orphan_completed_sealed_page_receipts_only`；`max_batch=8`、`prefix_row_limit=28`、`prefix_stream_limit=8`。
- 继续分别查看 `compact_gc` 与 `retention`。计数是最后观察值，可能为脏、过期或不可用；来源引用数仍只是下界，不能据一次回收推断全库根完整。

新健康投影只读固定大小内存，不增加 SQL 或文件扫描；整个 `/health` 仍包含原有检查。成功回收会使 occupancy 变脏，待后续有界刷新。SQLite 空页可复用，不承诺文件立即缩小，也不运行 `VACUUM`。

## 删除的范围与不会自动解决的问题

只删除可选 store 内精确匹配的孤立来源回执及对应内存缓存。来源须属于已发布、已释放 sealed page，主工作已完成，连续前缀及当前生产者上下文都能证明安全。前缀从可靠起点逐页建立，缺口或未知状态不能跳过；重新打开会先保守重建。

所有保留 cell（包括已释放 PCM 的 metadata-only cell）、依赖、剩余 compact、待完成 proof、pins、发布中的引用、生产者尾部、当前前驱和未来 replay 所需来源均受保护。独立上传、未释放页、仍在处理或无法证明的来源不因此可回收。当前 epoch 起点之前的 replay 区间及跨边界来源仍保留，不能用最新 frontier 冒充完整 replay 下界。

cells、PCM、recording outbox、主转写/事件/说话人结果及 dev17 已提交 MOSS 结构化历史均不删除。MOSS 的完整独立 raw 文本仍是易失状态，参见 [dev17 指南](DEV17_DURABLE_MOSS_API_ZH.md)。

4,096 回执、1,024 cells、2,056 compacts 上限不变。有实际引用的历史可占满容量，前台繁忙也可能使维护长期延后。已有终态 outbox 中的 `source_receipt_capacity` 或其他可选 gap 不会因后来有空位而自动修复；主工作成功不能抹去这些缺口。不要重复上传或直接改数据库来强迫回收。

## 维护预算与压力检查

沿用单一 owner 工作槽，普通交付及协调优先。每次最多检查/回收 8 个候选、推进 28 行前缀，最多缓存 8 个流前缀；body 读取上限 32 KiB，计入既有 256 KiB 投影预算，2 秒协作式期限和 150,000 SQL 步数不变。compact/receipt 一对完成后至少留 1 秒调度间隔；查询非硬抢占，不能承诺零延迟。

2026-10-16，冻结功能源码分别通过 72 个专项用例、574 个选定回归用例及 38 个子测试。加速压力检查另分三次运行，不与这些计数相加：

| 合成范围 | 页 | 来源 / compact 接纳 | 最高回执 / compact | 末尾回执 / compact | 实际 GC + 刷新调用 |
| --- | ---: | ---: | ---: | ---: | ---: |
| pilot（49 分钟） | 10 | 280 / 288 | 82 / 30 | 33 / 1 | 133 + 80 = 213 |
| 逻辑 12 小时 | 147 | 4,115 / 4,238 | 322 / 30 | 3 / 1 | 1,997 + 1,176 = 3,173 |
| 逻辑 48 小时 | 588 | 16,458 / 16,951 | 1,119 / 30 | 3 / 1 | 8,036 + 4,704 = 12,740 |

每页是 **8 对机会**，即最多 16 次 GC 调度机会，另加 8 次容量刷新；部分 compact 调度因只剩受保护尾部而拒绝，不能把机会数当实际调用数。48 小时样例共有 9,408 次 GC 机会、1,372 次 compact 占用拒绝。实际 GC 加刷新为每逻辑小时 265.417 次。

模拟空闲额度下界：pilot 每小时 98.604 秒、12 小时样例 98.785 秒、48 小时样例 98.709 秒。下界包括完成后的秒级间隔和测得维护工作，**不包括 owner 唤醒/交接延迟**，启动 sweep 另计；所有机会都依赖前台真正空闲。48 小时样例测得维护约 0.710 秒/逻辑小时，测试主体 115.389 秒，进程峰值 RSS 61.65 MB，已分配元数据 71.85 MB（十进制）。这些是本次观测，不是资源硬上界或真实吞吐保证。

48 小时样例在第 294 页重新打开可选 owner，原有持久行未改变，前缀缓存清空；下一页只证明到第 7 页，到第 338 页追上当前前缀。保守积压过程中最高 1,119 回执，最终回到 3 条。该结果不意味着任意重启或繁忙负载都能在固定时限内追上。

压力用例完全没有源 PCM，不解码、不加载模型。合成 `receipt_valid` 同时替代来源文件观察与其主库行复验，页发布和空闲时钟也由 fixture 模拟；生产回执 SQL 接纳、compact 接纳、调度条件、预算、collector 及有界重启 sweep 仍实际执行。未取得真实长时语音、全语音高引用负载、48 小时墙钟运行、一般文件系统或断电耐久资格。

## 安装包检查

标准离线 wheel 构建和全新 `--target` 安装通过。全部 118 个产品包文件与源码、wheel 和安装目标逐字节一致；依赖声明、第三方许可证和外部模型清单保持不变。默认关闭导入及 worker 关闭的应用构造通过，64 个路由包含可读历史路由，未构造 analyzer、启动服务或读取模型。CLI 帮助、新开关缺少 compact GC、compact GC 缺少 cells/core-seams 三项检查均符合预期。

另一个安装包模型无关用例通过：在新建合成来源/数据库中执行孤立回执回收，并逐字节确认历史结果、cells 与 PCM 未被改变。它使用合成 PCM 和模拟资格，不是真实语音或模型验收，也不是把前述纯元数据压力用例重新运行一遍。

安装复用既有合资格 Python/依赖，没有下载依赖或模型；这不是全新依赖齐备环境验收。三个包检查步骤最大采样 RSS 为 72.43 MB、最大采样聚合产物为 31.70 MB（十进制），不构成硬峰值保证。完成后仅补充本指南与 README，118 个包文件、测试及构建配置保持检查时字节，未为此次文档补充重建 wheel。

## 本地复现纯元数据用例

先准备兼容 Python 3.12 和已安装的项目测试依赖，不下载模型。从源码根目录执行；可先只运行 pilot：

```sh
mkdir -p evaluation/reports
PYTHONPATH=.:tests PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -B -m pytest \
  -q -p no:cacheprovider --import-mode=importlib \
  'evaluation/orphan-receipt-gc-20261016/test_metadata_stress.py::test_accelerated_metadata_retention[pilot]'
```

将参数 `[pilot]` 换成 `[12h]` 或 `[48h]` 可分别运行更长的逻辑元数据范围。保留 `tests` 在导入路径上，因为该文件复用现有 `test_native_owned_receipt_gc`、compact GC 与支持 fixtures。文件会向其父目录的 `reports` 写入结果，即 `evaluation/reports/metadata-stress-r1-<case>.json`；已有同名报告时主动拒绝覆盖。需要复跑时使用新的源码工作副本并保留旧证据。

复用文件 SHA-256：`e71355cbbbd66919a43c06971069d0126c7cb50d53ed16a68727ceac0e12c95b`。脚本内 fixture 产物上限为 240 MiB，不能把本次实测 71.85 MB 当作任意环境的硬上限；按资源预算串行运行并保留充足空间。它是测试用例，不是服务启动步骤。

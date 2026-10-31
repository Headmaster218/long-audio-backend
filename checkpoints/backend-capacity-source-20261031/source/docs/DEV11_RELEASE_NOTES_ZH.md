# 0.2.0rc3.dev11：同一 epoch 的相邻 core 诊断

日期：2026-10-15。基于 dev10 分页修正后的开发源码。默认行为、依赖、模型选择、主转写与显式 MOSS 诊断的分工不变。本产品源码已完成下述选定回归、标准离线 wheel 构建、隔离安装及 import/CLI 检查；未对该 wheel 重跑 API 或真实模型。

## 适用范围

新增 opt-in `--native-owned-core-seams`，要求 `--native-owned-cells`。同一 capture run、同一 reset 和 native epoch 内，两个相邻且已提交的 core 可生成 `native-owned-core-seam-v3` cell。既支持单次上传的内部 core 边界，也支持来源映射连续的相邻上传；必须满足精确 PCM、来源 receipt、不可变提交记录和同一次当前 native 响应的校验。

support 以边界为中心，通常各取 10 秒，受两个 core 的已提交范围约束。同一当前响应须完整覆盖 support，并在其中观察到不同 epoch-local 标签的正时长重叠。选择规则不要求被选中的那一对重叠本身跨越 core 边界，不能把「support 跨边界」解释成「选中的重叠一定跨边界」。这是替代诊断覆盖：`adds_primary_audio=false`，`automatic_dispatch=false`。

## 新表、容量与恢复

- 开启后新增空的 `native_core_commits` 表；不从历史 chunk metadata 回填，不认领旧 witness 或旧可选 store。可选 store 使用独立 v3 策略；旧策略目录不能原地当作 v3 使用。推荐使用独立新数据目录，保留旧数据和备份。
- 全库最多保留 2,056 条 compact commit，每条 body 最多 2 KiB。达到硬上限后拒绝新增可选记录，不以自动删旧记录来扩大窗口。
- 同时最多 4 条 pending；表中每条 pending 字段上限 128 KiB，但实际接纳的 canonical proof 仍受既有 64 KiB 上限限制。这些是记录限额，不是整个数据库或进程的内存承诺。
- compact 与 pending 在主提交事务中的独立 savepoint 内写入。预期可选容量或证据错误只撤销该可选 savepoint，主结果仍可提交；真正打断整个事务的异常仍须整体回滚，不能被写成主提交成功。
- owner busy 时保留有界 pending，后续 drain 可重试。相同 slot 与证据不会重复建立提交；不同证据不会覆盖原提交。owner 已提交、尚未收到确认的重放复用相同 cell；撤销、取消或来源变化后仍须失效，不能凭 pending 复活。
- 单操作 2 秒、150,000 SQL VM 步、256 KiB SQL 投影、40 MiB 来源读取，以及既有 owner 存储与资源上限均未提高。分页仍可能因单项成本、最终复核增长或 deadline 而明确拒绝。

## publication 策略的有限变化

既有 owner `_insert_cell`、`_transaction`、数据库 schema 及其 SQL 保持不变；新增主表和其 SQL 是本功能自身的明确增量。`_publish` 的提交后判断有新策略：开启 core seams 时，已验证的旧 v1 metadata 自然推进不再全局撤销独立 v3 工作，但该 v1 publication 仍返回 `status=unknown`、`reason=primary_commit_advanced_after_publication`，不能把未知结果记成 durable。真正来源失效和验证异常仍触发撤销。

历史 writer/source 等价与有限存储证据不覆盖这个新的 `_publish` 策略。新增专项分别检查自然推进、来源失效、取消、提交后撤销与确认竞态；不将旧的等价结论扩大为新策略的全部证明。生产 runtime registry 和二进制验证器未修改，也没有新增资格绕过开关。

## 已有证据与本版本状态

对应功能源码在受资格约束的实际 runtime 上通过 35 个 model-free 专项；另有 180 个既有选定测试和 103 个 subtests 通过。专项使用真实 Store、Worker 与 CellStore，但 native turns 和 ASR 文本为合成数据，不运行模型。覆盖相邻 core、同/跨上传、容量、重启重试、commit-before-ack、撤销及新 publication 策略。选定测试不等于整仓回归。

本产品树保留这 35 个测试的行为和断言，并复用既有 `simulated_profile` 作为测试专用资格 fixture，去除独立 checkout 路径与 source-pin 脚手架；同时保留基线 helper 的 4 个模型无关用例。这 39 个用例已全部在下述首轮运行通过。模拟 profile 不证明 runtime/VFS、分配边界或断电耐久性，不能作为生产资格。既有生产 AdmissionTests 保持独立。

### 产品树的选定回归

首轮共通过 217 个测试和 99 个 subtests，但有两个未修改的 runtime AdmissionTests 失败，命令退出码为 1。原因是运行命令未显式设置 `TMPDIR`，默认临时目录位于不匹配已发布资格的文件系统；生产资格门控拒绝了该环境。失败记录保留，不能记成首轮全通过。

随后只将这两个原测试方法的临时目录设在已具资格的文件系统，单独重跑通过 2 个测试和 4 个 subtests，退出码 0。没有修改产品源码、测试断言、资格 registry 或二进制验证器。两次运行的通过覆盖并集为 219 个选定测试及 103 个 subtests，其中新增/基线 39 个、既有 180 个；不是一次连续全部通过，也不是整仓回归。原功能源码的 35-case 资格 runtime 结果与这些可移植测试不重复累计。

### wheel、安装与 import/CLI

标准离线 `pip wheel` 构建与独立 target 安装成功，原命令退出码 0；随后在已具资格的 runtime 上完成 import、版本与 CLI 检查，退出码 0。包元数据与 `audio_backend.__version__` 都是 `0.2.0rc3.dev11`；103 个包文件在源码、wheel 与安装 target 间逐字节一致。

wheel 大小为 500,073 字节，SHA-256：`fdc99880f7fb55fcbb73808c9bc04ac909b8c98445698015206709c9de512a1d`。import 进程自身峰值 RSS 为 52,809,728 字节；构建全过程峰值 RSS 未测量，不能用 import 数值代替，更不是模型内存上限。

该 wheel 的源码快照早于本次 README 与发布说明更新；后续仅改动这两份文档，103 个包文件保持原字节。没有因此重新构建，也没有对已安装 wheel 发起 API、主模型或 MOSS 运行。以下真实短例来自此前字节相同的功能实现，不应写成该安装包的 HTTP 或模型验收。

### 此前真实短例

另有一次真实 40.5 秒 AMI 单上传：30 秒内部边界形成精确 20–40 秒 support，完成持久 v3 proof 校验、一次真实 MOSS 调用及 result/raw 绑定核验。总耗时 45.79 秒，采样进程树 RSS 峰值为 1,879,097,344 字节（约 1.879 GB）；采样峰值不是瞬时硬上限。服务收到 SIGTERM 后已明确等待并回收，结束时没有已知残留子进程，MOSS 清理有正向确认。

该例右 core 是 EOF-final。被选中的 native 重叠对位于 seam 之前；同一次当前响应另有不同标签在约 29.401–30.509 秒跨过 30 秒边界的重叠。不能把两项事实混为同一被选中对。过程中仍记录一次旧 v2 `seam_prepare` 不可用诊断；最终 v3 已发布、dropped=0、pending=false，不代表全过程没有诊断或历史失败。主成功、可选结果与清理各自判读。

## 仍未验收

- 跨 300 秒 reset 的组合明确不支持；300 秒边界与一小时连续运行的实际验收仍开放。合成拒绝测试不能替代真实长录音。
- 这一个短例不证明非 EOF 右 core、所有 EOS 条件、所有重叠内容、准确率、词时间真值或全局说话人身份。
- 没有新增 Windows、GPU、设备录音或任意新环境资格结论；没有 12–48 小时全语音通过结论。
- MOSS 仍须显式请求，结果是独立诊断，不自动修复或替换主文本。

操作步骤见 [dev11 API 补充](DEV11_CORE_SEAMS_API_ZH.md)。旧功能、旧安装包及历史失败见 [dev10 说明](DEV10_RELEASE_NOTES_ZH.md)，不能与本版本证据合并为一次完整验收。

# 0.2.0rc3.dev12：严格绑定的 reset-replay core 诊断

日期：2026-10-15。基于已发布 dev11 源码，合入独立核验后的 reset-replay 增量。产品树已完成下述选定回归、标准离线 wheel 构建、隔离 target 安装及合资格环境中的 import/CLI 检查；候选专项证据单独计数。仍为开发源码，未创建 release/tag，也未对安装后的包重跑 HTTP 或模型。

## 功能与升级

继续显式使用 `--native-owned-cells --native-owned-core-seams`，默认关闭，不增加开关。原同 epoch `native-owned-core-seam-v3` 语义不变；跨 reset 使用独立的 `native-owned-reset-replay-core-seam-v1` schema 和 policy。CLI 仅更新这项能力的帮助文字，参数与默认值不变。

新 proof 只允许同一个 fresh capture run、同一采样坐标和 runtime，两个相邻已提交 core 位于相邻 epoch，观察到的 reset serial 恰好连续，epoch ID 不同，边界精确落在 300 秒 epoch 网格。两侧必须各有完整 10 秒：support 为边界前后各 10 秒。只使用右侧新 epoch 的一次当前响应及其本地标签，验证 replay、已处理范围、精确 PCM 与不可变来源；不混用旧 epoch 标签。准备过程最多追加一次 turns 查询，不 push、不 finish、不触发 ASR。

启用此组合策略时，操作者须主动选择全新的可选 store；最直接的做法是选独立新数据目录，保留原数据目录及其中所有旧 cells。不得静默采用旧 store、删除或覆盖旧 cells、自动回填历史 metadata，也不能只改 policy 字段绕过不匹配。具体启用和 API 见[dev12 操作补充](DEV12_RESET_REPLAY_API_ZH.md)。依赖、模型选择、主转写与 MOSS 路由不变。

## 有界存储与精确重试

- 全库 compact receipt 上限仍为 2,056 条，每条最多 2 KiB；pending 最多 4 条，每条列值上限 128 KiB，实际 canonical proof 仍最多 64 KiB。没有提高限额或新增历史 witness 日志。
- `core_reset_short_side` 表示一侧不足 10 秒，记录可选 `gap`，不保存 pending proof、不发布 cell。缺少 replay、来源变化、非连续 reset serial 等也明确拒绝，不伪造跨界证据。
- 已在主提交事务持久化的精确 pending proof 可继续 drain；owner 已提交但确认丢失时，重放相同 proof 复用同一 cell。取消、来源失效与 generation fencing 仍有效。这不等于任意中断都能恢复 seam。
- 若新 reset 已观察到、但右 core 的主事务尚未持久化 proof，此时 native 重启又推进 reset serial，缺失 transition 无法重建；主任务重试仍可能成功，该 seam 则留下 `core_reset_transition` 诊断缺口，不承诺后补。
- 既有每操作 2 秒、150,000 SQL VM 步、256 KiB SQL 投影、40 MiB 来源读取及 owner 资源限额不变。预期可选错误仅撤销独立 savepoint；真正破坏主事务的错误仍须整体回滚。

## 已有专项证据，分别计数

独立候选的模型无关覆盖为 35 个既有用例、15 个 reset 负例及逻辑 300/3,600 秒边界各 1 例，共 52 个不同用例，分开串行运行。最初负例出现 fixture `KeyError`：测试误从不暴露路径的 `Store.chunk` 读取路径；仅修正 fixture 后复跑通过，原失败记录保留，不能写成一次 52-case 全通过。产品树合入这份修正后的测试源码。

长时间轴用例使用真实 Worker/Store/Capture/CellStore 事务、合成 native/ASR 响应；一小时边界用例推进 122 次主提交和 13 个 epoch。它验证逻辑时间轴，不是一小时 wall-clock、真实模型长时运行或全语音稳定性验收。

另有一次固定 344.5 秒 fixture（合成静音加官方 AMI 音频）的真实模型/API 案例，主结果成功，精确 [290,310) 秒 support 跨越 300 秒；相邻 core 为 [270,300) 与 [300,327)，右查询 `true_eof=false`，右 core 非 EOF-final。补充审计确认同一 fresh capture、连续 epoch/reset、仅新 epoch 标签及来源绑定。一个显式、绑定的 MOSS 调用返回 9 个 phrases、2 个本地标签；result/raw 绑定、进程关闭与清理、最终服务 idle 分别获确认。

重要限制：原 helper 仍是 exit 1、`success=false`，失败来自运行结束后沿用的“最多 16 个 observer 文件”断言。实际保留了完整的 35 个 observer 文件，随后独立补充审计接受该案例的机制与集成证据；这不是原 helper 干净通过，原始失败未被改写。

被选中的 witness pair 重叠为 297.071–297.999 秒，位于边界之前；另一些返回 turns 的重叠为 299.401–300.509 秒。support 跨界、被选中的 pair 与其他 turns 必须分别理解。该案例不证明识别准确率、全局人物身份、通用 EOS 或全部内容完整性。

实际案例峰值采样进程树 RSS 为 1,910,013,952 B，输出占用为 27,881,472 B；最大观察间隔约 RSS 0.506 秒、磁盘 1.019 秒、工作区 10.615 秒。采样没有提供瞬时硬上限证明。没有新环境、GPU/Windows、长时全语音或任意 reset 的通用资格结论。

## 产品回归、构建与历史

冻结的 dev12 产品树完成一次选定 authority/store/API/multicore 回归：184 个测试及 103 个 subtests 通过，exit 0，24.512 秒，峰值采样 RSS 128,638,976 B、输出占用 9,334,784 B。4 个既有 multicore fixtures 的 11 个文件仍保留，不能写成全部清理。它与前述独立候选的 52 个用例分别保留，不是一次 236-case 全通过，也不是完整测试套件或模型验收。

标准离线 wheel 构建、隔离 target 安装及合资格环境中的 target import/CLI 检查均 exit 0；版本、原开关与新帮助文字已检查，103 个包文件与源码、wheel、target 逐字节一致。wheel 为 500,622 B，SHA-256 为 `6c6a3b6aac24cda79ee275fe0815a9ec9859a897554397c438327dbb9b2bdd48`。import 检查峰值 RSS 为 52,334,592 B，构建/安装验证目录占用 10,473,472 B；整个构建过程的峰值 RSS 未测量。

这些检查后只刷新 README 和本版两篇新文档；全部 103 个包文件、测试与 pyproject 保持被验证的原字节。安装后的 HTTP/API、真实模型未重跑，现有真实模型证据仍是前述独立候选案例及其单独补充审计。升级前正常停服并备份，单数据目录单服务进程，保留既有 runtime/SQLite/VFS/文件系统资格要求。

[dev11 发布说明](DEV11_RELEASE_NOTES_ZH.md)和[dev11 API 补充](DEV11_CORE_SEAMS_API_ZH.md)按原文保留，其中“reset 不支持”等描述属于 dev11 历史；dev12 适用本页与新 API 补充的严格范围。旧版本的测试与安装结果不累计成 dev12 验收。

# 有界多 core 支持：实验范围与资格

版本：0.2.0rc3.dev37。此功能实验性、默认关闭，只允许新会话。它保留有效主转写，在已有 native-owned/automatic MOSS 路径上为一个原始重叠目标等待后续 core，再以真实提交和来源证据绑定有界支持区间。它不放宽模型输入、词汇投影或说话人身份规则。

## 启用与有限范围

通过应用的 Settings 显式配置，使用全新的隔离 `data_dir`/store 和新会话。这不是新的命令行开关；已有已提交主工作或既有自动任务的会话不能被改作首次启用的会话。

- `native_owned_bounded_core_support_enabled=True`。
- `moss_jobs_enabled=True`、`moss_automatic_enabled=True`。
- `native_owned_support_enabled=True`、`native_owned_cells_enabled=True`、`native_owned_core_seams_enabled=True`、`lexical_evidence_enabled=True`。
- `native_owned_seam_cells_enabled=False`：automatic 模式不能同时启用旧 seam-cells 路径。
- `diarization_backend='nemotron'`，并为 `native_diarization_model` 和 `native_diarization_library` 提供经过核对的显式本地模型与库路径。
- `inference_device='cpu'`、`overlap_backend='none'`、`start_worker=True`。
- `moss_jobs_model_path` 必须是显式的本地绝对模型路径；其余 MOSS runtime/model 配置必须与实际已核对的外部运行时匹配。

既有部署与运行时前提见 [Automatic MOSS](AUTOMATIC_MOSS.md) 和 [Native diarization setup](NATIVE_DIARIZATION_SETUP.md)。这些配置条件不构成真实 native/MOSS 运行或质量资格；本页下文列出仍未验证的部分。

- 最多四个原始与最终支持的并集 core 根；即使第五个 core 很短也拒绝。支持不超过 20 秒，即 16 kHz 下 320000 帧。
- 保持同一 capture run/reset epoch 的精确来源映射；不推断跨 epoch 或历史查询覆盖。真正的非整数帧来源边界拒绝，内部切点用整数帧计算。
- 目标身份冻结，可保留最多八个原始别名；原始目标不能被后来计划覆盖。等待最多 300 秒或三个后继 core，并受现有排队/保留额度约束；等待不占用 owner/model lease。
- 仅使用当前完整词汇清单的既有分组寻找完整词汇边界；CTC 时间仍是估计。支持调整不是声学边界准确性证明。
- 新 v2 数据采用加法式迁移与独立策略身份；旧数据不回写、不重放、不伪造词汇清单。关闭开关不会重跑历史目标，也不会撤销迁移；旧二进制拒绝已经迁移的 store。

## 证据与失败处理

绑定时把计划中的来源替换为实际持久提交，并核对完整主文本带、原始目标、依赖索引、来源回执、PCM 和修订。发布前、owner 事务内提交前、提交后分别读取新快照；参数化 SQL 比较已经验证的完整 proof/target 字节，并分别计量比较输入和查询投影。提交后的确认失败不代表已提交记录被回滚。

局部累计预算由 `native_owned_bounded_core_support_seconds` 设置，默认 0.75 秒，必须是有限数值且在 0.5–1.0 秒之间。规划上限 0.4 秒、notice 保留 0.02 秒及 SQL 步数/投影字节/内存上限不变；旧路径预算不变。这不是端到端 native 延迟或写锁持有时间上限。原有 SQLite 锁等待仍有自己的单次调用上限。

只有同一次规划读取中由 Budget 回调确认的中断，在回滚与关闭均确认成功后，才作为可选拒绝或延后处理。任意 SQL 错误、未标记中断、清理失败或写提交不确定不能据此降级；纯计算耗尽直到下一 SQL 边界才被发现时仍可能使主事务硬回滚。

## 明确限制

1. 当前 v2 保留使用保守的 store-wide GC fence。任何保留的 v2 目标都可能阻止无关孤儿回收。这只验证第一阶段的根保全；在宣称可长期使用之前，必须证明完整的每目标根记账或有界保留，并能回收无关孤儿。
2. bounded v2 的 recording speaker annotation/linking 尚未通过资格验证；缺少目标元数据的身份描述符会拒绝。文本、结果与统一转写仍可使用任务局部的说话人标签，不据此学习或绑定持久人员身份。
3. 尚未验证真实 native CAPI 对较早支持的当前查询保留范围、真实 TURNS 阻塞延迟、真实 MOSS 推理或识别/声学质量。合成证据不能替代真实跨碎片左侧覆盖或 proper partial-parent 收益。
4. 本版仍依赖外部模型/运行时 manifest 与本地模型，wheel 不是自包含部署包。

## 模型无关验证及未解决项

累计的固定版本串行批次及针对性复核中，144/144 个新用例和 926/927 个既有用例通过，零未执行。它们不是在最终单一 hash 上执行的一次整套测试；重跑和子测试没有重复计入唯一用例数。三个既有测试只做了经过审查的 setup 调整，保留所有原断言：重启丢失确认的准入屏障、worker 静态检查指向实际 loop、owner 的公开 close 确认。

合成 C 路径通过真实 Worker、提交、owner、automatic dispatch、结果与转写读取链，使用 fake native/stub MOSS；检查第三 core 的端到端绑定、实际提交 ID、精确坐标 PCM（包括零值区间）、单一目标任务，以及一次转写响应中的选择与原文字符守恒。四 core 接受由独立用例验证，跨页一致性由相关 transcript 回归覆盖。四根通过、第五根拒绝、保留失败保全、等待/超时/重启、事务中断、迁移隔离、完整依赖与修订变化、共享预算均有有限用例。最终三个 receipt fence 用例还要求旧版对照实际回收 26 个回执，迁移后的两种目标状态保留全部来源与历史。

保留一个明确未解决的既有用例：
`tests/test_native_owned_cell_capture.py::CaptureFixture::test_seam_primary_projection_budget_declines_without_widening_limits`。
它在有意缩小投影预算之前的成功对照阶段耗尽普通 150000 VM-step 上限；候选与同一解释器/SQLite 下的未改动 baseline 都观察到 150100 步。未放宽上限或断言，也没有把这个失败算作通过。因此不声称整套回归全通过。

后续真实短音频试验、部署以及长期回收资格分别需要独立验证。当前证据只支持上述有限、默认关闭的实验路径。

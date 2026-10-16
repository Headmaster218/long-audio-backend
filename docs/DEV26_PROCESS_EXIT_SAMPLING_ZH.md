# dev26：严格资源采样中的已确认进程退出

开发版本 `0.2.0rc3.dev26`，唯一基础为已发布 dev25。仅调整现有进程采样中的一个退出分支；没有新增依赖、schema、store policy、调度器、模型或默认开启项。

## 实际故障与证据边界

dev25 的一次固定混合输入运行在约 194.30 秒失败停止。首次 ownership 诊断为 `observe_primary / selected_rss`、`AdmissionError`，原因 `process_sample_unavailable`，底层类别 `NoSuchProcess`；当时记录 3 个原始 handle、1 个 descendant。Worker 仍存活，主结果已提交 105 秒；API 接收 189 秒，来源发布 157.5 秒，另有 4 个自动候选排队。3 个自动结果已提交，精确 3 次 native 尝试，其中 seam 结果含 `invalid_model_timestamps`。

诊断没有保留具体 PID，不能声称历史涉及某一个原始进程，也不能证明该次是已正常等待的退出。源码定位到成功读取身份后、读取 RSS 时发生消失的路径。进程枚举与随后查询之间确实允许退出；本版本只处理能够独立确认缺席的窄分支，不把这一解释升级为历史现场复现。

该次没有 pre-window 读竞争延后标记；另一次存储诊断是 write/begin 的 `SQLITE_BUSY`，不是 dev25 新增的只读延后分支。原始运行保持失败状态，完整 1,800 秒输入和后续历史重启验收未完成。

## 采样与所有权契约

仅当非调用者进程已通过 create-time、已知身份与枚举 parent 校验，且枚举祖先链能到达服务调用者时，才处理 `memory_info` 的精确 `NoSuchProcess` 类型。随后只做一次新的独立 PID 查询；它也必须以精确 `NoSuchProcess` 结束。任何返回的 PID，包括复用 PID，仍拒绝该采样。`ZombieProcess` 是 `NoSuchProcess` 的子类，不进入这个例外。

确认缺席的行不计入本次 RSS；不会生成伪造的零 RSS 存活行。已选出的其他进程仍逐一采样，包括消失父进程的存活后代。不能归属的存活后代仍阻止可选接纳；不会因父行缺席而忽略它的内存。调用者消失、parent/身份改变、AccessDenied、无法确认的查询、其他阶段消失、采样 deadline 或身份容量耗尽仍 fail-closed。

采样不清除任何原始 handle/child 所有权，不执行 reap，不代替已有的原始 handle 正向 poll/wait 与后代退出检查，也不释放可选 owner。已存在的 sticky 失败 latch 不会被后来一次成功采样解除。原有 250 ms、4,096 行 census、128 个身份以及主/可选执行、清理、发布上限不变；没有内部重试或额外等待。

`confirmed_sample_exits` 是只在完整成功采样后累计、最大为 2³¹−1 的观察计数。`sample_exit_confirmation` 保存第一次成功的固定 phase/reason/count，并在 status 返回副本；没有 PID、路径、命令或异常原文，也不调用外部日志 sink。计数和首次记录只说明采样分支发生，不是 cleanup、原始等待或任务成功凭证。旧的首次失败诊断独立保留。

## 分开的模型无关检查

- 新增专项 40 个通过，包含一个受 stdin 控制的真实 Python 子进程，确定性地在身份/parent 校验后、RSS 读取前退出，并验证原始正向等待。其余身份、复用、拒绝访问、后代、预算与不确定清理情形使用注入观察。
- 五个相关既有模块的回归原始结果是 125 个通过、10 个 subtest 通过和 1 个失败：旧断言期望在 `selected_recheck` 拒绝 PID 复用，新前置检查更早在 `selected_identity` 拒绝，reason 与 sticky latch 不变。
- 仅修正该旧测试的 phase 期望后，完整诊断模块另一次运行 22 个通过、6 个 subtest 通过。生产代码在这些轮次中保持相同。原始失败仍保留；这些结果是分开组合的证据，不是改记为一次全部通过。

运行均正向等待，无已记录的资源违规。通过轮次的新 fixture 已清理，失败轮次记录和 fixture 保留。测试边界为 CPU 3 / nice 19、300 秒、1 GiB pytest-PID 采样 RSS、2 GiB AS、512 MiB fixture；这不是连续进程树峰值保证。没有运行音频模型、访问网络或打开历史运行数据库。

## 安装与未完成范围

沿用标准离线 wheel、fresh target 安装和默认关闭检查，不采用到正在运行的环境。本文冻结时只陈述上述源代码测试；新包装回执另行核对。手动任务、文本后的 probe、有限 cohort/session 公平轮转、未知模型尝试不重放、不确定发布保留 owner 等契约不变。

完整 30 分钟实际运行、端到端吞吐、模型重载成本、有效时间戳、全部重叠恢复和准确率仍未合资格。旧实际失败与各测试轮次保留各自状态。

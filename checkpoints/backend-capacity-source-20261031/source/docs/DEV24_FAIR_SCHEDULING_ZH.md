# dev24：自动文本的公平、非抢占调度

开发版本 `0.2.0rc3.dev24`，唯一基础为已发布 dev23。保留其重启 seam、严格帧切点、存储诊断和 BEGIN 前 claim 退避；本次新增公平交接与首次 ownership 故障诊断。源码与测试不包含用户音频、转写、向量、模型、运行数据库或本地配置，不创建正式 release/tag。

## 调度契约与延迟

- 主请求明确区分 queued、granted waiting 和 executing。到期的自动机会冻结当前最多一个 Worker 加一个 probe 的 cohort；后到主请求留给下一组。当前组正向完成并清理后，先给予一个自动候选机会，再授予下一组，持续主请求不能无限延长同一组。
- Worker 在等待 grant 时不把 chunk 标为 running，不持有 SQLite 事务或 manager 锁；取得许可后重新检查当前可用任务。消失、暂停、取消和锁竞争不会被解释为已执行。
- 自动模式只在已安全提交的 core、回执发布完成且 analyzer 正常清理后交接，使用持久 next_offset 重新 claim。禁用、空队列或全局阻塞时保持原 per-input 路径。每次真实交接可能重载 analyzer 并重放 native 上下文；最后时刻的来源或 admission 变化仍可能造成一次无效重载。
- 现有索引读取最多 256 个 queued/published key、每 session 最多 64 个。快照保持到全部 key 被访问或明确跳过，session 在每次访问后轮转，新到条目留到下一完整轮。捕获的持续合资格 key 在最多 256 次机会内访问，新到 key 在最多 512 次内；这是健康调度机会下的选择界限，不是墙钟或成功推理保证。被阻塞 key 会消耗其本次访问，不占用整个轮。
- 同一被选 key 的发布、精确 ack 与 admission 可以在原 2 秒 publication operation 截止内继续；不重设预算、不循环未知 flight、不重跑 accepted/unknown 模型任务。

已接纳的自动文本 owner 不因后到的 queued 主请求而取消，保留既有 300 秒执行、60 秒清理及有界首次结果发布机会。setup、来源验证和发布仍有各自原上限，360 秒不是完整主请求延迟承诺。已经 granted/executing 的主 owner 始终排斥新可选执行。显式用户取消、shutdown、来源撤销、关闭功能、资源或 deadline 故障仍生效。手动任务策略不变；文本后的说话人 probe 为独立、可让位的 owner。

结果提交或清理不确定时保持 fail-closed。计算好的精确 snapshot 与 owner 在普通存储失败、提交 ack 丢失以及同时取消时保留；只能由既有精确已提交结果读回、正向确认终态，或权威删除/来源 veto 按清理证明解决。Worker 调度 pump 不自动解决这些未知提交，主任务可能持续阻塞到显式恢复，不能声称自动有界恢复。

首次 ownership 故障仅记录固定 phase/reason/exception 分类及已经观测到的数值事实；不记录路径、进程命令、异常原文、用户内容，不新增进程探测。日志 sink 失败不得替换原错误。sticky latch、采样、所有权和清理决策保持原语义；历史 `primary_ownership_uncertain` 的精确原因仍未知。

## 兼容与安装

没有新增依赖、schema、可选 store policy、调度表、后台 worker、重试 ledger 或阈值开关。默认关闭项不变。RAM cohort/快照在重启后从空状态重建，历史 accepted/unknown 不重放。沿用 dev23 的严格可选 store policy；旧 policy 不自动迁移，旧 cells/history 与配对主库保留，降级与已有用户库采用尚未合资格。

沿用 `requirements-runtime-dev9.lock.txt` 与 `requirements-build-dev9.lock.txt`。标准离线 wheel 使用已有合资格依赖、`--no-index --no-deps --no-build-isolation`，再安装到全新 target；不采用到运行中的环境。源码 checkpoint 与 wheel/安装结果分别记录，本文冻结时尚未把后续包装命令记为通过。实际包装回执在 checkpoint 交付中单独核对，第三方许可证与 notices 保留。

## 分开的模型无关资格

1. 调度首次专项：66 个通过、4 个 subtests，通过主处理后，两个新测试误读 compact 字段而失败。原始 exit 1 保留。
2. 仅修正测试 fixture/加强非空完整覆盖后的专项：68 个通过、4 个 subtests。此时 synthetic marker 不在实际 30 秒切点，不能据此单独声称切点 ownership 已验证。
3. 随后只将该 marker 放到真实切点并补齐一个显式 fake 的接口，相关回归：183 个通过、19 个 subtests。63/315 秒假声学输入的完整 compacts 与未交接参考一致，保留 2/3 个 seam（含 reset replay）；事件在帧 480000 两侧拆分，每个 lexical marker 恰好出现一次。synthetic analyzer 进入次数分别从 1 变为 3/11，真实模型重载成本尚未测量。
4. 独立首次 ownership 诊断：42 个通过、10 个 subtests，覆盖模拟进程退出竞态、故障分类、sink 失败和不解除 latch；不证明历史故障原因。

这些轮次有交叉覆盖，不能加总成一次完整套件通过。运行均为有界、模型无关测试；源码、原始失败、修正后的单独终端与正向清理记录分别保存。

## 实际模型观察仍有限

历史固定 42 秒重启案例完成 7 个自动结果；原 helper 因 history 分页假设 exit 1，单独关闭模型的 GET 补充读取全部 7 个结果通过。原始失败没有改记为通过，也未证明重启后说话人 identity 链接成功。

后续混合输入的实际失败分别包括约 112.7 秒的 fractional-frame 拒绝（历史精确值未保存）、约 563.6 秒的结果存储 503（历史底层异常未知），以及约 913.6 秒主 Worker BEGIN 前锁竞争退出。最后一轮两个已验证 MOSS 结果、五个中断，完整 1,800 秒输入未完成。dev23 已记录对应修正和保留证据；本版调度与诊断尚未由新真实模型运行验证，旧失败不能算作本版验收。

30 分钟连续全功能、12–48 小时真实运行、全语音吞吐、Windows/Vulkan、真实设备录音和准确率提升仍未完成资格确认。统一显示、候选终态或主转写进度不等于全部重叠内容恢复或全局人物身份。

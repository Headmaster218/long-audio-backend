# dev25：只读 pre-window 竞争安全延后

开发版本 `0.2.0rc3.dev25`，唯一基础为已发布 dev24。没有新增依赖、schema、store policy、调度表、模型、后台 worker、资源阈值或默认开启项。

## 已观察故障与精确范围

dev24 的一次固定混合输入实际运行在约 272.44 秒以失败结束。API 接收并发布 157.5 秒，主提交到 103.6 秒：起点 94.5 秒的 chunk 已持久提交本地 9.1 秒，随后报告 `ReadError: result_storage_unavailable`。Worker 仍存活，ownership latch 为空。固定日志记录 read/body、OperationalError、SQLite 扩展码 261 `SQLITE_BUSY_RECOVERY`、0 个已计 VM 步和空 budget category，没有观察到 deadline/VM 耗尽。

原始记录没有保存调用栈；源码路径推断指向下一次 `Automatic.before_window` 的只读容量预检查。其 database context 在原有 capacity 异常捕获之外，异常继续到 Worker 的失败处理。此前已提交的 9.1 秒没有回滚；不是不确定主提交被重试。新版本增加固定 callsite 标记，以便后续把该类延后直接定位到 before_window。

该次实际已有 7 个已提交自动结果、精确 7 次 native 尝试，没有观测到 interrupted/unknown；其中一个结果为 `invalid_model_timestamps`。提交数不等于 7 个有效时间结果，也不等于质量或完整恢复验收。原始整体失败保留，完整 1,800 秒计划未完成，没有用后续源代码修正改记为通过。

## 修正契约

只读 connection 的 begin/body 阶段遇到数值 BUSY/LOCKED（含扩展码）时，保持原固定字段存储诊断，并在 connection 正常关闭后产生私有 ReadError 子类；公开 503/reason 映射不变。只读 BEGIN 已建立 read transaction 并不等于写事务。close 失败不得被视为安全延后。

只有 before_window 将该信号转换为明确的 preflight-deferred 状态。Worker 在任何下一 native 准备、analyzer 调用或 core commit 之前，沿用现有条件 requeue，使持久 offset、已提交主结果和 pending proof 不变。正常退出 process、释放已证实清理的许可后，用 stop-event 等待 100 ms，再从现有队列重新检查。该轮不把未知预检查当作空闲，不调用可选 dispatch；不跳过 overlap，不延续未知模型尝试。

日志只有固定 operation、callsite、phase、reason 和数值 SQLite code；没有 SQL、路径、进程命令或异常原文。sink 失败不会替换原结果。非竞争错误、deadline/VM 上限、I/O、数据库替换、schema 错误、写入及提交 ack 不明均保持原失败语义。250 ms / 50,000 VM 步、busy timeout、执行/清理和 fair scheduling 上限不变，没有内部 SQL retry。

重复竞争可能继续推迟进度；本修正不承诺有限墙钟恢复时间。dev24 的不确定文本发布/清理仍保持 fail-closed，需既有显式恢复；本修正不能解除该 owner 或 sticky ownership latch。手动任务、文本后的 probe、公平 cohort/候选轮转和未知 accepted 不重放策略不变。

## 分开的模型无关资格

- 36 个专项通过：新建真实 SQLite 的 BUSY、shared-cache LOCKED、before_window 调用点与连接关闭；明确注入的扩展码 261、写入/丢失 commit ack、非竞争/预算/关闭失败和坏日志 sink；真实 Worker loop、正常 process 清理、100 ms stop-aware backoff、原 offset 恢复和恰好一次继续。
- 另一次六个相关既有模块回归 194 个通过，包含结果存储/只读 API、自动队列、事务、fair handoff 和 core 连续性。

两次均正向等待、无记录的资源违规，新的 fixture 已清理。运行边界为 CPU 3 / nice 19、300 秒、1 GiB pytest-PID 采样 RSS、2 GiB AS、512 MiB fixture；这些限制不是连续进程树峰值保证。Worker 专项的 acoustics、capture 和候选 plan 是明确的合成替代，不能声称真实模型或原失败现场重演。没有打开或改动已保存运行数据库。

## 安装与仍未完成的范围

沿用既有合资格依赖与标准离线 wheel / fresh target 安装方式，不采用到正在运行的环境；许可证、固定依赖和默认关闭检查沿用原契约。本文冻结时仅陈述源码测试，后续 package 回执单独核对，不据此预先宣布包装或真实模型通过。

完整 30 分钟实际运行、长时吞吐、重载成本、全部重叠恢复、有效模型时间戳与准确率仍未合资格。旧实际失败、旧专项 fixture 失败和分别通过的测试轮次保持各自状态，不合并为一次完整套件或模型验收。

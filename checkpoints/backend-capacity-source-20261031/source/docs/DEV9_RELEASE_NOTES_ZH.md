# 0.2.0rc3.dev9 发布说明

日期：2026-10-15。基于 dev8 产品源码，增加有限 recording batch 主路径，并合并 native owner 预算、诊断、持久 receipt outbox 与有界只读 BUSY 重试。

## 本版变化

1. 新增 recordings 的原始来源上传、状态查询、finish 和 pending cancel API。仅在一个显式共享采样坐标域内，对 finalized batch 的精确相等 PCM 去重；原始编码字节、回执与逐区间来源映射保留。
2. 去重并集切成相邻 10.5 秒普通工作片段，末片可较短，交给原有 Worker、主转写和 native capture。录音批次与普通上传不混用同一 session。
3. 观察到的 native receipt 与不可领取的 staging 工作、映射和 manifest 在主数据库中原子保存。既有维护循环逐项交付，pending 可恢复；每项 owner 操作仍使用原有两秒预算，批次交付不要求两秒内全部完成。
4. 修正 owner 预算使用及精确已提交行的确认；异常诊断仅记录固定阶段、异常类别、SQLite 代码和有界计数，不记录 SQL、异常消息、音频、转写或文件路径。
5. PrimaryAuthority 的六类只读快照仅对 SQLite `BUSY`（5）和 `BUSY_RECOVERY`（261）重试，最多三次、两次 5 ms 退避；共享原预算、计数和身份校验。写入、提交、推理和 capture 不重放。`LOCKED`、`BUSY_SNAPSHOT`、其他错误及预算耗尽不重试。
6. 增加可安装的 dev9 版本标识、固定依赖锁、普通安装/启动及中文 API 文档。功能和专项测试源码与已测候选相同；版本标识从 `0.2.0rc3.dev8` 改为 `0.2.0rc3.dev9`，属于包装元数据变化。

## 当前固定用例通过

- 41 个专项用例、12 个既有机制回归通过，测试运行器自检通过。专项包含精确别名、冲突/未知位置、finish 重试、原子 staging、收据等待/重试/恢复、取消/删除隔离、owner generation 变化，以及有界只读竞争。模型-free fixtures 的 native 资格为显式模拟；机制测试不等于模型质量测试。
- `BUSY_RECOVERY` 的特定错误码用注入覆盖；真实 rollback-journal `BUSY` 的释放和耗尽另有测试。这不等于在真实 WAL 中稳定重现了 RECOVERY 错误。
- 一次真实 API 固定用例使用官方 AMI ES2002a Array1-01 的 510.5–531.5 秒片段。三份有重叠的原始上传累计 27 秒，另做一次精确重试，得到 21 秒精确唯一 PCM、两个 10.5 秒工作片段和 16 个主 segments。保存结果的独立复核确认两个 receipt 各完成一次持久交付、3 个 durable cell、dropped=0，以及一次真实自然 seam MOSS dispatch 和 `returned_diagnostic`；result/raw 均为 HTTP 200，包含 9 条诊断短句和 2 个模型局部标签，已知子进程清理得到明确确认。
- 该次驱动完成耗时 40.647 秒，采样进程树 RSS 峰值 1,860,403,200 字节。采样峰值不是瞬时硬上限；服务进程按测试结束流程收到终止信号，不将其信号码写成自然退出成功。
- 保存摘要仍含 `last_capture_reason=publication_pending` 历史标记。应与本次最终 receipt/cell 状态一起保留，不能把该标记单独解释为所有回执仍未完成。

上述实际运行发生于包版本元数据更新之前。dev9 功能源码保持相同，未因版本字符串变化重跑模型。保存结果已通过独立复核；实际只读重试次数未知，日志未出现失败诊断，不能据此声称真实运行一定触发了重试。这些事实支持此固定路径的功能结果，不构成全面验收。

## 历史失败与当前结果分开

早期 recording batch 已完成精确主路径，但第二个 native receipt 遇到 busy，optional dropped=1，未运行 MOSS。之后的 receipt outbox 版本完成两个 receipt，却在 capture 的只读主数据库访问中遇到 `SQLITE_BUSY_RECOVERY`，使 seam 无效，完整集成仍失败。

本版加入有界只读重试后的上述固定用例完成主路径、两项 receipt 和一个自然 MOSS 诊断。它说明当前用例已通过，不能反写早期失败为成功，也不能推导所有并发/文件系统/恢复场景均已解决。

## 不在本版结论内

没有准确率/CER、词时间真值、全局人物身份、MOSS 内容完整性或所有同时说话者恢复保证。没有未知坐标通用对齐、近似 PCM 合并、连续 sealed-prefix、来源专属 undo、48 小时耐久、Windows/GPU 或任意部署环境资格结论。MOSS 输出始终是独立诊断。

依赖锁与模型来源见 [安装指南](INSTALL_DEV9_ZH.md)；升级、恢复与回退必须遵守其中的数据保留要求。既有历史文档保留原状，本说明仅描述 dev9 的新增行为与验证边界。

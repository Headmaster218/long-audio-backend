# dev12：双上传的一小时边界回归

## 结果与版本范围

2026-10-15，新增的模型无关用例单次运行通过：**1 passed**，退出码 0。
它补上“真实走完逻辑一小时，并在同一位置同时跨越 native reset 和上传边界”
这一组合路径。该结果不是全部测试套件的重跑，也不是模型质量验收。

基线为 [Headmaster218/long-audio-backend](https://github.com/Headmaster218/long-audio-backend)
的 `development/rc3-dev12-reset-replay` 分支，父提交
`6b05829d4e41ffd3dac0ff0685a53f836171b32c`。
本次仅新增测试与本文档，不修改产品运行时代码、版本、策略或资源限制。

测试入口：
`tests/test_two_upload_reset.py::test_hour_reset_across_two_uploads_then_owned_restart_replays_exact_proof`。
复用现有 `StreamedHarness`、真实事务与清理 fixture；native 响应和 ASR 内容为合成数据，
运行资格使用现有测试专用 `simulated_profile`。

## 实际覆盖

- 从帧零顺序处理 **3,600 秒 + 44.5 秒**，共 3,644.5 秒；仅生成一次。
  两个物理上传属于同一 session / stream，第一段 `stream_final=false`，第二段为 `true`。
  **两段均在开始处理前注册**。第二段只有 44.5 秒，不能表述为两个完整小时，
  也未覆盖第二段延迟到达或 `waiting_input` 恢复。
- **122 个主提交**精确、不重叠地覆盖全部逻辑时间；四个跨边界的合成双说话人标记
  各出现一次，没有重复标记，归属上传和 epoch 标签正确。
- 观察到 **13 个 native epochs**。3,600 秒边界两侧的 epoch index 为 11/12，
  reset serial 为 12/13，属于同一次原始 Capture；诊断查询只使用右侧新 epoch 的响应及标签。
  右侧主 core 是逻辑区间 `[3600,3630)`，其上传本地坐标为 `[0,30)`，
  生成证据时尚未到真正 EOF。
- 诊断支持严格为 **`[3590,3610)`**。两个不可变依赖对应不同上传、receipt、摘要及物理文件；
  第一段读取来源本地 `[3590,3600)`，第二段读取 `[0,10)`，逐段核对 PCM。
  该诊断不增加主音频，不自动触发后续分离任务。
- 使用真实 Store、Worker、Capture、coordinator、来源 receipt、主事务、outbox 和 CellStore。
  原始音频生成与哈希读取均使用不超过 64 KiB 的块，没有合成前缀提交或将短样本平移到一小时。

## 已提交但尚未确认的重放

先暂缓可选发布，完整处理主音频，并保留真实生成的边界 proof。
随后允许 CellStore 完成一次发布并返回 `durable` / `retained`，
但在 Capture 消费这个确认之前停止推进：此时 cell 已写入，而 outbox 仍保留原始 pending proof。

关闭 native coordinator，确认原 CellStore 所有者线程退出后，只重新打开本用例新建并拥有的可选 store，
创建新的 Capture，再重放已经持久化的原始 proof。结果严格为一次 `existing`：

- cell 数量仍为 1，依赖仍为 2
- cell sequence、revision、proof、PCM、依赖和来源 receipts 均未改变
- 主数据行、主提交、native 读取和 ASR 读取均未改变
- 重放新增 native / ASR 调用为 **0**
- 最终 pending 为 **0**，pins 为 **0**，无在途发布；再次 drain 不产生额外发布

新 Capture 的运行身份与 proof 中的历史身份不同，且它没有观察新 reset。
这里恢复的是已保存的证据，不表示在 proof 保存前重启 native / Capture 就能重新连接历史 epoch。
既有拒绝此类连接的约束没有放宽。

## 资源与清理

外层资源监测记录：

- 墙钟时间：**7.449 秒**
- 采样进程组 RSS 峰值：**65,134,592 bytes**
- 采样新输出占用峰值：**118,669,312 bytes**
- CPU 3，nice 19；地址空间硬上限 2 GiB
- RSS 512 MiB / 新输出 160 MiB 采样守护，采样间隔 0.2 秒
- 工作时限 240 秒，外层总包络 300 秒；没有触发资源或时间停止条件

这些采样峰值与守护不是瞬时硬内存或磁盘上限。
清理结果已确认：原所有者和重开后的所有者线程均退出，native coordinator 关闭，
仅在确认关闭后删除本用例 fixture；最终无 pending 和 pin。

## 不作出的结论

这是合成 native / ASR 下的逻辑时间、来源绑定、事务与幂等恢复回归，
没有加载模型或启动 API 服务。它不是实时录音耐久测试，不是第二个完整小时的连续运行，
不验收真实 ASR、说话人准确率或跨 epoch 身份一致性，也不验收 native 二进制或 SQLite/VFS 分配行为。
此次故障位置为确定性的“提交后、确认前”，恢复采用受控的干净关闭与重开；
**未测试进程强杀、断电或其持久性保证**。

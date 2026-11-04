# 持续录音与容量交接：已补齐部分和阻塞

本轮没有自动删除、移动原录音、终结ROTATION、改手机profile或切换服务路由。
不能把准备了空目录称为连续录音轮换成功。

## 已实现的安全增量

- run/watch在保存下一交付选择意图之前，读取实际append历史并检查冻结的输入数、
  时长、字节和source槽位规则。容量不足明确返回，尾段仍为staged，不发EOS。
- configure/initialize对首份交付先做确定的容量检查，超过限制不会创建状态或发送HTTP。
- `recorder_inbox_runner preflight --state-dir /private/runner/span` 是本地只读观察。
  已有pending选择保留；终结/导出状态优先报告，不把已完成工作错误报成等待新输入。
  它不查询远端，不证明磁盘或服务端已经预留容量。
- `recorder_receiver_generation` 可在停止状态下准备另一个端口的空代际，继承已有
  配置和凭据文件引用，保留原路径；有意图/完成回执和严格恢复检查。只返回prepared，
  不启动、不切换、不建session，也不声称端口可用。详见RECORDER_RECEIVER_GENERATION.md。

使用真实小WAV和本地uploader journal、HTTP测试替身的控制中，短段source槽位
在14份输入后耗尽：56帧保留
为staged，没有第15份选择意图，没有EOS。重复观察/执行保持所有已有文件字节一致。
这是小型状态/容量验证，不是原生模型、长期音频或真实手机录音验证。

在此前实际三段FTPS→原生分析→导出的数据上，prepare与精确resume均成功，新配置
通过check、空代际status通过，83个旧录音、状态、转写结果、配置及密钥文件的内容
摘要保持一致。新服务没有启动，Android没有改变。旧导出的preflight正确返回
exported_locally_verified，同时明确remote_completion_checked=false。

## 不能直接做的事

1. 容量界限不是物理录音终点。当前intake、恢复、status和export各自要求真实终结；
   ROTATION可能还有尚未发布的页尾。另建session或伪造STOP会遗失其分析闭环。
2. 旧版本可能已保存一份未接收的下一交付意图。新工具不删除、不挪用它；需保留并
   按精确原意图恢复。新的预检防止继续制造这类容量陷阱，不擅自修复旧现场。
3. 接收器直接换raw/inbox会破坏旧attempt的CWD/RETR回读，单纯保留文件不能保留
   远端路由。未开始的旧QUEUED项还需要在旧端点写入，不能把旧端点直接改成只读。
4. 新host/port/user/directory形成Android新revision，自动发送会重扫保留历史，
   可能重复上传；profile还有数量上限。旧530/550等协议错误可能永久阻塞原队列。
   因此不能先撤旧认证/路径，再补兼容，也不能将逐批改profile视作无限轮换。
5. 分析session切换会重置推理上下文。即使PCM逐帧保留，也不能保证边界转写与单个
   不间断session完全相同。识别质量、跨人声和重叠说话不能由文件完整性推导。

## 真正连续交接仍需要的实现

- 固定FTPS目的地的有限代际路由现已实现，保留旧attempt回读和新attempt写入。
  当前代际需停止/重启切换，最多八代；还没有自动热切换。详见RECORDER_RECEIVER_ROUTING.md。
- 同一冻结inbox内的显式逻辑切段、完整导出及准确下一session初始化现已实现，
  保留原ROTATION并用独立标记绑定回执；3秒+6秒真实原生分析PCM逐帧等于原9秒。
  详见RECORDER_LOGICAL_BOUNDARY.md和LOGICAL_SPAN_FLOW_RESULT_20261104.md。
  dev67另加入跨代际inbox的v2绑定和有界交接入口，见RECORDER_GENERATION_TRANSITION.md。
  它只使用明确的路由配置和准确继任者，仍不自动选择/调度切段或执行原子轮换。
- 对旧pending或部分接收输入设计经过验证的交接路径，不静默丢弃或重复归属。
- 长期存储规划及人工确认的归档/留存策略。保留全部代际不会凭空增加磁盘容量；
  当前没有无损自动清理，也没有无限期daemon验收。

现有每span默认12小时/最多192输入、接收器256attempt/8GiB及发布区等上限均未提高。
本轮只交付上述独立可行部分，不声称已解决无限期持续录音。

本轮合并的接收器、代际、容量、初始化和runner/watch相关回归共258项通过。
独立复核另外确认49项代际/容量控制通过；不是全仓测试或长期录音验收。

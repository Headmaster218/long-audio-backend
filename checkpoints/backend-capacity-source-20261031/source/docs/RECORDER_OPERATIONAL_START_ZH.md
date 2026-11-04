# 从接收到分析：一个明确录音段的操作流程

这份流程把已有运行入口串起来，避免手写接收器 JSON 和手工调用 API 创建 session。
它处理一个明确选择的录音 span，不是无限期自动录音服务。

## 必须先具备的条件

- 后端已经在目标解释器、SQLite 绑定和数据盘上通过资格审查并安装；默认新克隆
  **仍不保证能启动严格模式**。动态识别支持不等于目标获准。先阅读
  `ISOLATED_TARGET_INSTALL_ZH.md`，不要复制别的机器的候选补丁/回执。
- 原生模型和库已准备、校验，模型目录符合严格启动要求。模型不在代码仓库。
- FTPS 服务环境安装了当前后端源码和 `requirements-receiver.txt` 的固定依赖。
  同一Python3.13环境可选已列明pin差异的合并hash锁，见隔离安装说明。
  TLS 证书、私钥、账号及密码文件已经由操作者配置。下述步骤不创建凭据或信任。
- 所有数据路径在 Git 外，父目录已经存在、仅当前专用用户可访问。服务不要用 root。
- 当前验证范围是云端 loopback。手机要连接显式配置的受信私网地址，验证证书名称
  和信任链。公网、共享 NAT 和任意远程网络不属于已验收范围。

以下 `python` 表示安装对应入口的环境。接收器和推理服务可以使用不同环境；不要
把测试工具或另一份源码放进推理服务的 PYTHONPATH。

## 1. 启动严格分析服务

按 `STRICT_BACKEND_SUPERVISOR.md` 创建私有数据目录与既有 instance.lock，提供
明确的模型、库、policy/storage 参数。先执行 `strict_supervisor check`，成功后
在一个终端执行同参数的 `strict_supervisor serve`。保持前台运行并检查启动结果。
健康接口存活不等于模型已经完成推理。服务拒绝时先处理资格/模型原因，不降级校验。

## 2. 配置并启动 FTPS 接收器

在既有私有父目录下准备全新的空 raw/inbox 目录，再执行：

```sh
python -m audio_backend.recorder_receiver_publication configure \
  --state-dir /private/receiver/state \
  --staging-dir /private/receiver/raw --inbox-dir /private/receiver/inbox \
  --exclusive-receiver
python -m audio_backend.recorder_ftps_receiver configure \
  --config /private/receiver/service.json \
  --state-dir /private/receiver/state --inbox-dir /private/receiver/inbox \
  --cert-file /private/tls/server-chain.pem --key-file /private/tls/server.key \
  --username EXISTING_ACCOUNT --password-file /private/secrets/ftps-password \
  --bind-host 127.0.0.1 --port 2121 --passive-ports 30000 30007
python -m audio_backend.recorder_ftps_receiver check --config /private/receiver/service.json
python -m audio_backend.recorder_receiver_status --config /private/receiver/service.json
python -m audio_backend.recorder_ftps_receiver serve --config /private/receiver/service.json
```

已有配置不可覆盖。私网非loopback监听还需显式的
`--allow-non-loopback --acknowledge-data-peer-ip-only`，并自行配置网络可达性。
Android FTPS profile 的远端基础路径是 `/`，不是测试夹具历史路径 `/fixture`。
账号、服务器名称、端口和证书信任必须匹配；密码不进配置 JSON、命令参数或 Git。

## 3. 收到第一份完整交付后初始化分析绑定

等接收器发布一份真实完成的三文件交付，再选择确切的
`r-<64位小写hex>-<32位小写hex>` 目录。不能挑“最新目录”代替人工确认，也不能选择
仍在上传的 raw 目录。首次 initialize 创建一个新 session 并固定这一 span；不会
上传、推理、终结或导出：

```sh
python -m audio_backend.recorder_inbox_runner initialize \
  --state-dir /private/runner/span-01 --inbox-dir /private/receiver/inbox \
  --job-dir /private/intake/span-01 \
  --first-delivery /private/receiver/inbox/r-EXACT_COMPLETE_ATTEMPT \
  --receiver-state-dir /private/receiver/state \
  --api http://127.0.0.1:8765 --capture-id OPERATOR_SELECTED_CAPTURE \
  --output-dir /private/export/span-01 \
  --exclusive-writer --acknowledge-span-local-closure
```

state/job/export 必须是互不嵌套的新路径，父目录私有且已存在。API 密钥仅用既有
`AUDIO_API_KEY` 环境变量传入，不写入绑定。非loopback API 必须使用 HTTPS。
初始化会先保存创建意图，再只发一次创建请求。不确定响应**不得自动重发**；保留
所有状态和服务记录，由操作者核查。仅已有确认 session 回执时，使用相同参数加
`--resume` 做只读确认并补全本地绑定。严格 contract 是 session 级接口，创建后若
不兼容会留下一个已确认但未使用的 session；不会擅自删除或另建。

## 4. 有界等待后续交付、分析与导出

```sh
python -m audio_backend.recorder_inbox_runner watch \
  --state-dir /private/runner/span-01 \
  --exclusive-writer --acknowledge-span-local-closure \
  --finalize --export --export-recovery-attempts 3 \
  --poll-seconds 5 --max-polls 256 --max-actions 256 \
  --max-requests 1024 --max-network-writes 512 --deadline-seconds 600
```

这些是本次调用的明确权限和上限。只在真实 STOP/INTERRUPTION/单一RECOVERED_TAIL
出现后终结；ROTATION、缺段或超时不代表录音结束。导出到初始化时指定的新私有目录。
`waiting_for_input`、`pending`、`action_limit` 或截止到期均不等于分析全部完成。
保存所有结果，按 `RECORDER_INBOX_RUNNER.md` 对同一状态明确续跑；不要另建session
或丢弃旧状态来隐藏失败。版本变化导出只允许指定的有限只读恢复，不覆盖已有结果。

## 5. 停止、查容量与下一次录音

正常终止接收器和监督器，等待它们退出后再运行 receiver status。
status 是停止状态下的有界只读观察；服务占用锁时返回busy，不应循环抢锁。
它报告剩余预算，不判断文件可删除。监督器受SIGTERM正常退出码是143。

接收器保留尝试数上限256、raw累计8GiB，发布区另有限额；runner默认一个span最多
12小时/192输入。重试也消耗尝试数，5分钟分段192份也只有16小时，时间限制可能更早
达到。不能靠提高上限或定时删文件变成无限期运行。新capture/run/epoch要明确新的
span/session绑定；已有音频、原始交付、runner状态和导出必须保留。

目前没有证明无损的自动代际轮换/清理，也未验收跨人声识别质量、说话人身份、重叠
说话、长期连续运行、手机麦克风链路或用户服务器。请把这些与“短音频链路能完成”
分开看待。


## 容量前检查与代际准备

可使用 `recorder_inbox_runner preflight --state-dir /private/runner/span-01`
读取本地状态；新初始化的首份pending选择需按原意图执行，不能换选或删除。
运行中的后续选择会先做容量检查。容量拒绝保留staged尾段，不伪造结束。
准备另一接收代际只能显式使用不同端口，且不会切换手机或原路由；新profile可能
重扫历史，不能把它当作透明轮换。完整边界见CONTINUOUS_RECORDING_LIMITS_ZH.md。

## 需要显式交接或恢复时

固定接收端点的有限代际见RECORDER_RECEIVER_ROUTING.md；同一inbox内的显式逻辑
切段和下一session见RECORDER_LOGICAL_BOUNDARY.md。它们分别验证，不代表已实现
无人值守跨代际持续服务。pending是异步处理的正常状态；中断严格任务的明确恢复步骤
见RECORDER_OPERATIONAL_RECOVERY_ZH.md。不要新建替代任务掩盖旧任务的未解决状态。

# 后端仓库交付入口

当前开发代码在 `checkpoints/backend-capacity-source-20261031/source/`，**不要在仓库根目录执行 pip install**。根目录仍保留旧版本历史；当前源码是 dev66。本入口和恢复工具不修改推理实现或运行时保护。

## 从新克隆得到可安装的源码

需要 Linux、Git 和 Python 3.12+；当前严格原生路径针对 Linux x86_64，普通 Windows Python 不能直接运行该管线。以下只下载代码、校验并复制源码，不下载模型、不启动服务、不访问录音设备。

```sh
git clone --branch development/backend-capacity-source-20261031 \
  https://github.com/Headmaster218/long-audio-backend.git
cd long-audio-backend
git rev-parse HEAD
python3 -B checkpoints/backend-capacity-source-20261031/restore_source.py \
  --output ../long-audio-backend-source
cd ../long-audio-backend-source
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
python -m audio_backend.recorder_inbox_runner --help
```

记录 `git rev-parse HEAD` 的输出可以固定交付版本。开发分支仍可能更新，已有固定提交时先 `git checkout --detach 提交号`。`--output` 必须不存在且父目录已存在；工具拒绝覆盖已有目录。若复制中断，保留的部分目录不是成功产物，检查后换一个新路径重试。校验工具只信任当前提交内的清单，不能替代对仓库来源的信任。请在可信、没有其他进程改写的目录运行。

省略 `--output` 只校验。会检查全部文件集合、字节数、SHA-256 和 Git blob SHA-1，拒绝额外文件、符号链接和特殊文件。成功复制后才输出 `"restored": true`。不要把运行数据放入源码目录。

以上 pip 命令是**开发环境安装**，不是精确推理环境锁定或生产部署步骤。离线或可复现依赖安装需预先准备匹配平台的 wheel 和所选运行时锁文件；不要混用旧安装文档中的锁文件以绕过兼容检查。

## 哪些已经可用，哪些还不能承诺

- 后端包含 FTPS 收件的校验/发布边界、显式 span 绑定、有界 watch、增量导入、状态查询和私有导出。
- 一次云端本机 FTPS → watch → 原生 ASR → 导出已通过，含自然发生的导出版本冲突恢复。它使用经过单独核验的运行时适配候选，不等于新克隆默认源码在任意服务器上可以直接运行。
- 原生运行时、模型权重、私有数据库、录音、转写及凭据不在仓库里。默认运行时校验保持原样，不提供跳过校验的开关。
- 严格模式已有 `python -m audio_backend.strict_supervisor check/serve` 前台启动入口，自动持有并传递原始实例锁；需要已有私有数据目录、锁、显式模型与策略。先运行 `python -m audio_backend.strict_runtime_preflight --data-dir /绝对私有目录` 查看资格差异。默认运行时不匹配仍会拒绝，不能仅添加 `--strict-primary` 或复制旧资格回执来启动。
- 现有可运行 FTPS 接收进程：`python -m audio_backend.recorder_ftps_receiver`，安装单独的 `requirements-receiver.txt` 并提供已有私有配置、账号和 TLS 证书。已通过本机回环测试及实际 Android Java 传输代码测试；它不是公开互联网接收器，不自动创建凭据或部署服务。详见 `docs/RECORDER_FTPS_RECEIVER_SERVICE.md`。
- 每个选定 span 仍需明确指定首次完整交付；新增 `recorder_inbox_runner initialize` 可创建一次空 session 并绑定，不再必须手工调用 API。已有确认回执可只读恢复；不确定创建不自动重发。没有自动跨run/epoch的session生命周期、无限期daemon或无损留存清理。原始收件与发布区各有8GiB上限，保留目录也有上限。

## 受审隔离安装流程

新增 `qualification/materialize_target_candidate.py` 与 `qualification/verify_installed_target.py`，分别用于从明确受审证据生成隔离源码、校验已安装的产品字节。详见恢复后源码内 `docs/ISOLATED_TARGET_INSTALL_ZH.md`。这不是绕过默认运行时拒绝的开关，也不表示任意服务器已通过。

## 运行前按顺序阅读

以下路径相对恢复后的源码目录：

1. `docs/RECORDER_FTPS_RECEIVER_SERVICE.md`：接收进程配置、检查、启动、关闭、有限容量与网络边界。
2. `docs/RECORDER_RECEIVER_COMPLETION.md`：接收器所有写入必须经拥有流和真实完成回调，不以文件出现或 marker 到达代替传输成功。
3. `docs/RECORDER_RECEIVER_PUBLICATION.md`：私有目录、不可变发布和不确定结果处理。
4. `docs/RECORDER_INBOX_RUNNER.md`：已有空 session、私有独立路径、显式绑定与有限 watch。首次 configure 不会创建 session。
5. `docs/STRICT_RUNTIME_SETUP_ZH.md` 和 `docs/STRICT_BACKEND_SUPERVISOR.md`：所选目标的只读检查、明确的前台启动与安全关闭。`docs/INHERITED_LOCK_ADOPTION.md` 给出锁所有权约定。
6. `qualification/combined_chain_20261103/FRESH_OPTIN_RESULT.md`：实际原生管线通过的精确范围和局限。

API 密钥使用既有 `AUDIO_API_KEY` 机制，不写进命令参数、配置回执或 Git。源码安装与服务上线分开；接收器认证/TLS、目标服务器的精确运行时资格、监督启动和留存策略完成前，不把它当作已可上线的一键成品。

## 2026-11-04：另一套动态 SQLite 运行时验证

新增严格动态绑定识别，没有放开默认生产白名单。在同一云主机上，以全新依赖安装的
Python 3.13 / SQLite 3.46.1 完成物理恢复、受审源码物化、wheel 安装和短时真实语音验证。
这不是独立服务器验收。步骤见
[隔离目标安装说明](checkpoints/backend-capacity-source-20261031/source/docs/ISOLATED_TARGET_INSTALL_ZH.md)，
证据范围与已知测试失败见
[动态运行时结果](checkpoints/backend-capacity-source-20261031/source/qualification/dynamic_install_20261104/RESULT.md)。

## 接收与处理的实际操作入口

新增接收配置生成、首次session初始化及停止状态的容量查询。完整顺序见
[从接收到分析的操作流程](checkpoints/backend-capacity-source-20261031/source/docs/RECORDER_OPERATIONAL_START_ZH.md)。
这些入口减少手工步骤，不会自动批准新运行时，也不代表无限期录音、跨人声识别质量
或用户服务器已经验收。不要提高上限或删除旧录音来掩盖连续运行限制。

已在同一云主机的全新整合安装环境验证：实际Java连续三份ROTATION/ROTATION/STOP
交付，在watch运行中陆续送达，完成原生分析与选定段导出。详见
[操作链结果与剩余缺口](checkpoints/backend-capacity-source-20261031/source/docs/OPERATIONAL_FLOW_RESULT_20261104.md)。
该结果仍绑定受审目标候选，不把短时闭环写成长期连续、跨人声质量或独立服务器验收。

## 容量保护与下一代际准备

新增本地preflight和选择前容量检查，避免容量不足后才留下新的pending选择；
首份交付超出冻结容量时也不会创建session/状态。新增prepare-only接收代际命令，
严格保留旧录音、状态和端点，不自动切换手机。它们不等于连续轮换，详见
[持续录音阻塞与安全边界](checkpoints/backend-capacity-source-20261031/source/docs/CONTINUOUS_RECORDING_LIMITS_ZH.md)。

固定 FTPS 端点的有限代际路由已加入开发快照。配置帮助见
`source/docs/RECORDER_RECEIVER_ROUTING.md`：保留旧 attempt 回读和新队列写入，
当前代际切换需要停止/重启；最多八代，不删除原始数据，不等于热切换或无限期运行。
真实 Android 生产传输代码的三份公开语音交付及旧回读已在同一云主机验证。

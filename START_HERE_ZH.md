# 后端仓库交付入口

当前开发代码在 `checkpoints/backend-capacity-source-20261031/source/`，**不要在仓库根目录执行 pip install**。根目录仍保留旧版本历史；当前版本以该提交内源码的 `pyproject.toml` 和 `audio_backend/__init__.py` 为准。本入口和恢复工具不修改推理实现或运行时保护。

## 当前支持的部署顺序

先固定提交并恢复源码，再按以下当前入口操作；后面的历史结果按各自版本理解，
不要用旧版本的“手工逐代切换”步骤替代当前有限计划。普通非严格后端不是严格录音链的替代方案。

1. [目标运行时与隔离安装](checkpoints/backend-capacity-source-20261031/source/docs/ISOLATED_TARGET_INSTALL_ZH.md)：
   用实际目标解释器和数据盘执行证据准备命令，独立审查后才物化、安装和核验候选。
   云端结果不能批准用户服务器，不复制历史回执或改写 allowlist。
2. [容量预检](checkpoints/backend-capacity-source-20261031/source/docs/RECORDER_CAPACITY_PREFLIGHT.md)：
   检查固定采集节奏、每代上限及有限计划预算。配置算术通过不等于现有空间还有余量。
3. [全新部署配置包](checkpoints/backend-capacity-source-20261031/source/docs/RECORDER_FRESH_DEPLOYMENT_BUNDLE.md)：
   生成、审查配置和实际命令，再明确启动。只用于没有历史待对账上传的新用途；不能用全新命名空间替换已使用端点。
4. 等首份真实完整发布交付后，执行配置包的 initialize 命令，随后执行
   [有限多代分析计划](checkpoints/backend-capacity-source-20261031/source/docs/RECORDER_FINITE_ANALYSIS_PLAN.md)。
   最多八代，不自动扩容、清理或重放不确定写入；终端 ROTATION 耗尽计划不等于录音结束。

5. 可选的[重叠语音候选](checkpoints/backend-capacity-source-20261031/source/docs/RECORDER_OVERLAP_CANDIDATES.md)：
   主分析完成后，用 `--native-turns` 创建新的绑定导出，再在独立可选运行时使用
   `--auto-native-overlap` 自动选择有界区间。默认关闭，不覆盖主转写，不把模型局部标签当作人物身份。
   需要单独准备文档指定的模型与运行时，并管理所有进程的总资源；不是在严格主服务中启用任意分离插件。
   可在生成配置包或执行有限计划时显式加 `--native-turns`，让计划直接生成所需的三份输出文件及完成清单；
   默认仍为两份输出文件及完成清单，不会自动调用候选模型。未启用时，须保留完整原始输入并访问原服务，另建一次新导出。
   当前原生证据模式不支持自动导出恢复选项，组合会在操作前拒绝，不能静默降级。候选阶段可在服务停止后运行。

[最新安装态有限计划与旧回执验证](checkpoints/dev78-native-plan-validation-20261105/README.md)
完成三代公开语音输入、三份原生证据导出及停服零 HTTP 重复检查，也验证了真实旧两文件计划的无写入重复。
原测试脚本失败及后续独立只读验证分开保留；这仍不是用户服务器或长期声学质量验收。

[实测结果与保留失败](checkpoints/dev74-overlap-results-20261105/README.md)：
小样本已观察到中英文多人文本输出，但有少数人文本错误、额外通道及数字静音幻觉，整体质量验收未通过。
后续精确全零输入保护只跳过全零 PCM 的模型调用，不保证非零环境噪声不会产生幻觉。
[自动选区覆盖](checkpoints/dev76-automatic-overlap-20261105/PUBLIC_AUTOMATIC_SELECTOR_FINDINGS01.md)
是另外的检测/上下文覆盖检查，不代表新候选的识别准确率。

最新[六分钟并发接收与原生处理证据](checkpoints/dev73-concurrent-120s-20261105/README.md)
验证了同一云主机三份连续 120 秒公开 AMI 输入、单次计划和真实上传/分析重叠。
原始测试脚本的单 chunk 断言失败保留，独立只读聚合验证完成结果；本轮未测试重复调用。
它不是用户服务器验收、长时稳定性或说话人准确率证明。

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
- 首个 span 仍需明确指定首次完整交付；`recorder_inbox_runner initialize` 可创建一次空 session 并绑定。预声明的有限代际计划可接续后续分析 session，已有确认回执可只读验证；不确定创建不自动重发。这不是任意跨 run/epoch 的自动生命周期、无限期 daemon 或无损留存清理。原始收件与发布区各有 8 GiB 上限，保留目录也有上限。

## 受审隔离安装流程

先用恢复后源码中的 `qualification/prepare_target_evidence.py`，在准备运行服务的同一个解释器和目标数据盘上，执行既有目标观察、物理恢复探针、独立字节审计及九项候选校验。它只准备有界证据并停在待独立审查状态，不生成批准 basis、不改 allowlist、不安装或启动服务。失败或中断留下的目录不能覆盖或自动重跑。

```sh
python -I -B /absolute/source/qualification/prepare_target_evidence.py \
  --source /absolute/source \
  --manifest /absolute/SOURCE_MANIFEST.json \
  --manifest-sha256 EXACT_SOURCE_MANIFEST_SHA256 \
  --output /absolute/private-target-volume/new-evidence \
  --test-tools /absolute/verified-test-tools \
  --acknowledge-physical-probes
```

输出目录必须不存在，其父目录必须是目标数据盘上的私有目录。测试工具应预先按固定清单核验，放在目标运行环境外；只有测试子进程使用该目录，不向目标环境安装 pytest 或修改运行时指纹。完整依赖准备、证据审查与后续步骤见恢复后源码内 `docs/ISOLATED_TARGET_INSTALL_ZH.md`。

通过独立审查后，既有 `qualification/materialize_target_candidate.py` 从明确受审证据生成隔离源码，`qualification/verify_installed_target.py` 校验实际安装字节。普通的 `python -m audio_backend` 非严格模式不是本收件/分析链的等价替代，不能用它绕过严格资格。用户服务器仍须完成自己的实际目标验证；云端通过的证据不是任意服务器的启动许可。

## 运行前按顺序阅读

以下路径相对恢复后的源码目录：

1. `docs/RECORDER_FTPS_RECEIVER_SERVICE.md`：接收进程配置、检查、启动、关闭、有限容量与网络边界。
2. `docs/RECORDER_RECEIVER_COMPLETION.md`：接收器所有写入必须经拥有流和真实完成回调，不以文件出现或 marker 到达代替传输成功。
3. `docs/RECORDER_RECEIVER_PUBLICATION.md`：私有目录、不可变发布和不确定结果处理。
4. `docs/RECORDER_INBOX_RUNNER.md`：已有空 session、私有独立路径、显式绑定与有限 watch。首次 configure 不会创建 session。
5. `docs/STRICT_RUNTIME_SETUP_ZH.md` 和 `docs/STRICT_BACKEND_SUPERVISOR.md`：所选目标的只读检查、明确的前台启动与安全关闭。`docs/INHERITED_LOCK_ADOPTION.md` 给出锁所有权约定。
6. `qualification/combined_chain_20261103/FRESH_OPTIN_RESULT.md`：实际原生管线通过的精确范围和局限。

API 密钥使用既有 `AUDIO_API_KEY` 机制，不写进命令参数、配置回执或 Git。源码安装与服务上线分开；接收器认证/TLS、目标服务器的精确运行时资格、监督启动和留存策略完成前，不把它当作已可上线的一键成品。

## 历史版本证据与演进记录

以下各节保留所述版本和日期当时的结论。旧节中的能力限制或手工步骤不覆盖上面的当前操作入口，也不能把不同版本的证据合并成一次完整验收。

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

同一冻结 inbox 的显式逻辑切段和下一分析 session 已形成实际闭环，见
`source/docs/RECORDER_LOGICAL_BOUNDARY.md`。公开语音 3 秒 + 6 秒的两份原生分析
PCM 拼接与原 9 秒逐帧一致，准确下一 session 只创建一次；重复验证不发 HTTP。
这不是无人值守跨代际轮换，也不证明识别或说话人质量连续。处理/导出为异步，
中断后需显式检查和重试，见 `source/docs/RECORDER_OPERATIONAL_RECOVERY_ZH.md`。

dev67 提供跨接收代际的有界交接入口，见
`source/docs/RECORDER_GENERATION_TRANSITION.md`。它绑定明确路由文件和准确继任者，
串起旧段终结/导出及下一 session；不会控制 FTPS 进程或自动开始下一段录音摄入。
接收路由切换与正在进行的上传仍需另外协调，不能称为无人值守热轮换。

完整 dev67 wheel 的三代际实际流程已验证：同一端点旧 attempt 可回读，三个原生
任务拼接精确等于原144000帧；故意中断的原任务经一次明确retry恢复，重复交接
不再发HTTP。安装前后184个产品文件一致，相关575项回归通过。精确来源和限制见
`source/docs/INSTALLED_GENERATION_FLOW_RESULT_20261104.md`。这是同一云主机的
9秒公开语音验证，不是用户服务器、实体手机、长时或跨人声识别质量验收。

## dev70：固定端点的有界自动接收换代

新增显式启用的容量换代：只在新MKD之前选择已准备下一代，旧上传可继续完成、旧回读保留，首个目录写入结果不明时不重放。最多八代，不删数据、不自动推进分析session，也不是无限期录音保证。详见 [自动接收准入](checkpoints/backend-capacity-source-20261031/source/docs/RECORDER_AUTOMATIC_ADMISSION.md)。

## dev71：单次自动分析交接

新增明确启用的一次有界交接入口，可发现唯一连续继任录音、完成旧段导出、准确创建/核验下一session并启动摄入。每次只面向预先配置的一个继任代；暂停后仍需显式再调用。已用新客户端和接收器对现有合格dev67后端验证三段原生导出、原PCM共144000帧完全一致，重复交接零HTTP。该证明不是新整包原生资格或长期无人值守验收。详见 [自动分析交接](checkpoints/backend-capacity-source-20261031/source/docs/RECORDER_AUTOMATIC_ANALYSIS_HANDOFF.md)。

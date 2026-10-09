# 可选 MOSS-TD 重叠语音候选（官方 Python CPU BF16）

这条路径使用 OpenMOSS 的 **MOSS-Transcribe-Diarize 0.9B**，不是 Alibaba 的
MossFormer2 波形分离器。它从原始混音直接生成多个匿名说话人的文字。
当前定位是独立、未验证的文字候选，不替换普通混音转写、native 说话人活动或全局身份。

## 已知效果与限制

- 固定七片段上，BF16 与 FP32 的逐人主要错误计数一致；BF16 子进程峰值
  2.246 GiB，60.515 秒音频的推理共 113.78 秒。随后实际 HTTP 测试中，整个
  API、全部后代及客户端采样峰值最高 2.935 GiB；见[实际集成与故障结果](../reports/MOSS_TD_HTTP_INTEGRATION.md)。
  这是短集成测试，不能当作该模型已通过 12 小时长测。
- 后续独立选片的四段重叠语音中，按说话人最佳置换的中文 cpCER 为 24/222（10.81%），英文 cpWER 为
  19/134（14.18%）。同片实际 native+SenseVoice 的乐观未知人归属敏感性结果为
  116/222 和 65/134。它们与 12 小时试验的混音序列 CER/WER 不是同一个指标。
  样本很小，录音/说话人并非全新，不能当作通用准确率。
- 英文少数说话人的错误仍约 40%；两段英文的生成重叠时间覆盖仅 15.6%/2.1%。
  **模型文字时间不是可靠的说话人活动时间，更不是逐词对齐。**
- 静音试验生成了无关且格式不完整的英文回复；一段标为单人的中文出现额外的“嗯”。
  默认只对 native 检测到重叠的支持片段尝试此模型，并保留失败/不确定状态；
  下述显式语音探测模式会扩大尝试范围，也扩大假第二人的风险。
- 三人诊断虽然输出三个标签，第三人的 11 字仍错了 9 字。最大 8 个标签只是
  程序存储/资源安全上限，绝不是八人识别准确率承诺。
- 原 12.25 小时双流程试验的重叠候选功能关闭；不能把该试验算作 MOSS-TD 长时验证。

详细记录：
[FP32](../reports/moss-td-pilot/FP32_RESULTS.md)、
[BF16](../reports/moss-td-bf16/RESULTS.md)、
[新选片确认](../reports/moss-td-confirmation/RESULTS.md)、
[三人诊断](../reports/moss-td-capacity/RESULTS.md)。

## 安装（独立环境，不升级主服务环境）

先按 [中文快速开始](QUICKSTART_ZH.md) 准备基础 CPU 候选。以下在项目根目录执行。
模型和官方代码为 Apache-2.0；安装器固定完整文件 SHA256、模型修订及官方源代码修订，
保留来源和许可证。网络只用于首次下载；推理使用本地文件。

Linux：

```sh
python3.12 -m venv .venv-moss-td
.venv-moss-td/bin/python -m pip install torch==2.8.0+cpu torchaudio==2.8.0+cpu --index-url https://download.pytorch.org/whl/cpu
.venv-moss-td/bin/python -m pip install -r requirements-moss-td.txt
.venv/bin/python scripts/setup_moss_td.py --model-dir models/moss-td
.venv/bin/python scripts/setup_moss_td.py --model-dir models/moss-td --verify-only
.venv/bin/python scripts/run_cpu_candidate.py --enable-moss-td --moss-td-python .venv-moss-td/bin/python --moss-td-model-dir models/moss-td --dry-run
.venv/bin/python scripts/run_cpu_candidate.py --enable-moss-td --moss-td-python .venv-moss-td/bin/python --moss-td-model-dir models/moss-td --data-dir audio-data-moss-td
```

Windows PowerShell 迁移参考（尚未在 Windows 执行）：

```powershell
py -3.12 -m venv .venv-moss-td
.\.venv-moss-td\Scripts\python.exe -m pip install torch==2.8.0+cpu torchaudio==2.8.0+cpu --index-url https://download.pytorch.org/whl/cpu
.\.venv-moss-td\Scripts\python.exe -m pip install -r requirements-moss-td.txt
.\.venv\Scripts\python.exe scripts\setup_moss_td.py --model-dir models\moss-td
.\.venv\Scripts\python.exe scripts\run_cpu_candidate.py --enable-moss-td --moss-td-python .venv-moss-td\Scripts\python.exe --moss-td-model-dir models\moss-td --dry-run
```

检查打印的命令/警告后，去掉 `--dry-run` 启动。FFmpeg/ffprobe 仍需在 PATH。
默认启动器继续关闭重叠模型，必须明确传 `--enable-moss-td`；缺失文件或哈希错误会失败。
首次依赖安装、CPU 指令集与 BF16 性能在目标 Windows 机器需要重新验证。
主 CLI 也支持 `--overlap-backend moss_td_experimental --moss-td-python ... --moss-td-model-dir ...`。

安装器可续做普通网络失败后的部分安装：先核对并跳过已有匹配文件，只下载缺失项。
它不覆盖哈希不匹配文件、不删除无关文件；发现遗留 `.part` 文件会明确停止，需先将它移开。
`--verify-only` 会同时校验 LICENSE、MODEL_CARD.md 和全部运行资产。

### 尽量补查未被 native 发现的重叠

默认只在 native 检测到同时发声时尝试 MOSS。可额外传
`--moss-td-trigger speech_probe`，让已检测到语音、但尚未检测到重叠的支持片段也尝试生成候选。
它仍不会在没有 native 语音的纯静音片段运行。

新增行明确标为 `region_kind=speech_support_probe`，不是伪造的重叠事件。
native 峰值人数、native 不同标签数与模型标签数分别保留；单人探测中生成第二个标签，
只表示模型提出了候选，不证明真的有第二个人。这个选项会增加 CPU 耗时和假第二人风险，
所有候选仍不写入声纹库。默认 `native_overlap` 不变。

## API 时间与身份语义

读取 `GET /sessions/{sid}/overlap-candidates`，以及其变更 feed。

- 每次输入支持片段最多 20 秒；每个核心窗口默认最多 40 秒候选支持音频，另有工作数、
  300 秒运行超时、子进程及服务总内存保护。未尝试部分有显式预算/容量/失败状态。
- 支持片段在核心窗口内互不重叠；因此跨片段/跨核心的完整词句和匿名标签连续性尚不保证。
  原始音频及主转写保留，不凭空连成同一个人。
- `association_basis=support_not_model_timing` 表示“这条文字来自同一个待分析音频支持范围”。
  是否检测到 native 重叠，须另外检查 `region_kind` 和原始活动；探测行不代表重叠事实。
  它**不表示**文字在每个关联重叠区间内真实发生。一个支持片段的多个区间可共享同一条文字，
  客户端按 track ID 去重，不能把每个链接显示成另一段实际发言。
- 原始模型粗时间完整保留，`boundary_uncertain`/未验证质量保持可见。
  路由用的支持锚点不用于改写或伪造模型时间。需要声音活动时间时另外读取 `speaker-turns`。
- `scope_id` 与 `source_slot` 只在一个支持片段内标识匿名模型声道。
  `speaker_id` 为空，不创建或更新声纹库，不冒充跨日期同一人。
- 这也不证明主流程以前选出的“干净语音”真是单人：其纯净度来自 native 预测，
  当 native 漏掉另一人时，原有声纹观测也可能混杂。探测候选不会自动修复或认可这些档案。
- 无有效文本、格式错误、越界时间、token 上限、超时或资源失败都不能算“成功分离”。

## CPU/GPU 资源与可移植性

当前官方适配器固定 CPU BF16、两个计算线程、贪心解码，使用隔离 Python。
单独 BF16 子进程约 2.25–2.28 GiB 的已测峰值不包含主服务；目标仍是
**整个服务 RAM + 独立 VRAM 合计 6 GiB，最大 10 GiB**。内存轮询不是操作系统硬限额。
准备阶段、临时文件和模型都占磁盘，应留至少 5 GiB 工作余量。临时支持音频放在数据目录所在卷。

官方 Python 路径没有 Vulkan。另有独立的 [transcribe.cpp / GGUF 路径](MOSS_TD_CPP_SETUP.md)，
固定七片段的 CPU Q8 原始输出与同引擎 BF16 完全一致，进程树峰值约 1.445 GiB；
该路径需要显式选择；[完整 API 短测](../reports/MOSS_TD_CPP_HTTP_INTEGRATION.md)峰值为 2.10 GiB，
仍不能替代长时验证或新数据质量评估。
Windows、Vulkan 执行和显存仍未测量。现有 Whisper.cpp Vulkan 是另一条 ASR 路径，
CPU 实测不能换算成实测 GPU 速度或 VRAM。

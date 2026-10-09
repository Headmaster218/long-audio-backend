# 可选 MOSS-TD C++ / GGUF 路径

> Public-source edition: historical model measurements below were imported from prior development documentation, not rerun for this Git import. Full raw evaluation records and media are intentionally excluded. See [public test scope](../TEST_STATUS.md).


这条路径使用固定版本的 `transcribe.cpp 0.2.3`，从原始混音生成匿名说话人的文字候选。
推荐 Q8_0 以节省内存；仍需显式启用，不替代主转写、native 声音活动或跨片段身份。
基础 CPU 服务先按 [QUICKSTART_ZH.md](QUICKSTART_ZH.md) 配置。

**没有可供本项目直接下载的已验证 GGUF 公共 URL。**
`scripts/setup_moss_td_cpp.py` 只接受已通过尺寸和 SHA256 检查的本地 GGUF，
不会自动下载权重、转换模型或安装 Python 包。
下面给出从公开官方文件开始的准备流程，不需要本项目的测试录音、私有路径或现成模型。

## 已验证范围

- Linux x86_64 已实际完成官方 BF16 权重转换、未修改的官方量化工具编译、Q8_0 量化和七段 CPU 推理。
  转换 9.655 秒、峰值 3.723 GiB；量化 7.153 秒、峰值 3.402 GiB。
  这些是一次测量，不是其他机器的时间承诺。
- 新增的通用 `prepare_moss_td_cpp.py` 封装保持同一转换参数、源码、量化策略和输出哈希。
  **该通用封装只做过无模型的 CLI、路径、哈希和防覆盖测试；没有重新从空环境跑完整下载与转换。**
  已执行的原始脚本、来源和结果保留不变，见
  BF16 记录 (private historical evidence; see [public test scope](../TEST_STATUS.md))、
  Q8 记录 (private historical evidence; see [public test scope](../TEST_STATUS.md)) 和各记录中的 provenance。
- Windows 官方 wheel 已核对来源、版本及文件哈希；Windows 和 Vulkan 实际运行、速度、VRAM 均未测量。
  已验证的准备方法是 Linux；WSL2 可使用同一 Linux 方法，但本项目未实测 WSL2。
  准备好的 GGUF 可复制到 Windows，复制后必须再次校验。

七段固定录音中，Q8 原始输出与同引擎 BF16 逐字节相同，包括无声样本的错误回复。
Q8 推理总计 87.025 秒、峰值 1.445 GiB；native BF16 为 75.604 秒、2.378 GiB。
Q8 在本次 CPU 上更省内存但比 native BF16 慢。单个模型子进程的数据不能代表整个 API，
也不能作为 12 小时运行或新录音准确率的证明。

完整 API 短测 (private historical evidence; see [public test scope](../TEST_STATUS.md))已完成：59.71 秒拼接输入（含一次恢复）花 59.10 秒，
API、全部后代与客户端合计峰值 2.10 GiB；单独终止 MOSS 子进程后主结果不变，失败区域保留。
自然英文确认片段出现文字与细时间差异，cpWER 仍为 12/62，补充 cpCER 从 39/245 增至 41/245。
因此七片段精度对照不能推广为所有新输入的逐字节一致。

## 来源和固定版本

| 输入 | 固定来源 |
|---|---|
| 官方模型 | [OpenMOSS-Team/MOSS-Transcribe-Diarize](https://huggingface.co/OpenMOSS-Team/MOSS-Transcribe-Diarize/tree/704aa4a9c304e8520be88901e0d1960158ef5b15)，修订 `704aa4a9c304e8520be88901e0d1960158ef5b15` |
| 官方模型代码、Apache-2.0 许可证 | [OpenMOSS/MOSS-Transcribe-Diarize](https://github.com/OpenMOSS/MOSS-Transcribe-Diarize/tree/61bc29cd4120be7b5d3b761b64cd5dff57263642)，修订 `61bc29cd4120be7b5d3b761b64cd5dff57263642` |
| 转换器、量化器、对应 ggml 头文件 | [handy-computer/transcribe.cpp](https://github.com/handy-computer/transcribe.cpp/tree/63a44d9239d610b3908e8a66b384924cd4a77217)，修订 `63a44d9239d610b3908e8a66b384924cd4a77217` |
| 运行库 | [transcribe-cpp 0.2.3](https://pypi.org/project/transcribe-cpp/0.2.3/) 和 [transcribe-cpp-native 0.2.3](https://pypi.org/project/transcribe-cpp-native/0.2.3/) 官方 wheel |
| GGUF 写入工具 | [gguf 0.19.0](https://pypi.org/project/gguf/0.19.0/)，wheel SHA256 `70bcd10edfe697fb2dad6e40af2234b9d8ece9a41a99761405121ebda1c3c1cd` |

模型的 20 个平铺文件由 [moss-td-model-manifest.json](../moss-td-model-manifest.json)
逐项固定。C++ wheel、输出 GGUF 和小型归属文件由
[moss-td-cpp-model-manifest.json](../moss-td-cpp-model-manifest.json) 固定。
下载程序只取 4 个转换器文件、6 个量化器/头文件以及 3 个 wheel；无需克隆完整仓库。
它还从已校验的官方 wheel 提取 vendor 和 ggml 的 MIT 许可证，与源码一起保存。
gguf wheel 保留自身许可证。源码交付包不包含第三方源码、wheel 或模型二进制。

## 1. 先检查空间，创建全新的准备目录

以下命令在项目根目录执行，需要 Linux x86_64、Python 3.12 和 C++17 编译器（例如 `c++`）。
`cpp-preparation` 是示例新目录，可改成其他独立路径；不要指向现有模型、报告或代码目录。
不要把多个准备任务指向同一目录。

磁盘预算同时包含约 1.83 GB 官方输入、1.83 GB BF16、0.99 GB Q8、临时环境、wheel 和缓存。
初始检查还预留 3 GiB 环境空间和 5 GiB 空闲空间，当前总要求约 **12.4 GiB**；
建议从 **15 GiB 以上空闲空间**开始。5 GiB 是过程中保持的余量，不是从零准备的总需求。
依赖安装器、文件系统和临时文件可能需要更多空间；不要降低余量绕过失败。

```sh
set -e
python3.12 scripts/prepare_moss_td_cpp.py preflight --work-dir cpp-preparation
python3.12 scripts/prepare_moss_td_cpp.py init --work-dir cpp-preparation
python3.12 scripts/prepare_moss_td_cpp.py fetch --work-dir cpp-preparation
```

`preflight` 不创建文件、不联网、不导入模型。`init` 要求整个目录不存在。
`fetch` 要求 `bundle` 子目录不存在，逐项检查公共资源的尺寸和 SHA256。
任何旧文件、悬空链接、失败输出或未知内容都不会被覆盖/自动清除。

## 2. 临时转换环境和官方平铺输入

只在新的转换环境安装依赖，不升级主服务或既有 Transformers 环境。
以下 CPU 包版本对应实际转换；GGUF 工具只用于准备阶段。
所有临时安装文件放在准备目录所在卷，`--no-cache-dir` 避免额外 pip 缓存。
若 shell 设置了 `PYTHONPATH`、`LD_PRELOAD`、`LD_AUDIT` 或 `LD_LIBRARY_PATH`，
先在专用准备 shell 中取消这些覆盖；转换/编译/量化封装会拒绝它们。

```sh
test ! -e cpp-preparation/conversion-env
test ! -L cpp-preparation/conversion-env
test ! -e cpp-preparation/pip-tmp
test ! -L cpp-preparation/pip-tmp
mkdir cpp-preparation/pip-tmp
export TMPDIR="$PWD/cpp-preparation/pip-tmp"
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
python3.12 -m venv cpp-preparation/conversion-env
cpp-preparation/conversion-env/bin/python -m pip install --no-cache-dir --only-binary=:all: torch==2.8.0+cpu torchaudio==2.8.0+cpu --index-url https://download.pytorch.org/whl/cpu
cpp-preparation/conversion-env/bin/python -m pip install --no-cache-dir --only-binary=:all: -r requirements-moss-td.txt
cpp-preparation/conversion-env/bin/python -m pip install --no-deps --no-index cpp-preparation/bundle/wheels/gguf-0.19.0-py3-none-any.whl cpp-preparation/bundle/wheels/transcribe_cpp-0.2.3-py3-none-any.whl cpp-preparation/bundle/wheels/transcribe_cpp_native-0.2.3-py3-none-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl
cpp-preparation/conversion-env/bin/python scripts/benchmarks/moss_td_research/guard.py --report cpp-preparation/source-download-resources.json --seconds 7200 --disk-path cpp-preparation -- python3.12 scripts/setup_moss_td.py --model-dir cpp-preparation/source-model
python3.12 scripts/setup_moss_td.py --model-dir cpp-preparation/source-model --verify-only
```

`setup_moss_td.py` 默认目录为 `models/moss-td`；这里显式选择独立的 `source-model`。
也可对已有的官方平铺目录先运行 `--verify-only`，再将下一步的 `--source-model` 指向它；
不会复制或修改输入。原始权重 SHA256 必须为
`9a0ceb4ab7330357db3ff583dba8d83625d5b733b00e1d55d6970e11b07026c4`。
不要直接把任意 Hugging Face 缓存/链接树当作这个平铺目录。

完整 Python 依赖解析没有全量锁定传递依赖，因此未来仓库解析可能失败或产生数值差异；
输出的固定 SHA256 是最后一道检查。若不匹配，必须停止，不要修改 manifest 来绕过校验。

## 3. 离线转换、轻量编译和 Q8_0 量化

```sh
cpp-preparation/conversion-env/bin/python scripts/prepare_moss_td_cpp.py convert --work-dir cpp-preparation --source-model cpp-preparation/source-model
cpp-preparation/conversion-env/bin/python scripts/prepare_moss_td_cpp.py build --work-dir cpp-preparation --compiler c++
cpp-preparation/conversion-env/bin/python scripts/prepare_moss_td_cpp.py quantize --work-dir cpp-preparation
python3.12 scripts/prepare_moss_td_cpp.py verify --work-dir cpp-preparation
```

三个计算步骤自动使用现有 watchdog：进程树 RSS 6 GiB、主机可用 RAM 至少 1.5 GiB、
磁盘空闲至少 5 GiB、CPU 亲和性最多两个核、每步 30 分钟。
这是 50 ms 采样保护，不是内核硬内存上限；其他服务需要自己的余量。
下载阶段也受 RAM/磁盘 watchdog 保护。初始小文件下载及 Python 环境安装不受该采样监控，
所以开始前的总空间检查仍然必要。

转换器只读经 SHA256 校验的本地文件，设置 Hugging Face 离线标志和独立动态模块缓存，
禁用 bytecode 写入。官方 processor/configuration 自定义 Python 会执行，因此先核对来源。
保持规范参数 `--repo-id OpenMOSS-Team/MOSS-Transcribe-Diarize` 和
`--variant moss-transcribe-diarize`；不会将本机目录名或时间写入模型名。

编译步骤只编译同一提交的 `main.cpp` 和 `policy.cpp`，配同提交的 3 个 ggml 头文件及
`policy.h`，链接已按文件哈希验证的 wheel 内 `libggml-base.so`。
不构建完整 ggml，不安装 Vulkan SDK，也不使用自制 ctypes 量化。
Linux 编译器二进制本身会因工具链和路径变化而不同；helper 保存其实际哈希，
量化前重新检查编译产物和链接库。最终 GGUF 则必须匹配以下固定值。

| 输出 | 字节数 | SHA256 |
|---|---:|---|
| `conversion/MOSS-Transcribe-Diarize-BF16.gguf` | 1,826,882,720 | `57e40c63d9d33229bd2bab5d10bf2592ecaf026f9b4932d203bd9f828025f472` |
| `quantization/MOSS-Transcribe-Diarize-Q8_0.gguf` | 986,899,616 | `551c0a52fb0b75cb6fd85e2247554dfff84fbcd8435305ac83d06ec0c043162f` |

每个写入阶段要求自己的输出目录和资源报告不存在。失败保留现场，绝不把部分输出算成功；
先查看日志/资源 JSON，换一个新的准备目录再尝试。官方平铺输入安装器可以校验并跳过
匹配文件，但遗留 `.part` 文件或不匹配文件需要你先检查、移开。
不要直接重新运行上面的同名资源报告命令；为恢复下载选择新的报告名。

“BF16 reference” 实际含 BF16、F32、两个 F16 卷积张量，转换不是所有权重的纯 BF16 原样封装。
已有独立验证逐张量核对了转换和提示 token；Q8 核对了策略、名字、形状、保留张量及元数据。
这不等于完整激活/logit 相等，也不保证 Q8 对任意新录音都与 BF16 相同。

## 4. 安装小型运行环境，配置服务

```sh
python3.12 scripts/setup_moss_td_cpp.py --model-dir models/moss-td-cpp --gguf-path cpp-preparation/quantization/MOSS-Transcribe-Diarize-Q8_0.gguf --prepared-dir cpp-preparation/source-model --local-wheels cpp-preparation/bundle/wheels
python3.12 scripts/setup_moss_td_cpp.py --model-dir models/moss-td-cpp --gguf-path cpp-preparation/quantization/MOSS-Transcribe-Diarize-Q8_0.gguf --verify-only
test ! -e .venv-moss-td-cpp
test ! -L .venv-moss-td-cpp
python3.12 -m venv .venv-moss-td-cpp
.venv-moss-td-cpp/bin/python -m pip install --no-cache-dir -r requirements-moss-td-cpp.txt
.venv/bin/python scripts/run_cpu_candidate.py --enable-moss-td --moss-td-runtime cpp --moss-td-python .venv-moss-td-cpp/bin/python --moss-td-model-dir models/moss-td-cpp --moss-td-gguf cpp-preparation/quantization/MOSS-Transcribe-Diarize-Q8_0.gguf --data-dir audio-data-moss-td-cpp --dry-run
```

检查 `--dry-run` 后去掉它启动。`run_cpu_candidate.py` 明确使用 CPU；默认触发仍是
`native_overlap`，可显式改成 `--moss-td-trigger speech_probe`，含义和风险见
[MOSS_TD_SETUP.md](MOSS_TD_SETUP.md)。BF16 只需把 `--moss-td-gguf` 改到已校验的 BF16 文件。
GGUF 路径与 `--moss-td-model-dir` 是两个不同设置，安装器不会复制巨大 GGUF。

运行所需内容为：独立 C++ Python 环境、选定 GGUF，以及小型目录中的 LICENSE、MODEL_CARD.md、
复制的 CPP manifest。安装器还保存平台匹配的已验证 wheel，便于离线搬运；
已安装完成的环境不需要在每次推理时读取 wheel 文件。
**运行不需要 Torch、Transformers、转换工具或原始约 1.8 GB safetensors。**
保留来源/许可证和转换记录；只有确认运行目录、GGUF 及环境均已保留并通过校验后，
才可自行整理临时输入、BF16 或转换环境。不要误删仍被 `--moss-td-gguf` 指向的文件。

依赖安装也可复用已验证本地 wheel：在新运行环境内用 `pip install --no-deps --no-index`
安装 `models/moss-td-cpp/wheels/` 下的两个 wheel，再单独安装 `psutil==7.2.2`。
默认的 requirements 文件使用同样的官方 URL 和固定 wheel SHA256。

## Windows 搬运与 Vulkan 的边界

先在 Linux 完成并校验 GGUF，然后复制所选 GGUF 到 Windows 的新目录，例如
`models\moss-td-gguf\`。使用本仓库的新副本执行以下 PowerShell 命令：

```powershell
py -3.12 scripts\setup_moss_td_cpp.py --model-dir models\moss-td-cpp --gguf-path models\moss-td-gguf\MOSS-Transcribe-Diarize-Q8_0.gguf
py -3.12 scripts\setup_moss_td_cpp.py --model-dir models\moss-td-cpp --gguf-path models\moss-td-gguf\MOSS-Transcribe-Diarize-Q8_0.gguf --verify-only
if (Test-Path .venv-moss-td-cpp) { throw "请选择新的运行环境目录" }
py -3.12 -m venv .venv-moss-td-cpp
.\.venv-moss-td-cpp\Scripts\python.exe -m pip install -r requirements-moss-td-cpp.txt
.\.venv\Scripts\python.exe scripts\run_cpu_candidate.py --enable-moss-td --moss-td-runtime cpp --moss-td-python .venv-moss-td-cpp\Scripts\python.exe --moss-td-model-dir models\moss-td-cpp --moss-td-gguf models\moss-td-gguf\MOSS-Transcribe-Diarize-Q8_0.gguf --data-dir audio-data-moss-td-cpp --dry-run
```

Windows 安装器选择官方 `win_amd64` wheel；不要把 Linux wheel 或 Linux venv 复制过去使用。
上面是迁移参考命令，尚无 Windows 实测，不提供已验证的原生 Windows 编译器流程。

主 CLI 的 `--moss-td-cpp-device vulkan` 是显式实验选项；CPU 启动器不会自动选择它。
Vulkan 需要目标机器的设备/驱动检查，目前没有显存或完整 GPU 运行测量，不能承诺
RAM + VRAM 合计满足 6 GiB 目标。失败不能静默改成 CPU 后宣称 GPU 成功。

无论选择哪个引擎，模型时间都是未验证的粗时间，匿名标签只在当前支持片段内有效。
模型新增第二个标签并不证明真实存在第二个人；候选不写入声纹库。
原生运行库还有自适应生成预算：上限可设为 512，但短片段实际可低于 512，
不能把它表述成与官方 Torch 固定 512 完全相同的解码实验。

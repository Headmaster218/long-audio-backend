# dev13：源码安装与可选 FrameMN

适用版本：`0.2.0rc3.dev13`。这是开发源码检查点，未创建 release/tag。已完成基于精确发布源码的标准离线 wheel 构建、全新 `--target` 安装、默认关闭导入/应用构造，以及一次安装包的 30 秒真实模型/API 检查。安装复用了既有合资格环境的依赖；以下命令仍为目标环境操作参考，**尚未作为全新依赖齐备的单环境安装配方完成验收**。先前源码运行与后续安装包运行的独立结果见 [功能检查与边界](DEV13_FUNCTIONAL_CHECK_ZH.md)。

## 1. 从完整源码安装

继续使用 Linux x86-64、CPython 3.12、CPU 的既有部署条件。先准备 FFmpeg/ffprobe、独立 Python 环境、已核验的基础模型与 native runtime。基础及 inference 依赖不变；`requirements-runtime-dev9.lock.txt` 和 `requirements-build-dev9.lock.txt` 保留原名与原内容。资源、许可证及备份要求可参考 [dev10 安装指南](INSTALL_DEV10_ZH.md)，但不要执行其中的 dev10 产品安装命令来安装本版。

以下假设基础/inference 依赖及 setuptools、wheel 构建依赖均已在新环境中准备好。不要覆盖正在服务的环境。在完整 dev13 源码根目录执行，替换示例路径：

```sh
export RUNTIME="$HOME/audio-dev13-runtime"
export ASSETS="$HOME/audio-assets"
export DATA="$HOME/audio-dev13-data"

"$RUNTIME/bin/python3.12" -I -B -m pip install \
  --no-index --no-deps --no-build-isolation --no-compile .
SITE=$("$RUNTIME/bin/python3.12" -I -B -c \
  'import sysconfig; print(sysconfig.get_path("purelib"))')
cp moss-td-cpp-model-manifest.json "$SITE/moss-td-cpp-model-manifest.json"
"$RUNTIME/bin/python3.12" -I -B -c \
  'import audio_backend, importlib.metadata as m; assert audio_backend.__version__ == m.version("long-audio-backend") == "0.2.0rc3.dev13"; print(audio_backend.__version__)'
```

这里从本地源码安装，不联网补齐依赖。若依赖缺失，先按固定依赖方案准备环境，不移除检查或替换运行时来掩盖失败。根级 MOSS C++ manifest 的复制沿用既有安装要求：它不在 wheel 内，安装检查也按此要求单独复制。版本检查只核对安装身份，不证明模型、FrameMN 或可选 cell 能力已运行。

已完成的安装检查采用标准宿主 pip/setuptools/wheel 离线构建，再由合资格运行时的 pip 安装到全新 `--target`；没有解析或安装依赖。wheel 共 118 个成员，其中 114 个包文件包含上游 `LICENSE` 和 `ADAPTATION.json`，全部包文件与发布源码及安装结果逐字节一致。默认关闭检查在 Torch 不可用的环境完成 `Settings`、应用及普通 `AudioAnalyzer` 导入和 55-route 应用构造；worker 关闭，未启动服务、构造 analyzer、读取模型或运行推理。另一次显式启用的安装包真实检查使用事先准备的可选运行时与本地 checkpoint，不能据此推断这里的命令会自动安装它们。

## 2. 默认启动：不启用 FrameMN

以下显式选择既有 SenseVoice、Nemotron 和 CPU；基础模型/native 路径应已存在且核验完成。FrameMN、cells、core seams 和 MOSS jobs 均保持关闭：

```sh
export OMP_NUM_THREADS=2 OMP_THREAD_LIMIT=2
export OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2

"$RUNTIME/bin/python3.12" -I -B -m audio_backend \
  --host 127.0.0.1 --port 8765 \
  --data-dir "$DATA" --model-dir "$ASSETS/models" \
  --threads 2 --memory-limit-gib 6 --device cpu \
  --asr-backend sensevoice --diarization-backend nemotron \
  --native-diarization-model "$ASSETS/models/native-diarization/Nemotron-3-Diarization.q8_0.gguf" \
  --native-diarization-library "$ASSETS/vendor/native-diarization/linux-x86_64-cpu/nemo-speech-0.2.0-linux-x86_64-cpu/lib/libnemo_speech_asr_c.so" \
  --native-diarization-threads 2 --speaker-calibration eres2net-ami20-v1 \
  --overlap-backend none --language auto --window-seconds 30 --event-threshold 0.25
```

一个数据目录只运行一个服务进程，不使用 reload 或多 worker。`--memory-limit-gib` 是采样保护，不是瞬时硬限额。`/health` 与 `/docs` 可用于查看服务和接口；配置 `AUDIO_API_KEY` 时请求须带 Bearer 授权。FrameMN 不新增表或 schema，无需专门数据库迁移；升级前仍须正常停服并完整备份，既有 recording 迁移限制仍适用。

## 3. 显式选择本地 FrameMN checkpoint

只在确实需要实验时间候选时启用。该功能走普通 analyzer/worker，不能与 manifest-owned fragment-provider 参数混用。

1. 在同一个 runtime 中事先准备兼容的 CPU PyTorch、torchvision、torchaudio 和 NumPy。它们不是本项目新增的必装依赖，也不会由开关自动安装。已记录的研究环境版本见 [FrameMN 契约](FRAMEMN_EXPERIMENTAL_CANDIDATES.md)；它们只是历史记录，不是可直接套用或任意混搭的安装配方。
2. 自行确认权重的使用权限，并准备受支持的本地 `frame_mn10_strong_1.pt` 普通文件。MIT 源码许可不等于 checkpoint 再分发许可；本项目不附带或下载权重。固定大小、摘要与标签顺序见同一契约；其中 checkpoint 摘要是本地首次取得时记录的完整性值，不是发布者签发的校验值。
3. 在上面的启动命令末尾增加以下三项，并给前一行补上续行反斜杠：

```sh
  --framemn \
  --framemn-checkpoint /absolute/local/frame_mn10_strong_1.pt \
  --framemn-profile knock-001-median9-experimental-v1
```

这些是同一条启动命令的附加参数，不是独立命令。没有环境变量会隐式开启它。仅提供 checkpoint 而不启用会被拒绝；启用却不提供 checkpoint 也会被拒绝。CLI 可把显式相对路径转为本地绝对路径，URL 不会触发下载。加载器严格核对文件、权重结构与有限数值，不提供任意 pickle 或宽松权重回退。

只有 `knock-001-median9-experimental-v1` 可用：Knock 索引 228，median9 后严格大于 0.01；不是可调的通用事件策略。启用后首次非静音分析才加载可选运行时与模型，并把该 analyzer 进程的 PyTorch intra-op 线程数降为 1；此设置会在调用结束后保留。

## 4. 确认是否真正运行

从既有 `/sessions/{session_id}/event-candidates` 与变化流读取结果。`payload_kind=framemn_temporal_v1` 的行仍是 `unverified_model_candidate`；有候选行不等于有正事件，整条 tile 的 start/end 也不是 Knock 区间。原始 `frame_runs` 与 `owned_sample_runs` 含义不同，需按 [字段契约](FRAMEMN_EXPERIMENTAL_CANDIDATES.md) 解读。

- `framemn_status=completed`：本窗口的可选执行与结果检查完成。
- `failed`：可选旁路失败，查看安全的 failure code/class；不会把部分 FrameMN 行当作成功结果写入，也不替换主结果。
- `not_run_silence`：静音跳过，未进入 FrameMN 推理，实际及预期调用均为零。

chunk 普通 metadata 仅描述最后提交窗口；多窗口总量看 `chunk.provenance.framemn_execution` 的提交窗口与调用计数。配置了摘要或看到 `/health` 成功都不能替代执行证据。关闭时移除三项 FrameMN 参数即可；既有候选历史不会因此自动删除。

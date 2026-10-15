# dev10：安装、启动与数据升级

适用版本：`0.2.0rc3.dev10`，开发分支，未创建 release/tag。本页使用普通 `venv`、pip、curl、tar 和产品 CLI，不需要专用服务入口或候选文件覆盖。以下命令供部署者在自己的目标环境执行；安装成功不等于取得目标环境的模型质量、长期运行或可选存储资格。

## 1. 环境与能力边界

固定依赖锁针对 Linux x86-64、CPython 3.12；其中部分 wheel 要求 glibc 2.28 或更高版本。准备 FFmpeg/ffprobe、curl、tar、bzip2，以及足够保存模型、下载包、运行环境和数据的本地空间。模型资源约 1.48 GB 的压缩输入还不包括全部解包、环境、数据和安全余量。请保留至少服务配置要求的磁盘空闲量，不要把复制预算当作整个部署的磁盘上限。

实际短样本运行使用 CPython 3.12.14 和已验证的特定 runtime。native-owned-cell 存储会检查解释器字节及真实路径摘要、SQLite 绑定/编译选项/标准库、VFS、文件系统设备和分配条件。**装上相同包版本不等于获得该可选能力资格。** 新机器或新环境可能显示 optional owner unavailable/unqualified；不要修改资格表、伪造 receipt 或绕过检查。主 recordings 去重/转写可以在关闭这些可选能力时使用，但不能声称已经运行 cell/MOSS 完整路径。完整可选能力需另行完成目标环境的资格验证。

项目根目录的 `requirements-runtime-dev9.lock.txt` 和 `requirements-build-dev9.lock.txt` 原样采用已发布的[固定依赖配方](https://github.com/Headmaster218/long-audio-backend/tree/f45777beb72016dc5cd7e1d2508643f50a15a9e8/preparation/minimal-recovery-wip53)。dev10 没有增加或更换依赖，两个锁文件保留 dev9 文件名与原内容。它们只是依赖和资源版本参考；产品从当前 dev10 源码安装，不执行该历史配方中的恢复驱动或覆盖步骤。

## 2. 安装产品

先取得完整 dev10 产品源码并进入仓库根目录。将以下路径设为你自己的绝对路径；环境和构建目录应为全新目录，路径中不使用符号链接。示例不会修改既有 runtime。

```sh
export RUNTIME="$HOME/audio-dev10-runtime"
export BUILD="$HOME/audio-dev10-build"
export ASSETS="$HOME/audio-dev10-assets"
export DATA="$HOME/audio-dev10-data"

python3.12 -m venv --copies "$RUNTIME"
python3.12 -m venv --copies "$BUILD"
"$RUNTIME/bin/python3.12" -I -B -m pip install \
  --require-hashes --only-binary=:all: --no-deps --no-cache-dir --no-compile \
  -r requirements-runtime-dev9.lock.txt
"$BUILD/bin/python3.12" -I -B -m pip install \
  --require-hashes --only-binary=:all: --no-deps --no-cache-dir --no-compile \
  -r requirements-build-dev9.lock.txt
"$BUILD/bin/python3.12" -I -B -m pip wheel \
  --no-deps --no-build-isolation --no-cache-dir --wheel-dir "$BUILD/wheels" .
"$RUNTIME/bin/python3.12" -I -B -m pip install --no-deps --no-compile \
  "$BUILD/wheels/long_audio_backend-0.2.0rc3.dev10-py3-none-any.whl"

SITE=$("$RUNTIME/bin/python3.12" -I -B -c 'import sysconfig; print(sysconfig.get_path("purelib"))')
cp moss-td-cpp-model-manifest.json "$SITE/moss-td-cpp-model-manifest.json"
"$RUNTIME/bin/python3.12" -I -B -c \
  'import audio_backend, importlib.metadata as m; assert audio_backend.__version__ == m.version("long-audio-backend") == "0.2.0rc3.dev10"; print(audio_backend.__version__)'
```

复制根级 MOSS C++ manifest 是必要步骤：现有运行时按已安装包的相邻位置读取它。不要用旧的已安装 `audio_backend` 加临时 `PYTHONPATH` 或零散文件覆盖来代替安装。包版本/import 检查只验证安装身份，不加载模型，也不证明可选能力 ready。

如需离线安装，先在匹配平台获取锁文件指定的 wheel，并保留每个哈希；上述 pip install 添加 `--no-index --find-links /你的/wheel目录`。不要移除哈希检查或自动升级为其他 wheel 来掩盖不匹配。

## 3. 固定模型与 native runtime

先审阅 [FunASR 模型许可证](https://github.com/modelscope/FunASR/blob/main/MODEL_LICENSE)、[OpenMDW-1.1](https://raw.githubusercontent.com/OpenMDW/OpenMDW/main/1.1/LICENSE.OpenMDW-1.1) 和 [Handy MOSS 模型说明](https://huggingface.co/handy-computer/moss-transcribe-diarize-gguf/tree/bfa3d24438711391d8713c6ab0efd6264527757c)。保留原始许可证、模型来源和 native 包的所有第三方 notices；源码许可不替代模型许可。

已拥有匹配资源时可复用经哈希校验的只读模型目录。全新安装可以使用以下普通下载/解包步骤；在空资源目录执行，并让 shell 在失败时停止。下载失败或哈希不同就停止检查，不在未验证资源上继续。

```sh
set -eu
mkdir -p "$ASSETS/downloads" "$ASSETS/models/sensevoice" \
  "$ASSETS/models/native-diarization" \
  "$ASSETS/vendor/native-diarization/linux-x86_64-cpu" "$ASSETS/licenses"

curl --fail --location --limit-rate 4M --proto '=https' --proto-redir '=https' \
  'https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx' \
  -o "$ASSETS/models/speaker.onnx"
curl --fail --location --limit-rate 4M --proto '=https' --proto-redir '=https' \
  'https://github.com/k2-fsa/sherpa-onnx/releases/download/audio-tagging-models/sherpa-onnx-zipformer-small-audio-tagging-2024-04-15.tar.bz2' \
  -o "$ASSETS/downloads/events.tar.bz2"
curl --fail --location --limit-rate 4M --proto '=https' --proto-redir '=https' \
  'https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2' \
  -o "$ASSETS/downloads/sensevoice.tar.bz2"
curl --fail --location --limit-rate 4M --proto '=https' --proto-redir '=https' \
  'https://raw.githubusercontent.com/modelscope/FunASR/main/MODEL_LICENSE' \
  -o "$ASSETS/models/sensevoice/UPSTREAM_MODEL_LICENSE"
curl --fail --location --limit-rate 4M --proto '=https' --proto-redir '=https' \
  'https://huggingface.co/nvidia/Nemotron-3-Diarization/resolve/f667ed7/Nemotron-3-Diarization.q8_0.gguf' \
  -o "$ASSETS/models/native-diarization/Nemotron-3-Diarization.q8_0.gguf"
curl --fail --location --limit-rate 4M --proto '=https' --proto-redir '=https' \
  'https://github.com/NVIDIA/NeMo-Speech.cpp/releases/download/v0.2.0/nemo-speech-0.2.0-linux-x86_64-cpu.tar.gz' \
  -o "$ASSETS/downloads/native.tar.gz"
curl --fail --location --limit-rate 4M --proto '=https' --proto-redir '=https' \
  'https://huggingface.co/handy-computer/moss-transcribe-diarize-gguf/resolve/bfa3d24438711391d8713c6ab0efd6264527757c/MOSS-Transcribe-Diarize-Q8_0.gguf' \
  -o "$ASSETS/models/MOSS-Transcribe-Diarize-Q8_0.gguf"

(
  cd "$ASSETS"
  sha256sum --check <<'SHA256'
1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b  models/speaker.onnx
07e2fafcdcbc461f2816188d9b0bbafced12584030cf67d5652e549ef256a2c6  downloads/events.tar.bz2
7d1efa2138a65b0b488df37f8b89e3d91a60676e416f515b952358d83dfd347e  downloads/sensevoice.tar.bz2
7dba975a2069691db4992b0592d70828b330d2f8a30a71450f4e152a554e84f8  models/sensevoice/UPSTREAM_MODEL_LICENSE
08456d9e22cd9a323c0364d98375f3746d6e68507ebb705cd46438c534c7a3a1  models/native-diarization/Nemotron-3-Diarization.q8_0.gguf
f396057150f1b774935c7414fd32ebc04195a795a34120d78d8c9ecefa1b7507  downloads/native.tar.gz
64ec654dc6ffcfdfe180422dffce1d33422b0c30959b7edfd131bad77ee35039  models/MOSS-Transcribe-Diarize-Q8_0.gguf
SHA256
)

SENSE=sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17
EVENTS=sherpa-onnx-zipformer-small-audio-tagging-2024-04-15
tar -xjf "$ASSETS/downloads/sensevoice.tar.bz2" \
  -C "$ASSETS/models/sensevoice" --strip-components=1 \
  "$SENSE/model.int8.onnx" "$SENSE/tokens.txt"
tar -xjf "$ASSETS/downloads/events.tar.bz2" -C "$ASSETS/models" \
  "$EVENTS/model.onnx" "$EVENTS/class_labels_indices.csv"

mkdir "$ASSETS/downloads/native-unpacked"
tar -xzf "$ASSETS/downloads/native.tar.gz" -C "$ASSETS/downloads/native-unpacked"
cp -aL "$ASSETS/downloads/native-unpacked/nemo-speech-0.2.0-linux-x86_64-cpu" \
  "$ASSETS/vendor/native-diarization/linux-x86_64-cpu/"
cp licenses/native-diarization/OpenMDW-1.1.txt "$ASSETS/licenses/"
```

`cp -aL` 将已校验官方 native 包中指向包内文件的别名复制成普通文件，保留全部依赖和 notices；不能只拿一个 `.so`。上面保留下载与解包输入，方便检查。SenseVoice 许可证地址来自固定配方，但上游 main 可能更新；若许可证哈希变化，先检查条款与资源来源，不忽略失败。

还应核对解包后的四个运行文件：

```sh
(
  cd "$ASSETS/models"
  sha256sum --check <<'SHA256'
c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51  sensevoice/model.int8.onnx
f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc  sensevoice/tokens.txt
e61bdfaab92b48bc51209c0da91621fb8d89d1564bb5bb38be6e4a4380c1ed34  sherpa-onnx-zipformer-small-audio-tagging-2024-04-15/model.onnx
cdd1049833c4b86127c2773ac0d14a2754b6a6d0d1798002ed5c66e699708429  sherpa-onnx-zipformer-small-audio-tagging-2024-04-15/class_labels_indices.csv
SHA256
)
```

本例直接使用下面的产品 CLI，不生成旧 CPU 启动器要求的 installation receipt。若改用旧启动器，应遵守其安装/校验流程，不能伪造 receipt。Handy Q8 的固定哈希如上，不能替换为旧通用 MOSS manifest 中另一个本地量化模型。

## 4. 普通 localhost 启动

先确认 `ffmpeg`、`ffprobe` 在 PATH。以下是主 recording 路径，显式选择 SenseVoice/Nemotron/CPU，保留默认关闭的 cell/MOSS 能力：

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

要在已具备资格的环境使用本次完整 cell/MOSS 路径，在同一命令末尾显式增加：

```sh
  --native-owned-support --native-owned-cells --native-owned-seam-cells \
  --moss-jobs --moss-jobs-model "$ASSETS/models/MOSS-Transcribe-Diarize-Q8_0.gguf"
```

这是命令参数续行，不是另一个 shell 命令；在前一行末尾加续行反斜杠后合并使用。

- recordings 本身由使用新路由显式选择，没有额外的 recording-enable CLI 开关。
- `--native-owned-support` 是此 job 配置的前提；cells 启用被动持久化，seam-cells 依赖 cells；`--moss-jobs` 只开放显式任务，不自动启动 MOSS 推理。
- 必须使用普通 Worker、Nemotron、CPU、`overlap-backend=none`；不要混入 fragment provider 或自动 overlap backend。不要设置 native loader 覆盖变量来绕过固定运行时校验。
- 一个数据目录一个服务进程，不使用 reload/multi-worker。两线程参数不是总 OS 线程数承诺；6 GiB 参数是采样保护，不是瞬时硬限额。
- `/health` 和 `/docs` 可查看能力与接口。若配置 `AUDIO_API_KEY`，所有请求加 Bearer 授权，包括 health。不要把凭据写进公开日志或示例。

## 5. dev8/dev9 升级、schema 与数据保留

先停止新输入，等待工作和清理到可控状态，正常关闭旧服务并确认进程退出；完整备份数据目录和对应旧 runtime。备份应包含数据库、上传文件、可选存储和仍存在的日志文件。不要复制正在写入的零散 SQLite 文件，不要手工删除 WAL/journal。

dev9 已引入以下 recording schema；dev10 沿用它们，不因本次三项修正新增 schema。由 dev8 升级时，`Store` 初始化以事务化的 `CREATE TABLE IF NOT EXISTS` / 检查列后 `ALTER TABLE` 增加主数据库 schema：

- `recording_batches`：session/capture、revision、状态、finish key/request、origin/end、manifest 和 blockers。
- `recording_sources`：原始文件路径、编码摘要、格式、帧数、坐标和 idempotency receipt；另有 `recording_source_path` 索引。
- `recording_work`：派生 chunk、位置和 native receipt 状态，增加 `native_receipt`（最多 1024 字节的 BLOB）、`receipt_id`、`receipt_state`、`receipt_attempts`。
- `recording_ranges`：source-local → capture → work-local 的 owner/exact_alias 映射。

不需要手动执行 SQL。即使不使用 recordings，正常 Store 初始化也会建立这些表。native-owned-cell 可选数据库的 schema、pragmas 和既有配额不因本版变化。

首次升级优先保持数据目录的原绝对路径。正常启动会恢复完整 `pending_receipts` outbox，并要求当前 owner 重新确认；未完成的 preparing/staging 留为 interrupted。已结束或取消的批次不会被重新打开。若环境不具备可选 owner 资格，某些 pending 批次可能持续等待；先查询状态，不要手改 receipt 或 chunk 行。

## 6. 移动目录与回退限制

旧 `scripts/migrate_audio_data.py` 主要迁移 `chunks.path`；本版还有 `recording_sources.path`、receipt 文件身份及批次 manifest 绑定。**含 recording 数据的目录没有在本版提供通用跨路径迁移工具。** 不要把旧工具的成功当作这些数据已可安全迁移，不要绕过 startup path guard。先保留原目录，另行验证完整迁移方案；全新部署可用新空数据目录，并从独立保留的原始录音重新提交。

已有普通数据且没有 recording 数据时，可按原 [DATA_MIGRATION.md](DATA_MIGRATION.md) 的适用范围处理；涉及可选 cell 存储的身份/环境变化仍需单独检查。

回退源码或 wheel 不会回退 schema、文件与 outbox。dev8 及更早版本不知道 recording originals 和 pending 状态，直接用这些版本打开已升级目录可能误处理新数据。dev10 降回 dev9 也不能仅凭 schema 未变就认定完整兼容；回退后的可选行为需要独立验证。需要回退时，正常停服并保留整个已升级数据目录；使用升级前完整备份、匹配的旧 runtime 和原路径恢复旧服务，或者为旧服务创建独立空目录。升级后的新增数据不会自动合并进旧备份。不要 drop 新表、删除音频、改状态或删除日志来强行兼容。

## 7. 检查与测试

先做版本/import 检查，再启动并确认主路径，最后单独检查 optional owner 和显式 job。完整步骤见 [API 指南](TESTED_API_GUIDE_ZH.md)。没有自然 eligible cell 时应记录未运行 MOSS，不可伪造 proof。

开发测试应使用独立测试环境。本次隔离回归另从官方 Python 包注册表安装测试专用的 `pytest==9.1.1`、`httpx2==2.13.1` 和 `httpcore2==2.13.1`；它们不加入生产依赖锁。项目原有 test extra 的 `httpx>=0.27,<1` 保持不变，仅安装该 extra 不能视为复现本次测试环境。已检查的 Starlette 仍有 legacy httpx fallback，但本轮没有验证那条路径。

`python -m pytest tests/test_recording_overlap.py` 可运行既有录音专项，但不覆盖 dev10 全部新增行为。dev10 的 205 个既有选定用例与 46 个可移植新增用例已分两次源码回归通过，两项测试假设造成的失败及修正后的结果分别保留。标准离线 wheel 构建、安装/import 与一次使用已有真实模型的固定 HTTP smoke 已通过；该次返回四个 retained cell，没有 dispatch MOSS，产物和结果已完成独立复核，范围以该固定用例为限。完整证据及此前真实样本的未复现项见[开发分支说明](DEV10_RELEASE_NOTES_ZH.md)，不能合并成一次全面验收。新环境安装成功、机制测试通过和模型结果质量是不同结论。

# 长录音音频分析后端

本地单用户 Python API：中文优先的中英转写、独立说话人活动、跨录音声纹关联、环境声音候选、增量上传与断点恢复。推理不调用外部 API，不上传录音，不做 LLM 总结。

**开发中，尚未通过整体质量验收。当前没有正式 release。** 本仓库只公开审阅后的源码、依赖清单、安装/API 文档和合成单元测试。原始录音、转写真值、声纹向量、模型、运行数据库和完整研究记录不在仓库中。

## 开始使用

要求 Python 3.12+ 和 FFmpeg/ffprobe。当前 native 安装器支持 Linux/Windows x86_64；Windows 执行、Vulkan 和 GPU 显存尚未验证。

请先阅读 [中文安装指南](docs/QUICKSTART_ZH.md)中的模型许可证，再显式下载所需模型。基础 CPU 候选为 SenseVoiceSmall int8 + Nemotron-3 Q8 + ERes2Net + Zipformer-small，重叠候选默认关闭。无参数 CLI 仍使用兼容默认，具体差异见安装指南。

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[inference,test]'
python scripts/setup_models.py --sensevoice
python scripts/setup_native_diarization.py
python scripts/run_cpu_candidate.py --dry-run
python scripts/run_cpu_candidate.py --data-dir ./audio-data-cpu-candidate
```

首次准备依赖/模型需要联网；推理使用本地文件。`--dry-run` 验证所需本地资产，不加载模型或监听端口。保持 localhost；每个数据目录只允许一个服务进程。

- [API 契约](docs/API.md)
- [队列、连续流和独立候选](docs/QUEUING_AND_CANDIDATE_LEDGERS.md)
- [已有数据的安全迁移](docs/DATA_MIGRATION.md)
- [可选 MOSS-TD](docs/MOSS_TD_SETUP.md)与 [CPP/GGUF 准备](docs/MOSS_TD_CPP_SETUP.md)
- [声纹边界](docs/SPEAKER_REGISTRY.md)和[质量目标](docs/GOALS_AND_ACCEPTANCE.md)

## 已知边界

转写时间不是已验证的逐词强制对齐。重叠、短促发言、远场及跨日期身份仍会出错。身份余弦分数不是识别概率，普通自动匹配不会更新参考声纹。身份研究不会自动成为在线功能。

目标是服务 RAM + 独立 VRAM 合计不超过 6 GiB，部署上限 10 GiB；CPU 结果不能证明 GPU/Windows 资源结果。`/health` 只证明存活和配置，单元测试通过不证明模型质量或长期稳定性。

## 测试和版本管理

```sh
python -m pytest -q
python scripts/check_public_tree.py
```

测试以合成输入和假分析器为主，不下载数据或模型。部分工作进程测试要求项目所在磁盘至少 5 GiB 空闲。明确的范围、结果和未运行项见 [TEST_STATUS.md](TEST_STATUS.md)。

`main` 保存审阅后的来源基线，`develop` 接收通过检查的集成变更，研究内容使用独立 `research/` 分支。版本说明与安全同步流程见 [CONTRIBUTING.md](CONTRIBUTING.md) 和 [VERSIONING.md](VERSIONING.md)。

项目自有代码的许可证尚待所有者决定；没有附加开源授权。第三方模型、运行时和 notices 保留各自许可，公开本仓库不改变其条款。参见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

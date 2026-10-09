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

## 可选完整声学证据回放

新增 [Phase A 声学支持回放](docs/EMBEDDING_SUPPORT_LINEAGE.md)默认关闭。显式使用 --speaker-support-review 后，可在独立配额内保留完整、哈希绑定的实际输入片段供只读复听。它不启用身份学习、自动声纹更新或纯净度认证；已有身份匹配语义保持不变。

环境声音候选同时保留真实本地模型/标签表哈希、有效策略和是否实际执行模型的标记。旧记录不补写猜测来源。

## 实验性人工核验身份库

[B1/B2 人工核验身份模式](docs/VERIFIED_IDENTITY.md)默认关闭。需同时显式启用声学支持回放、选择 [experimental policy](audio_backend/profiles/verified-review-experimental-v1.json)，并为新身份组指定 `verified_review_v1`。已有组不会自动转换；普通默认身份流程保持原有行为。

正参考只来自操作者对全部 pooled support 的明确核验声明，包括前后上下文和贡献片段。服务记录声明，不能证明真人确实听过、标签正确或没有混声。自动结果标为 `experimental_identity_match`，不会自动成为参考，也不会自动更新模板。

- 阈值尚未校准；不能套用其他前端的身份准确率结论
- 目前仅支持[精确审计的 Linux x86_64 运行时、模型和 NumPy 组合](docs/EMBEDDING_SPACE_PROVENANCE.md)；Windows、未知构建或不兼容空间会拒绝匹配
- 客户端必须处理 `identity_bank` 修订失效事件；reset 还要求清除旧代全部身份缓存，包括人工标签
- verified 模式的 merge/undo 暂不支持；公开源码和单元测试不代表长期身份质量或正式 release 验收

原始向量和回放证据只在启用后的本地私有数据目录中受配额保留，不属于公开源码。逻辑删除不等于 SQLite/WAL、导出和备份的取证级擦除。

已完成一次 [36 秒真实 HTTP 功能门禁](docs/VERIFIED_IDENTITY_HTTP_SMOKE.md)，操作者声明使用脚本 fixture，且包含已披露的测试脚本修复。它不代表真人核验、身份准确率、跨日期或长录音验收。

## 默认关闭的离线片段实验

`0.2.0rc2.dev4` 增加[离线模型桥、独立 PCM 归一化和边界实验工具](docs/FRAGMENT_EXTENSIONS_INTERNAL.md)。保持默认关闭；没有新增 HTTP 接口、日期/上传整合或端到端产品验收。归一化器仍是独立类，尚未接入运行器、模型桥或压缩输入。

早期真实模型基础矩阵中的事件数组全部为空，只能说明负事件一致性。另行开展的四臂正事件实验使用已暴露的选定合成开发输入，证明其有限的非空 30 秒接缝覆盖，不代表普遍召回率。原生 P2 首次报告大小失败保留，后续单独准入的剩余分臂通过不能写成一次不间断成功。

p1 静音匹配候选仍需显式选择，历史 q1 已拒绝并保留严格失败控制。来源连续性和排除真实重放依赖调用方可信声明，相同 PCM 不能证明它们。原始支持区间和逻辑锚点完整保留，内部路由锚点仅确定归属。

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

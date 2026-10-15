# 长录音音频分析后端

本地单用户音频 API，提供增量转写、独立说话人活动、声音事件、来源回执和断点恢复。推理使用本地模型，不调用外部转写服务。

## 0.2.0rc3.dev9：有限录音批次的精确重叠去重

本版本将已知共享采样坐标的录音批次直接接入普通 `legacy` API。客户端先上传全部原始 PCM16 WAV，再明确 finish；只有重叠样本逐字节一致、坐标完整且并集连续时，才生成去重后的工作片段。原始文件与来源映射保留。未知位置、不同 PCM 或空隙会留下可检查的 unresolved 批次，不会丢弃冲突来源或猜测对齐。

- [中文安装、启动、升级和回退](docs/INSTALL_DEV9_ZH.md)
- [中文 API 操作指南](docs/TESTED_API_GUIDE_ZH.md)
- [录音批次契约与资源边界](docs/RECORDING_OVERLAP_BATCHES.md)
- [dev9 发布说明与验证范围](docs/DEV9_RELEASE_NOTES_ZH.md)
- [既有普通上传、结果与控制 API](docs/API.md)

这是完整产品源码中的功能，不需要另行覆盖候选文件或使用专用服务入口。安装后通过 `python -m audio_backend` 启动；依赖、模型和数据目录由部署者准备，不随源码分发。

## 当前验证范围

对应功能源码已通过 41 个录音批次专项用例、12 个既有机制回归，以及测试运行器自检。一次真实 AMI 自然重叠短样本运行将累计 27 秒的重叠上传精确归并为 21 秒唯一 PCM，产生两个 10.5 秒主工作片段和 16 个 segments；两个 native receipt 均持久化，保留 3 个 cell、丢弃计数为 0，一个自然 seam 的显式 MOSS 任务返回诊断，result/raw 均可读取，已知子进程清理完成。该次保存结果已通过独立复核。

dev9 仅在上述已测功能源码之外更新包版本字符串、安装锁文件和文档。它没有重新进行模型运行，不代表新环境已获得可选 cell 存储资格，也不代表文字准确率、CER、全局人物身份或重叠内容完整性通过。

## 使用边界

- 一个 capture 声明一个共享 PCM 采样坐标域，并独占一个全新空 `legacy` session。不能把多个麦克风仅凭时间戳视为同一坐标域。
- 输入为单声道、16 kHz、16-bit little-endian PCM WAV。每批最多 16 个来源、32 个派生片段、300 秒唯一音频、32 MiB 录音复制预算；全局录音复制预算 128 MiB，实际还受更严格的服务配置和磁盘余量约束。
- 本版只支持全部输入齐备后的 finalized batch，不支持连续 sealed-prefix、未知坐标或近似波形对齐、48 小时录音缓冲、按来源撤销或单来源删除。
- 原有 `/chunks` 路由仍拒绝时间区间重叠。MOSS 是显式、proof 绑定的独立诊断，不自动替换主转写，不自动建立人物身份。
- 默认不开启 native-owned cells、seam cells 或 MOSS jobs。即使显式开启，可选 cell 存储也只在匹配的受验证 runtime/SQLite/VFS/文件系统条件下启用；普通新环境可能仅能使用主路径。

## 部署原则

当前固定依赖路径针对 Linux x86-64、CPython 3.12、CPU。Windows、GPU 与新环境的长期运行尚未由本版本验证。直接无参数启动仍保留兼容默认配置；请使用安装指南中的显式 SenseVoice/Nemotron 参数。

一个数据目录只运行一个服务进程，不使用多 worker 或 reload。默认绑定 `127.0.0.1`；跨设备暴露服务前自行配置访问控制和安全传输。`/health` 表示存活和能力状态，不表示模型内容正确。

已有数据升级前须正常停服并完整备份。含新 recording 数据的目录不能直接交给旧版本或仅改写 `chunks.path` 的旧迁移工具。详见安装指南的迁移和回退限制。

模型和 native runtime 使用各自许可证。SenseVoice 使用 FunASR 模型许可证，Nemotron 使用 OpenMDW-1.1，MOSS Handy Q8 与 native runtime 的许可证和第三方 notices 亦须保留。下载和分发前请审阅对应上游条款。

# 已有音频数据的离线迁移

`chunks.path` 保存绝对路径。**仅复制 audio-data 目录还不够；首次启动新目录前必须迁移路径。** 旧版启动清理曾可能把新位置的音频误认为孤立文件而删除。当前版本会在清理前拒绝不属于当前目录的路径，提示先迁移；不会自动改数据库。

先在原机器暂停任务，等待工作器空闲，正常停止服务。保留完整原目录备份，再复制整个目录，包括 jobs.sqlite3 和 uploads 中的音频到新位置。不要复制正在写入的零散文件，不要用符号链接或硬链接代替数据库副本。若仍有非空 WAL/journal，先在原目录完成正常停止和检查点再重新复制；**不要手动删除 WAL/journal**，其中可能有已提交数据。若有待处理删除意图，在原服务完成清理后重新停止、复制。

以下命令只对新副本操作，默认 dry-run，不启动模型或服务。需要 Python 3.12+；应用使用连接局部的 SQLite trigger 控制，以免路径更新产生伪造 change-feed 事件。

Linux／macOS 示例：

```sh
python scripts/migrate_audio_data.py --data-dir /new/audio-data --from-root /old/audio-data
python scripts/migrate_audio_data.py --data-dir /new/audio-data --from-root /old/audio-data --apply
```

将 Linux 数据复制到 Windows 后，在目标 Windows 上执行：

```powershell
.\.venv\Scripts\python.exe scripts\migrate_audio_data.py --data-dir 'D:\AudioBackend\audio-data' --from-root '/old/linux/audio-data'
.\.venv\Scripts\python.exe scripts\migrate_audio_data.py --data-dir 'D:\AudioBackend\audio-data' --from-root '/old/linux/audio-data' --apply
```

反向迁移时 `--from-root` 可用原 Windows 路径，例如 `'C:\Users\Alice\audio-data'`，UNC 绝对路径也按 Windows 语义解析，不依赖当前主机的路径格式。必须提供旧数据目录，不是旧 uploads 目录。

工具取得与服务兼容的 instance.lock 和 SQLite 独占事务（包括 dry-run，需要可写的新副本；取得写锁后开启 query_only，不写入行），拒绝运行中的工作状态、非空清理意图、脏日志、非法／越界路径、符号链接、重复目标、缺失文件和 SHA256 不一致。只接受服务实际生成的 uploads/32位小写十六进制.audio 路径。全部文件验证通过后，--apply 在同一事务中只改 chunks.path；ID、时间、状态、声纹向量、结果历史及其他所有表行通过前后逻辑摘要验证保持一致。任何失败都回滚。

Dry-run 不修改数据库行或音频，也不更改主数据库内容；为协调并发，可能创建／写入 instance.lock，以及 SQLite 的空 WAL/SHM 协调文件，不能宣称整个目录字节完全不变。工具不调用自动清理，也不打开原数据库进行写入。迁移完成后再使用正常启动器，将 --data-dir 指向新目录。

若提示“running job state”，不要直接把数据库状态改掉。回到原位置通过正常服务暂停／恢复状态、停止服务并重新复制。若提示旧路径不匹配，先只读确认原数据目录，不要随意猜测路径。

验证范围：使用合成数据库和音频文件测试了 POSIX、Windows、UNC 来源路径、原始结果／向量保持、锁、回滚、坏哈希、链接和清理意图。Windows 实际执行、模型运行及操作系统锁行为仍需目标机验证；这些 Linux 上的路径格式测试不能冒称 Windows 运行验证。未修改任何冻结长测数据库或用户电脑。

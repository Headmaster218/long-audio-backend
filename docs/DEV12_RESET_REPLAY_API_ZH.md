# dev12 reset-replay 启用与 API 补充

适用于 `0.2.0rc3.dev12` 源码。产品树选定回归已通过 184 个测试及 103 个 subtests，标准离线 wheel、隔离 target 安装和合资格 import/CLI 检查通过；103 个包文件逐字节一致，随后仅刷新文档。未对安装后的包重跑 HTTP 或模型。独立候选的 52 个用例、真实案例及原失败保留情况见[发布说明](DEV12_RELEASE_NOTES_ZH.md)，与产品回归分别计数。上传、主结果、cell 分页与 MOSS 路由沿用[既有 API](TESTED_API_GUIDE_ZH.md)。

## 1. 主动选择新的可选 store

依照[安装与模型配置指南](INSTALL_DEV10_ZH.md)，使用操作者选定的独立新数据目录，并在原启动命令中增加：

```sh
--native-owned-cells --native-owned-core-seams
```

保留旧数据目录和旧 cells，不删除、覆盖或改写旧 store 来通过 policy 检查。新组合 store policy 为 `aa94af7e9ddd4e6c28ad5203bc9dc00ff47f62e96b848f42089276a4d4b32a2b`；旧 dev11 store 不会自动采用、迁移或回填。这个要求不否定同策略 store 中已经持久化的合法 pending proof 的恢复。

仅给 core-seams 开关仍会被拒绝；不会隐式开启旧 v2 seam cells 或 MOSS。显式 MOSS 还需要原有 `--native-owned-support --moss-jobs --moss-jobs-model "$MOSS_MODEL"` 和受支持的 Nemotron、CPU、普通 worker、无 overlap/fragment provider 配置。旧安装指南中的包验证结果属于旧版。

查看 `/health` 的实际资格与状态；不得改写 runtime/SQLite/VFS/文件系统资格记录。关注 `native_owned_cells.ready`、`warning`、`dropped`、`cleanup_pending`、`handoffs_active_or_pending`、`core_seam_pending`、`last_capture_reason` 和 `last_core_seam_reason`。本功能的 `core_seam_reset_support` 为 `observed_replay`，只声明支持这里的严格机制，不保证每个 reset 都生成 cell。

## 2. 先核对主结果，再发现 cell

按既有上传流程等待主 chunk 的明确终态。主结果成功与可选 cell 发布分别判定；缺少合格 overlap、短侧、来源变化或容量拒绝都可能留下主结果而没有 cell。使用本次响应中的变量，勿复用其他运行的标识：

```sh
curl --fail-with-body "$API/sessions/$SID/native-owned-cells?after=0&limit=20"
curl --fail-with-body -i "$API/sessions/$SID/native-owned-cells/$CELL_ID"
```

按 `has_more`、`next_cursor` 和 `expected_jobs_epoch` 完成分页，epoch 变化后重新发现。只选自然生成、`state=retained`、实际保留 PCM 且 eligible 的 cell，先检查 schema：

- 同 epoch 仍为 `native-owned-core-seam-v3`，policy 为 `d2ab011178c574034d80aff99d0a72979c78c0b6917c5e6e15c9ac990054db39`，coverage.kind 为 `same_epoch_core_seam_alternative`。
- 跨 reset 必须为 `native-owned-reset-replay-core-seam-v1`，policy 为 `ef4ddd10a08e9570752c256b382c88e0f5cdaa12c4f86b0f5514a7d8310d892c`，coverage.kind 为 `reset_replay_core_seam_alternative`。不可替换 schema 或 policy 来解释旧 proof。

对 reset proof 核验两项 `commits`：同一 fresh capture run、session/坐标/runtime；epoch index 和观察到的 reset serial 各递增 1，epoch ID 不同；左 core 终点和右 core 起点严格等于同一 300 秒网格边界。两侧各贡献 10 秒，精确 support 为 [boundary−10,boundary+10)。query 绑定右侧新 epoch，只含其本地标签，当前响应覆盖完整 support，replay/processed 范围与来源 PCM 均匹配。coverage 的 `primary_scope_sha256` 绑定两侧，`query_scope_sha256` 绑定右侧。

继续核对 `coverage.adds_primary_audio=false`、`coverage.automatic_dispatch=false`、dependencies、receipt、来源哈希与精确 frame range。support 跨界不代表选择的重叠对自身跨界，也不代表标签对应全局人物身份。

按原始 proof 响应字节核对 `X-Native-Cell-Proof-SHA256`，并保存 `X-Native-Cell-Sequence`、`X-Native-Cell-Revision`、`X-MOSS-Jobs-Epoch`。`support_id` 是完整 support 的 canonical JSON SHA-256（UTF-8、键排序、无多余空格、禁止非有限数），不能仅根据时间区间猜测。没有合格 cell 就不请求 MOSS。

## 3. 请求并核验一次显式 MOSS 诊断

先按既有指南确认 permit idle、无 primary waiting 或 optional admission 阻塞、owner 无待交付工作且清理完成。历史 reason、pending 归零或一次 idle 均不能单独证明 publication 成功。

新 schema 继续使用已有路由和 `coverage_intent="overlapping_diagnostic"`：

```sh
curl --fail-with-body -X POST "$API/sessions/$SID/native-owned-cells/$CELL_ID/moss-jobs" \
  -H 'Content-Type: application/json' -H "Idempotency-Key: $REQUEST_KEY" \
  -d "{\"jobs_epoch\":\"$EPOCH\",\"expected_cell_sequence\":$SEQ,\"expected_cell_revision\":$CELL_REV,\"expected_proof_sha256\":\"$PROOF_SHA\",\"support_id\":\"$SUPPORT_ID\",\"profile\":\"handy-moss-q8_0-cpu-experimental-v1\",\"coverage_intent\":\"overlapping_diagnostic\"}"
```

严格请求 body 最多 1,024 字节，不附任意音频、文件路径或自选模型。同一 session/key/target/body 的精确重试复用原 job，改变绑定会冲突；每个新的诊断使用新的 key。轮询超时不等于可以另发任务。

```sh
curl --fail-with-body "$API/sessions/$SID/moss-jobs/$JOB_ID"
curl --fail-with-body "$API/sessions/$SID/moss-jobs/$JOB_ID/result"
curl --fail-with-body "$API/sessions/$SID/moss-jobs/$JOB_ID/raw"
```

只有明确 `returned_diagnostic` 后才核对 result/raw、cell_binding、raw 哈希、`process_cleanup_verified` 与 `cleanup_pending=false`。分别保留 failed/cancelled/invalidated、内容不可用和清理未确认。job/result 是有期限的 volatile 状态，不保证跨重启恢复。MOSS 不自动覆盖主转写。

## 4. 缺口、容量与重试的边界

- `core_reset_short_side`：一侧不足 10 秒，状态为 `gap`，没有 pending proof。不会通过缩短 support 伪造 reset 证据。
- `core_reset_transition`：reset serial 或 epoch 转换不满足连续要求，包括观察新 reset 后、proof 持久化前的重启。记录诊断缺口；主任务可重试，不代表该 seam 会补齐。
- `core_reset_replay_missing`、`core_seam_future_unprocessed`：当前新 epoch 的 replay 或处理范围不足，不主动 push/finish，也不借用旧标签。
- `core_receipt_row_capacity`、`core_pending_row_capacity`、`core_pending_payload_capacity`：分别对应 2,056 条 compact、4 条 pending、pending 字节越界。compact 最多 2 KiB、pending 列最多 128 KiB、实际 proof 最多 64 KiB；不自动扩容或删旧记录。

仅已提交的精确 pending proof 享有 owner commit-before-ack 幂等重放；取消、来源失效和 owner 代际变化仍须拒绝。它与 HTTP job 的 idempotency key 是两个层次，也不授予任意中断或旧 store 的自动恢复。SQL/来源预算与分页拒绝条件不变，不通过改数据库状态、反复上传或重复建 job 绕过失败。

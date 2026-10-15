# dev11 core-seam 启用与 API 补充

本页适用于 `0.2.0rc3.dev11`，补充[既有 API 指南](TESTED_API_GUIDE_ZH.md)。上传、主结果、分页与 MOSS 路由不变；新增的是显式启用条件与 v3 proof。验证范围见[发布说明](DEV11_RELEASE_NOTES_ZH.md)。

## 1. 用独立数据目录启用

沿用既有安装与模型配置，在启动命令中增加：

```sh
--native-owned-cells --native-owned-core-seams
```

无这些开关时默认行为不变。仅给 `--native-owned-core-seams` 会被 CLI 拒绝。它不隐式开启旧 v2 `--native-owned-seam-cells`，也不隐式启用 MOSS。

选择新建的独立数据目录；不要删除旧可选数据库来绕过策略不匹配。开启后主库增加空的 `native_core_commits` 表，旧结果不回填。v3 可选 store 不接纳已有旧策略目录；同一 v3 store 的合法重启恢复与旧策略迁移是不同操作。

可选能力仍要求匹配已有 runtime、SQLite、VFS 和文件系统资格；相同 Python/依赖版本不等于资格相同。查看 `/health` 的实际可用状态，不改写资格记录或 proof。安装配方和许可证沿用[安装指南](INSTALL_DEV10_ZH.md)，其中 dev10 wheel 的测试结果不是 dev11 wheel 结果。

如需显式 MOSS job，还须按原指南配置 `--native-owned-support --moss-jobs --moss-jobs-model "$MOSS_MODEL"`，使用已有受支持的 Nemotron、CPU、普通 worker 和无 overlap/fragment provider 配置。模型路径由操作者提供，不在 API 请求中上传。

## 2. 先确认主结果，再检查可选结果

用原有上传流程提交输入，等待主 chunk 的明确终态并保存主结果。主 `completed` 不代表 cell 已发布；没有选中 overlap、可选容量不足或资格拒绝，均可能保留主结果但没有可用 cell。

读取 `/health` 时一起保留下列状态；未启用相应能力时，部分字段可能不存在：

- `native_owned_cells.ready`、`warning`、`dropped`、`cleanup_pending`
- `handoffs_active_or_pending`、`core_seam_pending`
- `last_capture_reason`、`last_core_seam_reason`、`last_seam_reason`
- `core_seam_reset_support`，当前应为 `unsupported`

开始 MOSS 前仍须核对原指南中的 permit idle、无 primary waiting、无 optional admission 阻塞、owner 无待交付工作和清理完成。pending 的归零、历史 reason 或一次 health idle 都不能单独证明 cell 成功。不要因超时反复重新上传，也不要修改数据库里的状态。

容量诊断需明确显示：`core_receipt_row_capacity` 表示已到 2,056 条 compact 硬限；`core_pending_row_capacity` 表示已有 4 条 pending；`core_pending_payload_capacity` 表示 pending 字节越界。每个 compact 最多 2 KiB，每个 pending 字段最多 128 KiB，实际 proof 最多 64 KiB。容量失败不会自动扩大限额或删除历史记录，主结果与可选 gap 分开判定。

## 3. 发现并核验 v3 cell

以下变量均来自本次服务与响应，示例不提供可复用的 cell、session 或 job 标识：

```sh
curl --fail-with-body "$API/sessions/$SID/native-owned-cells?after=0&limit=20"
curl --fail-with-body -i "$API/sessions/$SID/native-owned-cells/$CELL_ID"
```

按 `has_more`、`next_cursor` 和 `expected_jobs_epoch` 完成分页；epoch 变化后重新发现。原有 SQL 与资源预算未变，单项过大或最后复核成本增长仍可失败，降低 limit 不保证解决单项成本问题。

只选自然生成、`state=retained`、实际保留 PCM 且 eligible 的 cell，核验：

1. proof schema 为 `native-owned-core-seam-v3`；coverage.kind 为 `same_epoch_core_seam_alternative`。
2. `commits` 恰好两项，几何相邻，同一 capture/reset/native epoch；`dependencies`、receipt、来源哈希、精确 frame range 和 support 均与响应一致。
3. `coverage.adds_primary_audio=false`、`coverage.automatic_dispatch=false`。support 跨 core 边界不保证选中的重叠对本身跨边界，也不证明全局人物身份。
4. 按原始 proof 响应字节核对 `X-Native-Cell-Proof-SHA256`；核对 `X-Native-Cell-Sequence`、`X-Native-Cell-Revision`、`X-MOSS-Jobs-Epoch` 与当前列表绑定。
5. `support_id` 是完整 support 的 canonical JSON SHA-256（UTF-8、键排序、无多余空格、禁止非有限数），不能仅按时间范围猜测。没有合格 cell 就不请求 MOSS。

## 4. 显式请求一次 MOSS 诊断

v3 仍使用已有 cell-job 路由，必须声明 `coverage_intent="overlapping_diagnostic"`：

```sh
curl --fail-with-body -X POST "$API/sessions/$SID/native-owned-cells/$CELL_ID/moss-jobs" \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: core-seam-demo-1' \
  -d "{\"jobs_epoch\":\"$EPOCH\",\"expected_cell_sequence\":$SEQ,\"expected_cell_revision\":$CELL_REV,\"expected_proof_sha256\":\"$PROOF_SHA\",\"support_id\":\"$SUPPORT_ID\",\"profile\":\"handy-moss-q8_0-cpu-experimental-v1\",\"coverage_intent\":\"overlapping_diagnostic\"}"
```

严格 body 最大 1,024 字节；不附文件路径、任意音频或自选模型。同一 session/key/target/body 的精确重试复用原 job，改变绑定会冲突。每个新诊断使用新的 idempotency key。不要把原 job 的轮询超时当成重发另一任务的许可。

保存响应中的 job_id 并查询：

```sh
curl --fail-with-body "$API/sessions/$SID/moss-jobs/$JOB_ID"
curl --fail-with-body "$API/sessions/$SID/moss-jobs/$JOB_ID/result"
curl --fail-with-body "$API/sessions/$SID/moss-jobs/$JOB_ID/raw"
```

只有明确 `returned_diagnostic` 后，才继续核对 result/raw 可用、cell_binding 一致、raw 哈希以及 `process_cleanup_verified`、`cleanup_pending=false`。`failed`、`cancelled`、`invalidated`、内容不可用或清理未确认分别保留。job/result 为有期限的 volatile 状态，不能假定跨重启恢复。

可选 owner 的 commit-before-ack 重试幂等与 HTTP job 的 idempotency key 是两个不同层次：两者都不能放宽来源、proof、epoch 或取消校验。MOSS 输出不自动覆盖主 segments；一次返回不能证明全部内容或准确率。

## 5. 尚不支持的边界

`core_seam_reset_unsupported` 是明确拒绝，不是待后台补齐的跨 epoch 任务。跨 300 秒 reset 与一小时真实连续运行验收仍开放。当前唯一真实 core-seam 案例的右 core 为 EOF-final，不应据此推广到任意 EOS、非 EOF 右 core 或长录音。

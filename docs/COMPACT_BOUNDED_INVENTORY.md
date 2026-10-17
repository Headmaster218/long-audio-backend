# dev41：按固定 owner anchor 的原始根前缀计算等待上界

版本：0.2.0rc3.dev41。继续实验性、默认关闭，仅用于显式启用的新会话和隔离数据目录；启用及外部运行时条件沿用 [dev37 指南](BOUNDED_MULTI_CORE_SUPPORT.md)。本次只有等待上界的计数修正，不改变支持选择、proof/target/inventory wire、store 身份或模型。

## 有界计数与历史行

严格 validate_target 已返回唯一、按时间排列且帧连续的原始根链。在该链中定位首 origin 的最终 commit（固定 owner anchor），只计算截至该 anchor 的不同根前缀 p。新候选上界为 start + min(3, 4 - p)。后来加入的 successor alias 不再从固定 start 重复扣除；更早的必需根仍消耗前缀额度。已有行始终使用 min(stored_last, new_upper)，原墙钟 deadline、首 owner/候选身份和顺序不变。首次普通/seam 注册、迟到较早 alias、已有更紧 expiry 及 closed 行均有直接测试；既有窄 horizon 不延长，已关闭行不重开。

原始加最终根的不同并集仍最多四根，真实第五根和帧 gap/错误根顺序继续由生产验证器拒绝。原有 owner-anchor ordinal 查询及所有 SQL/投影保持原字节，不新增非 anchor 连续 ordinal 规则；其既有 gap 语义不变。取消、删除、当前 compact/batch/scope/store、来源/摘要、原始零 PCM 根、20 秒及全部字节/数量/VM 上限仍独立检查。较老 reader 会以 bounded_wait_metadata 拒绝新规则下合法的 forward-alias 等待元数据；不声称可降级处理或整个旧二进制全局只读。新 reader 继续接受原较短 horizon 和已绑定 proof，不改写历史证据。

## 固定正常生产路径的模型无关结果

当前源 19 个专项和 29 个直接受影响复核共 48 个不同用例通过，未重跑全套。先前固定 fixture 的操作顺序浮点断言失败保留；修正只匹配原生 analyzer 的 origin + local-frame-difference 运算顺序，没有改变传入时间或生产舍入。之后原生产代码的真实失败也保留：owner 3 的 ordinary3、seam2/3 先给出 horizon [3,5]，ordinal 4 的自然 seam3/4 把它错误缩为 [3,4]，ordinal 5 以 unresolved_commit_horizon 关闭。当时等待组合 37,840 字节，没有容量拒绝。

修正后复用同一预声明几何、普通 origin 在 seam 之前的正常 Capture.bounded_prepare → planning → enqueue_bounded 路径，不重排或手工注册合并目标。ordinal 4 的真实 alias 仍被发现，horizon 保持 [3,5]，ordinal 5 绑定。原支持优先和完整 atom 固定点选择器保持原样；已选支持为 [310400,505600) 帧，四根 2–5 的 PCM 贡献依次为 0、25,600、168,000、1,600 帧。两次上传贡献分别为 193,600 和 1,600 帧；零贡献 root 2 仍为受保护依赖。真实 owner PCM、三个新鲜准入边界、结果提交、whole-parent 与 lexical-estimated-v1 两种转写视图均通过。

该 proof 为 49,183 字节，完整 target 为 32,447，旧完整组合为 81,630，超过原 65,536 上限；初次绑定的 291 字节严格同 proof 引用使实际组合为 49,474，行 charge 为 68,287。完整 proof 独立验证且容纳；所有上限不提高。另一个既有固定 ordinary-first 合成 ledger 在新规则下也可绑定，旧的保存紧 horizon 控制仍拒绝；dev40 的原顺序失败证据保留为历史结果。

这些结果使用预声明的合成 native 查询、合法词汇父行/单元和 fake MOSS 驱动，不能证明实际识别库存会选出相同支持。正常生产 fixture 的累计 bounded-primary 预算显式为支持的 1.0 秒；规划阶段仍为 0.4 秒 / 120000 steps / 128 KiB，finalization 为 0.98 秒、notice 为 1.0 秒；产品默认仍为 0.75 秒，dev40 的默认 deadline 失败及非默认正确性资格保持分开，owner 发布预算仍为 2.0 秒。没有新的真实模型运行、质量或历史实际失败恢复资格。

## 保留的限制

dev40 的 94 个用例和更早累计证据是各自已审阅版本的历史资格，不合并成当前最终 hash 的全套通过。baseline 已复现的 150000 VM-step seam 预算失败仍未解决。所有 dev37/dev38/dev39 固定实际失败报告保持原字节；语音加数字零输入不等于真实跨文件对话准确性。保守全 store v2 GC fence、长期孤儿回收限制、bounded v2 持久身份/说话人链接 abstention 均保持。完整计划、等待别名、完整 proof 及其他 certificate guards 仍可能拒绝。

离线 128 文件 source/wheel/install 一致只验证包字节；wheel 不是自包含 native/model 运行包。本地模型/profile 固定只能建立重复性，上游转换/量化来源仍未独立验证。任何后续实际试验都需要单独固定配置、部署和运行资格。

---

以下 dev40 及更早章节保留当时的格式、测量和资格；其中“本版”“普通顺序仍受限”等指该历史版本。

# dev40：同 proof 的绑定目标引用

版本：0.2.0rc3.dev40。继续实验性、默认关闭，仅在显式配置的新会话和隔离数据目录使用。启用与外部模型/运行时条件沿用 [dev37 指南](BOUNDED_MULTI_CORE_SUPPORT.md)。这是绑定行的表示变化；目标选择、inventory-v3 支持范围、store 身份和等待 horizon 不变。

## 精确表示与读取

首次 waiting→bound 提交写入规范的 bounded-target-proof-reference-v1，只有 schema、target_key、target_sha256、proof_sha256 四个字段，引用本身最多 512 字节。target 摘要对应同一完整 proof 内 target 的规范字节，proof 摘要对应该 proof 的精确字节。完整 proof 仍自包含，保留所有 origins、提交、词汇清单和来源，不增加表或跨回调缓存。等待行保留完整 origins；closed-waiting 与 closed-bound 必须由已验证 proof schema 区分，不能仅凭 closed phase 解引用。

新 reader 继续接受既有完整 bound target，不自动重写历史行。每个新快照可接受完整 target 或严格引用这两种同 proof 的无损规范表示，并验证当前行的实际 planning/charge 字节。允许的写入转换在已有记账调用点原子更新 global/session usage；不新增每次读取的全局汇总检查。并不承诺跨回调 target_body 字节不变。未知/非规范/被篡改引用、不同 proof 或 target 摘要、含糊匹配及陈旧额度拒绝。已经支持 inventory-v3 的旧发布 reader 仍拒绝新 target 引用，不能据此创建/发布候选；不声称整个旧二进制全局只读。

读取保留一次长度预检和一次精确比较投影。仅为长度及各自组合上限都合格的候选绑定比较输入，其他候选绑定 NULL；必须恰有一个规范 wire 匹配，且 proof 精确相等。每次比较绑定的 proof 与所有非 NULL target 候选字节之和仍不得超过 65,536，含糊同长度且超限时直接拒绝，不顺序重读或回退。实际返回 wire 用于累计 SQL 投影及行额度；首次绑定的正、零、负净额度变化仍与 CAS、依赖和回滚原子提交。

原完整 proof 与 proof+实际存储 target 的独立 65,536 字节限制、清单大小/数量、最多四根/20 秒、SQL/VM/PCM/来源/取消限制均不提高。所有原始及最终依赖根仍受保护，包括零选中 PCM 的原始根。发布前、owner 提交前、提交后的三个 authority 边界都重新读取并验证，不缓存先前快照。引用不能消除完整 proof、等待别名合并、其他 certificate guards 或完整规划的容量拒绝，也不改变普通 origin-first 的较短 horizon。

## 固定模型无关测量及配置范围

累计固定源依赖复核通过 94 个不同用例：31 个新引用用例；4 个显式配置为 1.0 秒的既有 C/边界复核；2 个默认预算/真实主事务回滚检查；57 个受影响回归。原 fixture owner 错误及默认 deadline 失败均保留。不是一次默认配置整套 PASS，也没有重新运行全部历史用例。

默认 native_owned_bounded_core_support_seconds 仍为 0.75，可配置范围仍为 0.5–1.0。一次 C 第四主提交的 finalization 达到默认 0.73 秒子阶段 deadline，实际累计 0.735475 秒并回滚；没有把它改记为通过，也未隔离证明某个新增热点。四个 correctness fixture 仅在支持的 1.0 秒配置下完成，三个 mutation 边界拒绝仍有完整原子性/任务/租约检查。默认 0.75 的数值边界及真实 SQLite progress interruption 回滚另行通过；57 个受影响回归的实际 app 记录仍用 0.75。owner 发布保持原 2.0 秒预算，与主提交设置分开。

C 的 proof 30,705 字节、引用 291、组合 30,996，inventory 5,134。真实 owner fixture 在三个新鲜性边界依次使用引用→完整→引用，完整 target 14,143；实际六个非 NULL 比较参数累计 106,840 字节，分布于三次新读取，每次仍守单次上限。该次 authority 投影 72,702 字节、总投影 103,407，owner 发布 0.997945 秒且确认 durable；原 PCM、目标、文本、结果、转写和禁用模型重启检查保留。这些是 fake native/stub MOSS 的模型无关证据。

另一个固定四相邻提交/四独立上传/单个完整 opaque 父行/三个 origins 的合成案例，严格选择和完整 proof 验证通过后测得 proof 39,449、完整 target 26,894，组合 66,343，超过原 65,536 上限 807 字节。原 ordinary-first 增量注册的首 owner 保持 anchor1；seam alias 到达后 horizon 从4缩至3，ordinal4仍以 unresolved_commit_horizon 终止。不能重排或恢复该行。单独获准的新 seam-first ledger 用 anchor2/horizon4，目标 key、几何和 origin 集相同，但 candidate、kind/slots、target/proof hashes 改变；它自己的 proof+引用为 39,740，实际 charge 58,459，旧完整存储拒绝而新引用绑定通过。这个 lifecycle 是独立合成顺序，未证明 producer 会自然发现该历史，也不证明原真实失败已恢复。

## 实际失败与保留限制

[dev39 固定 63 秒失败报告](reports/DEV39_FIXED63_FAILURE_DIAGNOSIS.md)及原 dev37/dev38 报告按原字节保留。dev39 已有主文本、可读 v3 proof、部分父行精确分割和重启检查，但没有正长度第三根/跨上传见证，原结果仍为失败。历史 bounded_certificate_capacity 的具体 guard/大小未知；后续源代码诊断只为未来拒绝记录固定 guard/check 与已知字节操作数，不能倒推旧操作。本版没有新的实际模型执行或识别质量结论。

原 baseline 复现的 150000 VM-step seam 预算失败继续未解决。保守全 store v2 GC fence、缺失主词汇闭包 unresolved_lexical_gap、bounded v2 持久身份 annotation/linking 拒绝及任务局部标签限制不变。本地模型/profile pins 只建立可重复性，上游转换/量化来源仍未独立验证。离线128文件 source/wheel/install 一致不构成真实 native/model 资格；wheel 仍需外部运行时、manifest 和模型，不是自包含部署包。

---

以下保留 dev39 及更早版本当时的格式、测量和资格记录；其中“本版”等措辞仅指对应历史版本。

# dev39：版本化的支持区间 authority

版本：0.2.0rc3.dev39。继续实验性、默认关闭，只用于显式启用的新会话和隔离数据目录。设置及外部模型/运行时条件沿用 [dev37 指南](BOUNDED_MULTI_CORE_SUPPORT.md)。这次是显式版本化的 authority 范围变化，不只是压缩；原有重叠目标、PCM 映射、native 根和选择器算法不变。

## 精确选择后才局部化

完整提交带仍先经过原有计划及事务内新快照检查，使用同一个“原支持优先、否则完整 atom 固定点”选择器。只有在局部化后再次运行同一选择器，得到完全相同的 start/end 几何，才允许形成新 proof；丢弃外部 atom 若改变原支持优先分支，明确以 bounded_local_selection_changed 拒绝，不搜索替代支持，也不从截断证据声称全局最优。

内层新格式为 native-owned-bounded-inventory-v3，scope_policy 必须是 selected-support-whole-parent-v1。scan_frames 严格等于已选支持，目标全部包含在该支持内，保存所有与之正长度相交的完整父行以及这些整行的来源和每一个独立验证有效的词汇来源。端点相触按半开区间处理。来源可能位于支持之外，仍必须完整保留；只有无关上下文来源可在完整新扫描和验证之后剔除。行原文、raw/display 跨度和词汇单元不裁剪；临时 atom 仍确定性重建，不重复持久化。 相交词汇闭包为空时仍以 unresolved_lexical_gap 拒绝，原有缺失主词汇证据的限制不变。

发布前、owner 事务内提交前、提交后仍分别读取完整的新支持带，先发现新增相交行再按版本重建。新增、删除或改变相交父行会拒绝；即使改动只在已保留整行的支持外字符/单元，或位于支持外的有效词汇来源，也拒绝。完全在支持外的新父行及无关上下文来源不会撤销 v3。所有声明的原始目标、依赖、回执及最终 native 提交根仍独立验证和保留，包括零选中 PCM 的原始根；取消、摘要、来源放置、回执和提交撤销规则没有削弱。

## 兼容性和硬上限

外层 proof schema、语义标识和 store 身份不变；嵌套 v3 范围策略及规范 wire 均参与原有摘要、proof 相等性和 owner 绑定。新版本继续读取原 v1/v2 字节及摘要，并按原完整带的新鲜性规则检查历史已绑定 proof；历史 proof 不重写。原等待目标保留原 origin/身份，经正常完整计划后可生成新的 v3。旧 dev38 验证器以 bounded_inventory_policy 拒绝 v3，不能由该候选创建 Cell 或发布/准入；这不是整个旧程序全局只读的承诺，也不提供降级处理。

清单 wire、完整 proof、proof+target 的三个独立 65,536 字节上限，以及最多 128 父行/128 来源/512 单元/128 atom、四个并集 core 根和 20 秒支持均不变。完整带规划仍可能先于局部化达到容量上限；局部清单能容纳也不保证完整证书能容纳。SQL、预算、RSS、PCM、取消、owner 三个新鲜性边界及回滚规则不变，不新增表、缓存或运行框架。

serialization_capacity 仍使用同一异常类、原因和回滚。已有规范编码的超限分支只附带该次实际编码长度和原限制；有限调用点附带固定阶段。可用时 notice.capacity 只有 phase、encoded_bytes、limit_bytes，阶段仅为 target_registration、retained_inventory、dependency_roots、core_preimage、full_proof、source_binding 或 unknown。没有第二次序列化，不记录原文、路径、SQL 或堆栈；不安全/缺失的长度不输出，不猜测历史抛错对象，不把拒绝改为成功。

## 当前模型无关资格与失败记录

经过独立逐次源依赖核对，35 个专项和 41 个受影响既有回归共 76 个不同用例通过。三个先前专项失败分别为合成 raw 单元 eligibility、注册 anchor ordinal 和 v3 精确拒绝原因的 fixture/预期问题；原失败均保留，只作经过审阅的窄测试修正，未放宽生产选择器、异常或预算。历史 v1/v2 广范围新增行仍撤销的对照也通过。

合成端到端 C 仍证明第三 core 绑定、精确 PCM、三边界发布、结果、一次转写响应内的选择/字符守恒及禁用模型重启。其 v3 清单 5,134 字节，proof 30,705，target 14,143，合计 44,848 字节；发布投影 103,110 字节、比较输入 134,544 字节，二者分别计量。三次新鲜性边界中的实际主行/来源变更均拒绝；提交前没有留下 Cell/任务/租约，提交后失败保留已提交 proof 并不确认发布，不能声称回滚了已完成提交。独立四 core 接受/第五根拒绝回归也通过；C 本身不是四根案例。

[dev38 固定 63 秒失败报告](reports/DEV38_FIXED63_FAILURE_DIAGNOSIS.md)与[dev37 失败报告](reports/DEV37_FIXED63_FAILURE_DIAGNOSIS.md)原字节保留。dev38 虽有一个结果提交，proof GET 随后 409 cell_revoked，未完成转写/重启检查；其三个声明提交根只有两个与所选 PCM 正长度相交，且都来自第一次上传，不能视为第三根/跨上传见证。保存状态诊断是最终状态复现：支持外父行/来源追加改变了原广范围 authority 比较，但未定位该历史 409 的确切子检查；后续容量通知的确切历史抛错对象也仍未知。dev39 目前仅有 fake native/stub MOSS 的模型无关结果，没有真实模型或识别质量资格。

历史 926/927 个既有用例及 144/144 个原新增用例仍是跨已审阅版本累计证据；原 baseline 也复现的 150000 VM-step seam 预算失败保持未解决，未重新运行整套 927 个用例。保守全 store v2 GC fence 继续保留，长期孤儿回收未获资格；bounded v2 持久说话人 annotation/linking 仍拒绝，只保留任务局部标签。本地模型/profile 字节固定只能建立可重复性，上游转换/量化来源未独立验证。离线 128 文件 source/wheel/install 一致不等于外部 native/model 资格，wheel 不是自包含运行包。

---

以下保留 dev38 当时的格式、测量和资格记录；“本版本”等措辞仅指该历史版本。

# 紧凑的有界词汇证据清单

版本：0.2.0rc3.dev38。功能继续实验性、默认关闭，只用于显式启用的新会话。启用参数、隔离数据目录、外部模型/运行时条件和原有范围见 [dev37 指南](BOUNDED_MULTI_CORE_SUPPORT.md)。本次只修复证据表示的重复序列化；没有改变重叠目标、支持选择、来源映射或识别模型。

## 格式与兼容性

完整 proof 的外层仍为 `native-owned-bounded-core-support-v1`，外层语义策略和 store 身份不变。内层新增 `native-owned-bounded-inventory-v2`：完整主行和来源描述符各保留一次，不再保存逐父行与顶层的重复 atom 数组、可派生的父行摘要。有效且规范的词汇 JSON 可以用带 `canonical-json-v1` 标签的解析值保存；编码回原串必须逐字节相等。NULL、无效、opaque 或非规范字符串保持原字节，不把后续失败的标签值降级为 opaque。

验证器先检查紧凑 wire 的大小、严格字段、已知版本及数量，再从保存的原行确定性重建临时 atom 视图。该视图可以受现有计数与内存上限约束而大于 wire，但不再次作为持久化清单序列化。摘要、完整 proof 相等性及比较输入计量只使用原规范 wire。未知或混合版本、标签歧义、来源/主行/词汇变化均拒绝。

新二进制继续读取并验证已有 inventory-v1 的原字节和摘要；旧的已绑定 proof 仍按保存的 v1 重建新快照。旧的等待目标保持原始普通 v1 origin 和身份，可在后续计划中使用紧凑清单。新旧 wire 版本不能因临时语义视图相同而互换摘要。旧 dev37 验证器在 exact-key 检查处以 `core_seam_shape` 拒绝新 v2，不能据此创建 Cell、准入或发布该候选。这不意味着整个旧二进制全局只读，也不授权降级处理新 payload。

## 保持的上限与新鲜性

- 清单 wire 上限 65,536 字节；完整 proof 上限 65,536 字节；独立 proof+target 比较输入上限仍为 65,536 字节。清单能容纳不代表完整证书必定能容纳。
- 最多 128 个主父行、128 个来源、512 个词汇单元及 128 个 atom；最多四个并集 core 根、20 秒支持，原目标别名上限及全部 SQL、时间、内存、PCM 和回执检查不变。
- 发布前、owner 事务内提交前、提交后仍分别读取新快照，并按存储的清单版本重建。任何边界的主行/来源改变仍拒绝或不确认发布；提交后确认失败仍不能声称先前提交已回滚。
- 不新增表、数据库依赖的纯 proof 验证捷径或跨快照 authority 缓存；原子提交、回滚与可选失败保全规则不变。

## 本次模型无关证据

在固定 source60 上，32 个专项用例和 41 个受影响回归通过，共 73 个不同用例：30 个新增修复用例、两个既有复核以及 41 个其它既有复核。首次专项尝试有一个合成时间坐标 fixture 错误，原失败完整保留；修正为二进制及整数帧精确坐标后，32 个用例全部通过，先前八个通过没有重复计数。

旧 v1 兼容性包含一个字面量小清单及固定 source52 的七个旧函数 oracle。合成完整 proof 逐字节和摘要相同，18,419 字节，SHA256 `d0ec08e6fdbaccb3a698fba70dd0aae684a4dc6a3297bc3ae6df2becf437d9ff`。这是冻结代码的等价性证据，不是先前保留的字面量完整 proof。

三个合成规模与失败诊断中的父行/单元/atom 数量对应，文本与时序为合成数据，并非历史事务或真实目标的重建：

| 父行/单元/atom | 紧凑清单 | 展开 v1 清单 | 完整 proof | target | proof+target |
| --- | ---: | ---: | ---: | ---: | ---: |
| 8/68/65 | 21,842 | 72,220 | 39,533 | 7,908 | 47,441 |
| 13/106/101 | 34,503 | 113,162 | 52,194 | 7,908 | 60,102 |
| 12/85/80 | 29,358 | 92,008 | 47,049 | 7,908 | 54,957 |

三者完整组合均在原上限内。另一个负例的紧凑清单为 60,668 字节，但完整 proof 为 78,139 字节，仍正确拒绝；不得把该负例通过解释为该规模已能执行。

独立的端到端合成 C 路径通过第三 core 绑定、精确 PCM/来源、发布、结果、一次转写响应内的选择与字符守恒，以及禁用模型的重启：清单 6,640 字节、proof 32,212 字节、target 14,143 字节，组合 46,355 字节。三个实际发布新鲜性边界及修改拒绝均有检查。它使用 fake native/stub MOSS，不能证明真实 native 查询保留范围、阻塞行为或识别收益。

历史 dev37 累计验证仍单独报告为 926/927 个既有用例通过、144/144 个原新增用例通过。既有 `test_seam_primary_projection_budget_declines_without_widening_limits` 在未改动 baseline 也耗尽普通 150000 VM-step 上限，仍未解决。没有重新运行整套 927 个用例，也不声称一次最终 hash 上的全套 PASS。

## 真实试验与继续保留的限制

[dev37 固定 63 秒失败报告](reports/DEV37_FIXED63_FAILURE_DIAGNOSIS.md) 原字节保留。该试验处理了全部主文本并保持重启守恒，但三个目标全部 no_result，没有自动 MOSS 发布或第三根/跨上传组合见证。后续保存数据的清单大小诊断只说明最终状态的表示放大，不能确定历史抛错对象，也没有改变原失败结论。本版本的模型无关结果不能替代新的受控真实试验。

仍保留全 store 的保守 v2 GC fence，尚无长期孤儿回收资格；bounded v2 的持久说话人 annotation/linking 继续拒绝，文本及转写只使用任务局部标签。语音接数字零的固定输入不证明真实跨文件对话准确性。外部 runtime/model manifest 与本地模型仍须独立核对；离线 wheel/install 的字节一致和依赖元数据一致不是模型来源或运行资格，wheel 不是自包含 native 部署包。

本地模型与 profile 的字节固定只能建立可重复性；上游转换/量化来源尚未独立验证，不声称本地 GGUF 与历史分发 manifest 的模型等价。失败的 dev37 试验没有任何 MOSS 准入或调用，因此也没有验证这条模型路径。

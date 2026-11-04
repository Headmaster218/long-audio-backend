# 下一轮最小研究协议草案：两个域，不混合结论

状态：仅预注册草案，尚未获取音频、冻结具体人员或运行模型。以下候选数量和门禁是研究设计选择，不是文献证明最优，也不是已经完成的试验。已另行实现的纯JSON planner只覆盖下面的域D声明式跨会话选择契约，不等于两域完整执行器。

## 实际实现边界：仅域D的纯JSON planner

`../contract/multireference_trials.py` 接受有界JSON元数据，输出不可变试验计划，不接Store、不读音频、不含embedding或scores、不调用centroid、不改生产配置或身份库。实际容量上限是4MiB JSON、4096 observations、256 declared people，每人reference_count为2–8；下文K=3是待冻结协议选择，并非planner硬编码。

实际选择契约要求每人参考来自不同**declared session**，参考session不与任何query session相同，而且有日期证据时参考日期必须早于所在split最早query日期。重复reference session不会获得额外权重；同日或缺日期的reference/query顺序不能靠acquisition_order冒充跨天。不同reference session是否也各处不同日期，仍需域D正式协议和外部审查核对。

planner核对的是声明的hash、帧区间、C/T person/session隔离、role、space和外部证据字段；它拒绝非法JSON、重复键、越界、同声明源帧重叠、unknown enrollment等，且把不足或不可用项留在分母/ledger。**它不重算source/crop hash，不核实不同bytes是否是同一物理录音的衍生物，也不证明身份、语音纯净或统计独立。** 其精确输入schema以源码为准。

planner没有向量，因此**不实现**embedding维度、零范数、非有限向量或质心近零检查；JSON非有限数的语法拒绝不等于向量校验。向量检查属于现有scorer及未来执行器必须验证的职责，不能写成本轮planner已具备的能力。

## 两域未来执行器的设计要求，不是已实现schema

未来执行器应复用当前 `verified_identity.centroid_score` 与现有归一化/决策语义，而不是再写聚合器。外部证据包需绑定dataset/version、来源和实际核验的音频hash、物理录音/同步capture event（多麦同一发声event）、speaker-label namespace、原声道/采样率、原始帧区间、裁剪hash、模型/frontend/embedding-space、支持标注版本、证据等级及C/T和enrollment/query角色。不要把这些概念字段直接添加进当前exact-schema JSON。

未来执行器需实际验证来源bytes/帧见证、向量维度与有限非零范数，审查person/session别名和语料历史排除；证据不足标unresolved。metadata检查或调用者 `clean=true` 均不能证明声学纯净。源撤销、执行超时、资源上限和评分失败也需独立实现/验证，当前planner并未完成这些执行任务。

每个实验域固定两种评分配置：

1. Baseline：每人第一个合格2秒参考。
2. Candidate：同一人的固定前三个合格2秒参考，已有 normalized centroid。

同一查询只编码一次并用于两配置；第一参考是三参考集合子集，固定顺序、不按匹配分数挑参考。K固定3，失败者不降K；不做max-score、查询融合、自动更新或门限组合搜索。单/多参考门限必须在新C上分别校准，margin预先固定.10，记录单人gallery margin不可解释的问题。首轮gallery至少2已知身份并含未知人查询。

单参考2秒与三参考6秒同时改变总注册语音量和观测次数，任何提升属于这个组合干预，不能单独归因于平均算法。更长单条参考对照需另验编码器变长输入契约并先冻结，不能临时加入。

可复用先前0.00–1.00/0.01的一次C选择规则（零观察wrong-known和unknown accept，最大化correct-known，取更高阈值破平局），但要明确这是探索性经验约束，不是安全率保证。0.46/.75仅作外部历史描述，不直接套到新质心策略。若新C任何门限都只能全拒，则如实报告，不改grid/margin再试。参数、参考集合、T清单与hash在打开T分数前封存。T只评估一次锁定配置，不能调到有利结果。

## 域M：会议内多次干净观测

**尚无对应实现契约。** 当前域D planner会把同一meeting/session的参考与query判为不合格，也不会选择同session的三条参考。域M需另一个明确标记为within-meeting的schema/选择规则和测试，保留真实同会话关系；不得给turn伪造新session或放宽D契约后声称独立跨会话。

目标：在真实中文会议噪声/重叠环境中，可靠单人支持+三参考是否提高同会话已知身份覆盖，且没有已观察到的额外误接纳。

建议最小规模：AliMeeting C/T各4场、每场至少3位参与者；每场选标注ID按固定排序的2人为known，其他人为unknown。同会议只在一个split。同一真实人跨会议的关系若不明，必须标记person independence未证明；不能给严格独立人置信界。可扩大会议数但须在取得音频前冻结，不能结果后补人。

enrollment取会议前1/3中每人最早3个合格连续2秒片段，来自不同完整turn且相隔至少30秒；query取最后1/3最早合格片段作为primary，另至多2个后续片段仅描述。时间间隔是减少相邻相关性的设计约束，**不**制造独立会话。按所有speaker的原始标注排除重叠，沿用100ms边界侵蚀并盲于分数做完整支持听审。不拼接两段填满2秒，不按模型分数重选。

M结果只报within-meeting。额外报告门禁保留/拒绝数、标注与native门禁一致性、误放行、speaker confusion、unknown accepts、known refusals、每会议至少一次误归属。若评估完整分段另报DER（含overlap并明确collar）及边界错误；不能只用embedding识别率替代会议切分质量。

输入阻塞：尚未验证官方单WAV访问；仅确认3.42GiB Eval整包可HEAD。下一步是取得明确小包/带索引官方文件列表，或明确新预算后另做受限获取。不在此次启动整包。

## 域D：跨天身份

目标：把“同会议不同turn”与“不同天声音变化”分开，检验三天参考对第四天识别的影响。

原规模设想：SMIIP-TV新C与新T各16 known+16 unknown，共64人，**目前不可作为可执行最小计划**。官方eval仅58人，排除前轮已申请32人后至多剩26名未用者，实际满足日期/音频/单人支持的数量可能更少。排除范围必须包括所有已申请、已读或已打分人物/文件，不仅最后保留18人；其他旧项目语料的已知人物也应排除并披露跨库别名未解决的部分。

不能为凑64人直接转用其余官方training people：必须先完成新来源、人物身份与既往使用、具体权重训练分布/潜在重合的审查。换dataset split不证明model-training independence。当前未完成这些核查，也未证明还有64个可用新人物。

下一步仅做≤26名候选的无分数可行性清单审查及来源核查，再在任何新评分之前冻结一个诚实反映数量的较小探索协议，或找到经审查的新来源后重新冻结规模。不得取得分数后缩容成有利子集；较小样本不能承接原64人设想的统计目标。C/T人物和预指定日期分开，当前已看T全部封存不用。

每个known来自3个不同日期各1个2秒参考；主query来自预指定更晚的第4日期，另两个同日时段query仅作描述。unknown只有query，不建参考。日期、时段、prompt类别的固定次序和可缺失规则先写manifest，避免只选得分容易的内容。必要时先用独立preflight cohort验证采集和听审流程，不能让preflight人物进入C/T。

完整原录音及裁剪支持听审确认语音/单人；标明这是人工条件下的观测质量试验，不是无人值守采样性能。若所需数量、日期或支持不齐，按预定义不合格状态停止或报告缺口，不根据已有分数缩成“成功子集”。保存source/crop/model/frontend/decision hash及冻结时间链。

D主统计单位每人一个primary query；unknown以人计，重复日期、时段、gallery pairs不能增大n。可报告conditional-iid参考区间，但抽样不符合iid时必须明确不提供总体覆盖保证。即使能实现原设想的每split 16 unknown且零接受，也不足以验证很低误接受率；目前可行的更小人数不能证明产品可自动merge。

## 固定测试与停止条件

本轮planner契约测试只证明它对声明元数据的有界解析、源hash重复/帧重叠、query/reference session隔离、unknown参考、space不匹配、输入排序确定性及缺额不降K等规则的行为。实际执行了哪些测试以单独测试报告为准；不能借该列表宣称端到端验证。

未来执行器还需测试实际音频hash重算、衍生源与同步多麦事件去重、向量非有限/零范数/维度、质心近零、支持失效与人工标签撤销传播等。域M另测同会话的合法多turn规则，域D继续拒绝该输入。所有合成契约单测均不算声学试验成功。

完成条件是取得冻结输入并完成新C选择、新T一次评估、核验hash与完整失败计数；或明确报告输入/资源/身份映射阻塞。不得以“候选有提升”代替误接纳、拒识率和依赖假设。两域不合并安全统计，不推广至另一服务器、Android、长时在线流或新模型。

下一轮若只够推进一件事：先核对域D planner边界与合成契约测试，再解决≤26名候选的真实可行性及会议小样本取得；域M契约另行设计。不要先开另一次embedding阈值扫描。

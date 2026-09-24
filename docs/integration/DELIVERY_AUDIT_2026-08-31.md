# 1–4 号任务交付审查与 5 号集成清单

审查日期：2026-08-31。审查对象为最新远程代码，以 `ranking_pipeline@f91b3d5` 为内容基线。

后续更新：同日已读取官方飞书要求、安装并校验 catalog/BGE 数据资源，实际复跑 BM25 和 locked+lite 的公开 200 条。见 [官方要求与数据报告](OFFICIAL_REQUIREMENTS_AND_DATA_2026-08-31.md)。下文“资源缺失、未复跑”描述保留为首次审查时的历史状态，不代表当前数据安装状态；业务缺口尚未修复。

结论：**已有所有模块的可集成基础，但 1–4 号不能全部按任务清单验收。**
1 号基本具备模块交付；2 号是固定策略检索基线，缺少要求中的动态能力；3 号有状态框架和测试，但存在实际约束处理错误与接口分歧；4 号有排序和澄清实现及实验，但尚未证明澄清策略能减少交互并改善综合成绩。5 号仍需真正串联各模块，不能只把目录放在一起。

## 1. 集成分支与来源

已建立本地 `codex/system-integration`，基于 `origin/ranking_pipeline`，未修改 1–4 号业务实现。已取消对原 `origin/ranking_pipeline` 的 upstream 跟踪，避免误向队友分支推送。

| 来源分支 | 审查提交 | 已收集内容 |
|---|---|---|
| `main` | `30bec6f` | 意图识别、状态记忆 |
| `master` | `da40b72` | 与 main 全部文件相同 |
| `feature/retrieval-reranking` | `330ca6a` | 意图识别、检索重排、资源安装和评测 |
| `data/synthetic-proxy-3021` | `c1d8e73` | 意图识别、3,021 条合成数据 |
| `ranking_pipeline` | `f91b3d5` | 上述全部模块、高级排序与训练 |

同名模块目录的 Git tree 哈希完全一致。除根 README 的说明差异外，没有其他分支拥有集成基线缺少的业务代码。完整哈希见 [source-manifest.json](source-manifest.json)。

这是当前代码快照，**不会自动收集未来提交**。由于 ranking_pipeline 与其余分支无共同祖先，本次没有强行合并或重写历史。后续应从集成分支创建工作分支，将 PR 指向集成分支；队友尚在旧历史上的改动需要单独审查差异并迁移。发布到远程前，它仅在本机可见。

## 2. 本次实际验证

Python 3.12.13，使用本机 Codex 已有运行时；没有安装第三方依赖或下载模型。

| 测试套件 | 本次结果 | 验证边界 |
|---|---:|---|
| 意图识别 | 11/11 | 规则与输出字段 |
| 状态记忆 | 12/12 | 已有多轮用例 |
| 检索重排 | 6/6 | 临时目录/小型商品集、接口和容量、mock 资源安装 |
| 高级排序 | 34/34 | 规则、mock 模型、接口、改口处理、训练样本构建 |
| 合计 | **63/63** | 不等于完整 50k 商品检索、真实模型推理或公开 200 条重跑 |

原始输出见 [unit-test-results.json](unit-test-results.json)。安装器测试输出的 `example.invalid` 下载信息来自 mock，并未真实下载。

本机项目目录缺少 `retrieval-and-reranking/data/catalog.jsonl` 及 `resources/`。因此这次没有复跑真实 BGE/Qwen 推理、公开 200 条或 BM25 官方基线。历史结果只能标注为“仓库已保存结果”，不能标为本机复现通过。

复跑命令（`python3` 必须为 Python >=3.10；本机系统默认 3.9.6 不符合项目要求）：

```bash
# 分别在对应模块目录执行
python3 -B -m unittest discover -s tests -v
# 高级排序在仓库根目录执行
python3 -B -m unittest discover -s ranking_pipeline/tests -t . -v
# 在仓库根目录运行补充探查，无需真实商品集或模型
python3 -B docs/integration/probe_contracts.py
```

## 3. 逐项交付判断

### 1 号：Intent Router & Query Understanding — 基本可交接

已有 Buying/Browsing/未定意图、confidence、双轨 route、category/price/color/brand/use case 等槽位、硬软约束分离、关键词/语义 query rewrite、override 标记，以及 `IntentResult`、交接说明和测试报告。

证据：[router.py](../../intent-recognition/intent_router/router.py)、[models.py](../../intent-recognition/intent_router/models.py)、[交接说明](../../intent-recognition/docs/intent_router_handoff.md)。

仍需明确的边界：

- 规则分类器、confidence 是启发式分数，不是经过校准的模型置信度；rewrite 主要是规则构建查询。
- 模块报告里的官方 Buying 初始消息 80/80 被识别为未定意图；这是有意采用保守策略，不能宣称官方 Buying 分类准确率为 100%。1.0 是公开硬约束覆盖率，不是意图分类准确率。
- 1 号输出的 `IntentResult` 尚未成为 3 号唯一解析输入；1 号的路由也尚未实际决定 2 号策略。需要 1/3/5 联调，而不是要求 1 号自己重做整个 Agent。
- 需补与 3 号一致性的回归用例，以及 Router on/off 的端到端影响实验。

### 2 号：Hybrid Retrieval — 基线已有，动态部分未齐

| 要求 | 现状 |
|---|---|
| BM25 / Keyword | 已有 SQLite FTS5 BM25 |
| Category / Metadata | 有 category gate 和商品文本索引；未形成结构化数值预算等可靠过滤 |
| Dense / Vector | exact 已有 BGE 向量检索，但主要在候选不足 50 时补位，不是每次都与其他路线独立并行召回 |
| Multi-route / fusion | 有三条 FTS 路线、evidence 路线、固定 schedule 去重融合 |
| Buying / Browsing 差异化策略 | 未接入；generate 输入没有 intent/route，策略固定 |
| Dynamic weights | 未实现按 intent/context 动态权重；`FIXED_SCHEDULE` 和字段权重固定 |
| Hard constraints filtering | evidence 路线有硬词匹配，但其他路线/补位不保证全局硬约束；预算数值与排除语义未完整处理 |
| Dynamic truncation / Top-N | 当前契约固定恰好 50，不能按 state 返回动态 N 或不足 50 的安全候选 |
| 策略实验 | 有 exact/lite、完整需求覆盖和排序结果；缺 Buying/Browsing、动态权重、动态 N、严格过滤等消融 |

证据：[retrieval.py](../../retrieval-and-reranking/techjam_agent/retrieval.py)、[contracts.py](../../retrieval-and-reranking/techjam_agent/contracts.py)、[排序实验](../../retrieval-and-reranking/docs/RANKING_RESULTS.md)。

需要 2/5 明确一个决策：固定 Top50 可作为保留的基线，但不能同时声称实现动态 Top-N。若要求少量/零有效商品时严格遵守硬约束，需要修改/版本化接口，或在 Top50 契约外定义过滤与不足候选的处理方式。不能为了凑够 50 个商品而静默违反预算或排除项。

### 3 号：State / Memory / Dynamic Context — 框架已有，需修复后验收

已有 SessionState、UserProfile、状态阶段、需求累积、部分 override、软偏好随轮次衰减、摘要、画像蒸馏、ContextSnapshot、REROUTE/CLARIFY 等动作，以及 12 个多轮用例。

但以下项目尚不完整：

- 仍自己解析原始文本并分类，与 1 号重复，且已经出现同句不同意图。
- 槽位 priority 字段存在，但写入时固定 1.0；软偏好有衰减，尚非任务说明中完整的多来源优先级仲裁。
- 否定和反复改口处理有已复现错误，见下一节。
- UserProfile 存在于 manager 内存，可在同一实例内跨 session 复用；没有持久化存储。是否需要进程重启后保留，由 3/5 明确，官方 reset 画像仍应独立保留。
- `update()` 当前没有 IntentResult 参数；`ContextSnapshot.action/route/retrieval_budget` 没有成为主流程控制依据。
- 候选过载判断阈值是 300，retrieval_budget 为 100，而当前下游固定返回 50；需定义“截断前候选池统计”反馈，不能拿固定 50 的输出数量触发 >300 的过载判断。
- 当前 RankingAgent 未调用 `apply_retrieval_feedback()` 形成结果反馈闭环。

证据：[manager.py](../../conversation-state-memory/src/state_memory/manager.py)、[extractor.py](../../conversation-state-memory/src/state_memory/extractor.py)、[state_machine.py](../../conversation-state-memory/src/state_memory/state_machine.py)、[context_program.py](../../conversation-state-memory/src/state_memory/context_program.py)。

### 4 号：Ranking / Clarification — 排序已有，策略效果待验收

已有 locked weighted-RRF 基线、Qwen3 点式评分、分数融合、局部模型异常回退、约束冲突评分、Top20 预选、动态 prompt 容量、候选过泛检测、结构化 PolicyDecision、clarification question 和 recommend/clarify 决策。

有 34 项测试和多套保存的实验结果。**Top20/prompt 限制属于重排层 cutoff，不等于 2 号动态召回 Top-N。**

仍需完成：

- 澄清效果尚未达到“减少无效交互、优化 MTTC”的交付目标；保存实验反而显示显著退化，不能因为函数存在就验收。
- 当前默认关闭 policy、intent-router、state-memory。开启两个模块的 flag 也只是增加上下文，不能建立完整编排。
- 澄清触发使用候选冲突与得分阈值，应基于收益和可获得的新信息校准；需要限定剩余轮数、去重、无信息回复处理，以及与 3 号动作仲裁。
- 模型 checkpoint、模型配置、训练数据/选模方式、结果文件需要一一对应。public 数据参与训练/选模时，公开成绩是开发集成绩，不是独立泛化成绩；synthetic proxy 也不是官方 private 数据。
- 已跟踪模型 adapter/tokenizer，而根 CONTRIBUTING 仍说不要提交权重；应由 4/5 统一 Release/依赖与存储约定，不在本次审查中擅自删除现有文件。

证据：[contextual_ranking.py](../../ranking_pipeline/contextual_ranking.py)、[qwen_reranker.py](../../ranking_pipeline/qwen_reranker.py)、[结果说明](../../ranking_pipeline/results/README.md)。

## 4. 补充探查复现的问题

[probe_contracts.py](probe_contracts.py) 使用临时的人工商品集，只用于证明接口/规则行为，不用于报告搜索成绩。[原始输出](probe-results.json)。

| 优先级 | 输入/场景 | 实际观察 | 建议责任人 |
|---|---|---|---|
| P1 | `I'm looking for Earrings Hoop, but I'm still exploring.` | 1 号输出 browsing/semantic_track；3 号输出 buying/buying_filter | 1/3 明确单一分类来源，5 接入 |
| P1 | `I need shoes, not leather` | 3 号输出 must_match.material=leather，没有 material 排除 | 3 |
| P1 | 黑色裙子 → 蓝色 → 改回黑色 | 最终 must_match.color=black，同时 must_not_match 含 black | 3 |
| P1 | snapshot 中价格上限 50、排除黑色 | snapshot_to_requirements 把预算放入 soft_preferences，且该转换未带上排除项 | 4 的 adapter / 2 的 contract / 5 对齐；不等于所有旁路 prompt 都丢失 |
| P1 | 人工目录全部商品价格 500，要求 under $50 | Lite 仍返回 50 个商品；Candidate 商品快照不含 price | 2/5 定义数值过滤与候选不足策略 |
| P1 | 直接调用 RankingAgent，turn=11 | 仍返回 10 个推荐 | 5；官方 evaluator 限制轮数不等于 Agent 自身有限制 |

这些问题不会被当前 63 个测试捕获。已记录证据，未修改队友实现；修复后应将这些边界升级为正式回归测试。

## 5. 当前流程与目标流程的差距

当前 RankingAgent 的主要运行路径是：

```text
User message
  -> OverrideAwareRequirementsCollector（最终检索需求来源）
  -> 可选 StateMemory 自行解析 / 可选 IntentRouter 自行解析
  -> 前两轮固定问 other
  -> 固定策略 Top50（读取 collector.requirements）
  -> locked / hybrid / local 排序
  -> 可选 policy -> Response
```

State 在 Router 之前独立处理消息；不是 `Router -> State` 数据传递。State/Router 结果主要作为部分排序模式的补充上下文。locked 排序器没有 set_session_context，因而不会消费这些补充上下文。当前候选生成没有读取 Router.route、State.action 或 State.retrieval_budget。

目标流程应当是：

```text
User -> Router(IntentResult 增量)
     -> State（累积、覆盖、硬/软/排除，唯一有效需求）
     -> Orchestrator（路由、是否追问、是否重新检索、剩余轮数）
     -> Retrieval（明确策略与预算、硬约束语义、候选池统计）
     -> Ranking / Policy
     -> Orchestrator（动作仲裁、异常回退、10-turn 上限）
     -> Response；检索/推荐反馈回 State
```

## 6. 现有实验，不能混用评测条件

以下均来自仓库保存 JSON，本次核对了对应 200 条 session 数量与 hit 计数，没有在本机重新推理。

| 配置/文件 | Hit@10 | MRR | MTTC | Efficiency | Technical |
|---|---:|---:|---:|---:|---:|
| locked-exact-override-main | 197/200 | 0.884625 | 3.205 | 0.779500 | 0.913788 |
| locked-exact 旧版本 | 194/200 | 0.870548 | 3.325 | 0.767500 | 0.899664 |
| local-exact-lora | 195/200 | 0.786365 | 3.320 | 0.768000 | 0.877009 |
| local-exact-policy | 170/200 | 0.503417 | 9.955 | 0.104500 | 0.596925 |

这些不是新版本代码下同轮重跑的受控对比；可以说明仓库已有证据与风险，不能据此宣称最新策略的严格因果增益。1/2/3/4 的 on/off 与 policy 的收益需要 5 号在同一冻结版本下重新对比。

检索模块另报完整需求 Top50=199/200、Top10=198/200；完整需求和其 buying/browsing 反事实评测不能与上述原四场景交互评测混用。

BM25 starter 和 evaluator 已存在于 `intent-recognition/`，另有 baseline_results.json（Hit@10=0.125，MRR=0.068034，MTTC=9.81）。这是 5 号可以复用的起点，仍需在本机资源准备后复现。

## 7. 交给队友的补齐清单

| 负责人 | 最小补齐要求 |
|---|---|
| 1 | 与 3 对齐 unknown/buying/browsing 和字段名、否定项，提供一致性样例；解释 confidence 与公开 Buying 未定策略 |
| 2 | 定义 route/context 输入；明确差异化策略、动态权重/截断是否交付；正确处理数值预算与排除项；提供策略消融和截断前池统计 |
| 3 | 消费 IntentResult，修复否定/反复改口，明确优先级；提供唯一有效需求与 action；完成反馈接口、多轮回归和画像生命周期说明 |
| 4 | 保留可复现 locked 基线；校准或重新设计 clarification；统一 adapter 不降级硬约束；补模型版本/结果映射和 policy 消融 |
| 5（你） | 接口版本化、workflow 实际编排、动作仲裁、turn/异常保护、统一评测和交付文档 |

## 8. 5 号的建议执行顺序

1. 冻结当前集成快照与已知 locked baseline；准备 catalog/BGE 等校验资源，核实 Python 与依赖版本。不要先改官方 evaluator 来迁就 Agent。
2. 统一 IntentResult -> ContextSnapshot -> RetrievalRequest 的字段映射：category、数值预算、硬/软约束、排除项、偏好权重、intent/route、revision、retrieval budget；明确缺失值和未知意图处理。
3. 让 State 成为有效需求唯一来源；将多套 collector 的有用 override 行为迁入统一逻辑，避免同轮多份状态互相覆盖。
4. 接入状态动作与检索反馈；统一 3/4/5 的追问决策，避免重复提问或固定两次 other 假设。
5. 在 Final Agent 实现 10-turn 硬保护、无效 ID/重复推荐校验、资源缺失与检索/模型异常回退、可追踪日志。evaluator 的异常吞掉逻辑不能替代 Agent 稳定性。
6. 在同一版本/同一资源上跑 BM25、locked、Router/State on-off、检索策略、local reranker、policy 等消融；同时报告四场景、Hit@10/MRR/MTTC/Efficiency/Technical、异常数与延迟。
7. 冻结唯一提交入口、依赖锁定和资源清单；统一 README、模块限制、Devpost 与 Demo 内容。最终成绩与模型/代码版本绑定。

本次范围止于分支收集、交付审查与验证。上述缺口尚未自动修复；不能把该集成分支当作已验收的最终 Competition Agent。

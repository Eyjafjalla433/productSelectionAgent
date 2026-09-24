# 官方要求核对、数据安装与剩余缺口

核对日期：2026-08-31；当前分支 `codex/system-integration`；业务代码基线 `f91b3d5`，本次验证前提交 `1bb8c98`。没有修改业务实现、官方 evaluator 或公开标签。

## 1. 官方来源与适用范围

- [飞书赛题第 4 节](https://bytedance.larkoffice.com/wiki/DNtSwxgeciCS2nkiUefc5qqtnkf)：已通过用户有权限的浏览器直接读取 4.2–4.6，包括架构、限制、数据、交付和评审。
- [官方 participant-kit Release](https://github.com/TechJam2026/techjam-conversational-search/releases/tag/participant-kit)：比赛专用冻结数据和参考代码，发布于 2026-08-24。
- [官方代码仓库](https://github.com/TechJam2026/techjam-conversational-search)。

官方明确不需要下载或重建完整 Amazon Reviews 2023 上游数据。当前所需的是冻结的 50,000 件 Clothing_Shoes_and_Jewelry 商品、200 条公开开发会话和官方 evaluator；800 条私有会话由主办方保留，不应尝试获取或重建。

## 2. 已下载与放置

以下路径相对仓库根目录。

| 内容 | 位置 | 结果 |
|---|---|---|
| 官方原始 ZIP，约 18.34 MiB | `.local/downloads/techjam-participant-kit.zip` | SHA256 与官方 SHA256SUMS、Release digest 一致 |
| 官方校验文件 | `.local/downloads/SHA256SUMS` | Release digest 一致 |
| 完整官方参考包 | `.local/official-kit/techjam-conversational-search/` | 包含 catalog、public set、starter、evaluator、说明和契约 |
| 项目主商品目录，约 57.74 MiB | `retrieval-and-reranking/data/catalog.jsonl` | 50,000 行、50,000 唯一 ASIN，设置为只读 |
| 意图识别商品路径 | `intent-recognition/data/catalog.jsonl` | 相对符号链接指向上述主目录，避免两份数据分叉 |
| 公开开发会话 | 两个模块各自的 `data/public_set.jsonl` | 原有文件与官方 ZIP 完全一致，未覆盖 |
| 团队运行资源包，约 159.76 MiB | `.local/downloads/techjam-runtime-assets-v0.1.0.zip` | 用已有 GitHub 登录下载，SHA256 与仓库 manifest 一致 |
| BGE 本地模型文件 | `retrieval-and-reranking/resources/bge-small-en-v1.5/` | 模型权重通过已有 manifest 校验 |
| 商品向量和 ASIN 顺序 | `retrieval-and-reranking/resources/dense_catalog_embeddings/` | 向量 shape=50,000×384，ASIN 顺序与官方 catalog 完全一致 |
| 飞书第 4 节浏览器快照 | `.local/official-spec/track4-dom-snapshots.json` | 保留已读取页面状态，作为本地参考 |
| 本次公开集逐会话结果 | `.local/evaluation/` | BM25、locked+lite、Router 的实际运行输出 |

官方 catalog 来自校验后的官方 ZIP；团队资源包中的同名 catalog 与之哈希完全相同。运行资源安装时仅以完全相同字节重新写入了 catalog，并补充 resources，之后再次验证并设为只读。

关键 SHA256：

```text
官方 techjam-participant-kit.zip
b3d7e283b835343b42c4919ea2ca90f2fb5a2aa2b10537f14dcf42f03e5b38ae

解压后 catalog.jsonl
da979b05a68af864cb0dcf9ee6a81c010c7e66a57978ad286c7a2e005fc69a67

团队 techjam-runtime-assets-v0.1.0.zip
57769c08803b2a68604e12bbc59ec07beea65d831498aa3d361cb0e0a7004f2b
```

完整安装清单和验证摘要见 [data-installation-manifest.json](data-installation-manifest.json)。`.local/` 已加入 Git 忽略规则；catalog、resources 由原有规则忽略，没有把大数据或模型加入版本控制。

仅下载官方 ZIP 即足以跑 BM25 和 lite；BGE/向量是团队 exact 模式的附加资源，不是主办方数据包提供的模型。

## 3. 数据与官方接口核验

- catalog 行数与唯一 `parent_asin` 数均为 50,000。
- public 会话数和唯一 sample_id 均为 200；全部目标 ASIN 都在冻结 catalog 内。
- 场景分布：80 buying、80 browsing、30 intent_override、10 boundary。
- `intent-recognition` 和 `retrieval-and-reranking` 内的 public_set、local_evaluator、agent_api_contract、evaluation_config 均与官方 ZIP 逐字节相同。
- 没有向正式 catalog 注入 mock ASIN，也没有更改商品字段。前次审查的人工目录仅用于隔离的临时测试，不参与此次正式公开集评测。

## 4. 本机实际评测

使用已安装的 Python 3.12.13，只运行标准库路径，没有下载/安装 PyTorch 等依赖。结果均为 200 条原始公开会话的交互式评测，不是完整需求投影或 synthetic 数据成绩。

| 本次配置 | Hit@10 | MRR | MTTC | Efficiency | TechnicalScore |
|---|---:|---:|---:|---:|---:|
| 官方原样 BM25 starter | 25/200 = 12.5% | 0.068034 | 9.810 | 0.119000 | 0.106710 |
| 当前 RankingAgent，locked + lite | 197/200 = 98.5% | 0.884625 | 3.205 | 0.779500 | 0.913788 |

BM25 各项汇总与官方 baseline_results.json 完全一致。

当前 locked+lite 分场景：

| 场景 | 命中 | MRR | MTTC |
|---|---:|---:|---:|
| Buying | 79/80 | 0.911771 | 3.100 |
| Browsing | 80/80 | 0.912500 | 3.000 |
| Intent Override | 29/30 | 0.749444 | 3.833333 |
| Boundary | 9/10 | 0.850000 | 3.800 |

Router 公开消息模拟评测也已复跑：Buying 初始硬约束覆盖 80/80，override 检出 30/30；意图输出 buying=0、browsing=90、undetermined=110。这不是 100% 的意图分类准确率。

限制：本次 locked+lite 没有启用 Router/State/Policy，也没有运行 Dense 或 Qwen。它的汇总指标虽与仓库历史 locked-exact-override-main 相同，仍只能标为 **lite 实测**，不能改称 exact 复现。公开开发集已经用于调参，也不能据此推断私有 800 条成绩。

### 复跑命令

本机系统 `python3` 是 3.9.6，不符合项目 >=3.10 要求。以下使用实际验证过的现有运行时；其他机器将变量改成自己的 Python >=3.10 路径。

```bash
TECHJAM_PYTHON=python3

# 在官方参考包目录执行：.local/official-kit/techjam-conversational-search
"$TECHJAM_PYTHON" -B -m evaluator.local_evaluator --output ../../evaluation/official-bm25-public200.json

# 在仓库根目录执行
"$TECHJAM_PYTHON" -B -m ranking_pipeline.evaluate_agent \
  --mode locked --retrieval-mode lite \
  --output .local/evaluation/locked-lite-public200.json

# 在 intent-recognition 目录执行
"$TECHJAM_PYTHON" -B scripts/evaluate_intent_router.py \
  --output ../.local/evaluation/router-public200.json
```

BGE 数据资源已具备，但该运行时没有 torch、transformers、sentence_transformers；exact 和 Qwen 推理还需单独配置依赖并验证。此次未安装或运行 Qwen 底座模型，不把“有 adapter 文件”视为本地模型可用。

## 5. 按官方要求，尚未达到什么

“核心架构目标”“硬限制”“允许优化方向”“最终提交物”不能混为一谈。以下基于实际飞书条款及代码核验，不将内部五人分工中的每一个实现选项都宣称为官方硬门槛。

| 官方项目 | 当前状态 / 缺口 | 责任侧 |
|---|---|---|
| Buying 精确过滤、Browsing 多样化语义路线（4.2 I） | Router 有 route，但主候选生成未消费；固定 schedule，Dense 主要补位；尚无真正双轨或跨类目多样性验证 | 1/2/5 |
| Multi-route → semantic ranking（4.2 I） | 基础多路和 Qwen 代码都有，本机只验证了 BM25/lite/规则排序；尚未验证完整语义链 | 2/4/5 |
| 稳健的信息累积与 override（4.2 II） | 多套解析来源不一致；“不要皮革”误记硬要求，改回旧颜色导致必须与排除冲突 | 1/3/5 |
| 过泛时 cutoff + 主动结构化澄清（4.2 II） | Policy 存在但默认关；固定 Top50，缺真实未截断候选池统计；现有历史 policy 成绩退化 | 2/3/4/5 |
| Personalized Context Distillation（4.2 III） | 有内存画像和摘要，但没有成为主路径有效需求来源；应与官方 reset 画像衔接，不必扩成大型持久化系统 | 3/5 |
| Runtime workflow re-orchestration（4.2 III） | State.action/route/retrieval_budget 未实际控制流程，检索反馈未形成闭环，前两轮固定 other | 3/5 |
| 权重、动态截断、slot decay（4.3 in scope） | 软槽位衰减已实现；检索权重/截断固定，是尚未完成的优化方向，不等价于所有方案必须采用同一算法 | 2/3/5 |
| 严格 10-turn 上限（4.3 limits） | evaluator 限制 10 轮；Agent 单独接受 turn=11，Final Agent 必须自带保护 | 5，优先处理 |
| 数据只读、不注入 ASIN（4.3 limits） | 本次已校验，正式 catalog 保持原样并只读；后续修改代码需持续保证 | 5，持续守住 |
| 资源合理、内存执行、文本模态（4.3） | 当前轻量检索符合方向；Qwen 的内存、依赖、延迟与 CPU 可复现性仍需实测 | 4/5 |
| 模型训练范围（4.3） | 文档禁止基础模型训练/全参数微调，允许 prompt/local scoring；现有 LoRA 涉及 attention/MLP adapter，具体是否属于允许范围不能仅凭代码断定，需对照主办方进一步说明 | 4/5，待确认而非已判违规 |
| 公共 GitHub 代码库（4.5） | 已用 GitHub CLI 核实当前团队仓库 isPrivate=true；集成分支也仅本地存在，尚不满足最终公开交付 | 5；不能未经授权改可见性 |
| 总 README（4.5） | 模块说明已有，缺统一最终入口、跨机器安装与复现流程、真实模型/资源依赖、完整团队贡献表 | 1–4 提供材料，5 汇总 |
| Devpost 书面项目说明（4.5） | 本次没有发现/核验已完成投稿，不能算已交付；应包含问题回应、工具、API、框架、数据与资产 | 5 |
| 公开 YouTube Demo 并链接 Devpost（4.5） | 未核验已交付；需要端到端多轮演示，API/终端 walkthrough 可接受，无需另做 UI | 5 |
| 模型成本/token/latency/fallback 披露（kit） | 当前无模型路径 usage=0 合理；Qwen 路径仍沿用零 usage，需补实际统计或说明限制，并提供延迟与降级记录 | 4/5 |

约束转换仍是必须修复的集成缺口：snapshot_to_requirements 将数值预算降为 soft，且该转换不携带排除项；候选快照不含 price。应联合版本化接口，不能为满足固定 50 个候选而静默违反明确约束。前次复现证据见 [交付审查报告](DELIVERY_AUDIT_2026-08-31.md)。

### 综合评审不只看 TechnicalScore

飞书 4.6 的评审权重为：技术执行 35%、创新与问题洞察 20%、影响与相关性 20%、可行性与实用性 15%、最终现场展示与沟通 10%。本地 evaluator 的 0.913788 是其自动评测公式结果，**不能说成比赛总评分 91.38%**。

## 6. 建议下一步

1. 先修 P1 状态/否定/预算/排除和 10-turn 保护；保留当前 locked+lite 实测为回归基线。
2. 统一 Router -> State -> Retrieval 的唯一需求契约，接通 re-orchestration 和候选池反馈。
3. 在隔离 Python 环境验证 exact/Qwen，锁定模型和依赖；按相同代码、相同资源对比 Router/State、Dense、模型和 Policy 的消融。
4. 完成唯一 Final Agent、跨机器复现、成本与延迟报告、贡献表、Devpost 和 Demo。
5. 经团队确认后发布集成代码并满足公开仓库要求；目前未推送、未改变仓库权限，也未代发任何投稿或视频。

无需做的额外工作：整个 Amazon 原始库下载、获取 private800、强制前端 UI、多模态、外部工业向量数据库或多用户并发压力系统。

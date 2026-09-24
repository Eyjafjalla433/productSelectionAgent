# ESCI 数据实测、论文阅读与三周开工方案

分析日期：2026-09-11。结论：建议以 **ESCI 英文 small 版的候选排序和四分类**为主实验，以**人工复核的约束与澄清子集**证明 Agent 的额外价值。两套评测独立报告。

本次已下载官方三个文件、校验两个 Parquet 的 Git LFS SHA-256、扫描全量数据并查看训练样本。数据固定在官方 commit `7916cdf6ab75a462e77f20ab40428a10923998d5`。原始文件约 1.16 GB，保存在仓库忽略的 `.local/esci/`；统计是本次计算结果，不是照抄论文。尚未训练或评测任何 ESCI 排序模型。

**先读数据结论**

| 数据范围 | query 数 | query-product 判断数 | 建议用途 |
|---|---:|---:|---|
| 当前全量 large | 130,652 | 2,621,288 | 四分类、替代识别；后续扩大训练 |
| small，三语言 | 48,300 | 1,118,011 | 候选排序主 benchmark |
| small，US train | 20,888 | 419,653 | 首轮训练及内部 dev |
| small，US test | 8,956 | 181,701 | 最终冻结测试 |

三个市场的商品元数据共 **1,814,924** 行：US 1,215,854，ES 260,011，JP 339,059。examples 用到了其中 1,814,883 个 `(product_locale, product_id)` 组合。所有判断都能 join 到商品，无重复商品组合键，无重复 `(query_id, locale, product_id)` 判断。

需要纠正原始设想中的三点：

- 论文中的 2,621,738 与当前文件的 2,621,288 不一致；当前 README 内部也存在不同数字。实验以固定文件及 checksum 为准，不能混用论文时期的 public test 和如今发布的 test。
- “最多 40 个候选”不是当前文件的可靠硬限制。small 有 **1,219 个 query 超过 40**；US small train 最大 188，test 最大 95。不要硬编码 40，也不要用文件前 40 行截断评价集。
- large 中 `query_id=79706, query=piano` 同时有 train/test 行。扩大训练时，必须按 query 排除与评测重叠的训练数据。不能认为官方标记已经覆盖所有泄漏问题。

small 的所有 US 训练 query 都有 E，但 US test 有一个 query 没有 E；“至少有一个 Exact”同样不能写成通用假设。全量 `query_id` 有 130,652 个，原始 query 文本有 130,193 种，说明标识符和文本不是同一计数口径。

原始数据来源：[Amazon ESCI 官方仓库](https://github.com/amazon-science/esci-data)。以上实测的详细分组和复现来源见 [profile.json](profile.json)。

**哪些字段值得处理**

| 字段 | 本次实测 US 商品缺失率 | 用途与处理方式 |
|---|---:|---|
| `product_title` | 0% | 首个词法和语义基线；抽取商品类型、型号、尺寸、兼容对象 |
| `product_bullet_point` | 14.70% | 最优先补充的属性证据：功能、材质、适用对象、包装内容 |
| `product_brand` | 5.95% | 建立别名及标准化表；区分商品品牌和“兼容某品牌” |
| `product_color` | 33.40% | 原值与归一化色系并存；缺失、混色和自定义色名需要 unknown |
| `product_description` | 46.86% | 作为补充证据；清理 HTML，按 query 选片段并限制长度 |
| `query` | — | 检索输入、意图与条件抽取；保留原文及规范化形式 |
| `esci_label` | — | 四分类、同 query 的偏序训练、子类错误分析 |
| `query_id`, `split` | — | 查询级切分和评测分组；不作为模型语义特征 |
| `product_id`, `product_locale` | — | 联合键、去重和语言路由；不能仅按 ASIN join |
| `small_version`, `large_version` | — | 任务范围选择；不输入分类器 |
| `source` | — | 困难样本筛选与分桶分析；是采样来源，不是用户画像 |

缺失口径为 NULL 或去除首尾空白后的空字符串。`product_color` 里还能出现 `Washable`、规格组合等非纯颜色值；有字段不代表有可信的规格事实。商品表没有独立、稳定的价格、库存、配送、类别树、用户画像、尺寸或兼容性字段；部分属性只藏在文本中。

建议首版商品文本顺序：`title → brand → color → bullet points → description 的相关片段`。总预算先设 384–512 tokens，再做字段消融；数字、单位、型号、否定词不能在清洗中删掉。商品证据保存 `{field, raw_value, normalized_value, evidence_span, status}`，`status` 至少包括 supported / contradicted / unknown。

**最值得拿来做实验的条目**

`sources.csv` 有以下现成入口。全量 query 计数：`negations` 6,964、`parse_pattern` 6,083、`nlqec` 169、`behavioral` 3,780、`other` 113,656。它们是采样类别，不应直接当作经过人工验证的 query 类型标签。论文说明 negations 与 parse pattern 主要来自规则筛选，NLQEC 来自自然语言商品查询数据。[原始论文，第 2.1 节](https://arxiv.org/pdf/2206.06588)

缩小到首轮使用的 US small train，仍有 **1,629 个 negations query / 50,303 条判断**、**1,777 个 parse_pattern query / 62,369 条判断**、**69 个 nlqec query / 1,793 条判断**。优先从这三组做开发样本，保留所有其他 query 作为正常分布对照；不要只在困难子集上报总体指标。

| 样本池 | 已有规模／真实例子 | 可做处理 | 还缺什么 |
|---|---|---|---|
| 同 query 有 E 与 S | US small train **18,066 个 query** | E/S 难例、替代识别、成对排序 | S 为什么偏离，以及用户是否接受该偏离 |
| 同 query 有 E 与 C | **5,408 个 query** | 主商品与配件分流，降低配件挤占主推荐的概率 | 具体商品之间的兼容关系 |
| 同 query 四类齐全 | **2,739 个 query** | 诊断集、解释展示、四分类混淆分析 | 少量标签复核 |
| 否定／排除 | `1 in curtain rod black without brackets` | 提取黑色、直径、排除支架；匹配属性证据 | 哪些约束确实是硬约束 |
| 自然语言需求 | `A plain black, warm puffer jacket with a hood and decently sized pockets`，query 5624 | 类型／颜色／帽子／保暖／口袋抽取；软硬区分 | “warm”“decently sized”的可验证标准 |
| 复合电子产品需求 | query 2162 要求 15-inch、轻、较多存储/RAM、HDMI/USB | 数值与接口标准化；缺少阈值时澄清 | “lots of memory”究竟是多少 |

query 5624 的真实候选具有良好的教学价值：E 包含 Orolay 黑色羽绒服 `B00HHOXV7Q`；S 包含橄榄色 Steve Madden 外套 `B0751XL17H`；C 是黑色羽绒服修补贴 `B07YDPQNMY`；I 包含红色儿童摇粒绒外套 `B07NQW2QYF`。这组可展示颜色偏离、配件识别与品类区别。这里只转述原始标签，尚未人工验证所有“保暖、帽子、口袋”条件。

US small train 中，简单规则找到含基本颜色词的 query 1,562 个，含数字 4,395 个，含 for/with/without 4,036 个；1–3 个空格分词的短 query 有 9,642 个。**这些只是重叠的启发式候选池，不是属性抽取模型准确率。**

训练样本中可以看到明确的证据冲突。例如 query 1140：`1/4” black buttons for sewing without holes`，产品 `B07CZWRH6Z` 的标题描述棕色、四孔、2 英寸木纽扣，`product_color=Dark Brown`，却标为 E。query 940 要求 black，部分 E 商品的颜色为 Brushed Nickel。也存在产品类型看上去都不吻合的 E 样本。

这些例子是按颜色等词及四类齐全条件有意挑出的训练难例，**不能用来估计全数据的错误率**。它们证明：训练时可保留官方 E/S/C/I，但评估“硬约束是否满足”时必须另有证据标注；也不能把 E 自动变成“所有约束均满足”。原论文也承认标注噪声与 S/I 边界歧义。[原始论文，第 2.2 节](https://arxiv.org/pdf/2206.06588)

特别注意，ESCI 的 S/C 是 **query-product 关系**。不能从“同一个 query 下有 E 和 C”推出任意 E 商品与任意 C 商品都互相兼容，也不能直接生成经过验证的商品知识图谱。

**SOTA 要按任务理解：读到的主要方法与结果**

截至本次检索，没有找到一个同时统一 ESCI 原版、多轮澄清、硬约束、开放网页采购的排行榜。以下称“竞赛冠军”“论文内最好”“可用强基线”，分别说明协议；不把不同论文的数字横向排序。

| 工作 | 方法和阅读结论 | 对我们有何用处 |
|---|---|---|
| [A Semantic Alignment System，KDD Cup 2022 排序冠军](https://arxiv.org/pdf/2208.02958) | cross-encoder 联合读 query 和商品，先预测 E/S/C/I，再按概率加权排序；辅以多语言/英文模型、翻译增强、蒸馏和集成。报告竞赛 nDCG **0.9043**。 | 优先复现单模型四分类→排序这一核心；不必复刻昂贵集成。0.9043 不是我们 US test 的 nDCG@10 基准线。 |
| [Some Practice for Improving the Search Results of E-commerce，2022](https://arxiv.org/pdf/2208.00108) | cross-encoder 概率和候选组特征进入 LightGBM；Task 2/3 均第二。论文明确使用过与数据组织、任务成员关系有关的泄漏特征。 | 学字段建模与分数校准。剔除文件顺序、跨任务成员关系等不可部署特征，避免“涨分”来自数据捷径。 |
| [Qwen3 Embedding，2025](https://arxiv.org/html/2506.05176v3) | 检索双塔与逐对 reranker；后者通过 yes/no token 评分，有 0.6B/4B/8B。多阶段训练与高质量合成数据支持泛化。 | 可直接用 0.6B 做零样本基线。其相关性分数不是 E/S/C/I 四类概率，也不自动证明硬约束通过；通用 IR 成绩不代表 ESCI 当前冠军。 |
| [Rec-R1，2025，v3](https://arxiv.org/html/2503.24289v3) | 冻结检索器，GRPO 让 query 改写直接优化检索指标；扩展到生成候选排列。Table 4 在自建四领域候选协议中，Sports 的 nDCG@10 为 **62.91**，GPT-4o 为 **53.62**；Video Games 上 GPT-4o 仍更好。 | 关键是优化真实排序结果，而非只模仿漂亮改写。候选来自 Rec-R1 retriever，不能与官方候选评测直接比较；RL 留作后续。 |
| [FlexRec，2026 预印本](https://arxiv.org/html/2603.11901v1) | 面向固定候选排列，以反事实交换构造 item-level reward，用不确定性缩放稳定 RL。Table 2 的 ESCI nDCG@10：**0.553**，其 Rec-R1 对照 **0.536**，GPT-4o **0.530**。 | 这是较新的需求条件化排序路线。训练/测试各抽 5,000/1,000，4×A100 全参 RL；ESCI 候选构造仍需核查代码，不能宣称全量统一 SOTA。 |
| [ProductAgent / PROCLARE，EMNLP Industry 2025](https://aclanthology.org/2025.emnlp-industry.25.pdf) | 结构化记忆、SQL/向量检索、候选属性统计、澄清工具形成闭环。百万商品，2,000 模拟会话、每会话 5 轮；Table 3 中 GPT-4+BM25 的 Hit@10 为 **39.48**，BM25+GTE 为 **36.93**。 | 最贴近我们的 Agent。论文中的混合检索与 LLM 重排并非必然提高；澄清收益来自模拟用户，不等于真人体验验证。 |
| [PSCon，SIGIR 2025 / 公开预印本](https://arxiv.org/html/2502.13881v1) | 通过受指导的人与人对话收集双语言、双市场 CPS 数据；覆盖意图、关键词、系统动作、问题选择、商品排序、回答生成。 | 可作为真实语言的外部校验来源，补足仅依靠模拟器的偏差；商品 ID、候选与标签不能直接并入 ESCI。 |
| [AgenticShop，2026 预印本](https://arxiv.org/html/2602.12315v1) | 目标查找、替代比较、开放探索；按用户要求 checklist，结合商品来源证据评估个性化。Table 3 的 judge-human κ 为 **0.7320**；只给 URL 时为 **0.3792**。 | 借用“逐条件、有证据”的评价方式。首版在冻结商品文本上做，不必扩展到整个互联网；LLM judge 仍需人工抽查。 |
| [Small Agents, Big Gains，ACL Industry 2026](https://aclanthology.org/2026.acl-industry.39.pdf) | 有状态用户模拟器与 critic 反复修订购物轨迹，用于 SFT/DPO。开放权重复现实验中，Qwen3-30B-A3B 的 helpfulness 从人工 SFT **0.735** 到 DPO **0.809**。 | 第三周可借用轨迹审核与失败分类；这是 rubric 分数，不是 ESCI nDCG，也不是三周必须训练的规模。 |
| [TREC 2025 Product Search and Recommendation 概述，2026-08](https://arxiv.org/html/2608.17138v1) | 查询改写与商品关系推荐，另建人工池化标注。Hard query 上 RM3 task-completion nDCG **0.459–0.466**，高于 LLM baseline **0.420**；改写仍会伤害部分 query。 | 全库检索和多商品任务的下一阶段外部评测；保留原 query 路径，并报告改写退步率。该指标 gain 与 ESCI 不同。 |

补充的属性解析参考：[Building Natural Language Interface for Product Search，CIKM 2024](https://doi.org/10.1145/3627673.3680070)。其方向是用 LLM、搜索日志和商品信息生成 query→结构化 API/filter 数据，再训练 schema generator。对我们最有用的是先固定属性空间、操作符和值域，而不是让 LLM 自由编造过滤器。本文官方 PDF 的网页读取失败，本次只核对了出版页与索引到的正文片段，不将其当作已完整阅读全文的证据。

**建议的实现和实验**

最先落地：`需求状态 → 检索 → 逐对相关性 → 约束证据检查 → 推荐 / 澄清 → 更新状态`。

排序模型和约束验证保留两种输出：

```text
relevance: {p_E, p_S, p_C, p_I}
constraints: [{name, operator, wanted, observed, evidence, status}]
```

四分类训练以普通 cross-entropy 起步，单独对比类权重或 label smoothing，再加同 query 的 pairwise loss。首个候选分数为 `p_E + 0.1*p_S + 0.01*p_C`。优先采 E/S 和 S/I 难例；不要把 S、C 与随机 I 全压成同一负类。未经分类训练的 Qwen yes/no 评分走独立 baseline，不能伪装成四类概率。

硬约束是可行性判断：有明确反证的商品不进入“满足要求”列表；证据不足是 unknown。软偏好才用加权降分。S 可以出现在“替代选择”列表，但偏离已确认硬约束时，要先获得用户放宽该条件的表达。C 独立展示为搭配商品，不挤占主商品 Top-K。

澄清应由仍未确定且能改变候选选择的条件触发。候选按相关属性分组，优先问能区分高质量候选、且用户尚未回答的问题。仅有颜色字段缺失时，继续查已有商品文本可能更有效；问用户不能凭空补全商品事实。首版用规则和 dev 调阈值，比较零提问、固定 1 问、固定 2 问、候选驱动提问。候选熵可作启发式，但并不等价于用户收益；用每轮成功率及交互成本检验它。

**三个评价层不能混在一起**

| 层级 | 输入与真值 | 指标和边界 |
|---|---|---|
| A：给定候选排序 | small 原始 query 的完整已标注候选 | nDCG@10、全深度 nDCG、Exact Recall@K、四类 Macro/Micro F1、S 类二分类 F1 |
| B：全商品库检索 | 同市场 corpus，自行召回 | 对已知 E 计算 Recall@50/100，同时报 judged coverage；未标注商品不等于 I。需要 TREC 或补标才能更充分证明全库收益 |
| C：Agent 选择 | 单独的需求、属性证据、允许澄清答案、多个可接受商品 | Success@1/3、硬约束满足率、unknown 率、推荐覆盖率、平均/P95 澄清轮数、每轮成功曲线、成本与延迟 |

US small train 标签分布：E 181,819 (**43.33%**)，S 147,628 (**35.18%**)，C 19,090 (**4.55%**)，I 71,116 (**16.95%**)。只报 Micro-F1 容易忽视 C，故保留每类 F1 和混淆矩阵。Exact Recall 的分母是该 query 已标注 E 的数量；E=0 的 query 排除于该指标均值并另报数量。nDCG 的 gain 直接使用 `{E:1,S:0.1,C:0.01,I:0}`，不要再作 `2^gain-1`。全零 IDCG 需要明确定义并统计。

代码审阅还发现官方历史脚本不一致：训练映射 S=0.1/C=0.01，但 `prepare_trec_eval_files.py` 编码 S=2/C=3，启动脚本又指定 2→0.01、3→0.1，组合起来交换了 S/C。我们按论文的标签 gain 实现并写微型手算用例；如复现历史脚本，应另外命名协议，而不是假定 README 的 0.83 可直接比较。[训练脚本](https://github.com/amazon-science/esci-data/blob/7916cdf6ab75a462e77f20ab40428a10923998d5/ranking/train.py) · [qrels 脚本](https://github.com/amazon-science/esci-data/blob/7916cdf6ab75a462e77f20ab40428a10923998d5/ranking/prepare_trec_eval_files.py) · [评测命令](https://github.com/amazon-science/esci-data/blob/7916cdf6ab75a462e77f20ab40428a10923998d5/ranking/launch-predictions-task1.sh)

模型选择只看 train 内按 query 划出的 dev；建议按规范化 query 文本一起分组，从 US train 留出约 10%。不要随机拆 query-product 行。扩大到 large 时排除 dev/test 的 query；预训练模型使用过 ESCI 与否通常不能完全审计，应在模型卡中披露这一不确定性。test 只用于最终固定方案评测，按 query bootstrap 报差值置信区间。

Agent 子集建议 **300 个 query，每个 3–5 个候选**：100 个属性明确、100 个需要澄清、50 个无满足项、50 个反馈改变条件。来自 train/dev，按原 query 分成开发和冻结评估两部分；这些比例是建议设计，不是数据原有分布。LLM 可起草，人工复核 hard/soft、证据及所有可接受候选；标注员看不到模型排名。无满足项可以通过明示合成条件构建，但必须标注 synthetic。改写 query 或增加条件后重新标注，不能沿用原 ESCI 标签。模拟器只读取自己的需求卡，不读模型分数或答案排序；先用结构化回答，再检验自然语言模拟器的稳定性。

**三周的具体工作顺序**

| 时间 | 要交付的东西 | 完成判据 |
|---|---|---|
| 第 1–2 天 | 数据适配、query 级 train/dev、标签映射、评测器 | join/重复/泄漏断言通过；手算 nDCG、F1 与空标签边界通过 |
| 第 3–5 天 | 固定候选的标题 BM25、完整字段 BM25、dense、RRF 四个基线 | 同候选比较；保存逐 query 结果、source/属性分桶、速度 |
| 第 6–9 天 | 零样本 reranker 与一个领域四分类模型 | 比较字段、loss、E/S/S-I 难例；选择 dev 最优而非 test 最优 |
| 第 10–12 天 | 约束证据解析、人工审核子集、三态验证器 | 独立检查抽取与验证误差；显示证据，报告误杀和 unknown |
| 第 13–15 天 | 候选驱动澄清、需求更改、重新检索 | 固定检索和排序器，比较四种提问策略，测每轮净收益 |
| 第 16–18 天 | 消融、置信区间、失败分析与可操作演示 | 主结果可复现，收益和退步类型明确 |
| 第 19–21 天 | 冻结测试、报告、整理重放样本 | 排序/Agent 两张表，写清真实和合成标注，完整运行命令 |

这是一项范围控制建议，不是硬件耗时保证。CPU 可先完成数据、词法基线、证据与评价；已有合适 GPU 时再跑 0.6B reranker 或较小 encoder 微调，batch 与精度经显存试跑确定。暂不把全参 RL、多语言集成、视觉或实时采购接入排进必做项。

**与当前仓库的衔接**

当前根 README 的项目使用 Amazon Reviews 2023 服饰目录、`parent_asin` 和 TechJam 对话协议；该公共开发集的 Hit@10/MRR/TechnicalScore 不是 ESCI 的结果。已读入口确认当前 `shopping_agent/ranking.py` 使用 CPU 打分适配，不能因仓库存在 Qwen helper 就称线上入口已运行 Qwen。

可复用 `conversation-state-memory/` 的状态操作、`shopping_agent/policy.py` 的策略框架和检索／排序接口；另建 ESCI 数据适配及 evaluator。`parent_asin` 不应无条件等同 ESCI 的 `(locale, product_id)`。现有服饰词表、价格条件、Top50 上限、固定两轮澄清都需要重新检查；small 的给定候选协议不能被现有 Top50 合约截断。

建议第一张实验表只回答：“同一批完整候选上，BM25 → dense/RRF → cross-encoder → ESCI 四分类，是否逐步提高 nDCG 和 E/S 区分？”第二张再回答：“保持排序器不变，证据验证与动态澄清能否降低约束违反，同时控制轮数？”这两张表就是项目的核心交付。

**复现本次数据分析**

在仓库根目录执行，Python 3.11+：

```powershell
python -m pip install --target .local/esci-deps duckdb==1.5.5
python -X utf8 scripts/analyze_esci.py
```

输出 [profile.json](profile.json)，包含 schema、来源及 hash、字段缺失、标签、split、候选分组、source 分桶和训练样本。样本展示是有目的的难例选择，未导出测试样本。脚本在 [scripts/analyze_esci.py](../../../scripts/analyze_esci.py)。应用主依赖和业务代码未修改。

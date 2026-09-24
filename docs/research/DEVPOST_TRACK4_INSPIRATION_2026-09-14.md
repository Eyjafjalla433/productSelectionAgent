# TechJam 2026 Track 4 方案扫描与 MVP 决策

> 快照日期：2026-09-14。竞赛计划在 2026-09-15 公布获奖者，因此本文不是“获奖名单”。
> 点赞数会变化，也不等价于技术质量；评分数字均为各队公开页/仓库的自报 public-set 结果。

## 范围与方法

- 抓取 Devpost Track 4 筛选页全部 10 页，共 236 个项目。
- 用点赞量筛出高关注项目，再按工程完整性、实验可信度、交互表达和与本项目的互补性复筛。
- 浅克隆 8 个公开仓库到 `.local/devpost-reference/`，固定当前 commit 做只读对照；该目录被 `.gitignore` 排除。
- 8 个仓库根目录均未发现 LICENSE 文件。因此只借鉴公开阐述的设计模式，不复制实现代码、文案或视觉资产。

本地只读快照：SATU `3374fea`、Mercury `ced0a6f`、Goated `07d0148`、
BayesPilot `19fdab6`、APERTURE `5f01c96`、Needle `f2ccfa9`、ARC `c6387a3`、
MIRA `81ec8c2`。

入口：[全部 submissions](https://tiktoktechjam2026.devpost.com/submissions)；[赛事说明与评审标准](https://tiktoktechjam2026.devpost.com/)。

## 最值得看的方案

| 项目 | 抓取时关注度 | 公开亮点 | 对我们的启发 |
|---|---:|---|---|
| [SATU](https://devpost.com/software/satu) | 356 | 动态 slate、候选熵问题选择、shown-set 去重；自报 TS 0.9672 | “展示一个结果”也是一次不可逆承诺；问题和展示位应共用预算 |
| [Mercury](https://devpost.com/software/mercury-bx4dun) | 304 | 结构化证据、guarded paging、可检查 decision receipt | 反馈不能混入商品 query；改口后 paging 应重置 |
| [Goated Agent](https://devpost.com/software/thopzzzzzz) | 209 | template → catalog gazetteer → LLM 的级联；ESCI/自然改写压力测试 | 最大风险是对官方 simulator 话术过拟合；原始查询不能因抽取失败而丢失 |
| [BayesPilot](https://devpost.com/software/techjam-track-4-bayespilot) | 110 | 两级贝叶斯、posterior-mass 候选池、expected-utility 深度；自报 TS 0.9744 | 候选池宽度与展示深度应由“保留概率质量/预期效用”决定，而非固定常数 |
| [APERTURE](https://devpost.com/software/retrieval-as-sensing-a-copilot-that-retrieves-to-understand) | 47 | 把 retrieval 当作感知，用候选集反过来决定澄清 | 问题必须由实时 catalog evidence 支撑，不能是固定问卷 |
| [Needle](https://devpost.com/software/needle-0rae23) | 43 | 状态 ledger、clause-level negation、decision receipt、评分路径与产品层隔离；仓库自报 TS 0.9785 | 产品 UI 可丰富，但必须显示真实 trace，且不能反向污染评分核心 |
| [ARC](https://devpost.com/software/constraintflow-shopping-copilot) | 29 | Answerability-aware MVOI、Ask/Rank/Commit、可视化 planner；自报 TS 0.9804 | 信息增益还不够，要乘上“用户能否回答”与最终指标价值 |
| [MIRA](https://devpost.com/software/mira-maximum-information-retrieval-agent) | 9 | posterior belief、answerability-weighted EIG、metric-aware slot portfolio；自报 TS 0.9805 | rank 1 保 MAP，余下位置做去相关覆盖；把 MRR/MTTC 权衡显式化 |

关注度前三是 SATU、Mercury、Goated Agent；技术方法上最值得继续拆解的是
MIRA、ARC、Needle、BayesPilot。两组名单不同，正说明点赞只能作为入口信号。

## 可复用的共同模式

1. **对话是状态变更，不是 transcript 拼接。** set / clear / exclude / override / refusal 要有不同语义；旧值应可审计但不能继续污染检索。
2. **问题有机会成本。** 优先考虑 `answerability × coverage × information gain × downstream utility`，而不只是“还缺哪个字段”。
3. **展示位也有机会成本。** 早期十条全出容易提高 Hit、伤害 MRR；只出一条又可能无法恢复。应按 rank contention 或 expected utility 动态决定 1–10 条。
4. **没有成交就是新证据。** 已展示但会话继续的商品应进入 refutation/shown ledger，并触发去重、paging 或替代排序。
5. **核心与产品层隔离。** UI 可以解释、翻译、编排，但不得在浏览器重算排名，也不能偷偷改变被报告的 agent。
6. **必须有反过拟合评估。** public 200 之外至少要有 catalog-disjoint、paraphrase、真实 ESCI query、否定/改口/错别字切片。
7. **负结果要保存。** 多队都发现 dense、LLM、buying/browsing 双路或更复杂 fusion 可能变差；只有 end-to-end ablation 能决定是否保留。

## 与 Show Me Your Agent 的差距

现有方案已经具备：结构化状态、显式 override/exclusion、SQLite FTS5 多路召回、
CPU reranking、pre/post policy、真实 trace、离线零 token、public 200 的 0.914913
TechnicalScore。主要短板是：

- 当前提交策略固定暖场两问、随后固定最多 10 条，尚无 metric-aware 动态 slate。
- shown product 已记录，但检索/排序没有把“展示后仍继续”完整消费为 refutation evidence。
- 问题策略以规则和预算为主，尚无 catalog-driven answerability/EIG。
- 终端 demo 很诚实，但难以在 3 分钟内让评委看懂状态变化与每层决策。
- 公共集表现强，但真实自然查询下的 category/negation 鲁棒性仍需单独量化。

## 本轮落地的基础 MVP

新增 `mvp/`，坚持“产品层不污染评分核心”的边界：

- 零第三方依赖的本地 HTTP 服务，复用真实 `agent.Agent` 和 50,000 商品目录；
- 三栏交互：故事入口、真实对话/商品卡、实时 Decision Lens；
- 从 agent trace 提取有界 receipt：hard/soft/exclusion、policy reason、候选数、展示数、问题数、各阶段耗时；
- 不把 50 个候选和完整商品文档发给浏览器，不在 JS 里重算分数；
- 精准购买、探索需求、改口测试三个录屏入口；
- 独立单元测试验证 session、商品 enrichment、receipt 边界。

这轮 MVP 借鉴的是 Needle/Mercury 的“真实回执”、领先项目共同的状态可视化，
没有把尚未验证的动态 slate/EIG 直接塞进正式 Agent。下一轮应先做离线 ablation，
再决定是否进入 submission path。

## 建议的下一轮实验顺序

1. **Shown-set exclusion**：对 public、disjoint、override 切片跑 paired ablation；验证是否改善重复和恢复，不只看总分。
2. **Dynamic slate**：先 sweep score-relative contention，再试 expected-utility depth；同时报告平均展示数、MRR、MTTC。
3. **Catalog-driven question board**：对 color/material/use-case/style 等统计 coverage、entropy、answerability，保留 `unknown/not applicable` 分支。
4. **Real-query gate**：复用现有 ESCI 分析，构造不会泄漏 hidden target 的真实 query/改写评估；任何 public gain 若明显伤害该 gate，拒绝合入。
5. **产品层完善**：把 top-result evidence 从内部 feature 名翻成短句，但服务端生成且保留原始 evidence 展开项。

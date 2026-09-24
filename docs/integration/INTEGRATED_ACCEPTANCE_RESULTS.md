# 完整CPU链条实测验收

2026-09-01，入口 `shopping_agent.FinalAgent`，Python 3.12.13，真实50,000商品，词法召回 + 4号 HybridContextualReranker CPU路径。所有数据和 evaluator 保持原样。

## 实际结果

| 配置 | Hit@10 | MRR | MTTC | Efficiency | TechnicalScore |
|---|---:|---:|---:|---:|---:|
| **本次完整链条** | **194/200 = 97%** | **0.575508** | **2.255** | **0.874500** | **0.832552** |
| 此前旧 RankingAgent locked+lite，历史对照 | 197/200 = 98.5% | 0.884625 | 3.205 | 0.779500 | 0.913788 |

本次200个会话无流程异常。整次运行含加载约34.1秒；当时还有其他调试进程运行，这不是独占性能基准。Efficiency是官方按MTTC算出的指标，不是硬件吞吐。CPU没有LLM调用，报告token用量为0。

**新链条接口已贯通，但排序指标和综合分仍低于旧基线。** 平均交互轮数减少不代表排名质量提高。旧基线数据来自此前的[官方要求与数据报告](OFFICIAL_REQUIREMENTS_AND_DATA_2026-08-31.md)，不是声称本次又复跑了旧入口，也不是严格单变量消融。

| 场景 | 数量 | Hit@10 | MRR | MTTC |
|---|---:|---:|---:|---:|
| buying | 80 | 0.9625 | 0.539995 | 1.625 |
| browsing | 80 | 1.0000 | 0.558239 | 1.950 |
| intent_override | 30 | 0.933333 | 0.679762 | 4.333333 |
| boundary | 10 | 0.9000 | 0.685000 | 3.500 |

这200会话是用于调试的公开集，不是独立盲测集；不能推断隐藏集成绩。

## 测试覆盖

共109项通过：1号17项、3号26项、2号13项、4号34项、新集成19项。回归期间保留了用户原先已有的 catalog_lexicon.json 本地修改。

新集成验收覆盖：多轮覆盖与清空、类别切换、排除及取消排除、少量/空候选、未知价格、4A跳过检索、4B澄清、实际提问次数、两次提问上限、10轮限制、请求幂等、会话隔离、过期结果拦截、硬条件不被重试放松、模块异常不回用旧候选、Dense故障词法降级、品牌词典噪声、官方偏好覆盖、软偏好衰减与画像输入。

测试输出：`.local/integrated-debug/test-summary.txt`。Dense故障项使用故障注入测试，不代表真实Dense模型已经跑过。

## 真实断点结果

Python bdb在 `shopping_agent/agent.py` 的9个模块边界自动暂停并记录局部变量，共77次断点。不是伪造样例，也没有替换商品检索为mock。

| 场景 | 轮数 | 断点数 | 结果 |
|---|---:|---:|---|
| 手动修改条件 | 4 | 36 | 四轮全部完成真实检索和推荐 |
| 4A先澄清 | 2 | 14 | 第一轮缺类别→询问；第二轮补充后检索 |
| public_0001 | 1 | 9 | 第1轮命中，目标排第7 |
| public_0006 | 2 | 18 | 第一轮4B询问feature；第二轮命中，目标排第1 |

四轮手动样例的BP2实际值：

| 轮次 | 累计条件 | 多路去重数 | 硬过滤后数量 | 交给4B | 输出 |
|---|---|---:|---:|---:|---:|
| 1 | dress + black + ≤50 | 574 | 21 | 21 | 10 |
| 2 | dress + blue + ≤50 | 573 | 29 | 29 | 10 |
| 3 | dress + blue，清除预算 | 573 | 251 | 50 | 10 |
| 4 | shoes，排除leather | 599 | 543 | 50 | 10 |

第一轮BP4B的Top1为 `B0BBZXJYQ3`，分数1.0；4B输出顺序而不是命中概率。BP-feedback的版本为2，已展示10个ASIN；第二轮BP3版本为3，仍有类别和预算，但颜色已经变为blue。

public_0006第一轮：`intent=browsing`，hard.category=`basketball men`，4A允许检索；536个去重候选→170个过滤后候选→50个交给4B；4B输出 `clarify/feature`，最终推荐为空。第二轮收到关于Drawstring closure、透气网布的回答，累计为软偏好；类别没有被改写，最终推荐10个，第一名 `B071F2Z7JG`。

精简逐轮快照：[integrated-breakpoint-snapshots.json](integrated-breakpoint-snapshots.json)。完整局部变量位于：

- `.local/integrated-debug/debug_manual.json`
- `.local/integrated-debug/debug_pre_clarify.json`
- `.local/integrated-debug/public_0001.json`
- `.local/integrated-debug/public_0006.json`
- `.local/integrated-debug/public_200.json`（全量指标及200条会话结果）

以上五份最终输出中的43个模块源码哈希均与本次最终源码一致，errors数组均为空。`public_200-locked-initial.json` 属于过程中的早期试跑，不是最终版本的消融结果，不应混用。

## 数据指纹与后续工作

| 文件 | SHA-256 |
|---|---|
| catalog.jsonl | `da979b05a68af864cb0dcf9ee6a81c010c7e66a57978ad286c7a2e005fc69a67` |
| public_set.jsonl | `857259f7a438e6188ac63e18995b6ff4489bfcfc4a716a798b9a2aa0ee8f7579` |
| evaluator/local_evaluator.py | `79a5ea06f9a1b8c5036f30efa85dc1f36b8f6b06eb8feb8f545dfa767bc45564` |

5号下一步应固定当前接口并让1/3确认槽位更新语义，让2/4在此入口验收真实Dense/Qwen，比较“召回变化、画像权重、澄清时机”各项消融，优先查清MRR下降。不要在接口联调期间同时更改官方数据或evaluator。细节、运行命令和限制见[调试指南](INTEGRATED_PIPELINE_DEBUG.md)。

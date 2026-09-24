# 集成流程与4号指标结构对照

全部结果均使用冻结官方目录、200个public development sessions和未修改的
evaluator。以下是诊断实验，不是private-set结果。

| 配置 | Hit@10 | MRR | MTTC | Efficiency | Technical |
|---|---:|---:|---:|---:|---:|
| 4号原RankingAgent：固定2轮 + 原Top50 + locked | 0.985 | 0.884625 | 3.205 | 0.7795 | **0.913788** |
| 当前集成：自适应4A/4B + hybrid | 0.970 | 0.575508 | 2.255 | 0.8745 | 0.832552 |
| 当前集成：自适应4A/4B + locked | 0.970 | 0.602103 | 2.265 | 0.8735 | 0.840331 |
| 集成状态 + 固定2轮4A + 当前候选池 + hybrid | 0.950 | 0.792631 | 3.470 | 0.7530 | 0.863389 |
| 集成状态 + 固定2轮4A + 当前候选池 + locked | 0.950 | 0.851137 | 3.470 | 0.7530 | 0.880941 |
| 集成状态 + 固定2轮4A + 4号Top50结构 + locked | 0.970 | 0.849089 | 3.320 | 0.7680 | **0.893327** |
| **完整集成score_compat：state信息量4A + 4号Top50结构 + locked** | **0.985** | **0.879208** | **3.095** | **0.7905** | **0.914362** |
| **现采用：固定两次State-informed 4A + 动态4B + Top50 + locked** | **0.985** | **0.888375** | **3.205** | **0.7795** | **0.914913** |

## 为什么不一样

1. 4号流程前两轮无条件询问`other`，第三轮才推荐。当前集成通常更早推荐，MTTC更快，
   但用户披露的信息少，因此MRR下降。固定两轮后，locked MRR从0.602103升至0.851137。
2. 当前集成会对hard feature做逐词词法过滤，并拒绝无法证明预算的未知价格。长描述被截断、
   改写或目录措辞不一致时，目标会直接离开候选池；4号Top50是召回优先，不做同样的严格淘汰。
3. 当前集成使用动态route depth、加权fusion和variable candidate count；4号使用固定route
   schedule、证据补位和固定Top50。即使都用locked reranker，source ranks也不相同。
4. override经过1号和3号后会清除软偏好并把新描述提升为hard feature。对照中5个override
   会话因此丢失命中或从第1名掉到Top10之外。
5. 当前hybrid的软上下文重排在该公开集上低于locked。固定两轮时Technical分别为
   0.863389和0.880941，因此目前不应在追分配置中启用hybrid。

## 最终采用的改法

最终没有把1号和3号旁路掉，而是增加了可配置的`score_compat`：Intent的操作先更新
`StateSnapshotV2`，4A根据state内累计证据量判断是否询问，模块2从该state构造
`Requirements`并使用召回优先Top-50，4B采用locked排序并作最终决策。另修复了短回答、
预算回答、结构化override和只清除最近偏好等接口语义。

相对4号原结果，新配置Hit@10相同，MRR低0.005417，MTTC快0.11，Efficiency高0.011，
TechnicalScore高0.000574。逐会话对照中，200个会话的hit结果全部一致；共同命中的会话里
188个rank相同、3个更好、6个更差；23个更早命中、176个同turn、1个更晚。说明提升主要
来自state驱动的询问时机，而不是改evaluator、读标签或按sample ID写规则。

`adaptive`严格候选模式仍保留供消融和4B broad-pool演示使用，但不再是提交入口默认值。
由于公开集参与了开发，0.000574的小幅优势不能视为private set上的确定提升。

不应按sample ID、ground truth或public标签写特例。公开集用于开发，任何提升还需在private
sessions验证泛化。

本地诊断产物：`results/integrated-component4-structure-study.json`、
`results/integrated-score-compat-v4-public200.json`和
`results/submission-public200.json`。

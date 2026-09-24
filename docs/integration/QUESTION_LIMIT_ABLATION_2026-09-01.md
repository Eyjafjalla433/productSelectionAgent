# 4号固定提问轮数指标复现

复现命令：

```bash
python scripts/reproduce_question_limit_ablation.py
```

该命令使用官方冻结的50,000商品目录、200个public development sessions和未修改的
`local_evaluator.evaluate()`，运行4号的`RankingAgent`、override-aware collector、
locked reranker，以及Lite候选生成器。运行不需要GPU、PyTorch或模型API。

| 配置 | Hit@10 | MRR | MTTC | Efficiency | Technical |
|---|---:|---:|---:|---:|---:|
| 固定前两轮询问（limit=2） | 0.985 | 0.884625 | 3.205 | 0.7795 | 0.913788 |
| 固定第一轮询问（limit=1） | 0.965 | 0.769306 | 2.545 | 0.8455 | 0.882392 |
| limit=1 − limit=2 | -0.020 | -0.115319 | -0.660 | +0.066 | -0.031396 |

`limit=2`的新Lite结果与此前保存的Exact结果在整个JSON层面相同，包括全部200个
session的hit、first-hit turn、best rank和reciprocal rank。因此Dense补位没有改变这组
public sessions的最终结果；不能由此推断private sessions也相同。

这是一项4号旧`RankingAgent`流程的消融实验，不是当前
`User → 1 → 3 → 4A → 2 → 4B`集成Agent的指标。这里的“提问”是无条件在前一或两轮
询问`other`，不是4A/4B基于状态和候选质量作出的自适应澄清。若作为最终提交基线，
应在Devpost和视频中明确这一点。

结果文件：

- `ranking_pipeline/results/question-limit-2-lite.json`
- `ranking_pipeline/results/question-limit-1-lite.json`
- `ranking_pipeline/results/question-limit-ablation-lite.json`（配置、官方文件SHA-256及差值）

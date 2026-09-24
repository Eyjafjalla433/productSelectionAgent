# Amazon ESCI：A/B/C/D 一体化验收

本仓库现在提供一个统一命令：

```powershell
python -m submission_tools.abcd_evaluate --product-sessions 12
```

它使用本地 50,000 条 Amazon ESCI 商品目录和 200 个公开会话，默认不调用模型、不联网、不消耗 Token。

## 四层分别验什么

- **A — 数据与检索**：原封不动调用官方 evaluator，报告 Hit@10、MRR、MTTC、TechnicalScore、运行错误和目录规模。
- **B — 商品判断与证据**：检查 Match Card、优点、注意事项是否具有来源和证据，并统计硬条件展示覆盖率。
- **C — Agent 与后端（重点）**：检查多轮顺序、状态推进、意图覆盖、trace 阶段、比较/确认控制、Token 记账和审计链。trace 通过 `(session_id, turn)` 获取；TTL/LRU 删除会话时，同时清理 memory、trace 和 session error，避免长期运行泄漏或串会话。
- **D — 顾客体验**：检查商品卡、按需详情、比较表、结构化 handoff 和显式最终确认是否能完整走通。

公开 ground truth 只在 evaluator/simulator 一侧用于构造测试和算分；传给 Agent 的仍然只有 user profile、用户消息、turn 和 top_k。运行时不会收到目标 ASIN。

## 当前实测

2026-09-18 在冻结公开集上的修复后完整运行结果：

| 层 | 结果 |
|---|---|
| A | 200 会话；Hit@10 0.985；MRR 0.888375；MTTC 3.205；TechnicalScore 0.914913 |
| B | 12/12 分层抽样会话产生商品；1,466 个展示证据点全部带来源；硬条件平均覆盖率 1.0 |
| C | 12/12 审计会话通过；契约错误 0；12/12 为 0 Token |
| D | 12/12 有商品卡、详情、比较和 finalized 结果 |

首次一体化运行发现：`Material:alloy` 一类带字段名前缀的条件在 A 中已正确过滤，但 B 的展示解释没有使用相同的规范化逻辑，造成一个会话的展示假阴性。现已让 B 与 A 共用同一种查询字段去前缀规则，并加入回归测试。最终数字以修复后重新生成的 [abcd-public.json](../results/abcd-public.json) 为准。

开发 B/C/D 时可跳过较慢的 200 会话官方评分：

```powershell
python -m submission_tools.abcd_evaluate --skip-official --product-sessions 4 --output .local/abcd-smoke.json
```

这只是快速回归；提交分数仍以不带 `--skip-official` 的完整运行和官方 evaluator 为准。

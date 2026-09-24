# Intent Router & Query Understanding 测试报告

**测试日期：** 2026-08-27  
**模块路径：** `intent_router/`  
**测试对象：** Intent Detection、Slot Extraction、Constraint Classification、Query Rewrite、Intent Override

## 1. 测试目标

验证 Router 能够：

- 在明确购买承诺与明确探索表达之间做意图区分；
- 对信息不完整的当前轮输出 `intent_type=null`，但仍输出 slots、constraints 和 query signals；
- 提取用户明确表达的商品 slot，而不推断未说出的属性；
- 区分用户硬约束、可安全 metadata filter 的约束和软偏好；
- 识别 Intent Override；
- 对齐官方 evaluator 运行时生成的公开 session 消息格式。

## 2. 测试命令

在 participant-kit 根目录执行：

```bash
python3 -m unittest tests.test_intent_router -v
python3 scripts/evaluate_intent_router.py
python3 -m compileall -q intent_router scripts tests
```

## 3. 单元测试结果

结果：**11 / 11 通过**。

| 覆盖项 | 验证内容 | 结果 |
| --- | --- | --- |
| 明确 Buying | `ready to buy` 触发 `buying` | 通过 |
| 明确 Browsing | `still exploring` 触发 `browsing` | 通过 |
| 不完整意图 | 预算/用途不强制分类 | 通过 |
| 预算解析 | 上限、区间、目标预算的 hard/soft 区分 | 通过 |
| Slot Extraction | category、brand、color、material、size、style、audience、use_case、feature | 通过 |
| 否定约束 | `not leather`、`no heels` 生成 `*_exclude` | 通过 |
| Filter 安全性 | 非结构化 material/color/size 不进入 metadata filter | 通过 |
| Query Rewrite | 生成 keyword 与 semantic query | 通过 |
| Override | `Actually, ignore ...` 生成 `override_detected=True` | 通过 |
| Evaluator 格式 | Buying/Browsing 初始消息兼容 | 通过 |
| 未结构化 requirement | `key requirement` 原文保留为 feature/style | 通过 |

### 代表性测试 Query 与预期输出

下表中的 query 来自 [`tests/test_intent_router.py`](../tests/test_intent_router.py)。为便于阅读，表中只展示关键字段，不展示完整 `IntentResult`。

| Query | 预期意图 | 关键验证点 |
| --- | --- | --- |
| `I'm ready to buy shoes under $90; they must be black and Nike only.` | `buying` | `budget_max=90`、black/Nike 为 hard；只有 price、brand、category 进入 `filter_constraints`。 |
| `I'm still exploring ideas for comfortable outfits for a summer wedding.` | `browsing` | `comfortable`、`summer`、`wedding` 为语义偏好。 |
| `I want something for hiking under $100.` | `null` | 不把预算和 `I want` 强制判为 Buying。 |
| `Show me casual dresses around $60.` | `null` | `budget_target=60` 是 soft，不错误地当作 price cap。 |
| `I need a women's size 8 casual dress under $75.` | `null` | 提取 audience、size、style、category 和 budget；size 为 hard 文本约束。 |
| `I need hiking shoes under $100, not leather and no heels.` | `null` | 生成 `material_exclude=[leather]` 与 `category_exclude=[shoes]`。 |
| `Actually, ignore my earlier preference. What I need is a wool winter jacket.` | `null` | `override_detected=True`；不凭 `I need` 擅自判为 Buying。 |
| `I'm looking for Shirts T-Shirts. A key requirement is: cotton.` | `null` | evaluator Buying 初始模板；保留 `cotton` hard constraint。 |
| `I'm looking for Earrings Hoop, but I'm still exploring.` | `browsing` | evaluator Browsing 初始模板。 |
| `I'm looking for earrings. A key requirement is: Snap closure.` | `null` | 词典外的 `Snap closure` 原文作为 hard `feature` 保留。 |

这些 query 覆盖的是当前规则实现的关键分支；完整断言以单元测试源码为准。

## 4. Public Session 模拟评测

官方 `public_set.jsonl` 不直接存储用户 query。评测脚本复用 `evaluator/local_evaluator.py`，从公开 target metadata materialize 官方运行时消息，再调用 Router。

| 指标 | 结果 | 样本数 |
| --- | ---: | ---: |
| Buying 初始公开硬约束 slot 覆盖率 | 1.0000 | 80 |
| Intent Override 检出率 | 1.0000 | 30 |

当前轮意图决策分布：

| 官方场景 | Buying | Browsing | 未定意图 |
| --- | ---: | ---: | ---: |
| Buying | 0 | 0 | 80 |
| Browsing | 0 | 80 | 0 |
| Boundary | 0 | 10 | 0 |
| Intent Override | 0 | 0 | 30 |

对于 110 条未定意图，Router 仍输出 slots、constraints 和 query signals，例如：

```python
{
    "intent_type": None,
    "hard_constraints": {...},
    "soft_preferences": {...},
    "keyword_query": "...",
    "semantic_query": "...",
}
```

因此第二部分四路召回仍可消费当前轮信号；Router 不会因为 `$100`、`I want` 或单个 slot 就错误地强制判为 Buying。

## 5. 结果解释

公开 Buying 模板为 `I'm looking for ... A key requirement is ...`，它表达了需求和约束，但没有表达 `ready to buy`、`buy now` 等购买承诺。因此 Router 保守地输出未定意图，同时保留公开的 hard constraint。此处不将官方 `scenario_type=buying` 当作当前句子的 intent label。

这符合多轮设计：①负责当前轮理解，③负责保存、合并、覆盖 slot；未定意图不能覆盖 state 中已有的确定意图。

## 6. 性能

完整 200-session Router 模拟评测约 **1.2 秒**，其中包含从 50,000 商品 catalog 构建品牌和类目词表。

实际集成时，`load_catalog_brands` 和 `load_catalog_categories` 应在 Agent 初始化时运行一次并缓存；每轮只调用 `router.understand(user_message)`。

## 7. 限制与后续验证

- 当前评测覆盖官方固定模板，而非带人工标签的开放自然语言购物对话；不能将上述覆盖率视为真实用户场景的泛化指标。
- Router 尚未接入 Hybrid Retrieval，因此尚无 Router 带来的 HitRate@10、MRR、MTTC 增益数据。
- material、color、size、feature 在 catalog 中主要存在于文本字段，不能假设为精确 metadata filter；应由 Retrieval 的文本/语义匹配处理。
- 与③、②联调后，应新增端到端实验：Router on/off、fallback 策略对比、hard filter 消融与多轮 override case。

# Intent Router 交接清单

**交付方：** ① Intent Understanding  
**交接对象：** ② Hybrid Retrieval、③ State & Memory、⑤ Integration  
**状态：** 模块代码、接口文档、单元测试与公开 session 模拟评测均已完成。

## 交付文件

| 用途 | 路径 |
| --- | --- |
| Router 实现 | `intent_router/router.py` |
| 输出 schema | `intent_router/models.py` |
| Catalog 词表加载 | `intent_router/catalog_lexicon.py` |
| 单元测试 | `tests/test_intent_router.py` |
| Public session 评测脚本 | `scripts/evaluate_intent_router.py` |
| 中文完整接口说明 | `docs/intent_router_zh.md` |
| 英文完整接口说明 | `docs/intent_router.md` |
| 测试报告 | `docs/intent_router_test_results.md` |

## 初始化与调用

初始化一次并缓存，不能在每轮重新读取 50k catalog：

```python
from intent_router import IntentRouter, load_catalog_brands, load_catalog_categories

self.router = IntentRouter(
    known_brands=load_catalog_brands("data/catalog.jsonl"),
    known_categories=load_catalog_categories("data/catalog.jsonl"),
)

# Agent.respond(...) 的每一轮
intent = self.router.understand(user_message)
```

输入只有当前 `user_message: str`。Router 无状态，不管理 session history。

## 必须消费的字段

### 交给② Hybrid Retrieval

| 字段 | 使用约定 |
| --- | --- |
| `filter_constraints` | 仅用于 metadata filter。当前仅允许 `budget_min`、`budget_max`、`brand`、`category`。`brand` 对应 catalog `store`。 |
| `hard_constraints` | 用户明确约束，但 material/color/size/feature 未必有结构化列；以文本或语义检索处理。 |
| `soft_preferences` | 用于语义召回、融合或 reranking。 |
| `keyword_query` / `semantic_query` | 分别进入关键词和语义检索路径。 |

**不要**把 material、color、size、feature 或 `*_exclude` 直接当作 catalog metadata filter；catalog 没有可靠统一字段。

### 交给③ State & Memory

| 字段 | 使用约定 |
| --- | --- |
| `slots` | 合并当前轮明确提及的属性。 |
| `hard_constraints` / `soft_preferences` | 分别保存显式强约束与偏好。 |
| `intent_type` | `buying`、`browsing` 或 `None`。`None` 不能覆盖已有确定意图。 |
| `override_detected` | 为 `True` 时删除或替换与最新消息冲突的旧 slot。 |
| `ambiguity_flags` | 供状态与澄清策略参考。 |

③ 不需要重做 Buying/Browsing 分类。② 的四路召回并行运行；Router 只提供用户理解结果，不指定召回路径。

## 意图规则

| 当前轮情况 | `intent_type` |
| --- | --- |
| 明确购买承诺，例如 `ready to buy` | `buying` |
| 明确探索，例如 `still exploring` | `browsing` |
| 仅增量信息，例如 `under $100`、`maybe white` | `None` |

`intent_type=None` 不等于无输出；它表示当前消息不足以单独标注 Buying/Browsing，但 Router 仍输出 slots、constraints 和 query signals。

## 官方 API 边界

`IntentResult` 是内部对象，不能直接作为竞赛回包。⑤ Integration 必须按官方 Agent contract 生成：

```python
{
    "message": str,
    "ask_attribute": "category" | "material" | "color" | "size" | "style"
                     | "brand" | "budget" | "feature" | "use_case" | "other" | None,
    "recommendations": [{"parent_asin": "..."}],
}
```

只允许有效 catalog `parent_asin`，并由最终 Agent 确保推荐前 10 个唯一有效 ID、10-turn 上限及官方响应格式。

## 验收命令

在 participant-kit 根目录执行：

```bash
python3 -m unittest tests.test_intent_router -v
python3 scripts/evaluate_intent_router.py
```

当前结果：11/11 单元测试通过；公开 Buying 初始硬约束 slot 覆盖率 1.0000；Intent Override 检出率 1.0000。

## 联调前提与未覆盖内容

- ②必须实现实际 Retrieval，才能测 HitRate@10、MRR、MTTC。
- ③必须接入 slot 累积与 override 写入策略。
- ⑤负责把内部 `IntentResult` 转换为官方 Agent response，并跑完整 local evaluator。
- 公开 session 的用户消息由 evaluator 运行时生成；Router 模拟评测验证的是官方模板对齐，不是开放自然语言泛化指标。

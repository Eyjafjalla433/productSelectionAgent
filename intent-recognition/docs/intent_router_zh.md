# Intent Router 与 Query Understanding 接口说明

## 模块边界

本模块只理解当前轮用户消息，不保存 session state，不做商品召回、召回路径选择、排序或澄清决策。它输出当前轮明确提及的 slot、约束强度和意图信号；③ State & Memory 负责跨轮合并与 override，② Retrieval 决定如何使用约束并并行执行四路召回，④ Ranking / Clarification 决定是否提问。

Router 不会为了信息完整补写用户没有表达的属性。例如 `for hiking` 不会推出 `waterproof`，`for dinner` 不会推出 `black`。

## 官方 Agent API 对齐

`IntentResult` 是团队内部模块之间的交接对象，不能直接作为官方 Agent 回包。最终 `Agent.respond(...)` 必须按官方 contract 返回 `message`、`ask_attribute` 和最多 10 个有效 `parent_asin` recommendations。

官方固定的是 `ask_attribute`，只允许：`category`、`material`、`color`、`size`、`style`、`brand`、`budget`、`feature`、`use_case`、`other` 或 `null`。内部 `slots` 可以保留额外的工程字段，例如 `budget_max`、`material_exclude` 和 `decision_evidence`，但 Integration 在提问时必须映射回这组固定枚举。

## 初始化与调用

```python
from intent_router import IntentRouter, load_catalog_brands, load_catalog_categories

router = IntentRouter(
    known_brands=load_catalog_brands("data/catalog.jsonl"),
    known_categories=load_catalog_categories("data/catalog.jsonl"),
)

intent = router.understand("I want something for hiking under $100.")
```

品牌来自 catalog 的结构化 `store` 字段，类目来自 `categories`。两种词表应在 Agent 启动时缓存一次，不应每轮读取 catalog。

## IntentResult

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `raw_query` / `normalized_query` | `str` | 原消息与规范化消息。 |
| `intent_type` | `"buying" \| "browsing" \| null` | 当前轮有足够强的购买承诺或探索行为时才赋值。 |
| `intent_confidence` | `float` | 当前意图决策的可靠程度。 |
| `slots` | `dict` | 用户当前轮明确说出的信息。 |
| `hard_constraints` | `dict` | 用户明确不可妥协的约束，未必是 metadata filter。 |
| `filter_constraints` | `dict` | 可映射到真实 catalog 结构字段的安全过滤条件。 |
| `soft_preferences` | `dict` | 用于语义召回或排序的偏好。 |
| `keyword_query` / `semantic_query` | `str` | 两条下游检索 query。 |
| `ambiguity_flags` | `list[str]` | `missing_category`、`low_intent_confidence`、`intent_undetermined` 等。 |
| `override_detected` | `bool` | 是否出现 “actually / ignore earlier / change my mind” 等改需求信号。 |
| `decision_evidence` | `dict` | Buying/Browsing 证据、分数、分差和最终决策，用于调试。 |

示例：

```python
{
    "intent_type": None,
    "intent_confidence": 0.52,
    "slots": {"use_case": ["hiking"], "budget_max": 100.0},
    "hard_constraints": {"budget_max": 100.0},
    "filter_constraints": {"budget_max": 100.0},
    "soft_preferences": {"use_case": ["hiking"]},
    "ambiguity_flags": ["missing_category", "low_intent_confidence", "intent_undetermined"],
}
```

## 意图判断策略

这不是“关键词二分类”。预算、品类、slot 数量、`I want` 都只是弱 signal，不能单独将当前轮强制判为 Buying。

| 当前轮信号 | 输出 |
| --- | --- |
| `ready to buy`、`buy now`、`place an order` | `buying`。 |
| `still exploring`、灵感、开放式穿搭问题 | `browsing`。 |
| `under $100`、`maybe white`、`actually for hiking` | 通常 `intent_type=null`。 |

未定意图不是失败，而是多轮需求尚未完整的正常状态。Router 仍会输出新增 slot 和明确约束。③ 应合并这些增量 slot，并在 `override_detected=True` 时处理旧值的失效或替换；② 四路召回并行运行，不需要 Router 指定某一路。

## Slot、Hard 与 Filter 的区别

| 信息 | 例子 | 处理方式 |
| --- | --- | --- |
| 价格上限 | `under $100` | 明确 hard；catalog 有 `price` 字段，进入 `filter_constraints`。 |
| 精确品牌 | `Nike only` | 明确 hard；catalog 有 `store` 字段，进入 `filter_constraints`。 |
| 明确品类 | `ready to buy shoes` | Buying 时可成为 hard；catalog 有 `categories`，进入 `filter_constraints`。 |
| 尺寸 | `size 8` | hard，但 catalog 没有统一结构化 size 字段；只能作为文本/语义约束。 |
| 材质或颜色 | `must be cotton`、`must be black` | 明确 hard，但 catalog 没有独立 material/color 字段；不得假设可以精确 metadata filter。 |
| 排除条件 | `not leather`、`no heels` | hard 文本约束；不能因 catalog 缺字段而丢失。 |
| 偏好 | `comfortable`、`stylish`、`for travelling`、`maybe white` | soft preference，交给语义召回和排序。 |

当前支持 category、brand、color、material、budget、size、style、audience、use_case、feature 及 `*_exclude`。`A key requirement is: ...` 中无法归到预设词典的真实 metadata 会原样保留为 `feature` 或 `style`，避免丢失文本约束。

## 下游交接

### Hybrid Retrieval

仅使用 `filter_constraints` 做 metadata filter：当前安全字段是 `budget_min`、`budget_max`、`brand`、`category`。material、color、size、feature 只能进入 `keyword_query` 或 `semantic_query`，除非 Retrieval 同学证明存在可靠字段解析层。

### State & Memory

合并 `slots`、`hard_constraints` 与 `soft_preferences`。当 `intent_type=null` 时，不应把它当作新的 intent 覆盖历史；Router 仍会保留当前轮提取到的理解信号。`override_detected=True` 时，按最新显式值替换旧 slot。

### Ranking / Clarification

结合 `intent_confidence`、`ambiguity_flags`、候选池规模和对话轮数决定是否询问。Router 不直接输出官方 `ask_attribute`。

## 测试

```bash
python3 -m unittest tests.test_intent_router -v
python3 scripts/evaluate_intent_router.py
```

公开集没有静态用户 query。第二条命令使用官方 evaluator 的固定规则将 public sessions materialize 成消息，以验证 Router 与模拟器的对齐；它不等价于开放自然语言泛化评测。

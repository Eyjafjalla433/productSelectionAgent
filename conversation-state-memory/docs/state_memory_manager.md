# 状态与记忆管理器

## 目标

本模块将多轮购物对话转换为紧凑、可执行的检索上下文。它刻意与商品检索和
排序实现解耦：模块负责判断“用户当前想要什么”，下游模块负责判断“哪些商品
满足这些要求”。

模块覆盖赛题要求的动态状态机、信息逐轮累积、意图覆盖、槽位优先级与衰减、
短期和长期记忆、上下文蒸馏，以及自适应编排。

## 模块边界

输入：

- 当前用户消息；
- 前几轮保留的会话状态；
- 可选的检索反馈，例如候选数量和已展示的 ASIN；
- 可选的长期用户画像。

输出：包含有效硬约束、带权软偏好、排除条件、检索路由和下一步动作的
`ContextSnapshot`。

```text
当前消息 + 会话/画像 + 检索反馈
  -> 提取意图与槽位变更
  -> 判断覆盖、合并、删除与衰减
  -> 蒸馏画像和会话摘要
  -> 选择路由与下一步动作
  -> 输出供检索和排序层使用的 ContextSnapshot
```

## 状态模型

`SessionState` 是短期记忆，包含意图、当前硬槽位与软槽位、显式排除项、已展示
商品、紧凑摘要和决策阶段。`UserProfile` 只保存稳定且有置信度的偏好；它绝不能
覆盖当前会话中用户明确提出的要求。

槽位分类如下：

| 类型 | 示例 | 检索用途 |
|---|---|---|
| 硬约束 | category、gender、size、color、价格区间、occasion、material | 过滤 / 必须匹配 |
| 软偏好 | style、fit、comfort、season、pattern | 排序加分 / 倾向匹配 |
| 显式排除 | `no heels`、`not black` | 禁止匹配 |

优先级为：当前轮明确硬约束 > 当前轮明确软偏好 > 最近硬约束 > 会话推断 > 用户画像
> 较早的软偏好。软槽位衰减速度快于硬槽位。显式排除项在整个会话内持续有效，避免
重复推荐用户已经拒绝的商品。

## 状态迁移

```text
DISCOVERY -> BROWSING | CONSTRAINT_GATHERING
BROWSING -> CONSTRAINT_GATHERING | RETRIEVAL
CONSTRAINT_GATHERING -> RETRIEVAL | CLARIFY
RETRIEVAL -> COMPARISON | CONVERSION | CONSTRAINT_GATHERING
COMPARISON -> CONVERSION | CONSTRAINT_GATHERING
```

对于显式替换，例如 `not black, blue instead`，系统会覆盖旧槽位，将 `black`
保留为排除项，并激活 `blue`。对于类目覆盖，例如
`actually, show running shoes`，系统会清理与新类目不兼容的槽位，但保留预算、颜色
和使用场景等跨类目属性。

## 上下文编程契约

`ContextSnapshot` 是下游模块所需的唯一结构：

```python
ContextSnapshot(
    intent="buying",
    route=Route.BUYING_FILTER,
    action=NextAction.RETRIEVE_BUYING,
    must_match={"category": "dress", "color": "blue", "price_max": 80.0},
    should_match={"style": {"minimal": 0.7}},
    must_not_match={"color": ["black"]},
)
```

- 过滤式检索使用 `must_match` 和 `must_not_match`。
- 稠密检索使用原始 query、有效软偏好和用户画像提示。
- 重排序将违反硬约束的商品直接淘汰，将软偏好视为带权加分。
- 发送给 LLM 的仅需会话摘要和有效结构化上下文，无需完整原始历史。

## 自适应编排

管理器会输出以下动作之一：`retrieve_buying`、`retrieve_browsing`、
`ask_clarification`、`reroute`、`compare` 或 `convert`。

当用户请求过于宽泛，或最新候选池超过配置阈值时，系统会提问以收敛需求。问题按
信息价值依次补充 category、gender、occasion、size、budget、color，从而降低用户
认知负担并改善 MTTC。当候选为空时，系统保留硬约束，并通知检索层先放松软偏好。

## 公共 API

```python
from state_memory import StateMemoryManager

manager = StateMemoryManager()
snapshot = manager.update(
    session_id="demo-1",
    user_id="user-1",
    utterance="Actually, not black; show me a blue work dress under $80",
    retrieval_feedback={"candidate_count": 120},
)
```

`snapshot.debug` 会记录槽位新增、更新、删除、排除和动作选择原因，可用于评测
分析和演示。

## 测试覆盖

回归测试覆盖：约束逐轮累积、槽位替换、类目与意图覆盖、预算改写、偏好衰减、画像
优先级、候选过载澄清、零结果恢复、显式排除保留、Browsing 到 Buying 路由和转化识别。

运行命令：

```powershell
& ..\.tools\python312\python.exe -m unittest discover -s tests -v
```

## 设计限制

初始抽取器采用确定性英文规则，因为比赛对话预期是预清洗文本。它不依赖付费 API，
可作为稳定基线。后续可替换为本地模型或 LLM 抽取器，而无需改变状态机、
`ContextSnapshot` 或下游契约。

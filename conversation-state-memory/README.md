# 多轮对话状态、记忆与动态上下文

这是 Shopping Copilot 项目的第 3 部分，负责维护用户在多轮购物对话中的
当前需求与长期偏好。本组件不负责商品目录检索；它向检索、排序和 Agent
编排层提供可执行的结构化上下文。

## 负责内容

- 用于信息逐轮累积和需求覆盖的动态状态机。
- 硬约束、软偏好、显式排除项、槽位优先级及衰减。
- 短期会话状态与长期用户画像蒸馏。
- 紧凑的对话摘要及可执行的 `ContextSnapshot` 输出。
- 自适应下一步决策：检索、重路由、澄清、比较或转化。

## 目录结构

```text
src/state_memory/     组件实现
tests/                多轮对话回归测试
docs/                 API 与设计契约
```

## 运行测试

在当前目录执行：

```powershell
& ..\.tools\python312\python.exe -m unittest discover -s tests -v
```

官方 Agent 通过 `StateMemoryManager.update(...)` 调用本组件，并取得供检索与
编排层使用的 `ContextSnapshot`。详细字段和行为请阅读
[模块设计与 API 契约](docs/state_memory_manager.md)。

## 文件与职责说明

该目录是独立的“对话状态与记忆”组件：它**不执行商品检索或排序**，而是把每轮用户消息转换成下游可使用的 `ContextSnapshot`。检索器可据此过滤商品，排序器可据此理解偏好和排除条件。

```text
用户消息 + 当前 SessionState + 可选检索反馈
  -> RuleBasedExtractor（意图与槽位）
  -> DynamicStateMachine（累积、覆盖、删除）
  -> ProfileDistiller（长期偏好）
  -> ContextProgrammer（路由、澄清或检索动作）
  -> ContextSnapshot
```

| 路径 | 作用 |
|---|---|
| `src/state_memory/__init__.py` | 组件的公开入口，导出 `StateMemoryManager`、`ContextSnapshot`、路由和动作枚举。 |
| `src/state_memory/models.py` | 定义所有数据契约：短期会话状态 `SessionState`、槽位 `Slot`、长期画像 `UserProfile`、状态变更 `StateDelta` 与下游输出 `ContextSnapshot`。 |
| `src/state_memory/manager.py` | 对外主入口。维护按 session/user 隔离的内存状态，协调抽取、状态机、画像蒸馏和上下文编程。调用方通常只需使用 `update()` 与 `apply_retrieval_feedback()`。 |
| `src/state_memory/extractor.py` | 确定性英文规则抽取器。识别 Buying/Browsing/Compare 意图，以及类别、颜色、预算、材质、尺码、场景、风格、服饰属性、评分和显式排除。 |
| `src/state_memory/catalog_lexicon.py` | 从本地比赛 catalog 构建并缓存完整类别、`store` 品牌、功能属性，以及 `details` 中与购买决策相关的属性和值；避免把未在商品数据中出现的功能强行写入状态。 |
| `src/state_memory/state_machine.py` | 多轮状态更新规则。负责新增槽位、更新同名条件、处理 `not / without / ignore` 等撤销、类别切换时的兼容性清理，以及会话摘要。 |
| `src/state_memory/profile.py` | 从多轮观测中蒸馏稳定偏好；当前轮硬约束始终优先于长期画像。 |
| `src/state_memory/context_program.py` | 将状态转成 `ContextSnapshot`，选择 Buying filter 或 Browsing dense 路由，并限制主动澄清次数，防止对话接近 10 轮上限。 |
| `tests/test_state_memory.py` | 组件回归测试：累计、覆盖、撤销、澄清上限、catalog-aware 属性以及路由行为。 |
| `docs/state_memory_manager.md` | 详细 API 契约和状态机设计说明。 |
| `pyproject.toml` | Python 包元数据和构建配置。 |

## 主要数据对象

- `SessionState`：一个 session 的短期记忆。保存当前意图、硬约束、软偏好、明确排除项、已展示商品、候选数量和澄清计数。
- `UserProfile`：一个 user 的长期稳定偏好。它只能补充当前需求，不能覆盖用户本轮明确表达的条件。
- `ContextSnapshot`：该组件唯一的下游输出。`must_match` 是硬过滤条件，`should_match` 是带衰减权重的软偏好，`must_not_match` 是排除条件，`action` 说明下一步该检索、澄清、比较还是转化。

## Catalog-aware 提取

当仓库存在 `official_kit/data/catalog.jsonl` 时，`StateMemoryManager()` 会自动加载并缓存比赛提供的 50,000 条商品数据；也可以显式传入路径：

```python
from state_memory import StateMemoryManager

manager = StateMemoryManager("path/to/catalog.jsonl")
snapshot = manager.update(
    session_id="demo-1",
    user_id="user-1",
    utterance="I need Columbia waterproof shoes rated 4 stars",
)
```

首次加载后，词表会写入 `conversation-state-memory/.cache/catalog_lexicon.json`。缓存记录 catalog 文件名、文件大小和修改时间；任一项变化时会自动重新读取 catalog 并原子替换缓存文件。该缓存已纳入版本控制，供克隆仓库后的运行直接复用；若部署目录不可写，组件会自动回退为仅进程内缓存。

上例会产生类似的硬条件：

```python
{
    "category": "shoes",
    "brand": "Columbia",
    "feature_waterproof": True,
    "rating_min": 4.0,
}
```

当前支持的 catalog 相关属性包括完整类别、品牌（`store`）、已在 catalog 中出现的常见功能属性、评分与评论数。组件还会从每条商品的 `details` 字段动态收集材质、颜色、闭合方式、领型、袖型、版型、图案、场景／运动、季节、护理方式、鞋底／内外材质、鞋宽等结构化值；服饰规则另支持 `fit`、`sleeve` 和 `pattern`。用户说“any color is fine”或“ignore my earlier budget”时，状态机会删除对应的旧条件；“blue instead”会覆盖颜色，但不会把旧颜色误记为禁止项。

## 澄清与轮次保护

澄清只在类别缺失或候选池过大时触发。组件最多主动澄清两次；从第 8 轮开始强制输出检索动作，不再继续提问。`snapshot.debug` 会提供 `clarification_count`、`last_clarification_slot` 和 `forced_retrieval`，方便接入方监控 MTTC 与 10 轮上限风险。

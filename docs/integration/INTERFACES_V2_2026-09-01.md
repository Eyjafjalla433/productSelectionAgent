# 三处接口变更：只定义数据契约，不改变算法

本次基于 `4dbd5ec`，仅修改/新增数据模型、序列化、导出适配器、类型协议、测试和示例。
没有改变 Router 的解析、3 号状态合并/衰减/策略、检索/排序算法、Agent 调用顺序或官方 evaluator。
新版契约独立于旧流程，不能把本次改动理解为 1→3→4A→2→4B 已经运行。

## 1. 1→3：明确本轮需求变更

文件：`intent-recognition/intent_router/models.py`。

新增 `SlotUpdate(slot, operation, values, constraint_type, confidence, evidence)`，
以及 `IntentResult.slot_updates` 和 `to_state_handoff(session_id=..., turn=...)`。
3 号接收方声明为 `state_memory.contracts.IntentStateUpdater.update_from_intent(handoff)`。
这是 Protocol 接口声明，现有 StateMemoryManager 尚未实现它。

| 操作 | 表达含义 | 参数约束 |
|---|---|---|
| set | 设置/替换指定 hard 或 soft 层的值 | 必须有 values 和 constraint_type |
| clear | 取消该属性两层限制及排除项 | values 为空，constraint_type 为空 |
| exclude | 增加全局排除值 | 必须有 values，constraint_type 为空 |
| remove_exclusion | 允许此前排除的值，不代表必须购买该值 | 必须有 values，constraint_type 为空 |

操作按列表顺序表达；set 本身不撤销排除，跨层迁移或撤销排除必须明确表达。
例如需要彻底替换该属性并撤销旧限制，可以先 clear，再 set。
这些是未来消费者必须实现的语义，本次没有实现状态更新算法。

新版操作的预算名统一为 `price_min/price_max`，接收 `budget_min/budget_max` 别名。
只映射字段名，不改变金额单位，也不自动推断币种。旧 IntentResult 字典不改名。

```python
from dataclasses import replace
from intent_router import IntentRouter, SlotUpdate

parsed = IntentRouter().understand("blue instead")
current = parsed.to_state_handoff(session_id="s", turn=3)
assert current["slot_updates"] is None  # 旧解析器尚未提供明确操作

# 接口构造示例；不是声称解析器自动生成了这个操作。
explicit = replace(parsed, slot_updates=(
    SlotUpdate("color", "set", ("blue",), "hard", evidence="blue instead"),
))
payload = explicit.to_state_handoff(session_id="s", turn=3)
```

新 envelope 包含 schema_version、session_id、turn、intent、slot_updates、legacy_result。
`legacy_result` 保留全部旧解析字段（包括置信度、约束和查询），不进行有损迁移。
顶层 intent 将旧 None 映射为 unknown，不把它自动变成 buying/browsing。

**未知与无变化不同：** slot_updates=None 表示没有提供可执行变更；空列表表示明确无变更。
消费者不得将 None 默认为空列表，也不得仅凭 override_detected 批量删除旧属性。

兼容：旧 `IntentResult` 构造调用和 `to_dict()` 输出形状不变；to_dict 是旧序列化器，
不会输出新增操作。需要新字段时必须调用 to_state_handoff。

## 2. 3→下游：完整、独立的状态快照

文件：`conversation-state-memory/src/state_memory/contracts.py`。

新增 `StateSnapshotV2.from_legacy(snapshot, session=..., state_version=...)`。
调用方传入同一次状态更新对应的旧快照和 SessionState，不再解析用户文本。

| 新字段 | 来源及含义 |
|---|---|
| session_id / turn / state_version | 明确会话和需求版本；版本由编排层提供，不擅自等于轮次 |
| intent / intent_confidence | 当前累计意图，保留 unknown/compare 等已有状态 |
| hard_constraints | 原 must_match，预算仍为数值硬约束 |
| soft_preferences | 原 should_match 转为每个属性的 value/weight 列表，保留权重，不再次衰减 |
| exclusions | 原 must_not_match，完整保留排除值 |
| slot_metadata | hard/soft 各属性的值、source_turn、confidence、priority、evidence |
| profile_hints / session_summary / query | 保留现有画像提示、摘要和当前查询 |
| shown_asins | 已展示的商品 ID |
| asked_questions / pending_question | 编排层提供的实际提问信息，不从策略建议推断 |
| suggestions | 旧 route/action/clarification_question/retrieval_budget，只是建议 |

```python
from state_memory import StateSnapshotV2

exported = StateSnapshotV2.from_legacy(
    old_snapshot, session=manager.sessions["s"], state_version=7,
)
payload = exported.to_dict()
```

导出不会增加 turn、澄清计数或执行任何动作。会检查旧快照与会话的需求/意图是否一致。
输入嵌套容器与序列化输出均复制：下游修改它们不会影响 manager 的原始状态。
软偏好例如 `{"size": [{"value": 42, "weight": 0.75}]}`，数值放在 value 中，
避免以字典键保存时被 JSON 悄悄转为字符串。
它不是递归不可变对象；需要不可变存储的调用方应自行管理自己的副本。
默认提问历史 None 表示未知，空元组表示调用方明确确认历史为空。
尚未自动推断缺失属性，也没有将官方 user_profile 接入状态算法。

## 3. 2→4：变长候选和排序结果

文件：`retrieval-and-reranking/techjam_agent/contracts_v2.py`。

- `RetrievalResultV2`：候选集 ID、会话、轮次、状态版本、候选上限、候选、统计。
- `RankingResultV2`：对应候选集及状态版本、排序商品、排序方法、分数语义。
- `RetrievalStats`：去重后各路匹配集合大小 matched_count、硬过滤后大小 filtered_count。
- `VariableCandidateReranker`：后续支持变长候选的排序器协议，旧排序器暂不实现。

两项统计都是 Top-N 截断之前的数量；不知道就为 None。returned_count 单独由实际列表计算。
候选允许 0..candidate_limit 件；排序允许 0..min(top_k, returned_count) 件，不自动补齐。
校验唯一 ID、连续排名、上限、有限分数、子集关系及会话/轮次/状态版本对应关系。
分数默认标记 uncalibrated，不能直接当成概率。

沿用旧 Candidate 的商品快照和召回来源字段，没有凭空添加价格列或声称硬过滤已经实现。
完整 StateSnapshotV2.to_dict() 可以放入 state_snapshot 一起传给 4，预算和排除不压平成字符串。
legacy_requirements 另存旧请求；携带完整状态不等于旧检索算法已消费它。

```python
from techjam_agent.contracts_v2 import RetrievalResultV2, RankingResultV2

v2_candidates = RetrievalResultV2.from_legacy(
    old_candidates, state_version=7,
    state_snapshot=exported.to_dict(),  # 必须同会话、轮次和版本
)
v2_ranking = RankingResultV2.from_legacy(
    old_ranking, retrieval=v2_candidates, top_k=10, ranking_method="locked",
)
v2_ranking.validate_against(v2_candidates, top_k=10)
```

适配器保留旧候选和排序顺序。它不会编造过滤统计，且返回 warnings 说明旧结果的结构化约束执行未经验证。
**旧 contracts.py 完全保留：** 原 CandidateSet 仍要求50件，原排序结果仍要求恰好 top_k 件。
没有提供把变长结果偷偷补成50件再交给旧算法的反向适配器。

## 查看和验证

完整可查看示例：`interface-examples-v2.json`。
示例中明确的操作、状态和 EXAMPLE_ONLY 商品为契约演示数据，不写入正式 catalog，不是端到端运行结果。

仓库根目录使用 Python >=3.10：

```bash
python -B docs/integration/show_interface_examples.py
```

新增测试分别为 `test_handoff_contract.py`、`test_state_contract.py`、`test_contracts_v2.py`。
在对应模块根目录执行 `python -B -m unittest discover -s tests -v`。
Ranking 旧测试从仓库根目录执行 `python -B -m unittest discover -s ranking_pipeline/tests -t . -v`。

本次使用 Python 3.12.13 验证 **90/90 通过**：Router 17、State 26、Retrieval 13、Ranking 34。
其中新增契约测试20项，原有测试70项；日志位于 `.local/interfaces-v2/`。
已确认原算法文件、Agent 主流程、旧 candidates 契约和官方 evaluator 没有改动，
正式商品 catalog 的 SHA-256 与安装时一致。没有执行模型推理或新的 public-200 评测。

下一阶段再实现：Router 生成明确操作、StateMemoryManager 消费增量、5 号管理版本/实际提问反馈、
检索和排序消费完整状态及变长候选。4A/4B 策略与完整主链本次不变。

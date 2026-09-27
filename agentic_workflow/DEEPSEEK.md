# DeepSeek 主链路

在仓库根目录运行（本机环境需要 `DEEPSEEK_API_KEY`）：

```powershell
.\scripts\start_deepseek.ps1 -Python E:\Software\Miniconda3\python.exe
```

等价入口：`python -m agentic_workflow --model-provider deepseek`。
省略 `--model-provider` 仍为无云模型模式。本功能不依赖 `optional_bedrock/`。

开启 DeepSeek 后，普通需求更新采用模型优先：模型收到本轮文本、当前硬条件、
偏好、排除项、待回答问题和规则提示，返回本轮操作，而不是重新生成整个状态。
`set` 决定 hard / soft；`exclude` 加入 Avoid 并移除冲突的正向值；
`clear` 清除整个维度；`remove_value` 移除指定正向值；
`remove_exclusion` 取消指定排除。未涉及的维度保留，状态管理器继续维护 Undo。

模型结果整批通过操作类型、值类型、原文证据和当前状态值校验后，替代规则操作。
成功返回空数组表示不修改需求。网络错误或不合格结果回退到规则，receipt 的
`model_assist.replaces_rules` 表示本轮是否使用模型操作，`warning` 记录回退原因。
证据校验不等于语义正确性保证；当前值校验较保守，无法落地的同义词可能回退。

Undo/Redo、重置、收藏及已有确定性对话控制仍走原流程。此改动只启用 DeepSeek
的模型优先解析，其他可选 provider 保留补充模式。请求会把本轮需求和当前需求状态
发送到 DeepSeek；密钥只从后端环境读取。

真实 API 配合模拟目录已验证：

- `I want a cotton blue shirt` → 三项硬条件。
- `I don't want cotton anymore` → cotton 进入 Avoid，保留 blue / shirt。
- `Any material is fine` → 清除材质条件和排除项。
- `Blue is only a preference now` → blue 从硬条件移到偏好。

`audience` 支持 men / women / boys / girls / kids / baby / unisex。
明确要求男款会记录为硬条件，并在切换品类时保留；不会从 dress、XL 等推断性别。
过滤依据商品部门、标题或分类，未知对象不满足男款硬条件；成人 unisex 可满足。
同时堆砌男款和女款标签、但未明确标为 unisex 的矛盾商品也会排除。

重新检索不等于清空需求：普通需求更新、更多选项、再次搜索通常触发检索，
重复条件也可能重新检索。查看详情、需求回顾、收藏等控制操作以及部分闲聊不检索。
明确的 `start over` / `clear my search requirements` 才清空需求；换品类清掉旧商品的
颜色、材质、尺码等局部条件，但保留 audience 和预算。Undo 可恢复之前的状态和结果。
`receipt.search_execution` 分别报告是否检索、是否重置需求和是否切换品类。

## 简短回复与商品理由

搜索完成后，一次额外的 DeepSeek 请求为当前最多 10 个商品生成个性化理由，
并生成 1–2 句对话回复（校验上限 240 字符）。不固定使用 Got it，不重复报告商品数量。
上下文包含本轮消息、最近三轮对话、会话需求、意图摘要、收藏/隐藏 ID、当前候选、
确定性的匹配检查和相关商品摘录；不推测额外的人口属性或跨会话画像。

Evidence 先按需求相关度提取最多六段短原文，再由模型为每个商品选择最多两段。
服务端按商品 ID 和引用 ID 验证，前端显示原文而非模型生成的引文。模型生成的推荐理由
仍可能存在语义误差；原有支持/冲突/未知检查及风险提示不会被它改写。
未提及某材质不等于确认不含该材质。接口失败或输出不合格时保留原回复与确定性理由，
仍使用筛选后的短证据。`receipt.response_assist` 记录状态和用量，总用量包含此额外请求。

# search_tool 接入

默认入口 `Agent()` 和网页运行时都使用真实 `search_tool`；只有显式提供模拟目录或使用 `--demo` 才使用模拟商品。

适配位置：`shopping_agent/search_adapter.py`。调用约定保持为：

```python
results = search_products(query, top_k)
details = get_product_details([row['product_id'] for row in results])
```

工作流把已识别的需求整理为英文检索词，调用工具取得候选，再应用硬条件、排除项和已展示商品过滤。保留工具返回的相对顺序及原始分数，不将分数解释为概率。候选召回数量与最终展示数量分开，网页最多展示 10 款。

工具只返回 ID 和分数，因此另外调用它的详情接口补充标题、品牌、颜色和卖点。缺失的价格、评分和库存不会补造；缺失价格的商品无法证明满足预算约束。

## 不修改工具目录

- 不复制、移动或重写 `search_tool` 中的代码、权重和索引。
- 导入时关闭字节码写入，避免在工具目录生成 `__pycache__`。
- `search_resources.py` 识别 `model/` 或 `models/task_1_us_title_bullet/`，只在当前进程中设置工具的模型路径。
- 本机 CUDA 配置中 `expandable_segments=True` 的语法问题只在当前进程修正，不修改系统设置。
- `live_smoke` 比较运行前后的全部文件路径与 SHA-256；有新增、删除或内容变化即验证失败。

## 从仓库根目录运行

```powershell
python -B -m pip install -r agentic_workflow/requirements-search.txt
python -B -m agentic_workflow.preflight
python -B -m agentic_workflow.live_smoke
python -B -m agentic_workflow.verify
python -B -m agentic_workflow --port 8000
```

网页地址：http://127.0.0.1:8000 。首次搜索会加载本地模型及索引，后续在同一进程复用。`live_smoke` 使用真实模型验证搜索、详情、比较、确认与审计；`verify` 是无需模型资源的隔离回归测试。

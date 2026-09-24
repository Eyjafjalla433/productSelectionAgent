# Show Me Your Agent：本地演示指南

这套演示使用 `mvp/demo_data/catalog.jsonl` 中的 15 条虚构商品，不需要下载
50K 比赛数据，不需要安装第三方依赖，也不需要任何模型 Key。它调用的仍是
正式 MVP 的需求解析、状态、检索、排序、优劣点、选择和审计代码。

## 1. 一条命令看完整中文案例

在仓库根目录运行：

```powershell
python -m mvp.demo --case dress_zh
```

脚本会依次演示：

1. 用户需要 50 美元以内的蓝色棉质连衣裙；
2. 用户补充不要涤纶、适合夏天；
3. Agent 展示商品优点、注意事项和证据；
4. 用户比较第一、第二件商品；
5. 用户确认最终选择；
6. Terminal 输出给 B/D 的结构化 handoff，并验证审计链。

另外两个完整案例：

```powershell
python -m mvp.demo --case running_shoes
python -m mvp.demo --case commuter_bag
python -m mvp.demo --list-cases
```

## 2. 自由 Terminal 对话

```powershell
python -m mvp.demo
```

直接输入自然语言即可，例如：

```text
我想要一条蓝色棉质连衣裙，价格不超过50美元。
不要涤纶。
```

也可以使用演示命令：

```text
/cases
/run dress_zh
/select 1 2
/compare 1 2
/reject 2
/finalize
/handoff
/audit
/new
/quit
```

`/select`、`/compare`、`/reject` 和 `/finalize` 会进入与网页相同的会话控制
和审计流程，不是 Terminal 自己伪造结果。

## 3. Web 对话界面

```powershell
python -m mvp.demo --web --port 8000
```

然后打开：

```text
http://127.0.0.1:8000
```

Web 版可以自由聊天、查看需求状态、Top 商品、匹配证据、优缺点、候选清单、
后端数据驱动的横向对比表、最终确认和审计导出。对比表只显示价格、评分、
硬条件覆盖率和证据数量，不会在前端重新排序。展开 `CATALOG DETAILS` 会
按需读取当前会话已展示商品的 description、bullet points 和 details，不会
重新检索或调用模型。按 `Ctrl+C` 停止服务。

## 4. 模型选项与数据边界

默认 `off` 模式完全本地，Token 使用为 0。假商品已经包含足够的描述和
bullet points，因此不启用模型也能展示有证据的优缺点。

如果本机已有 OpenAI-compatible 模型服务，可以显式启用：

```powershell
python -m mvp.demo --web --model-provider local --model qwen2.5:7b
```

只有明确需要测试 DeepSeek 时才设置：

```powershell
$env:DEEPSEEK_API_KEY = "your-key"
python -m mvp.demo --web --model-provider deepseek
```

DeepSeek 模式会把用户消息发送到云端；比较、确认或导出时，还可能发送已选
商品的有限描述片段，并产生云端 Token 费用。Key 不会写入仓库、响应或审计。

## 5. 自动验证

```powershell
python -m unittest discover -s mvp/tests -q
```

测试会对三个脚本案例逐一确认：至少两个候选、最多 Top 10、存在有证据的
优点和注意事项、最终状态为 `finalized`、handoff 有商品、审计链有效且默认
Token 为 0。

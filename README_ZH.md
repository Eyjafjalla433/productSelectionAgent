# Shopping Copilot：多轮对话式商品搜索

> 一个可离线运行的购物 Agent：它会记住有效需求、清除已经过期的条件，并在“先问清楚”与“直接推荐”之间做决策。

[English README](README.md) · [技术报告](docs/submission/TECHNICAL_REPORT.md) · [Devpost 文案](docs/submission/DEVPOST_ABOUT_PROJECT.md)

## 项目解决什么问题

用户的购物需求会在对话中不断变化。例如，用户可能先说“黑色、50 美元以内的裙子”，随后改成蓝色、取消预算，最后又改为找鞋子。普通搜索会丢失前文；对话系统如果保留所有旧条件，又会受到过期条件干扰。

Shopping Copilot 将这件事拆成可观察的链条：意图理解、会话状态、检索前决策（4A）、多路召回、重排与检索后决策（4B）、反馈回写。

```text
用户 → 意图理解 → 状态/上下文 → 4A
                              ├─ 信息不足：澄清问题 → 状态更新
                              └─ 信息足够：Top-50 检索 → 重排/4B → 推荐或澄清
```

提交版会先进行两轮基于状态的证据收集；从第三轮起，根据候选池质量与用户反馈动态决定推荐还是继续询问。

## Public 200 开发集结果

| Hit@10 | MRR | MTTC | Efficiency | TechnicalScore |
|---:|---:|---:|---:|---:|
| **98.5% (197/200)** | **0.888375** | **3.205** | **0.779500** | **0.914913** |

结果来自官方未修改的评测器和 200 条公开开发会话，不代表 private 800 结果，也不等同于比赛总评分。

## 如何运行

提交入口是 `from agent import Agent`。运行只依赖 Python 标准库与 SQLite FTS5，不需要 GPU、PyTorch、模型权重、外部 API 或密钥。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m submission_tools.prepare_data
python -m submission_tools.evaluate --offline-check
```

`prepare_data` 会下载并校验官方 participant kit；仓库和提交 ZIP 不包含 50,000 商品目录、标签、模型权重或私有数据。

## 目录导航

| 目录 | 内容 |
|---|---|
| `intent-recognition/` | 1 号：意图路由与查询理解 |
| `conversation-state-memory/` | 3 号：多轮状态、覆盖与上下文 |
| `retrieval-and-reranking/` | 2 号：候选检索接口与路线 |
| `ranking_pipeline/` | 4 号：排名、策略与消融实验 |
| `shopping_agent/` | 5 号：全链路集成、4A/4B 与编排 |
| `submission_tools/` | 安装数据、评测、打包和录屏 demo |

更多架构、复现、局限与视频材料请见 [英文主页](README.md)。

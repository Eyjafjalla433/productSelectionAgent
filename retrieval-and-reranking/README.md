# TechJam Two-Stage Conversational Search Agent

可替换的两阶段商品搜索实现：先生成固定 Top50，再把这 50 个候选重排为官方 Top10。

> **关键实验假设：进入本模块时，用户需求已经全部获得。** 输入应当已经包含商品类别、全部已确认硬约束和全部软偏好。本模块不负责判断需求是否已经问完，也不能证明官方私有会话经过两次 `other` 后一定公开全部信息。当前结果衡量的是完整信息条件下的检索与排序能力。

```text
两次 other 后已公开的完整需求
              |
              v
Top50CandidateGenerator.generate(...)
              |
              v
CandidateSet（唯一的层间接口，恰好 50 个商品）
              |
              v
Top10Reranker.rerank(...)
              |
              v
官方 recommendations（10 个 parent_asin）
```

## 已验证结果

| 验证 | 结果 |
|---|---:|
| 完整需求离线 Top50 覆盖 | 199/200 |
| locked Top10 命中 | 198/200 |
| 三轮 buying/browsing 反事实 Agent 命中 | 198/200 |
| MRR | 0.913042 |
| MTTC | 3.08 |
| 单元测试 | 6/6 |

重要：三轮 Agent 结果来自把公开集投影为 100 buying + 100 browsing 的诊断，不是原始四场景官方分数。`boundary` 和 `intent_override` 不能保证两次 `other` 后得到同样的完整信息。

## 两个清晰接口

接口定义在 [`techjam_agent/contracts.py`](techjam_agent/contracts.py)。

### 1. Top50 候选生成

```python
class Top50CandidateGenerator(Protocol):
    def generate(
        self,
        requirements: Requirements,
        *,
        session_id: str,
        turn: int,
    ) -> CandidateSet:
        ...
```

输入只包含当前已经公开的：

- `category`
- `hard_constraints`
- `soft_preferences`

输出必须是恰好 50 个唯一商品的 `CandidateSet`。每个候选包含：

- `parent_asin`
- 当前候选排名
- 各召回路线中的原始排名 `source_ranks`
- catalog 中可公开的商品字段快照

### 2. Top10 重排

```python
class Top10Reranker(Protocol):
    def rerank(
        self,
        candidate_set: CandidateSet,
        *,
        top_k: int,
    ) -> RerankResult:
        ...
```

Top10 只能读取 `CandidateSet`，不能访问 catalog、重新召回商品或增加候选。返回结果必须是 CandidateSet 的唯一子集。

替换算法时，在 [`techjam_agent/agent.py`](techjam_agent/agent.py) 中注入新实现即可；官方对话接口不需要修改。

## Exact 与 Lite

| 模式 | Top50 | 第三方依赖 | 用途 |
|---|---|---|---|
| `exact`（默认） | 三路 Top30 + evidence 保留 + BGE Dense 补位 | NumPy、PyTorch、Transformers、Sentence Transformers | 正式复现与提交 |
| `lite` | 三路 Top30 + evidence + 词法兜底 | 无，纯 Python 标准库 | 快速调试、CI smoke test、降级 |

Lite 在公开 200 条上的目标计数同样是 Top50 199/200、Top10 198/200，但它的实际 Top10 列表只有 135/200 与 exact 完全一致，因此不能冒充 exact。

Exact 与历史结果比较：Top50 候选集合 200/200 一致；Top50 顺序 199/200 一致。唯一差异是一个样本的第 43/44 位因约 `1.19e-7` 的浮点差发生互换。最终 Top10 序列 200/200 一致。

## 最快安装：Exact 模式

已测试环境：Windows、Python 3.12.13、CPU。

Exact 模式需要以下固定版本：

| 依赖 | 版本 | 用途 |
|---|---:|---|
| Python | `>=3.10`，已验证 `3.12.13` | 运行 Agent 与 evaluator |
| NumPy | `2.5.2` | 读取向量并计算 Dense 相似度 |
| PyTorch CPU | `2.13.0` | 本地 BGE 编码 |
| Transformers | `5.16.1` | 本地模型加载 |
| Sentence Transformers | `6.0.0` | query embedding |

除 Python 包外，Exact 模式还需要：

- 官方 50,000 商品 `data/catalog.jsonl`；
- `BAAI/bge-small-en-v1.5` 本地模型；
- 与冻结 catalog 对齐的 embeddings 与 parent-ASIN 顺序文件。

以上所有非 Python 运行资源统一打包在团队仓库 GitHub Release 的 `techjam-runtime-assets-v0.1.0.zip` 中，不需要分别访问 Amazon 或 Hugging Face。资源包包含：

- 完整的 `data/catalog.jsonl`；
- `BAAI/bge-small-en-v1.5` 的权重、tokenizer、config、pooling 配置和模型说明；
- 冻结 catalog 的 embeddings、parent-ASIN 顺序文件及 Dense manifest。

这些大文件不进入普通 Git 历史。`asset-manifest.json` 记录 Release、ZIP 和关键文件 SHA256，`scripts/install_assets.py` 负责下载、校验和安装。因为团队仓库是 private，普通 URL 如果受到权限限制，安装器会自动调用已经登录的 GitHub CLI；也可以在 GitHub 页面下载 ZIP 后使用 `--archive`。Python 依赖版本固定在 `pyproject.toml` 的 `exact` optional dependency 与 `requirements-exact.txt` 中。

Windows 上建议把 venv 放在短路径。PyTorch 包含很深的许可证目录，长项目路径可能触发 `WinError 206`。

```powershell
Set-Location <仓库目录>\retrieval-and-reranking
py -3.12 -m venv C:\venvs\techjam-agent
C:\venvs\techjam-agent\Scripts\python.exe -m pip install -e ".[exact]"
C:\venvs\techjam-agent\Scripts\python.exe -m scripts.install_assets
$env:TECHJAM_MODE = "exact"
C:\venvs\techjam-agent\Scripts\python.exe -m unittest discover -s tests -v
C:\venvs\techjam-agent\Scripts\python.exe -m scripts.validate_pipeline --mode exact
```

不要手动解压，也不要把文件放到仓库根目录或 `intent-recognition/`。安装器始终以 `retrieval-and-reranking/` 为根目录，自动生成：

```text
retrieval-and-reranking/
├── data/
│   └── catalog.jsonl
└── resources/
    ├── bge-small-en-v1.5/
    └── dense_catalog_embeddings/
```

如果已经下载了资源 ZIP：

```powershell
C:\venvs\techjam-agent\Scripts\python.exe -m scripts.install_assets `
  --archive C:\Downloads\techjam-runtime-assets-v0.1.0.zip
```

`--archive` 只需要接收 ZIP 路径；仍由安装器校验并解压到上述固定目录，不需要手动移动文件。

资源 ZIP 为 159.76 MiB，SHA256：

```text
57769c08803b2a68604e12bbc59ec07beea65d831498aa3d361cb0e0a7004f2b
```

安装脚本会校验整个 ZIP，并再次校验 catalog、模型权重和向量文件。运行时完全离线，不需要 API key、外部服务或 LLM token。

## Lite 模式

Lite 不需要安装任何第三方包，但仍需要官方 `data/catalog.jsonl`：

```powershell
py -3.12 -m venv C:\venvs\techjam-agent-lite
C:\venvs\techjam-agent-lite\Scripts\python.exe -m pip install -e .
$env:TECHJAM_MODE = "lite"
C:\venvs\techjam-agent-lite\Scripts\python.exe -m unittest discover -s tests -v
C:\venvs\techjam-agent-lite\Scripts\python.exe -m scripts.validate_pipeline --mode lite
```

## 端到端三轮测试

以下命令使用未修改的官方 `evaluate()`，将公开 200 条仅用于 buying/browsing 反事实诊断：

```powershell
$env:TECHJAM_MODE = "exact"
python -m scripts.evaluate_full_requirements
```

对话顺序为：

1. Turn 1：记录初始需求并询问 `other`。
2. Turn 2：记录最多两条新要求并再次询问 `other`。
3. Turn 3：冻结公开需求，调用 Top50，再调用 Top10，输出推荐。

## GitHub 大文件方案

源码仓库不提交以下文件：

- `data/catalog.jsonl`（约 57.7 MiB）
- `resources/bge-small-en-v1.5`（约 128.3 MiB）
- `resources/dense_catalog_embeddings`（约 73.9 MiB）

它们被合并为一个 GitHub Release asset。普通 Git 仓库会阻止超过 100 MiB 的单文件；GitHub Release 单文件允许小于 2 GiB，并且不会让每次 `git clone` 都下载模型资源。

创建 Release 后，把 asset URL 填入 [`asset-manifest.json`](asset-manifest.json) 的 `download_url`，用户即可省略 `--url`：

```powershell
python -m scripts.install_assets
```

## 项目结构

```text
agent.py                         官方提交入口
starter/agent.py                 未修改 evaluator 的兼容入口
techjam_agent/agent.py           对话编排：两次 other → Top50 → Top10
techjam_agent/contracts.py       两阶段稳定接口与边界校验
techjam_agent/retrieval.py       exact/lite Top50 实现
techjam_agent/ranking.py         locked weighted-RRF Top10
scripts/install_assets.py        Release asset 下载、SHA256、解压
scripts/validate_pipeline.py     Top50/Top10 完整需求诊断
scripts/evaluate_full_requirements.py  官方三轮反事实评测
tests/                           接口与容量回归测试
```

更详细的接口约束见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)，部署和 GitHub Release 步骤见 [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md)，全新环境验证证据见 [`docs/VALIDATION.md`](docs/VALIDATION.md)。

与现有 Intent Router 的连接方式见 [`docs/INTENT_ROUTER_INTEGRATION.md`](docs/INTENT_ROUTER_INTEGRATION.md)，公开集与 3,021 条代理集的排序结果见 [`docs/RANKING_RESULTS.md`](docs/RANKING_RESULTS.md)。

## 数据与模型来源

- catalog 与公开会话来自 TechJam participant kit，底层源为 Amazon Reviews 2023；见 [`DATA_ATTRIBUTION.md`](DATA_ATTRIBUTION.md)。
- Dense 模型为 [BAAI/bge-small-en-v1.5](https://huggingface.co/BAAI/bge-small-en-v1.5)，模型页面标注 MIT License。
- 资源包不包含私有 800 条、organizer-only 文件、API key 或账户凭据。

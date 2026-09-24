# Validation record

验证日期：2026-08-30。

## 全新环境

- Python：3.12.13
- 解释器：`C:\Users\16211\Documents\ChatGPT\New project\tjv-clean\Scripts\python.exe`
- 安装方式：从本目录执行 `pip install -e ".[exact]"`
- 资源安装：通过 `scripts.install_assets --archive` 安装并校验本地 Release ZIP
- `PYTHONPATH`：未设置

Windows 的长路径环境 `.venv-clean` 在安装 PyTorch 时触发 `WinError 206`；改用短路径 venv 后安装和测试均成功。该问题属于依赖包路径长度，不是算法错误。

## 验证结果

| 检查 | 结果 |
|---|---:|
| 单元测试 | 6/6 passed |
| exact Top50 target coverage | 199/200 |
| locked Top10 target hit | 198/200 |
| 三轮反事实 HitRate@10 | 0.99 |
| 三轮反事实 MRR | 0.913042 |
| 三轮反事实 MTTC | 3.08 |
| 三轮反事实 Efficiency | 0.792 |
| 三轮反事实 TechnicalScore | 0.927313 |

三轮反事实评测把公开 200 条确定性地投影为 100 条 buying 和 100 条 browsing。它验证接口接线和完整信息假设，不代表原始四场景官方成绩。

## 与历史 locked 输出对齐

- Top50 候选集合：200/200 完全一致。
- Top50 顺序：199/200 完全一致。
- 唯一顺序差异：`public_0072` 的第 43/44 位互换；两个 Dense 分数只相差约 `1.19e-7`，属于单条/批量浮点计算差异。
- 最终 Top10 序列：200/200 完全一致。

因此，发布包的正式 Top10 行为与历史 locked `weighted_rrf` 一致；没有使用公开标签重新选择 schedule。

## Lite 模式边界

Lite 模式用于无第三方依赖的接口测试和降级：

- 目标命中计数仍为 Top50 199/200、Top10 198/200；
- 但 Top50 完整序列仅 101/200 与 exact 一致；
- Top10 完整序列仅 135/200 与 exact 一致。

因此 Lite 不能作为 exact 的等价复现，也不应拿来声明正式 locked 排名结果。

## 泄露审计

运行时目录 `techjam_agent/` 不包含或读取以下字段：

- ground truth / target product
- intent card
- quality tier / selection frequency
- profile preference tags
- 私有 800 条会话

Top10 重排器只接收 `CandidateSet`，没有 catalog 句柄，也不能向候选集合外添加商品。公开标签只在离线验证脚本中用于计算指标。

## 私有 GitHub Release 安装验证

完整非 Python 资源包已发布为团队私有仓库的 Release asset。验证使用一个不含 catalog 和 resources 的干净源码归档执行：

```powershell
python -m scripts.install_assets
python -m unittest discover -s tests -v
python -m scripts.validate_pipeline --mode exact
```

安装器通过已登录的 GitHub CLI 下载私有 Release，验证整个 ZIP 的 SHA256，解压后再次验证 catalog、模型权重、embeddings 和 parent-ASIN 文件。结果为 6/6 测试通过、Top50 199/200、locked Top10 198/200。

# Optional AWS Bedrock gateway integration

这是可删除的独立接入包。**原有 `python -m agentic_workflow` 保持不变，默认仍不调用云模型。**
核心模块不导入本包；删除 `optional_bedrock/` 后原项目仍能运行。
本包也不读取、导入或依赖 `ShowMeYourAgent-Starter-Kit/`，参考仓库可以单独删除。
本包依赖当前项目本身，不是独立的商品搜索系统。

## 接入范围和当前验证状态

主办方提供的 AWS Resource Usage Model 幻灯片明确允许 Lightsail 和 Bedrock
Claude Sonnet 4.5 JSON API 调用。团队配额、实际 URL、模型别名和 Key 以主办方邮件及
Slack 为准。本包不会申请 AWS 资源、修改配额或使用仓库示例中的凭证。

协议参考：[官方 starter kit](https://github.com/kenken64/ShowMeYourAgent-Starter-Kit)，
本地参考版本 `bffda0d15c494abef9202cab8feb13e067a40d1d`，重点是 `weather_demo.py`
的非流式 Ollama `POST /api/chat`、`X-API-Key` 和 `options.num_predict`。
返回读取 `message.content`、`prompt_eval_count`、`eval_count`。不要求网关原生 tool calling，
也不解析 Claude XML 工具调用；返回工具调用时拒绝该结果，不执行模型自报的工具结果。

适配器实现现有 `complete_json` 接口，供需求辅助提取及 Description/Comparison 使用。
现有 Python 策略仍控制检索、状态修改和最终确认。**这不是把整个 Agent 换成 LLM。**
若提供的是 API Gateway/Lambda 专用 JSON 接口而不是 Ollama 兼容接口，不可直接套用本包；
需要先根据 Slack 中的实际请求/响应格式适配。

目前完成本地模拟响应、真实项目 runtime 集成测试；**尚未使用团队真实网关和 Key 联调**。
通过本地测试不能证明主办方服务连通、实际模型质量或 AWS 部署可用。

## 最快启动方式

先在项目根目录复制配置：

```powershell
Copy-Item optional_bedrock/.env.example optional_bedrock/.env
```

只在本机编辑 `.env`，填写真实 URL、团队 Key 和模型别名。这三项当前留空，必须与主办方
实际配置匹配；不要盲目使用 Bedrock 原始 model ID。真实环境变量优先于 `.env`。
`.env` 已被根目录规则忽略，不要提交或截图 Key。

URL 填网关根地址，不要添加 `/api/chat` 或 `/v1`。远程连接要求 HTTPS。
如果网关只提供 HTTP，可在主办方允许的 SSH 连接上转发到本机，然后填
`http://127.0.0.1:11434`；即使通过本机隧道，界面仍明确说明数据会交给云端 Bedrock。

先做一次低 token 连通测试（**会消耗配额**，不自动重试）：

```powershell
E:/Software/Miniconda3/python.exe -B -m optional_bedrock --check
```

确认 `ok: true` 后，终端一启动小型演示目录 + 真实云模型：

```powershell
E:/Software/Miniconda3/python.exe -B -m optional_bedrock --demo --port 8001
```

`--demo` 仅表示商品是模拟数据，**LLM 调用仍是真实的**。用于快速检查需求提取及比较，
演示时必须如实标注。如果使用真实商品索引：

```powershell
E:/Software/Miniconda3/python.exe -B -m optional_bedrock --warmup --port 8001
```

预热在监听端口前加载本地检索模型，不调用云模型。仍需原项目的 BM25/PyTorch/
Transformers/Parquet 依赖及 LFS 资源。

终端二，从项目根目录启动独立前端配置：

```powershell
cd frontend
npm ci
npm run dev -- --config ../optional_bedrock/vite.config.mjs
```

打开 **http://127.0.0.1:5174/**。独立配置转发到 **8001**，不会误连原来的 8000 服务。
原前端 5173 可以继续运行。Node/npm 需已安装；本包无额外 Python 第三方依赖。
Linux 使用相同命令，将 Python 路径改为自己的 `python3` 或虚拟环境解释器即可。

## 联调检查清单

1. `/api/health` 应显示 `model_provider: aws_bedrock_gateway` 和 `cloud_model: true`。
2. 输入 `I need a blue dress. I prefer cotton.`，检查状态和实际 usage，不只看回答流畅度。
3. 输入 `Compare #1 and #2`，检查返回的 `handoff.comparison_assist.status`、证据和未知项。
   模型失败可能是 `partial`；HTTP 200 不代表生成成功。
4. 修改偏好再比较，检查缓存失效；相同结果复用时不要当作新的模型成功调用。
5. `Finalize my selection` 仅确认会话内选择，不产生购买。
6. 模拟错误 Key、超时与配额耗尽时应清楚降级，不能冒充完整模型分析。

现有 React 页面没有完整展示 `personalized_comparison`；本包不改前端产品代码。
如需展示全部个性化分析，可以在后端返回/原生演示页核对字段，但是否实际显示仍需逐项验证。

## 超时和额度

默认单次 HTTP 等待 20 秒，同一外层 chat/handoff 的模型等待共享 35 秒预算。
嵌套比较调用不会重置预算。预算不包含完整冷启动，也不是 socket 总耗时的严格硬截止；
原 React 的 45 秒总超时仍需实测。网关繁忙时可能出现 partial，此时不要无限重试。

默认每个进程最多 100 次云请求，失败请求也计数。达到上限后返回显式 provider 错误，
由现有工作流提供降级结果。这是请求次数保护，**不是 AWS 账单上限**；重启会重置。
根据主办方 usage plan 配置，额外监控官方配额。比较通常有三个模型阶段，输入 token
也会计费。网关未返回 token 计数时结果使用兼容值 0，不能解读成免费。

请求最多 60,000 字节、响应最多 1 MB，不自动重试、不跟随重定向、不输出 Key 或服务端错误正文。
starter kit 提醒 WAF 可能拦截大请求；默认大小并不是主办方保证的 WAF 阈值。
若收到 403，先检查 Key、网关权限和请求大小，请主办方确认限制，不自行绕过安全规则。

## 意图反转回归

无模型实测，第一句 `I want a cotton blue shirt` 记录：
`hard = {category: shirt, color: blue, material: cotton}`。

| 第二句 | 当前无模型结果 |
|---|---|
| `No cotton` | 清除 cotton 硬要求，添加 material 排除 cotton，保留蓝色和品类 |
| `Any material is fine` | 清除材质要求，不排除 cotton |
| `I don't want cotton anymore` | 清除 cotton 硬要求，添加 material 排除 cotton，保留蓝色和品类 |
| `wait I don't want cotton anymore, could you recommend me some other materials?` | 同上；右侧需求摘要与搜索过滤使用同一份更新后的状态 |

此规则覆盖缺口已在核心意图模块修复，不依赖本可选包或 LLM。核心回归测试使用包含
cotton、linen、silk 的模拟目录，同时检查需求摘要、实际候选和 Undo。
当前模型辅助模块只接受受证据检查的 `set/exclude`，不会接管任意清除和修改操作；
上述原话仍应作为真实 LLM 联调回归项。状态诊断脚本见 `check_intent_reversal.py`。

## Lightsail 部署边界

主办方允许资源不等于已经替团队部署应用。本包只提供模型接入和独立启动方式。
云端需要单独部署 Python 后端、构建前端并配置同源 `/api` 反向代理、HTTPS 和评委访问控制。
Vite dev server 仅用于本地联调，不直接作为公网生产入口。
部署密钥只放服务器环境；不要将 Key 写入 React 或 `VITE_*` 配置。

本地真实搜索曾出现约 53 秒冷启动。商品表接近 0.9 GB，内存加载和模型还会增加开销；
starter kit 示例的 4 GB Lightsail 不能直接当成已验证容量。先测 CPU 环境资源和预热，
必要时在明确标注的模拟目录上验证云模型，再扩展真实目录。不要擅自超过主办方资源额度。

## 测试和删除

```sh
python -B -m unittest discover -s optional_bedrock/tests -v
python -B -m optional_bedrock.check_intent_reversal
```

这些测试不需要 Key、不调用 AWS。删除前先停掉本包启动的服务，再删除 `optional_bedrock/`。
无需撤销核心代码或安装 OpenClaw/Hermes。回到 `python -m agentic_workflow` 和原前端命令即可。

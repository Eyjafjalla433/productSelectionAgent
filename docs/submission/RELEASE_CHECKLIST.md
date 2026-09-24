# Final release checklist / 发布前核对

## 已准备的交付内容

- `agent.py`：官方入口；`requirements.txt`：CPU依赖声明。
- README和`submission_tools`：安装、校验、官方评测、可录制demo、打包。
- TECHNICAL_REPORT与CONTRIBUTIONS：技术报告与组件分工。
- DEVPOST：可复制的英文项目说明。
- YOUTUBE_PLAN_ZH与YOUTUBE_DESCRIPTION：录制分镜、旁白和视频描述。
- public200.json与reproduction.json：实际评测及干净环境验收。
- `dist/shopping-copilot-submission.zip`：源代码提交包及SHA-256校验文件。

## Devpost填写对应

| 字段 | 内容 |
|---|---|
| Project name | Shopping Copilot: Stateful Conversational Product Search |
| Tagline | DEVPOST开头的Tagline |
| Project story | DEVPOST的Inspiration至Team contributions |
| Built with | Python, SQLite, BM25, reciprocal rank fusion, GitHub, Visual Studio Code |
| Source code | 审核并公开后的仓库/提交分支链接 |
| Video | 录制并公开上传后的真实YouTube URL |
| Team members | 团队在Devpost确认成员账号，未根据Git昵称猜姓名 |

## 最终外部动作与确认

- [ ] 确认成员和角色分工。贡献表按用户给定五个组件列出，不冒填姓名。
- [ ] 确认CPU提交配置和对应公开集结果，不把研究模型写成当前实测。
- [ ] 审核公开代码范围。精简ZIP检查**不等于整个Git历史安全审计**；现有仓库含研究文件，若直接改公开需要额外核对全历史、权重和权限。建议公开审核后的提交包内容。
- [ ] 选择仓库/分支并授权发布；本地交付包不代表已经推送或改公开。
- [ ] 按计划录制、审看并公开上传YouTube，确认素材许可。
- [ ] 粘贴Devpost正文，补成员和真实链接，核对当前截止时间、字段限制及声明，再确认正式提交。

没有自动公开GitHub、发送团队消息、上传YouTube或点击Devpost提交；这些材料不含飞书访问密码、API密钥或私人页面快照。

不必为了交付另做前端、购买GPU、从头训练模型、重建Amazon数据或获取private800。Dense/Qwen若启用须另行验收；未启用时如实说明。该包是CPU配置的提交候选，不承诺特定成绩或覆盖未来更新的赛事细则。

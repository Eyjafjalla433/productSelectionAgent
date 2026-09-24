# YouTube演示：重点与逐段讲稿

建议3–4分钟，这是建议时长，不是已核实的官方时长限制；提交前以Devpost当前字段为准。第4项目允许API/终端walkthrough，不需要网页。视频须公开上传YouTube并链接到Devpost。

## 最值得突出五件事

1. **记住变化的需求**：改蓝色但保留类别/预算；取消预算只清预算；换鞋清除旧裙子的颜色条件。
2. **4A与4B的区别**：4A缺类别先问、跳过检索；4B看到候选过宽后再问。不要只展示一次成功推荐。
3. **召回优先的提交边界**：提交配置保留模块2的确定性Top-50候选契约，再由4B排序；严格动态候选池作为可复现实验配置保留。
4. **真实官方数据与入口**：展示Agent、校验、实际ID、200会话指标。
5. **诚实配置与局限**：CPU规则版；未运行Dense/Qwen；98.5%是公开开发集结果；MRR略低于4号基线，综合分略高。

## 录制准备

- 提前安装数据并激活Python环境，放大终端字体；可裁掉用户名和绝对目录。
- 关闭通知。不要录入私人飞书、访问密码、邮箱、API密钥或无关浏览器账号。
- 用自己的流程图、屏幕录制和旁白，不加入未授权音乐、商标图案或素材；商品图片不是必要内容。保留数据来源说明，素材使用权仍需团队核对。
- 使用`--pause`逐轮展示。允许剪掉加载等待，但应注明；不要改日志冒充实跑或据剪辑后的时长宣传性能。

## 分镜与可直接念的英文旁白

| 时间建议 | 画面/命令 | 英文旁白 |
|---|---|---|
| 0:00–0:20 | 标题和自制流程图 | “Shopping requests change during a conversation. Our agent keeps explicit state, decides when to ask for missing information, and searches a frozen catalog of 50,000 products.” |
| 0:20–0:40 | Agent入口及4A/4B箭头 | “We connect intent, state, a pre-retrieval policy, retrieval, ranking and a post-retrieval policy. Actual questions and shown products are written back into state.” |
| 0:40–1:10 | `python -m submission_tools.demo --scenario clarify --pause` | “The first message has no category. Policy 4A asks for it and skips retrieval. Once the shopper supplies the missing information, the normal search path runs.” |
| 1:10–2:10 | `python -m submission_tools.demo --scenario override --pause` | “We begin with a black dress under fifty dollars. Blue replaces black without losing the budget. Removing the budget clears that constraint. Switching to shoes clears old dress-specific requirements and adds the new material exclusion to our lexical checks.” |
| 2:10–2:45 | `python -m submission_tools.demo --scenario browse --orchestration-mode adaptive --pause` | “This optional strict profile demonstrates policy 4B: after inspecting a broad dynamic pool it asks for a feature. Our submitted score profile instead preserves module two's recall-compatible Top-50 boundary.” |
| 2:45–3:15 | 打开`docs/submission/public200.json` | “Across 200 official public development sessions, our CPU entry reaches 98.5 percent Hit Rate at 10, MRR 0.8884, and mean turns to conversion 3.205. We use the unchanged official evaluator. These are not private-test results.” |
| 3:15–3:40 | README复现命令和局限 | “The submission runs offline with Python and SQLite, without a GPU or model API. It remains primarily rule-based, without Dense or LLM inference or persistent profile learning. Next we will isolate clarification and ranking effects through controlled experiments.” |

篇幅紧时，保留4A、多轮覆盖、4B和实测指标，压缩背景介绍，不能省略配置说明。

## 必须核对的实际效果

4A样例第一轮：`clarify missing_category`、`RETRIEVAL: skipped by 4A`、category问题、0推荐。第二轮补充黑裙和预算后，提交配置仍会请求额外偏好；第三轮补充材质/风格后检索。这正是基于state信息量而非固定turn编号的4A。

| 多轮样例 | 状态重点 | 当前参考候选数 |
|---|---|---:|
| 黑裙≤50 | dress/black/price_max=50，4A先补信息 | 此轮不检索 |
| 改蓝色 | dress/blue/price_max=50，4A再次补信息 | 此轮不检索 |
| 取消预算 | dress/blue，没有price_max | Top-50进入4B |
| 改鞋，不要皮革 | shoes，无旧color，exclusions.material=leather | Top-50进入4B |

这些是冻结数据和当前配置的参考值。输出不同应检查版本/数据/配置，不要改日志。词法检查不能保证实物材质，不能把“未出现leather”说成真实材质认证。

**已实跑确认的推荐错误也要交代**：裙子会混入dress shirt，鞋子会因Clothing, Shoes & Jewelry总类名混入非鞋商品。此段重点是状态如何覆盖、清除和重编排，不要声称每个结果都准确。可加旁白：“This demonstrates state changes. Lexical category matching still produces false positives, including dress shirts for a dress request.” 展示推荐效果时用篮球服饰样例，但不要隐藏或否认这个局限。

Browsing的4B片段应显式使用`--orchestration-mode adaptive`：首轮4A允许检索，4B随后询问feature；补充Drawstring closure和透气网布后，软偏好增加而类别保持。该片段展示研究配置的4B能力，不能冒充默认提交配置或评测命中证明。要展示官方单会话结果，用：

```bash
python -m submission_tools.evaluate --sample-id public_0006 --trace --output results/public0006.json
```

## 上传与提交

1. 标题建议：**Shopping Copilot | Stateful Conversational Search | TikTok TechJam 2026**。
2. 描述复制`YOUTUBE_DESCRIPTION.md`正文；只有完成剪辑后才按真实时间添加章节。
3. 设为**Public/公开**，不是Unlisted/不公开；用未登录窗口检查可访问。
4. 源代码仓库也必须公开可访问；描述里的现有仓库链接需在团队授权发布后才能满足这一点。
5. 将真实YouTube URL填入Devpost视频字段，并在说明中附链接。不能填本地路径或虚构URL。
6. 标题、视频、README和Devpost保持CPU配置/指标一致。

本文件是可执行录制脚本，不代表已经录制、上传或公开视频。

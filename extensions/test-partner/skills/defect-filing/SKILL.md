---
name: defect-filing
description: 把已经查清的问题整理成 TAPD 缺陷单草稿并等用户确认。用户说「提个缺陷」「提个 bug」「把这个问题提到 TAPD」「补个缺陷单」，或一条测试旅程执行完出了 FAIL 需要出缺陷出口时使用。工序：从会话/run 产物取六段素材 → 写草稿 JSON → 跑 render_draft.py 出固定 6 段预览 → ask_user 等用户确认标题与处理人 → 交付正文。三条硬纪律随包带：字段编号一律不写死（用 cus_ 别名）、六段结构禁改写、每缺陷只挂 1 个主复现 log。**本 skill 不提交**：TAPD 写工具没在白名单里，确认之后由用户建单。
---

# 缺陷出口：从排查结论到可提交的缺陷单

配套三份文件（用 `read_skill` 按需取）：

| 文件 | 是什么 |
|---|---|
| `references/bug-format.md` | 正文格式规范（从小铁资产移植），6 段结构与三条硬纪律的人类可读版 |
| `references/field-defaults.json` | 项目字段默认值（前缀、报告人、候选值表）。**没有字段编号，故意的** |
| `scripts/render_draft.py` | 草稿 → 预览的渲染器。三条硬纪律在这里是判红，不是叮嘱 |
| `references/example-draft.json` | 一份跑得通的样例草稿，照它的形状填 |

## 这条链路能做到哪一步（先说清楚，别承诺兑现不了的）

```
取素材 → 写草稿 JSON → 渲染预览 → ask_user 确认 → 交付正文给用户建单
                                                   ↑
                                        链路到此为止：写工具没放行
```

容器里的 TAPD 只放行 `get_stories_or_tasks` / `get_stories_fields_info`
两个**只读**工具；`create_bug` 等 17 个写工具**一个都没放行**。这不是配置漏了，
是决策 0027 裁定二：**写工具与确认闸同批放行**——先给写能力、后补确认流程，
中间那段窗口期就能绕过确认直接提单，这个口子不开。

所以：**不要说"已提交""已授权""帮你提上去了"**。确认完就把正文交给用户，
让他在 TAPD 页面建单；他要的是可以直接贴的正文，不是一句"好的我提了"。

附件同理：官方 MCP 全量 43 个工具里**没有任何附件上传工具**（只有只读的
`get_entity_attachments` / `get_image`）。log 以链接或正文占位路径承载，
用户要真附件就自己上传——不因附件卡住整条链（0027 降级条款）。

## 工序

### 1. 取素材：六段各自从哪来

| 段 | 来源 |
|---|---|
| 环境 | 被测实例身份：靶机 base_url、部署 commit 或镜像 digest、固件版本。旅程线直接抄 `intake_profile` 里的构建锚 |
| 复现步骤 | **你实际操作过的那条路径**。旅程线抄 case 的 steps，排查线抄会话里真跑过的命令 |
| 期望结果 / 实际结果 | 旅程线抄 case 的 `expected` 与 run 里的实际返回；排查线抄会话里的对比 |
| 日志证据 | 一个主复现 log 的路径或链接 + 分链路的关键行摘录 |
| 初步判断 | 测试视角归纳；已看码定位到根因就写明 `文件:行号` + 一句修复方向 |

旅程线的证据指针从 `journey_get_batch` / `journey_project` 的产物里取
（`verdicts.jsonl`、run 收据、evidence bundle），**别凭印象复述**——
写进缺陷单的每个数字都要能指回一份落盘产物。

### 2. 写草稿 JSON

照 `references/example-draft.json` 的形状填。`sections` 的六个键**必须是这六个、
必须是这个顺序**：环境 → 复现步骤 → 期望结果 → 实际结果 → 日志证据 → 初步判断。

三条硬纪律在草稿这一层就要守住：

1. **字段编号一律不写死**。自定义字段用 `cus_<字段中文名>` 别名（TAPD 后台自己
   转义成 `custom_field_*`）。缺陷侧**没有**查编号的工具面——
   `get_entity_custom_fields` 的 `entity_type` 只支持 stories/tasks/iterations/tcases，
   不含 bug。所以编号猜不得也查不得，只能走别名；真要核编号让用户在页面上看。
2. **六段结构禁改写**。附件清单、对照日志、优先级建议都不许另立成段。
   优先级是 TAPD 字段 `priority_label`，不进正文。
3. **每缺陷只挂 1 个主复现 log**。对照日志、设备 B 的日志不是本缺陷的复现证据；
   要提就在「初步判断」里一句话带过并指向它。

还有一条复现步骤的铁律：**只写实操过且确实复现到的路径**。从日志反推出来的
触发机制归第 6 段，不进步骤。草稿里要显式写 `"repro_verified": true`。

### 3. 渲染预览

```bash
python -X utf8 <skill>/scripts/render_draft.py --draft draft.json
```

exit 0 出预览；**exit 2 是被纪律闸拦下**，错误码就是原因，照着改草稿再跑，
不要绕过脚本自己拼正文——绕过去就等于三条纪律没人守。

常见错误码：`E_FIELD_NUMBER`（写了 `custom_field_数字`）、
`E_SECTION_DRIFT`（段缺/多/改名/调序）、`E_MULTI_LOG`（挂了不止一个 log）、
`E_REPRO_UNVERIFIED`（没声明实操复现过）、`E_PRIORITY_IN_BODY`（正文写了优先级）、
`E_UNKNOWN_PROJECT`（项目不在配置里——问用户，别套别的项目的前缀）。

### 4. 人闸：用 `ask_user` 等确认

把预览给用户看，然后**用 `ask_user` 问**，一次问清这几项：

- 标题对不对（模块 + 一句话现象）
- 处理人 `de` 是谁（`field-defaults.json` 里默认是空的，这一项必须问）
- 严重程度 `severity` 与优先级 `priority_label`

> 这条通道有等待器，问答卡是真的能等到答复的：「测试」capability 跑在主聊 turn 上，
> `wait_for_user_reply` 由 `deeptutor/services/session/turn_runtime.py:1620` 注入
> （锚见 `server/gateway/gate_selfcheck.py` 的 `KNOWN_CHANNELS["capability_test"]`）。
> **别照抄伙伴通道的写法**：伙伴通道故意没有等待器，在那边调问答卡会被拍平成
> 本轮最终回复、结构化答复全丢且用户无感知（BB-502）——那两份 SKILL.md 里
> 「文字提问 + 等下一轮」的写法是给那条通道的，不是给这里的。
>
> 这份 skill 只挂在「测试」capability 这条有等待器的通道上，
> 不进伙伴的技能清单（`tests/test_defect_filing_skill.py` 有一条守着）。

用户不答就停在这里。**沉默不是同意**，也不要替他把处理人填上。

### 5. 交付

确认后把两样东西给用户：

1. `--format html` 那段 description 正文（可以直接贴进 TAPD 的富文本框）
2. 字段清单：workspace_id / title / severity / priority_label / de / te / reporter

再说一句这条链路到哪儿为止（见本文开头），别让用户以为单已经建好了。

## 你不做的事

- **不调任何 TAPD 写工具**。`create_bug` / `update_bug` / `create_comments` 等
  17 个写工具在本仓一律禁止调用，白名单里也没有它们——即使哪天放行了，
  也必须先经过第 4 步的确认闸，这两件事同批发生，没有例外。
- 不替用户拍处理人、严重程度、优先级。
- 不把同一条缺陷的多个复现场景并列进复现步骤。
- 不为了让脚本过闸而删素材——素材不够就说不够，问用户补。
- 不引用没有真实分配的单号。要提"关联需求/关联缺陷"，先确认那个号真的存在。

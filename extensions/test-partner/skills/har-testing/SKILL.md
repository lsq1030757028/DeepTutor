---
name: har-testing
description: 从 HAR 抓包产出可执行的接口测试用例，并可当场执行。用户给出 .har 文件（放 inbox/、给绝对路径，或直接粘贴内容）、提到「录了一段流量」「抓包生成用例」「接口用例」「HAR 转用例」「导入 Apifox/Postman」「跑一下用例」时使用。工序：parse_har 体检 → 汇报摘要 → 确认测试意图 → 生成场景级用例并按体检样例填 request 块 → validate_cases 自检 → 定向修复 → save_delivery 落盘（默认 Excel + Postman Collection 双产物）→ list_environments 看已配环境 → 问用户用哪个环境 → execute_cases(env=...) 真发请求出执行报告。凭据走本机配置页的「测试环境」，不在聊天里索要 token；档A可重登录的抓包优先用登录换新（auth="login"），用户配一次账号口令，token 每轮自动换新。
---

# HAR → 接口测试用例

本剧本配套 `test-partner` MCP 服务器的五个工具：`parse_har`、`validate_cases`、`save_delivery`、
`list_environments`、`execute_cases`。
工具是确定性的，编排和写作由你负责。**校验规则的唯一权威是 `validate_cases`**——
下面「写作纪律」「用例格式规格」的每条都标了它的规则编号，照着写就不会被拦。

## 工序（按顺序走，不跳步）

### 1. 确认 HAR 来源

三种通道，问清楚是哪一种：

- **inbox**：用户把 `.har` 放进 test-partner 仓库根的 `inbox/` 目录 → `parse_har(har_path="xxx.har")`（相对路径按 inbox 解析）。
- **绝对路径**：`parse_har(har_path="D:\\...\\xxx.har")`。
- **粘贴内容**：小 HAR 直接 `parse_har(har_content="{...}")`。

DeepTutor 在容器里、MCP 服务器在宿主机，聊天里的附件传不到服务器——**别假设你能读到用户上传的文件**，
必须走上面三种通道之一。工具返回 `error` 时把 `message` 和 `hint` 原样转达用户，不要自己猜文件内容。

### 2. 体检并汇报摘要

拿到报告后，用大白话向用户汇报（不要把整个 JSON 倒给用户）：

- **规模**：`summary.entry_count` 条请求 → `summary.endpoint_count` 个端点，过滤掉 `noise_entries_filtered` 条静态/埋点噪声。
- **业务域**：`domains` 里每个域的端点数与请求数（如「订单 3 个端点 / 5 次请求，购物车 2 / 2」）。
- **鉴权**：`auth.methods` 的 kind 与位置（如「Bearer token 走 Authorization 头，另有 cookie 会话」）。
- **可重放档位**：`replay.grade_label`（档A可重登录 / 档B静态 / 不可回放）；不可回放时把 `replay.missing` 的建议转达用户。
- **能不能登录换新**：看 `replay.auth_refresh.supported`。为 `true`（档A且识别出了登录请求）就告诉用户「这批用例以后每轮能自动重登录换 token，你只要在配置页配一次账号口令」；为 `false` 就把 `reason` 转达一句（做不了就是做不了，不要暗示能）。**记下 `replay.login_request` 这个对象**，第 7 步落盘要用。
- **脱敏声明**：一句话说明报告里没有任何 token/cookie/口令值（`redaction` 段），让用户放心。
- 若 `truncated` 里有 `endpoints_total`，说明端点被截断，问用户是要提高上限还是按业务域分批。

报告里每个端点还带一个 `sample`（请求样例），是第 4 步填 `request` 块的**唯一素材来源**：

```
sample: { method, url（归一化 URL，如 https://host/api/v1/orders/:id）,
          query_keys（只有非凭证键名，没有值）, headers[{key,value}]（凭证头的值是 <redacted>）,
          body{mode,language,raw}（凭证键的值已掩码；raw 可能被截断，`truncated` 为真时要自己裁成合法 JSON）,
          response_status }
```

`summary.endpoints_with_sample` 小于端点数时，说明样例被体积闸截了（`truncated.samples_note`），
剩下的端点要么分批体检，要么只写人执行用例（不填 `request` 块）。

### 3. 确认测试意图与范围（人闸）

**直接用文字提问，然后等用户的下一轮回复**，一次问清，别连环追问。

> 这条通道没有问答卡（BB-502）：伙伴通道**故意**不带 `wait_for_user_reply` 等待器。
> 在这里调问答卡类工具不报错，但会把问题拍平成本轮最终回复并结束本轮，
> 结构化答复与同轮上下文全丢。**问完就把这一轮收在问题上。**

至少问到：

- **范围**：全部端点，还是聚焦某几个业务域 / 某条主流程？
- **深度**：只要主流程正例，还是要带鉴权、边界、异常、幂等？
- **交付格式**：默认 `xlsx+postman` 双产物；用户只要表格就 `xlsx`，只要能跑的就 `postman`。

用户答得含糊时给一个默认方案让他点头，不要自己闷头开做。

### 4. 生成用例（聊天内先给 Markdown 预览）

按**场景级粒度**写：一个业务场景一条用例，断言写在预期结果里，不要一个断言一条用例。
聊天里用精简六字段的 Markdown 表格给用户过目：

| 编号 | 标题 | 前置条件 | 操作步骤 | 预期结果 | 优先级 |
| --- | --- | --- | --- | --- | --- |
| TC-001 | 已登录用户分页查询订单列表成功 | 持有效 token | 1. 调用 GET /api/v1/orders，page=1<br>2. 检查响应结构 | 状态码 200；code=0；data.total 为整数 | 高 |

落盘用的全字段（所属模块 / 用例类型 / 测试数据 / 关联端点）在调 `save_delivery` 时一并传，
聊天预览里不用铺开。

**每条用例都要按体检报告的 `sample` 填 `request` 块**——这是用例能被 Apifox/Postman 直接跑起来的
前提。填法（字段表见下面「用例格式规格」）：

- `method` / `url` 抄样例：url 把 `https://host` 换成 `{{baseUrl}}` 变量，路径用归一化形态
  （`{{baseUrl}}/api/v1/orders/:id`），query 用样例的 `query_keys` 配占位值（`?page=1`）。
- `headers` 抄样例的 headers：值是 `<redacted>` 的凭证头改写成 `{{token}}` 这类变量占位，
  **绝不回填明文凭证**（E10 会拦）。带认证 scheme 的写法照 Postman 的规矩来——
  `Bearer {{token}}`、`Basic {{cred}}` 都是合规值，不会被 E10 误伤。
- `body` 抄样例的 body：字段名照抄，敏感值保持 `<redacted>` 或换成变量；
  **样例里没有的字段一个都不许加**——报告没给的东西就是不知道，编出来的接口跑起来必错。
- `assertions` 至少一条，从预期结果翻过去：状态码用 `status`，业务码/字段值用 `json_path`，
  文本片段用 `body_contains`。没有断言的用例导出后跑完无从判定成败（W05）。
- 端点没有 `sample`（被体积闸截了 / 纯人工场景）时**不要硬造 request 块**：那条用例照常写六字段，
  导出时会变成占位 item，如实告诉用户这条要人工执行。

### 5. `validate_cases` 自检

把用例数组连同体检报告的 `endpoints` 一起传：

```
validate_cases(cases=[...], endpoints=<parse_har 报告里的 endpoints 数组>)
```

- `errors` 非空 = 未通过。**不要把校验报错原样甩给用户**，自己按 `case_id` + `field` 定向修复。
- `warnings` 不算失败，但要看：覆盖率缺口（W04）值得跟用户提一句「这些端点没覆盖，要补吗」；
  请求块没断言（W05）自己补上，别留着。

### 6. 定向修复一次再验

只改被点名的条目和字段，别整批重写。改完再调一次 `validate_cases`。
第二次仍然不过，就把剩余错误如实告诉用户并说明卡在哪，不要硬落盘、更不要偷偷改规则。

### 7. `save_delivery` 落盘

```
save_delivery(cases=[...], title="订单域接口用例", format="xlsx+postman",
              source_fingerprint=<parse_har 报告里的 source.fingerprint>,
              login_request=<parse_har 报告里的 replay.login_request，有就带上>)
```

- `format` 默认 `xlsx+postman` 双产物：`cases.xlsx` 给人读、进 TAPD，
  `<标题>.postman_collection.json` 给机器跑。用户点名要 `markdown` / `csv` / 单一格式就照办。
- 一定要带 `source_fingerprint`，收据才能溯源到这份 HAR。
- `replay.auth_refresh.supported` 为 `true` 时**一定要带 `login_request`**（原样传那个对象，
  它的账密位置是 `{{login_username}}` / `{{login_password}}` 占位，真值不在里面）。
  带上之后这个批次就自带「怎么换新 token」，用户以后在配置页的工作台里勾一下
  「登录换新」当场能跑，不用回聊天找你。为 `false` 就不传这个参数。

### 8. 告知落盘路径与收据

把返回的 `delivery_dir`、产物文件名、`receipt.json` 里的用例条数与校验结论转达用户。
产物在**宿主机**上，用户去那个目录取文件。

出了 postman 产物就**明确告诉用户**：`<标题>.postman_collection.json` 是标准
Postman Collection v2.1，可以直接导入 **Apifox**（导入 → Postman 格式）或 **Postman** 执行；
导入后在环境变量里填 `baseUrl`（返回值里 `postman.base_url` 为空时尤其要说）和 `{{token}}`
之类的凭证变量——**凭证由用户自己填，我们的产物里不带明文**。
返回值的 `warnings` 有内容（占位 item、缺断言、baseUrl 未定）时如实转达，别报喜不报忧。

### 9. 问用户要不要当场执行（人闸）

用例带了 `request` 块就已经是可执行的请求描述——**不必绕道 Apifox 也能跑**。

**先调 `list_environments`**（只读，返回已配环境的名字、base_url、变量个数，
不返回任何变量值），再按下面两种情况之一开口。

开口前先定**这批用例走哪条凭据路线**——看第 2 步记下的 `replay.auth_refresh.supported`：

| 体检结论 | 凭据路线 | 让用户配什么 |
| --- | --- | --- |
| 档A **且** `auth_refresh.supported` 为 `true` | **优先登录换新**（`auth="login"`） | 环境里配一次 `login_username` / `login_password` 两个变量 |
| 档B、不可回放，或 `supported` 为 `false` | 现状的环境变量路线（`auth="env"`，默认） | 环境里配 `token` 之类变量的**当前有效值** |

走登录换新的那句话这么说（不要说成"我要你的密码"，账密是填在他自己机器的页面上的）：

> HAR 里录的那个 token 早晚会过期。这份抓包能重登录，所以更省事的做法是：
> 去配置页 **http://localhost:3789** 的「测试环境」里配一次 `login_username`
> 与 `login_password`，以后**每轮执行前自动重登录换新 token**，不用再手工贴。
> 账密存在你自己机器上，不经过聊天、不进模型上下文。

**A. 已经有配好的环境**——用文字问一次并等下一轮（一次问清，别连环追问）：

> 这批用例可以现在就对着你的环境跑一遍，出「几过几败」的执行报告。要跑吗？
> - **用哪个环境**：<列出 list_environments 返回的环境名和地址> / 不执行，我自己导入 Apifox 跑
> - **执行范围**：默认全部 N 条；只想跑一部分就说编号

**B. 一个环境都没配**——不要问 base_url、**更不要向用户要 token 或口令明文**，而是告诉他：

> 执行需要环境地址和 `{{token}}` 这类变量的真值。请去本机配置页
> **http://localhost:3789** 的「测试环境」分区配一次：填环境名、base_url、
> 以及用例里用到的变量（<把本批用例里出现的变量名列出来>）。
> 这样凭据存在你自己机器上，**不经过聊天、不进模型上下文**；配好回来说一声环境名就能跑。
>
> （走登录换新时把这句换成：变量只要配 `login_username` 与 `login_password` 两个。）

用户选「不执行」就到此为止，把 Apifox/Postman 导入路径再说一遍即可，不要劝。
要跑就调（下面是现状的 env 路线）：

```
execute_cases(cases=[...],                                # 本次生成的全部用例
              env="测试环境",                              # 环境名，凭据的正门
              delivery_dir=<上一步返回的 delivery_dir>)   # 报告并进同一个交付目录
```

档A且识别到了登录请求，就改成登录换新（多两个参数，其余一样）：

```
execute_cases(cases=[...], env="测试环境",
              delivery_dir=<delivery_dir>,
              auth="login",                                # 执行前先重放登录换新 token
              login_request=<parse_har 报告里的 replay.login_request>)
```

- **`login_request` 原样传**那个对象，不要自己改它的 body（账密位置是占位符，
  真值在用户机器上）。工具会把新 token 注入成 `{{token}}` 供后续用例使用；
  用例里的变量名不是 `token` 就加 `auth_token_variable="你的变量名"`。
- 登录换不来（账密没配、登录 4xx、取不到 token、登录端点跨 host）→ 工具返回
  `ok:false` + `LOGIN_*` 错误码，**整轮不执行、不落报告**。照它的 `hint` 转达用户，
  典型处置是去配置页把 `login_username` / `login_password` 改对，**不要退回去
  在聊天里要 token**。取值路径不对时让用户在环境里加一个 `auth_token_path`
  变量（填 `$.data.token` 这类路径，或 `cookie:名字`）。
- 执行报告里会多一行「凭据来源」，写明本轮是通过登录换新拿的凭证与登录端点路径
  （**不含任何值**）；汇报时带上这一句，用户才知道 token 是新换的不是旧的。

- **执行范围默认 = 本次生成的全部用例**。`cases` 要把这一批**全部**传进去，
  缩减范围只能是用户明确要求的结果（他说了编号就传 `case_ids`，或者他说「只跑正向」
  你就明说自己挑了哪几条）。返回值里的 `received_case_count`（工具收到几条）
  与 `summary.total`（本轮执行几条）是给你自己对账用的：这两个数和你生成的条数对不上，
  说明范围被谁悄悄缩了，**汇报时必须说明少了哪些、为什么**。
- **别在聊天里索要凭据明文**。用户主动在聊天里贴了值，`variables={"token": "..."}`
  仍然可用（临时覆盖环境里的同名变量），但要**提醒一次**：这个值会留在聊天记录里，
  下次配到配置页更稳妥。提醒一次即可，不要反复劝。
- `base_url` 只在这两种情况下传：用户没配环境、临时打另一台机器。同时传 `env` 和
  `base_url` 时，**显式的 `base_url` 覆盖环境里配的地址**（变量仍从环境来）。
- **执行前先说一句会发生什么**：这会对那个环境发真实请求（写操作用例会真的改数据）。
  生产环境要格外确认一次。
- 工具的安全红线（照抄给用户也无妨）：只向 `base_url` 所在 host 发请求，跨 host 的用例
  直接 `skipped` 不发；变量没给全的用例 `skipped` 不发；不跟随重定向。
- 环境名写错时工具返回 `error: "ENV_NOT_FOUND"` 并列出已配环境名——照着改，别改成
  「让用户在聊天里贴 token」。
- 汇报执行结果：`summary` 的 **几过几败几跳过** + 失败用例的断言差异（期望 vs 实际）+
  `report_dir` 里的 `execution_report.json` / `execution_report.md` 路径。
- **失败要如实报告**，不要解读成「用例生成得不好」就想重写——被测系统真有问题时就该是
  这个结果，这正是测试的意义。要不要改用例、要不要提缺陷，交给用户判断。
- `skipped` 条目也要说清各是什么原因（人执行用例 / 缺断言 / 缺变量 / 跨 host），
  别只报过与败，把跳过的那些藏起来。`MISSING_VARIABLES` 的正确处置是**让用户把这个变量
  加进配置页的那个环境**，不是在聊天里要值。

## 用例格式规格

### 精简六字段（聊天展示）

| 字段 | 要求 | 校验规则 |
| --- | --- | --- |
| 编号 | 非空、全局唯一，形如 `TC-001` | E02 / E03 |
| 标题 | 非空，一句话说清「谁在什么条件下做什么，期望什么结果」 | E04 |
| 前置条件 | 字段必须在；没有前置就显式写「无」 | E05（缺字段=错）/ W01（空值=提醒） |
| 操作步骤 | 至少 1 步，字符串数组或按行分隔的字符串 | E06 |
| 预期结果 | 非空，写可判定的断言（状态码、业务码、关键字段） | E07 |
| 优先级 | 只能是 `高` / `中` / `低` | E08 |

### 落盘全字段（列名对齐 TAPD 用例库）

`用例名称`（=标题）/ `所属模块` / `前置条件` / `用例步骤` / `预期结果` / `用例等级`（=优先级）/
`用例类型`（默认「功能测试」）/ `测试数据` / `关联端点`。
`所属模块`建议直接用体检报告的业务域名（它同时是 Postman collection 的文件夹名）；
`关联端点`写 `METHOD /归一化path`。
编号不是 TAPD 导入列，不进表格，`save_delivery` 会把它记进 `receipt.json` 的 `case_index`。

### `request` 块（可选，可执行化用）

写了就能导出成可跑的 Postman/Apifox 请求；没写照样是合法用例，只是导出成占位 item。

| 字段 | 要求 | 校验规则 |
| --- | --- | --- |
| `method` | `GET/POST/PUT/PATCH/DELETE/HEAD/OPTIONS` 之一（大小写不限） | E11 |
| `url` | 非空。写 `{{baseUrl}}/归一化path?k=v`，也可写体检报告里出现过的绝对 URL；host 与 `METHOD path` 必须与「关联端点」对得上 | E12（空）/ E13（对不上） |
| `headers` | 数组 `[{key, value}]`，可省。凭证头的值写 `{{token}}` 变量、`Bearer {{token}}`、`<redacted>` 或 `***` | E14（不是数组）/ E10（明文凭证） |
| `body` | `{mode: "raw"\|"none", raw, language: "json"}`，可省。字段名只能来自体检样例 | E14（mode 非法）/ E10（体里有明文凭证） |
| `assertions` | 数组，至少一条。`{type, expected, path?}`：`status` 的 expected 是 100-599 整数；`json_path` 要给 `path`（`$.data.total`）与 `expected`；`body_contains` 的 expected 是非空文本 | W05（一条都没有）/ E15（写法不合法） |

断言怎么翻成 Postman 测试脚本（`save_delivery` 自动做，写的时候心里有数即可）：
`status` → `pm.response.to.have.status(...)`；`body_contains` → `pm.expect(pm.response.text()).to.include(...)`；
`json_path` → `pm.expect(jsonData[...]).to.eql(...)`。

### 场景级粒度判据

- 一个业务场景一条用例，多个断言写进同一条的预期结果。
- 步骤数落在 1–15：0 步是错（E06），超过 15 步会收到粒度提醒（W02），该拆成多条。
- 别把「登录」拆成一条独立用例又在每条用例里重复——登录属于前置条件。

## 写作纪律

1. **端点必须真实存在**。每条用例锚定体检报告里出现过的端点；报告里没有的端点不许写，
   传了 `endpoints` 时 `validate_cases` 会报 W03，`request.url` 对不上会报 E13（直接判败）。
   路径写归一化形态（`/api/v1/orders/:id`），不要把抓包里的具体 id 当成端点的一部分。
2. **不编造字段和数据**。字段名只能来自体检报告的 `sample`（请求头、请求体键名、query 键名）；
   样例里没有的字段一个都不许加，响应字段名报告没给就别在 `json_path` 里瞎猜。
   要写测试数据就写占位（`page=1`、`orderId=<有效订单号>`），不要虚构真实值。
3. **不写执行声明措辞**（E09，会被直接拦）。用例是**待执行的计划**，不是执行记录：
   - 禁止过去时自报：`已执行`、`实际验证`、`测试已通过`、`PASSED`；
   - 禁止未来时自述调用：`将调用`、`接下来我会调用`、`我将执行`。
   - 正常写法：步骤写「调用 GET /api/v1/orders」，预期写「状态码 200」——陈述句，不带人称。
   - 「未执行」「尚未验证」「系统将扣减库存」这类不是执行声明，可以写。
4. **脱敏值不得回显**。报告里的 `<redacted>`、`:opaque`、`:id` 是脱敏产物，
   原样保留或换成 `{{token}}` 这类变量即可，不要脑补它们背后的真实值，
   也不要向用户索要 token 明文——凭证由用户在 Apifox/Postman 的环境变量里自己填。
   请求块里出现明文凭证会被 E10 直接拦下。
5. **生成阶段不许自己判绿**。用例是**待执行**的计划，里面不能出现「结果：通过」之类的
   结论字段（E09 会拦执行声明措辞）。没真跑过 `execute_cases` 之前，不许说「已经跑通了」；
   导出的 collection 也只是待执行的用例集。
6. **执行结果只陈述事实**。跑完 `execute_cases` 之后，只说工具返回的事实：几过几败几跳过、
   哪条用例的哪条断言期望什么、实际拿到什么、状态码多少。
   - 不替被测系统找借口（「可能是环境不稳」「大概是测试数据的问题」都不要写）；
   - 不替自己找补（失败不等于用例生成得不好，别急着重写用例来「让它变绿」）；
   - 不把 `skipped` 混进「通过」里报，也不把它藏起来——跳过的原因逐条说清；
   - 结论性判断（是缺陷还是用例要改）交给用户，你只把事实摆齐。
7. **执行是有副作用的动作**。发请求之前必须有用户明确点头（工序 9 的人闸），
   写操作用例会真改被测环境的数据；用户没说环境地址就不要自己猜一个。
   凭据变量的值只在这一次请求里用，不回显、不写进报告、不在后续对话里复述。
8. **不要把凭据往聊天里引**。执行要真凭据，但正门是配置页的「测试环境」——
   聊天里只说环境名，值由网关在用户本机解析。**不要主动索要 token / cookie / 口令明文**：
   在聊天里打出来的凭据会进聊天记录、进模型上下文、进模型供应商，这是不可撤销的。
   用户自己坚持贴，就用 `variables` 接住并提醒一次风险，然后继续干活，别反复劝。
   **登录换新也是这条规矩**：账号口令填在配置页的环境变量表里（`login_username` /
   `login_password`），不在聊天里问、不在聊天里收。`login_request` 里出现的是占位符
   而不是值——它如果带上了真账密，说明哪里出错了，停下来查，别原样传出去。
9. **执行范围默认是全部**。`execute_cases` 的 `cases` 传本次生成的**整批**用例，
   不要为了省事只挑几条。缩范围必须是用户明确要求；用 `received_case_count` 与
   `summary.total` 自查，对不上就在汇报里说明。

## `execute_cases` 的 skipped 语义（与工具实现同源）

工具跳过一条用例只会是这五个原因之一，汇报时按人话说清是哪一种：

| `skip_code` | 人话 | 怎么办 |
| --- | --- | --- |
| `NO_REQUEST_BLOCK` | 没有 request 块 = 人执行用例 | 人工执行后回填结论，不是缺陷 |
| `NO_ASSERTION` | 有请求块但一条断言都没有（W05） | 补断言后重跑，不然跑完无从判定成败 |
| `MISSING_VARIABLES` | 用例里的 `{{var}}` 没给值 | 让用户把这些变量加进配置页那个环境（**不要在聊天里要值**），再重跑 |
| `CROSS_HOST_BLOCKED` | 用例 URL 的 host 与 `base_url` 不一致 | 安全红线，绝不发；要么改 `base_url`，要么这条本就不该跑 |
| `REQUEST_SHAPE_INVALID` | method 非法 / url 为空（E11/E12） | 回到 `validate_cases` 修用例 |

后两条是硬红线：**跨 host 不发、变量缺失不发**，不要试图绕过（换个写法、手工拼 URL
都不行），也不要在汇报里说成「工具跑不了」——这是工具在替被测系统和用户的凭据兜底。

## 工具速查

| 工具 | 入参 | 出参要点 |
| --- | --- | --- |
| `parse_har` | `har_path` 或 `har_content`，可选 `max_endpoints`（默认 120）、`include_samples`（默认开）、`max_samples`（默认 40） | `summary` / `hosts` / `replay`（含 `auth_refresh` 与档A时的 `login_request`）/ `auth` / `domains` / `endpoints`（含 `sample`）/ `redaction`；出错给 `error` + `hint` |
| `validate_cases` | `cases`（用例数组），可选 `endpoints`；形状传歪了（JSON 字符串/多包一层/单条传对象）会被规整并在 `normalized` 里回显 | `ok` / `errors[{case_id,field,code,problem}]` / `warnings` / `coverage` / `summary.cases_with_request` |
| `save_delivery` | `cases`、`title`、`format`（默认 `xlsx+postman`，大小写/空格宽容）、`source_fingerprint`、`login_request`（体检支持登录换新时带上）；入参规整同上 | `delivery_dir` / `files` / `postman_file` / `import_hint` / `warnings` / `receipt` |
| `list_environments` | 无 | `count` / `environments[{name, base_url, variable_count}]` / `config_url`；**不返回变量的值，也不返回变量键名** |
| `execute_cases` | `cases`（默认传全部）、`env`（环境名，凭据的正门）、`base_url`（不用 env 时必填）、`variables`（可选，临时覆盖，凭据值不回显）、`case_ids`（可选，只跑指定编号）、`timeout_s`（默认 15）、`delivery_dir`（可选，报告并进交付目录）、`auth`（`none`/`env` 默认/`login`）、`login_request`（`auth="login"` 时必给）、`auth_token_variable`（默认 `token`） | `summary{total,executed,passed,failed,skipped,verdict}` / `received_case_count`（收到几条，对账执行范围）/ `results[{case_id,name,passed,assertions[{type,expected,actual,passed}],status_code,elapsed_ms,error}]` / `environment` / `auth{mode,refreshed,login_endpoint,token_variable,note}`（只有模式与端点，没有值）/ `report_dir` / `files` / `report_hint`；`ok` 只表示这轮跑起来了，不表示用例全过 |

## 规则编号总表（权威在 `validate_cases`）

| 编号 | 含义 |
| --- | --- |
| E01-E08 | 六字段：形状 / 编号空 / 编号重复 / 标题空 / 缺前置条件 / 步骤空 / 预期空 / 优先级非法 |
| E09 | 执行声明措辞 |
| E10 | 请求块出现明文凭证（`{{var}}`、`<redacted>`、`***`、`Bearer {{token}}` 这类占位不算） |
| E11 / E12 / E13 | 请求块 method 非法 / url 为空 / url 与关联端点对不上 |
| E14 / E15 | 请求块结构非法 / 断言写法非法 |
| W01-W04 | 前置条件空串 / 步骤过多 / 端点不在报告里 / 端点未覆盖 |
| W05 | 请求块没有断言 |

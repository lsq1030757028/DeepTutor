# test-partner

DeepTutor 二开「测试伙伴」——多技能测试 agent（需求→用例、HAR→用例、UI 自动化）。

- 计划：docs/PLAN.md
- 决策：docs/decisions/
- 纪律红线：只写插件，不改 DeepTutor 内核。

## 能力网关（宿主机唯一常驻进程）

决策 `docs/decisions/0006-capability-gateway.md`。一个进程两个面：

| 面 | 绑定 | 干什么 | 谁访问 |
| --- | --- | --- | --- |
| **MCP 面** | `0.0.0.0:3790`，路径 `/mcp` | 提供测试工具（七个） | Docker 里的 DeepTutor，走 `host.docker.internal` |
| **配置面** | **只绑 `127.0.0.1:3789`** | 填凭据、配测试环境、看状态灯、重启子服务、刷新聊天会话，以及**工作台**（勾用例、选环境、当场执行、看结果表）——按类型分五个 tab，可 `?embed=1` 嵌进 DeepTutor 右侧面板 | 你自己的浏览器，局域网不可达 |

网关还代管子服务（当前是 `mcp-server-tapd`）：按配置注入环境变量起子进程、探健康、
注册进 DeepTutor 的部署注册表。**逐能力的 cmd 脚本路线已废止**，新增能力 =
网关加子服务 + 配置页加一个分区，不再新增脚本。

### 首次准备

```
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

要求 Python ≥ 3.10（实测 3.12.10）。配置面用的 starlette / uvicorn 都在
`mcp[cli]==2.0.0` 的依赖树里，不需要额外装。

### 启动

双击或命令行执行（**唯一入口，没有第二个脚本要跑**）：

```
scripts\start_server.cmd
```

启动前必须把同一个 `TEST_JOURNEY_BRIDGE_SECRET`（至少 32 UTF-8 字节）分别写入
DeepTutor 根目录的 gitignored `.env` 与本目录的 gitignored `config/secrets.env`。
`start_server.cmd` 会先做只显示键态的同值预检，缺失、过短、重复或不一致都在打开监听前
失败；不会输出值、长度、掩码或摘要。默认检查 `..\..\.env`，独立目录部署可用非敏感路径
变量 `DEEPTUTOR_ENV_FILE` 指向 DeepTutor 的实际 `.env`。

预检通过后等价于 `.venv\Scripts\python.exe -m server.main`。`server.main` 还会从
`config/secrets.env` 再校验并注入当前宿主进程，防止绕过入口时带着缺失或冲突值启动。
前台运行，Ctrl+C 停止，
启动时会把配置页地址打在横幅里。首次启动 Windows 防火墙可能弹窗，允许即可
（需放行专用/专有网络，容器走 Docker NAT 访问宿主；配置面绑回环，与防火墙无关）。

想开机自启：`scripts\install_autostart.cmd`（用 `schtasks` 注册一个 ONLOGON 任务，
不提权、不装服务、不碰凭据；跑之前会先把要创建什么打出来并 `pause` 等你确认）。
撤销用 `scripts\uninstall_autostart.cmd`。

### 配置页

浏览器打开 **http://localhost:3789**。页面按**类型分五个 tab**（0.6.1 起，不再是一个
往下滚的长页面——分区只会越加越多，堆一页的话每加一个能力就多一屏滚动）：

| tab | URL 锚点 | 内容 |
| --- | --- | --- |
| **概览** | `#overview` | 状态灯（网关版本、MCP 面端口、DeepTutor 里 `test-partner` 与 `tapd` 两条的连接状态与工具数、TAPD 子服务）+ 维护动作 + 入口链接与配置目录路径 |
| **连接与凭据** | `#credentials` | TAPD 访问令牌；数据库连接、Git 凭据两个 disabled 的「预留」分区 |
| **测试环境** | `#environments` | 环境与变量金库 |
| **测试伙伴** | `#partner` | 一键创建/修复测试伙伴 |
| **工作台** | `#workbench` | 交付批次 → 用例表（勾选）→ 选环境与鉴权方式执行 → 结果表（0.7.0 起，0.8.0 加登录换新） |

切 tab 是纯前端的：不发请求、不重建表单，填了一半切走再切回来内容还在。当前 tab 记进
URL hash，`http://localhost:3789/#environments` 可以直达，刷新也落回同一个 tab。

各 tab 里做什么：

- **TAPD 访问令牌**：password 输入框，粘贴 → 「保存并生效」，页面逐条显示四步结果
  （写配置 √ → 重启子服务 √ → 注册进 DeepTutor √ → 回读连接状态 √）。
  已配置时显示掩码（末 4 位）与长度，直接贴新令牌即完成更换；
- **测试环境**：执行用例要的环境地址与 `{{token}}` / `{{session}}` 这类变量在这里配。
  一个环境 = 环境名 + `base_url` + 备注 + 若干变量键值对（值是 password 输入框）。
  列表只显示变量的**键名与掩码**，值从不回显；编辑时值输入框留空表示保留原值，
  删掉整行才会删掉那个变量。删除环境走**行内确认条**（不是浏览器弹窗，原因见下）。
  有三个**保留变量名**是「登录换新」用的，页面上有行内说明：`login_username`（登录账号）、
  `login_password`（登录口令）、`auth_token_path`（可选，登录响应里 token 的取值路径）。
  配好之后在聊天里**只说环境名**，`execute_cases(env="测试环境")`
  由网关在本机解析出地址与全部变量值；
- **维护动作**：「重启 TAPD 服务」、「刷新聊天会话」（= 把 DeepTutor 里 `test-partner`
  条目的开关拨一次，过去每次重启网关都要手工去 MCP 服务页做的那一下）。

失败不让你猜：子服务起不来时按启动日志分四类给诊断（令牌被拒 401/403、连不上
api.tapd.cn、端口被占、`.venv` 依赖缺失），页面上可展开原始日志末几行。

### 工作台（0.7.0）

固定工作流不该靠聊天来驱动——勾几条用例、选个环境、点执行，鼠标一秒完成的事，
让模型转述三轮既慢又不可靠。「工作台」tab 走的就是「批次 → 用例 → 执行 → 结果」这条线：

1. **批次列表**：扫 `deliveries/`，按时间倒序列出每次 `save_delivery` 的落盘（标题、
   时间、用例数、产物文件数、执行过没有）。
2. **用例表**：点批次进去，表格列出编号 / 标题 / 模块 / 优先级 / 有没有请求块 / 断言数。
   支持全选、全不选、反选、只选可执行、按模块选；点一行展开看请求详情
   （方法、URL、请求头、请求体、断言逐条）。**凭据位置显示成 `{{变量名}}` 原样，页面不解析它。**
3. **执行**：勾好用例 + 选环境 → 「执行选中用例」→ 页面弹出**行内确认条**（不是浏览器
   弹窗）说清「将向 <base_url> 发 N 条真实请求，写操作会真改数据」→ 确认后后台开跑，
   页面显示「第 N/M 条 + 当前用例名」。执行走的是 `execute_cases` **本人**（进程内直接
   调用，不绕 MCP、不另写 HTTP 客户端），四条安全红线原样生效。
4. **结果表**：几过几败几跳过 + 明细（状态码、耗时、点开看断言的期望 vs 实际、
   跳过条目的原因）+ 一行「凭据来源」（本轮凭据怎么来的，只有模式与登录端点、没有值）。
   执行报告照常落进批次目录，页面给路径 + 复制按钮。
5. **产物区**：这个批次所有文件的绝对路径 + 复制。

**旧批次会明说降级**：0.7.0 起 `save_delivery` 每次都会多落一份 `cases.json`
（给机器读的结构化用例——xlsx 丢了 request 块、collection 把断言编译成了 JS 文本，
两者都反推不回来）。这之前的批次没有这份文件，工作台会写明「该批次没有结构化用例数据，
只能查看产物路径」，执行按钮不给点——不反推、不假装能用。重新生成一批即可。

凭据在这条线上一步都不经过页面：页面传得出的只有**环境名**，值由网关在本机解析。
响应体里回显的凭证也会被换回 `{{变量名}}` 再显示。

### 把配置页嵌进 DeepTutor

DeepTutor 聊天页点任意 http(s) 链接会在**右侧 Viewer 面板**用 iframe 打开它，面板宽度
400–960px 可拖拽。概览 tab 里有一行现成的 markdown，点「复制链接」贴进聊天或伙伴的
SOUL 提示词即可：

```
[打开测试伙伴工作台](http://127.0.0.1:3789)
```

- **`?embed=1`**：`http://127.0.0.1:3789/?embed=1#environments` 这种形态不渲染大标题
  那一块「独立页面才需要的 chrome」，外框留白也收掉，只出内容主体，直接开在环境 tab 上。
- **深浅色自适应**：颜色全走 CSS 变量 + `prefers-color-scheme`。DeepTutor 默认深色，
  嵌进去不会是一块刺眼的白。
- **400–960px 都不塌**：容器只给 `max-width`，栅格与 flex 全相对单位。实测 400px 无横向溢出。
- 这些都不放开任何一道安全闸：内嵌是**同源回环**访问，Host 白名单与 CSRF 双提交照旧生效。

宿主 iframe 带 `sandbox="allow-scripts allow-same-origin allow-forms allow-popups"`、
`referrerPolicy="no-referrer"`，由此有三条**改页面时不能破的约束**：

1. **不发 `X-Frame-Options`，也不发 `CSP: frame-ancestors`**。`DENY` 固然不行，
   `SAMEORIGIN` 一样会挡死——DeepTutor 在 `127.0.0.1:3782`、配置页在 `localhost:3789`，
   端口不同即跨源。starlette 默认不加这些头，别去补
   （`test_page_is_framable_by_the_deeptutor_host` 钉着这条）。
2. **不用 `alert()` / `confirm()` / `prompt()`**。sandbox 没给 `allow-modals`，
   调用会被**静默阻止**：`confirm()` 直接返回 `false`，二次确认永远点不动且不报错
   （已实测）。要二次确认就在页面里画一条确认条。
3. **不做浏览器下载**。sandbox 没给 `allow-downloads`。要给用户文件就给**绝对路径 +
   一键复制**（`navigator.clipboard`，被拒时回退成选中文本让用户 Ctrl+C）。

另外 `referrerPolicy="no-referrer"` 意味着 `Referer` 头压根不来——不要做任何基于它的
来源判断，来源校验只能加在 Host 白名单那一层。

### 凭据存哪、看得见看不见

| 文件 | 内容 | 进版本库？ |
| --- | --- | --- |
| `config/gateway.json` | 非敏感：子服务端口、放行工具名、DeepTutor 地址 | 否（每台机器一份） |
| `config/secrets.env` | 敏感：`TAPD_ACCESS_TOKEN`、`TEST_JOURNEY_BRIDGE_SECRET` 等 | **否** |
| `config/environments.json` | 敏感：测试环境的 `base_url` 与变量键值对（`{{token}}` 的真值） | **否** |
| `config/secrets.env.example` | 模板与说明 | 是 |

- 三个文件都**原子写**（临时文件 + `os.replace`）：写一半被掐不会留下半截配置。
- **令牌值与环境变量值任何时候不回显**：页面、所有 API 响应、日志里它们只以
  「已配置 / 未配置」+ 掩码（至多末 4 位）+ 长度出现。这条有硬断言盯着
  （`tests/test_gateway_config.py`、`tests/test_gateway_webapp.py`、
  `tests/test_execute.py` 与 `scripts/verify_server.py`）。
- `TEST_JOURNEY_BRIDGE_SECRET` 更严格：公开状态只给 `configured` 键态，不给掩码、长度
  或摘要；真实值只存在上述两个 gitignored 文件及启动后的两个进程环境里。
- **凭据不经过聊天**：执行用例时传的是**环境名**，不是凭据。解析在网关进程内完成，
  值不进模型上下文、不进聊天历史。`list_environments` 工具连变量键名都不返回。
- **权限的实话**：0.6 的权限意识止步于「不进版本库、不出本机、不进聊天、不进容器」。
  Windows 上 `secrets.env` 就是一个普通文件，同机器上以你身份运行的程序都读得到；
  操作系统级隔离要上 DPAPI / 凭据管理器，还没做。
- `TAPD_API_BASE_URL` 不在配置文件里，被网关钉死成 `https://api.tapd.cn`
  （`server/gateway/supervisor.py` 的 `PINNED_API_BASE_URL`）。官方包对 base_url
  无任何校验，那一行是凭据流向的唯一闸门，不参数化、不给页面改。

### 配置面的四道安全闸

1. **只绑 127.0.0.1**——局域网不可达，其余三道都是它的补强；
2. **Host 头白名单**——只放行 `localhost` / `127.0.0.1` / `::1`（可带端口），
   挡 DNS rebinding（把 `evil.com` 解析到 127.0.0.1 那一手）。所有请求都查，不只 POST；
3. **CSRF 双提交**——`SameSite=Strict` 的 HttpOnly cookie + 页面内嵌同值 token，
   POST 要求 `X-CSRF-Token` 头与 cookie 逐字节相等。只靠 SameSite 不够：那是浏览器
   行为，非浏览器客户端不受约束；双提交是服务端自己能验的；
4. **凭据不回显**——见上一节。

### DeepTutor 侧挂载

`test-partner` 这一条仍需手工登记一次（学习空间 → 个性化 → MCP 服务 → Deployment
registry），URL 填：

```
http://host.docker.internal:3790/mcp
```

容器内 `host.docker.internal` 指向宿主机；服务器绑 `0.0.0.0` 才对容器可达，绑 `127.0.0.1` 会连不上。
`tapd` 那一条不用手填——在配置页保存令牌时由网关调 DeepTutor 的 API 自动 upsert
（只放行 `get_stories_or_tasks` / `get_stories_fields_info` 两个只读工具）。

### 当前工具

| 工具 | 说明 |
| --- | --- |
| `ping` | 存活探针，返回 `{"server": "test-partner", "version": "0.8.0", "status": "ok"}` |
| `server_info` | 服务器用途简介 |
| `parse_har` | HAR 体检：端点识别、业务域分组、鉴权方式、可重放档位、脱敏 |
| `validate_cases` | 用例确定性校验：六字段、编号唯一、优先级、粒度、执行声明、端点覆盖率、请求块 |
| `save_delivery` | 交付产物（xlsx/csv/markdown/postman，默认 `xlsx+postman`）+ 收据 JSON 落盘 |
| `list_environments` | 列出配置页里配好的测试环境（只出名字、`base_url`、变量个数）（0.6） |
| `execute_cases` | 按用例的 `request` 块真发请求、逐条判断言、出执行报告（M2.6）；`auth` 三模式 `none`/`env`/`login`，`login` 为登录换新（M2.7） |

后五个见下面的「HAR 测试能力」。

### 回归验证

服务器跑着的情况下另开一个终端：

```
.venv\Scripts\python.exe scripts\verify_server.py
```

MCP 面：initialize + list_tools + call ping/server_info + 五个工具的签名核对 + 真调
`parse_har`（内存里的极小 HAR）、`validate_cases`（一条故意违规的用例）、`execute_cases`
（故意不给 `base_url`，走入参闸返回，**不发网络请求、不落文件**）与 `list_environments`
（断言每条只有 name / base_url / variable_count，且不含任何环境变量的原值）。
配置面：GET 一次配置页，断言 200、是网关页面、有「测试环境」分区、**且已配置的凭据与
环境变量原值一个字符不在页面上**。
逐项断言，全过退出码 0。`--url` / `--gateway-url` 指定其他端点，`--skip-gateway` 只验 MCP 面。

开发期不要占用常驻实例的 3790 / 3789 端口，另起一份即可（两个面各有各的端口变量）：

```
set TEST_PARTNER_HOST=127.0.0.1
set TEST_PARTNER_PORT=3791
set TEST_PARTNER_GATEWAY_PORT=3788
.venv\Scripts\python.exe -m server.main
.venv\Scripts\python.exe scripts\verify_server.py --url http://127.0.0.1:3791/mcp --gateway-url http://127.0.0.1:3788
```

`TEST_PARTNER_GATEWAY=0` 可以只起 MCP 面不起配置面。

## HAR 测试能力

M2 里程碑的产物：录一段流量 → 体检 → 生成接口用例 → 校验 → 落盘 Excel。
编排剧本在 `skills/har-testing/SKILL.md`（Agent Skills 格式，把它挂进 DeepTutor 的技能里）。

### 五个工具

| 工具 | 入参 | 出参 |
| --- | --- | --- |
| `parse_har` | `har_path`（绝对路径或 inbox 下的文件名）或 `har_content`（贴内容）；`max_endpoints` 默认 120 | 体检报告 JSON：`summary` / `hosts` / `replay`（可重放档位 + `auth_refresh` 登录换新可行性 + 档A时的 `login_request`）/ `auth`（鉴权方式）/ `domains`（业务域）/ `endpoints`（method、归一化 path、次数、状态码分布、脱敏后的请求 `sample`）/ `redaction`（脱敏声明）；出错给 `ok:false` + `error` + `hint` |
| `validate_cases` | `cases`（用例数组）；可选 `endpoints`（直接传体检报告里的 endpoints） | `{ok, errors:[{case_id, field, code, problem}], warnings, coverage, summary}` |
| `save_delivery` | `cases`、`title`、`format`（默认 `xlsx+postman` / `xlsx` / `csv` / `markdown` / `postman`）、`source_fingerprint`、可选 `login_request`（体检支持登录换新时带上，写进 `cases.json` 让工作台可用） | `{delivery_dir, files, postman_file, import_hint, receipt}` |
| `list_environments` | 无 | `{ok, count, environments:[{name, base_url, variable_count}], config_url}`；**变量的值与键名都不返回** |
| `execute_cases` | `cases`、`env`（环境名，凭据的正门）、`base_url`（不用 `env` 时必填）、可选 `variables`（临时覆盖，凭据值不入报告）/ `case_ids`（只跑指定编号）/ `timeout_s`（默认 15）/ `delivery_dir`（报告并进交付目录）/ `auth`（`none`/`env` 默认/`login`）/ `login_request`（`auth="login"` 必给）/ `auth_token_variable`（默认 `token`） | `{ok, summary{total,executed,passed,failed,skipped,verdict}, received_case_count, selected_case_count, results, environment, auth{mode,refreshed,login_endpoint,token_variable,note}, report_dir, files, report_hint}` |

**凭据从哪来（0.6 起的正门）**：`variables` 参数意味着用户要在聊天框里打出真实凭据——
那等于凭据进模型上下文、进聊天历史、进模型供应商。正门是 `env`：在配置页的「测试环境」
分区配一次，执行时只说环境名，**解析在网关进程内完成，值不经过模型**。合并优先级是
「显式传入的覆盖环境值」：显式 `base_url` 覆盖环境地址；显式 `variables` 逐键覆盖同名
环境变量，没被点名的环境变量照常生效。环境名写错返回 `ENV_NOT_FOUND` 并只列已配环境名。

**登录换新（0.8 起，`auth="login"`）**：HAR 里录的 token 会过期，靠用户手工往环境变量里
贴新 token 不可持续。体检判为**档A可重登录**且识别出登录请求时，报告的
`replay.login_request` 就是那条登录请求的可执行描述（账号口令的位置是
`{{login_username}}` / `{{login_password}}` 占位，**真值不在里面**）。把它传给
`execute_cases(auth="login", login_request=...)`，执行前先重放一次登录、按
`token_extract` 规则从响应里取新 token，注入成 `{{token}}`（可用 `auth_token_variable`
改名）供后续用例使用——等价 Postman 的 pre-request auth，录一次以后每轮自动换新。

- 账号口令走「测试环境」的两个保留变量名 `login_username` / `login_password`，不新开存储、
  不经聊天。token 取值规则默认用体检推断的那条（`$.data.token` 这类路径，备选路径也列进
  报告让人能改），环境里配一个 `auth_token_path` 变量即可显式覆盖（写路径，或 `cookie:名字`）。
  推断不出就如实说不支持自动登录换新，不猜。
- `auth` 三个模式：`none`（一个环境变量都不注入，跑公开接口用）/ `env`（默认，用环境变量表）/
  `login`（在 `env` 之上先换新 token）。
- **登录换不来就整轮不执行**：账密没配、登录 4xx/5xx、取不到 token、登录端点跨 host——
  任何一条不成立都返回 `ok:false` + `LOGIN_*` 错误码与可读原因，**不落报告**，
  不带着坏 token 硬跑一堆 401。登录请求本身受同一套四条红线约束。
- 执行报告与返回值多一段 `auth`（模式、是否换新、登录端点路径、注入的变量名），
  **一个凭据值都不在里面**。

**执行范围**：`received_case_count`（工具收到几条）与 `summary.total`（本轮执行几条）
是给调用方对账用的——执行范围默认是整批用例，缩减范围只能是用户明确要求的结果。

**执行的安全红线**（`server/execute.py` 的模块 docstring 是权威）：只向 `base_url` 所在
host 发请求（跨 host 的用例标 `skipped` 绝不发，也不跟随重定向）；变量没给全的用例
`skipped` 不发；没有 `request` 块或没有断言的用例 `skipped` 不发；变量的值（无论来自
`env` 还是 `variables`）只用于真实请求，不进返回值、不进报告、不进日志（响应体里回显的
凭证也会被换回 `{{占位}}`），报告只记 `base_url` 的 host 与环境名。

**脱敏是硬约束**：`parse_har` 的报告只由 method、归一化 path、host、状态码、计数与键名拼成——
query 整段剥除，path 逐段归一化（数字/UUID/长十六进制 → `:id`，高熵段 → `:opaque`），
host 剥 userinfo；收尾另跑一遍凭证哨兵扫描，凭证值一旦出现在报告文本里就替换成 `<redacted>` 并计数。
token、cookie、口令类字段的值不会出现在任何输出里。逻辑移植自 blackbox-test-agent 的
`backend/intake_har.py`（对应关系写在 `server/har_parse.py` 的模块 docstring 里）。

### inbox 约定

DeepTutor 在容器里、MCP 服务器在宿主机，聊天附件传不到服务器。首版约定：

- 把 `.har` 放进仓库根的 `inbox/` 目录，然后传文件名（`parse_har(har_path="demo.har")`）；相对路径只在 inbox 内解析，跳不出去；
- 或者传宿主机绝对路径；
- 小 HAR 直接贴 `har_content`。

`inbox/` 的内容不进版本库（HAR 原件含未脱敏凭证），只保留 `.gitkeep`。

### 交付物结构

```
deliveries/
  20260804-181530-订单域接口用例/
    cases.xlsx                              # 或 cases.csv / cases.md
    订单域接口用例.postman_collection.json    # format 含 postman 时
    cases.json                              # 给机器读的结构化用例（工作台按它列表与执行）
    receipt.json
    execution_report.json                   # 跑过 execute_cases 并传了 delivery_dir
    execution_report.md
```

`cases.json`（schema v2）除了用例数组，落盘时带了 `login_request` 的话还会存一份——
批次因此自带"怎么重登录"，工作台上勾一下「登录换新」就能跑。它里面的账号口令位置
是占位符，真值在配置页的测试环境里。

产物列名对齐 TAPD 用例库：`用例名称 / 所属模块 / 前置条件 / 用例步骤 / 预期结果 / 用例等级 /
用例类型 / 测试数据 / 关联端点`。用例编号不是 TAPD 导入列，记在 `receipt.json` 的 `case_index` 里
（Markdown 产物给人评审，额外多一列编号）。

`receipt.json`（轻量治理：无哈希链、无装箱关）记：输入指纹 `input_fingerprint`、来源指纹
`source_fingerprint`（体检报告的 `source.fingerprint`）、生成时间、校验结果摘要、
产物文件清单与各自 sha256。`deliveries/` 不进版本库。

### 跑测试

```
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest tests\ -q
```

不起 MCP 服务器、不连外网：执行层的测试用 httpx 的 `MockTransport` 拦在传输层，只有一条
真 socket 集成测试会在 `127.0.0.1` 的随机端口起临时 HTTP 服务。
样例 HAR 在 `tests/fixtures/sample.har`（13 条请求，
含带 token 的 header、cookie 会话、口令登录、静态资源噪声与跨 host 请求）。

网关那几个文件同理不碰真东西：配置页的测试用 starlette `TestClient` + 假子服务管家 +
假 DeepTutor 客户端；子服务管家的生命周期用 `tests/fixtures/fake_service.py` 当替身起
真子进程（真 mcp-server-tapd 一次都不起——它 import 期就拿令牌去打 api.tapd.cn）。

### 关于 SDK 版本

依赖钉 `mcp[cli]==2.0.0`。官方 Python SDK 自 2.0.0 起把高层服务器类 **`FastMCP` 更名为 `MCPServer`**，`mcp.server.fastmcp` 模块已移除；客户端侧 `streamablehttp_client` 同步更名为 `streamable_http_client`，返回值也从三元组收敛为 `(read, write)` 两元组；结果模型字段改 snake_case（`server_info` / `protocol_version` / `structured_content` / `is_error`）。参照旧版 FastMCP 教程改代码时注意这几处。

协议版本可向下协商（SDK 默认 `2025-03-26`），已实测 DeepTutor 容器内旧版客户端可正常 initialize。

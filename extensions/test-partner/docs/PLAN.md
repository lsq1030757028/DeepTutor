# 测试伙伴（test-partner）· DeepTutor 二开推进计划

> ⚠ **2026-08-05 起本文件转为历史记录，不再是推进权威。**
> 路线已改为真二开（fork），后续计划见 [FORK-PLAN.md](FORK-PLAN.md)，规范见
> [0007](decisions/0007-pivot-to-real-fork.md) / [0008](decisions/0008-release-and-baseline.md)。
> M0–M3.6 的**已交付事实**仍然有效，可作为迁移清单；M4 及"下一轮候选"作废。
> 下面第 5 行的「不改内核」红线已被 0007 推翻。

- 立项日期：2026-08-04（用户拍板，决策记录见 `decisions/0001-route-deeptutor-plugin.md`）
- 项目定位：基于 DeepTutor 插件机制二次开发「测试伙伴」——多技能测试 agent（需求→用例、HAR→用例、后续 UI 自动化）。
- 纪律红线：**只写插件，不改 DeepTutor 内核**（保住上游演进红利）。
- 流程：每里程碑一张票 + 票内一页纸轻量需求卡；三闸照走（`docs/ai/ITERATION_GATE.md`）；缺陷入 `bug-bank/` 闭环；不走完整产品逼问，唯一逼问切片 = 用例产出格式规格（M2 动手前与用户对齐）。
- 模型分级：主脑决策 Fable；一切执行 subagent 用 Opus（用户 2026-08-03 拍板）。

## 里程碑

### M0 · 摸底与脚手架（1 天）——摸底已完成 2026-08-04
**实机结论**（实例：Docker `ghcr.io/hkuds/deeptutor:latest` = v1.5.8，2026-08-02 构建，容器名 `deeptutor`，端口 3782，唯一数据卷 `/app/data`）：
- `deeptutor.plugins` 模块**未随 v1.5.8 发布**（容器内 import 失败，master 克隆亦无此目录）——代码级 Tools/Capabilities 外挂发现机制在该版不可用，原 M2「har_test Capability 插件」路径关闭。
- CLI 应用商店只装 CLI-Anything 目录快照（`services/cli_apps/catalog.py` vendored catalog），不支持自定义本地应用。
- **可用的扩展面**：① MCP 服务（学习空间 → 个性化 → MCP 服务，支持任意远程 URL 服务器；容器内 `mcp` 包已装，`/app/data/system/user-mcp` 配置目录存在）；② 技能（SKILL.md，模型按需读取）；③ 伙伴（SOUL.md + 工具策略 + 知识库，技能/工具在伙伴工作区照常可用）；④ chat 的 agentic loop 自带多轮、并行工具、流式、`ask_user` 人闸。
- **修订后的技术形态：test-partner = 自研测试 MCP 服务器（宿主机常驻，HTTP/SSE）+ SKILL.md 测试剧本 + 测试伙伴配置**。编排交给 DeepTutor 的 chat agent loop，我们不写 Capability、不做派生镜像、不碰内核——比原方案更轻。
- 剩余脚手架任务：FastMCP 服务器骨架 + hello 工具挂进实例跑通（容器访问宿主走 `host.docker.internal`，服务器需监听容器可达接口）。
- **验收已通过（2026-08-04）**：聊天代理经 `load_tools` 调用 `mcp_test-partner_ping` 成功返回。两条运维发现：① 用户自配 URL 有 SSRF 严格模式（禁内网地址），自部署服务器必须走「Deployment registry」（ADMIN 级，全部署账号共享，团队推广的正确位置），配置存 `data/user/settings/mcp.json`；② 用户的 DeepTutor 容器原以 `--rm` 方式运行，Docker 重启后消失，已用同镜像+同数据卷重建并设 `--restart unless-stopped`。MCP SDK 注意：2.0.0 已移除 `mcp.server.fastmcp`（改 `mcp.server.MCPServer`），启动脚本 `.cmd` 必须纯 ASCII。

### M1 · blackbox 接线修复（0.5-1 天，与 M0 并行）
- BB-407：TAPD 链接路由到票05 提取器 + 取材层「零业务内容」前置闸 + 内部错误码翻译成用户语言。
- BB-408：generate 段合同禁令写进提示词（合同-提示词互钉）。
- 验收：用户亲手跑通一次需求旅程和一次 HAR 旅程。
- 此后 blackbox 冻结：只当资产库与对照系统，不再结构改造。**其内置 TAPD 旧通道不迁移 MCP**（冻结范围内，避免搭上在优化中的移动目标）。

### M2 · HAR 测试能力（约 1 周，核心里程碑）
- 测试 MCP 服务器新增工具：`parse_har`（移植 blackbox HAR 体检逻辑：端点识别/脱敏）、`validate_cases`（用例确定性校验：格式/字段/覆盖）、`save_delivery`（产物 + 收据 JSON 落盘）。
- `har-testing` SKILL.md 剧本：工序（体检 → 意图确认走 `ask_user` → 用例生成 → 调 `validate_cases` 自检 → 定向修复一次 → `save_delivery`）、用例格式规格、写作纪律——规则与校验器同源。
- 轻量治理：收据记输入指纹、模型、校验结果、产物路径；无哈希链、无装箱关。
- **用例格式规格（2026-08-04 用户拍板，含 0804 补充：按链路分默认）**：
  - 交付格式可配置，默认按链路区分——**HAR 链路默认 Excel/CSV**（偏自动化执行，机器消费为主，聊天内仍给 Markdown 预览）；**需求链路默认 Markdown**，可选 Excel、XMind（人评审为主，XMind 导出在 M3 实现）。
  - 字段分层：聊天展示精简六字段（编号/标题/前置条件/操作步骤/预期结果/优先级）；落盘导出带全字段，列名对齐 TAPD 用例库（+所属模块/用例类型/测试数据/关联需求或端点）。
  - 粒度=场景级（一个业务场景一条用例，步骤内多断言）。
- HAR 输入通道（v1 约定）：DeepTutor 在容器内、MCP 服务器在宿主机，附件不互通；首版约定用户把 HAR 放宿主目录（`test-partner/inbox/`）或给绝对路径，小 HAR 可直接粘贴内容；容器附件打通后置到 M4。
- 验收：用户在 DeepTutor 界面贴 HAR 端到端出用例，体验明显好于 blackbox。

### M2.5 · 用例可执行化（2026-08-04 立项，决策 0005，与 M3 并行）
- 用例 schema 加可选 `request` 块（method/url/headers/body/assertions，取自 HAR 真实样本并过脱敏哨兵）。
- `save_delivery` 新增 Postman Collection v2.1 导出（Apifox 原生可导入）；**HAR 链路默认双产物 = xlsx（人评审）+ postman_collection.json（可执行）**，修订此前"HAR 默认 Excel/CSV"口径。
- validate_cases 扩展：request 与关联端点一致性、断言存在性、请求体脱敏复检。
- 验收：真实 HAR 生成的 collection 导入 Apifox/Postman 可直接执行。
- **实现已落地（2026-08-04）**，与上面的计划口径一致，落地细节：
  - `parse_har` 每个端点带脱敏后的请求样例 `sample`（method / 归一化 URL / 非凭证 query 键名 /
    白名单请求头 / 掩码后的请求体 / 响应状态码）——`request` 块的字段只能来自它，禁止编造。
    凭证防线两道叠加：凭证键结构化掩码 + 原有收尾哨兵扫描；样例三道体积闸
    （样例端点 40 / 单条请求体 600 字符 / 全报告请求体 8000 字符）守住原输出上限。
  - `save_delivery` 的 format 扩为 `xlsx|csv|markdown|postman|xlsx+postman`，默认 `xlsx+postman`；
    postman 产物按所属模块分文件夹，assertions 翻成 pm.test，baseUrl 走 collection 变量，
    无 `request` 块的用例出占位 item 并计数告警；收据逐产物记 sha256。
  - `validate_cases` 续接编号：E10 明文凭证 / E11 method / E12 url 空 / E13 url 与端点对不上 /
    E14 结构 / E15 断言写法 / W05 无断言；剧本 `skills/har-testing/SKILL.md` 与校验器同源。
  - 离线测试 244 例（含 sample.har 走完整链路的集成测试）。
- **热修 0.3.1（2026-08-04 实机冒烟后）**：小模型传大体积工具参数时形状不稳，两处「修复优于拒绝」：
  ① `server/args_tolerance.py` 统一规整 `validate_cases` / `save_delivery` 的入参
  （cases 的 JSON 字符串、`{"input": ...}` 一类单键包裹最多解 3 层、单个用例对象包成数组、
  format 大小写与分隔符宽容），规整动作在返回值与收据的 `normalized` 里如实回显；
  ② E10 放行占位形态（`{{var}}` / `${VAR}` / `<redacted>` / `***` / `Bearer {{token}}`
  这类 scheme + 占位），真凭证（长随机串、`Bearer eyJ...` JWT）照拦——
  `Bearer {{token}}` 本就是 Postman 里的正确写法，拦它等于逼模型把对的写法改坏。

### M2.6 · 用例执行能力（2026-08-05，UAT 第一轮反馈驱动）
**一句话**：用例自带的 `request` 块本身就是可执行的请求描述——直接确定性执行，不必让用户导入 Apifox 才跑得起来（UAT 反馈：HAR 链路只通生成没通执行）。
- 新工具 `execute_cases`（`server/execute.py`）：`cases`（args_tolerance 同款宽容解包）+ `base_url`（必填，替换 `{{baseUrl}}`）+ `variables`（可选，替换其余变量）+ `case_ids`（可选，只执行指定编号——用户可选择的接口面）+ `timeout_s`（默认 15，夹取 1-120）+ `delivery_dir`（可选，报告并进交付目录）。
- 执行器用 httpx（运行环境是 mcp 2.0 带的 `httpx2`，模块内做导入兼容）：**同步逐条**执行（不并发，保证日志可读与被测系统安全），每条走「变量替换 → 红线闸 → 发请求 → 逐条判断言 → 记结果」；断言语义与 `server/postman.py` 的 pm.test 翻译同源（status / json_path / body_contains）。
- **安全红线四条**（`server/execute.py` 模块 docstring 是权威，SKILL.md 的 skipped 语义与之同源）：
  ① 只向 `base_url` 所在 host 发请求——渲染后 URL 的 `host:port` 对不上就标 `skipped` 绝不发，且整轮不跟随重定向（302 会把请求带去别的 host）；
  ② 变量未提供、渲染后残留 `{{var}}` 的用例标 `skipped`，reason 只写变量名；
  ③ `variables` 的值不进返回值、不进落盘报告、不进日志——请求头压根不记录，所有出站文本过 `_scrub` 门（够长的变量值换回 `{{名字}}`，因为响应体和异常串里可能回显凭证），收据只记 `base_url` 的 host；
  ④ 没有 `request` 块（人执行用例）、有请求块但没有断言（W05）一律 `skipped`，不拿「发出去了」冒充「通过」。
- 输出 `{ok, summary{total,executed,passed,failed,skipped,verdict}, results, report_dir, files, report_hint}`；`ok` 只表示这轮跑起来了，不表示用例全过。体积限幅：单断言 actual 截 200 字符，返回结果最多 200 条（全量在报告里）。执行报告落 `deliveries/`（新目录或并进传入的 `delivery_dir`，只接受 `deliveries/` 之下的既有目录）：`execution_report.json` + 表格版 `execution_report.md`。
- 剧本 `skills/har-testing/SKILL.md` 加工序 9：`save_delivery` 之后用 `ask_user` 问要不要当场执行（范围 = 全部/只正向/指定编号/不执行，需要时一并问 `base_url` 与凭据变量，并说明凭据只用于真实请求、不会被记录）；写作纪律加两条——执行结果只陈述事实（不替被测系统找借口、不替自己找补、跳过条目不藏），执行是有副作用的动作（须用户点头，写操作会真改数据）。
- 服务器版本 0.4.0；`scripts/verify_server.py` 把 `execute_cases` 纳入 `list_tools` 与签名断言，并真调一次（故意不给 `base_url`，走入参闸，不发请求不落文件）。
- 离线测试 292 例（`tests/test_execute.py` 47 例：断言三类型过/败、跨 host 拒发、协议降级与换端口都算跨 host、变量残留 skip、无 request/无断言 skip、`case_ids` 过滤、**变量值不出现在返回值与两份落盘报告里的硬断言**、报告合并进交付目录、一条真 socket 集成测试）。

### M3.5 · 能力网关 + 本地配置页（2026-08-05，UAT 二轮反馈驱动，决策 0006）
**一句话**：宿主机常驻进程从「一个 MCP 服务器」升级为「能力网关」——唯一进程、唯一自启项、唯一入口；凭据在本机配置页上填，不再一个能力一个 cmd 脚本（UAT 反馈：逐能力脚本不可扩展、令牌为什么不能在页面上配）。
- **一个进程两个面**（`server/main.py` v0.5.0）：MCP 面现状不动（`0.0.0.0:3790`，streamable-http，六工具，占主线程）；配置面 `server/gateway/`（**只绑 `127.0.0.1:3789`**，starlette + uvicorn 起在守护线程，两者都已在 mcp 2.0 的依赖树里，不引新框架）。硬把两个 ASGI 应用拼进同一个事件循环需要动 SDK 的启动路径，那是「改内核」，不做。
- **配置中心** `server/gateway/config.py`：`config/gateway.json`（非敏感：子服务端口、放行工具名、DeepTutor 地址）+ `config/secrets.env`（敏感，白名单键）。两者 gitignore，`config/secrets.env.example` 入库。读写原子（临时文件 + `os.replace`）；给页面的只有 `public_state()` 这个去凭据投影（`configured` / 掩码至多末 4 位 / 长度）。
- **子服务管家** `server/gateway/supervisor.py`：代管 mcp-server-tapd 子进程，用 `tapd-runtime/.venv` 的 PINNED 可执行文件；env = 清场（照抄 `start_tapd.cmd` 的六个污染键）+ 白名单注入 + 钉死 `TAPD_API_BASE_URL=https://api.tapd.cn`；启/停/重启/状态（探端口 + 进程存活，`running and not managed` = 遗留进程，重启时先接管）；起不来按启动日志分四类诊断（与 `register_tapd.py` 的 START_SYMPTOMS 同源，两份副本互为脚注）。退出联动是 `atexit` 尽力而为——网关被 End Task 强杀时子进程会遗留，已写进 README。
- **DeepTutor 客户端** `server/gateway/deeptutor_client.py`：收编 `register_tapd.py` 的逻辑（单条 upsert、既有字段保底、`enabled_tools` 尊重用户收窄）；新增 `toggle_entry()`——把 `test-partner` 条目开关拨一次刷新聊天侧会话，就是过去每次重启都要手工做的那一下。
- **配置页** `server/gateway/webapp.py`（内嵌单页 HTML，无前端框架、无 CDN）：状态区（网关版本 / MCP 面端口 / DeepTutor 两条条目状态 / TAPD 状态灯）+ TAPD 令牌表单（password 输入，已配置显示掩码）+ 维护动作（重启子服务、刷新聊天会话）+ 预留分区（数据库连接、Git 凭据，disabled）。保存走四步流水，页面逐条 √/× 并可展开日志末几行。
- **四道安全闸**：只绑 127.0.0.1；Host 头白名单（挡 DNS rebinding，所有请求都查）；CSRF 双提交（`SameSite=Strict` HttpOnly cookie + 内嵌同值 token + `X-CSRF-Token` 头，`compare_digest` 比对）；凭据零回显（页面、API 响应、日志三处均有硬断言）。
- **收尾**：`start_server.cmd` 仍是唯一入口（横幅打印配置页地址）；`setup_tapd.cmd` 顶部标注「已被配置页取代，排障时才需要」，保留不删；新增 `scripts\install_autostart.cmd` / `uninstall_autostart.cmd`（`schtasks` ONLOGON，纯 ASCII，先 echo 说明再 pause 等确认，**只创建不执行**，常驻由用户亲手启用）；`verify_server.py` 加配置面存活断言（GET 200 + 页面不含任何已配置凭据的原值）。
- 离线测试 101 例（config 20 / supervisor 25 / deeptutor 15 / webapp 41），全量 393 例全绿；`verify_server.py` 在 3791/3788 实测一轮全 PASS（含真读 DeepTutor 注册表：`test-partner · connected · 6 tools`）。

### M3 · 需求链路 + 伙伴门面（约 1 周）
- TAPD 接入（决策 0003 修订）：**用官方 `mcp-server-tapd`**（PyPI，活跃维护，原生 streamable-http），uvx 起服务后 DeepTutor 按 URL 挂载；不桥接、不耦合进测试服务器，不再给自研件补 HTTP 面。团队推广前须核验该包官方来源/源码审计（凭据经它进出）。自研 `tapd-capability` 的治理层价值（基线/漂移/证据）如何重定位，移交其维护线，不属本项目。
- 测试 MCP 服务器与 TAPD 解耦：HAR 链路完全不依赖 TAPD；`req-testing` 剧本声明依赖 `tapd-capability` 服务器的工具，未挂载时降级为"用户粘贴需求正文"。
- `req-testing` SKILL.md 剧本：需求 → 拆解 → 测试点 → 用例，人闸走 `ask_user`。
- **交互纪律（2026-08-04 用户实测 blackbox TAPD 回复后定，SOUL.md 与剧本共用）**：① 链接带具体需求 ID（如 dialog_preview_id/story id）时直取那一条，不拉列表不报工作区统计；② conf_id/queryToken 等机器字段永不回显给用户，一律转译为业务语言；③ 默认执行最可能的意图，只在真歧义时提问且一次只问一个问题——不许把选择成本外抛（列一堆选项让用户挑）。
- 测试伙伴上线：SOUL.md（测试工程师人格 + 写作纪律，与校验规则同源）、绑测试规范知识库、工具策略启用上述插件。
- 过渡方案（随时可配，零开发）：「我的智能体」接 Claude Code 顶班干重活。
- 验收：网页端贴 TAPD 链接跑通需求→用例（钉钉渠道后置 M4）。

### M3.6 · 工作台交互（2026-08-05 用户拍板，当前主线）
用户原话：`HAR先放过吧，后续把变量、交互这些做好，再接入吧，我不想基于会话体验了`。
- **定位确认**：固定工作流的交互只能长在我们自己的网页里。调研已证实 DeepTutor 侧没有任何 UI 扩展点——
  MCP 协议只走 list_tools/call_tool，工具返回值被压成纯文本，前端无"结构化返回→渲染卡片"机制，
  markdown 层白名单会把 iframe 转义掉；唯一的结构化交互卡片 `ask_user` 是内置的，MCP 产不出来。
- **内嵌通道已实测打通**（无需改 DeepTutor 一行）：聊天页点任意 http(s) 链接会在**右侧 Viewer 面板**
  用 iframe 打开（`allow-scripts allow-same-origin allow-forms allow-popups`，宽 400–960px 可拖），
  面板 Activity 页也有地址栏可直输。实测 `GET /?embed=1` 200，且 iframe 内的同源 fetch 正常工作。
  约束：不发 X-Frame-Options/frame-ancestors、无原生弹窗、无浏览器下载、无 referer、tab 不持久。
- **配置页已按此形态交付（0.6.1）**：四 tab（概览/连接与凭据/测试环境/测试伙伴）、`?embed=1` 去 chrome、
  深浅色自适应、400–960px 自适应。它就是工作台的雏形。
- **第一版已交付（0.7.0，2026-08-05）**：配置页加第五个 tab「工作台」，做通「批次 → 用例 → 执行 → 结果」这条线（**与用例是怎么生成的无关**，HAR 接不接得上都不影响它）。落地细节：
  - **`cases.json`（`server/delivery.py`）**：`save_delivery` 每次多落一份结构化用例（无条件，不看 format）。xlsx 丢了 `request` 块、collection 把断言编译成了 pm.test 的 JS 文本，两者都反推不回来——工作台要的是"把这批用例原样读回来"，只能自己落一份。它**不进收据的 `artifacts`**（那个清单的语义是"按 format 选出来的产物"），哈希与大小记在 `cases_file` 里照样可对账。
  - **旧批次降级**：0.7.0 之前的批次没有 `cases.json`，工作台明写「该批次没有结构化用例数据，只能查看产物路径」，执行按钮不给点。不从 collection 反推——解错了会**静默地执行错的断言**，比不给用更坏。
  - **后端** `server/gateway/workbench.py`：扫批次（元信息读 `receipt.json`，坏 receipt / 缺 receipt 都不许让整页塌）、读用例、`RunRegistry` 管执行（后台线程 + 内存台账 + 轮询，不上 websocket——一轮几秒到几十秒，每秒 GET 一次够了）。批次 id 只能是 `deliveries/` 下的一级目录名，带分隔符或上跳的一律拒（`safe_delivery_id`）。
  - **端点**（`webapp.py`，Host 白名单 + CSRF 双提交一条不放开）：`GET /api/deliveries`、`GET /api/deliveries/{id}`、`POST /api/deliveries/{id}/execute`、`GET /api/runs/{run_id}`。
  - **执行复用而非复制**：进程内直接调 `server/execute.py` 的 `execute_cases`，四条安全红线原样生效；`execute_cases` 只加了一个可选 `progress` 回调（执行是同步逐条的，所以"第 N/M 条"是真的，不是估的）。凭据同理——页面传得出的只有**环境名**，值由网关在本机解析，响应体里回显的凭证过 `_scrub` 换回 `{{变量名}}` 再显示。
  - **前端**：仍是内嵌单页 + 原生 JS，无框架无 CDN。表格在 400px 下横向滚动（滚动条只长在 `.tablewrap` 上，body 不横向溢出）；执行前的二次确认是**行内确认条**（沙箱没给 allow-modals）；产物一律绝对路径 + 复制（没给 allow-downloads）；可点的行补了 `role`/`tabindex` 与 Enter/空格，键盘也能用。
  - 离线测试 600 例全绿（新增 72 例：workbench 34 / webapp 工作台段 30 / cases.json 8 / 进度回调 4）；实机在 3788 起临时实例、对本地 mock 走完一轮：6 条用例勾选执行 → 2 过 2 败 2 跳过，断言差异与跳过原因都看得见，**页面与两份报告里都搜不到凭据值**（mock 故意回显鉴权头，页面上显示的是 `Bearer {{token}}`）。
- 下一轮候选（未拍板）：变量的页面填写与临时覆盖、执行历史、失败重跑、把 HAR 链路接回来。

### M2.7 · 登录换新（HAR）——**已交付（0.8.0，2026-08-05）**
问题：HAR 里录的 token 会过期，靠用户手工往环境变量里贴新 token 不可持续。
体检本来就能判「档A可重登录」，那就让网关**自己重放登录请求换新 token**再跑用例，
等价 Postman 的 pre-request auth——录一次，以后每轮自动换新。落地细节：

- **体检产出登录请求描述**：`parse_har` 的 `replay` 段多两块——`auth_refresh`
  （支不支持 + 原因 + 三个保留变量名）与档A时的 `login_request`（method/url/白名单头/
  body 形状）。账号与口令的**位置**用 `{{login_username}}` / `{{login_password}}` 占位
  标出（占位是位置信息不是值），真账密一律不出报告，脱敏哨兵扫描照旧全程有效。
  URL 写成 `{{baseUrl}}<path>` 而不是抓包时那台机器的绝对地址：登录换新要打的是
  **用户在配置页配的那套环境**，这也让执行层的跨 host 红线天然成立。
- **token 取值规则可见可改**：从登录响应推断 JSON 路径（`$.data.token` / `$.token` /
  `$.access_token` …，按优先级挑一个，备选全列进报告），推断不出就如实说不支持、不猜。
  环境里配一个 `auth_token_path` 变量即可显式覆盖（写路径，或 `cookie:名字`）。
  体检**不推断 Set-Cookie**：登录请求是靠"响应里产出了 token"认出来的，会话 cookie 型
  登录压根不会被认成登录（那种 HAR 判档B，走环境变量路线），写个 cookie 兜底分支
  永远执行不到——执行层仍认这种规则，留给用户显式指定。
- **执行层 `auth` 三模式**：`none`（一个环境变量都不注入，跑公开接口用）/ `env`
  （默认，0.6 起的现状行为）/ `login`（先重放登录换新 token 再跑）。登录失败、账密没配、
  取不到 token、登录端点跨 host —— 任何一条不成立都**整轮不执行、不落报告**并给可读原因，
  不带着坏 token 硬跑一堆 401。登录请求受同一套四条红线约束（同 host、不跟随重定向、
  凭据不出流、不判绿不猜）。执行报告多一行「凭据来源」，只记模式与登录端点路径，不记值。
- **批次自带怎么重登录**：`save_delivery` 多一个可选 `login_request`，写进 `cases.json`
  （schema 升 v2；不给就不写这个键，0.7.0 的批次形状不变）。工作台执行面因此能给出
  「鉴权方式：跟随环境 / 登录换新」的下拉——批次里没带这份描述时那个选项不给点，
  并说清缺什么，而不是让用户点了以后看着它红。
- **配置页**：「测试环境」的变量金库加了三个保留变量名的说明（静态一段 + 敲键名时的
  行内提示），用户知道 `login_username` / `login_password` / `auth_token_path` 是干什么的。
- 离线测试 661 例全绿（新增 61 例：har_parse 登录段 17 / execute 登录段 26 /
  delivery 边车 4 / workbench 鉴权 8 / webapp 页面与端点 8，另有 args_tolerance 4）。
  其中一条是**真 socket 全链路**：本地 mock 起一个"要登录才给数据"的服务，
  登录 → 拿 token → 带 token 请求 → 断言通过，账密与新 token 都不在返回值里。

### M4 · UI 自动化 + 渠道（视 M3.6 后启动）
- playwright MCP 挂载做 UI 自动化；钉钉渠道让伙伴进群；Claude Code 作本地执行臂。

## 风险
1. 用户实例插件机制与 master 克隆有出入 → M0 首日实机验证。
2. HAR 体检逻辑与 blackbox 内部耦合过深 → 最坏按已验证逻辑重写，成本可控。
3. 全局 TAPD MCP 在优化中、接口未冻结 → M3 前对齐契约 + 薄适配层隔离；blackbox 侧不迁移。

## 时间线
约 2.5-3 周到「两条链路能用 + 伙伴门面上线」。

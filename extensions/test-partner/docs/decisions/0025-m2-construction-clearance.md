# 0025 · M2 施工放行：Queenie 授权档、模式隔离取舍、API 轨无 trace、MCP 通道处置

- 日期：2026-08-11
- 触发：施工前对账 C1-C13 返工完成，放行前的最后一组裁定

## 1. Queenie 环境与授权档 —— 用户拍板

**选定 A2：测试环境地址 + 测试账号 + 数据库只读。** 不含可恢复写。

- 买到什么：能断到数据层——接口说成功且库里真写对了，证据强度高于纯接口断言。
- 不买什么：故障注入与写入链路验证（需可恢复写，属 A3）。M2 主证据不覆盖该档，如需该档能力实证，走白月SMS 零授权路径单独证（三靶分工见工程稿 §1.1.3）。
- **凭据纪律**：manager 与任何执行者只确认凭据是否存在、是否可连通，**不打印、不复制、不写入任何产物或聊天**。数据库账号必须是只读账号本身，不得用高权账号"自觉只读"。
- **获取责任在用户**（需对外协调）。未到位前，依赖 Queenie 的 DoD 条目不得判分，其余施工照常推进——不整体停工等凭据。

## 2. 模式隔离是否多花 1 登记行买硬闸 —— manager 自决

**选 B1：不买。**

- 依据：`owned_tools` + `is_active` 属 `LoopCapability` 协议字段（`protocol.py:61,63`），其候选池为 `USER_TOGGLEABLE_TOOL_NAMES ∩ BUILTIN_TOOL_NAMES`（`tool_composition.py:64-78`），**MCP 工具不在其中**；MCP 走 `build_tool_view` 另一条链（`agentic_pipeline.py:472-504`、`view.py:90-163`），管理员 grant 还会短路为 unrestricted（`authorize.py:54-63`）。花掉触点也治不到 MCP 面。
- 替代：可见性层如实标"软"，后果层做硬——`gate_token` 门票 + 既存 `batch_id` + 写确认三道，全在 extensions 内 0 触点（工程稿 §5.1.4，DoD#8e 拆 8e-1/2/3）。
- **证伪信号**：施工期实测主聊能调到 journey 工具且三道后果闸拦不住 → 重开本项。

## 3. API 轨不产 trace（本轮新发现，非对账报告所列）

`run_api_case` 不起浏览器、`page=None`、全程无 tracing（`pw_harness.py:115-121`），对照 UI 轨 `:58-65,109`。三处连带已裁定：

1. `journey_trace_open` **M2 不建**（ADR-M2-01 第 5 条的扩面动作在 M2 不发生）；**但 BB-501 仍须修**——它治的是测试侧起真 GUI 进程与 PID 不回收，与扩面无关。
2. 新增错误码 `E_TRACE_NOT_APPLICABLE`：「本轨没有 trace」≠「trace 打不开」。合并即违反 0021 红线六「成对错误态分开说」。
3. API 轨 caseset 的 `required_evidence` 禁含 `playwright_trace` / `screenshot`，并入 E22 校验。

## 4. 对账报告一处判错，执行者顶回成立（manager 已独立复核）

C1 连带洞 ②「护栏 5 五条红线在 API 轨下口径未定义」**不成立**。`server/journey/redlines.py:2-15` 的模块文档串明确声明「UI 轨 + API 轨共用」并逐条写明双轨口径（第 5 条：API 轨 `follow_redirects=False`；UI 轨重定向落点 host 须 ∈ 等价类否则 skip），实现见 `pw_runtime.py:209-214` 的 `_NoRedirect`（`redirect_request` 恒返 None）。该项由「须新设计」改判「须显式继承 + 纳入 M2 回归」。

记录此条是因为：**执行者带证据推翻验收者结论并且成立**，这是本线希望鼓励的行为，不应被"验收者说了算"淹没。

## 5. MCP 通道处置（施工 Day-1 硬前置）

当版实测（manager，2026-08-11）：现役实例 `deeptutor` @ `127.0.0.1:3782`（卷 `deeptutor-data`）的 `/app/data/user/settings/mcp.json` 两条均 `enabled:true`，但——

| 条目 | 配置指向 | 实际 |
|---|---|---|
| `tapd` | `host.docker.internal:3795/mcp` | 宿主无进程监听，连不上 |
| `test-partner` | `host.docker.internal:3790/mcp` | 端口被**圆桌线前端**占用（Next.js，PID 49148）——指针指错服务 |

3790 在 `DEV_STATUS.md` 共享资源节登记给圆桌线 UAT 使用，属合法占用；冲突方是 test-partner MCP 的配置。

**原裁定（已作废，留档见下）**：曾裁定「test-partner MCP 迁到未被占用端口并同步现役实例配置」。

### ⚠ 本节前提有误，manager 当轮更正（2026-08-11，施工 Day-1）

上表「3790 被圆桌线前端占用，冲突方是 test-partner 配置」**不成立**。施工执行者带证据顶回，manager 已独立复核确认：

- `DEV_STATUS.md:14` 将 3790 **登记给 P3 线 test-partner MCP**，并记「圆桌线曾误占此端口，2026-08-10 已迁走并实测让出」；
- `DEV_STATUS.md:15` 记圆桌线已迁 3791/8011；manager 复核实测 3790/3791/3795 三口均有服务响应（`/mcp` 分别回 400/404/406，均为服务在听的特征响应）；
- 现役实例 mcp.json 的两条指针与网关默认配置一致，**没有指错**。

**manager 的错误在于开的是错药方**：观测到「3790 上是 Next.js」属实（该时点圆桌线尚未让出或本机状态未刷新），但据此推断「配置指错、应迁端口」是错的。`DEV_STATUS.md:16` 自身早已记载真根因与真解法——**宿主 test-partner 网关没在运行**，拉起即可，两个 MCP 面一并恢复；且该行明确记录「前两条方案都建立在『服务不存在需要新建』的错误前提上」，manager 重复了同一类错误第三次。

**更正后裁定**：不迁端口、不改现役实例配置、不触碰圆桌线。按登记原地启用 3790；TAPD 子服务由 test-partner 网关托管并注册到 3795，随网关启动一并恢复。执行者已据此实施并全绿（P1-P4），**该偏离 manager 事后批准**。

处置后仍须实测两条通道可取到业务响应，不通即上浮；禁止用本地文档 mock 需求正文顶替（那会让 oracle 退回 M1 形态、主证据作废）。

**教训（与 DEV_STATUS.md:16 同源，第三次）**：排查顺序必须先确认进程在不在，再谈端口与架构。manager 在给出涉及共享资源的处置裁定前，须先读该资源在 `DEV_STATUS.md` 的登记行——本次跳过了这一步。

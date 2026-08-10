# M1 吸收设计稿 · DT 测试能力吸收工程（已过设计闸 2026-08-10，拍板结果见 decisions/0015）

- 日期：2026-08-10 · 作者：architect（deeptutor-absorb-m1 任务）
- 性质：**设计稿草案**。§七 所有拍板项均为「推荐 + 影响」，不构成决定；ApprovedCaseSet schema 同为草案（见 `agent-lab-runs/deeptutor-absorb-m1-20260810/approved-caseset-schema-draft.json`），设计闸通过后才冻结。
- 上游契约：`agent-lab-runs/deeptutor-test-agent-engineering-20260810/HANDOFF-implementation.md`（v2，唯一入口）；目标流程 SSOT 同目录 `workflow-comparison-and-target-flow.md`；决策 0013 / 0014。
- 版本锚（2026-08-10 实测重锚，非引用记忆）：
  - DeepTutor 基线 = 分支 `feat/p3-har-inspect` @ `49414819651be5fa9f6c888f50a3985042d79632`（`git rev-parse HEAD` 实跑）
  - 运行拓扑 = 容器 `deeptutor@127.0.0.1:3782`、`deeptutor-p3flow@127.0.0.1:3785`、`deeptutor-closure@127.0.0.1:3786` 均 healthy（`docker ps` 实跑）
  - 信封 `fact_anchors` 经 `fact_recheck.py` 复检报 UNVERIFIABLE（信封字段形态与脚本 schema 不符，属信封格式问题非事实失效）；本稿两条拓扑事实均已用锚内 source 命令**重新实跑验证**，与信封 value 一致，不再依赖信封锚。
- 证据锚约定：本稿内对 DT 现版代码/能力的事实断言均附 `文件路径:行号`（基于上述 commit 的工作树）；锚不到的显式标 **[假设]**。

---

## 1. 架构形态总则（凌驾于各节的组合约束）

按 HANDOFF §三 架构形态约束与 SSOT「调用模型」节：

1. **原子工具 + 类型化产物接口，禁向导式流水线。** M1 各阶段实现为独立可调用的服务端工具，接口是类型化产物，不是流程步号：

| 原子工具（M1） | 输入产物 | 输出产物 | 挂在产物上的牙 |
|---|---|---|---|
| ingest（接入+定档） | URL + 凭证引用 + 需求文档 | `intake_profile`（终点+档位确认卡数据） | 溯源/能力锁（sot_gate 前置字段） |
| clarify（澄清） | intake_profile + 需求正文 | `business_frame`（R 规则 + confirmed_facts） | sot_gate |
| analyze（测试分析） | business_frame | `test_analysis`（Example Map + 消费面盘点） | downstream_gate |
| draft-cases（用例生成） | test_analysis | `case_draft` | validate_cases（格式族 + 覆盖族扩展） |
| adopt（采纳冻结） | case_draft + 工作台勾选 | `ApprovedCaseSet`（双 digest 冻结） | cases_gate + digest 计算 |
| compile（编译） | ApprovedCaseSet | `AutomationBundle`（pytest+Playwright 工程，M1 单轨） | compile-gate 最小版（schema/collect/case 映射/凭据扫描） |
| execute（执行） | AutomationBundle + 环境引用 | `run_receipt` + evidence-bundle 素材 | 执行红线五条 + 写确认 |
| project（结论投影） | evidence-bundle | `verdicts.jsonl`（派生，禁手写） | evidence_gate + bundle_to_verdicts + mechanical_check(M1/M3/M5 + M4 降级) |
| coverage（覆盖收口） | verdicts + test_analysis | `coverage_ledger`（覆盖图数据） | gap 无解释不 done |

2. **任意前缀 = 合法交付**：走到 clarify 或 adopt 即止都是完整交付，产物过对应闸即可交（DoD「部分旅程交付」条实证此点）。
3. **牙挂产物不挂流程位**：每个闸的触发条件是「该类型产物产出」，与后续是否继续无关。
4. **定档分流**：ingest 按 风险×复杂度 出确认卡（checklist / 标准 / 深）；M1 至少实现 checklist 档与标准档分流，深档以 DoD 全链实证。
5. 批次（run/journey 实例）是唯一状态对象；聊天与工作台是它的两个投影（见 §4）。

---

## 2. 拓扑四项（HANDOFF §四，每项两案比价 + 建议，随设计闸拍板）

### 背景事实（全部当版实证）

- DT 应用跑在容器（`deeptutor@127.0.0.1:3782`，`docker ps` 实跑）；test-partner 服务在**宿主机**：MCP 面经 `http://host.docker.internal:3790/mcp` 挂进容器，配置面绑 `127.0.0.1:3789`（`extensions/test-partner/server/main.py:6-7`）。
- 现有执行器（HTTP 解释轨）就在宿主机进程内跑（`extensions/test-partner/server/execute.py` 全文，被 main.py 的 MCP 工具直接调用）。
- Playwright 当前**不在** DT 栈中：`Dockerfile`、`Dockerfile.runner`、`requirements*.txt` 及 `extensions/test-partner/**/*.py` 中零 playwright/chromium 引用（rg 实扫，零匹配）。
- 白月SMS 靶机装在用户本机，监听 `127.0.0.1:80`（可改端口如 8047，安装页原文，见 §9）。
- 容器与宿主的 localhost 语义割裂已有既成惯例：`docker-compose.yml:17`（"Use host.docker.internal instead of localhost"）。

### 2.1 Playwright 浏览器装在哪

| 维度 | 案 A：DT 容器内加装 | 案 B：宿主机 sidecar（推荐） |
|---|---|---|
| 上游触点 | 动 `Dockerfile`（护栏 6 明示为跨上游同步风险项，占触点预算） | **0 上游触点**（落在 test-partner 宿主服务侧） |
| 镜像体积 | chromium + 系统依赖，镜像膨胀数百 MB，每次上游同步重建成本增 | 无镜像影响；宿主一次性 `playwright install chromium` |
| 靶机可达 | 容器内 127.0.0.1 ≠ 宿主 127.0.0.1，必须走 host.docker.internal 映射（红线复杂化，见 2.2） | 与白月SMS 同 host，`127.0.0.1` 字面直达 |
| trace 可开性 | trace 落容器卷，用户开 viewer 需挂载/拷出 | trace 直接落宿主盘，viewer 一键开（见 2.3） |
| 进程治理 | 容器内 chromium 泄漏难被宿主 supervisor 观测 | 与 test-partner 既有宿主进程治理同域（`server/gateway/supervisor.py` 先例） |
| 新增负担 | 上游同步冲突风险持续存在 | 新增宿主进程生命周期治理（由 2.4 资源纪律覆盖） |

**建议：案 B（宿主机 sidecar）**。执行本来就发生在宿主（execute.py 现状），Playwright runner 作为 test-partner 宿主服务的兄弟能力落地；不动 Dockerfile，护栏 6 的 ≤5 处上游触点预算完整留给工作台/聊天面。被否决案 A 的复活条件：未来 DT 多用户云部署（无"用户本机"概念）时重开。

### 2.2 靶机可达与跨 host 红线口径

现行红线实现（当版实证）：`execute.py:47-49` —— 渲染后 URL 的 `host:port` 与 base_url 不一致 → 整条 skip 绝不发（`_host_key`，`execute.py:199-211`）；不跟随重定向 `follow_redirects=False`（`execute.py:49,564`）；登录请求同样过跨 host 闸（`execute.py:544-551`）。

| 维度 | 案 A：执行器在宿主，字面等价（推荐） | 案 B：执行器在容器，引入 host 别名映射 |
|---|---|---|
| base_url 写法 | `http://127.0.0.1:8047`，用户视角与执行视角同一字符串 | 用例写 `host.docker.internal:8047`，用户浏览器访问却是 `127.0.0.1:8047`，双写法 |
| 红线判定 | 字面 `host:port` 相等，零映射层 | 需"127.0.0.1 ↔ host.docker.internal 视为同 host"映射表，红线加层 = 静默失效面 |
| 红线测试迁移 | 现有测试语义原样保留 | 每条映射需配套"映射外仍拒发"测试，测试面翻倍 |

**建议：案 A**（与 2.1 案 B 联动）。**红线口径显式定义（无论最终选案，此口径入 ADR）**：

1. 红线判定基于「有效 host 等价类」：**默认等价类 = 单元素**，即渲染后 URL 的 `host:effective_port` 必须与该批次环境 base_url 的 `host:effective_port` 字面相等（沿用 `_host_key` 语义：http 补 80 / https 补 443，`execute.py:199-211`）。
2. 若未来拓扑需要映射（容器执行场景），映射表必须显式配置在环境配置（`config/environments.json`，`server/gateway/config.py:372` 既有金库位），**禁止代码内置隐式别名**；且每条映射必须带一条「映射外 host 仍拒发」的红线测试才允许生效。
3. **Playwright 面的红线延伸（新增，UI 执行独有）**：页面主文档导航目标 host 必须 ∈ 等价类，越界导航 = 该 case skip；页面发起的子资源/XHR 出站默认按 Playwright route 拦截策略处理——同等价类放行，第三方域 abort（M1 靶机为本地自包含系统，预期第三方请求为零；出现即记录并 abort）。重定向：API 轨保持 `follow_redirects=False`；UI 轨浏览器重定向不可禁，故以「重定向落点 host 仍须 ∈ 等价类，否则中止并判 skip」为口径。
4. 五条执行红线（跨host拒发 / 变量残留skip / 凭据零回显 / 无断言不算过 / 不跟随重定向）在 UI 轨各有对应实现与测试，禁止因拓扑改写静默失效（护栏 5）。变量残留语义沿用 `execute.py:50,681`；凭据零回显沿用 `_scrub` 出站门（`execute.py:53,171-178`）。

### 2.3 trace 落盘与 viewer 打开路径

事实：test-partner 数据目录 `extensions/test-partner/data/test-workbench/{deliveries,drafts}`（ls 实证）；`data/` 已在仓库 `.gitignore:9` 内（`git check-ignore` 实证命中 `data/`），落仓内不进 git。Playwright trace 查看方式 = 本地 `playwright show-trace <trace.zip>`；trace.playwright.dev 需上传外网，**排除**（trace 内含请求/凭据回放，违反凭据零外泄精神）。

| 维度 | 案 A：仓内 data 目录（推荐） | 案 B：用户目录（~/.test-partner/runs） |
|---|---|---|
| 路径 | `extensions/test-partner/data/test-workbench/runs/<run_id>/<case_id>/trace.zip` | `~/.test-partner/runs/...` |
| 与既有惯例 | 与 deliveries/drafts 同域，gateway 已能读写该树（`server/gateway/config.py` 同模式） | 新增第二数据根，发现性差 |
| git 卫生 | `.gitignore:9` 已覆盖 | 天然仓外 |
| 备份/迁移 | 随 data 目录一体 | 分散 |

**建议：案 A**。**用户打开 trace 的可执行操作路径（DoD 判据，两条并列缺一不可）**：

1. 工作台结果页每条已执行 case 行提供「打开 trace」按钮 → 调 gateway 本地接口（127.0.0.1:3789 域内）→ gateway 在宿主起 `playwright show-trace <绝对路径>` 子进程弹出本地 viewer（进程按 2.4 纪律登记回收）。
2. 同一行同时显示可复制命令 `python -m playwright show-trace "<绝对路径>"` 作为降级路径（gateway 未起/按钮失效时用户仍可自助）。

### 2.4 浏览器并发上限与进程回收（吸收 LOOP_SPEC §3 资源纪律）

吸收源：`test_agent/flow/LOOP_SPEC.md:80-86`（资源纪律：编排层不 per-cycle 新建 browser、跨面 ≤2 / 面内串行、子进程登记 PID + finally 清理、退出统一 reap、资源硬上限 ≠ 成本预算、trace 增量 append 崩溃可 resume）。DT 是多用户 Web 服务，此项不许弃（HANDOFF §四.4）。

| 维度 | 案 A：常驻浏览器池跨 run 复用 | 案 B：per-run 生命周期 + 周期 reap（推荐） |
|---|---|---|
| 性能 | 免启动开销（秒级） | 每 run 冷启动一次浏览器（白月SMS 规模无感） |
| 泄漏面 | 常驻进程 = 长期泄漏累积面，池状态污染跨 run 传染 | run 结束 finally 必杀，泄漏窗口 = 单 run |
| 状态隔离 | storageState 串 run 风险需额外清理逻辑 | 天然隔离 |
| 崩溃恢复 | 池损坏影响后续所有 run | 单 run 自愈 |

**建议：案 B**，具体纪律（入 ADR，逐条可机检）：

1. **并发顶：同时运行 run ≤ 2；run 内 case 串行**（对齐 LOOP_SPEC「跨面 ≤2、面内串行」；M1 的"面"映射为 run）。第 3 个执行请求进队列排队，不并发争抢。
2. **run 内复用**：一个 run 起一个浏览器实例（一个 browser context per case），run 内各 case 复用该实例——对齐「不 per-cycle 新建 browser」；run 结束 `try/finally` 关闭。
3. **PID 登记**：每个浏览器/viewer 子进程启动即登记（run 目录 `pids.json`，append-only），finally 清理时销记；只杀登记过的 PID，绝不按进程名广杀。
4. **周期 reap**：gateway supervisor（`server/gateway/supervisor.py` 既有守护先例）周期扫描登记表，发现宿主上超过 TTL 且 run 已终结的登记 PID → reap 并记日志。
5. **资源硬上限**：登记 chromium 进程数 / 单进程内存超阈 → 暂停新 case、reap、优雅中止该 run 判 BLOCK（不是成本预算，是防崩底线）。
6. **trace/收据增量落盘**：每 case 执行完即 append run 目录，崩溃后已完成 case 不重跑、run 可 resume。

---

## 3. 待用户拍板项（HANDOFF §七 第 0-4 项，拍板卡形态：问题 / 选项 / 推荐 / 影响）

### 拍板卡 0 · 交互形态与 M1 卡片形态

- **问题**：SSOT「交互呈现」节四原则（单一状态双视图 / 动作分流 / 卡片非持久层 / 产物账本 stepper）在 M1 的最简落地形态。
- **勘察结论**：DT 现版聊天渲染能力**足以支撑**，无需降级（详见 §4，逐条带代码锚）。
- **选项 A（推荐）**：复用 `ask_user` 机制做全部人闸卡（定档确认 / 澄清 / 采纳流转 / 写确认），产物呈现用结构化 Markdown（GFM 表格）+ 工作台链接锚；**不新建任何聊天卡片组件**。stepper = 批次页头一条产物状态栏（产物存在性推导亮灭）。
- **选项 B**：新建聊天侧富卡片组件体系（专用批次卡 / 采纳卡等，沿 `ChatMessages.tsx:326-370` capability 结果卡模式扩展），聊天内完成逐条勾选等复杂交互。
- **推荐理由**：选 A 时 `ask_user` 的已答/过期态语义（`AskUserOptions.tsx:71-73`：仅最新未答卡可交互，旧卡强制 resolved）机械满足「卡片非持久层、过期卡片不产生第二真相」，聊天侧零组件开发。
- **影响**：选 A 则 M1 前端增量 ≈ 批次页 + 结果表 + 覆盖图三个工作台面，复杂采纳必须去工作台完成（聊天只做流转确认）；选 B 则聊天内交互表现力强，但新增组件挤占护栏 6 触点预算、M1 工期扩面，且卡片自持状态风险（双真相 = SSOT 明示唯一死法）需额外机制防守。

### 拍板卡 1 · 工作台新增面挂 `(workspace)` 还是 `(utility)` 路由组

- **事实**：现有 test-workbench hub 在 `(utility)`（`web/app/(utility)/test-workbench/page.tsx:1`）。两路由组的**唯一实质差异是 `UnifiedChatProvider`**：`(workspace)` layout 有（`web/app/(workspace)/layout.tsx:5,14-17`），`(utility)` 没有（`web/app/(utility)/layout.tsx:1-4,11-17`）。`CapabilityGate` **不是差异**——两个路由组的 layout 都渲染它（`(utility)/layout.tsx:3-4,14` 同样 import 并包裹 children）；且 capability 门控只对 `ROUTE_CAPABILITIES` 登记过的前缀生效，未登记路径 always available（`web/lib/capability-routes.ts:9-11,34-39`），该表中没有 `/test-workbench`（`capability-routes.ts:21-28`）——**批次页无论挂哪组都不需要 capability 注册，此触点不存在**。
- **选项 A（推荐）**：M1 新增「批次页」（stepper + 结果表 + 覆盖图）挂 `(workspace)`；既有 test-workbench hub 留在 `(utility)` 不动。
- **选项 B**：全部继续挂 `(utility)`，「继续对话」按跳转 + query 参数携批次 id 实现。
- **推荐理由**：SSOT 双向跳转要求「批次页『继续对话』回聊天携批次上下文」——`(workspace)` 有 `UnifiedChatProvider`，此能力是 layout 原生的；`(utility)` 下做等价功能要跨路由组传状态，属绕行。
- **影响**：两案的真实上游触点相同 = **导航注册侧**（sidebar 菜单项新增，`web/components/sidebar/SidebarShell.tsx:113-121` 已有 `/test-workbench` fork 注释先例可循，两案同付、计入护栏 6 预算）。选 A 额外获得聊天上下文原生能力；选 B 省不了触点，「继续对话」体验降级且 M2 大概率返工迁移。

### 拍板卡 2 · 鉴权档位

- **事实**：每用户隔离**已有拍板**（decision `0009-per-user-isolation.md`：2026-08-07 用户原话「各看各的」，鉴权档定为普通登录档 `dependencies=_auth` 非管理员档，`0009:21-22`）；但其**实现未落地**——`deliveries/` 仍单一平铺、`config/environments.json`/`secrets.env` 仍全局一份、gateway 整体假设单人使用（`0009:30,47-53` 自述 + data 目录 ls 实证）。HANDOFF §七.2 说的「尚未落地是待决」指实现层。
- **选项 A（推荐）**：M1 新增后端路由一律挂 `dependencies=_auth`（遵守 0009 已拍档位与「router 绝不裸挂」硬约束，`0009:24-26`），**数据分区（deliveries / environments 按用户隔离）延到 M2**；M1 批次产物 schema 预留 `owner` 字段，避免迁移时补造历史归属。
- **选项 B**：M1 一步做完数据分区。
- **推荐理由**：M1 靶是用户本机单人操作的白月SMS，隔离收益为零；分区改造被 0009 自己标为「最大改造面」，塞进 M1 违反细线优先。
- **影响**：选 A 则 M1 存在已知债（与 0009 待办清单同项，`0009:55-58`），M2 需带迁移口径；选 B 则 M1 工期显著扩面且触碰 gateway 单人假设重构。**注意本卡不改 0009 的决定，只拍「实现排期」。**

### 拍板卡 3 · 执行拓扑四项

- **问题与选项**：见 §2 逐项两案比价。
- **推荐打包**：宿主 sidecar（2.1B）+ 字面 host 等价类红线（2.2A）+ 仓内 data 落盘与双路径 viewer（2.3A）+ per-run 生命周期与周期 reap（2.4B）。四项互相咬合（sidecar 使字面等价类与本地 viewer 成为可能），**建议整包拍板**；拆包改选任一项需回看其余三项的联动格。
- **影响**：整包接受 = 0 Dockerfile 触点、红线零映射层、DoD trace 判据可操作；改选容器方案则四项全部重推且护栏 6 预算吃紧。
- **风险栏（AC-6 实证缺口，显式）**：白月SMS 当前**仅证到文档页可达**（安装页/需求文档/网盘分享页 HTTP 200），**本机可安装可运行为零实证**——二进制未下载、未起过服务。因此设本卡附带硬前置：**施工授权的第一道硬前置 = 施工 Day-1 装起白月SMS 并留登录成功证据（截图 + `http://127.0.0.1:8047` 响应）；装不上当天上浮，且当场提名 2 个候选备选靶（可一键起 + 有公开需求文档）等用户换靶拍板。** 此前置不通过，M1 其余施工不得开始。

### 拍板卡 4 · ApprovedCaseSet schema 冻结

- **问题**：`approved-caseset-schema-draft.json`（v0.1.0-draft）是否冻结为 v1。
- **选项 A（推荐）**：随本设计闸一并冻结为 v1.0.0；冻结后任何字段/序列化/哈希规则变更必须升 `schema_version` 并提供迁移说明，禁止原地改语义。
- **选项 B**：本闸只批结构方向，冻结推迟到 M1 首条用例真实走通采纳链后（用实数据回验 schema 再锁）。
- **附带子项（须一并拍）**：草案含一处**超出 HANDOFF「双 digest」措辞的扩面**——caseset 级 `source.content_digest`（需求正文快照摘要，防 oracle 出处漂移），是 `source_case_digest`/`oracle_digest` 之外的第三个摘要。接受则保留，不接受则删除该字段（schema 内已标注待拍板）。
- **影响**：选 A 则 digest 计算规则即刻不可变（变 = 全部存量 digest 作废），施工可并行开始；选 B 则施工期 schema 仍可能漂移，采纳链联调需预留一轮返工。两案下派生产物（pytest 工程、cases_exec）都永远只带 digest 回指，禁反写（architecture-analysis §3 契约）。

---

## 4. DT 聊天富卡片渲染能力勘察（当版实证）+ M1 卡片最简形态

### 4.1 能力盘点（逐条代码锚）

| 能力 | 证据锚 | M1 用途 |
|---|---|---|
| Markdown 全量渲染：ReactMarkdown + remarkGfm（表格）+ mermaid + KaTeX + 受控 rehypeRaw | `web/components/common/RichMarkdownRenderer.tsx:5-6,40-43,730-742` | 产物摘要卡（R 规则表 / 用例清单表 / 覆盖表）直接用 GFM 表格 |
| 保留链接 scheme → 内联卡片：remark 插件把 `attachment:` href 拦成 `InlineFileCard` 组件，scheme 在 URL 白名单内且不可导航 | `web/components/common/InlineFileCard.tsx:12-24,36-38` | 既有先例证明「链接语法 → 卡片组件」扩展点存在；M1 产物文件（cases.md 等）可用它内联展示 |
| **`ask_user` 交互卡**：assistant 轮暂停 → 渲染选项 chips + 自由文本 → `onSubmitUserReply` 经统一 WebSocket 回传 → 同轮 resume（无新用户气泡）；v2 支持多问题结构化 `answers[{questionId,text}]` | `web/components/chat/home/ChatMessages.tsx:303-318,372-397`；`web/components/chat/home/AskUserOptions.tsx:82` | **人闸卡的全部机制现成**：定档确认卡、澄清卡、采纳确认卡、写确认卡 |
| 卡片时效纪律：只有最新未答卡可交互，旧卡被 `ask_user_resolved` progress 事件强制置 resolved（已答视图） | `AskUserOptions.tsx:54-73,114,150-163` | 机械满足 SSOT「卡片非持久层 / 过期卡片显示已处理」，零新代码 |
| capability 结果卡：按 `msg.capability` + `resultEvent.metadata` 分支渲染专用卡（research outline / quiz / math_animator / visualize） | `ChatMessages.tsx:326-370` | 富卡片组件的既有先例与挂点；M1 不新建，M2 若要专用「批次卡」有现成模式 |
| 消息附件文件卡（可点开 Viewer） | `ChatMessages.tsx:164-176,193-257` | 交付物（用例文档、报告）作为生成文件挂消息，点开即看 |

**结论：DT 聊天富卡片渲染能力对 SSOT「交互呈现」节要求为「足够」，不触发信封 escalation 第 2 条（降级形态）。** 尤其 `ask_user` 的同轮 resume + 结构化多问答 + 旧卡强制 resolved 三件套，正是「闸时刻遥控器、非持久层」的机制化身。

### 4.2 M1 卡片最简形态方案

1. **判断类动作（人闸）全部走 `ask_user`**：定档确认（三档选项 chips）、澄清（多问题 v2 answers）、采纳确认（勾选在工作台，聊天卡只做「去工作台采纳 / 已采纳继续」的流转确认）、写确认（写操作逐项确认，chips = 允许/跳过）。点击写回批次状态（服务端），卡带「在工作台打开」`attachment:`/URL 锚。
2. **产物呈现**：结构化 Markdown ——R 规则用 GFM 表格、Example Map 用列表分组、覆盖摘要用表格 + 批次页链接；文件级产物走消息附件卡。
3. **禁项**：不新建聊天侧持久卡组件；不在卡片内自持状态（状态唯一在批次对象，`ask_user` 卡答案本身也经 WebSocket 落服务端）；机械类动作（重跑/导出/筛选/开 trace）只出现在工作台按钮，不做成聊天卡。
4. **[假设]** DT 聊天 agent（服务端）可在 test-partner 能力会话中调用 `ask_user` 工具——前端渲染与回传链路已实证（上表），但 test-partner 的 partner 会话是否已接 `ask_user` 工具的服务端发射面未逐层核验；若 M1 施工发现该会话类型未接，补接属 extensions 侧改动（依据：`ChatMessages.tsx:372-378` 显示 ask_user 是通用 events 流语义、非某 capability 专属，故判低风险）。

---

## 5. DoD 判定归属（HANDOFF §五 逐条：CI 判 / 手动集成跑判）

判分原则（HANDOFF §五 末条）：牙/schema/蒸馏 golden = CI（全离线）；执行闭环 = 手动集成跑 + 证据目录留档。

| # | DoD 条目 | 归属 | 说明 |
|---|---|---|---|
| 1 | 靶=白月SMS 本地安装可访问 | 手动集成 | 开工实证项；页面可达与安装形态已证（§9），本机装起+登录截图留档 |
| 2 | 输入=L1+L2+官方需求文档，全程不读被测源码 | 手动集成 | 流程纪律，run 留档自证（输入清单落 run 目录）；靶机运维权与凭证阶梯两轴分离照 HANDOFF 口径 |
| 3 | ≥8 条用例走完整链（接入→…→覆盖图） | 手动集成 | 端到端主证据；各环产物过闸的闸本身是 CI 项（见 7/#牙） |
| 4a | 故障注入类1：SUT 真故障 → 判 FAIL/BLOCK | 手动集成 | 停服务/改本地数据需要活靶机 |
| 4b | 故障注入类2：闸链变异（篡改 bundle/删证据/手写 verdicts）→ 被拦 | **CI** | 纯闸行为，离线构造篡改样本即可判（evidence_gate / bundle_to_verdicts 向量）；另在手动集成中真跑一次复证 |
| 4c | 故障注入类3：零假绿（200 但业务未生效）→ 业务层断言判 FAIL | 手动集成（活靶）+ **CI**（assert_layer retcode/discriminating 金标向量离线过） | 双层：机制金标 CI 判，端到端手动判 |
| 5 | verdict 由闸链投影生成、禁手写 | **CI**（bundle_to_verdicts 测试向量逐条过）+ 手动集成抽查真 run | 牙移植验收 = 原件测试向量集逐条过（护栏 2） |
| 6 | trace 可按 §2.3 路径在 viewer 逐步回看 | 手动集成 | 按钮路径 + 命令降级路径各验一次 |
| 7 | 凭据零落盘由机械扫描证明（覆盖自定义高熵串） | **CI**（扫描器自身 + 金标样本离线判）+ 手动集成（真 run 产物全量扫描） | 报告注明本靶凭据为公开值，证明机制在场不证强度 |
| 8 | 同环境连续两次执行 verdict 一致（无 healer 无豁免） | 手动集成 | 需要活靶两趟 |
| 9 | 覆盖图工作台可见，gap 显式原因 | 手动集成（UI 目检）+ **CI**（coverage_ledger 生成逻辑 golden） | |
| 10 | 用例集删除后可从 ApprovedCaseSet 重编译再跑通 | 手动集成 | 编译器确定性子集（同输入同输出 hash）可加 CI golden |
| 11 | 部分旅程交付演示（分析/采纳即止 + checklist 档一次） | 手动集成 | 对应产物闸（sot_gate/downstream_gate）离线部分 = CI |
| 12 | 判定分层本身（本表） | 设计产物 | 本表即交付，verify_gate 最小版检查其在场 |
| 13 | schema/digest 计算、蒸馏技能 golden、牙移植向量 | **CI** | digest 规范化序列化用金标样本离线判 |

---

## 6. 吸收清单（护栏 2 指定的牙 + LOOP_SPEC 纪律，验收方式）

M1 移植的牙（全部：从 test_agent 抄**测试向量**不抄代码，逐条过原件向量 = 移植验收）：

| 牙 | 原件位置（采石场，只读） | M1 形态 |
|---|---|---|
| sot_gate / downstream_gate / cases_gate | `test_agent/kit/gates.manifest.json`（闸清单信任契约）+ `.agents/agents/test-engineer/scripts/` | 服务端确定性工具 |
| validate_cases 覆盖族扩展 | DT 现有 `extensions/test-partner/server/case_validate.py`（格式族 **E01–E15 / W01–W05**，`docs/HANDOFF.md:59` 明示该文件为规则唯一权威；文件内实定义含 E00 共 E00–E15，rg 实扫）+ test_agent 覆盖语义 | 现有工具加覆盖规则族；注意存量规则面比早期口径（E01-E09/W01-W03）大近一倍，新增覆盖族编号须接在 E15/W05 之后且不与存量语义重叠，工作量估算按 21 条存量规则回归为底 |
| compile-gate 最小版 | architecture-analysis §3 编译工序 6 | schema/静态检查/collect/case 映射/凭据扫描 |
| evidence_gate | `.agents/agents/test-engineer/scripts/evidence_gate.py`（gates.manifest.json cmd 实指） | 结论账本闸 1 |
| bundle_to_verdicts | `test_agent/kit/tools/bundle_to_verdicts.js` | 唯一投影器，verdicts 禁手写 |
| mechanical_check M1/M3/M5（M4 降级=实例指纹在场） | `test_agent/kit/f9/mechanical_check.js`（自测 12/12） | 收口阻断闸 |
| assert_layer retcode 闸 + discriminating 闸 | `test_agent/kit/tools/assert_layer.py:53-59,72-73` | 防假绿机械支撑，必入 M1 |
| LOOP_SPEC §3 资源纪律 | `test_agent/flow/LOOP_SPEC.md:80-86` | §2.4 设计整条吸收 |

技能蒸馏（req 旅程方法）：Example Map / 消费面盘点 / 澄清纪律，每个带 golden example + 回归锚（护栏 2）；覆盖面允许裁剪但必须显式列「本次未蒸馏条目 + 后果」（护栏 3）。

---

## 7. 质量属性场景（设计靶，可测四元组）

| ID | 刺激 | 环境 | 响应 | 量度 |
|---|---|---|---|---|
| QS-1 安全/红线 | 用例渲染出等价类外 host 的 URL（含 UI 导航与重定向落点） | 宿主 sidecar + 白月SMS 127.0.0.1 | 拒发/中止并判 skip，理由可见 | 出站跨 host 请求数 = 0；红线测试向量 100% 过 |
| QS-2 防假绿 | 注入「接口 200 但业务未生效」故障 | 活靶手动集成跑 | 业务层断言判 FAIL，retcode 层不放行 | 类3 注入样本 100% 判 FAIL；assert_layer 金标向量 100% 过 |
| QS-3 资源纪律 | 连续 3 个 run + 1 次人为中途崩溃 | 宿主 sidecar，supervisor 在岗 | run 后浏览器进程全回收，崩溃 run 可 resume | 执行后登记 PID 存活数 = 0；chromium 进程数回基线；reap 日志可查 |
| QS-4 确定性 | 同环境重复执行同一 ApprovedCaseSet 两次 | 无 healer、无用例变更 | verdict 完全一致 | 两趟 verdict diff = 0 |
| QS-5 单一真相 | 用户先点旧 ask_user 卡再看工作台 | 聊天与批次页同开 | 旧卡为 resolved 态不可交互，状态以批次对象为准 | 卡片操作后聊天/工作台状态 diff = 0；旧卡交互入口数 = 0 |
| QS-6 可审计 | 手写/篡改 verdicts 或删证据文件 | CI 闸向量 + 手动复证 | 闸拦截并 BLOCK | 类2 变异样本 100% 被拦 |

---

## 8. ADR 草案（proposed，随设计闸裁决；正文在任务运行目录）

| ADR | 主题 | 位置 |
|---|---|---|
| ADR-M1-01 | Playwright 执行拓扑：宿主 sidecar + 字面 host 等价类红线 | `agent-lab-runs/deeptutor-absorb-m1-20260810/adr/ADR-M1-01-host-sidecar-topology.md` |
| ADR-M1-02 | trace 落盘与 viewer 双路径 + per-run 浏览器生命周期与 reap | `agent-lab-runs/deeptutor-absorb-m1-20260810/adr/ADR-M1-02-trace-and-process-discipline.md` |
| ADR-M1-03 | M1 聊天卡片形态：复用 ask_user，不建新卡组件 | `agent-lab-runs/deeptutor-absorb-m1-20260810/adr/ADR-M1-03-chat-card-minimal-form.md` |

用户拍板通过后，ADR 定稿迁入 `extensions/test-partner/docs/decisions/`（0015 起），迁移动作在施工授权内执行，本稿不代迁。

---

## 9. 白月SMS 靶机可用性实证（AC-6）

- 安装文档页 `https://www.byhy.net/prac/pub/info/bysms/`：**HTTP 200，6268 bytes**（curl 实测 2026-08-10，落档 scratchpad `bysms.html`）。
- 安装形态（页面原文摘录）：百度网盘下载 `bysms.zip` → 解压 → 双击 `runserver.bat` 启动（Windows）；**单 Go 二进制 `bysms.exe`，零外部依赖（无 Python/数据库安装步骤）**；默认监听 `0.0.0.0:80`，80 被占时改 `runserver.bat` 为如 `bysms.exe 0.0.0.0:8047`；访问 `http://127.0.0.1[:8047]`；管理员账号 `byhy / 88888888`（公开教学值）。
- **官方需求说明文档可达**：安装页内链「该系统的需求说明文档」→ `https://www.byhy.net/py/django/req_1/`，**HTTP 200，5587 bytes**（curl 实测）；内容为《BYSMS系统 需求1.0》——管理员/销售员双角色、订单/客户/药品管理、权限差异（销售员仅本人订单）、登录 3 次错锁 1 小时等真实业务规则，**作 M1 oracle SOT 成立**。
- 下载渠道：百度网盘分享链 `https://pan.baidu.com/s/1rnWhuroIMNebStzpwLjsTA`（HTTP 200 可达）。
- **实证边界（显式）**：以上证到的是**文档可达 + 安装形态清楚（单二进制零依赖）**；**本机可安装可运行为零实证**（二进制未下载、未启动）。该缺口不作假设吞掉，已升级为**施工授权硬前置**：施工 Day-1 装起白月SMS 留登录成功证据；装不上当天上浮 + 当场提名 2 个候选备选靶（见拍板卡 3 风险栏）。
- 结论：设计层判定**不触发换靶上浮**（文档/需求 SOT/安装形态三项已证），运行可行性留待 Day-1 硬前置裁决。运行时端口建议：直接改用 8047（Windows 上 80 常被占且需绕权限），base_url 固定写 `http://127.0.0.1:8047`。

---

## 10. 假设清单（汇总，锚不到当版证据的全部条目）

1. §4.2-4：test-partner partner 会话已/可接 `ask_user` 服务端发射面（前端链路已实证，服务端会话接线未逐层核验）。
2. §2.4-4 [假设]：`server/gateway/supervisor.py` 的守护线程模式可承载周期 reap 任务（文件存在已证，其扩展成本未深读——若不适配则 reap 独立为 sidecar 自带定时器，纪律不变）。

以上任一假设被证伪不推翻主结构，均有降级路径且已写明。

注：白月SMS「本机可安装可运行」**不在**本假设清单——它已从假设升级为**施工授权硬前置**（Day-1 装起留证，装不上当天上浮 + 提名 2 候选靶），见拍板卡 3 风险栏与 §9 实证边界。

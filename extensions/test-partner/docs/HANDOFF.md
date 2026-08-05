# 交接说明 · test-partner（截至 2026-08-05）

给接手的 agent / 人。读完这一份 + `docs/decisions/0007-pivot-to-real-fork.md` 就能接上。
**不要从头读全部历史文档**——本文标注了哪些还有效、哪些已被推翻。

---

## 1. 一句话现状

一个能用的测试助手已经跑通并被用户部分验收，但**形态错了**（变成了 DeepTutor 之外的第二个前端）。
用户已拍板改为**真二开（fork DeepTutor）**，接手方的任务是把已交付的服务端能力搬进 fork，
前端按 DeepTutor 设计系统重写，做成一个产品一个入口。

---

## 2. 仓库与基线

| 项 | 值 |
|---|---|
| 本地 | `D:\Test_Coding\test-partner` |
| 远端 | https://github.com/lsq1030757028/test-partner （**私有**，2026-08-05 建） |
| 分支 | `main`（单分支直提，49+ 提交） |
| 回滚基线 | tag **`v0.8.0-preforke`** —— 搬迁前最后的可用状态（含登录换新） |
| CI | **无**（决策 0008 待办） |
| 测试 | **667 例**离线（`python -m pytest tests/ -q`），全绿 |
| 服务端版本 | 0.8.0 |

相关仓库：`blackbox-test-agent`（已冻结，资产库）、`tapd-capability`（TAPD 语义层，另一条线）。

---

## 3. 运行时（当前形态，搬迁后会变）

```
DeepTutor（Docker，v1.5.8，:3782，容器名 deeptutor，数据卷 deeptutor-data）
   ├─ MCP 挂载 test-partner  → 宿主机 :3790（7 个工具）
   └─ MCP 挂载 tapd          → 宿主机 :3795（官方 mcp-server-tapd 8.0.80，2 个只读工具）

宿主机常驻：scripts\start_server.cmd = 能力网关（一个进程两个面）
   ├─ MCP 面   0.0.0.0:3790/mcp
   └─ 配置页   127.0.0.1:3789（五 tab，含工作台）
```

**运维已知项**（搬迁后仍可能踩）：
- 网关重启后 DeepTutor 聊天侧的 MCP 会话不会自动跟进，要拨一次条目开关（配置页「概览」有按钮）。
- DeepTutor 聊天输入框回车不发送，要点发送键。
- DeepTutor 容器必须 `--restart unless-stopped`（曾用 `--rm` 跑，Docker 一重启就消失）。
- MCP SDK 2.0 已移除 `mcp.server.fastmcp`，用 `mcp.server.MCPServer`；`.cmd` 脚本必须纯 ASCII。

---

## 4. 已交付资产（搬迁时按此清单处置）

### 4.1 服务端能力 —— **随迁，不重写**（决策 0007 第 4 条）

| 模块 | 能力 | 备注 |
|---|---|---|
| `server/har_parse.py` | HAR 体检：端点归并、业务域、鉴权识别、可回放判档、**脱敏**、请求样例 | 逻辑移植自 blackbox，附对照表在模块 docstring |
| `server/case_validate.py` | 用例确定性校验 E01–E15 / W01–W05 | **规则的唯一权威**，SKILL.md 与它同源 |
| `server/postman.py` | Postman Collection v2.1 导出（Apifox 可导入） | 断言翻 pm.test |
| `server/delivery.py` | 产物落盘 + 收据 + `cases.json`（结构化用例） | 收据记指纹/校验/sha256 |
| `server/execute.py` | 用例执行器 + **四条安全红线** + **登录换新**（`auth=none/env/login`） | 见下，红线必须原样保留 |
| `server/args_tolerance.py` | 工具入参宽容解包（小模型传参形状不稳的兜底） | 修复优于拒绝 |
| `server/gateway/*` | 配置中心 / 子服务管家 / DeepTutor 客户端 / 工作台后端 | 配置中心与管家随迁；webapp 前端重写 |

**执行安全红线（`server/execute.py` 模块 docstring 是权威，不得削弱）**：
① 只向 `base_url` 同 host 发请求，且整轮不跟随重定向；
② 变量未提供、渲染后残留 `{{var}}` 的用例跳过不发；
③ 凭据值不进返回值/报告/日志（响应体回显凭证也会被换回 `{{名字}}`）；
④ 无 request 块、有请求块但无断言，一律跳过——不拿"发出去了"冒充"通过"。

### 4.2 前端 —— **随迁重写**（用 DeepTutor 设计系统）

`server/gateway/webapp.py` 里的内嵌单页（原生 JS，五 tab：概览/连接与凭据/测试环境/测试伙伴/工作台）。
功能是对的、交互已验收过，**但技术形态要换**。搬迁时保留的是交互设计，不是这份代码。

### 4.3 聊天侧资产 —— 保留

- `skills/har-testing/SKILL.md`、`skills/req-testing/SKILL.md`（已装进容器 `/app/data/user/workspace/skills/`）
- `partner/SOUL.md` + `partner/README.md`（伙伴人格与上手；用户自己的伙伴是 `test-agent`，**不要动**）
- `tapd-runtime/`（官方 mcp-server-tapd 的钉版运行时，`PINNED.md` 有 sha256 与升版纪律）

---

## 5. 用户的硬性偏好（违反必被打回，来自多轮实测反馈）

1. **不要会话式固定工作流**：固定流程要有真界面，不能靠聊天里堆步骤。
2. **不要长页面**：按类型分 tab。
3. **不要两个前端**：一个产品一个入口（这就是 0007 的由来）。
4. **凭据不经聊天**：一律在本机配置里配一次，执行时按名引用。
5. **不要机器字段外露**：conf_id/queryToken/内部错误码一律转译成业务语言。
6. **链接带 ID 就直取那一条**，不拉列表、不报工作区统计、不把选择成本外抛给用户。
7. **产物格式按链路分**：接口用例 → Postman Collection（Apifox 可导入）+ xlsx；功能用例 → Markdown/xlsx（列名对齐 TAPD）；不自建资产库（决策 0005）。

---

## 6. UAT 状态（未完全签收）

| 项 | 状态 |
|---|---|
| UAT-2 TAPD 链路（需求→用例） | ✅ 用户实测通过 |
| UAT-1 HAR 链路 | 挂起（用户决定；能力已具备，待新形态下接入） |
| UAT-3 伙伴门面 | 免验（用户自管 `test-agent`） |
| UAT-4 网关与配置页（6 条） | 机器预验 6/6 全绿，**用户未签收** |
| UAT-5 工作台（5 条） | 机器预验 5/5 全绿，**用户未签收** |

证据：`docs/uat-evidence-20260805.md`（含"被测系统回显凭据也不外泄"的硬证据）。
验收单：`docs/UAT.md`。

---

## 7. 决策索引（读这些，别读全部历史）

| # | 结论 | 状态 |
|---|---|---|
| 0001 | 转 DeepTutor 二开；blackbox 冻结 | 第 1 条"不改内核"**已被 0007 推翻**，其余有效 |
| 0002 | TAPD 独立可插拔 | 有效 |
| 0003 | TAPD 用官方 `mcp-server-tapd`（含安全采用条件） | 有效，条件见 `docs/research/mcp-server-tapd-audit-20260804.md` |
| 0004 | TAPD 维护线并入本线，spec 收窄为语义层 | 有效 |
| 0005 | 生成管线 + 标准格式导出，不自建资产库 | 有效 |
| 0006 | 能力网关 + 本地配置页 | **形态被 0007 取代**；配置中心/子服务管家的设计仍有效 |
| 0007 | **改真二开（fork DeepTutor）** | 当前主线 |
| 0008 | 单仓/双仓、可见性、版本与回滚、CI | **待接手方拍板** |

---

## 8. 接手方的建议起手顺序

1. **先立 0008**（发布与回归基线）——0007 第 5 条明确要求"基线先于搬迁"。要定的：
   fork 仓归属与可见性（注意：GitHub 上公开仓的 fork 默认公开，要私有得用 duplicate 而非 fork）、
   单仓 vs 双仓、tag/release 规则、CI 至少跑 `pytest tests/`（现成 600 例）。
2. **fork HKUDS/DeepTutor**（Apache-2.0），确认能本地 build 出镜像（当前容器只有编译产物，无源码）。
3. **先搬服务端能力**（`server/*.py` 除 webapp），带着 600 例测试一起搬，绿了再动前端。
4. **前端按 DeepTutor 设计系统重写工作台**，交互照 `docs/uat-evidence-20260805.md` 里已验收过的那套。
5. 搬完对 `v0.7.1-preforke` 做一次功能对账，确认没搬坏。

---

## 8.5 登录换新（0.8.0，最后一项交付）

HAR 里的 token 会过期，手填环境变量不可持续。体检判为「档A可重登录」时，`parse_har` 给出
`login_request`（账密位置是 `{{login_username}}`/`{{login_password}}` 占位，真值不出报告），
执行时 `auth="login"` 先重放登录拿新 token 注入后续请求。账密在配置页环境里配一次。

搬迁时注意两个设计要点（都是踩过坑才定的）：
- `login_request.url` 用 `{{baseUrl}}<归一化 path>` 而非抓包时的绝对地址，原 host 存 `recorded_host`。
  否则「HAR 录在 A 机、测试环境是 B 机」这个最常见场景会直接撞跨 host 红线。
- token 提取只从 JSON 体推断（11 条常见路径，命中即用，都不中取第一个 token 形键名）；
  Set-Cookie 走用户显式配置 `auth_token_path=cookie:名字`，不做自动推断。
  推断不出就 `auth_refresh.supported=false` 如实说不支持，**不猜**。

登录失败（账密没配 / 4xx-5xx / 取不到 token / 跨 host）→ **整轮不执行、不落报告**，
返回可读原因，不带着坏 token 硬跑一堆 401。

## 9. 遗留物与坑

- `git stash` 已空（登录换新已续完合入 0.8.0）。
- `deliveries/20260805-162854-工作台走查-本地mock` 是走查样本（唯一带 `cases.json` 的批次），可删。
- 用户机器上另有进程用 `uvx mcp-server-tapd --access-token <明文>` 常驻，**令牌在进程 argv 里可读**，
  且 `uvx` 每次拉最新绕过钉版——**建议提醒用户轮换该令牌**（本项目的 `tapd-runtime` 做法是对的）。
- `tests/test_gateway_webapp.py` 曾因缺 `python-multipart` 红过一条，已装并钉进 requirements。
- DeepTutor 前端在容器内只有编译产物且不在数据卷里，改了重建即丢——这是必须 fork 的技术原因。

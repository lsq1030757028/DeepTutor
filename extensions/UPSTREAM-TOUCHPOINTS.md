# 上游改动登记表（UPSTREAM-TOUCHPOINTS）

> 本仓 = `HKUDS/DeepTutor` 的私有二开副本。上游 remote 只读（push 已禁用）。
> 决策依据：[0007 真二开转向] / [0008 发布规范与回归基线]（`test-partner` 仓 `docs/decisions/`，P2 随代码迁入本仓）。
> fork 起点锚：tag `ut1.5.8-base` → `44fa7a1`（上游 v1.5.8），经内容对账确认与现役镜像一致。

## 这张表是什么

0007 撤销了「不改 DeepTutor 内核」的红线。红线撤了不等于可以随便改——
替代它的是**可量化的纪律**：每一处对上游既有文件的修改都登记在此。

**这张表变长，就是架构在往内核里长。** 它是警报器，不是工作量清单。
每次同步上游前先读它：表里的每一行都是潜在冲突点；表外的改动（新增文件）零冲突。


> **M2 额度账（2026-08-11）**：0024 钉死的口径 = 登记表新增行数，M2 额度 **≤6**。
> 本轮新增 **5 行**（#7-#11，manager 2026-08-11 批准），**余 1 行**。
> 余下这 1 行是**硬余量，不是可用额度**——任何新需求想动它，先停手上浮。
>
> **`tests/core/test_capabilities_runtime.py` 刻意不入册**（省下第 6 行）：
> 那条是全等集合断言，fork 多注册一个 capability 即必然红，且上游每加一个 capability
> 都会碰它 —— 改它等于**保证反复冲突**。处置改走**具名扣除**：
> 清单在 `extensions/test-partner/scripts/known-deductions.json`（机械可读、每条带
> 「为什么红/何时删/谁复核」三段），回归闸每次打印「本次扣除 N 条」。
> 正向断言由我方 `tests/test_fork_capabilities.py` 守，反向的全等断言让给上游。
> 实测：不扣除 1 failed / 扣除后 5 passed 1 deselected（同文件其余 5 例照跑，
> 按测试全名扣而非按文件扣，不会连带藏掉真红）。

## 优先级

1. **新增文件**（`extensions/`、新页面目录、新 router 文件）——首选，与上游零冲突，不必登记
2. **改既有文件**——必须登记，且每条都要回答「能否被新增文件替代」
3. 若某条长期填「否」，说明该处需要向上游提 PR 或重新设计，不该无限期扛着

## 登记表

| # | 文件 | 改动原因 | 上游若变更如何处置 | 能否用新增文件替代 |
|---|---|---|---|---|
| 1 | `.github/workflows/pypi-release.yml` | 加 `if: github.repository == 'HKUDS/DeepTutor'` 闸。本仓每建一个 Release 都会触发它，而把上游的包发布到 PyPI 的 `deeptutor` 项目名下既不正确也未获授权 | 保留这一行 `if:`，其余取上游 | **否**——工作流是既有文件，没有「禁用某个上游工作流」的外部开关 |
| 2 | `.github/workflows/docker-release.yml` | `images:` 由 `ghcr.io/hkuds/deeptutor` 改为 `ghcr.io/lsq1030757028/deeptutor`。`GITHUB_TOKEN` 只对本仓 packages 有写权限，推上游命名空间必然 403；0008 七的回滚 digest 也从本仓命名空间取 | 保留改后的 `images:` 行，其余取上游 | **否**——同上 |

| 3 | `deeptutor/api/main.py` | 注册测试工作台 router：import 块加 1 项 + 1 个 `include_router`（带 `dependencies=_auth`）。路由注册是集中式的，没有插件位 | 保留这两处，其余取上游。**`dependencies=_auth` 不可省**——裸挂会让落盘静默写进 admin 工作区（决策 0009） | **否** |
| 4 | `Dockerfile` | 加 2 行 `COPY extensions/test-partner/{server,skills}/`。上游只 COPY `deeptutor/` `deeptutor_cli/` `scripts/`，**没有这两行镜像里就没有我们的代码**（P1 等价性验证时发现）。<br>**⚠ 2026-08-11 性质加深（M2，0018 已批准，条件=独立成可整块删除的 fork 块）**：本行由「2 行 COPY」变成「**2 行 COPY + 装一套 TAPD MCP 运行时**」（uv 建 `/opt/tapd-mcp` venv、钉死 `mcp-server-tapd==8.0.80` + `mcp==1.29.0`）。按登记表自述，改构建配置属「架构在往内核里长」的第三类信号——**虽不新增登记行，但每次上游同步的冲突面与镜像体积都实打实变大**，故在此显式报备而非静默吞掉。fork 块带 `[fork] TAPD MCP 运行时` 起止标记，有测试守着它可整块删除 | 保留这两行，其余取上游 | **否**——COPY 清单是集中式的 |
| 5 | `web/components/sidebar/SidebarShell.tsx` | `SECONDARY_NAV` 加 `NavEntry`。导航注册是集中式数组，无插件位。<br>**2026-08-11（M2）：由 1 项变 2 项**——新增「测试旅程」入口与既有「测试工作台」并列（0017 O1）。两者状态对象不同（交付件 vs 一条需求的过程），合成一个入口会互相盖住。<br>**机制约束**：`SECONDARY_NAV` 是模块级常量、`(workspace)` 与 `(utility)` 两组侧栏共用，**不存在「只给一组加一项」的写法**，故两个入口的文案必须能区分 | 保留这一段，其余取上游 | **否** |
| 6 | `web/locales/en/app.json` + `zh/app.json` | 成对追加文案 key。`i18n:parity` 是硬闸，两边 key 集必须一致。<br>**2026-08-11（M2）：+2 键**（`Test Journey` / `Test Journey tooltip`）。<br>**⚠ 已知缺口，不当已完成**：M2 薄壳（`web/components/test-journey/**`）的 UI 文案**暂为中文硬编码，未走 `t()`**，eslint i18n 规则报 47 条 warning（warning 不拦闸，故不影响回归闸）。manager 2026-08-11 裁定：**UAT 可带中文硬编码，合 main 不可**——本条登记为**合 main 的阻断项**，定稿后一次性抽取约 50 个键 | 追加行在文件尾部，merge 时保留我方追加段，其余取上游 | **否**——文案库是集中式的 |
| 7 | `deeptutor/runtime/bootstrap/builtin_capabilities.py` | `BUILTIN_CAPABILITY_CLASSES` dict 加 1 项 `"test"`（M2，决策 0019 案 B 的载体）。能力注册是集中式 dict，无插件位 | 保留这一行，其余取上游。dict 尾部追加，上游新增 capability 也在此追加 → 行级冲突可自动合 | **否** |
| 8 | `deeptutor/services/prompt/manager.py` | `PromptManager.MODULES` 列表加 `"test"`。<br>**⚠ 全表唯一「漏改不报错」的一处**：少了这一行，`load_prompts` 查不到模块会**静默回落**——模型照跑但身上没有旅程纪律，行为退化成普通聊天而不报任何错。<br>**因此必须有测试守**：`tests/test_fork_capabilities.py::test_prompt_module_is_registered` 断言 `"test" in MODULES`（断注册事实，**不断文案内容**——断文案会让每次改提示词都红一次，红着红着就没人看了） | 保留这一行，其余取上游 | **否** |
| 9 | `web/app/(workspace)/home/[[...sessionId]]/page.tsx` | `CAPABILITIES` 常量数组加 1 项。DT 没有意图路由，模式**由用户在 picker 里显式选中**，不在这张表里它就不存在 | 保留这一项，其余取上游 | **否** |
| 10 | `web/components/chat/home/ChatMessages.tsx` | 富结果卡挂点 5 处：dynamic import、模式徽章分支、extractor useMemo、默认渲染抑制、渲染分支。<br>**⚠ 这是登记表的首个「第三类触点」——改组件内部逻辑，而不是往集中式清单里加一行。一条分界线在此被跨过。**<br>**改动原因栏如实写：改渲染逻辑（含事件流累加）**，不写「加一行」。那个 useMemo 依赖 `msg.events` 而非 `resultEvent`（两张卡是流式的），因此多一条流式↔落地一致性契约、多一个卡序列渲染语义——行数与登记行不变，**耦合面变宽**。<br>**状态：额度已获批（manager 2026-08-11），代码尚未落地。** | 同步时**必须人读上游 diff**，不能机械取舍。上游若重构卡片分派结构，这 5 处需整体重写 | **否** |
| 11 | `deeptutor/i18n/metadata_i18n.py` | `_CAPABILITY_DESCRIPTIONS` 加 1 项 `"test"`（picker 那一行显示的中英文案） | 保留这一项，其余取上游 | **否** |

勘察详见 `test-partner` 仓 `docs/recon-deeptutor-extension-points.md`。

### 一处**没有**成为触点的地方（值得记）

**每用户隔离（0009）没有改上游任何文件。** DeepTutor 自带 `UserScope`
（`deeptutor/multi_user/paths.py`），而我们的 `server/gateway/workbench.py`
每个函数本来就接受可选 `root` 参数——把当前用户的 scope 目录传进去即可。
两边各自已有的机制正好对得上，没有新增触点，也没有自造分区方案。

## 触点模式观察

目前全部触点归为两类：**集中式注册表**（导航数组、路由注册）与 **CI 工作流的仓库身份**。
两类都是「一行值」级别的改动，天然 merge 友好。

若日后出现第三类——改了组件内部逻辑、改了构建配置、改了数据结构——
那才是架构在往内核里长的信号，按 0008 三处置：要么向上游提 PR，要么重新设计成新增文件。

## 填写说明

- **文件**：精确到路径
- **改动原因**：为什么非改不可，一句话；不写「为了支持 X」这种没信息量的话
- **上游若变更如何处置**：具体动作，例「保留本行，其余取上游」
- **能否用新增文件替代**：是 / 否 + 理由。填「是」的条目应尽快改掉

## 同步流程

`git fetch upstream` → 按本表逐条核对 → **merge（不 rebase，`main` 已共享）** → CI 全绿 → 更新本表。

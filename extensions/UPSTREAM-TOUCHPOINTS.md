# 上游改动登记表（UPSTREAM-TOUCHPOINTS）

> 本仓 = `HKUDS/DeepTutor` 的私有二开副本。上游 remote 只读（push 已禁用）。
> 决策依据：[0007 真二开转向] / [0008 发布规范与回归基线]（`test-partner` 仓 `docs/decisions/`，P2 随代码迁入本仓）。
> fork 起点锚：tag `ut1.5.8-base` → `44fa7a1`（上游 v1.5.8），经内容对账确认与现役镜像一致。

## 这张表是什么

0007 撤销了「不改 DeepTutor 内核」的红线。红线撤了不等于可以随便改——
替代它的是**可量化的纪律**：每一处对上游既有文件的修改都登记在此。

**这张表变长，就是架构在往内核里长。** 它是警报器，不是工作量清单。
每次同步上游前先读它：表里的每一行都是潜在冲突点；表外的改动（新增文件）零冲突。

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
| 4 | `Dockerfile` | 加 2 行 `COPY extensions/test-partner/{server,skills}/`。上游只 COPY `deeptutor/` `deeptutor_cli/` `scripts/`，**没有这两行镜像里就没有我们的代码**（P1 等价性验证时发现） | 保留这两行，其余取上游 | **否**——COPY 清单是集中式的 |
| 5 | `web/components/sidebar/SidebarShell.tsx` | `SECONDARY_NAV` 加 1 个 `NavEntry`（工作台入口）。导航注册是集中式数组，无插件位 | 保留这一段，其余取上游 | **否** |
| 6 | `web/locales/en/app.json` + `zh/app.json` | 成对追加工作台文案 key（P3 前端各屏）。`i18n:parity` 是硬闸，两边 key 集必须一致 | 追加行在文件尾部，merge 时保留我方追加段，其余取上游 | **否**——文案库是集中式的 |

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

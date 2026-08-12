# 0032 · 容器内 extensions 依赖走 Dockerfile fork 块（非根 requirements）；L6 因 CI 外部阻塞推 M3

- 日期：2026-08-12
- 决策人：manager 自决（技术取舍 + 受阻范围处置），最终交付 UAT 时向用户明示 L6 状态
- 触发：镜像重建剧本预写时挖出两处前置（m2-build3）

## 一、psycopg2 / 容器内 extensions 依赖

**事实**：`Dockerfile:98-99` 只 `pip install -r requirements.txt`（**根** requirements），不装 `extensions/test-partner/requirements.txt`；而 L3 的 `db_readonly.py` 运行时 `import psycopg2`。照现状重建 fork 镜像，容器内带 db op 的用例判 `BLOCKED`（设计内诚实降级，非崩溃）。

**m2-build3 给的两案**：A 把 `psycopg2-binary` 加进**根** requirements（能跑但污染根 requirements，影响主镜像干净度）；B 不加，容器内 L3 恒 BLOCKED。

**裁定：两案都不取，走第三方案 C** ——

**在 Dockerfile 的 fork 块里 `COPY extensions/test-partner/requirements.txt` 并 `pip install`，不动根 requirements。**

理由：
1. **不污染根 requirements**：现役主镜像与上游同步的干净度不受影响——这正是 A 的代价、C 规避掉。
2. **治本不治标**：只加 psycopg2 是治标（extensions 还依赖 mcp / yaml / pydantic 等，容器内 extensions 代码要真跑就都得有）；装整份 extensions requirements 一次到位。
3. **在已授权范围内**：Dockerfile fork 块由 0012/0025 授权（登记表第 4 条，已报备性质加深），装 extensions 依赖属 fork 块自然内容，不新增登记行、不新增上游触点。
4. **fork 块须可整块删除**（0020 约束）：install 段独立成块、带注释边界，上游同步时可整块摘除。

**约束**：C 是 Dockerfile 改动，走提交纪律（只 add Dockerfile，禁 -a）；重建后须验证容器内 `python -c "import psycopg2"` 成功 + 一条 db op 用例真跑非 BLOCKED（P1 教训：构建过 ≠ 依赖在里面）。

## 二、L6 范围：推 M3，M2 收口时如实标未完成

**事实**（m2-build3 自评，未标绿）：L6 两半都没完成——
- **构建锚定**：机制在 `code_anchor.deploy_anchor`（origin 可达 + git cat-file，已测），但**未接进 `run_receipt.build_fingerprint`**，收据里仍是 L1 式 `_instance_fingerprint`；
- **回归入 CI**：设计稿 §6.5 要的"可被 CI 消费的回归包 + 示例 workflow 片段"两样都没产出。
- （L5 机制 done，本仓 HEAD 判 degraded 是正确行为。）

**裁定：L6 整档推 M3，不纳入 M2 收口。** 理由：
1. **CI 半有真实外部阻塞**：GitHub Actions 因账号计费问题全线秒失败（长期未修），"回归入 CI"这一半即便做了也**无法在 CI 上验证**——做一个验不了的东西是纸面交付。
2. **一档能力应整档交付**，不拆半绿：构建锚半虽本地可做，但与 CI 半同属 L6，拆开交会留一个悬空的半档。
3. **M2 闭环不依赖 L6**：主证据全链、四卡、四闸、能力锁 L3、缺陷出口已构成完整的"需求→用例→执行→证据→覆盖"闭环；L6 是部署级锦上添花，非闭环必需。
4. **让 M2 尽快收口交 UAT**：把受外部阻塞的一档从关键路径上挪走。

**这不是偷偷缩范围**：HANDOFF 原定 L3-L6 属 M2。L6 推 M3 是**因外部阻塞的带理由降级**，须在最终交付 UAT 时明确列给用户（连同 CI 计费这个根阻塞），由用户知情。M3 前置清单登记：① 接构建锚进 run_receipt；② CI 恢复后产回归包 + workflow 片段并在 CI 上验。

## §一 实测推翻，改甲（2026-08-12，manager 更正）

**0032 §一的前提错了，Dockerfile 不改。** m2-build3 落地前先验前提，三条实测 + manager 本地复核成立：

- `server/main.py:29` `from mcp.server import MCPServer`（mcp 2.0 命名）；DT `pyproject.toml:128` `mcp>=1.26.0,<2.0.0`；extensions `requirements.txt` `mcp[cli]==2.0.0`——**两边对 mcp 的约束互斥**，把扩展 requirements 装进容器主 python 会把 mcp 升到 2.0.0、越过 DT 上界，**撞坏 DT**。
- 容器主 python 下扩展服务端连 import 都过不了（容器 mcp 1.29 vs server 用 2.0 的 MCPServer）。
- fork 实例的 test-partner MCP 指向宿主 `host.docker.internal:3790`——**extensions 在容器根本不跑**（cases.jsonl #66 早有实证）；L3 的 bundle 子进程跑宿主（已有 psycopg2）。故容器装 psycopg2 是**空动作**。

**我错在**：§一理由 2「extensions 容器内要真跑就都得有 mcp/yaml/pydantic」是**未验证的前提直接写进裁定**——这是本会话我第三次同类（前两次：凭二手转述断言 0012 不成立→被 0027 推翻；凭没找到断言凭据不存在→0029）。执行者的「落地前先验前提」纪律第三次兜住了我的裁定。这条纪律（动手/下裁定前先验前提成不成立）对 manager 自己同样适用，不只对执行者。

**改甲**：M2 **不改 Dockerfile**。本次镜像重建的真实交付物 = **前端**（富卡 + i18n）；server 侧跑宿主、已带本轮改动 live，不靠重建。DoD#8b/8c 冒烟本就是「容器前端 + 宿主服务端」。L3/E19/实体闸/缺陷出口在宿主已实证（1400 passed），不在容器验范围。

**「extensions 入容器」随 L6 一起进 M3**：正解是丙——给 extensions 在容器建独立 venv（装 mcp 2.0，与 DT 隔离，像 tapd-mcp 那样）+ 平台改容器内 stdio 拉起。登记为 M3 前置第 3 条。这本就是 0012「进容器 + stdio」那条一直没落地的半拉子，归位到 M3 一起做。

## 证伪信号

若 CI 计费在 M2 收口前恢复，则 L6 的 CI 半重新评估是否顺手纳入（不再有"验不了"的阻塞）；构建锚半无论如何在 M3 补。

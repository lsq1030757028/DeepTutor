# 0029 · Queenie 凭据本就存在（manager 误判为缺失）；写操作授权口径与一处必须先验的矛盾

- 日期：2026-08-11
- 决策人：manager 自决（授权口径）+ 用户指认凭据位置
- 用户原话：**「我记得我们本地存了访问权限了 你找找」**

## 一、事实更正：凭据从未缺失，是 manager 没找

`0025 §1` 与其后多轮汇报把「Queenie 侧四项全无（base_url / 测试账号 / DB 只读账号 / 可恢复写许可）」当作阻塞事实，并据此把 DoD#4 / #4c / #12b 标为「不判分」、向用户索要凭据两次。

**该事实错误。** 凭据自 2026-05-26 起即存在于 `.claude/agents/queenie-qa/ENV.local.md`（git 忽略，13 项），含测试环境后台地址、测试账号与密码、数据库五件套、服务器 SSH、代码仓令牌。

**根因**：manager 在 0025 判定「四项全无」时，只核了 DeepTutor 仓与 M2 产物目录，**未检索工作区既有的被测系统资产**——而 `queenie-qa` 是本工作区为该系统专设的测试 agent，是这类凭据的第一顺位落点。这与本线反复出现的「作用域没人核」同族：**检索范围本身没有被当作判据的一部分**，"没找到"被当成了"不存在"。

**溯源已对齐**（第 0 步）：`queenie-qa/README.md:3` 自述为「Queenie（QiQi 手办）版本测试专用 Agent」，与 0023 锁定的靶需求所属系统一致，非张冠李戴。

## 二、必须先验的矛盾（不得凭任一侧下结论）

`ENV.md` 内部自相矛盾：
- `:41` 账号名 `QIQI_DB_USER="qiqi_readonly"` —— 名义只读；
- `:35` 说明「权限：**DML**，无 DDL」—— DML 含 INSERT/UPDATE/DELETE，即可写。

两者不可能同时成立，而它恰好压在「M2 能不能跑写用例」这个决定主证据成色的问题上。

**裁定：先在库上实测实际授权再用，不采信任一侧文档措辞。** 实测方式=只读探针（查询自身权限，不实际写入）。结论落 M2 证据目录并回写本文。**在实测出结论前，DB 通道按只读使用**（保守侧）。

### 实测结论回写（2026-08-12，M2 第三棒）

**`ENV.md:35`（DML）属实；`ENV.md:41` 的账号名 `qiqi_readonly` 与实际授权不符。**

只读探针（`agent-lab-runs/deeptutor-absorb-m2-20260810/evidence/m2-stage3/db_privilege_probe.py`，全程只跑 `SELECT` / 系统目录 / `has_*_privilege()`，未做任何真写入试探）实测：

- **38 / 38** 张业务表 `INSERT` + `UPDATE` + `DELETE` 全部为 true；`information_schema.table_privileges` 显式 grant **152** 条 = 38 表 × 4 权限，grantor 全为 `postgres`；
- `TRUNCATE` 0/38、schema `CREATE` 0/13、database `CREATE` false、`rolsuper/rolcreaterole/rolcreatedb/rolbypassrls` 全 false、`pg_auth_members` 零行（不是继承来的）、自有表 0 —— 即「无 DDL」那半也属实；
- **额外发现**：`pg_default_acl` 在 13 个 schema 各有 `qiqi_readonly=arwd/postgres`，**将来新增的表也自动带 DML**，安全边界不会随 schema 演进自动收紧。

**处置不变（§三 第 4 条不因实测有权限而放宽）**：DB 层一律只读，且做成机械保险而非承诺 —— 连接固定带 `options="-c default_transaction_read_only=on"` 且 `set_session(readonly=True)`，实测 `SHOW default_transaction_read_only` 回 `on`。L3 只读通道复用同一套连接构造，不另开没有这两道锁的路径。

证据：`evidence/m2-stage3/step0-preflight.md` §一 + `db-privilege-probe.json`。

## 三、写操作授权口径（manager 自决）

**应用层写操作（经测试环境后台账号新建/修改业务对象）判为已授权**，理由三条：
1. 该账号是工作区为该系统**专设的测试账号**，其存在目的即执行测试；
2. 目标是**测试环境**，非生产——manager 此前向用户描述「再上一档要碰真实产品数据」是错误框定，已在本文更正；
3. 用户以「我们本地存了访问权限了，你找找」指认该资产，语义是启用它。

**约束四条（施工必须遵守）**：
- 写操作**只落测试环境**，生产环境地址不得出现在任何 caseset 中；
- 每条写用例必须带 `side_effects.cleanup` 声明（v1.0.0 schema S29 已强制非空）；**注意 0028 §一已裁定 M2 不做可执行 undo**，故清理靠声明 + 测试环境可重置，**不得对外宣称"写操作可自动回滚"**；
- 写确认人闸必须真实生效（`journey_write_confirm` 已于 `7bb7864a` 落地，BB-511）；
- **DB 层一律只读**，即使第二节实测出 DML 权限也不启用写——业务写走应用层，绕过应用层直接改库会使被测行为失真。

## 四、连带解除的阻塞

DoD#4（主证据：真需求全链）、#4c（判别力）、#12b、L3（只读 DB 三层断言）从「凭据未到位不判分」恢复为**可判分**。L3/L4 靶选择不再依赖白月SMS 兜底。

## 五、证伪信号

若实测发现该测试环境为**多人共用**且存在他人正在进行的测试数据，则写操作口径需收窄为「只创建带本次 run 前缀的隔离数据、不修改既有对象」，并在 UAT 时向用户说明。当前假设为单人 QA 环境——**此假设未经实证**，施工第一步须确认。

### 实测结论回写（2026-08-12，M2 第三棒）：**证伪信号已触发，收窄条款生效**

该测试环境**不是单人 QA 环境**，且他人正在测的恰好就是本轮靶需求：

1. `character.custom_characters` 全量 25 行中 **7 行 `language='한국어'`**，全部创建于 **2026-08-10 ~ 08-11**（探针前 1~2 天），来自 **3 个不同 `creator_id`**，`status` 全为 `PENDING`；另有 1 行 `日本語`。是他人在跑的测试数据，非历史存量。
2. 我方持有的 `QIQI_ADMIN_USER_TEST` 字面量**就是 `admin`**（实测相等），而 `auth_admin.admin_users` 中 `admin` 行 `last_login_at = 2026-08-11 08:10:40` —— 本线在此之前从未接触该环境（凭据今天才被找到），故那次登录不是我们：**后台账号是共享账号**。
3. `audit.events` 2026-08-11 共 102 条 / 4 个 distinct actor，08-10 共 84 条 / 5 个；近 30 天 `actor_type='user'` 的 distinct id 有 4 个；`chat_history.messages` 近 7 天 123 条。已排除「只有 system 在跑定时任务」的反例。

**据此，§三 的写操作口径按本节收窄（非可选）**：

- 只创建**带本次 run 前缀**（`M2R3-` + run id 片段）的隔离数据，一切可命名字段都带前缀；
- **不修改、不删除任何既有对象** —— 尤其上述 7 行韩语角色与 `character.voice_assets` 中 2 条 `韩语 PRESET`；
- 写只走应用层测试后台（实测 HTTP 200 可达），DB 只读；
- `side_effects.cleanup` 写清「删哪条、按什么前缀认」，并如实标注这是**声明式清理，不是可执行 undo**（0028 §一 L4 取乙）；
- **UAT 时须向用户说明「该测试环境多人共用」**，不得按单人环境呈报。

### 连带取得的 oracle 事实（未经应用层验证，引用请看置信标注）

靶需求功能在测试环境里**看起来已实现且正在被测**，非待开发：`character.voice_assets` 有 `language='韩语'` 的 `PRESET` 2 条且 `status=ACTIVE`，被实际引用的是 `voice_bomin_ko`（보민，女名）与 `voice_doohwan_ko`（두환，男名），一女一男，与需求正文「新增男女各一韩语音色」吻合；`recommended_characters` 3 条全部 `language='中文'`，与「默认 3 角色本期不支持切韩语」吻合。

**对主证据的影响**：本轮用例是对**已实现行为的验证**，不是对未实现功能的预写。可执行性因此提高，但「用例全绿」不得被读成「需求刚被实现且正确」，覆盖图须标注取数时点的实现状态。
**置信标注**：DB 侧数据形态是当版实测；「功能已实现」是从数据形态**反推**，未经应用层功能验证 —— 属待主证据链路证实的观察，不是已验证结论。

证据：`evidence/m2-stage3/step0-preflight.md` §二/§三 + `env-exclusivity-probe2.json`。

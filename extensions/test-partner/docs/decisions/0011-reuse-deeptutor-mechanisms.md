# 0011 · 贴合 DeepTutor 原设计：凭据与配置一律复用平台机制

- 日期：2026-08-07
- 拍板人：用户（原则）；具体落点由 AI 依该原则勘察后确定
- 原话：`我们的方案设计尽量贴合DT项目的原设计，比如MCP工具，我们的TAPD MCP应该做到在那边配置个人令牌，类似github那种，而不是自己搞一个配置页面`
- 反馈落盘：[2026-08-07-design-v2-har.md](../feedback/2026-08-07-design-v2-har.md) 第六轮

## 原则

**凡是我们要加的配置、入口、机制，先问一句「DeepTutor 里已经有对应的地方了吗」。**
有就用它的，没有才自己开。

这不是风格偏好。自建一套等于：用户要在两个地方配同类东西、
上游升级时我们那套要自己维护、我们的实现还得自己解决平台已经解决过的问题（隔离、权限、沙箱）。

## 勘察结论：DeepTutor 已有的机制比我们以为的多

| 我们原打算自建 | 平台已有 | 出处 |
|---|---|---|
| TAPD 令牌输入框（完整稿第 10 屏） | **MCP 服务配置面**：服务器表单自带「环境变量」与「HTTP headers」两个 KeyValue 编辑器 | `web/components/mcp/McpServerForm.tsx:262,288`、`deeptutor/api/routers/mcp_settings.py` |
| 自己存凭据（`config/secrets.env`） | **`${secret:<server>/<field>}` 引用机制**：值不进配置文件，连接时在内存解析；界面上是 `type="password"` 掩码 | `deeptutor/services/mcp/secrets.py:36-45`、`web/components/mcp/KeyValueEditor.tsx:10-20,84` |
| 自己做每用户凭据分区（0009 最难的一点） | **`owner_secrets_dir(owner_id)`**：按 owner 寻址、`mkdir` + `chmod 0700`、位于 `data/system` 下 exec 沙箱**唯一不挂载**的分支 | `deeptutor/multi_user/paths.py:211-225` |

## 三条落点

### 一 · TAPD 令牌：我们什么都不建

TAPD 走 MCP，令牌就是**该 MCP 服务器条目上的一个 HTTP header**，
用平台的 `${secret:...}` 机制存，在 DeepTutor 既有的 MCP 配置界面里填——
和别的连接器（GitHub 那种）同一套形态、同一个位置。

**完整交互稿第 10 屏「公共配置 / 外部连接」作废。** 那一屏方向对了一半
（确实挪出了工作台），但仍然是我们自己新开的一块配置区。

### 二 · 测试环境的凭据：用 `owner_secrets_dir`，不用我们的 `secrets.env`

现役 `extensions/test-partner/server/gateway/config.py` 的 `secrets.env`
是**每台机器一份的全局文件**，与 0009 的每用户隔离直接冲突。

改用 `deeptutor.multi_user.paths.owner_secrets_dir(owner_id)` 作为落点。
这一步同时解掉 0009 里我标为"唯一会造成真实越权"的那条：

> 凭据分区不能只靠前端过滤——后端按环境名解析凭据时就要带用户身份，
> 否则 A 猜到 B 的环境名就能借 B 的凭据发真实请求。

`owner_secrets_dir` **本来就按 owner_id 寻址**，不是按请求作用域，这个问题在平台层已经解过了。

**耦合层级的选择**（这一条是判断，不是照搬）：不直接复用
`deeptutor/services/mcp/secrets.py` 的 `store_secrets()`——那个模块自我定位是
"Credentials for a user's own **MCP servers**"，拿它存测试环境变量属于借用他人语义，
上游改它时我们会被动。**只复用更中立的 `multi_user/paths.owner_secrets_dir`**，
它给的是"某个 owner 的机密目录"这个中性事实，文件格式仍由我们自己定。

### 三 · 测试环境本身（base_url + 变量名）不是凭据，另说

环境的非敏感部分（名字、base_url、变量的**键名**）是我们的业务数据，
平台没有等价物，仍由我们存，落在用户 scope 下（与交付批次同一套路，0009 已定）。
**只有变量的值走 `owner_secrets_dir`。**

## 触点影响

**这三条落点都不新增上游触点。** 全是"用平台已有的东西"，
既没改上游文件，也没往 `UPSTREAM-TOUCHPOINTS.md` 里加行——
反而删掉了原计划里"自建配置页"会带来的那些。

## 待办

- 完整交互稿删掉第 10 屏，并在第 9 屏写明凭据值的落点是平台机密目录
- `server/gateway/config.py` 的 `secrets.env` 路径改造（属 0009 的隔离改造，另开工单）
- 第 9 屏「测试环境与变量」是否还有平台等价物**尚未逐项核实**，
  已确认的是：MCP 的环境变量编辑器服务的是 MCP 进程，与测试执行环境不是一回事，不可混用

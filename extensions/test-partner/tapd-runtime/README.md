# tapd-runtime — mcp-server-tapd 宿主机运行时

给 M3 用的 `mcp-server-tapd` 本地运行脚手架：钉死 8.0.80，只走个人 token，
以 streamable-http 跑在宿主机 `0.0.0.0:3795`，供 DeepTutor 容器挂载。

部署条件全部来自 `docs/research/mcp-server-tapd-audit-20260804.md`（判定：**可用但需条件**）。
本目录是那份审计条件的落地，不是它的替代品——升版、扩大使用范围之前回去读原文。

> **本目录已退居排障位（2026-08-05，M3.5 能力网关上线）。**
> 正常上手不在这里：起网关（`scripts\start_server.cmd`）→ 浏览器开
> **http://localhost:3789** → 粘令牌 → 保存。写配置、起子服务、注册进 DeepTutor
> 由网关代劳（`server/gateway/`），凭据落 `config/secrets.env`，输入框不明文显示。
>
> 本目录留着的理由是它**不依赖网关**：网关自己起不来的时候，这里是证明 TAPD 那一侧
> 没问题的独立路径。两条路的凭据文件是分开的——`tapd-runtime\.env` 网关不读；
> 两边都填过的话，子进程里网关注入的那个赢（python-dotenv 默认不覆盖已存在的环境变量）。
> 版本钉死（`PINNED.md`）、工具清单、`.venv` 重建、以及下面那些踩过的坑，仍然以本文件为准。

## 上手（排障模式；正常路径见上面的提示框）

**双击 `tapd-runtime\setup_tapd.cmd`，粘贴令牌，完。**

令牌在 TAPD 网页端右上角头像 → 个人设置 → 个人访问令牌 里生成，只勾你自己要用的项目。
脚本粘完会自己把剩下的事做掉：

1. 写 `.env`（CRLF、钉死 `TAPD_API_BASE_URL=https://api.tapd.cn`）；
2. 后台起 mcp-server-tapd，探 3795 端口确认真活着；
3. 调 DeepTutor 的配置 API 把 `tapd` 条目注册进部署注册表，并回读确认状态是 connected；
4. 打印"TAPD 能力已生效"。

看到这句话就可以回 DeepTutor 聊天里贴需求链接了，页面上不用点任何东西。

几件值得先知道的事：

- **令牌输入是明文显示的。** cmd 的 `set /p` 没有隐藏输入的办法，这条在脚本里也写着。
  令牌只写进 `tapd-runtime\.env`（已被 `.gitignore` 拦住），窗口关掉屏幕上就没痕迹了。
- **脚本可以随便重跑，是幂等的。** `.env` 里已经有令牌就跳过录入；3795 已经在服务就复用
  现有进程不重复起；DeepTutor 里已经有 `tapd` 条目就覆盖更新（你手工改过的
  `enabled_tools` 之类会被保留）。
- **服务活在那个最小化的 "tapd-runtime :3795" 窗口里**，想停 TAPD 就关它。
  用 `start /b` 让它跟 setup 窗口共用控制台是不行的——那样你一关 setup 窗口服务就跟着死。
- **换令牌**：删掉 `tapd-runtime\.env`，重跑 `setup_tapd.cmd`。
- 默认只放行两个只读工具（`get_stories_or_tasks`、`get_stories_fields_info`）。
  这个包一共 43 个工具，17 个是写操作，而它的凭据是模块级单例，写操作在 TAPD 审计里
  全部归到同一个令牌名下、追不到人——所以默认不放行。要放宽在 DeepTutor 的
  MCP 设置页改 Enabled tools，重跑脚本不会把你的修改冲掉。

### 起不来的时候

脚本探不到 3795 会自己读启动日志做分类诊断，按提示做就行。四类已知故障：
令牌被 TAPD 拒（401/403，**最常见**）、连不上 api.tapd.cn（代理/VPN/DNS）、
3795 被占、`.venv` 依赖缺失。完整 traceback 在 `tapd-runtime\tapd-start.log`。

注意这个包**启动即校验令牌**：令牌不对不是"起来之后调用失败"，是进程根本起不来。

## 为什么绑 0.0.0.0，以及为什么这个绑法不能推广

绑 `0.0.0.0` 是 Docker 容器能连到宿主机服务的**必要条件**——容器里的
`host.docker.internal` 解析到宿主机的 LAN 侧地址，服务只听 `127.0.0.1` 的话容器打不进来。

代价是：这个端口对整个局域网敞着，而 **mcp-server-tapd 没有任何鉴权选项**。
更要命的是凭据是模块级单例，所有调用方共用同一个身份，其中包含
`create_bug` / `update_story_or_task` 这类写操作——TAPD 侧的审计日志只会看到你一个人，
谁改的分不出来。官方 issue #7（按调用传 token）至今 open 无回复。

所以：

- **单人本机开发**可以这么跑，前提是这台机器不在不可信网络里。
- **团队推广不这么部署**。走每人本地 stdio + 各自的个人 token：
  ```
  mcp-server-tapd --mode=stdio
  ```
  凭据留在各人自己机器上，TAPD 审计能归因到人。
- 真要共享一份 HTTP 服务，必须换成非默认绑定 + 带鉴权的反向代理 + 专用机器人账号，
  不能是本目录这个形态。

## 审计条件摘要（做了什么 / 为什么）

| 条件 | 本目录的落地 |
| --- | --- |
| **版本钉死** `==8.0.80` + 记 sha256，禁 `uvx` | `PINNED.md`（含两个发布物的 sha256、`mcp==1.29.0`、完整 freeze、升版清单） |
| **只用 token**，禁 `API_USER`/`API_PASSWORD` | `.env.example` 只给 token 一行；`start_tapd.cmd` 启动前主动清空 `TAPD_API_USER`/`TAPD_API_PASSWORD` |
| **`TAPD_API_BASE_URL` 钉死** `https://api.tapd.cn` | `setup_tapd.cmd` 生成 `.env` 时把这一行硬写死（不问用户）；`.env.example` 里注明这是凭据流向的唯一闸门——包对 base_url 无任何协议/域名校验 |
| **启动目录无杂 `.env`** | 包会 `load_dotenv()` 找 `.env`；启动脚本 `cd` 到本目录，这里的 `.env` 就是唯一那份 |
| **`BOT_URL` 非必要不配** | `.env.example` 里默认注释掉 |
| **团队走本地 stdio** | 见上一节 |
| 每次升版重跑 仓库vs发布物 diff | `PINNED.md` 升版清单第 1 条 |

补一条审计报告没有、本次搭环境实测出来的：`start_tapd.cmd` 会在读 `.env` **之前**
清空继承来的 `TAPD_ACCESS_TOKEN` 等变量。这台机器的环境里本来就有一个 TAPD token，
不清的话服务会拿一个谁也没在 `.env` 里写过的凭据悄悄起来。

## 运行时行为（踩过的坑，别再踩）

- **import 期就真打 TAPD API。** `server.py` 顶层有 `client = TAPDClient()`，
  而 `TAPDClient.__init__` 在检测到 token 时立刻 `get_user_info()` →
  `GET {TAPD_API_BASE_URL}/users/info?s=mcp`，`raise_for_status()` 无 try/except。
  后果：**token 错、网络不通、base_url 写错 —— 服务不是起来后调用失败，是根本起不来**，
  直接抛 traceback 退出。启动脚本已经把这句话打在启动横幅里。
- **不要用 `python -m mcp_server_tapd.server` 启动。** 包的 `__init__.py` 已经 import 过该模块，
  runpy 会再执行一遍，上面那个 `/users/info` 请求会发两次。用 venv 里的
  `Scripts\mcp-server-tapd.exe`（启动脚本就是这么做的）。
- **Python 3.13 是硬门槛。** 宿主机的 3.12.10 装不上，pip 会把 8.0.x 全系列
  以 `Requires-Python >=3.13` 忽略掉。本目录的 `.venv` 是 uv 拉的独立 CPython 3.13.14。
- 环境变量名是 `BOT_URL`，不是 `TAPD_BOT_URL`。写错了不报错，只是静默不生效。
- **`load_dotenv()` 不只吸 CWD 的 `.env`。** python-dotenv 的 `find_dotenv()` 是从
  调用方所在文件的目录**往上逐级找**，调用方是 `site-packages/mcp_server_tapd/app_config.py`，
  一路往上就走到 `tapd-runtime/`，于是不管你在哪个目录起这个进程，本目录的 `.env` 都会被吃进去。
  2026-08-05 实测踩到：为了做无令牌验证，特意换到一个空目录起服务，它照样加载了
  `tapd-runtime\.env`。要真的无令牌起，得先把 `.env` 挪走。
- **`start /b` 起服务是错的。** `/b` 的子进程和父窗口共用控制台，用户一关 setup 窗口，
  Windows 给整个控制台发 CTRL_CLOSE_EVENT，服务跟着死。`setup_tapd.cmd` 用的是
  `start "..." /min`，独立控制台，setup 窗口关掉服务照活。
- **`NoDefaultCurrentDirectoryInExePath=1` 的环境里，`cmd /c start_tapd.cmd` 找不到脚本。**
  必须写 `.\start_tapd.cmd`。托管/CI shell 里常设这个变量，实测踩到过。

## 手工模式（排障用）

一键脚本挂了、或者你想单独控制某一步的时候用。正常上手不需要看这一节。

**1. 填凭据**

```
copy .env.example .env
```

用编辑器打开 `.env`，把 `TAPD_ACCESS_TOKEN=` 后面补上你的 TAPD 个人访问令牌。另外两行保持原样。

> 保存时**必须保留 CRLF 换行**。`start_tapd.cmd` 用 `for /f` 读 `.env`，
> 而 cmd 从一个 UTF-8 且 LF-only 的文件里会读出**零行**——不是报错，是静默读空，
> 表现为"我明明填了 token 却说我没填"。VS Code 右下角确认是 CRLF。
> （`setup_tapd.cmd` 写出来的 `.env` 天然是 CRLF，手工编辑才有这个风险。）

**2. 起服务**

```
tapd-runtime\start_tapd.cmd
```

前台运行，Ctrl+C 停。脚本会先卡三道：`.venv` 在不在、`.env` 在不在、
`TAPD_ACCESS_TOKEN` / `TAPD_API_BASE_URL` 是否为空，任一不过直接退出，不会把值回显到终端。

起来后本机自查（列 43 个工具，不 call 任何一个）：

```
..\.venv\Scripts\python.exe verify_tapd.py
```

**3. 登记进 DeepTutor**

走脚本（幂等，可单独跑，不依赖 `setup_tapd.cmd`）：

```
..\.venv\Scripts\python.exe register_tapd.py              # 注册/更新
..\.venv\Scripts\python.exe register_tapd.py --dry-run    # 只读现网配置，打印将写入什么
..\.venv\Scripts\python.exe register_tapd.py --status     # 只看 tapd 条目连接状态
..\.venv\Scripts\python.exe register_tapd.py --remove     # 摘掉 tapd 条目
```

它打的是 DeepTutor 的 `PUT /api/v1/settings/mcp/servers/tapd`（单条 upsert，
不碰别的条目）。DeepTutor 不在 3782、或者你的部署开了登录需要 admin 令牌，
它都会给出对应的中文提示。

要在页面上手填也行——DeepTutor 设置 → MCP → Deployment registry，名字 `tapd`，URL：

```
http://host.docker.internal:3795/mcp
```

端口选 3795 是因为 3790 已经被 test-partner MCP 服务器占了。
容器里必须用 `host.docker.internal`，填 `127.0.0.1` 会打到容器自己身上。

## 重建 .venv

`.venv/` 不进版本库。换机器或者删了要重建：

```
py -3 -m pip install --user uv
py -3 -m uv venv tapd-runtime\.venv --python 3.13
tapd-runtime\.venv\Scripts\python.exe -m ensurepip --upgrade
tapd-runtime\.venv\Scripts\python.exe -m pip install "mcp-server-tapd==8.0.80" "mcp==1.29.0"
```

uv 会自动下载 python-build-standalone 的 CPython 3.13，不需要在系统里另装一个 Python。
装完对着 `PINNED.md` 的 freeze 核一遍。

## 工具清单（43 个，`verify_tapd.py` 实测产出）

M3 的 req-testing 剧本里的工具名占位用这份清单替换。
入参列的是 schema 里的 property 名；除 `get_user_participant_projects`（只要 `nick`）外，
`workspace_id` 一律必填。`options` 是个 dict，具体键看工具自己的 description
（在 `verify_tapd.py` 的非 `--json` 输出里能看到完整版）。

拿机器可读版：

```
..\.venv\Scripts\python.exe verify_tapd.py --json > tools.json
```

### 读类（26 个）

| 工具 | 入参 | 说明 |
| --- | --- | --- |
| `get_bug` | options, workspace_id | 获取 TAPD 缺陷 |
| `get_bug_count` | options, workspace_id | 获取 TAPD 缺陷数量 |
| `get_comments` | options, workspace_id | 获取符合查询条件的所有评论（分页，默认一页 30 条） |
| `get_commit_msg` | options, workspace_id | 获取需求/缺陷/任务的源码提交关键字 |
| `get_entity_attachments` | options, workspace_id | 获取附件信息，含下载链接（默认有效 300s） |
| `get_entity_custom_fields` | options, workspace_id | 获取需求/任务/迭代/测试用例的自定义字段配置 |
| `get_entity_relations` | options, workspace_id | 获取关联关系（缺陷↔需求、需求↔测试用例） |
| `get_image` | options, workspace_id | 按图片地址取单张图片下载链接 |
| `get_iterations` | options, workspace_id | 按项目取符合条件的所有迭代 |
| `get_related_bugs` | options, workspace_id | 取需求关联的缺陷 ID |
| `get_release_info` | options, workspace_id | 取发布计划信息（分页，默认 30，最多 200） |
| `get_stories_fields_info` | workspace_id | 取需求所有字段及候选值 |
| `get_stories_fields_lable` | workspace_id | 取需求所有字段的中英文名 |
| `get_stories_or_tasks` | options, workspace_id | 取需求或任务（无 limit 时需配合 count 工具） |
| `get_story_or_task_count` | options, workspace_id | 取需求/任务数量 |
| `get_tcases` | options, workspace_id | 取测试用例（分页，默认 30，最多 200） |
| `get_timesheets` | options, workspace_id | 取花费工时（分页，默认 30，最多 200） |
| `get_todo` | entity_type, limit, page, workspace_id | 取用户待办 |
| `get_user_participant_projects` | nick | 取用户参与的项目列表（不带 workspace_id 时先调它） |
| `get_wiki` | options, workspace_id | 取 Wiki（分页，默认一页 30 条） |
| `get_workflows_all_transitions` | options, workspace_id | 取工作流流转细则 |
| `get_workflows_last_steps` | options, workspace_id | 取工作流结束状态 |
| `get_workflows_status_map` | options, workspace_id | 取工作流状态中英文名映射 |
| `get_workitem_types` | options, workspace_id | 取需求类别 |
| `get_workspace_info` | workspace_id | 取项目信息 |
| `get_workspace_users` | options, workspace_id | 取项目成员列表 |

### 写类（17 个，会改 TAPD 真实数据）

共享部署下这些操作在 TAPD 审计里全部归到同一个 token 名下，无法追人。

| 工具 | 入参 | 说明 |
| --- | --- | --- |
| `add_timesheets` | options, workspace_id | 填写花费工时 |
| `create_bug` | options, title, workspace_id | 创建缺陷 |
| `create_comments` | options, workspace_id | 添加评论 |
| `create_iteration` | options, workspace_id | 创建迭代 |
| `create_or_update_tcases` | options, workspace_id | 新建/更新测试用例 |
| `create_story_or_task` | name, options, workspace_id | 创建需求或任务 |
| `create_tcases_batch` | options, workspace_id | 批量新建测试用例 |
| `create_wiki` | options, workspace_id | 新建 Wiki |
| `entity_relations` | options, workspace_id | 创建关联关系 |
| `program_bind_entities` | action, entity_ids, entity_type, workspace_id | 项目集批量关联/取消关联 |
| `send_qiwei_message` | msg | 发企业微信群消息（需配 `BOT_URL`） |
| `update_bug` | options, workspace_id | 更新缺陷 |
| `update_comments` | options, workspace_id | 更新评论 |
| `update_iteration` | options, workspace_id | 更新迭代 |
| `update_story_or_task` | options, workspace_id | 更新需求或任务 |
| `update_timesheets` | options, workspace_id | 更新花费工时（每次一条） |
| `update_wiki` | options, workspace_id | 更新 Wiki |

## 本次验证做到哪一步

- `initialize` + `list_tools` **已实测通过**，43 个工具名如上。
- 验证时服务端是**不带 token** 起的，并且只听 `127.0.0.1`。原因见上面"import 期就真打 TAPD API"：
  带一个假 token 起服务的做法在这个包上行不通——import 阶段就会拿假 token 去打
  `https://api.tapd.cn/users/info`，401 之后 `raise_for_status()` 抛出、进程直接死，
  既起不来也白白往 TAPD 发了一次带假凭据的请求。不带 token 时那条分支不触发，
  工具注册与 list_tools 完全不受影响，是干净的验证路径。
- 假 token 的行为另外用 `TAPD_API_BASE_URL=http://127.0.0.1:9`（本机死端口，不出网）
  单独证实过：确实在 import 期发请求、确实因此起不来。
- **没有真调过任何 TAPD 工具**，一条真实业务数据都没读过、没写过。
- 验证完服务进程已停，`.env` 已删（只留 `.env.example`）。

### 一键化改造的验证（2026-08-05）

- 失败分支：假令牌 + `TAPD_API_BASE_URL=http://127.0.0.1:9` 走完
  `setup_tapd.cmd`（录入 → 写 `.env` → 起服务 → 起不来），确认诊断文案可读。
  四类故障分类（401/403、网络不通、端口占用、依赖缺失）用构造日志逐条验过。
  全程没往 api.tapd.cn 发过一次假凭据请求。
- 成功分支：先手工起一个**不带令牌**的服务（真活着、能 `list_tools`），再跑
  `setup_tapd.cmd`——它识别出 3795 已在服务、复用现有进程、真调 DeepTutor API
  完成注册，回读状态 `connected`、工具数 2
  （`mcp_tapd_get_stories_or_tasks` / `mcp_tapd_get_stories_fields_info`）。
  连跑第二次验证幂等（走的是"已有条目→覆盖更新"分支）。
- 现场已恢复：`register_tapd.py --remove` 摘掉测试条目，DeepTutor 的
  `data/user/settings/mcp.json` 与改造前逐字段一致（只剩 `test-partner` 一条）；
  测试服务进程已杀，`.env` / `tapd-start.log` 已删。

## 用户需要做的一步

双击 `setup_tapd.cmd`，粘贴 TAPD 个人访问令牌。没有第二步。

想确认凭据真的通：服务能起来本身就是证据——这个包在启动那一刻就拿令牌打过一次
`/users/info`，起来了就说明令牌是有效的。

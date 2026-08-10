# 0012 · TAPD 令牌迁移改走「进容器 + stdio」，0011 落点一作废

- 日期：2026-08-08
- 拍板人：用户
- 原话：`选B`
- 修订对象：[0011 落点一](0011-reuse-deeptutor-mechanisms.md)（TAPD 令牌走 MCP 条目的 HTTP header）

## 为什么 0011 落点一必须作废

**它在技术上不成立**，不是风格问题。

0011 写的是「令牌就是该 MCP 服务器条目上的一个 HTTP header，用平台的
`${secret:...}` 机制存」。勘察实证推翻了这个前提：

TAPD 那个 MCP 服务（`mcp-server-tapd`）在 streamable-http 模式下
**没有任何按调用读取 header 的能力**——它的凭据只能是**进程自身的环境变量**。
平台就算把 header 安全地存好并发过去，服务端也不读它。

也就是说，0011 落点一即使完整实现，令牌仍然得留在我们自己的进程环境里，
「作废自建配置区」这个目标一步都没往前走。

0011 自己的勘察表其实列了两个 KeyValue 编辑器（环境变量 / HTTP headers），
落点一**挑错了那一个**。正确的等价物是 MCP 条目的「环境变量」编辑器。

## 定下来的方案（B）

把 TAPD MCP 服务**搬进 DeepTutor 容器**，以 stdio 方式由平台自己拉起，
令牌走平台密钥库的 `${secret:...}` 引用。

### 四步，以及每步的实证依据

**一 · 运行时进镜像**

镜像基底 Debian 13 **自带 `/usr/bin/python3.13`（3.13.5）**——
容器内实测确认。这一条推翻了勘察时「必须把 Python 3.13 打进镜像」的判断，
B 方案最大的一块成本本来就不存在。

缺的只是包管理：Debian 的 python3.13 是精简版（无 pip / ensurepip）。
用 `uv` 解决——**它已经是本项目既有的工具**（宿主机 tapd-runtime 就用它），
且能从 PyPI 装（`ghcr.io` 当前不可达，走 PyPI 镜像绕开）。

容器内已实测通过：
```
uv venv --python /usr/bin/python3.13 → CPython 3.13.5
uv pip install "mcp-server-tapd==8.0.80" → 装上、import ok
mcp-server-tapd --help → 入口点可执行
```

**供应链纪律照旧**（`tapd-runtime/PINNED.md`）：钉死 8.0.80 + 记 sha256，
**禁用 `uvx` 拉最新**。上面的验证命令刻意用 `uv pip install 包==版本` 而不是 `uvx`。

**二 · 注册成 stdio 条目**，command 指向容器内那个 venv 的入口点。

**三 · 令牌写入路径放在我们自己的 router**

平台的密钥库有个真缺口：**管理员 MCP 配置面根本没接它**——
`store_secrets` 全仓只有 `space_mcp.py`（每用户空间面）调用，
`mcp_settings.py` 一次都没调。所以在管理员表单里填的 env 值会
**明文落进 `settings/mcp.json` 并从 GET 原样回吐**，与 0011 承诺的
「值不进配置文件」直接冲突。

**但读取半边是通的**（`${secret:...}` 在管理员条目里能解开）。
所以缺的只是一条写入路径，而它**不必改上游**：
`store_secrets(owner_id, server, values)` 是个普通函数，
在我们自己的 router 里调它即可。

**这一步是本决策的关键取舍**：宁可在自己的文件里加一个写入端点，
也不去改上游的 `mcp_settings.py`——后者会新增一个上游触点，
而触点表变长就是架构在往内核里长（0008 三）。

**四 · 退役**：我们自己的 `secrets.env` TAPD 存储 + 单页的令牌输入框。

### 触点影响

| 文件 | 是否新增触点 |
|---|---|
| `Dockerfile` | **否**——已是登记表第 4 条，本次只是在既有 COPY 段里加内容 |
| 我们自己的 router（写入端点） | 否，新增文件 |
| `mcp_settings.py` | **刻意不改** |

**净增触点 0 条。** 这是 B 相对 A 的额外优势：A 维持现状看似省事，
但它让单页永远退不掉，触点与自建配置区都留着。

## 明确不做的事

**不追求「像 GitHub 那种」的每用户 MCP 商店。** 用户原话提到过这个形态，
但那条路今天走不通：每用户面永久拒绝 stdio 类条目，
且地址策略把 loopback / 私网 / `host.docker.internal` 全封了。
本决策的形态是**管理员级单条目**，一个实例一个 TAPD 令牌。

**这带来一个必须说清的限制**：`mcp-server-tapd` 的凭据是模块级单例，
一个进程只能挂一个令牌。所以**每用户令牌隔离在这个方案下做不到**——
TAPD 审计里所有写操作都会归到同一个令牌名下。
现状（网关 `enabled_tools` 只放行两个只读工具）把风险压住了，
本决策**沿用只读白名单**，不因为搬了家就放开写操作。

## 前置安全动作

TAPD 令牌曾以 `uvx mcp-server-tapd --access-token <明文>` 的命令行形式暴露过
（进程列表可见，HANDOFF 遗留）。**迁移时顺带轮换，不要把旧令牌搬过去。**

## 待办

- [ ] Dockerfile 加 uv + 钉死版 mcp-server-tapd 的 3.13 venv
- [ ] 我们 router 里加令牌写入端点（调平台 `store_secrets`，只回显 configured 与末四位）
- [ ] mcp.json 注册 stdio 条目（`${secret:...}` 引用），沿用只读工具白名单
- [ ] 容器内验证：聊天侧真能调到 TAPD 工具（这同时完成 FORK-PLAN 那条「MCP 面复验」）
- [ ] 退役 `secrets.env` 的 TAPD 存储与单页令牌框；同步更新 0011（标注落点一已被本决策取代）

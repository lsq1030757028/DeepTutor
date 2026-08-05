# tapd-runtime 版本钉死记录

本文件是 `mcp-server-tapd` 宿主机运行时的供应链锚点。
依据：`docs/research/mcp-server-tapd-audit-20260804.md` 采用条件 C（版本钉死 + 记录 sha256，
禁 `uvx` 拉最新；每次升版重跑 仓库vs发布物 diff；钉 `mcp` SDK 版本）。

记录时间：2026-08-04。宿主：Windows 11，x86_64。

## 主包

| 项 | 值 |
| --- | --- |
| 包名 | `mcp-server-tapd` |
| 版本 | **8.0.80**（2026-07-29 发布，审计时的 PyPI 最新版） |
| Requires-Python | `>=3.13`（**硬门槛**，pip 直接拒装，不是软 metadata） |
| 声明依赖 | `httpx`、`requests`、`markdown`、`mcp[cli]<2,>1` |
| 控制台入口 | `mcp-server-tapd = mcp_server_tapd.server:main` |

### 发布物 sha256

`pip download --no-deps` 从 PyPI 取回后计算：

```
1a4d3835a015f94d3d8ea10c3125fb11536176e9dfd9b699dca06e5bfe77e458  mcp_server_tapd-8.0.80.tar.gz          (23748 bytes)
f9d4c0467309a59bc3cd1d6b09152d085da4f7acd9e73c15737b680463442760  mcp_server_tapd-8.0.80-py3-none-any.whl (23587 bytes)
```

复核命令（Windows）：

```
pip download --no-deps --no-binary :all: -d <dir> mcp-server-tapd==8.0.80
pip download --no-deps              -d <dir> mcp-server-tapd==8.0.80
certutil -hashfile <dir>\mcp_server_tapd-8.0.80.tar.gz SHA256
certutil -hashfile <dir>\mcp_server_tapd-8.0.80-py3-none-any.whl SHA256
```

## MCP SDK（服务端侧）

`mcp-server-tapd` 只声明 `mcp[cli]<2,>1`，**上界只到 2.0**，1.x 内部会随时间漂移。
本次解析到并锁定：

| 项 | 值 |
| --- | --- |
| `mcp` | **1.29.0** |

重建环境时用 `mcp==1.29.0` 显式约束，别让它自己漂：

```
pip install "mcp-server-tapd==8.0.80" "mcp==1.29.0"
```

注意：验证脚本 `verify_tapd.py` 跑在 test-partner 主 venv 上，那边是 `mcp==2.0.0`
（客户端入口 `streamable_http_client`，两元组，snake_case 结果模型）。
两个 venv 各自独立，协议层兼容，不要为了"统一版本"去动任何一边。

## 完整依赖闭包（`pip freeze`，本 venv 实测）

```
annotated-doc==0.0.5
annotated-types==0.8.0
anyio==4.14.2
attrs==26.1.0
certifi==2026.7.22
cffi==2.1.1
charset-normalizer==3.4.9
click==8.4.2
colorama==0.4.6
cryptography==50.0.0
h11==0.16.0
httpcore==1.0.9
httpx==0.28.1
httpx-sse==0.4.3
idna==3.18
jsonschema==4.26.0
jsonschema-specifications==2025.9.1
Markdown==3.10.3
markdown-it-py==4.2.0
mcp==1.29.0
mcp-server-tapd==8.0.80
mdurl==0.1.2
pycparser==3.0
pydantic==2.13.4
pydantic-settings==2.14.2
pydantic_core==2.46.4
Pygments==2.20.0
PyJWT==2.13.0
python-dotenv==1.2.2
python-multipart==0.0.32
pywin32==312
referencing==0.37.0
requests==2.34.2
rich==15.0.0
rpds-py==2026.6.3
shellingham==1.5.4
sse-starlette==3.4.6
starlette==1.3.1
typer==0.27.1
typing-inspection==0.4.2
typing_extensions==4.16.0
urllib3==2.7.0
uvicorn==0.52.1
```

## 解释器

| 项 | 值 |
| --- | --- |
| 版本 | CPython 3.13.14（`main, Jun 23 2026, 15:19:27` / MSC v.1944 64bit） |
| 来源 | uv 0.12.1 拉的 python-build-standalone，不是系统装的 |
| 位置 | `tapd-runtime/.venv/`（已 gitignore） |

宿主机自带的是 3.12.10，**装不上**：pip 会把 8.0.x 全系列列为
`Ignored ... Requires-Python >=3.13` 然后报 `No matching distribution found`。
实测结论是硬门槛，没有"3.12 也能凑合"的余地。

## 升版清单（不做完不许升）

1. 重跑 **仓库 vs 发布物 diff**。审计报告已记：PyPI 8.0.80 比公开仓库 HEAD（8.0.76）
   多一个完整功能 `program_bind_entities`，公开仓库滞后于发布物。
   PyPI 无 PEP 740 provenance、无 project_urls，信任根是维护者个人 PyPI 账号——
   这条 diff 是唯一的代码级把关手段，不能跳。
2. 重新审 `tapd.py` 的凭据外发点（应只有 TAPD API 的 Authorization 头 + 不带凭据的企微 webhook）
   和 `base_url` 是否仍然无校验。
3. 重算两个发布物的 sha256，更新本文件。
4. 重新解析并记录 `mcp` SDK 版本。
5. 重跑 `verify_tapd.py`，diff 工具名清单——工具名变了，
   req-testing 剧本里引用的工具名要同步改。
6. 禁止 `uvx mcp-server-tapd`：那是每次拉最新，绕过以上全部关卡。

# 构建与等价性验证笔记（P1 产出）

本仓是 `HKUDS/DeepTutor` 的私有二开副本。本文件只记**从源码构建镜像**这件事上
非显而易见、且踩过坑的部分。上游自己的部署文档见 `CONTAINERIZATION.md`。

## 构建命令（照抄，三个参数缺一不可）

```bash
docker build --target production \
  --ulimit nofile=1024:65536 \
  --add-host=host.docker.internal:host-gateway \
  --build-arg HTTP_PROXY=http://host.docker.internal:7890 \
  --build-arg HTTPS_PROXY=http://host.docker.internal:7890 \
  --build-arg NO_PROXY=localhost,127.0.0.1 \
  -t deeptutor:ut1.5.8-local .
```

### 1 · `--target production` —— **不带它构出来的是开发镜像**

Dockerfile 最后一个阶段是 `FROM production AS development`（第 444 行）。
`docker build` 不指定 `--target` 时构建**最后一个** stage，于是拿到 development：
它把完整 `node_modules`、源码版 `web/package.json`、`web/next.config.js` 加了回去，
supervisord 跑的是 `node scripts/dev.mjs` 而不是 `node /app/web/server.js`。

后果是**静默的**：镜像构建成功、健康检查还能过（后端起来了），
只有前端进程反复 FATAL、宿主访问 3782 连不上。若不做等价性验证，
后面所有工作会建在一个开发镜像上。

产物大小是最快的自查信号：**production ≈ 1.36 GB，development ≈ 2.29 GB**。

### 2 · `--ulimit nofile=1024:65536` —— 不带它 apt 会被 OOM 杀掉

Docker Desktop 给容器的默认 `nofile` 是 **1048576**。apt-get 按这个上限遍历文件描述符，
实测 `anon-rss` 冲到 **13–15 GB** 后被内核 OOM killer 干掉：

```
Out of memory: Killed process 6225 (apt-get) anon-rss:13214200kB
Out of memory: Killed process 7280 (apt-get) anon-rss:15103208kB
```

BuildKit 把它报成 `ResourceExhausted`，字面上看像磁盘满——**不是**，
当时 docker 数据盘还剩 948 GB。判定方法：进 VM 看内核日志

```bash
docker run --rm --privileged --pid=host alpine nsenter -t 1 -m -u -i -n -- dmesg | grep -i "out of memory"
```

### 3 · 代理相关参数 —— 网络环境相关，不是所有机器都需要

本机直连 `deb.debian.org` 会间歇性拿到 **404**（CDN 边缘节点问题），
表现为 `Failed to fetch .../binary-amd64/Packages 404 Not Found`。走本机代理稳定。
**在网络正常的环境（含 CI）可以去掉这三个 `--build-arg` 与 `--add-host`。**

## 等价性验证（P1 判据）

脚本比对「自建镜像」与「官方 `ghcr.io/hkuds/deeptutor:latest`」，
运行态用 `deeptutor-data` 的**副本卷** `deeptutor-data-verify`，独立端口 3783，
**全程不碰现役 `deeptutor` 容器与 `deeptutor-data` 卷**（第 0 步先自证）。

**2026-08-05 结果：14/14 PASS，判定等价。**

覆盖面：

| 面 | 项 |
|---|---|
| 镜像层 | `deeptutor/**/*.py` 排序哈希、`__version__.py`、三个启动脚本哈希 |
| 运行态 | 容器健康检查 |
| 主路径 | 前端首屏 200、`/api/v1/auth/status` 结构化响应且字段集与官方一致、副本卷数据可读、MCP 注册表条目在 |

### 两条断言写坏过，记在这里

1. 最初查 `/api/v1/auth/me` 并判「非 5xx 即通过」——**那个端点根本不存在**，
   404 也算过，等于恒真。改成真实存在的 `/api/v1/auth/status` 并断言响应体形状 + 与官方字段集一致。
2. 曾打算比对 Next.js `buildId`——它默认每次构建随机生成，两边必然不同，
   会因为一个无害原因判失败。改成比对渲染出的语义形状（标题 / 语言 / 挂载点）。

**因不存在而恒真的断言，和因随机数而恒假的断言，都是坏闸。**

## 上游锚

`ut1.5.8-base` → `44fa7a1`（上游 v1.5.8）。
现役镜像 OCI 标签写的 `revision=5a197bd…` **在上游仓不可达**，不可用作锚点；
锚点靠内容对账确定（容器内 `deeptutor/**/*.py` 排序哈希 `0a24bf09c346b951…` 唯一命中 v1.5.8）。

---

## P3 骨架端到端验证（2026-08-07）

脚本：`extensions/test-partner/scripts/verify_p3_skeleton.sh`。**14/14 PASS。**
同样用副本卷 `deeptutor-data-verify` + 独立端口 3784，第 0 步先自证现役实例未被触碰。

覆盖面比 P1 多的三处，都是踩过或差点踩的坑：

| 查什么 | 为什么要查 | 结果 |
|---|---|---|
| `extensions/test-partner/server/` 里有几个 `.py` | P1 挖出来的坑：镜像构建成功、健康检查也过，但 extensions 整个不在里面 | 15 个 |
| `skills/` 下 `SKILL.md` 在不在 | `.dockerignore` 有 `*.md` 规则。Docker 的 `*` 不跨 `/`，理论上只排根目录——**但这条不靠推理，直接查** | 2 份，没被误伤 |
| `tests/` `docs/` `tapd-runtime/` 有没有混进去 | COPY 范围过宽会让镜像带上不该带的东西 | 三个都不在 |

其余：前端产物含 `(utility)/test-workbench/page.js`（说明 `next build` 真编了这个路由）、
容器 healthy、`/api/v1/test-workbench/health` 返回 `extension_loaded:true`
（**sys.path 引导在容器里也成立**，不只是本机）、`/test-workbench` 返回 200 且 HTML 里有工作台标识。

### 一处必须说清的验证边界

第 6 步只看到 `/app/data/test-workbench`——那是 **admin 兜底路径**，
因为该实例 `AUTH_ENABLED=false`、没有当前用户。

**所以「两个真实用户互相看不见」这件事，容器里没有被端到端验过。**
它由单元测试覆盖（`tests/test_api_router.py::test_two_users_get_different_delivery_roots`，
且已反向验证：故意让两用户共用 root 立刻转红）。
开启鉴权后的多用户实测，留到有真实多账号环境时补。

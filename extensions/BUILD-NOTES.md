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

---

## CI 不可用期间的补跑（2026-08-07）

GitHub Actions 仍被停（所有 job 零步骤、分配不到 runner，手动触发同样如此），
按决策 0008「CI 不可用期间的临时口径」在本地补跑替代证据。
**真正的追认仍欠着**——Actions 恢复后必须对本期间合入的代码变更补跑一次全量。

### 跑了什么

| 闸 | 结果 | 怎么跑的 |
|---|---|---|
| **i18n parity**（硬闸） | **OK** | `node scripts/i18n_parity.mjs`，零依赖可直接跑 |
| i18n audit | exit 0 | `node scripts/i18n_audit.mjs` |
| i18n 占位符规则 | **0 违规** | 按 `tests/i18n-placeholders.test.ts` 里的真 `NAMESPACE_KEY` 正则复算全库 |
| **我们的 pytest 全量** | **679 passed** | `extensions/test-partner` 本地 |
| 上游 7 条 import check | **7/7 pass** | 在 `deeptutor:p3-skeleton` 镜像内按 `tests.yml` 原样跑 |
| 路由注册 | **3 条工作台路径在 OpenAPI 里** | 镜像内 `app.openapi()` |
| 上游 `tests/api` | **264 passed / 2 failed** | 镜像内挂仓库跑，失败已归因（见下） |

### 两条红的归因：不是我们的

`tests/api/test_cors_settings.py` 两条断言 `allow_origin_regex is None` 失败。

**判法**：把 P3 代码全部移除（删 `test_workbench.py`，`main.py` 用
`git show ut1.5.8-base:deeptutor/api/main.py` 还原成上游 504 行原版），
在**基线镜像** `deeptutor:ut1.5.8-local` 里跑同样两条 —— **照样红**。

故与 P3 无关。至于它为什么红，怀疑是本补跑的 harness 预先播种了
`data/user/settings/auth.json`（AUTH_ENABLED=false）导致 CORS 走 permissive，
**但这一步没有进一步验证，只记为怀疑，不当结论**。

### 没跑的，如实列

| 没跑 | 原因 |
|---|---|
| `npm run test:node`（前端 50 个测试） | `web/node_modules` 未安装 |
| eslint | 同上 |
| 上游其余 ~320 个测试文件 | 只跑了 `tests/api` 这一个直接受影响的子集 |

### 一处自我纠错

一度按 `app.routes` 过滤 `path` 找不到工作台路由，差点当成"路由没挂"。
实际是这版 DeepTutor 用 `_IncludedRouter`（32 个）惰性包装，静态取不到 `.path`。
**权威来源是 `app.openapi()` 的 paths**，不是 `app.routes` 的属性——查错了地方。

---

## P3 全链路端到端验证（2026-08-07）

脚本：`extensions/test-partner/scripts/verify_p3_flow.sh`。**19/19 PASS。**
同样用副本卷 `deeptutor-data-verify` + 独立端口 3785，第 0 步先自证现役实例未被触碰。

### 这次和骨架验证的差别：跑了真模型

该实例已配模型（`model_ready: true`），所以第 8 步不是走空态分支，
而是**真发起了一次生成并跑完**。结果：

| 项 | 实测 |
|---|---|
| 生成 | 8 条用例，`complete=true`，共 4 次模型调用 |
| AI 挑端点 | 自己挑了 2 个，每个带 `why`；`skipped_reason` 有值 |
| 断言质量 | **8 条全部有响应体断言**，没有一条只断言 status |
| 意图分布 | 正常 / 异常 / 鉴权 都有 |

### 出境闸的实证

模型起草的场景原文：

> 先登录账号，然后提交了一个包含SKU-1、数量2和 **`<手机号>`** 的订单创建请求。

两件事同时被证明了：

1. **PII 没有出境**——模型看到的是 `<手机号>` 而不是 `13800138000`（BB-430 的闸生效）；
2. **模型照抄了占位符，没有编造一个真手机号**——这正是三段提示词里那条
   「占位符是脱敏结果，原样保留，绝不要编造真实值」要的效果。不写那句的话，
   模型会自作主张填一个像样的号码，用例就带上假数据。

全量结果里逐个搜过：手机号真值、Bearer 令牌、明文密码，**三个都没出现**。

### 两处第一版写错的检查（记下来，免得下次再踩）

1. `/openapi.json` 打到了**前端**端口。前端只转发 `/api/*` 与 `/ws/*`，
   `/openapi.json` 在那一面本来就是 404——要打容器内后端的 `:8001`。
   当时能力探测那条是通的，正是靠它才看出"路由是活的、是检查查错了面"。
2. HAR 上传用 `mktemp` 的路径，curl 在 Git Bash 下吃不进去，
   于是文件根本没发出去、端点正确地报了 `Field required: file`。
   **两条都是测试写错不是产品坏了**——但这个判断是靠"手工直接打一次端点成功"
   证出来的，不是靠读代码猜的。

---

## P3 执行/导出/环境面端到端验证（2026-08-08）

脚本：`extensions/test-partner/scripts/verify_p3_workbench.sh`。**27/27 PASS。**
同样用副本卷 + 端口 3785，第 0 步先自证现役实例未被触碰。

覆盖面：新 8 条路由在 openapi、环境金库建/删/去值投影/掩码、金库落点实测在
`owner_secrets_dir`（0011 落点二）、执行起跑→轮询→逐条结论→**真通过**
（夹具用例打容器内后端自己的 health 端点，断言 status+body 双过）、
报告落盘进批次目录、四格式导出、单文件下载、zip 打包、路径穿越被拒。

浏览器实测另走了一遍界面（批次列表→详情→环境表单→执行→导出面板→下载清单），
**中文环境名经 UI 的 UTF-8 路径保存成功**——这条 Git Bash curl 验不了
（CJK 被按 GBK 发出，FastAPI 报 body 解析错，是 harness 限制不是产品缺陷）。

### 这轮验证挖出的真缺陷（已修，bug-bank BB-435）

执行跑通但 `execution_report.json` **落不进批次目录**：
`execute._resolve_report_dir` 的防任意写闸只认扩展自带的 `deliveries/` 模块常量，
宿主部署下每用户批次根在 `/app/data/...`，合法目录被当"任意路径"拒掉，
报告 fallback 到镜像内只读路径直接 PermissionError。
修法与 `save_delivery` 的 `out_root` 同构：`execute_cases` 增 `deliveries_root`
注入参（不上 MCP 工具面），`RunRegistry` 传台账同根。
**单测测不出它**——所有单测都用模块常量根；这类"多部署形态下常量与语境脱节"
只有真容器形态能暴露，这正是每轮改动都要重跑容器级验证的理由。

### 三个 harness 坑（都不是产品问题）

1. Git Bash 的 curl 发 CJK JSON 会变 GBK 字节——脚本里凡带中文的 payload 用 ASCII 或走 python。
2. `docker exec` 不带 `-i` 时 heredoc 整个被丢，python 静默跑空脚本——夹具"造了"其实没造。
3. Windows 版 curl 把 `/tmp/...` 当别的盘符路径，`-o` 的文件根本没写出来——落盘用相对路径。

# 勘察：DeepTutor 扩展点（P3 前置）

- 日期：2026-08-05
- 对象：上游 `v1.5.8`（`44fa7a1`），本地工作副本 `D:\Test_Coding\DeepTutor`
- 目的：回答 FORK-PLAN 风险台账 #2「DeepTutor 前端耦合深、组件不可复用」到底成不成立
- 结论：**不成立。工作台可以做成原生页面，改上游既有文件只需 2 处、约 10 行。**

## 为什么先勘察

FORK-PLAN P3 明写「前置勘察独立成步，先勘察再动手，不许边做边猜」。
0007 之所以立，正是因为上一轮在「DeepTutor 没有 UI 扩展点」这个判断上一路推到了自建前端——
那个判断在当时的约束（不改内核）下是对的，但没人回头验它在新约束下还成不成立。
这次先把结论钉在证据上。

## 应用形态

单容器跑两个进程，`supervisord` 托管（见上游 `CONTAINERIZATION.md`）：

| | |
|---|---|
| 后端 | FastAPI，`:8001` |
| 前端 | Next.js 16 standalone，`:3782` |
| 前后端连接 | `web/proxy.ts` 拦 `/api/*` 与 `/ws/*`，请求时改写到 `DEEPTUTOR_API_BASE_URL` |
| 基础镜像 | `python:3.11-slim` |

浏览器只跟 `:3782` 说话，URL 不烘进前端 bundle——这对我们是好消息：
新增 API 走同一条 `/api/*` 通道，不需要动代理层。

## 前端：新增页面的形态

App Router，四个路由组：`(workspace)` / `(admin)` / `(auth)` / `(utility)`。

`(workspace)` 下每个功能就是一个目录，自带 `components/`：

```
web/app/(workspace)/
  book/        page.tsx + components/(14 个 block 组件)
  co-writer/   …
  home/  partners/  playground/
```

**加一个工作台 = 新建 `web/app/(workspace)/workbench/`**（`page.tsx` + 自己的 `components/`），
与上游零冲突。

导航入口集中在一处：`web/components/sidebar/SidebarShell.tsx` 的 `PRIMARY_NAV` / `SECONDARY_NAV`
两个数组，每项是个对象：

```ts
{ href: "/space", label: "Learning Space", icon: LayoutGrid, tooltipKey: "Space tooltip" }
```

加一项 ≈ 7 行 + 一个 icon import。**这是前端唯一需要改的既有文件。**

### 设计系统的实际情况

`web/components/ui/` 只有三件：`Button` / `ConfirmDialog` / `Tooltip`。
样式主体是 **Tailwind**（`tailwind.config.js` + `app/globals.css`），组件按功能域分在
`components/<域>/` 下，不是那种「全套设计系统包」。

**对我们的含义**：没有厚重的组件库要学，但也没有现成的表格/表单组件可白捡——
工作台的表格、进度、结果视图要自己写，用 Tailwind 对齐它的视觉语言。
`book/components/blocks/` 那 14 个组件是最好的抄写样本（同为「数据驱动的结构化视图」）。

## 后端：新增 API 的形态

路由集中注册在 `deeptutor/api/main.py`（504 行），每个域一行：

```python
app.include_router(chat.router, prefix="/api/v1", tags=["chat"], dependencies=_auth)
```

**加我们的 API = 新建 `deeptutor/api/routers/<我们的>.py` + `main.py` 里加 1 行 include_router（+1 行 import）。**
鉴权用现成的 `dependencies=_auth`，不自己造。

## 触点预算（P3 预计）

| 文件 | 改动 | 能否用新增文件替代 |
|---|---|---|
| `web/components/sidebar/SidebarShell.tsx` | +1 个 NavEntry，约 7 行 | 否——导航注册是集中式数组，没有插件位 |
| `deeptutor/api/main.py` | +1 行 include_router，+1 行 import | 否——同上，路由注册是集中式 |

**合计 2 个文件、约 10 行**，其余全是新增文件。
这个量级的触点，上游 merge 时冲突面极小，也印证了 0008 一「单仓」的可行性。

> 两处都是「集中式注册表」这一种模式。若日后触点表长出第三种模式（比如改了组件内部逻辑、
> 改了构建配置），那才是架构在往内核里长的信号——按 0008 三处置。

## 尚未勘察（P3 动手前补）

- i18n：`web/i18n/` + `web/locales/`，新页面的文案要不要走它们的 parity 检查（`npm run i18n:check`）
- 前端既有测试：`web/tests/` + `playwright.config.ts`，我们的页面要不要挂进去
- 鉴权：`_auth` 依赖具体是什么、工作台该用哪一档
- `(workspace)/layout.tsx` 对子页面有无布局约束

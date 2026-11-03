# 上游同步手册（P5 演练产出）

> 本文是**照着做就能同步一次**的操作手册，不是通用模板——每一步的取舍都来自
> 2026-08-08 那次 `ut1.5.8-base → v1.5.10` 的真实演练，量出来的数字与踩到的坑都在。
>
> 配套：`extensions/UPSTREAM-TOUCHPOINTS.md`（触点登记表，同步前必读）。

## 演练结论（2026-08-08，`ut1.5.8-base` → `v1.5.10`）

| 量 | 实测 |
|---|---|
| 上游改动文件 | 4454 |
| **其中误提交的构建产物** | **4322**（`web/.next-deeptutor/`，见下方坑一） |
| 真正的源码改动 | 132 |
| **文本冲突** | **0** |
| 我们的触点被波及 | 3 个（`deeptutor/api/main.py` + 两个 `app.json`），全部自动合并成功 |
| 合并后回归 | 扩展 864 passed；上游 `tests/api` 284 passed（合并前 262，上游新增 22） |

**触点登记表经此演练验证为准确**：表里 6 条覆盖的正好是我们改过的 7 个上游文件，
无漏登记、无因上游重命名而失效的条目。

这个结论的可信区间：**只对"上游没动我们改过的那几行"这一情形成立。**
一次零冲突不等于下次零冲突——`main.py` 的 router 注册区和两个 `app.json` 的尾部
是天然的高频冲突带，上游一旦在同位置插入就会撞。

---

## 操作步骤

### 0 · 前置：别在正在用的工作树上做

演练与真同步都**另开 worktree**。主工作树可能正被并发的 agent 或你自己写着，
merge 会把未提交改动卷进冲突判定，事后分不清哪个冲突是上游带来的。

```bash
git worktree add -b sync/upstream-vX.Y.Z ../DeepTutor-syncdrill feat/你的当前分支
```

### 1 · 取上游

```bash
git fetch upstream --tags
git log --oneline upstream/main -1
```

网络不稳时这一步会 `Recv failure: Connection was reset`——重试即可，
本机实测同一条命令连续失败两次后第三次成功。**不要因为 fetch 失败就改用
本地已有的 tag 当"上游最新"**：本地 tag 是 fork 克隆时带来的历史快照，
上游后来发的版本它没有（演练时正是这样，本地最新 tag 是 v1.5.9，
上游实际已到 v1.5.10）。

### 2 · 先干跑，量冲突面

```bash
git merge --no-commit --no-ff upstream/main
git diff --name-only --diff-filter=U | wc -l      # 冲突文件数
git diff --cached --name-only | wc -l             # 合入文件总数
```

冲突数不为 0 时，逐个对着 `UPSTREAM-TOUCHPOINTS.md` 核：
表里有的按"上游若变更如何处置"那一列处理；**表外出现的冲突是警报**——
说明有人改了上游文件却没登记，先补登记再解冲突。

### 3 · 坑一：剔除上游误提交的构建产物

```bash
git rm -r --cached web/.next-deeptutor
rm -rf web/.next-deeptutor
```

上游 commit `b7b15ccc`（一个只改 2 行 `auth.py` 的 refactor）把 Next.js 的
构建产物 **4322 个文件 / 约 95 MB** 一并提交进了仓库。

我们的 `.gitignore` 第 79 行**有** `web/.next-deeptutor/` 这条规则，
但它救不了——**已跟踪文件不受 gitignore 约束**。不显式 `git rm --cached`
就会把 95 MB 拖进我们仓，并且此后每次同步都再拖一次。

这一步没有替代品，也不能靠"下次上游会修"来省掉：只要上游那些文件还在跟踪状态，
**每次同步都要做一遍**。

### 4 · 验触点没被合坏

三条最小断言（演练时实际跑的就是这三条）：

```bash
python - <<'PY'
import ast, json
src = open("deeptutor/api/main.py", encoding="utf-8").read()
ast.parse(src)                                    # 语法没被合坏
assert "test_workbench.router" in src             # 我们的 router 还在
assert "dependencies=_auth" in src                # 鉴权依赖还在（决策 0009 硬约束）
en = json.load(open("web/locales/en/app.json", encoding="utf-8"))
zh = json.load(open("web/locales/zh/app.json", encoding="utf-8"))
assert set(en) == set(zh), "i18n parity 漂了"      # CI 硬闸，本地先自查
assert "Test Workbench" in zh                      # 我们的词条没被覆盖掉
print("触点完好")
PY
```

`dependencies=_auth` 那条尤其不能省：裸挂不会报错，只会**静默把所有人的
落盘写进 admin 工作区**（决策 0009）。合并冲突解错时最容易丢的就是这个参数。

### 5 · 跑回归

```bash
git commit                                        # 先落合并提交
bash extensions/test-partner/scripts/regression_gate.sh
```

四层全绿才算同步成功。演练时的基线：扩展 864、上游 `tests/api` 284。

**一处待观察**：演练中 `tests/api` 第一次跑在收集阶段报过一次
`test_co_writer.py - FileNotFoundError: Configuration file not found`，
随后两次（含单独跑该文件）都通过，**未能复现**。记在这里是因为它可能是
真实的测试间污染，下次同步若再出现就当作真问题追，不要当噪声划掉。

### 6 · 收尾

- 更新 `UPSTREAM-TOUCHPOINTS.md`：本次是否新增/失效了触点
- 在 `extensions/BUILD-NOTES.md` 记一次同步记录（版本、冲突数、回归数字）
- 演练用的 worktree 用完即删：`git worktree remove ../DeepTutor-syncdrill`

---

## 坑二：同步 PR 触发不了我们自己的闸（已修）

一次典型的上游同步只改 `deeptutor/**` 与 `web/**`。而 `test-partner.yml`
原本只挂 `extensions/test-partner/**`——于是**改动了工作台的挂载点
`deeptutor/api/main.py`，却触发不了我们那 850+ 例**，「不绿不合」在同步 PR 上
是一句空话。

已在 2026-08-08 修：`test-partner.yml` 的 `paths` 补了三条宿主接线面
（`deeptutor/api/routers/test_workbench*.py`、`deeptutor/api/main.py`、`Dockerfile`）。
刻意**不**整个 `deeptutor/**` 兜底——那样每次上游同步都全量跑，
0008 的额度纪律就白立了。

## 为什么不 rebase

`main` 是共享分支，圆桌线也在往上合。rebase 会重写已推送的历史，
另一条线拉下来就是一堆"凭空消失又重现"的提交。这一条在 0008 里已定，
本手册只是重申：**同步一律 merge**。

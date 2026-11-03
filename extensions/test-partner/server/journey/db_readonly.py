# -*- coding: utf-8 -*-
"""db_readonly — L3 只读数据层通道（设计稿 §6.2）。

## 两道互相独立的锁，不是一道

| 层 | 谁执行 | 挡什么 | 失效时另一层还在吗 |
|---|---|---|---|
| 语句白名单（本模块 `check_statement`） | 我们的进程 | 语句压根发不出去 | 在（服务端仍是只读事务） |
| 只读会话（`default_transaction_read_only=on` + `set_session(readonly=True)`） | 数据库服务端 | 语句发出去了也执行不了 | 在（白名单先拦了一遍） |

分两层是因为它们的失效方式不同：白名单会被新语法绕过（见下节），
会话标志会被"换了个连接构造"绕过。**一层的漏洞不是另一层的漏洞**。

## 白名单为什么不能只看第一个关键字

Postgres 的 **data-modifying CTE** 是这道题的正确答案，也是只看首关键字必然漏的那个：

```sql
WITH gone AS (DELETE FROM character.custom_characters RETURNING *) SELECT * FROM gone
```

它以 `WITH` 开头、以 `SELECT` 结尾，任何"首关键字是 SELECT/WITH 就放行"的白名单都会放它过去，
而它删表。同族的还有 `WITH x AS (INSERT ... RETURNING *) SELECT ...`。

所以本模块的判据是：**归一化之后，全句任何位置都不许出现变更类关键字**。归一化包含
三步，缺一步就有对应的绕法：

1. **去注释**（`--` 行注释、`/* */` 块注释）——否则 `/*harmless*/DELETE FROM t` 里
   前缀看着无害，或者 `SELECT 1 --\n; DROP TABLE t` 用注释藏掉分号；
2. **去字符串字面量**——否则 `SELECT * FROM audit.events WHERE event_type='user.delete'`
   会被自己的数据判红（假红），而假红多了白名单就会被人放宽，最后变成真漏；
3. **单语句**——分号分隔的多语句里，第二条可以是任何东西。

## 0029 §三第 4 条：即使实测有 DML 也不放开

2026-08-12 实测该账号在 38/38 张表上有 INSERT/UPDATE/DELETE。**处置不因此放宽**：
业务写走应用层，绕过应用层直接改库会让被测行为失真（改出来的状态不是产品逻辑能产生的状态，
基于它的断言测的是幻觉）。本模块因此不提供任何写入口——不是"默认关闭"，是没有。
"""
from __future__ import annotations

import os
import re
from typing import Any

#: 执行期注入的只读 DSN 环境变量名。**不进 bundle、不落盘**——与 TP_VARS_JSON 同纪律。
DSN_ENV = "TP_DB_READONLY_DSN"

#: 变更类关键字。归一化后全句任何位置命中即拒。
#:
#: 词以 `\b` 界定，所以 `updated_at` / `deleted_flag` 这类列名不会误伤
#: （`update` 后面跟的是词字符，`\b` 不成立）。这一点由测试钉住，别改成子串匹配。
_FORBIDDEN = (
    "insert", "update", "delete", "merge", "upsert",
    "drop", "truncate", "alter", "create", "rename",
    "grant", "revoke", "reassign",
    "copy", "call", "do", "vacuum", "analyze", "reindex", "cluster",
    "lock", "commit", "rollback", "savepoint",
    "set", "reset", "discard", "listen", "notify", "prepare", "execute",
    "refresh", "import", "security",
)
_FORBIDDEN_RE = re.compile(r"\b(" + "|".join(_FORBIDDEN) + r")\b", re.I)
_DANGEROUS_FUNCTION_RE = re.compile(
    r"\b(?:pg_sleep|pg_advisory_(?:lock|lock_shared|unlock|unlock_all|unlock_shared)"
    r"|pg_read_(?:file|binary_file)|pg_ls_dir|lo_(?:import|export)|dblink(?:_exec)?)\s*\(",
    re.I,
)
STATEMENT_TIMEOUT_MS = 15_000
LOCK_TIMEOUT_MS = 3_000

#: 允许的首关键字。`show` 留着是因为设计稿 §6.2 明写 `只允许 SELECT/SHOW`。
_ALLOWED_HEAD = ("select", "with", "show", "table", "values")

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT = re.compile(r"--[^\n]*")
_SQ_STRING = re.compile(r"'(?:[^']|'')*'")
_DQ_IDENT = re.compile(r'"(?:[^"]|"")*"')
#: dollar-quoted 串。两种形态分开写：带 tag 的用反向引用配对，
#: 裸 `$$...$$` 单列——把它们合成一条正则时 `\1` 在未参与分组下不匹配，
#: 于是 `$$ ... $$` 会整个漏过归一化（那正好是藏语句的最佳位置）。
_DOLLAR_QUOTED = re.compile(r"\$([A-Za-z_]\w*)\$.*?\$\1\$|\$\$.*?\$\$", re.S)


class DbChannelError(Exception):
    """通道级问题（缺 DSN / 缺驱动 / 连不上）。调用方必须映射成 BLOCKED，不得当跳过。"""


class DbStatementRejected(Exception):
    """语句没过白名单。这是**我们拒发**，不是数据库拒执行。"""


def normalize(sql: str) -> str:
    """去注释、去字符串字面量、去标识符引号，压空白。供白名单判据使用。

    注意去除顺序：先 dollar-quoted（它内部可以含任何字符，包括 `--` 和 `'`），
    再块注释，再行注释，最后字符串。顺序换了就会有一种藏法漏出去。
    """
    s = _DOLLAR_QUOTED.sub(" '' ", sql)
    s = _BLOCK_COMMENT.sub(" ", s)
    s = _LINE_COMMENT.sub(" ", s)
    s = _SQ_STRING.sub(" '' ", s)
    s = _DQ_IDENT.sub(" ident ", s)
    return re.sub(r"\s+", " ", s).strip()


def check_statement(sql: Any) -> None:
    """白名单。过 = 静默返回；不过 = 抛 `DbStatementRejected`，理由说清是哪一条。"""
    if not isinstance(sql, str) or not sql.strip():
        raise DbStatementRejected("SQL 必须是非空字符串")
    norm = normalize(sql)
    if not norm:
        raise DbStatementRejected("整条语句去掉注释后是空的——这通常意味着有人在用注释藏东西")

    body = norm[:-1].strip() if norm.endswith(";") else norm
    if ";" in body:
        raise DbStatementRejected(
            "只允许单条语句。分号后面可以跟任何东西，多语句白名单等于没有白名单。"
            f"归一化后：{body!r}")

    head = body.split(" ", 1)[0].lower().strip("(")
    if head not in _ALLOWED_HEAD:
        raise DbStatementRejected(
            f"首关键字 {head!r} 不在只读白名单 {list(_ALLOWED_HEAD)} 里")

    hit = _FORBIDDEN_RE.search(body)
    if hit:
        raise DbStatementRejected(
            f"归一化后出现变更类关键字 {hit.group(1)!r}。"
            f"**判据是全句而不是首关键字**——Postgres 的 data-modifying CTE "
            f"（WITH x AS (DELETE ... RETURNING *) SELECT * FROM x）以 WITH 开头、"
            f"以 SELECT 收尾，却真的删数据。若这是列名/字面量误伤，"
            f"说明归一化漏了一种引号形态，去修 normalize()，不要放宽关键字表。")
    function_hit = _DANGEROUS_FUNCTION_RE.search(body)
    if function_hit:
        raise DbStatementRejected(
            f"只读语句调用了禁止的资源/文件函数 {function_hit.group(0).strip()}。"
            "SELECT 不等于无副作用；休眠、advisory lock、文件读取与 dblink 都不得由测试配方调用。")


def dsn_present() -> bool:
    return bool(os.environ.get(DSN_ENV, "").strip())


def connect(dsn: str = "") -> Any:
    """建只读连接。缺 DSN / 缺驱动 / 连不上一律抛 `DbChannelError`。

    **不做静默降级**：没有"连不上就跳过 DB 断言"这条路——那正是护栏 3 点名的
    静默降级，会让一条从没验过数据的用例显示成绿的。
    """
    dsn = dsn or os.environ.get(DSN_ENV, "").strip()
    if not dsn:
        raise DbChannelError(
            f"{DSN_ENV} 未注入。L3 通道的 DSN 只在执行期经环境变量进子进程内存，"
            f"不写 bundle、不落盘；没有它就是没有 L3，按能力锁显式缺口处理。")
    try:
        import psycopg2  # noqa: PLC0415 - 可选依赖，缺失要能分类报错
    except ImportError as exc:
        raise DbChannelError(
            "psycopg2 未安装：L3 只读通道不可用。这是环境缺件不是用例失败，"
            "按 BLOCKED 记账，不得降级成跳过或通过。") from exc
    try:
        conn = psycopg2.connect(
            dsn,
            connect_timeout=15,
            # 服务端层的锁。与白名单互相独立：白名单被绕过时它还在。
            options=(
                "-c default_transaction_read_only=on "
                f"-c statement_timeout={STATEMENT_TIMEOUT_MS} "
                f"-c lock_timeout={LOCK_TIMEOUT_MS} "
                f"-c idle_in_transaction_session_timeout={STATEMENT_TIMEOUT_MS}"
            ),
        )
        conn.set_session(readonly=True, autocommit=True)
    except Exception as exc:  # noqa: BLE001 - 驱动异常族杂，统一归类为通道问题
        raise DbChannelError(f"只读库连接失败：{type(exc).__name__}") from exc
    return conn


def verify_readonly_session(conn: Any) -> dict[str, Any]:
    """确认只读会话真的生效，**不靠假设**。

    正向对照用 `SHOW default_transaction_read_only`。
    反向对照（锁真的挡得住吗）由靶侧探针单独做（本轮落在
    `agent-lab-runs/deeptutor-absorb-m2-20260810/evidence/m2-stage3/l3_channel_probe.py`），
    用的是 `CREATE TEMP TABLE` —— 选它是因为**锁一旦失效，它的后果是零**
    （临时表随会话消失，碰不到任何人的数据）。拿 INSERT 去试的话，
    "验锁"本身就成了那个要防的风险。
    """
    cur = conn.cursor()
    cur.execute("SHOW default_transaction_read_only")
    flag = cur.fetchone()[0]
    cur.close()
    return {"default_transaction_read_only": flag, "ok": str(flag).lower() == "on"}


def query(conn: Any, sql: str, params: Any = None, *, limit: int = 500) -> dict[str, Any]:
    """跑一条过了白名单的只读查询。返回 {columns, rows, row_count}。

    `row_count` 是**本次返回的行数**，受 `limit` 截断影响 —— 所以计数类断言
    不要用它，要用 `SELECT count(*)` 让数据库去数。这一条写在这里是因为
    "拿被截断过的结果下计数断言"与本线那条取证纪律（穷尽断言不得建立在
    截断输出上）是同一个错误，只是换了层。
    """
    check_statement(sql)
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        cols = [d[0] for d in (cur.description or [])]
        rows = cur.fetchmany(limit)
        return {
            "columns": cols,
            "rows": [list(r) for r in rows],
            "row_count": len(rows),
            "truncated": len(rows) >= limit,
        }
    finally:
        cur.close()


def scalar(conn: Any, sql: str, params: Any = None) -> Any:
    """取标量（守恒量取数用）。零行返回 None；多行/多列取第一格并如实标注。"""
    res = query(conn, sql, params, limit=2)
    if not res["rows"]:
        return None
    return res["rows"][0][0]


__all__ = ["DSN_ENV", "DbChannelError", "DbStatementRejected", "check_statement",
           "connect", "dsn_present", "normalize", "query", "scalar",
           "verify_readonly_session"]

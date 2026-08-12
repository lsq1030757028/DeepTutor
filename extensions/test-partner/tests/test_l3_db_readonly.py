# -*- coding: utf-8 -*-
"""L3 只读数据层通道的判据（设计稿 §6.2）。

本文件的重点不是"白名单能放行 SELECT"，而是**它到底挡不挡得住**：
每一条绕法都单列一例，且都是真存在的绕法，不是想象出来的。
"""
from __future__ import annotations

import json

import pytest

from server.journey import db_readonly as dbro
from server.journey.gates import track_purity


def test_conservation_runtime_retains_before_after_delta_for_evidence(tmp_path,
                                                                     monkeypatch):
    """守恒断言通过后必须有可独立落盘的 before/after/delta，不只是一格 PASS。"""
    from server.journey import pw_harness, pw_runtime

    ctx = {"base_url": "http://x", "variables": {"baseUrl": "http://x"},
           "run_dir": str(tmp_path), "write_authorized": set(),
           "done_cases": set(), "scrub_pairs": []}
    runner = pw_runtime.CaseRunner(
        ctx, {"case_id": "x/R1-C001", "track": "api"}, page=None)
    values = iter([10, 11])
    monkeypatch.setattr(runner, "_db_query", lambda _a: {
        "rows": [[next(values)]], "row_count": 1, "truncated": False})
    runner._op_db_snapshot({"metric": "roles", "sql": "SELECT count(*) FROM roles"})
    runner._op_expect_db_delta({"metric": "roles", "delta": 1})

    monkeypatch.setattr(pw_harness, "_CTX", ctx)
    pw_harness._finish(runner, {"evidence_dir": "x__r1__c001"})
    evidence = json.loads(
        (tmp_path / "x__r1__c001" / "db_snapshot.json").read_text(encoding="utf-8"))
    metric = evidence["metrics"]["roles"]
    assert metric == {
        "sql": "SELECT count(*) FROM roles", "before": 10, "after": 11,
        "delta": 1, "expected_delta": 1, "passed": True,
    }
    assert "_query" not in json.dumps(evidence)


# ── 白名单：放行面 ────────────────────────────────────────────────────────
@pytest.mark.parametrize("sql", [
    "SELECT 1",
    "select count(*) from character.custom_characters",
    "SELECT * FROM auth_app.users WHERE created_at > now() - interval '7 days'",
    "WITH recent AS (SELECT id FROM character.custom_characters) SELECT count(*) FROM recent",
    "SHOW default_transaction_read_only",
    "SELECT 1;",                       # 尾分号是合法的单语句
    "  SELECT   1  ",                  # 前后空白
    "SELECT 1 -- 这是注释",
    "SELECT /* 块注释 */ 1",
])
def test_readonly_statements_pass(sql):
    dbro.check_statement(sql)          # 不抛即过


@pytest.mark.parametrize("sql", [
    # 列名/字面量里含关键字**不许误伤** —— 假红会逼人放宽白名单，最后变成真漏
    "SELECT updated_at, created_at FROM character.custom_characters",
    "SELECT * FROM audit.events WHERE event_type = 'user.delete'",
    "SELECT * FROM audit.events WHERE payload = 'INSERT INTO x VALUES (1)'",
    "SELECT deleted_flag, insert_count FROM t",
    "SELECT * FROM bff_admin.security_configs",
    "SELECT last_analyze FROM pg_stat_user_tables",
])
def test_keywords_inside_identifiers_and_literals_are_not_false_positives(sql):
    dbro.check_statement(sql)


# ── 白名单：拦截面（每条都是真绕法）────────────────────────────────────────
@pytest.mark.parametrize("sql,why", [
    ("DELETE FROM character.custom_characters", "裸 DELETE"),
    ("UPDATE t SET a=1", "裸 UPDATE"),
    ("INSERT INTO t VALUES (1)", "裸 INSERT"),
    ("DROP TABLE t", "DDL"),
    ("TRUNCATE t", "TRUNCATE"),
    ("SELECT 1; DELETE FROM t", "多语句：分号后可以是任何东西"),
    ("SELECT 1 -- \n; DROP TABLE t", "用行注释藏分号"),
    ("/* harmless */ DELETE FROM t", "用块注释伪装无害前缀"),
    ("SEL/**/ECT 1", "注释切开关键字后首关键字不合法"),
    ("SET ROLE postgres", "换角色"),
    ("COPY t FROM '/tmp/x'", "COPY 写入"),
    ("DO $$ BEGIN DELETE FROM t; END $$", "DO 块"),
    ("CALL some_proc()", "存储过程"),
    ("SELECT pg_sleep(100); UPDATE t SET a=1", "先无害后有害"),
    ("", "空串"),
    ("   ", "纯空白"),
    ("-- 只有注释", "注释掉全部内容"),
])
def test_write_shapes_are_rejected(sql, why):
    with pytest.raises(dbro.DbStatementRejected):
        dbro.check_statement(sql)


@pytest.mark.parametrize("sql", [
    # 这一族是**只看首关键字必然漏**的那个：以 WITH 开头、以 SELECT 收尾，却真的改数据
    "WITH gone AS (DELETE FROM character.custom_characters RETURNING *) SELECT * FROM gone",
    "WITH ins AS (INSERT INTO t VALUES (1) RETURNING *) SELECT count(*) FROM ins",
    "WITH upd AS (UPDATE t SET a=1 RETURNING *) SELECT * FROM upd",
])
def test_data_modifying_cte_is_rejected(sql):
    """Postgres 的 data-modifying CTE：本闸存在的主要理由。"""
    with pytest.raises(dbro.DbStatementRejected) as exc:
        dbro.check_statement(sql)
    assert "CTE" in str(exc.value) or "全句" in str(exc.value)


def test_dollar_quoted_body_is_neutralised():
    """`$$...$$` 里可以塞任何字符（含分号和引号）。归一化必须先吃掉它。

    反过来说：一条**内容无害**的 dollar-quoted 字面量不该因为里面的词被判红。
    """
    assert ";" not in dbro.normalize("SELECT $$a;b$$")
    assert "delete" not in dbro.normalize("SELECT $tag$ delete from t $tag$").lower()


def test_rejection_message_says_which_rule_fired():
    """拒绝理由要指名是哪一条判据 —— 一句「不合法」会让人去放宽关键字表。"""
    with pytest.raises(dbro.DbStatementRejected) as exc:
        dbro.check_statement("SELECT 1; DELETE FROM t")
    assert "单条语句" in str(exc.value)


# ── 通道：缺件必须分类报错，不许静默降级 ──────────────────────────────────
def test_missing_dsn_raises_channel_error_not_silent_skip(monkeypatch):
    monkeypatch.delenv(dbro.DSN_ENV, raising=False)
    assert dbro.dsn_present() is False
    with pytest.raises(dbro.DbChannelError) as exc:
        dbro.connect()
    assert dbro.DSN_ENV in str(exc.value)


def test_connect_never_returns_none_on_failure(monkeypatch):
    """失败路径只有一条：抛。**不许返回 None 让调用方自己判**——

    返回 None 的接口迟早会被写成 `if conn:` 然后静默跳过整个数据层。
    """
    monkeypatch.setenv(dbro.DSN_ENV, "postgresql://nobody@127.0.0.1:1/none")
    with pytest.raises(dbro.DbChannelError):
        dbro.connect()


# ── 纪律④：中立集的作用域边界单验 ─────────────────────────────────────────
def test_l3_data_ops_are_track_neutral_behaviourally():
    """DATA_OPS 的中立性**不靠清单自称**：每个中立 op 必须不碰 `self.page`。

    判法是行为的：用 `page=None` 造 runner，逐个调用中立 op，断言失败原因
    绝不是 `AttributeError: 'NoneType' object has no attribute ...`。
    只要有人往 DATA_OPS 里塞一个 UI op，这条就红。

    （若只验"清单里的 op 名字看起来像 db_*"，改名就能绕过；若只验
    "API 轨用例里带 db op 能编译"，那只证明闸放行了，没证明它跑得起来。）
    """
    from server.journey import pw_runtime

    ctx = {"base_url": "http://x", "variables": {"baseUrl": "http://x"},
           "run_dir": ".", "write_authorized": set(), "done_cases": set(),
           "scrub_pairs": []}
    runner = pw_runtime.CaseRunner(ctx, {"case_id": "x/R1-C001"}, page=None)
    assert runner.page is None

    samples = {
        "db_query": {"op": "db_query", "sql": "SELECT 1"},
        "expect_db_rows": {"op": "expect_db_rows", "sql": "SELECT 1", "rows": 1},
        "expect_db_value": {"op": "expect_db_value", "sql": "SELECT 1", "equals": 1},
        "db_snapshot": {"op": "db_snapshot", "metric": "m", "sql": "SELECT 1"},
        "expect_db_delta": {"op": "expect_db_delta", "metric": "m", "delta": 1},
    }
    assert set(samples) == set(track_purity.DATA_OPS), (
        "DATA_OPS 变了但本测试的样例没跟——中立集的每一个成员都必须在这里被真调用过")

    for name, action in samples.items():
        with pytest.raises(pw_runtime.CaseBlocked) as exc:
            runner.act(action)
        msg = str(exc.value)
        assert "NoneType" not in msg, f"{name} 碰了 self.page，它不是轨道中立的：{msg}"


def test_vocabulary_has_no_gap_against_runtime():
    """词表与运行时零差集。新增 op 忘了登记 → 这里红，而不是运行时静默放行。"""
    gaps = track_purity.vocabulary_gaps()
    assert gaps == {"runtime_only": [], "vocab_only": []}, gaps


def test_data_ops_do_not_make_a_case_ui_track():
    """API 轨用例里带 db op **不算越轨**，也不算混轨。"""
    case = {
        "case_id": "q/R1-C001",
        "automation": {"admissible": True, "recipe": {"track": "api", "actions": [
            {"op": "db_snapshot", "metric": "n", "sql": "SELECT count(*) FROM t"},
            {"op": "request", "method": "POST", "path": "/api/x"},
            {"op": "expect_status", "status": 200},
            {"op": "expect_db_delta", "metric": "n", "delta": 1},
        ]}},
        "assertion_layers": {"api": {"assertions": ["a"], "required_evidence": ["http_transcript"]}},
    }
    assert track_purity.check_case(case) == []


def test_data_ops_do_not_launder_a_real_ui_op():
    """中立不是豁免：真的 UI op 混进 API 轨照样判红（防"加了 db op 就放行"）。"""
    case = {
        "case_id": "q/R1-C002",
        "automation": {"admissible": True, "recipe": {"track": "api", "actions": [
            {"op": "db_query", "sql": "SELECT 1"},
            {"op": "click", "selector": "#go"},
        ]}},
    }
    problems = track_purity.check_case(case)
    assert problems and any("click" in p["problem"] for p in problems)


# ── 第三份 op 清单：已并回单一真相，别让它再长出来 ────────────────────────
def test_draft_op_vocabulary_is_derived_not_handcopied():
    """`draft_cases.ACTION_OPS` 必须等于 track_purity 的三张表之并。

    L3 落地时这里真的漏过一次：runtime 与 track_purity 都补齐了 db op，
    draft_cases 那份手抄清单没补，于是一条完全合法的守恒用例在生成阶段
    被判 E18 —— 症状是"生成侧写得出的 op 编译侧不认"。
    """
    from server.journey import draft_cases

    assert set(draft_cases.ACTION_OPS) == set(
        track_purity.UI_OPS | track_purity.API_OPS | track_purity.DATA_OPS)


def test_assert_ops_match_the_runtime_record_assert_surface():
    """断言 op 集合与运行时真正会记断言的 op 对拍。

    只靠 `expect_` 前缀约定不够：一个忘了调 `_record_assert` 的 `expect_x`
    会被算进断言计数，让红线四「无断言不算过」形同虚设 —— 一条零判决的用例
    看起来有 1 条断言。所以判据落在实现上（该 op 的方法体里有没有记断言），
    不落在名字上。
    """
    import inspect

    from server.journey import draft_cases, pw_runtime

    recording = set()
    for op in track_purity.UI_OPS | track_purity.API_OPS | track_purity.DATA_OPS:
        fn = getattr(pw_runtime.CaseRunner, "_op_" + op, None)
        assert fn is not None, f"词表里的 {op} 在运行时没有实现"
        if "_record_assert" in inspect.getsource(fn):
            recording.add(op)
    assert recording == set(draft_cases.ASSERT_OPS), (
        f"会记断言的 op={sorted(recording)}，ASSERT_OPS={sorted(draft_cases.ASSERT_OPS)}")


def test_db_snapshot_evidence_kind_is_allowed_on_api_track():
    """`db_snapshot` 是数据层证据，不是 UI-only 证据 —— API 轨要它必须放行。"""
    assert "db_snapshot" not in track_purity.UI_ONLY_EVIDENCE
    from server.journey import schema
    assert "db_snapshot" in schema.EVIDENCE_KINDS

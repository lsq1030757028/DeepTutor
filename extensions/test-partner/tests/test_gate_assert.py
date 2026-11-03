# -*- coding: utf-8 -*-
"""assert_gates 移植验收：原件 kit/tools/assert_layer.py selftest 15 检逐条过。"""
from server.journey.gates import assert_gates as ag


def test_t1_failed_retcode_blocked_not_none_eq_zero():
    r = ag.business("T1", expected=0, actual=None, retcode=10100)
    assert r["verdict"] == "BLOCKED"
    assert "retcode" in r["gates_failed"]


def test_t2_t3_equality():
    assert ag.business("T2", expected=409, actual=409)["verdict"] == "PASS"
    assert ag.business("T3", expected=409, actual=408)["verdict"] == "FAIL"


def test_t4_t5_control_gate():
    assert ag.business("T4", expected=[5], actual=[5],
                       control_group={"n": 4, "distinct": [5]})["verdict"] == "INCONCLUSIVE"
    assert ag.business("T5", expected=[5], actual=[5],
                       control_group={"n": 46, "distinct": [2, 3, 5]})["verdict"] == "PASS"


def test_t6_t7_discriminating_gate():
    assert ag.business("T6", expected=108, actual=108,
                       discriminating_samples=0)["verdict"] == "INCONCLUSIVE"
    assert ag.business("T7", expected=108, actual=108,
                       discriminating_samples=21)["verdict"] == "PASS"


def test_t8_t9_conservation_gate():
    r8 = ag.business("T8", expected=1, actual=1,
                     conservation={"aggregate": 33, "sum_of_parts": 32})
    assert r8["verdict"] == "FAIL" and r8["conservation_residual"] == 1
    assert ag.business("T9", expected=1, actual=1,
                       conservation={"aggregate": 32, "sum_of_parts": 32})["verdict"] == "PASS"


def test_t10_denominator_gate():
    assert ag.business("T10", expected=1, actual=1, denominator=0)["verdict"] == "INCONCLUSIVE"


def test_t11_probe_always_observed():
    assert ag.probe("T11", observed="x")["verdict"] == "OBSERVED"


def test_probe_tampered_to_pass_caught():
    led = ag.Ledger()
    led.add(ag.probe("P1", observed="x"))
    led.rows[0]["verdict"] = "PASS"  # 人为破坏
    assert len(led.validate()) > 0


def test_valid_ledger_and_summary():
    led = ag.Ledger()
    led.add(ag.business("B1", expected=1, actual=1))
    led.add(ag.evidence_row("E1", finding="db 对账一致"))
    assert led.validate() == []
    assert led.summary() == {"business/PASS": 1, "evidence/OBSERVED": 1}


def test_duplicate_id_caught():
    led = ag.Ledger()
    led.add(ag.business("X", expected=1, actual=1))
    led.add(ag.business("X", expected=1, actual=1))
    assert len(led.validate()) > 0


def test_business_pass_without_retcode_gate_caught():
    led = ag.Ledger()
    row = ag.business("B2", expected=1, actual=1)
    row["gates_passed"] = []  # 人为抹掉 retcode 闸痕迹
    led.add(row)
    assert any("retcode" in e for e in led.validate())

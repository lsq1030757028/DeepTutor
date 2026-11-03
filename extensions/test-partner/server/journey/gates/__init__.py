"""机械牙移植包（采石场 test_agent/，只抄语义与测试向量，不抄依赖）。

移植对照（设计稿 §6 吸收清单）：

| 本包模块 | 原件（只读） | 验收 |
|---|---|---|
| sot_gate | test_agent/loop/sot_gate.js | 原件 selftest 向量逐条过 |
| downstream_gate | test_agent/loop/downstream_gate.js | 同上 |
| cases_gate | test_agent/loop/cases_gate.js | 同上 |
| evidence_gate | .agents/agents/test-engineer/scripts/evidence_gate.py | 同上 |
| verdict_projection | test_agent/kit/tools/bundle_to_verdicts.js + loop/record.js | 同上 |
| mechanical_check | test_agent/kit/f9/mechanical_check.js（M1/M3/M5 + M4 降级） | 同上 |
| assert_gates | test_agent/kit/tools/assert_layer.py（retcode + discriminating） | 同上 |
| credential_scan | 凭据零落盘机械扫描（DoD 7，覆盖自定义高熵串） | 金标样本 |
| compile_gate | architecture-analysis §3 编译工序 6（最小版） | 金标样本 |

纪律：判官不当闸（护栏 4）——本包全部是确定性机械判据，零模型调用。
"""

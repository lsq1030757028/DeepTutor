---
name: req-journey
description: 黑盒需求旅程方法（M1 吸收）——把需求推进为经机械闸把关的原子工具链产物。用户走"接入→澄清→分析→用例→采纳→编译→执行→覆盖"完整黑盒闭环，或需要 Example Map / 下游消费面盘点 / 澄清纪律三项方法时使用。工序全部实现为 server/journey/ 原子工具，牙挂产物；本技能只讲方法与 golden，机械判据是代码不是自觉。
---

# 需求旅程方法（黑盒闭环 · req-journey）

配套 `server/journey/` 九个原子工具（ingest/clarify/analyze/draft/adopt/compile/execute/project/coverage）。
**每项方法都有一颗机械牙把关**——写不对会被牙拦，本技能给的是「怎么写才过牙」+ golden example。
牙是唯一权威，本文只解释语义。golden 的可执行版见
`agent-lab-runs/deeptutor-absorb-m1-20260810/demo/run_bysms_demo.py`（真靶跑通过的整链）。

三项蒸馏方法（护栏 2：每项带 golden + 回归锚）：

## 1. 澄清纪律（牙 = sot_gate，回归锚 = tests/test_gate_sot.py 14 例）

confirmed_facts 必须交代两栏，非做实即须在「零access降级声明」段给【谁来补 + 补不到的后果】：

- **被测构建**：被测实例身份（黑盒线=实例指纹：靶机地址+banner+二进制哈希；白盒线=部署 commit）。
- **doc×code 对齐**：黑盒线不读源码，此栏走降级声明（谁来补=转白盒时 M2 负责，后果=漏"代码做了文档没说"的分支）。

歧义答复以官方需求文档为准；文档撑不住的预期一律标 `probing=true`，**不进 PASS 判据**。

golden（run_bysms_demo.py `CONFIRMED_FACTS`）：被测构建写实例指纹并"一致?是"，doc×code 走降级声明带真后果 → 过闸。

## 2. Example Map（牙 = draft 覆盖族 E16，回归锚 = tests/test_journey_tools_design.py）

每条 R 规则 × charter × 判别 Example。规则无用例覆盖且无带「谁覆盖+后果」的不覆盖声明 → E16 拦。
Example 的 kind（happy/boundary/negative/coexist）只作组织标签，不设闸。

golden（run_bysms_demo.py `EXAMPLE_MAP`）：R1 登录三例（正确/错误/未登录），R2-R4 查询各例，R5 权限标 probing。

## 3. 下游消费面盘点（牙 = downstream_gate，回归锚 = tests/test_gate_downstream.py 28 例）

每个业务对象过通用生命周期六档：获得→使用→**同类共存(必答)**→计量结算→撤销退回→记账对账。
每档要么有判别用例（case_cell 是像 id 的引用），要么显式不覆盖（谁覆盖·后果）。缺档/没人接/整条零覆盖 → 拦。

golden（run_bysms_demo.py `ANALYSIS` 的盘点表）：会话凭证与业务列表两对象各盘六档，无对账面的档走不覆盖声明。

## 采纳与执行（牙串联，不属"方法蒸馏"但一并说明）

- 采纳：schema 全量校验（v1.0.0）+ 双 digest 盖章复算 + cases_gate → ApprovedCaseSet 冻结（禁反写）。
- 编译：compile-gate 五闸（schema+禁反写复算/py_compile/collect/case映射/凭据扫描）→ pytest+Playwright bundle。
- 执行：五红线（跨host拒发/变量残留skip/凭据零回显/无断言不算过/不跟随重定向）+ 写确认 + 凭据零落盘。
- 投影：evidence_gate → 唯一投影器（verdicts 禁手写）→ mechanical_check(M1/M3/M5+M4降级)。
- 覆盖：gap 无解释不 done。

## 本次未蒸馏条目 + 后果（护栏 3 显式声明）

- **feature 中间层工作台面**：OUT（HANDOFF §三，M2）。后果：M1 分析产物只有 Example Map + 消费面盘点，
  无中间 feature 视图；不影响闭环，M2 补。
- **异脑判官 / LLM 复核**：M1 不做（护栏 4）。后果：语义支撑度不在 M1 机械复核，靠 mechanical_check 的
  M1/M3/M5 机械项 + 人读抽样；判官作信号的接线留 M2。
- **零 access 分支**：仅保留文档语义未实现（HANDOFF §三）。后果：完全无 access 的目标无法接入，需至少 GET / 可达。
- **fixture 图砍引擎**：只留 preconditions.checkable 最小字段。后果：复杂前置编排不支持，写用例的 cleanup/前置靠声明。

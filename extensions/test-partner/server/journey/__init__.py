"""req 旅程（M1 黑盒闭环）原子工具包。

九项原子工具 + 类型化产物接口（设计权威 docs/design/m1-absorption-design.md §1，
拍板 docs/decisions/0015-m1-design-gate.md）：

    ingest    → intake_profile      接入 + 定档（checklist / standard / deep）
    clarify   → business_frame      R 规则 + confirmed_facts（sot_gate 牙）
    analyze   → test_analysis       Example Map + 消费面盘点（downstream_gate 牙）
    draft     → case_draft          用例草稿（validate_cases 覆盖族）
    adopt     → ApprovedCaseSet     采纳冻结（cases_gate + 双 digest）
    compile   → AutomationBundle    pytest+Playwright 工程（compile-gate 最小版）
    execute   → run_receipt         执行 + evidence-bundle 素材（红线五条）
    project   → verdicts.jsonl      唯一投影器（evidence_gate + bundle_to_verdicts + mechanical_check）
    coverage  → coverage_ledger     覆盖收口（gap 无解释不 done）

组合约束（凌驾各工具）：原子工具 + 类型化产物，禁向导式流水线；任意前缀是合法交付；
牙挂产物不挂流程位；批次（batch）是唯一状态对象，聊天与工作台是它的两个投影。
"""

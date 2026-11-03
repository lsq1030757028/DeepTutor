# 0014 · UI 自动化执行栈统一 Playwright，Selenium 降级

- 日期：2026-08-10
- 决策人：用户（原话：`关于AI执行测试自动化方面的selenium工程降级，优先使用playwright工具`）
- 上下文：吸收路线已定（见 0013 + `agent-lab-runs/deeptutor-test-agent-engineering-20260810/workflow-comparison-and-target-flow.md`）；automation-compile 代码工程轨需定生成目标栈。

## 裁定

1. **automation-compile 代码轨的生成目标 = pytest + Playwright**（API/DB 断言同工程内）；不新建任何 Selenium 工程。
2. 白月黑羽工序中与 Selenium 绑定的细节（浏览器优先级、密码弹窗禁用等）只取工序思想，落地翻译为 Playwright 等价物；页面快照 / site-map 机制与栈无关，照迁。
3. 存量 Selenium 资产（如有）只维持不扩建；回归价值高的用例经 automation-compile 重编译为 Playwright 版，其余自然淘汰。

## 依据

- test_agent 现有解释轨（`loop/loop_driver.js` primitives）已压在 Playwright 上；双轨共用同一浏览器栈可共享程序化登录/storageState、trace 证据格式与防假交互闸（DOM-diff）。
- Playwright 的 auto-wait / trace / API 拦截贴合证据账本语义；Selenium 无对应物，接证据链需自建等价层，纯增维护面。

## 证伪信号

代码轨落地后若出现 Playwright 覆盖不了、而 Selenium 可覆盖的目标浏览器/内核硬需求（当前未知有），重开本决策。

# 0002 · TAPD 为独立可插拔能力，面向团队推广

- 日期：2026-08-04
- 拍板人：用户（原话要点："计划后续要推广团队内部，TAPD MCP 最好是一个独立可插拔的能力"）

## 结论

1. TAPD 能力不并入、不桥接进测试 MCP 服务器；两者是并列的独立 MCP 服务器，DeepTutor 分别按 URL 挂载。
2. 载体沿用全局 `tapd-capability`（`D:\Test_Coding\.agents\tools\tapd-capability\`，核心/适配器分离 + C9 契约，天然可插拔），缺口只是 stdio→HTTP 传输面（FastMCP streamable-http 一步可达）。该改动走 TAPD 维护线对齐后实施，不由 test-partner 单方面修改 `.agents/` 共享正文。
3. 测试技能与 TAPD 解耦：HAR 链路零依赖；需求链路（`req-testing` 剧本）在 TAPD 服务器未挂载时降级为"用户粘贴需求正文"。
4. 团队推广预留：HTTP 端点须有访问令牌与绑定地址控制（凭据不出服务进程）；DeepTutor v1.5.8 自带多用户模式（auth + 按用户分配工具/MCP 策略），作为团队部署的候选形态，后续里程碑再议。

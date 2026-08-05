# 0003 · TAPD 接入改用官方 mcp-server-tapd，自研件不再承担通用接入

- 日期：2026-08-04
- 背景：用户经另一 agent 提示发现官方 TAPD MCP 存在，质疑自研重复造轮子。核实：`mcp-server-tapd`（PyPI，腾讯云 MCP 广场收录）活跃维护（2026-07 仍月更），覆盖需求/缺陷/任务/迭代，支持 stdio + streamable-http，token 鉴权。

## 结论

1. **test-partner 的 TAPD 接入改用官方 `mcp-server-tapd`**：streamable-http 起服务，DeepTutor 按 URL 挂载。0002 号决策中"给自研 tapd-capability 补 HTTP 面"的方案作废——官方现成支持，且官方跟进 TAPD API 变更，正中"独立可插拔 + 团队推广"。
2. **自研 `tapd-capability` 不是废件，但定位收窄**：其独有价值是治理层（字段基线确认/漂移检测/证据留痕/watchlist 复查），官方版没有这些。建议其维护线将它重新定位为官方接入之上的治理增值层，而非维护一个平行的全量 TAPD 客户端——此建议移交 TAPD 维护线裁决，不属本项目范围。
3. **团队推广前的安全前置**：凭据全程经该 MCP 服务器进出，PyPI 包维护者署名为个人邮箱，与"腾讯官方"的关系须在推广前核验（确认官方仓库来源或做一次源码审计），否则属于把 TAPD 凭据交给未经核验的第三方包。
   - **修订记（2026-08-04 核验完成）**：血统确认为腾讯 TAPD 团队官方（首 commit 腾讯企业邮箱 + 腾讯云广场官方标注），代码审计干净；判定"可用但需条件"，条件清单见 `docs/research/mcp-server-tapd-audit-20260804.md`（要点：版本钉死禁 uvx 拉最新、仅 token 模式、base_url 钉死、团队走每人本地 stdio、共享 HTTP 须加鉴权反代与机器人账号）。
4. blackbox 内置旧通道维持不动（冻结范围）。

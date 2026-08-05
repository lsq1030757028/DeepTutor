# UAT-4 / UAT-5 机器预验证据（2026-08-05）

主脑在常驻实例（网关 v0.7.0，3789/3790）上逐条实测，用真实 DOM 事件驱动页面，非绕过前端。
用户验收时可当抽查基线；本文件不替代用户签收。

## UAT-4 · 能力网关与配置页

| # | 验收点 | 实测证据 | 结论 |
|---|---|---|---|
| 1 | 一个入口、五 tab | `data-tab` 实测为 概览/连接与凭据/测试环境/测试伙伴/工作台 | ✅ |
| 2 | 状态真实 | 概览显示 v0.6.1→0.7.0、MCP 面 0.0.0.0:3790、DeepTutor test-partner connected(7)/tapd connected(2)、TAPD 子服务运行中:3795——与 `GET /api/v1/settings/mcp` 及 netstat 实况一致 | ✅ |
| 3 | 凭据不外露 | 页面全文搜不到已配令牌原值（既有硬断言 + 本轮页面文本复核） | ✅ |
| 4 | 环境金库 | 新建「UAT验证环境」→ 列表只显示 `token ********9f3a`；页面全文无明文；删除后 `config/environments.json` 回到空数组 | ✅ |
| 5 | 刷新聊天会话 | 拨开关后回读 `test-partner connected 7 tools`、`tapd connected 2 tools` | ✅ |
| 6 | 内嵌 DeepTutor | 面板 iframe `src=http://127.0.0.1:3789/?embed=1`，`sandbox=allow-scripts allow-same-origin allow-forms allow-popups`，网关日志有 `GET /?embed=1 200` 及 iframe 内 `/api/status`、`/api/partner/status` 调用 | ✅ |

## UAT-5 · 工作台

| # | 验收点 | 实测证据 | 结论 |
|---|---|---|---|
| 1 | 批次看得见 | 5 个批次时间倒序；0.7.0 前的 4 个标「仅可查看产物」；执行过的显示上次结果 | ✅ |
| 2 | 用例勾得动 | 表格列全（编号/标题/模块/优先级/请求块/断言）；「只选可执行」精确选中 5/6；展开 TC-005 显示 `GET {{baseUrl}}/api/v1/whoami`、`Authorization: Bearer {{token}}`、两条断言——凭据位置保持占位原样 | ✅ |
| 3 | 执行前问一句 | 出现**页面内**确认条：「将用环境「UAT验证环境」向 http://127.0.0.1:8099 发出 5 条真实请求。写操作会真改那套环境里的数据…」+ 确认执行/取消；源码无 `confirm(`/`alert(` | ✅ |
| 4 | 过程与结果 | 执行中捕获「第 1/5 条」；完成出「1 过 3 败，1 条跳过未执行」明细表（状态码/耗时）；失败展开见 `× json_path $.token_seen 期望 "Bearer 走查期望值" 实际 "Bearer {{token}}"`；跳过 TC-006 标注原因；报告路径给绝对路径 + 复制 | ✅ |
| 5 | 凭据不外露（关键） | mock 故意在响应里**回显鉴权头** `Bearer TESTSECRET-uat-9f3a`；页面显示的「实际」值是 `Bearer {{token}}`，全页搜 `TESTSECRET`/`9f3a` 均为 false；`execution_report.json`/`.md` 各 0 处命中 | ✅ |

## 本轮发现的缺陷（1 条，已修复）

- **WB-001（体验级）**：在「测试环境」tab 新建环境后，「工作台」tab 的环境下拉不刷新，仍显示「还没有配置任何测试环境」，需刷新页面才出现。环境列表在页面加载时取一次，切 tab 不重取。
  **已修复（2026-08-05，v0.7.1）**：`showTab("workbench")` 时调 `wbRefreshEnvs()` 重取 `/api/environments`，同时刷新 `state.config.environments` 与批次详情里缓存的那份。600 例测试全绿，网关已重启上线。

## 清场

临时 mock（127.0.0.1:8099）已停；临时环境「UAT验证环境」已删（`config/environments.json` 为空数组）；
执行报告写进了走查批次目录（`20260805-162854-工作台走查-本地mock`），该批次本就是走查产物，留作用户点验样本。

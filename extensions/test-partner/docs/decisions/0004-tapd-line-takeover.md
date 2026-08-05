# 0004 · TAPD 维护线并入本会话决策，三项裁决

- 日期：2026-08-04
- 背景：用户另一会话（"TAPD MCP 基础能力化"）与本项目 TAPD 决策重合，用户拍板：决策权交本线，不再双线对接。彼线现状：`tapd-capability` 已独立成仓（github.com/lsq1030757028/tapd-capability，PR #1 已合并，CI=离线测试+凭据扫描两道必需检查），`tapd-business-queries` spec 未动笔，停在人闸。

## 裁决

1. **`tapd-business-queries` spec 继续写，但按 0003 边界收窄**：只做语义/治理层——业务状态问答（"我还有哪些缺陷没验关"）、状态语义映射、字段基线/漂移/证据；**通用原始查询与 CRUD 明确不做**，spec 里写明"通用接入由官方 mcp-server-tapd 承担"。验收标准维持彼线已定的可观测口径（业务问题能答且答对）。
2. **门禁现状接受，不花钱不公开**：GitHub 免费私有仓拿不到强制分支保护（403），维持"CI 必绿"为约定 + pre-push 本地补偿（README 已如实记录）。团队推广真启动时再升级 GitHub Pro 或转组织仓，届时重议。
3. **消费方映射**：DeepTutor/test-partner 挂官方 `mcp-server-tapd`（决策 0003）；Claude 工作区流程（对账、manager、走查）用自研 `tapd-capability` 语义层。两者凭据分开配置。

## 交接备注

彼线全部状态在磁盘与 GitHub（仓库 `D:\Test_Coding\.agents\tools\tapd-capability`），无未落盘的中间态；后续该仓工序由本线按其既有流程（spec→实现→CI→PR）推进。

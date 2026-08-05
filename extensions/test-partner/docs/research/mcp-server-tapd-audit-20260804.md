# mcp-server-tapd 血统与安全审计（2026-08-04）

判定：**可用但需条件**。血统与代码干净，部署形态与供应链可验证性有真实缺口。取证产物在会话 scratchpad `tapd-audit/`（sdist 8.0.80 + cnb.cool 仓库 clone + wheel 解包）。

## 血统 — 腾讯官方（高置信度）
- 腾讯云 MCP 广场 #11474 标注 `By 腾讯云TAPD团队`，登记仓库 `cnb.cool/tapd_mcp/mcp-server-tapd`（cnb.cool 为腾讯自家托管平台）。
- 决定性证据：仓库首 commit（2025-04-09）作者 `ryanjhzheng@tencent.com`；PyPI 署名 gmail 是同一 handle 的个人注册邮箱。其余贡献者含腾讯企业邮箱与知名腾讯开源工程师。
- 残留风险：PyPI 项目无 project_urls/license/PEP 740 provenance——PyPI→仓库无密码学链接，信任根是维护者个人 PyPI 账号。

## 凭据链路 — 代码干净（全量 2905 行审计，wheel 与 sdist 一致）
- 凭据仅两处外发：TAPD API（Authorization 头，目标由 `TAPD_API_BASE_URL` 决定）与企微机器人（不带凭据）。
- 零日志系统、零 eval/exec/subprocess、零第三方域名、依赖闭包 33 包无可疑项。
- 弱点（设计缺陷非后门）：`base_url` 无域名/协议校验（配置纪律是唯一闸门）；README 示范命令行传凭据（会进 ps/history）；`load_dotenv()` 吸 CWD 的 `.env`；Basic Auth base64 可逆。

## 传输面 — 团队共享部署的最大风险
- streamable-http 默认绑定 `0.0.0.0:8000`，**完全无鉴权选项**；凭据为模块级单例，所有调用者共用一份身份（含 add_bug/update_story 等写操作），TAPD 审计无法归因到人。
- 官方 issue #7（按调用传 token）/#16（只读拆分）均 open 无回复——多租户与只读模式短期无解。

## 活跃度
- 77 个版本，最新 8.0.80（2026-07-29）；2026 年 7 次发版；有跟进 TAPD API 新特性的 commit 实据。
- issue 响应冷淡（5 open 均无维护者回复）。
- **供应链缺口**：PyPI 8.0.80 比公开仓库 HEAD（8.0.76）多一个完整功能（`program_bind_entities`，内容审计良性），公开仓库滞后于发布物，每次升版需重新 diff。

## 采用条件（要点）
A. 团队推广只走每人本地 stdio + 个人 token；共享 HTTP 服务必须非默认绑定 + 带鉴权反代 + 专用机器人账号。
B. 只用 `TAPD_ACCESS_TOKEN`（禁 API_USER/PASSWORD）；凭据走环境变量；`TAPD_API_BASE_URL` 钉死 `https://api.tapd.cn`；启动目录无杂 `.env`；BOT_URL 非必要不配。
C. 版本钉死 `==8.0.80` + 记录 sha256，禁 `uvx` 拉最新；每次升版重跑 仓库vs发布物 diff；钉 `mcp` SDK 版本。
D. 无 SLA 预期；多租户诉求等 issue #7 或每人本地绕开。

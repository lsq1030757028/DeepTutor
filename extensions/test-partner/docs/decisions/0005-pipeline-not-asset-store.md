# 0005 · 定位拍板：生成管线 + 标准格式导出，不自建资产库

- 日期：2026-08-04
- 拍板人：用户（参考 MeterSphere 的"AI 生成嵌在结构化资产库、同步用例入库可执行"模式后定）

## 结论

1. **不自建 MeterSphere 式工作台/资产库**。本项目的核心资产是"从流量/需求到标准测试资产的生成管线"（生成器+校验器+导出器），可交互性由各领域专业工具承接。
2. **资产的家**：接口用例 → Postman Collection v2.1（Apifox 原生可导入）；功能用例 → TAPD 用例库（xlsx 列名已对齐，导入即入库）；未来 UI 自动化 → 可执行 Playwright 脚本工程。
3. **M2.5（立项，与 M3 并行）**：用例 schema 加结构化 `request` 块（源自 HAR 真实样本，过脱敏哨兵）；`save_delivery` 新增 postman 导出；HAR 链路默认双产物 = xlsx（人评审）+ postman_collection.json（可执行）——修订 0004 之前"HAR 默认 Excel/CSV"的口径。
4. **M4+ 可选深化**：Apifox 开放 API / TAPD 用例接口 / MeterSphere API 直推（等价"同步用例"按钮）；届时再评估是否部署 MeterSphere 作执行平台。

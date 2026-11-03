# 0019 · 测试能力载体 = 正式 Capability 模式（案 B），三坑纳入 M2

- 日期：2026-08-11
- 决策人：用户
- 取代：0017 O2（manager 自决"用掉第 5 处触点接 Partner 人闸"）**作废**——其前提"载体不变"已被推翻
- 依据产物：`agent-lab-runs/deeptutor-absorb-m2-20260810/carrier-comparison.md`（含 evidence/carrier-comparison-anchors.md）

## 用户的提问与被纠正的心智模型（原样保留）

> 我想知道，我们设计中的正式模式，是怎么实现的，是正常聊天时富卡片不会出现，只有当识别到测试的情况下才出现需求旅程这些元素吗

**回答（当版实证）**：一半成立。普通聊天不出富卡成立（富卡 extractor 第一关即 `msg.capability` 命中字面量，纯 Chat 模式该字段为空串，全部早退）；
但触发方式**不是自动识别**——DT 无意图路由，capability 由用户在输入框的 **capability picker** 显式选中（`web/components/chat/home/ChatComposer.tsx:95` 注释即 "One row in the capability picker"）。
所以"正式模式"= 在该选择器里新增一项「测试」，与研究/出题/可视化并列。

- [推断未实证] 未找到"发送后重置 capability"的代码，故选中后应跨轮保持，多轮旅程无需每条重选。施工须实证。

## 裁定一 · 载体 = 案 B（正式「测试」Capability 模式）

用户选中「直接做 B：一步到位正式模式」，放弃 A→B 的分期路径。

- **Partner（伙伴）方案作废**：五项判据全输。根因是上游**故意**关闭人闸——`deeptutor/services/partners/runtime.py:396-397` 显式不注入 `wait_for_user_reply` 并注释说明 IM 语义，且被 `test_partner_runtime.py` 锁死；主聊侧在 `deeptutor/services/session/turn_runtime.py:1620` 注入。manager 已独立核验该不对称。
- **M1 资产不受影响**：九原子工具当前未绑定任何载体（`@mcp.tool()` 计数 7，无一是 journey 工具），作废面仅伙伴人格与配置，约后端 4.3%。
- 代价：5~7 处上游触点、工期 4~6 天；**首次把第三类触点引进登记表**，manager 已知情接受。
  - **[2026-08-11 修正]** 本行原写"均为'清单加一行'的 merge 友好型"，与同句"第三类触点"自相矛盾，且与实测不符。
    实测（m2-design 复核）：登记行计数吻合，但**性质不符**——`ChatMessages.tsx` 的 3 处是**改渲染逻辑**不是加一行。
    以"第三类被知情接受"为准，登记表原因栏须如实写"改渲染逻辑"，不得美化为"加一行"。
    该错误由 manager 起草时造成；不重开载体决策（B 的价值不依赖该措辞）。
- 与 0015 卡 0 的关系：人闸继续复用 `ask_user`（不变）；**新增**富结果卡组件（0015 当时定的"零新组件"随载体升级而放宽，属演进非推翻）。

## 裁定二 · 三个坑全部纳入 M2

用户选中「都排进 M2」：

1. **Partner 人闸静默降级**（DT 主体隐患）：partner 仍挂载 `ask_user`、技能仍教模型调用，无 waiter 时问题被拍平成最终回复并结束本轮，用户无感知。→ 立缺陷 + M2 修（载体转 B 后 partner 侧应移除该误导性挂载或补明确降级提示）。
2. **批次产物未按用户隔离**：`journey/artifacts.py` 产物根是模块级常量无 root 覆盖 → 决策 0009「各看各的」在批次路径不成立。**归因：M1 引入的新缺口，非 0009 原有欠账。** 修复须带机械闸（artifacts.py 不得有无 root 参数的路径构造函数，与 `gateway/workbench.py` 签名对拍）。
3. **重放会重跑副作用工具**：两条路径——「重新生成」按钮（`ChatMessages.tsx:1381-1386`，B 免疫）与「编辑消息→分支重跑」（`canEdit :906-907` 不读 capability，**B 同样中招**）。`turn_runtime.py` 的 `_regenerate` 全部出现点均不阻断工具派发。
   → 幂等化为**载体无关必做项**（`idempotency_key` + `replayed` + 写确认不因重生成复用 + `run_receipt` 标 `triggered_by`），约 1~2 人日，0 触点。
   **两条重放路径各须一条测试**，不得因选 B 而豁免。

## 对既有产物的连带影响（须同步修订）

- **交互稿** `docs/design/m2-workbench-interaction.html`：按 A 形态画的"聊天侧只用 GFM 表格"须改——B 下富结果卡成立；须补 capability picker 里「测试」项的入口呈现、模式徽章、以及富卡与工作台页的分工边界。
- **M2 工程设计** `m2-engineering-design.md` §5.1 的设计取值「M2 聊天侧只用 GFM 表格 markdown，不做富卡组件」**作废**，按 B 重出；ADR-M2-03 随之改写。

## 证伪信号

- 若「测试」模式注册后发现 capability 不跨轮保持、每条消息都要重选，则 B 的入口体验劣于预期，需补模式粘滞机制或重议 A。
- 若 5~7 处触点在首次上游同步时即产生冲突，说明"merge 友好"判断错误，需重估 B 的长期成本。

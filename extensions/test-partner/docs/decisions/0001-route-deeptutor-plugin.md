# 0001 · 路线拍板：放弃 blackbox 结构改造，转 DeepTutor 插件二开

> ⚠ **本条第 1 项的「不改内核」红线已于 2026-08-05 被决策 0007 推翻**（路线改为真二开 / fork）。
> 其余各项（blackbox 冻结、轻量治理、伙伴门面、TAPD 接入）继续有效。

- 日期：2026-08-04
- 拍板人：用户
- 背景：blackbox 0803 用户上手四阻断（BB-407~410，复盘锚 `blackbox-test-agent/docs/feedback/2026-08-03-user-handson-four-blockers.md`）暴露结构性缺陷——治理与执行焊死、开环控制（闸只拒收不回流）、编排器预塞上下文、段内单轮无修复回路。与 DeepTutor（HKUDS，Apache 2.0）编排对比分析后确认：自研 agentic 内核（路线 B，2-4 周）≈ 重造 DeepTutor 已有基础设施。

## 拍板结论

1. **主路线**：DeepTutor 插件二开直接立项，不做 POC。测试技能以 Level 1 Tools + Level 2 Capabilities 插件承载，不改内核。
2. **治理取舍**：轻量版——每次运行留产物 + 收据文件（输入指纹/模型/校验结果/产物路径）；哈希链、装箱关、判官词表不迁移。质量策略改为确定性校验前置 + 一次定向修复（DeepTutor visualize 模式）。
3. **产品形态**：「伙伴（Partner）」作门面（SOUL.md 人格 + 知识库 + 工具策略 + 可绑钉钉渠道）；「我的智能体」接 Claude Code 作过渡执行臂；人闸语义映射到 `ask_user` 暂停/恢复。
4. **blackbox 处置**：冻结结构改造，只修 BB-407/408 接线，转为资产库（HAR 体检、提示词资产、校验规则）与对照系统。
5. **流程**：不走完整产品逼问；唯一逼问切片 = 用例产出格式规格。三闸、bug-bank 闭环照旧。
6. **TAPD 接入**（2026-08-04 补充）：工作区全局 TAPD 已 MCP 化且在优化中；新项目优先挂全局 TAPD MCP（DeepTutor 内置 MCP 客户端），加薄适配层隔离变动；blackbox 内置旧通道不迁移。

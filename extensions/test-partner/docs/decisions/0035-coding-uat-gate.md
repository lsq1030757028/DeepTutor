# 0035 · CODING 固定 SHA 覆盖完整 UAT 前置闸

- 日期：2026-08-13
- 状态：已定稿（用户要求：`继续推进，我需要UAT`）
- 修订：0034 第 3 条

## 背景

仅运行 test-partner pytest 不能证明 DeepTutor 主体、前端生产构建和首屏体积适合进入 UAT。P3 本地集成复核已经实际发现 root-shell 超预算，因此 CODING 绿灯必须覆盖这些消费面。

## 决定

1. 每次必过构建仍绑定一个 GitHub 分支和一个 40 位固定 SHA，并验证该 SHA 属于该分支。
2. 同一次构建依次运行 test-partner 全量回归、Linux root 回归、Web Node 测试、i18n 一致性、生产构建和路由体积预算。
3. GitHub Actions 继续仅允许手动触发；CODING 流水线继续禁止部署。
4. UAT 环境部署仍需用户另批，构建绿灯不得写成“已发布”或“UAT 已通过”。

## 证伪

CODING 绿灯缺少任一上述闸；构建没有绑定精确 SHA；流水线产生部署副作用；或把自动化结果冒充用户验收。

# 0034 · 必过 CI 改走 CODING，GitHub Actions 只留手动

- 日期：2026-08-13
- 状态：已定稿（工作区计划批准）
- 上位：0008 发布规范；工作区 `docs/decisions/2026-08-13-unified-release-coding-ci.md`

## 为什么改 0008 六

0008 把「不绿不合」绑在 GitHub Actions 上。私有仓额度用尽后闸整条失效（2026-08-07 已发生）。用户不付费、不公开仓。

## 决定

1. 合入 `main` 的必过证据 = CODING 对固定 GitHub SHA 的构建，不是 Actions。
2. `tests.yml` / `test-partner.yml` 只保留 `workflow_dispatch`。`docker-release.yml` / `pypi-release.yml` 仍仅在 GitHub Release 时触发。
3. 本机合入前跑 `python -m pytest -q extensions/test-partner`。CODING 默认只跑扩展层 pytest（0008 四层里的第 1 层），降低云上时间。
4. 部署默认关。发 3785 须用户另批。
5. Deploy Key 一仓一把，只读，不复用萌伴 Web。

## 证伪

新推 PR 仍自动出现 Actions run；或回执把「Actions 没跑」写成通过/失败。

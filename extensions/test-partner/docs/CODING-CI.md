# DeepTutor 必过构建（CODING）

GitHub 只记账。合入与发布的必过证据在 CODING，不在 Actions。

## 本地（每个 PR 先跑）

```bash
python -m pytest -q extensions/test-partner
```

## CODING（U2 配好钥匙之后）

原生 PUSH 触发（默认日常路径）：

- `CCI_TRIGGER_METHOD=PUSH`
- `GIT_LOCAL_BRANCH=<branch>`：转换为 `refs/heads/<branch>`，tag/MR/危险 ref 拒绝
- `GIT_COMMIT=<40 位小写 SHA>`：作为唯一 checkout 与校验 SHA

手动/API 回退参数：

- `GITHUB_REF`：`refs/heads/<branch>`
- `GITHUB_COMMIT`：40 位小写 SHA

默认只跑 `extensions/test-partner` pytest，**不部署**。
未知触发方式失败关闭；原生 PUSH 不启用自动合并，也不存在部署 stage。

## 回执

质量看这次 CODING 构建。Actions 没跑写「未使用」。没有用户「发」不得写已发布。

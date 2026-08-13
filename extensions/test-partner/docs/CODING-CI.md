# DeepTutor 必过构建（CODING）

GitHub 只记账。合入与发布的必过证据在 CODING，不在 Actions。

## 本地（每个 PR 先跑）

```bash
python -m pytest -q extensions/test-partner
```

## CODING（U2 配好钥匙之后）

参数：

- `GITHUB_REF`：`refs/heads/<branch>`
- `GITHUB_COMMIT`：40 位小写 SHA

默认只跑 `extensions/test-partner` pytest，**不部署**。

## 回执

质量看这次 CODING 构建。Actions 没跑写「未使用」。没有用户「发」不得写已发布。

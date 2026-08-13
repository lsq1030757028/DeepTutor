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

同一个固定 SHA 必须依次通过：

- GitHub 来源、分支归属与精确 SHA 校验；
- `extensions/test-partner` 全量 pytest；
- Linux 下的 DeepTutor root pytest；
- Web Node 测试、i18n 一致性、生产构建与路由体积预算。

流水线只构建和验收，**不部署**。

## 依赖锁审查

只增加 Node 版本约束时，`package-lock.json` 只能同步根包的 `engines`；不得顺带升级 Next、Sharp 或其他依赖。若确需升级依赖，必须拆成独立变更并单独验收。

## 回执

质量看这次 CODING 固定 SHA 构建，并保留两份 Python JUnit。Actions 没跑写「未使用」。没有用户「发」不得写已发布。

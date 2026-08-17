# DeepTutor 必过构建（CODING）

GitHub 只记账。合入与发布的必过证据在 CODING，不在 Actions。

## 本地（每个 PR 先跑）

```bash
python -m pytest -q extensions/test-partner
```

## CODING（U2 配好钥匙之后）

Job 侧必须提供仓库专用环境变量 `DEEPTUTOR_GITHUB_SSH_CREDENTIALS_ID`，值是
DeepTutor 自己的只读 GitHub SSH 凭据 ID。它不是构建参数，也不在 Jenkinsfile 里给默认值；
只允许 3-128 位字母、数字、点、下划线和连字符。空值、占位词或非法格式都会在 checkout
前失败，流水线不会打印该值，checkout 也只消费校验后的局部变量。

`github.com` 的 strict host-key 信任仍是 CODING agent / job 的外部配置前置，目前 pending。
Jenkinsfile 不写 `StrictHostKeyChecking=no`、不自动接受未知 host key，也不把这项外部前置伪装成
源码已关闭。

原生 PUSH 触发（默认日常路径）：

- `CCI_TRIGGER_METHOD=PUSH`
- `GIT_LOCAL_BRANCH=<branch>`：转换为 `refs/heads/<branch>`，tag/MR/危险 ref 拒绝
- `GIT_COMMIT=<40 位小写 SHA>`：作为唯一 checkout 与校验 SHA

手动/API 回退参数：

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

未知触发方式失败关闭；原生 PUSH 不启用自动合并，也不存在部署 stage。

## 回执

质量看这次 CODING 固定 SHA 构建，并保留两份 Python JUnit。Actions 没跑写「未使用」。没有用户「发」不得写已发布。

"""网关配置中心：非敏感配置 `config/gateway.json` + 敏感凭据 `config/secrets.env`
+ 测试环境与变量金库 `config/environments.json`。

分文件不是为了好看，是为了让"能给人看的"和"绝不能给人看的"物理隔开：

- `gateway.json`：子服务开关、端口、DeepTutor 地址、放行工具名。**可以整份回显给页面**。
- `secrets.env`：TAPD 令牌一类的凭据。**任何时候不整值回显、不进日志**，
  页面只拿得到 `configured: true/false` 与掩码（见 `public_state()`）。
- `environments.json`：被测环境（名字 + base_url + 备注 + 变量键值对）。变量的值是
  `{{token}}` / `{{session}}` 这类真凭据，**与 secrets.env 同等对待**：对外只出
  名字、base_url、备注、变量**键名与掩码**，值一个字符都不出去
  （见 `environments_public()`）。名字与 base_url 本身不敏感，但和值同住一个文件——
  拆成两个文件会让"改一个环境要原子改两处"，那才是真正会出错的地方。

三个文件都被仓库根 `.gitignore` 拦住；入库的只有 `config/secrets.env.example`。

## 测试环境这一层是为了什么

执行用例要 `{{token}}` 的真值。在有这一层之前，唯一的入口是 `execute_cases` 的
`variables` 参数——那意味着用户得在聊天框里打出真实凭据：进模型上下文、进聊天历史库、
进 LLM 供应商。我们在 `server/execute.py` 里严防凭据出流，却让它从前门进来。
所以环境与变量存在本机、执行时只按**环境名**引用，解析发生在网关进程内，不经模型。

## 权限的实话

0.6 的权限意识止步于"文件在你自己机器上、不进版本库"。Windows 上这就是一个普通
文件，同机器上任何以你身份运行的程序都读得到——`os.chmod` 在 Windows 上只能拨只读位，
拨不出 POSIX 的 0600。要更强的隔离得上 DPAPI / 凭据管理器，那是后续的事，
现在把这个局限写在 README 里，不假装做到了。

## 为什么读写要原子

保存动作发生在用户点"保存并生效"的那一刻，紧接着就是重启子服务。若写到一半进程被
掐（或者用户手贱关了窗口），非原子写会留下一个半截文件，下次启动读出来是
"令牌少了几个字符"——表现成"令牌突然失效了"，而这是最难查的一类故障。
所以一律写临时文件 + `os.replace`（同卷内原子替换，Windows 上也是原子的）。
"""

from __future__ import annotations

import copy
import json
import os
import re
import tempfile
import threading
from typing import Any
from urllib.parse import urlsplit

#: 仓库根（本文件在 <root>/server/gateway/config.py）
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: 配置目录。测试与多实例可用环境变量改到别处，默认 <repo>/config。
DEFAULT_CONFIG_DIR = os.environ.get("TEST_PARTNER_CONFIG_DIR") or os.path.join(
    REPO_ROOT, "config")

GATEWAY_FILE = "gateway.json"
SECRETS_FILE = "secrets.env"
ENVIRONMENTS_FILE = "environments.json"

#: 环境数量与单环境变量数的上限。不是性能考虑，是防手滑（页面上一次粘一大坨 JSON）。
MAX_ENVIRONMENTS = 50
MAX_VARIABLES_PER_ENV = 100
MAX_NAME_LEN = 60
MAX_NOTE_LEN = 200
MAX_VAR_KEY_LEN = 64
MAX_VAR_VALUE_LEN = 8192

#: 变量键名：非空、不含空白与花括号（`{{name}}` 里再套花括号必然渲染不出来）。
_VAR_KEY_RE = re.compile(r"^[^\s{}]+$")

#: 允许落进 secrets.env 的键名白名单。不在名单里的键写入即拒——
#: 免得页面上一个手滑把任意环境变量塞进子进程的注入面。
ALLOWED_SECRETS = (
    "TAPD_ACCESS_TOKEN",   # TAPD 个人访问令牌
    "BOT_URL",             # 可选：企业微信机器人 webhook（不带 TAPD 凭据）
    "DEEPTUTOR_TOKEN",     # 可选：DeepTutor 开了登录时的 admin 令牌
)

#: gateway.json 的默认值。读的时候按键补齐（用户的文件缺字段不算坏），
#: 写的时候只写全量，避免"改了一个键把别的键写没了"。
DEFAULT_SETTINGS: dict[str, Any] = {
    "version": 1,
    "services": {
        "tapd": {
            # 端口 3795：3790 被 test-partner 自己的 MCP 面占了。
            "port": 3795,
            # 容器视角的地址。宿主机上服务绑 0.0.0.0，容器里必须走
            # host.docker.internal，填 127.0.0.1 会打到容器自己身上。
            "mcp_url": "http://host.docker.internal:3795/mcp",
            # 只放行两个只读工具。这个包 43 个工具里 17 个是写操作，而凭据是
            # 模块级单例，写操作在 TAPD 审计里全归到同一个令牌名下、追不到人。
            # 出处：docs/research/mcp-server-tapd-audit-20260804.md。
            "enabled_tools": ["get_stories_or_tasks", "get_stories_fields_info"],
            "tool_timeout": 30,
        },
    },
    "deeptutor": {
        # 容器把 3782 映射到宿主机，网关在宿主机上跑，所以是 127.0.0.1。
        "api_base": "http://127.0.0.1:3782",
        # 聊天侧会话刷新要拨的那个条目名（网关自己在 DeepTutor 里的登记名）。
        "self_entry": "test-partner",
    },
}


def _deep_merge(base: dict[str, Any], override: Any) -> dict[str, Any]:
    """按键递归补齐：override 里有的用 override 的，没有的用 base 的。"""
    merged = dict(base)
    if not isinstance(override, dict):
        return merged
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def mask_secret(value: str) -> str:
    """把凭据压成掩码。短值全星号，长值露末 4 位方便用户认出"是不是我刚贴那个"。

    露末 4 位是掩码的通行做法（银行卡尾号同理）：4 个字符不足以重建令牌，
    但足够让用户分辨自己换没换。不想露的话把阈值调到无穷大即可，
    页面上的"已配置/未配置"本身不依赖掩码。
    """
    text = (value or "").strip()
    if not text:
        return ""
    if len(text) < 12:
        return "*" * len(text)
    return "*" * 8 + text[-4:]


class EnvironmentConfigError(ValueError):
    """测试环境配置的入参错误。`code` 给页面做分支，`args[0]` 是给人看的中文。

    不叫 `EnvironmentError`：那个名字在 Python 3 里是 `OSError` 的别名，
    覆盖它会让 `except EnvironmentError` 的老代码悄悄改变语义。
    """

    def __init__(self, message: str, code: str = "ENV_INVALID") -> None:
        super().__init__(message)
        self.code = code


def _clean_text(value: Any, limit: int) -> str:
    return str(value if value is not None else "").strip()[:limit]


def valid_base_url(base_url: str) -> bool:
    """base_url 必须是 http(s) 绝对地址。与 `server/execute.normalize_base_url`
    同口径——那边是执行前的最后一道，这边是存盘前的第一道，两道都要过。"""
    from server.journey.redlines import safe_target_url
    return bool(safe_target_url(base_url)["ok"])


def normalize_variables(raw: Any) -> dict[str, str]:
    """变量表规整成 `{键: 值}`。字典与 `[{key, value}]` 数组都认（页面发的是后者）。

    键去空白、值**逐字保留**（前后空白剥掉：粘贴令牌时最常见的是尾随换行）。
    """
    pairs: list[tuple[Any, Any]] = []
    if isinstance(raw, dict):
        pairs = list(raw.items())
    elif isinstance(raw, (list, tuple)):
        for row in raw:
            if isinstance(row, dict):
                key = row.get("key", row.get("name", row.get("k")))
                pairs.append((key, row.get("value", row.get("v", ""))))
            elif isinstance(row, (list, tuple)) and len(row) == 2:
                pairs.append((row[0], row[1]))
    elif raw not in (None, ""):
        raise EnvironmentConfigError(
            "变量表要么是键值对象，要么是 [{key, value}] 数组。", code="ENV_VARS_SHAPE")

    result: dict[str, str] = {}
    for key, value in pairs:
        name = str(key if key is not None else "").strip()
        if not name:
            continue                      # 空行（页面上加了一行没填）直接丢，不报错
        if len(name) > MAX_VAR_KEY_LEN or not _VAR_KEY_RE.match(name):
            raise EnvironmentConfigError(
                f"变量名「{name[:MAX_VAR_KEY_LEN]}」不合法：不能带空格或花括号，"
                f"长度不超过 {MAX_VAR_KEY_LEN}。用例里写 {{{{变量名}}}} 引用它。",
                code="ENV_VAR_KEY_INVALID")
        if name in result:
            raise EnvironmentConfigError(
                f"变量名「{name}」重复了，同一个环境里每个变量名只能有一行。",
                code="ENV_VAR_DUPLICATE")
        text = str(value if value is not None else "").strip()
        if len(text) > MAX_VAR_VALUE_LEN:
            raise EnvironmentConfigError(
                f"变量「{name}」的值超过 {MAX_VAR_VALUE_LEN} 字符，不像是凭据。",
                code="ENV_VAR_VALUE_TOO_LONG")
        result[name] = text
    if len(result) > MAX_VARIABLES_PER_ENV:
        raise EnvironmentConfigError(
            f"一个环境最多 {MAX_VARIABLES_PER_ENV} 个变量。", code="ENV_TOO_MANY_VARS")
    return result


def normalize_environment(raw: Any) -> dict[str, Any]:
    """一条环境记录规整成 `{name, base_url, note, variables}`。非法即抛。"""
    if not isinstance(raw, dict):
        raise EnvironmentConfigError("环境记录必须是对象。", code="ENV_SHAPE")
    name = _clean_text(raw.get("name"), MAX_NAME_LEN)
    if not name:
        raise EnvironmentConfigError(
            "环境名必填——执行时就是按这个名字引用环境的。", code="ENV_NAME_REQUIRED")
    base_url = _clean_text(raw.get("base_url"), 500).rstrip("/")
    if not base_url:
        raise EnvironmentConfigError(
            f"环境「{name}」缺 base_url：不给被测环境地址，执行时无从拼请求。",
            code="ENV_BASE_URL_REQUIRED")
    if not valid_base_url(base_url):
        raise EnvironmentConfigError(
            "base_url 不是安全的 http(s) 绝对地址；不能内嵌用户名、密码、"
            "query 或 fragment，敏感值请放环境变量。",
            code="ENV_BASE_URL_INVALID")
    from server.journey.redlines import safe_target_url
    base_url = safe_target_url(base_url)["url"]
    return {
        "name": name,
        "base_url": base_url,
        "note": _clean_text(raw.get("note"), MAX_NOTE_LEN),
        "variables": normalize_variables(raw.get("variables")),
    }


def _same_name(left: str, right: str) -> bool:
    """环境名比较：去空白、忽略大小写。用户记不住自己当初有没有大写。"""
    return str(left or "").strip().casefold() == str(right or "").strip().casefold()


class GatewayConfig:
    """一个配置目录的读写门面。线程安全（网关的 Web 面在独立线程里跑）。"""

    def __init__(self, config_dir: str | None = None) -> None:
        self.config_dir = os.path.abspath(config_dir or DEFAULT_CONFIG_DIR)
        self._lock = threading.RLock()

    # ── 路径 ────────────────────────────────────────────────────────────────

    @property
    def settings_path(self) -> str:
        return os.path.join(self.config_dir, GATEWAY_FILE)

    @property
    def secrets_path(self) -> str:
        return os.path.join(self.config_dir, SECRETS_FILE)

    @property
    def environments_path(self) -> str:
        return os.path.join(self.config_dir, ENVIRONMENTS_FILE)

    def _ensure_dir(self) -> None:
        os.makedirs(self.config_dir, exist_ok=True)

    # ── 原子写 ──────────────────────────────────────────────────────────────

    def _atomic_write(self, path: str, text: str) -> None:
        """临时文件 + fsync + `os.replace`。半截文件永远不会以正式名字存在。"""
        self._ensure_dir()
        directory = os.path.dirname(path)
        handle_fd, tmp_path = tempfile.mkstemp(
            prefix=os.path.basename(path) + ".", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(handle_fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, path)
        except BaseException:
            # 失败时把临时文件收干净，别在 config/ 里堆垃圾。
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    # ── gateway.json ────────────────────────────────────────────────────────

    def load_settings(self) -> dict[str, Any]:
        """读非敏感配置。文件不存在或坏损都回默认值——配置页要能开起来。"""
        with self._lock:
            try:
                with open(self.settings_path, "r", encoding="utf-8") as handle:
                    raw = json.load(handle)
            except (OSError, json.JSONDecodeError):
                return _deep_merge(DEFAULT_SETTINGS, {})
            return _deep_merge(DEFAULT_SETTINGS, raw)

    def save_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        """写非敏感配置（补齐默认值后整份写）。返回落盘后的完整配置。"""
        with self._lock:
            merged = _deep_merge(DEFAULT_SETTINGS, settings)
            self._atomic_write(
                self.settings_path,
                json.dumps(merged, ensure_ascii=False, indent=2) + "\n")
            return merged

    def service_settings(self, name: str) -> dict[str, Any]:
        """取某个子服务的配置段（缺失回该服务的默认段，再缺回空字典）。"""
        services = self.load_settings().get("services") or {}
        default = (DEFAULT_SETTINGS["services"] or {}).get(name) or {}
        return _deep_merge(default, services.get(name) or {})

    # ── secrets.env ─────────────────────────────────────────────────────────

    @staticmethod
    def _parse_secrets(text: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            # 只按第一个 '=' 切：令牌里带 '=' 是合法的（base64 尾巴）。
            key, _, value = stripped.partition("=")
            key = key.strip()
            if key:
                result[key] = value.strip()
        return result

    def read_secrets(self) -> dict[str, str]:
        """读全部凭据。**调用方负责别把返回值整个塞进日志或响应。**"""
        with self._lock:
            try:
                with open(self.secrets_path, "r", encoding="utf-8") as handle:
                    text = handle.read()
            except OSError:
                return {}
            return self._parse_secrets(text)

    def get_secret(self, name: str) -> str:
        return self.read_secrets().get(name, "")

    def set_secret(self, name: str, value: str) -> None:
        """写一条凭据（其余条目原样保留）。空值等同删除。"""
        self.write_secrets({name: value})

    def delete_secret(self, name: str) -> None:
        self.write_secrets({name: ""})

    def write_secrets(self, updates: dict[str, str]) -> None:
        """批量更新凭据：白名单外的键拒写，空值删键，其余键逐字保留。"""
        for name in updates:
            if name not in ALLOWED_SECRETS:
                raise ValueError(
                    f"{name} 不在凭据白名单里，拒绝写入 secrets.env。"
                    f"允许的键：{', '.join(ALLOWED_SECRETS)}")
        with self._lock:
            current = self.read_secrets()
            for name, value in updates.items():
                cleaned = (value or "").strip()
                if cleaned:
                    current[name] = cleaned
                else:
                    current.pop(name, None)
            lines = [
                "# 由 test-partner 配置页写入。真实凭据，永不进版本库。",
                "# 这些值只在本机被读取：注入 TAPD 子进程的环境变量，或用于调 DeepTutor 本地 API。",
                "# 想换令牌就回 http://localhost:3789 页面上改，不要手工编辑本文件。",
            ]
            for name in ALLOWED_SECRETS:
                if name in current:
                    lines.append(f"{name}={current[name]}")
            # 白名单之外的历史遗留键（比如手工加过的）原样留着，不悄悄抹掉。
            for name, value in current.items():
                if name not in ALLOWED_SECRETS:
                    lines.append(f"{name}={value}")
            self._atomic_write(self.secrets_path, "\n".join(lines) + "\n")

    # ── environments.json（测试环境与变量金库） ────────────────────────────
    #
    # 这一段有两组读接口，别用错：
    #   `load_environments` / `get_environment` **带变量原值**，只给网关进程内的
    #       解析路径用（`execute_cases(env=...)`）；
    #   `environments_public` / `environment_index` 是**去值投影**，给页面、给 MCP
    #       工具、给任何会流向模型或浏览器的地方用。
    # 加新出口时先问一句：这个出口的输出会不会离开本进程？会就只能用投影。

    def load_environments(self) -> list[dict[str, Any]]:
        """读全部环境（**含变量原值**）。文件缺失或坏损回空列表——配置页要能开。

        **调用方负责别把返回值整个塞进日志、响应或模型上下文。**
        """
        with self._lock:
            try:
                with open(self.environments_path, "r", encoding="utf-8") as handle:
                    raw = json.load(handle)
            except (OSError, json.JSONDecodeError):
                return []
        rows = raw.get("environments") if isinstance(raw, dict) else raw
        if not isinstance(rows, (list, tuple)):
            return []
        out: list[dict[str, Any]] = []
        for row in rows:
            try:
                out.append(normalize_environment(row))
            except EnvironmentConfigError:
                continue          # 手工编坏的一条不该让其余环境全部消失
        return out

    def save_environments(self, environments: Any) -> list[dict[str, Any]]:
        """整份写环境（校验 → 查重 → 原子写）。返回落盘后的列表（含值）。"""
        rows = [normalize_environment(row) for row in (environments or [])]
        if len(rows) > MAX_ENVIRONMENTS:
            raise EnvironmentConfigError(
                f"最多配置 {MAX_ENVIRONMENTS} 个环境。", code="ENV_TOO_MANY")
        seen: list[str] = []
        for row in rows:
            if any(_same_name(row["name"], other) for other in seen):
                raise EnvironmentConfigError(
                    f"环境名「{row['name']}」重复了。执行时按名字引用环境，"
                    "重名会引用到哪个说不准，所以不允许。", code="ENV_NAME_DUPLICATE")
            seen.append(row["name"])
        payload = {"version": 1, "environments": rows}
        with self._lock:
            self._atomic_write(
                self.environments_path,
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        return rows

    def get_environment(self, name: str) -> dict[str, Any] | None:
        """按名字取一个环境（**含变量原值**，大小写不敏感）。没有返回 None。"""
        for row in self.load_environments():
            if _same_name(row["name"], name):
                return copy.deepcopy(row)
        return None

    def environment_names(self) -> list[str]:
        """已配环境的名字清单（只有名字，别的什么都没有）。"""
        return [row["name"] for row in self.load_environments()]

    def upsert_environment(self, environment: Any,
                           original_name: str = "") -> dict[str, Any]:
        """新增或更新一个环境。`original_name` 非空表示改名式编辑（按旧名定位）。

        返回落盘后的那一条（**含值**，调用方自己决定给谁看）。
        """
        row = normalize_environment(environment)
        locator = str(original_name or "").strip() or row["name"]
        with self._lock:
            rows = self.load_environments()
            index = next((i for i, item in enumerate(rows)
                          if _same_name(item["name"], locator)), -1)
            if index >= 0:
                rows[index] = row
            else:
                rows.append(row)
            self.save_environments(rows)
        return row

    def delete_environment(self, name: str) -> bool:
        """删一个环境。删掉了返回 True，本来就没有返回 False。"""
        with self._lock:
            rows = self.load_environments()
            kept = [row for row in rows if not _same_name(row["name"], name)]
            if len(kept) == len(rows):
                return False
            self.save_environments(kept)
            return True

    def environment_index(self, include_variable_keys: bool = False) -> list[dict[str, Any]]:
        """**去值**索引：`[{name, base_url, variable_count}]`。

        这是给 MCP 工具 `list_environments` 用的形状——它的返回值会进模型上下文，
        所以连变量键名默认都不给（`include_variable_keys=True` 时才带，页面用）。
        """
        out: list[dict[str, Any]] = []
        for row in self.load_environments():
            item: dict[str, Any] = {
                "name": row["name"],
                "base_url": row["base_url"],
                "variable_count": len(row["variables"]),
            }
            if include_variable_keys:
                item["variable_keys"] = sorted(row["variables"])
            out.append(item)
        return out

    def environments_public(self) -> list[dict[str, Any]]:
        """**去值**投影，给配置页：变量只出键名、掩码与长度，值永不出现。"""
        out: list[dict[str, Any]] = []
        for row in self.load_environments():
            out.append({
                "name": row["name"],
                "base_url": row["base_url"],
                "note": row["note"],
                "variable_count": len(row["variables"]),
                "variables": [
                    {"key": key,
                     "masked": mask_secret(row["variables"][key]),
                     "length": len(row["variables"][key])}
                    for key in sorted(row["variables"])
                ],
            })
        return out

    # ── 给页面看的安全投影 ──────────────────────────────────────────────────

    def public_state(self) -> dict[str, Any]:
        """页面能拿到的全部配置视图。**这里出去的东西不含任何凭据原值。**

        凭据只以 `{configured, masked, length}` 三元组出现：`masked` 至多露末 4 位，
        `length` 用来让用户确认"我贴的是完整的那串"。
        测试环境的变量走 `environments_public()`，同样只出键名与掩码。
        """
        secrets = self.read_secrets()
        return {
            "settings": self.load_settings(),
            "environments": self.environments_public(),
            "secrets": {
                name: {
                    "configured": bool(secrets.get(name)),
                    "masked": mask_secret(secrets.get(name, "")),
                    "length": len(secrets.get(name, "")),
                }
                for name in ALLOWED_SECRETS
            },
            "paths": {
                "config_dir": self.config_dir,
                "settings_file": self.settings_path,
                "secrets_file": self.secrets_path,
                "environments_file": self.environments_path,
            },
        }


#: 进程级默认实例（网关自己用这个；测试各自造带临时目录的实例）
_default: GatewayConfig | None = None
_default_lock = threading.Lock()


def default_config() -> GatewayConfig:
    global _default
    with _default_lock:
        if _default is None:
            _default = GatewayConfig()
        return _default

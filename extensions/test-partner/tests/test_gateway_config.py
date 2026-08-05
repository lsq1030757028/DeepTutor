"""网关配置中心的离线测试：原子读写、白名单、掩码、以及「凭据不出投影」的硬断言。

这一层是凭据的第一道闸：只要 `public_state()` 泄了，后面页面怎么写都白搭。
所以本文件里有若干条看起来重复的「TOKEN 不在返回值里」断言——它们不是重复，
是分别钉住不同的出口。
"""

from __future__ import annotations

import json
import os

import pytest

from server.gateway import config as cfgmod
from server.gateway.config import (EnvironmentConfigError, GatewayConfig,
                                   mask_secret, normalize_variables)

TOKEN = "tapd-pat-9f8e7d6c5b4a39281706abcdef012345"
ENV_TOKEN = "eyJhbGciOiJIUzI1NiJ9.env-secret-value-987654321"
ENV_SESSION = "SESSIONID=8f2c1d4e6a7b9c0d1e2f3a4b5c6d7e8f"


def env_row(name="测试环境", base_url="https://api-test.example.com",
            variables=None, note=""):
    return {"name": name, "base_url": base_url, "note": note,
            "variables": {"token": ENV_TOKEN} if variables is None else variables}


@pytest.fixture()
def cfg(tmp_path):
    return GatewayConfig(str(tmp_path / "config"))


# ── gateway.json ────────────────────────────────────────────────────────────

def test_settings_defaults_when_file_missing(cfg):
    settings = cfg.load_settings()
    assert settings["services"]["tapd"]["port"] == 3795
    assert settings["services"]["tapd"]["enabled_tools"] == [
        "get_stories_or_tasks", "get_stories_fields_info"]
    assert settings["deeptutor"]["api_base"] == "http://127.0.0.1:3782"
    # 只读不建文件：光看一眼配置不该在磁盘上留东西
    assert not os.path.exists(cfg.settings_path)


def test_settings_roundtrip_and_merge(cfg):
    cfg.save_settings({"services": {"tapd": {"port": 4795}}})
    settings = cfg.load_settings()
    assert settings["services"]["tapd"]["port"] == 4795
    # 没提到的键按默认补齐，不会因为只改一个键把别的键写没
    assert settings["services"]["tapd"]["enabled_tools"]
    assert settings["deeptutor"]["self_entry"] == "test-partner"


def test_settings_broken_json_falls_back_to_defaults(cfg):
    os.makedirs(cfg.config_dir, exist_ok=True)
    with open(cfg.settings_path, "w", encoding="utf-8") as handle:
        handle.write("{ 这不是 JSON")
    # 配置文件坏了配置页也得能开——否则用户连修的入口都没有
    assert cfg.load_settings()["services"]["tapd"]["port"] == 3795


def test_service_settings_merges_service_defaults(cfg):
    cfg.save_settings({"services": {"tapd": {"port": 4000}}})
    tapd = cfg.service_settings("tapd")
    assert tapd["port"] == 4000
    assert tapd["tool_timeout"] == 30
    assert cfg.service_settings("不存在的服务") == {}


# ── 原子写 ──────────────────────────────────────────────────────────────────

def test_write_is_atomic_no_temp_left_behind(cfg):
    cfg.save_settings({})
    cfg.write_secrets({"TAPD_ACCESS_TOKEN": TOKEN})
    leftovers = [n for n in os.listdir(cfg.config_dir) if n.endswith(".tmp")]
    assert leftovers == []


def test_failed_write_keeps_previous_file_intact(cfg, monkeypatch):
    cfg.save_settings({"services": {"tapd": {"port": 4321}}})
    original = open(cfg.settings_path, encoding="utf-8").read()

    def boom(src, dst):
        raise OSError("模拟替换失败")

    monkeypatch.setattr(cfgmod.os, "replace", boom)
    with pytest.raises(OSError):
        cfg.save_settings({"services": {"tapd": {"port": 9999}}})

    # 旧文件逐字节不变，临时文件也收干净了——半截配置永远不会以正式名字存在
    assert open(cfg.settings_path, encoding="utf-8").read() == original
    assert [n for n in os.listdir(cfg.config_dir) if n.endswith(".tmp")] == []
    assert json.loads(original)["services"]["tapd"]["port"] == 4321


# ── secrets.env ─────────────────────────────────────────────────────────────

def test_secret_roundtrip(cfg):
    cfg.set_secret("TAPD_ACCESS_TOKEN", TOKEN)
    assert cfg.get_secret("TAPD_ACCESS_TOKEN") == TOKEN


def test_secret_value_containing_equals_is_preserved(cfg):
    weird = "abc==def=ghi"
    cfg.set_secret("TAPD_ACCESS_TOKEN", weird)
    assert cfg.get_secret("TAPD_ACCESS_TOKEN") == weird


def test_secret_is_trimmed(cfg):
    cfg.set_secret("TAPD_ACCESS_TOKEN", f"  {TOKEN}\t ")
    assert cfg.get_secret("TAPD_ACCESS_TOKEN") == TOKEN


def test_empty_value_deletes_the_key(cfg):
    cfg.set_secret("TAPD_ACCESS_TOKEN", TOKEN)
    cfg.set_secret("TAPD_ACCESS_TOKEN", "   ")
    assert cfg.get_secret("TAPD_ACCESS_TOKEN") == ""
    assert "TAPD_ACCESS_TOKEN" not in cfg.read_secrets()


def test_other_secrets_survive_a_single_key_update(cfg):
    cfg.write_secrets({"TAPD_ACCESS_TOKEN": TOKEN, "BOT_URL": "https://qyapi/x"})
    cfg.set_secret("TAPD_ACCESS_TOKEN", "new-token-value-1234567890")
    assert cfg.get_secret("BOT_URL") == "https://qyapi/x"


def test_unknown_key_is_rejected(cfg):
    with pytest.raises(ValueError, match="白名单"):
        cfg.write_secrets({"AWS_SECRET_ACCESS_KEY": "x"})
    assert not os.path.exists(cfg.secrets_path)


def test_manual_extra_keys_are_not_silently_dropped(cfg):
    os.makedirs(cfg.config_dir, exist_ok=True)
    with open(cfg.secrets_path, "w", encoding="utf-8") as handle:
        handle.write("# 手工加的\nLEGACY_THING=keep-me\n")
    cfg.set_secret("TAPD_ACCESS_TOKEN", TOKEN)
    assert cfg.get_secret("LEGACY_THING") == "keep-me"


def test_comments_and_blank_lines_are_ignored(cfg):
    os.makedirs(cfg.config_dir, exist_ok=True)
    with open(cfg.secrets_path, "w", encoding="utf-8") as handle:
        handle.write("# 注释\n\n  \n# TAPD_ACCESS_TOKEN=被注释掉的\nBOT_URL=u\n")
    assert cfg.read_secrets() == {"BOT_URL": "u"}


def test_settings_file_never_holds_secrets(cfg):
    cfg.set_secret("TAPD_ACCESS_TOKEN", TOKEN)
    cfg.save_settings({"services": {"tapd": {"port": 3795}}})
    assert TOKEN not in open(cfg.settings_path, encoding="utf-8").read()


# ── 掩码与投影 ──────────────────────────────────────────────────────────────

def test_mask_hides_everything_for_short_values():
    assert mask_secret("") == ""
    assert mask_secret("abc") == "***"
    assert mask_secret("12345678901") == "*" * 11


def test_mask_reveals_at_most_last_four():
    masked = mask_secret(TOKEN)
    assert masked.endswith(TOKEN[-4:])
    assert TOKEN[:-4] not in masked
    # 露出的原文不超过 4 个字符
    assert sum(1 for ch in masked if ch != "*") == 4


def test_public_state_never_contains_the_token(cfg):
    cfg.set_secret("TAPD_ACCESS_TOKEN", TOKEN)
    blob = json.dumps(cfg.public_state(), ensure_ascii=False)
    assert TOKEN not in blob
    # 连 8 个字符的片段都不该出现（掩码只露末 4 位）
    assert TOKEN[:8] not in blob
    assert TOKEN[8:20] not in blob


def test_public_state_reports_configured_and_length(cfg):
    state = cfg.public_state()
    assert state["secrets"]["TAPD_ACCESS_TOKEN"]["configured"] is False
    cfg.set_secret("TAPD_ACCESS_TOKEN", TOKEN)
    state = cfg.public_state()
    entry = state["secrets"]["TAPD_ACCESS_TOKEN"]
    assert entry["configured"] is True
    assert entry["length"] == len(TOKEN)
    assert entry["masked"].startswith("*")


def test_public_state_lists_every_allowed_secret(cfg):
    keys = set(cfg.public_state()["secrets"])
    assert keys == set(cfgmod.ALLOWED_SECRETS)


# ── 测试环境：增删改查 ──────────────────────────────────────────────────────

def test_no_environments_by_default(cfg):
    assert cfg.load_environments() == []
    assert cfg.environment_names() == []
    assert cfg.environments_public() == []
    # 只读不建文件
    assert not os.path.exists(cfg.environments_path)


def test_environment_roundtrip_keeps_values_on_disk_only(cfg):
    cfg.upsert_environment(env_row())
    stored = cfg.get_environment("测试环境")
    assert stored["base_url"] == "https://api-test.example.com"
    assert stored["variables"]["token"] == ENV_TOKEN


def test_environment_lookup_is_case_insensitive_and_trimmed(cfg):
    cfg.upsert_environment(env_row(name="Staging"))
    assert cfg.get_environment("staging") is not None
    assert cfg.get_environment("  STAGING ") is not None
    assert cfg.get_environment("别的环境") is None


def test_upsert_updates_in_place_not_appends(cfg):
    cfg.upsert_environment(env_row())
    cfg.upsert_environment(env_row(base_url="https://api-test2.example.com",
                                   variables={"token": "new-token-value-0001"}))
    rows = cfg.load_environments()
    assert len(rows) == 1
    assert rows[0]["base_url"] == "https://api-test2.example.com"
    assert rows[0]["variables"]["token"] == "new-token-value-0001"


def test_upsert_with_original_name_renames(cfg):
    cfg.upsert_environment(env_row(name="旧名"))
    cfg.upsert_environment(env_row(name="新名"), original_name="旧名")
    assert cfg.environment_names() == ["新名"]
    assert cfg.get_environment("旧名") is None


def test_multiple_environments_coexist(cfg):
    cfg.upsert_environment(env_row(name="测试环境"))
    cfg.upsert_environment(env_row(name="预发环境",
                                   base_url="https://api-pre.example.com",
                                   variables={"session": ENV_SESSION}))
    assert cfg.environment_names() == ["测试环境", "预发环境"]
    assert cfg.get_environment("预发环境")["variables"]["session"] == ENV_SESSION
    # 改一个不该动另一个
    cfg.upsert_environment(env_row(name="测试环境", variables={"token": "x" * 20}))
    assert cfg.get_environment("预发环境")["variables"]["session"] == ENV_SESSION


def test_delete_environment(cfg):
    cfg.upsert_environment(env_row())
    assert cfg.delete_environment("测试环境") is True
    assert cfg.environment_names() == []
    assert cfg.delete_environment("测试环境") is False


def test_duplicate_name_is_rejected(cfg):
    with pytest.raises(EnvironmentConfigError) as caught:
        cfg.save_environments([env_row(name="测试环境"), env_row(name="测试环境 ")])
    assert caught.value.code == "ENV_NAME_DUPLICATE"


def test_broken_environments_file_does_not_kill_the_page(cfg):
    os.makedirs(cfg.config_dir, exist_ok=True)
    with open(cfg.environments_path, "w", encoding="utf-8") as handle:
        handle.write("{ 这不是 JSON")
    assert cfg.load_environments() == []
    assert cfg.public_state()["environments"] == []


def test_one_broken_row_does_not_hide_the_others(cfg):
    os.makedirs(cfg.config_dir, exist_ok=True)
    with open(cfg.environments_path, "w", encoding="utf-8") as handle:
        json.dump({"version": 1, "environments": [
            {"name": "坏的", "base_url": "不是地址"},
            env_row(name="好的"),
        ]}, handle, ensure_ascii=False)
    assert cfg.environment_names() == ["好的"]


# ── 测试环境：入参校验 ──────────────────────────────────────────────────────

@pytest.mark.parametrize("payload,code", [
    ({"name": "", "base_url": "https://a.example.com"}, "ENV_NAME_REQUIRED"),
    ({"name": "x", "base_url": ""}, "ENV_BASE_URL_REQUIRED"),
    ({"name": "x", "base_url": "api.example.com"}, "ENV_BASE_URL_INVALID"),
    ({"name": "x", "base_url": "ftp://api.example.com"}, "ENV_BASE_URL_INVALID"),
])
def test_invalid_environment_is_rejected(cfg, payload, code):
    with pytest.raises(EnvironmentConfigError) as caught:
        cfg.upsert_environment(payload)
    assert caught.value.code == code
    assert not os.path.exists(cfg.environments_path)


def test_base_url_trailing_slash_is_stripped(cfg):
    cfg.upsert_environment(env_row(base_url="https://api-test.example.com/"))
    assert cfg.get_environment("测试环境")["base_url"] == "https://api-test.example.com"


@pytest.mark.parametrize("key", ["带 空格", "{{token}}", "a{b"])
def test_invalid_variable_key_is_rejected(key):
    with pytest.raises(EnvironmentConfigError) as caught:
        normalize_variables({key: "v"})
    assert caught.value.code == "ENV_VAR_KEY_INVALID"


def test_variables_accept_key_value_array_from_the_page():
    assert normalize_variables([{"key": "token", "value": "v1"},
                                {"key": "session", "value": "v2"}]) == {
        "token": "v1", "session": "v2"}


def test_blank_variable_rows_are_dropped_not_errors():
    assert normalize_variables([{"key": "  ", "value": "x"},
                                {"key": "token", "value": " v "}]) == {"token": "v"}


def test_duplicate_variable_key_is_rejected():
    with pytest.raises(EnvironmentConfigError) as caught:
        normalize_variables([{"key": "token", "value": "a"},
                             {"key": "token", "value": "b"}])
    assert caught.value.code == "ENV_VAR_DUPLICATE"


def test_environment_write_is_atomic(cfg):
    cfg.upsert_environment(env_row())
    assert [n for n in os.listdir(cfg.config_dir) if n.endswith(".tmp")] == []


def test_failed_environment_write_keeps_previous_file_intact(cfg, monkeypatch):
    cfg.upsert_environment(env_row())
    original = open(cfg.environments_path, encoding="utf-8").read()

    def boom(src, dst):
        raise OSError("模拟替换失败")

    monkeypatch.setattr(cfgmod.os, "replace", boom)
    with pytest.raises(OSError):
        cfg.upsert_environment(env_row(name="第二个", variables={"a": "b" * 20}))

    assert open(cfg.environments_path, encoding="utf-8").read() == original
    assert [n for n in os.listdir(cfg.config_dir) if n.endswith(".tmp")] == []


# ── 测试环境：值不出投影（硬断言） ──────────────────────────────────────────

def test_environments_public_never_contains_a_variable_value(cfg):
    cfg.upsert_environment(env_row(variables={"token": ENV_TOKEN,
                                              "session": ENV_SESSION}))
    blob = json.dumps(cfg.environments_public(), ensure_ascii=False)
    for value in (ENV_TOKEN, ENV_SESSION):
        assert value not in blob
        assert value[:12] not in blob


def test_public_state_never_contains_a_variable_value(cfg):
    cfg.upsert_environment(env_row(variables={"token": ENV_TOKEN}))
    blob = json.dumps(cfg.public_state(), ensure_ascii=False)
    assert ENV_TOKEN not in blob
    assert ENV_TOKEN[:12] not in blob
    # 但键名、环境名、地址是要给页面看的
    assert "token" in blob and "测试环境" in blob
    assert "https://api-test.example.com" in blob


def test_environments_public_shows_keys_masks_and_lengths(cfg):
    cfg.upsert_environment(env_row(note="日常测试环境",
                                   variables={"token": ENV_TOKEN}))
    row = cfg.environments_public()[0]
    assert row["name"] == "测试环境"
    assert row["note"] == "日常测试环境"
    assert row["variable_count"] == 1
    assert row["variables"][0]["key"] == "token"
    assert row["variables"][0]["masked"].endswith(ENV_TOKEN[-4:])
    assert row["variables"][0]["length"] == len(ENV_TOKEN)


def test_environment_index_hides_even_the_variable_keys(cfg):
    """`list_environments` 工具的投影：它的返回值会进模型上下文。"""
    cfg.upsert_environment(env_row(variables={"token": ENV_TOKEN}))
    rows = cfg.environment_index()
    assert rows == [{"name": "测试环境",
                     "base_url": "https://api-test.example.com",
                     "variable_count": 1}]
    blob = json.dumps(rows, ensure_ascii=False)
    assert ENV_TOKEN not in blob and "token" not in blob
    # 页面那一路要键名，显式要才给
    assert cfg.environment_index(include_variable_keys=True)[0][
        "variable_keys"] == ["token"]


def test_settings_and_secrets_files_never_hold_environment_values(cfg):
    cfg.upsert_environment(env_row(variables={"token": ENV_TOKEN}))
    cfg.set_secret("TAPD_ACCESS_TOKEN", TOKEN)
    cfg.save_settings({})
    assert ENV_TOKEN not in open(cfg.settings_path, encoding="utf-8").read()
    assert ENV_TOKEN not in open(cfg.secrets_path, encoding="utf-8").read()

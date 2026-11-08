"""首装那两道闸（`WP-SOURCE-FIRST-RUN-IMPORT`）：没配齐的不进每一轮，配齐了才进。

用户 11-05：「用户没有配置微信。就不采集。相关的采集腿也不要反复报错了。」
从前 `collecting_sources()` 只问生效开关，首装机器上没配过的源每轮被打一遍、每轮一条
`build_failed`、屏上再画一行红。这里钉的是：开关点着但必填没填 ⇒ 不在这一轮里；
填齐 ⇒ 回到这一轮；开关关着 ⇒ 填齐了也不在（开关仍然是第一道闸）。
"""

from __future__ import annotations

import pytest

from plobi.collectors import registry, sources_config


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("PLOBI_HOME", str(tmp_path))
    registry.ensure_discovered()


def _configure_email() -> None:
    # 密钥先落：`set_setup_params` 的"还缺哪格"检查只豁免**已经设过**的密钥格。
    sources_config.set_secret("email_qq", "password", "an-app-token")
    sources_config.set_setup_params("email_qq", {"address": "me@example.test"})


def test_enabled_but_unconfigured_is_not_in_the_round() -> None:
    sources_config.set_enabled("email_qq", True)

    assert sources_config.setup_complete("email_qq") is False
    assert "email_qq" not in registry.collecting_sources(), "没配齐就不该被打，也就不会每轮报一条错"


def test_configuring_it_puts_it_back_into_the_round() -> None:
    sources_config.set_enabled("email_qq", True)
    _configure_email()

    assert sources_config.setup_complete("email_qq") is True
    assert "email_qq" in registry.collecting_sources()


def test_the_switch_is_still_the_first_gate() -> None:
    _configure_email()
    sources_config.set_enabled("email_qq", False)

    assert "email_qq" not in registry.collecting_sources(), "配齐了但关着，照样不打"


def test_a_source_with_no_setup_fields_still_waits_for_its_own_step() -> None:
    # 微信**没有可填的格子**（密钥走它自己那条读口），但它的人肉那一步是"装 ChatLog、
    # 登录微信、导出密钥"——数据密钥不在，就等于这一步没做。
    # 11-08 用户现场：全新装机的微信明明什么都没配，却每轮被打、每轮报一条错，
    # 屏上还画成一颗开关而不是加号。
    sources_config.set_enabled("wechat", True)

    assert sources_config.setup_complete("wechat") is True, "它没有必填格，这一格按定义恒真"
    assert sources_config.setup_ready("wechat") is False, "但那一步没做，就是没配好"
    assert "wechat" not in registry.collecting_sources(), "没配好就不该被打，也就不会每轮报一条错"


def test_a_source_with_no_setup_fields_joins_the_round_once_its_own_step_is_done(monkeypatch) -> None:
    # 数据密钥到手 = 微信那一步做完了（那枚密钥是产品自己取的，裁定 203 口径 2）。
    monkeypatch.setenv("CHATLOG_DATA_KEY", "k" * 64)
    sources_config.set_enabled("wechat", True)

    assert sources_config.setup_ready("wechat") is True
    assert "wechat" in registry.collecting_sources()

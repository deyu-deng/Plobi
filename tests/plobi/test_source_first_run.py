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


def test_a_source_that_needs_no_setup_is_not_blocked_by_the_new_gate() -> None:
    # wechat 的必填格是空的（密钥走它自己那条读口），新闸不该把这种源挡在外面。
    sources_config.set_enabled("wechat", True)

    assert sources_config.setup_complete("wechat") is True
    assert "wechat" in registry.collecting_sources()

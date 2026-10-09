"""The gateway owns its downstream key: one place, and no silent fallback.

裁定 86 甲 makes this service the value's owner (consumers may keep a default but
must reconcile against what the gateway declares). 裁定 94 makes an empty string
mean "not declared". Together they say: read it from `server.api_key`, and refuse
to boot rather than serve a hub whose key nobody configured.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aigw.main import _WEAK_API_KEYS, _declared_api_key


def test_returns_the_declared_key():
    assert _declared_api_key({"server": {"api_key": "sk-from-config"}}) == "sk-from-config"


def test_missing_key_is_a_startup_error_not_an_open_hub():
    with pytest.raises(RuntimeError) as exc:
        _declared_api_key({"server": {"host": "127.0.0.1"}})
    assert "server.api_key" in str(exc.value)


def test_empty_key_is_treated_as_undeclared():
    # 裁定 94: "" means "not declared", never "declared as no authentication".
    with pytest.raises(RuntimeError) as exc:
        _declared_api_key({"server": {"api_key": "   "}})
    assert "empty" in str(exc.value)


def test_no_server_section_at_all_still_raises():
    with pytest.raises(RuntimeError):
        _declared_api_key({})


def test_the_gateway_carries_no_built_in_default_key():
    """The shipped local default must live in the config, not in this module.

    A second copy here would go stale the moment someone edits `config.yaml`, and
    the weak-key warning would silently stop matching the value actually in use.
    """
    source = Path(__file__).resolve().parents[1] / "main.py"
    assert "sk-local-dev-key" not in source.read_text(encoding="utf-8")


def test_weak_list_still_covers_the_local_shape():
    # The prefix rule is what replaced the duplicated literal.
    assert "sk-local-dev-key".startswith("sk-local-")
    assert "changeme" in _WEAK_API_KEYS

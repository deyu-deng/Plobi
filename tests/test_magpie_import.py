"""The one-click import must copy, never move, and never point us at a stranger's magpie.

These tests keep the three promises in ``plobi_cli/magpie_import.py``: sources are
read-only, writes land only under ``$PLOBI_HOME/magpie``, and an import that brings
no usable allowance says so instead of reporting success.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from plobi_cli import magpie_import


def _source(root: Path, *, logins: dict | None = None) -> Path:
    """A plausible other-magpie data directory on this machine."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "logins.json").write_text(json.dumps(logins or {"anthropic": {"agent": "claude"}}), encoding="utf-8")
    (root / "plugins.json").write_text("[]", encoding="utf-8")
    # Runtime state of that installation: must not travel.
    (root / "usage.jsonl").write_text('{"tokens":1}\n', encoding="utf-8")
    (root / "settings.lock").write_text("", encoding="utf-8")
    plugins = root / "plugins"
    plugins.mkdir()
    (plugins / "index.js").write_text("module.exports = 1\n", encoding="utf-8")
    (plugins / ".cache").mkdir()
    (plugins / ".cache" / "junk.bin").write_bytes(b"junk")
    return root


@pytest.fixture()
def homes(tmp_path, monkeypatch):
    """``$PLOBI_HOME`` for us, a fake user home for the other copy."""
    plobi_home = tmp_path / "plobi"
    user_home = tmp_path / "user"
    user_home.mkdir()
    monkeypatch.setenv("PLOBI_HOME", str(plobi_home))
    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.setenv("USERPROFILE", str(user_home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    return plobi_home, user_home


def test_scan_finds_another_copies_data_directory(homes):
    _plobi_home, user_home = homes
    source = _source(user_home / ".config" / "magpie")

    result = magpie_import.scan()

    assert result["target"].endswith("magpie")
    assert [row["path"] for row in result["sources"]] == [str(source)]
    assert result["sources"][0]["importable"] is True
    # Nobody's runtime history is offered, which is what makes this an import and not
    # a takeover of the other installation.
    names = {row["name"] for row in result["sources"][0]["files"]}
    assert "logins.json" in names
    assert "usage.jsonl" not in names
    assert "settings.lock" not in names


def test_scan_offers_nothing_when_the_machine_has_nothing(homes):
    assert magpie_import.scan()["sources"] == []


def test_apply_writes_only_under_our_own_data_directory(homes):
    plobi_home, user_home = homes
    source = _source(user_home / ".config" / "magpie")

    result = magpie_import.apply()

    target = plobi_home / "magpie"
    assert result["ok"] is True
    assert result["from"] == str(source)
    assert (target / "logins.json").is_file()
    assert (target / "plugins" / "index.js").is_file()
    # The source keeps everything it had: an import that empties the other tool is a
    # sign-out, and this must never be one.
    assert (source / "logins.json").is_file()
    assert (source / "plugins" / "index.js").is_file()
    # Runtime state stays behind on both sides.
    assert not (target / "usage.jsonl").exists()
    assert not (target / "settings.lock").exists()
    assert not (target / "plugins" / ".cache").exists()


def test_apply_keeps_the_copy_it_replaces(homes):
    plobi_home, user_home = homes
    _source(user_home / ".config" / "magpie", logins={"anthropic": {"agent": "claude"}})
    ours = plobi_home / "magpie"
    ours.mkdir(parents=True)
    (ours / "logins.json").write_text(json.dumps({"openai": {"agent": "codex"}}), encoding="utf-8")

    magpie_import.apply()

    kept = ours / "logins.json.pre-import"
    assert json.loads(kept.read_text(encoding="utf-8")) == {"openai": {"agent": "codex"}}
    assert json.loads((ours / "logins.json").read_text(encoding="utf-8")) == {"anthropic": {"agent": "claude"}}


def test_apply_refuses_a_source_it_did_not_offer(homes):
    _plobi_home, user_home = homes
    _source(user_home / ".config" / "magpie")
    elsewhere = user_home / "Downloads" / "magpie"

    result = magpie_import.apply(str(elsewhere))

    assert result["ok"] is False
    assert result["error"] == "unknown-source"


def test_apply_is_idempotent_so_the_second_press_is_harmless(homes):
    plobi_home, user_home = homes
    _source(user_home / ".config" / "magpie")

    first = magpie_import.apply()
    second = magpie_import.apply()

    assert first["ok"] is True
    assert second["ok"] is True
    # Same bytes the second time: no ``.pre-import`` pile-up behind the user's back.
    assert not (plobi_home / "magpie" / "logins.json.pre-import").exists()


def test_a_copy_without_an_account_is_not_a_subscription(homes, monkeypatch):
    _source(homes[1] / ".config" / "magpie")
    magpie_import.apply()

    monkeypatch.setattr(magpie_import, "accounts", lambda force=False: [])
    assert magpie_import.has_usable_subscription() is False

    monkeypatch.setattr(
        magpie_import,
        "accounts",
        lambda force=False: [{"agent": "claude", "on": True, "user": "someone@example.com"}],
    )
    assert magpie_import.has_usable_subscription() is True


def test_a_switched_off_account_is_not_an_allowance(homes, monkeypatch):
    monkeypatch.setattr(
        magpie_import,
        "accounts",
        lambda force=False: [{"agent": "claude", "on": False, "user": "someone@example.com"}],
    )

    assert magpie_import.has_usable_subscription() is False

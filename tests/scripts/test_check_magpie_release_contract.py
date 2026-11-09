"""The magpie watcher's contract, offline.

Three things have to hold, or the pin rots and the release ships a binary nobody
checked:

1. the pin is one readable file with a version and a sha256 (a hash we can
   verify at install time);
2. "is upstream newer" is a **numeric** comparison — upstream ships `v0.1.NNNN`
   many times a day, and string comparison calls `v0.1.999` newer than `v0.1.1000`;
3. the pin is never bumped to a release whose asset carries no hash: an
   unverified binary is exactly what the pin exists to prevent.

No network here: the upstream read is the one seam we replace.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "plobi" / "check_magpie_release.py"
PIN = ROOT / "third_party" / "magpie.json"


def _module():
    spec = importlib.util.spec_from_file_location("check_magpie_release", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_magpie_release"] = mod
    spec.loader.exec_module(mod)
    return mod


def _release(version: str = "v0.1.3404", sha: str = "a" * 64) -> dict:
    return {
        "tag_name": version,
        "published_at": "2026-11-10T02:00:00Z",
        "assets": [
            {
                "name": "magpie-cli-windows-amd64.exe",
                "digest": f"sha256:{sha}",
                "browser_download_url": (
                    f"https://github.com/yetone/magpie/releases/download/{version}/magpie-cli-windows-amd64.exe"
                ),
            }
        ],
    }


def test_the_pin_is_one_file_with_a_version_and_a_hash():
    data = json.loads(PIN.read_text(encoding="utf-8"))
    for key in ("repo", "version", "asset", "sha256", "url"):
        assert data.get(key), f"the pin is missing {key}"
    assert len(str(data["sha256"])) == 64
    assert all(c in "0123456789abcdef" for c in str(data["sha256"]))
    assert str(data["version"]).startswith("v")


def test_newer_is_numeric_not_string_order():
    mod = _module()
    assert mod.is_newer("v0.1.1000", "v0.1.999") is True
    assert mod.is_newer("v0.1.999", "v0.1.1000") is False
    assert mod.is_newer("v0.1.3403", "v0.1.3403") is False
    assert mod.is_newer("v0.2.0", "v0.1.3403") is True


def test_a_bump_takes_the_version_and_the_hash_upstream_published():
    mod = _module()
    pin = mod.read_pin(PIN)
    out = mod.bump(pin, _release("v0.1.3404", "b" * 64))
    assert out["version"] == "v0.1.3404"
    assert out["sha256"] == "b" * 64
    assert "v0.1.3404" in out["url"]


def test_a_release_without_a_hash_is_never_pinned():
    mod = _module()
    pin = mod.read_pin(PIN)
    broken = _release("v0.1.3404", sha="")
    with pytest.raises(ValueError):
        mod.bump(pin, broken)


def test_the_pin_survives_a_round_trip():
    """Reading and writing the pin must not lose a field."""
    mod = _module()
    pin = mod.read_pin(PIN)
    out = mod.bump(pin, _release())
    for key in pin:
        assert key in out, f"the bump dropped {key}"

"""The magpie pin is the only thing standing between "we ship a binary" and
"we ship whatever the network gave us".

Three things have to hold, or the pin rots and the release ships a binary nobody
checked:

1. the pin is one readable file with a version and, for **every platform we
   build**, an asset name and a sha256 (a hash we can verify at build time);
2. "is upstream newer" is a **numeric** comparison — upstream ships `v0.1.NNNN`
   many times a day, and string comparison calls `v0.1.999` newer than `v0.1.1000`;
3. the pin is never bumped to a release whose asset carries no hash: an
   unverified binary is exactly what the pin exists to prevent. One unverifiable
   platform refuses the whole bump, so we never ship a half-verified set.

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


def _pin() -> dict:
    return json.loads(PIN.read_text(encoding="utf-8"))


def _release(version: str = "v0.1.3404", sha: str = "a" * 64, *, drop: str | None = None) -> dict:
    """Upstream's answer, carrying every asset the pin names.

    `drop` blanks one platform's digest — the "release without a hash" case.
    """
    assets = []
    for platform, entry in _pin()["assets"].items():
        name = entry["asset"]
        digest = "" if drop == platform else f"sha256:{sha}"
        assets.append(
            {
                "name": name,
                "digest": digest,
                "browser_download_url": f"https://github.com/yetone/magpie/releases/download/{version}/{name}",
            }
        )
    return {"tag_name": version, "published_at": "2026-11-10T02:00:00Z", "assets": assets}


def test_the_pin_is_one_file_with_a_version_and_a_hash_per_platform():
    data = _pin()
    for key in ("repo", "version", "assets"):
        assert data.get(key), f"the pin is missing {key}"
    assert str(data["version"]).startswith("v")
    # Every platform we build has to be coverable, or that build ships unverified.
    for platform in ("windows-x64", "darwin-arm64", "linux-x64"):
        assert platform in data["assets"], f"the pin does not cover {platform}"
    for platform, entry in data["assets"].items():
        assert entry.get("asset"), f"{platform} names no asset"
        sha = str(entry.get("sha256") or "")
        assert len(sha) == 64, f"{platform} has no usable sha256"
        assert all(c in "0123456789abcdef" for c in sha), f"{platform}'s sha256 is not hex"
        assert str(entry.get("url") or "").startswith("https://"), f"{platform} has no url"


def test_the_assets_are_distinct_per_platform():
    """One asset name per platform: a Mac build must not fetch the Windows PE."""
    names = [entry["asset"] for entry in _pin()["assets"].values()]
    assert len(names) == len(set(names))
    assert not [n for n in names if n.endswith(".exe") and "windows" not in n]


def test_newer_is_numeric_not_string_order():
    mod = _module()
    assert mod.is_newer("v0.1.1000", "v0.1.999") is True
    assert mod.is_newer("v0.1.999", "v0.1.1000") is False
    assert mod.is_newer("v0.1.3403", "v0.1.3403") is False
    assert mod.is_newer("v0.2.0", "v0.1.3403") is True


def test_a_bump_takes_the_version_and_every_platform_hash_upstream_published():
    mod = _module()
    pin = mod.read_pin(PIN)
    out = mod.bump(pin, _release("v0.1.3404", "b" * 64))
    assert out["version"] == "v0.1.3404"
    assert set(out["assets"]) == set(pin["assets"])
    for platform, entry in out["assets"].items():
        assert entry["sha256"] == "b" * 64, f"{platform} kept the old hash"
        assert "v0.1.3404" in entry["url"], f"{platform} still points at the old release"


def test_a_release_without_a_hash_is_never_pinned():
    mod = _module()
    pin = mod.read_pin(PIN)
    broken = _release("v0.1.3404", sha="")
    with pytest.raises(ValueError):
        mod.bump(pin, broken)


def test_one_unverifiable_platform_refuses_the_whole_bump():
    mod = _module()
    pin = mod.read_pin(PIN)
    with pytest.raises(ValueError):
        mod.bump(pin, _release("v0.1.3404", "b" * 64, drop="darwin-arm64"))


def test_the_pin_survives_a_round_trip():
    """Reading and writing the pin must not lose a field."""
    mod = _module()
    pin = mod.read_pin(PIN)
    out = mod.bump(pin, _release())
    for key in pin:
        assert key in out, f"the bump dropped {key}"
    for platform in pin["assets"]:
        assert set(out["assets"][platform]) == {"asset", "sha256", "url"}

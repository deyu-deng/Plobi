#!/usr/bin/env python3
"""盯住 magpie 的发布：有新版就把仓里那份钉子升上去。

为什么要有这个：magpie 一天发好几版（v0.1.3403 是 2026-11-09 19:3x 的），而我们要
**随包发布**它 ⇒ 钉在仓里的版本号与 sha256 必须跟着上游走。手抄必烂，所以这一步交给
机器人：它读钉子、问上游最新的 release、发现新版就**开一个 PR** 把钉子升上去。

**它只开 PR，不自动合、不自动发版**：升级一个 75 MB 上下、而且每天都在变的第三方
二进制，该有人看一眼再合。（用户 11-09 20:0x：「magpie 要随着 magpie 的更新更新。
是不是要建一个机器人呀。」）

用法：
    python scripts/plobi/check_magpie_release.py --check   # 问上游有没有新版
    python scripts/plobi/check_magpie_release.py --write   # 把钉子升到上游最新版

退出码：
    0  已是最新（绿）
    10 上游有更新（workflow 据此开 PR；**不是红**）
    2  读不到上游（网络/限流/上游改名）⇒ **不判红**：查不到不等于有问题，
       和 dependency-health 同一口径（一扇常红的门等于没有门）。

只读上游，不写任何别的地方；``--write`` 只改仓里那份钉子文件。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PIN_PATH = ROOT / "third_party" / "magpie.json"
API = "https://api.github.com/repos/{repo}/releases/latest"

EXIT_UP_TO_DATE = 0
EXIT_NEWER = 10
EXIT_CANNOT_TELL = 2

_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def read_pin(path: Path = PIN_PATH) -> dict[str, Any]:
    """The one pin: which version of magpie we ship, and its hash per platform.

    ``assets`` carries one line per platform (``windows-x64``, ``darwin-arm64``,
    …). Every line must name an asset and its sha256 — a platform we ship without
    a hash is a platform we install unverified, which is the thing this file
    exists to prevent.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in ("repo", "version", "assets"):
        if not data.get(key):
            raise ValueError(f"pin is missing {key!r}: {path}")
    assets = data.get("assets")
    if not isinstance(assets, dict) or not assets:
        raise ValueError(f"pin has no platform assets: {path}")
    for platform, entry in assets.items():
        if not isinstance(entry, dict) or not entry.get("asset") or not entry.get("sha256"):
            raise ValueError(f"pin's {platform!r} line is missing asset/sha256: {path}")
    return data


def parse_version(text: str) -> tuple[int, int, int]:
    """`v0.1.3403` → (0, 1, 3403). Raises ValueError on anything else."""
    m = _VERSION.match(str(text or "").strip())
    if not m:
        raise ValueError(f"cannot read a version out of {text!r}")
    return tuple(int(part) for part in m.groups())  # type: ignore[return-value]


def is_newer(candidate: str, current: str) -> bool:
    """Is `candidate` a newer release than `current`?

    Upstream ships `v0.1.NNNN` many times a day, so the comparison is numeric:
    string comparison would call `v0.1.999` older than `v0.1.1000`.
    """
    return parse_version(candidate) > parse_version(current)


def fetch_latest(repo: str, *, timeout: float = 20.0) -> dict[str, Any]:
    """The upstream's latest release, as GitHub's API answers it.

    Raises on anything that is not a readable answer — the caller turns that into
    "cannot tell", never into "there is a problem".
    """
    url = API.format(repo=repo)
    request = urllib.request.Request(
        url, headers={"Accept": "application/vnd.github+json", "User-Agent": "plobi-magpie-watch"}
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def asset_of(release: dict[str, Any], name: str) -> dict[str, Any] | None:
    for asset in release.get("assets") or []:
        if isinstance(asset, dict) and asset.get("name") == name:
            return asset
    return None


def digest_of(asset: dict[str, Any]) -> str:
    """The asset's sha256, as the release API reports it (`sha256:<hex>`)."""
    digest = str(asset.get("digest") or "")
    if digest.startswith("sha256:"):
        return digest.split(":", 1)[1].strip().lower()
    return digest.strip().lower()


def bump(pin: dict[str, Any], release: dict[str, Any]) -> dict[str, Any]:
    """The pin as it should read once we ship the release the API just described.

    Every platform line is bumped together — one release, one version, all the
    binaries — and the whole bump is refused when *any* line cannot be verified.
    A release that carries five platforms but no hash for the sixth would leave us
    shipping one unverified binary, which is exactly what the pin prevents.
    """
    version = str(release.get("tag_name") or "").strip()
    if not version:
        raise ValueError("the release carries no tag_name")

    bumped: dict[str, Any] = {}
    for platform, entry in dict(pin.get("assets") or {}).items():
        name = str(entry.get("asset") or "")
        asset = asset_of(release, name)
        if asset is None:
            raise ValueError(f"the release has no asset named {name!r} ({platform})")
        sha = digest_of(asset)
        if not _HEX64.match(sha):
            raise ValueError(f"the {platform} asset carries no usable sha256 ({asset.get('digest')!r})")
        bumped[platform] = {
            "asset": name,
            "sha256": sha,
            "url": str(
                asset.get("browser_download_url")
                or f"https://github.com/{pin['repo']}/releases/download/{version}/{name}"
            ),
        }

    out = dict(pin)
    out["version"] = version
    out["assets"] = bumped
    out["checked_at"] = str(release.get("published_at") or "")[:10] or pin.get("checked_at", "")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="ask upstream whether there is a newer release")
    parser.add_argument("--write", action="store_true", help="bump the pin in the repo to the latest release")
    parser.add_argument("--pin", default=str(PIN_PATH), help="where the pin lives")
    args = parser.parse_args(argv)

    pin_path = Path(args.pin)
    try:
        pin = read_pin(pin_path)
    except Exception as exc:  # noqa: BLE001
        print(f"cannot read the pin: {exc}", file=sys.stderr)
        return EXIT_CANNOT_TELL

    try:
        release = fetch_latest(str(pin["repo"]))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        print(f"cannot reach upstream: {exc}", file=sys.stderr)
        return EXIT_CANNOT_TELL

    latest = str(release.get("tag_name") or "").strip()
    if not latest:
        print("upstream answered without a tag name", file=sys.stderr)
        return EXIT_CANNOT_TELL

    try:
        newer = is_newer(latest, str(pin["version"]))
    except ValueError as exc:
        print(f"cannot compare versions: {exc}", file=sys.stderr)
        return EXIT_CANNOT_TELL

    if args.write:
        try:
            out = bump(pin, release)
        except ValueError as exc:
            print(f"refusing to bump the pin: {exc}", file=sys.stderr)
            return EXIT_CANNOT_TELL
        pin_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        hashes = ", ".join(f"{p}={a['sha256'][:12]}…" for p, a in out["assets"].items())
        print(f"pinned {out['version']} ({hashes})")
        return EXIT_UP_TO_DATE

    if newer:
        print(f"upstream has {latest}; the pin says {pin['version']}")
        print(f"newer=true")
        return EXIT_NEWER
    print(f"the pin is current ({pin['version']})")
    print("newer=false")
    return EXIT_UP_TO_DATE


if __name__ == "__main__":
    raise SystemExit(main())

"""从一台活着的 magpie 上重生成插件集的钉子、名册与随包 tarball。

这是「插件集那一份钉子」的**唯一**生成口（对应 `third_party/magpie.json` 的角色）：

    python scripts/plobi/refresh_magpie_plugins.py --magpie-home <magpie 数据目录>

它做四件事：

1. 读 magpie 自己的 ``plugins.json``（它装了哪些插件）与 ``plugin-providers.json``
   （它认识哪些 provider、每个 provider 有哪些登录方式）——**都是从他们自己的文件读**，
   我们不猜。
2. 把每个插件的 tarball 取下来（公开 npm 上的从 registry 取；只有他们市场里有的，
   从他们 ``plugins/dist/`` 里那份拿），算 sha256。
3. 把名册**洗掉登录态**再写出去——``signedIn``／``accountId``／``accounts`` 这些是
   **这台机器**的状态，随包带出去就是把别人的登录态塞进发行包里。
4. 写下钉子 ``third_party/magpie-plugins.json``（版本＋sha256＋插件集自己的版本）。

用法：
    --magpie-home   magpie 的数据目录（默认从 ``$XDG_CONFIG_HOME/magpie`` 找）
    --out           仓里的落点（默认 ``third_party/magpie-plugins``）
    --pin           钉子文件（默认 ``third_party/magpie-plugins.json``）
    --magpie-version 钉住本体版本（默认从 ``third_party/magpie.json`` 读）
    --check         只核对，不写（CI 用）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]

#: 名册里**属于这台机器**的字段：随包带出去就是别人的登录态。
#: 键名照 magpie 自己的写法（``plugin-providers.json`` 每一行）。
#: 只洗这四个——``signedIn``／``authType``／``accountId``／``accounts``。
#: ``usage``／``renews``／``maxAccounts`` 那些是**能力与策略**（这个 provider 报不报用量、
#: 租约多久到期），不是这台机器的状态，所以留着。
STATE_KEYS = (
    "signedIn",
    "authType",
    "accountId",
    "accounts",
)

NPM = "https://registry.npmjs.org"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def npm_latest(name: str) -> str | None:
    url = "%s/%s" % (NPM, name.replace("/", "%2f"))
    try:
        with urllib.request.urlopen(url, timeout=30) as res:
            data = json.loads(res.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    latest = (data.get("dist-tags") or {}).get("latest")
    return str(latest) if latest else None


def npm_tarball(name: str, version: str, dest: Path) -> Path:
    base = name.split("/")[-1]
    url = "%s/%s/-/%s-%s.tgz" % (NPM, name.replace("/", "%2f"), base, version)
    with urllib.request.urlopen(url, timeout=120) as res:
        dest.write_bytes(res.read())
    return dest


def spec_name(spec: str) -> str:
    """``@scope/pkg@latest`` → ``@scope/pkg``；``pkg@latest`` → ``pkg``。"""
    if spec.startswith("@"):
        head, _, tail = spec.rpartition("@")
        return head if tail else spec
    return spec.split("@", 1)[0]


def vendored_tarball(name: str, dist_dir: Path) -> tuple[Path, str] | None:
    """他们市场里那份 tarball —— 作用域被拍平成 ``magpie-community-`` 前缀。"""
    base = name.split("/")[-1]
    for prefix in ("magpie-community-%s-" % base, "%s-" % base):
        for candidate in sorted(dist_dir.glob("%s*.tgz" % prefix)):
            version = candidate.name[len(prefix):].removesuffix(".tgz")
            return candidate, version
    return None


def sanitize_roster(rows: list[dict], pinned: dict[str, str]) -> list[dict]:
    """名册洗掉登录态，并把 spec 钉到我们钉住的那个版本。"""
    cleaned: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = {k: v for k, v in row.items() if k not in STATE_KEYS}
        name = spec_name(str(item.get("spec") or ""))
        if name in pinned:
            item["spec"] = "%s@%s" % (name, pinned[name])
        cleaned.append(item)
    return cleaned


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--magpie-home", default="")
    parser.add_argument("--out", default=str(REPO / "third_party" / "magpie-plugins"))
    parser.add_argument("--pin", default=str(REPO / "third_party" / "magpie-plugins.json"))
    parser.add_argument("--magpie-version", default="")
    parser.add_argument("--set-version", default="")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    home = Path(args.magpie_home) if args.magpie_home else Path(
        os.environ.get("XDG_CONFIG_HOME", "") or ""
    ) / "magpie"
    if not home.is_dir():
        print("找不到 magpie 的数据目录：%s（给 --magpie-home）" % home, file=sys.stderr)
        return 1

    spec_file = home / "plugins.json"
    roster_file = home / "plugin-providers.json"
    if not spec_file.is_file() or not roster_file.is_file():
        print("magpie 的数据目录里没有 plugins.json / plugin-providers.json：%s" % home, file=sys.stderr)
        return 1

    specs = json.loads(spec_file.read_text(encoding="utf-8")).get("plugins") or []
    roster = json.loads(roster_file.read_text(encoding="utf-8"))

    out_dir = Path(args.out)
    tar_dir = out_dir / "tarballs"
    if not args.check:
        tar_dir.mkdir(parents=True, exist_ok=True)

    dist_dir = home / "plugins" / "dist"
    pinned: dict[str, str] = {}
    entries: list[dict[str, Any]] = []
    for row in specs:
        spec = str(row.get("spec") or "")
        name = spec_name(spec)
        if not name:
            continue
        off = bool(row.get("off"))
        version = npm_latest(name)
        source = "npm"
        tarball = ""
        if version:
            target = tar_dir / ("%s-%s.tgz" % (name.split("/")[-1], version))
            if not args.check and not target.exists():
                npm_tarball(name, version, target)
            tarball = target.name
        else:
            found = vendored_tarball(name, dist_dir)
            if not found:
                print("拿不到 %s 的 tarball（npm 无、市场里也没有）——跳过" % name, file=sys.stderr)
                continue
            source_path, version = found
            target = tar_dir / source_path.name
            if not args.check and not target.exists():
                shutil.copy2(source_path, target)
            tarball = target.name
            # 这几个只有他们市场里有 ⇒ 出处记成 market，不是 npm。
            source = "marketplace"
        pinned[name] = version
        entry = {
            "spec": name,
            "version": version,
            "sha256": sha256_file(tar_dir / tarball) if tarball else "",
            "source": source,
            "tarball": tarball,
        }
        if off:
            entry["off"] = True
        entries.append(entry)
        print("%-9s %-52s %-10s %s" % (source, name, version, tarball))

    cleaned = sanitize_roster(roster if isinstance(roster, list) else [], pinned)
    roster_bytes = (json.dumps(cleaned, ensure_ascii=False, indent=1) + "\n").encode("utf-8")

    magpie_version = args.magpie_version
    if not magpie_version:
        try:
            magpie_version = str(
                (json.loads((REPO / "third_party" / "magpie.json").read_text(encoding="utf-8")) or {}).get("version") or ""
            )
        except Exception:
            magpie_version = ""

    set_version = args.set_version or "unversioned"
    pin = {
        "name": "magpie-plugins",
        "set_version": set_version,
        "magpie": magpie_version,
        "plugins": entries,
        "roster": {"file": "roster.json", "sha256": hashlib.sha256(roster_bytes).hexdigest()},
        "note": (
            "插件集这一份钉子：`third_party/magpie.json` 钉的是本体（magpie-cli），"
            "这里钉的是**它那 21 个插件**——版本＋sha256＋tarball。"
            "每一份 tarball 都**进仓**（third_party/magpie-plugins/tarballs/），"
            "所以打包不联网；`source` 记的是**出处**（15 个来自公开 npm，6 个来自他们"
            "自己的市场），不是取件的地方——要换版本由 "
            "`scripts/plobi/refresh_magpie_plugins.py` 从一台活着的 magpie 重生成。"
            "`set_version` 是插件集自己的版本，与 magpie 的版本**分开演进**："
            "本体可以单独更新（随包那份是地板，运行期 magpie 更新后插件集不回落），"
            "插件集自己涨版本时也不必等本体。"
            "`roster` 是他们的 provider 名册，**洗掉了登录态**"
            "（signedIn／accountId／accounts 那些是某台机器的状态，不许随包）。"
        ),
    }
    pin_bytes = (json.dumps(pin, ensure_ascii=False, indent=1) + "\n").encode("utf-8")

    if args.check:
        print("只核对，不写。当前会写：%d 个插件，名册 %d 行" % (len(entries), len(cleaned)))
        return 0

    (out_dir / "roster.json").write_bytes(roster_bytes)
    Path(args.pin).write_bytes(pin_bytes)
    print()
    print("钉子：%s（插件集版本 %s，配本体 %s）" % (args.pin, set_version, magpie_version or "未钉"))
    print("名册：%s（%d 行，已洗登录态）" % (out_dir / "roster.json", len(cleaned)))
    print("tarball：%s（%d 份）" % (tar_dir, len(entries)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Where the engine's three big files live, and what we know about them.

随包分发（用户 10-12 拍「随包全带」，约 +73MB）的**运行期那一半**。架构师 10-15 又加了一条硬约束：
**geo 与可执行体落 `$PLOBI_HOME` 侧，不落进仓**——Code 仓是公开产品仓（裁定 134），
把 59MB 的二进制与两份数据提交进 git 既污染历史也不是发布路径。

这一格**不下载任何东西**。它只做三件不需要网络也不需要猜的事：

* 说清三份文件**该在哪**、这台**在不在**、多大、指纹是什么（指纹是这台现读算出来的，不是抄的）；
* 让引擎在 `engine.binary` 空着的时候能找到随包落位的那一份；
* 把「冷启动缺 geo ⇒ mihomo 打印 start download 然后一直不监听 ⇒ 而那次下载又需要它自己提供的出口」
  这颗死结变成**一条能读的话**，而不是一个只能靠现场观察发现的挂起。

要不要加自动下载是另一个决定（它要同时回答两件事：从哪个 URL、以及第一次下载走哪条出口——
引擎还没起来的时候，表里那条出口正是它自己）。所以这里只留 `source` 字段并写明**未核**，
不凭印象填 URL。
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

#: What we pinned, measured on this box 2026-10-15 09:5x (`sha256` of the file actually in use,
#: `mihomo -v` for the version).  A digest we did not compute does not go in here.
ENGINE_VERSION = "v1.19.32"


@dataclass(frozen=True)
class Artifact:
    key: str
    basename: str
    subdir: str
    bytes: int
    sha256: str
    note: str

    @property
    def human(self) -> str:
        return f"{self.bytes / 1024 / 1024:.1f} MB"


BINARY = Artifact(
    key="binary",
    basename="mihomo.exe",
    subdir="bin",
    bytes=61_276_672,
    sha256="04f8d7fc2b314771e1ecbf15951d59f0e1cb7914503c88cfb6972ff6a824e314",
    note=f"Mihomo Meta {ENGINE_VERSION} windows amd64 with_gvisor；来源 URL 未核，不凭印象填",
)
GEOSITE = Artifact(
    key="geosite",
    basename="GeoSite.dat",
    subdir="",
    bytes=4_290_986,
    sha256="5800301b7bb0a04607c660b5d456aeca2c2262849fb77e6c7962174829a6b0f4",
    note="引擎按 geosite: 规则分类用；缺失时 mihomo 会自己打印 start download",
)
GEOIP = Artifact(
    key="geoip",
    basename="geoip.metadb",
    subdir="",
    bytes=8_414_245,
    sha256="605026f19311038efd07e7a006e49a59f1dec3e2b7d4e07b4ca47967fbb73e81",
    note="geoip 规则用，同上",
)

ARTIFACTS = (BINARY, GEOSITE, GEOIP)

#: The fourth file, deliberately **not** in `ARTIFACTS`: the three above are "the engine and
#: its data", and `engine files: n/3 在位` is that sentence.  wintun is the engine's *driver*
#: — only asked for when `engine.tun.enabled` is true, and it must sit next to the
#: executable (Windows loads it by name, `wintun.dll`, from the exe directory or System32),
#: which is a different question from "is it in the workdir".
#:
#: Digest measured on this box 2026-11-01 from the official package, whose own published
#: SHA2-256 (`https://www.wintun.net/`: `07c256185d6ee3652e09fa55c0b673e2624b565e02c4b9091c79ca7d2f24ef51`)
#: was compared to the bytes actually downloaded before extracting `wintun/bin/amd64/wintun.dll`.
WINTUN_VERSION = "0.14.1"
WINTUN = Artifact(
    key="wintun",
    basename="wintun.dll",
    subdir="bin",
    bytes=427_552,
    sha256="e5da8447dc2c320edc0fc52fa01885c103de8c118481f683643cacc3220dafce",
    note=f"wintun {WINTUN_VERSION} amd64（官方包 www.wintun.net，指纹现读核对）；TUN 建网卡用，"
    "必须与引擎可执行体同目录或在 System32 —— 缺它 mihomo 起不来 TUN",
)


def wintun_sides(binary: str = "", *, workdir: str | Path | None = None) -> list[Path]:
    """Where Windows would actually look for `wintun.dll`, in load order.

    The order is the OS's, not a preference: the executable's own directory, then the system
    directories, then the **current directory** — and the engine is spawned with
    `cwd=<workdir>` (`engine._spawn`), which is why the workdir counts as a real answer here.
    `$PLOBI_HOME/egress/bin` is *our* shelf, not a search path, so it never appears below.
    """
    out: list[Path] = []
    if binary:
        raw = os.path.abspath(os.path.expanduser(str(binary)))
        out.append((Path(raw) if os.path.isdir(raw) else Path(raw).parent) / WINTUN.basename)
    system = os.environ.get("SystemRoot") or r"C:\Windows"
    out.append(Path(system) / "System32" / WINTUN.basename)
    if workdir is not None:
        out.append(Path(workdir) / WINTUN.basename)
    return out


def wintun_side(binary: str = "", *, home: str | Path | None = None,
                workdir: str | Path | None = None) -> dict[str, Any]:
    """Is the TUN driver reachable, and where would we put it?  Read-only.

    `target` is the directory **we** own (the engine's workdir, else next to the binary):
    writing into somebody's portable tool directory is not a default, and the workdir is on
    the load path anyway because the engine starts with that as its current directory.

    With no workdir given it falls back to the compiled-config one (`<egress>/runtime`), so a
    status poll — which has no snapshot in hand — still asks about the directory the engine
    will actually be sitting in.
    """
    if workdir is None:
        workdir = egress_root(home) / "runtime"
    sides = wintun_sides(binary, workdir=workdir)
    found = next((p for p in sides if p.is_file()), None)
    source = artifact_path(WINTUN, home=home)
    target = Path(workdir) / WINTUN.basename
    return {
        "found": bool(found),
        "where": str(found) if found else "",
        "searched": [str(p) for p in sides],
        #: what `install_wintun` copies from, and where it lands
        "source": str(source) if source.is_file() else "",
        "target": str(target) if target else "",
    }


def install_wintun(binary: str = "", *, home: str | Path | None = None,
                   workdir: str | Path | None = None) -> tuple[bool, str]:
    """Put our shipped `wintun.dll` where the engine will load it.  **Copy, never download.**

    The engine loads it by name when the TUN comes up; with no copy on its search path mihomo
    fails the adapter and the machine keeps its old route — which reads as "TUN 拨上去了但没生效".
    Copying into the engine's own workdir is what makes 装机后不碰命令行 true on a box that
    pointed `engine.binary` at somebody's portable folder.
    """
    import shutil

    side = wintun_side(binary, home=home, workdir=workdir)
    if side["found"]:
        return True, f"TUN 驱动在位：{side['where']}"
    source, target = side["source"], side["target"]
    if not source:
        return False, (
            f"没有 wintun.dll（{WINTUN.human}，sha256 {WINTUN.sha256[:12]}…）可放：随包里没带，"
            f"$PLOBI_HOME 侧也没有 —— 把它放到 {target} 是 TUN 的前置"
        )
    if not target:
        return False, "引擎工作目录没读出来，wintun.dll 没地方放"
    try:
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    except OSError as exc:
        return False, f"wintun.dll 没能从 {source} 放到 {target}：{type(exc).__name__} {exc}"
    return True, f"TUN 驱动放到 {target}（来自 {source}）"


def egress_root(home: str | Path | None = None) -> Path:
    from agent.egress.engine import egress_dir

    return egress_dir(home)


#: Where a **packaged** app put the three files. Set by the desktop's main process from
#: `process.resourcesPath/egress` and inherited by every child it spawns — the alternative
#: (telling the user to run a script after install) is the thing 口径 32 says must not exist.
BUNDLE_ENV = "PLOBI_EGRESS_ASSET_DIR"


def bundle_dir() -> Path | None:
    """The随包 directory, or None in a source checkout / when the build shipped without them."""
    raw = (os.environ.get(BUNDLE_ENV) or "").strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_dir() else None


def artifact_path(artifact: Artifact, *, home: str | Path | None = None,
                  workdir: str | Path | None = None) -> Path:
    """Where this artifact actually is: 随包 first, then `$PLOBI_HOME/egress`, then the engine's workdir.

    随包 wins because it is the copy that shipped with *this build* and the one whose digest the
    manifest knows; a stale hand-placed file in the home directory must not silently outrank it.
    """
    roots: list[Path] = []
    bundle = bundle_dir()

    if bundle is not None:
        roots.append(bundle / artifact.subdir if artifact.subdir else bundle)
    roots.append(egress_root(home))
    if workdir is not None:
        roots.append(Path(workdir))

    for root in roots:
        candidate = root / artifact.basename
        if candidate.is_file():
            return candidate

    # Nothing is there: name the place a human could put it (the home side, not the bundle —
    # a shipped app's resources dir is read-only by intent).
    subdir = artifact.subdir
    return (egress_root(home) / subdir / artifact.basename) if subdir else (egress_root(home) / artifact.basename)


def in_workdir(artifact: Artifact, workdir: str | Path) -> Path:
    """The path the **engine** looks at. `artifact_path` may answer with the 随包 copy; this one
    never does — asking it "is the geo data present" would read the bundle and lie about the
    directory mihomo was actually started in."""
    return Path(workdir) / artifact.basename


def seed_into(workdir: str | Path, *, home: str | Path | None = None) -> list[str]:
    """Place the engine's own data files into its working directory — **copy, never download**.

    The cold-start deadlock this closes was measured on this box: with no `GeoSite.dat` next to
    the config, mihomo prints ``Can't find GeoSite.dat, start download`` and never binds, and
    that download itself needs the egress the engine is supposed to provide (纸 §28).  An
    installed app has the files; requiring someone to copy them by hand is the thing this
    function exists to make unnecessary.
    """
    import shutil

    target = Path(workdir)
    placed: list[str] = []
    if not target.is_dir():
        return [f"引擎工作目录 {target} 不在场：没地方播，先让它建出来再说"]

    for artifact in ARTIFACTS:
        if not artifact.subdir:
            dst = in_workdir(artifact, target)

            if dst.is_file():
                continue
            src = artifact_path(artifact, home=home)

            if not src.is_file() or src.resolve() == dst.resolve():
                continue
            try:
                shutil.copy2(src, dst)
                placed.append(
                    f"随包播了 {artifact.basename}（{dst.stat().st_size} 字节，来自 {src}）"
                )
            except OSError as exc:
                placed.append(f"{artifact.basename} 没能从 {src} 播到 {dst}：{type(exc).__name__} {exc}")

    return placed


def roots_note() -> str:
    bundle = bundle_dir()
    return f"随包落点 {bundle}" if bundle else "没有随包落点（源码态或这版构建没带）"


def _digest(path: Path, limit: int = 64 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        read = 0
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
            read += len(chunk)
            if read > limit:
                break
    return h.hexdigest()


def locate(artifact: Artifact, *, home: str | Path | None = None, workdir: str | Path | None = None,
           verify: bool = False) -> dict[str, Any]:
    """Where it should be, whether it is there, and (only if asked) whether the digest matches.

    `verify=False` on purpose for the status path: hashing 60 MB on every poll would make
    the panel/tray loop slower than the thing it reports on.
    """
    path = artifact_path(artifact, home=home, workdir=workdir)
    out: dict[str, Any] = {"key": artifact.key, "path": str(path), "exists": path.is_file(),
                           "expected_bytes": artifact.bytes, "human": artifact.human, "sha256": ""}
    if out["exists"]:
        out["bytes"] = path.stat().st_size
        if verify:
            out["sha256"] = _digest(path)
    return out


def match(entry: dict[str, Any], artifact: Artifact) -> bool:
    return bool(entry["exists"]) and (entry.get("sha256") in ("", artifact.sha256))


def default_binary(*, home: str | Path | None = None) -> str:
    """The shipped binary's location, used only when the table does not name one."""
    path = artifact_path(BINARY, home=home)
    return str(path) if path.is_file() else ""


def geo_warnings(workdir: str | Path) -> list[str]:
    """What the engine will try to download at startup — the cold-start deadlock, made visible.

    Measured on this box (10-12): with no geo data in the working directory mihomo prints
    `Can't find GeoSite.dat, start download` and **never binds**, and that download itself
    needs the egress the engine is supposed to provide.  Naming it before the spawn beats
    discovering it from a hung engine.
    """
    out = []
    for artifact in (GEOSITE, GEOIP):
        if in_workdir(artifact, workdir).is_file():
            continue
        source = artifact_path(artifact, workdir=workdir)
        if source.is_file():
            # 随包（或 $PLOBI_HOME）里有：起之前 seed_into 会复制过去，这不该读成一个洞。
            out.append(f"{artifact.basename} 会在起之前从 {source} 播到 {workdir}（随包分发，不下载）")
            continue
        out.append(
            f"engine workdir {workdir} has no {artifact.basename} — mihomo will print "
            f"'start download' and never bind (that download needs the egress this engine "
            f"provides); place it there ({artifact.human}, sha256 {artifact.sha256[:12]}…)"
        )
    return out


def summary(*, home: str | Path | None = None, workdir: Optional[Path] = None) -> dict[str, Any]:
    return {"version": ENGINE_VERSION,
            "files": [locate(a, home=home, workdir=workdir) for a in ARTIFACTS]}

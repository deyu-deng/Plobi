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

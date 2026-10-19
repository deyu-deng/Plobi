"""「接一个自己的」：装 / 卸一个第三方信源包。

这一层是**产品面上唯一一个会执行任意第三方代码**的地方，所以它的闸门比别的层都紧：

1. **REST 不直接调 pip。** 它 spawn 的是 `plobi collectors install <包名>` 这个 **CLI
   子命令**（argv 列表），跟 `plobi skills install` 那条路同形——闸门在 CLI 里，
   不在 HTTP 那一层。理由不是洁癖：HTTP 那一层是**用户输入直接落地到进程**的最近
   的一跳，把校验放在那儿等于校验和执行之间只隔着一次函数调用。

2. **只收包名，不收别的。** :func:`validate_package_name` 允许的形状就是 PEP 503 那个
   名字本身。**拒掉**：版本约束（`==1.2`）、extras（`[cli]`）、URL / git 地址
   （`git+https://…`、`pkg @ file:///…`）、可编辑安装（`-e`）、任何分隔符与空格。
   这一层**故意不接受** ``pip`` 自己接受的全集——那是任意代码执行的等价物，而一个
   设置面板里的一个输入框不配拥有那种权限。要钉版本、带 extras、从 git 装，请去
   终端里做，那里有 shell、有权限感、有人看着。

3. **装完立刻能被发现。** 装的是同一个 venv，`importlib.metadata` 那组
   ``plobi.collectors`` entry-point 立刻看得见，所以 :func:`refresh` 重跑一次发现；
   **卸掉**的那些要从注册表里真的消失（记在 :data:`registry.external_source_ids`），
   不然界面上会留一条再也装不回来的僵尸行。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: PEP 503 的名字形状。**没有版本、没有 extras、没有 URL、没有 `-e`** —— 见模块头第 2 条。
#: 结尾不许是 `.` 或 `-`（PEP 503 归一化会把它们去掉，所以它们在这里等于歧义）。
_PACKAGE_NAME = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$")

#: 单个名字的字符数上限（PyPI 自己用的是 100）。比那一行长 ⇒ 不是一个人手敲的名字。
_MAX_NAME_LENGTH = 100


class InvalidPackageName(ValueError):
    """这个输入不是一个包名——**拒掉，并把为什么写清楚**。"""


def validate_package_name(raw: str) -> str:
    """把用户敲的那一串收敛成**一个**包名，或者报错。

    归一化照 PEP 503（`-` 与 `_` 与 `.` 互认，大小写不敏感），所以用户敲
    `My-Package`、`my_package`、`MY.PACKAGE` 拿到的是同一个东西。

    报错那句话里要**说清这一层不收什么**——只说「名字不合法」的话，用户会一个
    一个试到装上为止，而每试一次就是一次任意代码执行。
    """
    candidate = (raw or "").strip()

    if not candidate:
        raise InvalidPackageName("空的是不行的：敲一个包名。")
    if len(candidate) > _MAX_NAME_LENGTH:
        raise InvalidPackageName(
            f"这个名字有 {len(candidate)} 个字符，比 {_MAX_NAME_LENGTH} 长："
            "人不会手敲这么长的包名，多半是粘了一堆别的东西进来。"
        )
    if candidate.startswith("-"):
        raise InvalidPackageName(
            "以 `-` 开头的是命令行开关，不是包名。这一层只收包名本身。"
        )
    if any(ch in candidate for ch in "/\\:@"):
        raise InvalidPackageName(
            "带路径分隔符、`:` 或 `@` 的是地址或 URL（本地路径、git 地址、直接引用），"
            "**不收**。这一层只从包索引装名字；要装 git 上的或本地的，去终端里 `pip install`。"
        )
    if "[" in candidate or "]" in candidate or "#" in candidate:
        raise InvalidPackageName(
            "带 extras（`[cli]`）或环境标记（`#`）的不收：extras 能带任意依赖树，"
            "而这里要的是「装一个自己」，不是「执行一段安装指令」。"
        )
    for marker in ("==", ">=", "<=", "~=", "!=", ">", "<"):
        if marker in candidate:
            raise InvalidPackageName(
                f"带版本约束（`{marker}`）的不收。要钉版本请在终端里 `pip install 'pkg==1.2'`——"
                "这里装的是最新版，装完 `pip list` 能看见版本，要换随时能换。"
            )
    if not _PACKAGE_NAME.match(candidate):
        raise InvalidPackageName(
            "这不是一个包名：只允许字母、数字、点、下划线、短横线，且不能以点或短横线开头。"
        )

    return candidate.replace("-", "_").replace(".", "_").lower()


def refresh() -> dict[str, Any]:
    """重跑一次发现，让刚装上的 / 刚卸掉的立刻生效。

    **注册表不会自己看**：`_discovered` 是粘的（幂等是它该有的性质），所以装完不刷新
    的话，用户会看到「装成功了但那一条没出现」——而那是最容易被读成「装失败」的一种
    形状，于是用户又装一遍。

    卸掉的那些要从表里真的拿掉（`external_source_ids` 记着哪几条是外面来的）。
    """
    from plobi.collectors import registry

    before = set(registry.source_ids())
    registry.refresh_discovery()
    after = set(registry.source_ids())

    return {
        "added": sorted(after - before),
        "removed": sorted(before - after),
        "sources": sorted(after),
    }


def install(package: str, *, upgrade: bool = True) -> dict[str, Any]:
    """装一个包，然后刷新发现。返回一句给**人**看的说法。

    `upgrade` 默认开：装一个"自己"的信源，用户要的是**最新版**那个（第三方包升级
    频繁，不开就等于让人自己猜"为什么我装完还是旧的"）。真要钉版本去终端里做。
    """
    name = validate_package_name(package)

    from plobi_cli.tools_config import _pip_install

    result = _pip_install([name] + (["--upgrade"] if upgrade else []))
    report = refresh()

    if result.returncode != 0:
        # 失败要说 pip 自己说了什么，别转述成"装不上"——那一句才是能排障的东西。
        tail = (result.stderr or result.stdout or "").strip().splitlines()[-12:]

        return {
            "ok": False,
            "output": "\n".join(tail),
            "package": name,
            "summary": f"装 {name} 失败（退出码 {result.returncode}）。上面是 pip 自己说的话。",
            **report,
        }

    added = f"，多了一条信源：{'、'.join(report['added'])}" if report["added"] else "，但没发现新的信源"

    return {
        "ok": True,
        "output": (result.stdout or "").strip().splitlines()[-6:],
        "package": name,
        "summary": f"装上了 {name}{added}。",
        **report,
    }


def uninstall(package: str) -> dict[str, Any]:
    """卸一个包，然后刷新发现。"""
    name = validate_package_name(package)

    from plobi_cli.tools_config import _pip_install

    # pip 没有 `uninstall`；用 `pip show -f` 确认它在，再拿 uninstall 的 argv。
    import subprocess
    import sys

    probe = subprocess.run(
        [sys.executable, "-m", "pip", "show", name],
        capture_output=True, text=True, timeout=120,
    )
    if probe.returncode != 0:
        raise InvalidPackageName(f"这个环境里没有 {name}，没什么可卸的。")

    result = subprocess.run(
        [sys.executable, "-m", "pip", "uninstall", "-y", name],
        capture_output=True, text=True, timeout=300,
    )
    report = refresh()

    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()[-12:]

        return {
            "ok": False,
            "output": "\n".join(tail),
            "package": name,
            "summary": f"卸 {name} 失败（退出码 {result.returncode}）。上面是 pip 自己说的话。",
            **report,
        }

    return {
        "ok": True,
        "output": (result.stdout or "").strip().splitlines()[-6:],
        "package": name,
        "summary": f"卸掉了 {name}。",
        **report,
    }


def installed_external_packages() -> list[dict[str, Any]]:
    """外面装上的那些包（配它们带来的信源 id）。

    这不是"我记了个账"——是从 `importlib.metadata` 现读，所以用户自己在终端里
    `pip install` 的也会出现在这儿。记一份账的话，用户终端里装的那一个就永远
    在界面上看不见。
    """
    from importlib import metadata

    from plobi.collectors import registry

    rows: list[dict[str, Any]] = []
    for entry_point in metadata.entry_points(group=registry.EXTERNAL_ENTRY_POINT_GROUP):
        spec: Optional[Any] = None
        try:
            spec = entry_point.load()
        except Exception as exc:  # noqa: BLE001 - 载入失败也是一条读数，不是不存在
            logger.warning("external collector %s failed to load: %s", entry_point.name, exc)
        rows.append({
            "distribution": entry_point.value.split(":")[0].split(".")[0],
            "entry_point": entry_point.name,
            "load_error": None if spec is not None else "这个包装上了但载不进来。",
            "source": getattr(spec, "source_id", None),
        })

    return sorted(rows, key=lambda row: row["entry_point"])
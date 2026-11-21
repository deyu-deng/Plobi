"""退役的自研腿躺在 `providers/apps/_archive/` 里，但它们仍然是可以 import 的模块。

归档目录不在打包清单里，所以它们到不了用户机器上；真正会出事的是**开发期**——
有人顺手 `from ..apps._archive.kiro import ...`，CI 全绿、真机全炸，因为打包时那
一行指向的文件根本不在包里。

这条测试就是那道闸：`_archive/` 之外的任何一行 Python 都不许提到它。
"""

from __future__ import annotations

from pathlib import Path

BEZEL = Path(__file__).resolve().parents[1] / "bezel"
ARCHIVE = BEZEL / "providers" / "apps" / "_archive"


def _sources() -> list[Path]:
    return [
        path
        for path in BEZEL.rglob("*.py")
        if ARCHIVE not in path.parents and path.is_file()
    ]


def test_archive_directory_is_outside_the_package() -> None:
    """归档目录存在，且不在任何 `__init__.py` 的导入链上。"""
    if not ARCHIVE.exists():
        return  # 还没归档过东西，没什么要守的
    package_init = BEZEL / "providers" / "apps" / "__init__.py"
    if package_init.exists():
        text = package_init.read_text(encoding="utf-8", errors="replace")
        assert "_archive" not in text, "apps/__init__.py 把 _archive 拉进了导入链"


def test_nothing_outside_the_archive_mentions_it() -> None:
    offenders: list[str] = []
    for path in _sources():
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            code = line.split("#", 1)[0]
            if "_archive" in code:
                offenders.append(f"{path.relative_to(BEZEL)}:{lineno}: {line.strip()}")
    assert not offenders, "这些地方引用了已退役的腿：\n" + "\n".join(offenders)

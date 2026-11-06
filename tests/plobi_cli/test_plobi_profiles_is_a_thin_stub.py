"""旧门牌只剩转发：`plobi_cli/profiles.py` 里不许再有实现（WP-PROFILE-CANCEL 第七轮）。

这条判据挡的是**概念长回来**：改名之后谁往那档加函数，等于把「一个分身一个家」那套
实现重新住进一个还叫 profiles 的文件里。门牌只能越来越薄。
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
STUB = REPO / "plobi_cli" / "profiles.py"


def test_the_old_doorplate_holds_no_implementation() -> None:
    assert STUB.is_file(), "门牌不在了——router.py 那一跳改完了就该把它删掉，并撤掉这条判据"
    tree = ast.parse(STUB.read_text(encoding="utf-8"))
    funcs = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert sorted(funcs) == ["__dir__", "__getattr__"], (
        "旧地址里长出了实现：%s。实现只许住在 plobi_cli/agent_identity.py。" % sorted(funcs)
    )
    assigns = [
        t.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        for t in n.targets
        if isinstance(t, ast.Name)
    ]
    assert not assigns, "门牌里不该有赋值：%s" % assigns
    assert sum(1 for _ in ast.walk(tree) if isinstance(_, (ast.Import, ast.ImportFrom))) <= 2


def test_the_doorplate_forwards_to_identity(tmp_path, monkeypatch) -> None:
    """转发是真的通：从旧地址取一个名字，取到的就是 identity 那一个对象。"""
    monkeypatch.syspath_prepend(str(REPO))
    import importlib

    identity = importlib.import_module("plobi_cli.agent_identity")
    legacy = importlib.import_module("plobi_cli.profiles")
    for name in ("get_profile_dir", "normalize_profile_name", "validate_profile_name"):
        assert getattr(legacy, name) is getattr(identity, name), name

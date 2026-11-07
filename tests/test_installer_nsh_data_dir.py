"""装机器那一页「选择数据目录」：用户填的那一格**必须被记下来**。

2026-11-07 实测：安装时填好了，安装完主程序却读到默认格 ⇒ 数据与 uv 托管的 Python 全落
C 盘。根因是 `customInstall` 跑在安装 Section 里，页面早销毁了，那个控件句柄读回来是空。

契约：页还活着的时候把值读进 Var；`customInstall` 只用那个 Var。

**这档不编译 NSIS**（要真 MUI2 ＋ makensis，由本地与 CI 的构建验）——它盯形状。
"""

import json
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_NSH = _ROOT / "apps" / "desktop" / "installer.nsh"
_PACKAGE_JSON = _ROOT / "apps" / "desktop" / "package.json"

_SOURCE = _NSH.read_text(encoding="utf-8")


def _function_text(name: str) -> str:
    start = _SOURCE.index(f"Function {name}")
    end = _SOURCE.index("FunctionEnd", start)
    return _SOURCE[start:end]


def _macro_text(name: str) -> str:
    start = _SOURCE.index(f"!macro {name}")
    end = _SOURCE.index("!macroend", start)
    return _SOURCE[start:end]


def test_the_page_reads_the_typed_value_into_a_var_while_the_page_is_alive():
    """值必须在页面还活着的时候读进 Var（`nsDialogs::Show` 之后、`FunctionEnd` 之前）。"""
    body = _function_text("PlobiDataPageFunc")

    show = body.index("nsDialogs::Show")
    read = body.index("${NSD_GetText} $PlobiDataDirField $PlobiDataDirValue")

    assert show < read, "读值必须在 Show 之后（Show 返回时页面才刚要销毁，句柄还活着）"
    assert "FunctionEnd" not in body[:read], "读值必须在页面函数里"


def test_the_install_macro_uses_the_var_not_the_dead_control_handle():
    """`customInstall` 只用那个 Var；再去读那个控件句柄 ⇒ 读回来是空 ⇒ 落回默认格。"""
    macro = _macro_text("customInstall")

    assert "$PlobiDataDirValue" in macro, "安装那一段必须用页面存下来的那个 Var"
    assert "NSD_GetText $PlobiDataDirField" not in macro, (
        "安装那一段不许再读那个控件句柄（页面早销毁了，读回来是空）"
    )


def test_the_macro_still_writes_the_registry_key_the_app_reads():
    """落点写进 `HKCU\\Software\\Plobi\\DataDir`——主进程读的就是这一格。"""
    macro = _macro_text("customInstall")

    assert 'WriteRegStr HKCU "Software\\Plobi" "DataDir"' in macro
    assert re.search(r'WriteRegStr\s+HKCU\s+"Software\\Plobi"\s+"DataDir"\s+"\$PlobiDataDirValue"', macro)


def test_the_page_is_declared_after_the_installation_directory_page():
    """这一页紧跟在「选择安装目录」之后——两件事分开问，改一个不等于改了另一个。"""
    macro = _macro_text("customPageAfterChangeDir")

    assert "Page custom PlobiDataPageFunc" in macro, (
        "自定义页要用 MUI2 的形状：顶层只留一页的声明，正文在那个 Function 里"
    )
    assert "MUI_HEADER_TEXT" not in macro, (
        "顶层不许用 MUI_HEADER_TEXT——它展开成 SendMessage，而顶层不在 Section/Function 里"
    )


def test_the_script_is_utf8_with_bom():
    """**必须带 BOM**：NSIS 按系统 ACP 读 .nsh，不带 BOM 的 UTF-8 中文会变成乱码
    （编译期报 `Bad text encoding`）。"""
    raw = _NSH.read_bytes()

    assert raw.startswith(b"\xef\xbb\xbf"), (
        "installer.nsh 必须以 UTF-8 BOM 开头（NSIS 按 ACP 读，没 BOM 的中文会乱码）"
    )


def test_the_builder_actually_includes_this_file():
    """这一页进包靠 `package.json` 的 nsis.include——它必须指着**仓里这一份**。

    原来它躺在 `apps/desktop/build/installer.nsh`，而 `apps/desktop/build/` 整个是
    gitignore 的 ⇒ CI 的干净 checkout 里根本没有它 ⇒ 「装的时候能自选数据目录」那一页
    **从未进过任何一个发布包**。
    """
    config = json.loads(_PACKAGE_JSON.read_text(encoding="utf-8"))
    include = config.get("build", {}).get("nsis", {}).get("include")

    assert include == "installer.nsh", f"nsis.include 指着别处了：{include!r}"
    assert _NSH.is_file(), "被指的那一份必须在仓里（build/ 整个是 gitignore 的）"

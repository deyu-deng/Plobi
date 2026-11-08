"""装机器那一页「选择数据目录」：用户填的那一格**必须被记下来**。

2026-11-07 实测：安装时填好了，安装完主程序却读到默认格 ⇒ 数据与 uv 托管的 Python 全落
C 盘。根因是 nsDialogs 的控件**只在 `nsDialogs::Show` 之前活着**：Show 返回时对话框连同
控件已经销毁 ⇒ 在它之后读句柄只会读回空。

契约：读值放在**离开回调**里（`Page custom 创建函数 离开函数`）；`customInstall` 只用那个
Var，不再碰控件句柄。

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


def test_the_value_is_read_in_the_leave_callback_not_after_show():
    """值必须在**离开回调**里读，不是在 `Show` 之后读。

    nsDialogs 的控件只在 `nsDialogs::Show` 之前活着：Show 返回时对话框连同控件已经
    销毁 ⇒ 那时再去读句柄只会读回空（用户明明选了 D 盘，记下来的却是默认格）。
    官方形状是 `Page custom 创建函数 离开函数`。
    """
    page = _function_text("PlobiDataPageFunc")
    leave = _function_text("PlobiDataPageLeave")

    assert "${NSD_GetText} $PlobiDataDirField $PlobiDataDirValue" not in page, (
        "Show 之后控件已经销毁，那一读只能读回空"
    )
    assert "${NSD_GetText} $PlobiDataDirField $PlobiDataDirValue" in leave, (
        "读值必须在离开回调里（用户点「下一步」时控件还在）"
    )


def test_the_page_declares_the_leave_callback():
    """那一页的声明必须带上离开回调，否则读值没有落脚点。"""
    macro = _macro_text("customPageAfterChangeDir")

    assert "Page custom PlobiDataPageFunc PlobiDataPageLeave" in macro, (
        "自定义页的形状：Page custom 创建函数 离开函数"
    )


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


def test_the_default_is_the_previous_choice_when_there_is_one():
    """重装时那一页的默认值 = 上一次选的那一格（注册表里那一格）。

    静默更新（`/S`）时那一页**根本不跑** ⇒ 那一格只能从这里来；否则每次更新都会把用户
    选的位置改回默认格（数据看着像丢了，运行时也搬到另一块盘）。
    """
    pre = _macro_text("preInit")

    assert "ReadRegStr" in pre, "默认格必须先看注册表里那一次的选择"
    assert '"Software\\Plobi"' in pre and '"DataDir"' in pre, pre


def test_uninstall_keeps_the_recorded_directory():
    """卸载只摘程序的门牌，不抹掉「数据目录在哪一格」——重装指回同一格，历史还在。"""
    macro = _macro_text("customUnInstall")

    assert 'DeleteRegValue HKCU "Software\\Plobi" "DataDir"' not in macro, (
        "抹掉那一格 ⇒ 重装时又回到默认格，用户以为数据没了"
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


def test_the_installer_registers_the_app_user_model_id():
    """点通知是 Windows **按 AUMID 唤起可执行文件** ⇒ 这一格得登记。

    2026-11-08 用户截图实锤：点通知弹出来的是 Electron 的默认窗口（开发态那个
    不带 app 路径的 electron.exe）。装机器上 AUMID 只在开始菜单快捷方式里、
    注册表里没有 ⇒ 系统没有硬目标。electron-builder 的卸载器本来就按这个键清理，
    我们此前却从来没登记过。
    """
    config = json.loads(_PACKAGE_JSON.read_text(encoding="utf-8"))
    app_id = config.get("build", {}).get("appId")
    assert app_id, "package.json 的 build.appId 没了（AUMID 要与它一致）"

    key = f'"Software\\Classes\\AppUserModelId\\{app_id}"'
    install = _macro_text("customInstall")
    uninstall = _macro_text("customUnInstall")

    assert key in install, f"安装时没登记 AUMID（{key}）：系统没有硬目标可唤起"
    assert key in uninstall, "卸载时要把那一格摘掉"

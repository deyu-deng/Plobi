"""`.js` 编译产物遮住 `.ts/.tsx` 源码这颗硬检查（WP-DESKTOP-POOL-GHOST-PROFILE 的连带刀，用户 10-13 拍：铸成检查）。

现形过的两次都长得不像 bug：桌面整页白屏，以及 vitest 报"刚导出的函数不存在"。
成因是 `apps/desktop/src` / `apps/desktop/electron` 里躺着 build 模式 tsc 吐出的 `.js`，
而 `.gitignore:166` 整类忽略它们 ⇒ `git status` 干净、模块解析却**优先**取磁盘上真存在的 `.js`。
所以判据不能靠"看一眼 status"，只能当场扫。
"""

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"


def _load_script(filename: str):
    """scripts/ 不是包，按文件加载（模块名必须是合法标识符，同 test_no_drive_root_writes）。"""
    safe = "_plobi_script_" + "".join(ch if ch.isalnum() else "_" for ch in filename)
    spec = importlib.util.spec_from_file_location(safe, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[safe] = module
    spec.loader.exec_module(module)
    return module


footguns = _load_script("check-windows-footguns.py")


def _desktop_src(tmp_path: Path) -> Path:
    src = tmp_path / "apps" / "desktop" / "src" / "app"
    src.mkdir(parents=True, exist_ok=True)
    return src


def test_shadow_is_found_when_js_sits_next_to_its_source(tmp_path: Path) -> None:
    src = _desktop_src(tmp_path)
    (src / "foo.ts").write_text("export const foo = 1\n", encoding="utf-8")
    (src / "foo.js").write_text("export const foo = 0\n", encoding="utf-8")  # 上一版编译产物

    found = footguns.find_js_shadows(tmp_path)

    assert ("apps/desktop/src/app/foo.js", "apps/desktop/src/app/foo.ts") in found, found
    assert footguns.check_js_shadows(tmp_path) == 1, "有影子必须非零退出"


def test_js_without_a_source_of_the_same_stem_is_not_a_shadow(tmp_path: Path) -> None:
    """宁缺勿滥：本仓 `allowJs: false`，`src` 下不该有手写 `.js`；但没有同名源码就谈不上"遮住"，
    把这种也算红会让人分不清要清的是哪一个。"""
    src = tmp_path / "apps" / "desktop" / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "legacy.js").write_text("1\n", encoding="utf-8")

    assert footguns.find_js_shadows(tmp_path) == []


def test_node_modules_is_never_scanned(tmp_path: Path) -> None:
    pkg = tmp_path / "apps" / "desktop" / "src" / "node_modules" / "dep"
    pkg.mkdir(parents=True)
    (pkg / "index.ts").write_text("1\n", encoding="utf-8")
    (pkg / "index.js").write_text("1\n", encoding="utf-8")

    assert footguns.find_js_shadows(tmp_path) == []


def test_the_real_tree_is_shadow_free_right_now() -> None:
    """这条就是今天白屏与假"导出不存在"的守门：谁再 emit 出 `.js`，这里立刻红。

    判据与清理命令同一个口径（`npx tsc -b . --clean`，`git ls-files` 里 0 个被跟踪的
    `apps/desktop/**/*.js` ⇒ 清不掉任何真实文件）。
    """
    shadows = footguns.find_js_shadows(REPO_ROOT)

    assert shadows == [], (
        "apps/desktop 源码旁躺着 %d 个编译产物 .js，Vite/Vitest 会先撞上它：%s；"
        "清法：cd apps/desktop && npx tsc -b . --clean" % (len(shadows), shadows[:5])
    )

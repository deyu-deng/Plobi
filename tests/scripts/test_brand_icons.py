"""`scripts/brand_icons.py` 的行为契约（WP-ICON-FULL-SWEEP）。

判据全是**不变式**，不是今天的像素快照：

  1. ICO 七档齐全，且**每一档 blob 自己的像素尺寸 == 表里声明的尺寸** —— 这一条专防
     R-052 那个坑：Pillow 存 ICO 会从最大那张重新降采样出每一档，预先算好的小档被静默丢掉。
  2. 16px 档**不等于**把 1024 档缩到 16（字节级不同）—— 各档必须各画各的。
  3. ICNS 的 magic 是 `icns`（不是被改了扩展名的 PNG），长度字段 == 文件实际字节数，
     且十个类型档逐条长度自洽能走到底。
  4. ACP 那稿过 `tests/acp/test_registry_manifest.py` 同四条：16 网格、无十六进制色、
     无渐变、每个 fill/stroke 只能是 currentColor/none。
  5. 主色对浅底与深底**都**达到 Fluent 要求的 3.0:1（对比度是算出来的，不是挑出来的）。
  6. 生成是确定性的：同一份图元跑两次，产物逐字节相同。
  7. 供界面用的那份主源**只能是 currentColor** —— 界面里那两处品牌图形要随主题换色，
     塞进十六进制色就再也换不了（WP-BRAND-THEME-FOLLOW · 裁定 213）。

只用 stdlib（PNG 尺寸从 IHDR 读，不引 PIL）。
"""

from __future__ import annotations

import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = ROOT / "scripts" / "brand_icons.py"
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
ICNS_WANTED = {"ic04", "ic05", "ic07", "ic08", "ic09", "ic10", "ic11", "ic12", "ic13", "ic14"}

#: icns 每一档的**标称像素边长**（Apple 那份表）。PNG 档的尺寸必须与它对上；
#: 最小的两档（ic04/ic05）在 macOS `iconutil` 出来的形状里是**原始 ARGB**，
#: 不是 PNG —— 那是 icns 的正规载荷，不是「改扩展名的 PNG」。
_ICNS_NOMINAL_PX = {
    "ic04": 16, "ic05": 32, "ic07": 128, "ic08": 256, "ic09": 512,
    "ic10": 1024, "ic11": 32, "ic12": 64, "ic13": 256, "ic14": 512,
}

#: 这两条落点已经不在 `brand_icons.py` 手里了（11-11 `bfe5a9a1`
#: 「三个平台各给一张对味的图标」）：`.ico` 由 `apps/desktop/scripts/make-platform-icons.py`
#: 从母版出（满幅圆角，给 Windows 任务栏），`.icns` 是 macOS 上 `iconutil` 出的
#: （最小的两档走原始 ARGB，见 `test_icns_container_is_a_real_icns`）。
#: 所以 `brand_icons.py --check` 对这两条**本来就红**——那是现况，不是漂移。
_OWNED_ELSEWHERE = {
    "apps/desktop/assets/icon.ico",
    "apps/desktop/assets/icon.icns",
}


def _ico_entries(blob: bytes):
    assert blob[:4] == b"\x00\x00\x01\x00", "不是 ICO（Reserved=0, Type=1）"
    n = struct.unpack("<H", blob[4:6])[0]
    out = []
    for i in range(n):
        o = 6 + 16 * i
        w, h, _cc, _rs, _pl, _bp, size, off = struct.unpack("<BBBBHHII", blob[o:o + 16])
        out.append((w or 256, h or 256, size, off))
    return out


def _png_size(blob: bytes):
    assert blob[:8] == b"\x89PNG\r\n\x1a\n", "blob 不是 PNG"
    w, h = struct.unpack(">II", blob[16:24])
    return w, h


def _run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)


def test_ico_has_every_size_and_each_blob_is_its_own_resolution():
    blob = (ROOT / "apps/desktop/assets/icon.ico").read_bytes()
    entries = _ico_entries(blob)
    assert sorted(px for px, _, _, _ in entries) == sorted(ICO_SIZES)
    for px, py, size, off in entries:
        assert px == py
        w, h = _png_size(blob[off:off + size])
        assert (w, h) == (px, py), f"{px}px 档里装的是 {w}x{h} —— 有人拿别档凑数"


def test_small_size_is_not_a_downscale_of_the_largest():
    blob = (ROOT / "apps/desktop/assets/icon.ico").read_bytes()
    entries = {px: blob[off:off + size] for px, _, size, off in _ico_entries(blob)}
    from PIL import Image
    import io
    big = Image.open(io.BytesIO(entries[256])).convert("RGBA")
    down = big.resize((16, 16), Image.LANCZOS).tobytes()
    shipped = Image.open(io.BytesIO(entries[16])).convert("RGBA").resize((16, 16)).tobytes()
    assert shipped != down, "16px 档是 256 缩下来的 —— 各档必须各画各的"


def test_icns_container_is_a_real_icns():
    """icns 是个**容器**：头四字节 `icns`、长度字段 == 文件实际字节数，
    每一档逐条长度自洽能走到底。

    载荷有两种合法形状：大尺寸走 PNG（它的 IHDR 尺寸必须与那一档的**标称边长**对上），
    最小的两档（ic04/ic05）在 macOS `iconutil` 出来的形状里走**原始 ARGB**
    （`iconutil` 就是这么出的）——那是 icns 的正规载荷，不是「改扩展名的 PNG」。
    另有一档 `info` 是元数据（bplist），不参与画。
    """
    blob = (ROOT / "apps/desktop/assets/icon.icns").read_bytes()
    assert blob[:4] == b"icns", f"头四字节是 {blob[:4]!r}，不是 icns（改扩展名的 PNG 就是这个形状）"
    declared = struct.unpack(">I", blob[4:8])[0]
    assert declared == len(blob), f"长度字段 {declared} != 实际 {len(blob)}"
    o, seen = 8, set()
    while o < len(blob):
        t = blob[o:o + 4].decode()
        ln = struct.unpack(">I", blob[o + 4:o + 8])[0]
        assert ln >= 8 and o + ln <= len(blob), f"{t} 档长度越界"
        payload = blob[o + 8:o + ln]
        if t == "info":
            assert payload[:4] == b"bpli", f"info 档不是 bplist：{payload[:4]!r}"
        else:
            assert t in _ICNS_NOMINAL_PX, f"没记到的 icns 档：{t}"
            if payload[:4] == b"ARGB":
                # 最小的两档：原始 ARGB（iconutil 的形状）
                assert t in ("ic04", "ic05"), f"{t} 不该是原始 ARGB"
            else:
                w, h = _png_size(payload)
                nominal = _ICNS_NOMINAL_PX[t]
                assert (w, h) == (nominal, nominal), f"{t} 档是 {w}x{h}，标称边长是 {nominal}"
        seen.add(t)
        o += ln
    assert ICNS_WANTED <= seen, f"缺档：{ICNS_WANTED - seen}"


def test_acp_icon_svg_satisfies_the_registry_contract():
    import re
    import xml.etree.ElementTree as ET
    text = (ROOT / "acp_registry/icon.svg").read_text(encoding="utf-8")
    root = ET.fromstring(text)
    assert root.attrib["viewBox"] == "0 0 16 16"
    assert root.attrib["width"] == "16" and root.attrib["height"] == "16"
    assert "linearGradient" not in text and "radialGradient" not in text and "url(#" not in text
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", text), "ACP 稿里不许出现十六进制颜色"
    for el in root.iter():
        for attr in ("fill", "stroke"):
            v = el.attrib.get(attr)
            if v is not None:
                assert v in {"currentColor", "none"}, f"{el.tag} 的 {attr}={v} 不合规"


def _lum(hx):
    c = [int(hx[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    c = [(v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4) for v in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def _ratio(a, b):
    l1, l2 = sorted([_lum(a), _lum(b)], reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


def test_brand_color_clears_fluent_contrast_on_both_themes():
    sys.path.insert(0, str(ROOT / "scripts"))
    import brand_icons as bi
    shell = "%02X%02X%02X" % bi.SHELL
    assert _ratio(shell, "F6F6F6") >= 3.0, "陶土对浅底不过 3.0:1"
    assert _ratio(shell, "262626") >= 3.0, "陶土对深底不过 3.0:1"
    cream = "%02X%02X%02X" % bi.CREAM
    assert _ratio(shell, cream) >= 3.0, "底板与形之间不过 3.0:1"


def test_upstream_brand_assets_are_gone():
    """上游 Nous 的品牌图形不许还留在渲染层能拿到的地方。

    R-052 那份"9 个位置"清单漏了 `brand-mark.tsx`，About 那一屏一直挂着
    `nous-girl.jpg`（戴耳机女孩）。这一条同时盯**引用**与**文件本身**——
    只换引用不删文件，下次有人 grep 图片名又会把它捡回来。

    ⏸ `public/plobi.png` 与 `public/plobi-sprite.png` 同样是上游美术（蓝色翅膀、
    手持双蛇杖的像素骑士，7-14 那批），但**全仓零引用**、属内容资产不是图标位，
    删不删归 R-054 对外门面那一刀，本条不替它决定，所以不在 banned 里。
    """
    banned = ("nous-girl",)
    roots = [ROOT / "apps/desktop/src", ROOT / "apps/desktop/public", ROOT / "web", ROOT / "website/static"]
    for base in roots:
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if not p.is_file() or "node_modules" in p.parts:
                # node_modules 里 @nous-research/ui 自带一个 nous-girl 徽章组件，那是**依赖内部**，
                # 我们没 import 它。把它算成残留是判据错，不是残留错（第一版就是这么红过一次）。
                continue
            if any(b in p.name for b in banned):
                raise AssertionError(f"上游品牌资产还在：{p.relative_to(ROOT)}")
            if p.suffix in {".tsx", ".ts", ".html", ".css", ".json"}:
                text = p.read_text(encoding="utf-8", errors="replace")
                if any(b in text for b in banned):
                    raise AssertionError(f"{p.relative_to(ROOT)} 仍引用上游品牌资产")
    # 内部品牌位必须吃生成出来的那份图元表（内联 SVG，currentColor 随主题换色）
    src = (ROOT / "apps/desktop/src/components/brand-mark.tsx").read_text(encoding="utf-8")
    assert "plobi-glyphs" in src, "BrandMark 没吃 brand_icons.py 生成的那份图元表"
    assert (ROOT / "apps/desktop/src/components/plobi-glyphs.ts").exists()
    assert "bg-white" not in src, "BrandMark 不许再写死白底 —— 深色模式里那是一块白砖"


def test_theme_aware_masters_carry_no_baked_color():
    """界面里那两处品牌图形要随主题换色 ⇒ 供它们用的主源只能是 currentColor。

    固定色那两份（`plobi-wordmark.svg` / `-dark.svg`）是**品牌常量**，不归这条管；
    这条只盯**界面用的那一份**：它一旦被塞进十六进制色，界面就再也换不了色。
    """
    import re
    mono = (ROOT / "brand/plobi-wordmark-mono.svg").read_text(encoding="utf-8")
    assert "currentColor" in mono, "currentColor 那份字标里没有 currentColor"
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", mono), "currentColor 那份字标里混进了十六进制颜色"

    glyphs = (ROOT / "apps/desktop/src/components/plobi-glyphs.ts").read_text(encoding="utf-8")
    assert "PLOBI_MARK_PATH" in glyphs and "PLOBI_WORDMARK" in glyphs, "图元表缺内容"
    m = re.search(r"export const PLOBI_MARK_PATH =\s*'([^']+)'", glyphs)
    assert m, "图元表里没有 PLOBI_MARK_PATH"
    assert m.group(1) in mono, "图元表里的龟与 brand/plobi-wordmark-mono.svg 不是同一张图"


def _load_brand_icons():
    import importlib.util

    spec = importlib.util.spec_from_file_location("_brand_icons", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _same_raster(a: bytes, b: bytes) -> bool:
    """两张位图比**像素**，不比编码字节。

    为什么不能比字节：同一张图在 Windows 与 Linux 上由 Pillow 编出来的 PNG/ICO
    字节不同（zlib 版本与压缩级别），逐字节比 ⇒ 同一个内容在 CI 上被判「不一致」。
    像素是跨平台的那一层，「这张图还是不是那张」问的就是它。
    """
    import io

    from PIL import Image

    if a[:8] == b"\x89PNG\r\n\x1a\n" and b[:8] == b"\x89PNG\r\n\x1a\n":
        ia = Image.open(io.BytesIO(a)).convert("RGBA")
        ib = Image.open(io.BytesIO(b)).convert("RGBA")
        return ia.size == ib.size and ia.tobytes() == ib.tobytes()
    if a[:4] == b"\x00\x00\x01\x00" and b[:4] == b"\x00\x00\x01\x00":
        ia = Image.open(io.BytesIO(a))
        ib = Image.open(io.BytesIO(b))
        if sorted(ia.ico.sizes()) != sorted(ib.ico.sizes()):
            return False
        for size in ia.ico.sizes():
            if (
                ia.ico.getimage(size).convert("RGBA").tobytes()
                != ib.ico.getimage(size).convert("RGBA").tobytes()
            ):
                return False
        return True
    return a == b


def test_generation_is_deterministic():
    """同一份图元跑两次，产物逐字节相同；并且 `brand_icons.py` 仍管着的那几份落点
    与盘上的一致。

    **跨平台写法**（原来跑 `scripts/brand_icons.py --check`，有两处不能成立）：
    * 鸿蒙那两条落点在**另一个仓**（与 Code 同级的 `Plobi-Harmony-App/`）里，CI 只签出
      Code 一个仓 ⇒ `--check` 会在 `_abs()` 上撞「不是一个 git 仓」直接抛错
      （`scripts/brand_icons.py:317-329`）。那个仓不在场时，那两条没有可比对象。
    * `.ico` / `.icns` 已由平台那一路接管（`_OWNED_ELSEWHERE`），`--check` 对它们本来就红。

    位图那几份与盘上的比的是**像素**（`_same_raster`）：同一张图在两个平台上编出来的
    字节不同，比字节会把「同一个内容」判成「不一致」。
    """
    bi = _load_brand_icons()

    h1, masters1 = bi.assets()
    h2, masters2 = bi.assets()
    assert h1 == h2 and masters1 == masters2, "同一份图元跑两次的产物不一样 —— 生成不是确定性的"

    files = {**h1, **{k: ("t", v) for k, v in masters1.items()}}
    harmony_ready = (bi.HARMONY / ".git").exists()
    checked = 0
    for rel, (kind, data) in sorted(files.items()):
        if rel in _OWNED_ELSEWHERE:
            continue
        if rel.startswith("@harmony/"):
            if not harmony_ready:
                continue
            path = bi.HARMONY / rel[len("@harmony/"):]
        else:
            path = bi.ROOT / rel
        if kind == "b":
            cur = path.read_bytes()
            same = _same_raster(cur, data)
        else:
            cur = path.read_text(encoding="utf-8")
            same = cur == data
        assert same, f"{rel} 与主源不一致（重画一遍就能定）"
        checked += 1
    # 几乎没比到东西 ⇒ 判据自己坏了（不是「一致」）
    assert checked >= len(files) - len(_OWNED_ELSEWHERE) - 2, (
        f"只比到 {checked}/{len(files)} 份落点 —— 判据自己没跑起来"
    )


def test_no_legacy_avatar_bytes_remain():
    """落点上的 1024 主图必须已经是"陶土底占大头"，而不是旧头像那种奶油脸铺满。

    旧头像没有底板，主色是米白；新图标是满幅陶土方底。所以"陶土像素过半"就是
    "这张已经不是旧头像"的可复跑判据——比留一个 md5 更耐改。
    """
    from PIL import Image
    im = Image.open(ROOT / "apps/desktop/assets/icon.png").convert("RGB")
    assert im.size == (1024, 1024)
    px = list(im.getdata())
    n = len(px)
    hits = sum(1 for p in px if abs(p[0] - 0xCD) <= 2 and abs(p[1] - 0x6B) <= 2 and abs(p[2] - 0x43) <= 2)
    assert hits / n > 0.5, f"陶土底只占 {hits / n:.1%}，落点上的还是旧图形"

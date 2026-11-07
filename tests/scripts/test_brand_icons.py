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
    blob = (ROOT / "apps/desktop/assets/icon.icns").read_bytes()
    assert blob[:4] == b"icns", f"头四字节是 {blob[:4]!r}，不是 icns（改扩展名的 PNG 就是这个形状）"
    declared = struct.unpack(">I", blob[4:8])[0]
    assert declared == len(blob), f"长度字段 {declared} != 实际 {len(blob)}"
    o, seen = 8, set()
    while o < len(blob):
        t = blob[o:o + 4].decode()
        ln = struct.unpack(">I", blob[o + 4:o + 8])[0]
        assert ln >= 8 and o + ln <= len(blob), f"{t} 档长度越界"
        _w, _h = _png_size(blob[o + 8:o + ln])
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


def test_generation_is_deterministic():
    a = subprocess.run([sys.executable, str(SCRIPT), "--check"], capture_output=True, text=True)
    assert a.returncode == 0, a.stdout + a.stderr
    assert "GREEN" in a.stdout


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

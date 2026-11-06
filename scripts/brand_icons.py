#!/usr/bin/env python3
"""Plobi 软件图标唯一生成入口（WP-ICON-FULL-SWEEP）。

真源 = 本文件顶部的图元表 `MARK` / `WORDMARK_*`。SVG 主源与所有位图都从同一份图元生成，
所以不存在"主源画一套、产物另一套"。各档一律在该像素尺寸直接画（4× 超采样后 LANCZOS
收敛），**禁止把大档降采样当小档用**——R-052 那次用 Pillow 存 ICO 就是栽在这：Pillow 会从
最大那张重新降采样出每一档，预先算好的小尺寸简化版被静默丢掉。

用法
    python scripts/brand_icons.py           # 生成 SVG 主源 + 写全部落点
    python scripts/brand_icons.py --check   # 不写盘：重画一遍，与盘上逐字节比对

ICO / ICNS 容器手写，不走 Pillow 的 ICO/ICNS 编码器（理由同上）。
"""
from __future__ import annotations

import argparse
import io
import struct
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SS = 4  # 超采样倍数

# ---- 色：陶土是唯一在浅底 3.36:1 与深底 4.17:1 都过 Fluent 3.0:1 的一支 ----
SHELL = (0xCD, 0x6B, 0x43)
CREAM = (0xFD, 0xED, 0xD2)
INK = (0x24, 0x18, 0x12)

# ---- 图元（64 网格，y 向下）：穹顶甲壳 + 肩头 + 四颗甲缘裙边 ----
MARK = [
    ("halfdisc_up", 36, 42, 22),
    ("disc", 11, 30, 7),
    ("halfdisc_down", 19.5, 42, 5.5),
    ("halfdisc_down", 30.5, 42, 5.5),
    ("halfdisc_down", 41.5, 42, 5.5),
    ("halfdisc_down", 52.5, 42, 5.5),
]
MARK_BBOX = (4.0, 20.0, 54.0, 27.5)  # x0, y0, w, h

# ---- 字标：单线几何 p l o b i；'i' 的点 = 同一只壳 ----
SW = 10
WM_STEMS = [("caps", 20, 36, 20, 92), ("caps", 77, 16, 77, 72),
            ("caps", 151, 16, 151, 72), ("caps", 208, 36, 208, 72)]
WM_BOWLS = [(40, 54), (114, 54), (171, 54)]          # 圆心；外径 20 / 内径 10 = 线宽 10
WM_R_OUT, WM_R_IN = 20, 10
WM_BOX = (10.0, 6.0, 218.0, 98.0)  # 含左右边距；'i' 到 213.4，p 下伸到 97
TITTLE_AT = (208.0, 27.0)                             # 'i' 点的中心（竖笔顶在 y=36）
TITTLE_SCALE = 0.20                                   # 0.20×54=10.8 单位 ≈ 竖笔 10，不超出字身


# ---------------- 图元 -> 路径 / 绘制 ----------------
def _bbox(kind, a, b, c):
    """pieslice 吃的是**完整椭圆**的包围盒，再按角度取一半；给半高框会把形状压成 1/4 高。"""
    return (a - c, b - c, a + c, b + c)


def _svg_path(kind, a, b, c):
    if kind == "disc":
        return f"M {a - c},{b} a {c},{c} 0 1 0 {2 * c},0 a {c},{c} 0 1 0 {-2 * c},0 Z"
    if kind == "halfdisc_up":
        return f"M {a - c},{b} A {c},{c} 0 0 1 {a + c},{b} Z"
    if kind == "halfdisc_down":
        return f"M {a + c},{b} A {c},{c} 0 0 1 {a - c},{b} Z"
    raise ValueError(kind)


def _fit(canvas, bx, by, bw, bh, frac):
    s = canvas * frac / bw
    return s, (canvas - bw * s) / 2 - bx * s, (canvas - bh * s) / 2 - by * s


def _draw_mark(dr, T):
    s, dx, dy = T
    for kind, a, b, c in MARK:
        x0, y0, x1, y1 = _bbox(kind, a, b, c)
        box = [x0 * s + dx, y0 * s + dy, x1 * s + dx, y1 * s + dy]
        if kind == "disc":
            dr.ellipse(box, fill=255)
        elif kind == "halfdisc_up":
            dr.pieslice(box, 180, 360, fill=255)
        else:
            dr.pieslice(box, 0, 180, fill=255)


def mark_mask(px, frac=0.62):
    big = px * SS
    m = Image.new("L", (big, big), 0)
    _draw_mark(ImageDraw.Draw(m), _fit(big, *MARK_BBOX, frac))
    return m.resize((px, px), Image.LANCZOS)


def _solid(size, rgba):
    return Image.new("RGBA", size, rgba)


def tile_image(px, bg=SHELL, fg=CREAM, frac=0.62):
    im = _solid((px, px), bg + (255,))
    im.paste(_solid((px, px), fg + (255,)), (0, 0), mark_mask(px, frac))
    return im


def bare_image(px, color=SHELL, frac=0.86):
    im = _solid((px, px), (0, 0, 0, 0))
    im.paste(_solid((px, px), color + (255,)), (0, 0), mark_mask(px, frac))
    return im


def wordmark_image(width, ink=INK, tittle=SHELL, bg=None):
    bx0, by0, bx1, by1 = WM_BOX
    bw, bh = bx1 - bx0, by1 - by0
    s = width * SS / bw
    W, H = width * SS, int(bh * s)
    dx, dy = -bx0 * s, -by0 * s
    mask = Image.new("L", (W, H), 0)
    dr = ImageDraw.Draw(mask)
    for _, x0, y0, x1, y1 in WM_STEMS:
        w = int(round(SW * s))
        dr.line([x0 * s + dx, y0 * s + dy, x1 * s + dx, y1 * s + dy], fill=255, width=w)
        r = SW * s / 2
        for cx, cy in ((x0, y0), (x1, y1)):
            dr.ellipse([cx * s + dx - r, cy * s + dy - r, cx * s + dx + r, cy * s + dy + r], fill=255)
    for cx, cy in WM_BOWLS:
        ro, ri = WM_R_OUT * s, WM_R_IN * s
        dr.ellipse([cx * s + dx - ro, cy * s + dy - ro, cx * s + dx + ro, cy * s + dy + ro], fill=255)
    for cx, cy in WM_BOWLS:  # 反切出字腔
        ri = WM_R_IN * s
        dr.ellipse([cx * s + dx - ri, cy * s + dy - ri, cx * s + dx + ri, cy * s + dy + ri], fill=0)
    im = _solid((W, H), (0, 0, 0, 0))
    im.paste(_solid((W, H), ink + (255,)), (0, 0), mask)
    # 'i' 的点 = 同一只壳
    tpx = max(int(MARK_BBOX[2] * TITTLE_SCALE * s), 8)
    timg = _solid((tpx, tpx), (0, 0, 0, 0))
    timg.paste(_solid((tpx, tpx), tittle + (255,)), (0, 0), mark_mask(tpx, 1.0))
    ty = int(timg.height * 0.5)
    im.paste(timg, (int((TITTLE_AT[0] - bx0) * s - tpx / 2), int((TITTLE_AT[1] - by0) * s - ty)), timg)
    if bg:
        under = _solid(im.size, bg + (255,))
        under.alpha_composite(im)
        im = under
    return im.resize((width, H // SS), Image.LANCZOS)


# ---------------- SVG 主源（与位图同一份图元） ----------------
def svg_mark(fill):
    d = " ".join(_svg_path(*p) for p in MARK)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64" fill="none">\n'
            f'  <path d="{d}" fill="{fill}" fill-rule="nonzero"/>\n</svg>\n')


def svg_acp():
    """ACP 注册表稿：16 网格、只有 currentColor、无十六进制、无渐变（tests/acp 四条硬判据）。"""
    d = " ".join(_svg_path(k, a / 4, b / 4, c / 4) for k, a, b, c in MARK)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="16" height="16" fill="none">\n'
            f'  <path d="{d}" fill="currentColor" fill-rule="nonzero"/>\n</svg>\n')


def svg_tile(canvas=1024):
    s, tx, ty = _fit(canvas, *MARK_BBOX, 0.62)
    d = " ".join(_svg_path(*p) for p in MARK)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {canvas} {canvas}" width="{canvas}" height="{canvas}">\n'
            f'  <rect width="{canvas}" height="{canvas}" fill="#{hexc(SHELL)}"/>\n'
            f'  <g transform="translate({tx:.2f},{ty:.2f}) scale({s:.6f})">'
            f'<path d="{d}" fill="#{hexc(CREAM)}" fill-rule="nonzero"/></g>\n</svg>\n')


def svg_wordmark(ink, tittle):
    parts = [f'<line x1="{x0}" y1="{y0}" x2="{x1}" y2="{y1}" stroke="{ink}" '
             f'stroke-width="{SW}" stroke-linecap="round"/>' for _, x0, y0, x1, y1 in WM_STEMS]
    parts += [f'<circle cx="{cx}" cy="{cy}" r="{(WM_R_OUT + WM_R_IN) / 2}" fill="none" '
              f'stroke="{ink}" stroke-width="{SW}"/>' for cx, cy in WM_BOWLS]
    d = " ".join(_svg_path(*p) for p in MARK)
    s, tx, ty = _fit(1, *MARK_BBOX, 1.0)
    parts.append(f'<g transform="translate({TITTLE_AT[0]},{TITTLE_AT[1]}) scale({TITTLE_SCALE}) '
                 f'translate({-MARK_BBOX[0] - MARK_BBOX[2] / 2},{-MARK_BBOX[1] - MARK_BBOX[3] / 2})">'
                 f'<path d="{d}" fill="{tittle}"/></g>')
    x0, y0, x1, y1 = WM_BOX
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {x1 - x0} {y1 - y0}" '
            f'width="{x1 - x0}" height="{y1 - y0}" fill="none">\n  <g transform="translate({-x0},{-y0})">\n    '
            + "\n    ".join(parts) + "\n  </g>\n</svg>\n")


def hexc(t):
    return "%02X%02X%02X" % t


# ---------------- 容器：ICO / ICNS 手写 ----------------
def png_bytes(im):
    b = io.BytesIO()
    im.save(b, "PNG", optimize=True)
    return b.getvalue()


def build_ico(sizes):
    """sizes: [(px, Image)] -> ICONDIR + ICONDIRENTRY[] + PNG blob[]，偏移逐条累加。"""
    blobs = [png_bytes(im.convert("RGBA")) for _, im in sizes]
    out = b"\x00\x00\x01\x00" + struct.pack("<H", len(sizes))
    off = 6 + 16 * len(sizes)
    for (px, _), blob in zip(sizes, blobs):
        b = 0 if px >= 256 else px
        out += struct.pack("<BBBBHHII", b, b, 0, 0, 1, 32, len(blob), off)
        off += len(blob)
    return out + b"".join(blobs)


ICNS_TYPES = [("ic04", 16), ("ic11", 32), ("ic05", 32), ("ic12", 64), ("ic07", 128),
              ("ic13", 256), ("ic08", 256), ("ic14", 512), ("ic09", 512), ("ic10", 1024)]


def build_icns(by_px):
    body = b""
    seen = []
    for t, px in ICNS_TYPES:
        if px not in by_px:
            continue
        blob = png_bytes(by_px[px].convert("RGBA"))
        body += t.encode() + struct.pack(">I", len(blob) + 8) + blob
        seen.append(t)
    return b"icns" + struct.pack(">I", 8 + len(body)) + body, seen


# ---------------- 落点与主源 ----------------
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
# 鸿蒙仓与 Code **不同级**：Code 在 Plobi-Desktop 下，鸿蒙在 Plobi 下。
# 上一刀就是把 `../` 数错一级，在 Plobi-Desktop 里凭空建出一棵假树（不在任何 git 仓，
# 等于改了没有历史）。所以这里不拼相对猜测，改成：显式解析 + 父目录不在就**报错不建**。
HARMONY = ROOT.parent.parent / "Plobi-Harmony-App"


def _abs(rel: str) -> Path:
    if rel.startswith("@harmony/"):
        if not (HARMONY / ".git").exists():
            raise FileNotFoundError(
                f"{HARMONY} 不是一个 git 仓（缺 .git）—— 拒绝写入。\n"
                f"  拦的是上一刀那个形状：`../` 数错一级，在 Plobi-Desktop 里 mkdir 出一棵\n"
                f"  假 `Plobi-Harmony-App/`，不在任何仓里，等于改了没有历史。\n"
                f"  仓根不许建；仓**内**缺的那层目录（如 AppScope/resources/base/media）允许补。")
        return HARMONY / rel[len("@harmony/"):]
    return ROOT / rel


def assets():
    pxs = set(ICO_SIZES) | {180, 512, 1024}
    im = {px: tile_image(px) for px in pxs}
    ico = build_ico([(px, im[px]) for px in ICO_SIZES])
    icns, _ = build_icns(im)
    H = {
        "apps/desktop/assets/icon.png": ("b", png_bytes(im[1024])),
        "apps/desktop/assets/icon.ico": ("b", ico),
        "apps/desktop/assets/icon.icns": ("b", icns),
        "apps/desktop/public/apple-touch-icon.png": ("b", png_bytes(im[180])),
        "website/static/img/favicon.ico": ("b", ico),
        "website/static/img/favicon-16x16.png": ("b", png_bytes(im[16])),
        "website/static/img/favicon-32x32.png": ("b", png_bytes(im[32])),
        "website/static/img/apple-touch-icon.png": ("b", png_bytes(im[180])),
        "website/static/img/logo.png": ("b", png_bytes(wordmark_image(760, INK, SHELL))),
        "website/static/img/logo-dark.png": ("b", png_bytes(wordmark_image(760, CREAM, SHELL))),
        "web/public/favicon.ico": ("b", ico),
        "acp_registry/icon.svg": ("t", svg_acp()),
        "@harmony/entry/src/main/resources/base/media/app_icon.png": ("b", png_bytes(im[1024])),
        "@harmony/AppScope/resources/base/media/app_icon.png": ("b", png_bytes(im[1024])),
    }
    masters = {
        "brand/plobi-mark.svg": svg_mark(f"#{hexc(SHELL)}"),
        "brand/plobi-mark-mono.svg": svg_mark("currentColor"),
        "brand/plobi-appicon.svg": svg_tile(),
        "brand/plobi-wordmark.svg": svg_wordmark(f"#{hexc(INK)}", f"#{hexc(SHELL)}"),
        "brand/plobi-wordmark-dark.svg": svg_wordmark(f"#{hexc(CREAM)}", f"#{hexc(SHELL)}"),
    }
    return H, masters


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="不写盘，与盘上逐字节比对")
    a = ap.parse_args()
    H, masters = assets()
    all_files = {**H, **{k: ("t", v) for k, v in masters.items()}}
    if a.check:
        bad = []
        for rel, (kind, data) in all_files.items():
            p = _abs(rel)
            if not p.exists():
                bad.append(f"缺文件：{rel}")
                continue
            cur = p.read_bytes() if kind == "b" else p.read_text(encoding="utf-8")
            want = data if kind == "b" else data
            if cur != want:
                bad.append(f"与主源不一致：{rel}")
        if bad:
            print("\n".join(bad), file=sys.stderr)
            print(f"RED — {len(bad)}/{len(all_files)} 处", file=sys.stderr)
            return 1
        print(f"GREEN — {len(H)} 档落点 + {len(masters)} 份主源，逐字节与图元一致")
        return 0
    for rel, (kind, data) in all_files.items():
        p = _abs(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        if kind == "b":
            p.write_bytes(data)
        else:
            p.write_text(data, encoding="utf-8", newline="\n")
    print(f"wrote {len(H)} 落点 + {len(masters)} 份主源")
    return 0


if __name__ == "__main__":
    sys.exit(main())

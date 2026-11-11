#!/usr/bin/env python3
"""生成 Windows / Linux 的平台专用应用图标。

为什么需要这个脚本：一张 1024 的满幅方图喂给三个平台是行不通的——
  * macOS 的 Dock 把图直接铺满 48pt 方框，必须自带留白与圆角（assets/icon-macos.png，
    824/1024 内容 + 185 圆角 + 轻投影，已入库，本脚本不重新生成它，避免把那版已经
    验收过的外观改掉）；
  * Windows 的窗口/任务栏只吃 .ico，且小尺寸要单独到位，所以从这里出多帧；
  * Linux 走 freedesktop，图要**满幅**（桌面环境自己裁），但不能是直角实色大块，
    所以满幅 + 圆角。

用法：
    python3 scripts/make-platform-icons.py

产物（都写进 assets/，源码态与打包共用）：
    assets/icon.ico         16/24/32/48/64/128/256，满幅 + 圆角（Windows）
    assets/icon-linux.png   1024，满幅 + 圆角（Linux）

母版是 assets/icon.png（1024 满幅直角）。这个文件不覆盖，它还是网页/favicon 那一路
的来源。跑完记得看一眼 16px 那帧——任务栏就这么大。
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFilter
except ImportError:  # pragma: no cover
    sys.exit('需要 Pillow：python3 -m pip install Pillow')

DESKTOP = Path(__file__).resolve().parent.parent
ASSETS = DESKTOP / 'assets'
SOURCE = ASSETS / 'icon.png'

# 圆角半径按母版边长的比例给：22.5% ≈ macOS 那版的 185/824，三个平台视觉同族。
RADIUS_RATIO = 0.225
SS = 4  # 蒙版超采样倍率，抗锯齿靠它

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def rounded(source: Image.Image, radius_ratio: float = RADIUS_RATIO) -> Image.Image:
    """把满幅方图裁成圆角方图（保留原画布尺寸，四角变透明）。"""
    w, h = source.size
    radius = int(round(min(w, h) * radius_ratio))
    # 先在 SS 倍画布上画圆角蒙版，再缩回原尺寸——直接画的话边缘是硬的
    mask = Image.new('L', (w * SS, h * SS), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, w * SS - 1, h * SS - 1), radius=radius * SS, fill=255
    )
    mask = mask.resize((w, h), Image.LANCZOS)

    out = source.convert('RGBA').copy()
    out.putalpha(mask)
    return out


def main() -> int:
    if not SOURCE.exists():
        sys.exit(f'母版不存在：{SOURCE}')

    src = Image.open(SOURCE).convert('RGBA')
    if src.size[0] != src.size[1]:
        sys.exit(f'母版必须是正方形，实际 {src.size}')

    round_src = rounded(src)

    linux_png = ASSETS / 'icon-linux.png'
    round_src.save(linux_png, 'PNG', optimize=True)

    ico = ASSETS / 'icon.ico'
    # Pillow 会按 sizes 从这张 1024 圆角图逐帧 LANCZOS 重采样；≤64 走 DIB、128/256
    # 走 PNG 压缩帧，跟 Windows 自己的规矩一致（resedit 那类工具只认 DIB 帧，所以
    # 小尺寸必须留在 DIB 上）。
    round_src.save(ico, 'ICO', sizes=[(s, s) for s in ICO_SIZES])

    for p in (linux_png, ico):
        print(f'{p.relative_to(DESKTOP)}  {p.stat().st_size} B')

    # 自检：四角必须真的透明了，否则又回到直角大块
    for p in (linux_png, ico):
        im = Image.open(p).convert('RGBA')
        a = im.getchannel('A')
        w, h = im.size
        for corner in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
            if a.getpixel(corner) != 0:
                sys.exit(f'{p.name} 的角 {corner} 不透明（{a.getpixel(corner)}），圆角没生效')

    print('四角 alpha=0 校验通过')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

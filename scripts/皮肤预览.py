#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
皮肤预览器 —— 把 64×64 皮肤渲染成**看得懂的图**，不用开游戏。

两种视图：
  ① 六面展开（atlas）—— 原图放大 + 每个 UV 区域框选标注，查"哪个面画歪了"
  ② 人物三视图      —— 正面 / 侧面 / 背面 三个正交视图并排，查"整体像不像人"

⚠️ 刻意**不做透视/等距渲染**。第一版用等距投影 + 仿射贴图，结果人形只画出
   9 个像素（公式把高度和深度混进同一项，立方体退化成线）。正交三视图用矩形
   拼接就能保证正确，且对"配色对不对、配件贴在哪、比例顺不顺眼"这些**验收目的**
   表达力更强 —— 形状问题摊成图才看得见，但摊图的方式必须是**我能算对的**那种。

用法：
  python 皮肤预览.py skin.png -o preview.png
  python 皮肤预览.py skin.png -o preview.png --scale 6
  python 皮肤预览.py skin.png -o atlas.png --atlas-only
  python 皮肤预览.py --selftest
"""
from __future__ import annotations

import argparse
import os
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import 皮肤规范 as reg


def crop_face(img, part, face, layer="base"):
    table = reg.PART_FACES if layer == "base" else reg.PART_FACES_OVERLAY
    x, y, w, h = table[part][face]
    return img.crop((x, y, x + w, y + h))


# ══════════════════════════════════════════════════════════════
# 三视图组装
# ══════════════════════════════════════════════════════════════

# 各视图里每个部件贴哪个面
VIEW_FACES = {
    "front": {"head": "front", "body": "front", "right_arm": "front",
              "left_arm": "front", "right_leg": "front", "left_leg": "front"},
    "back":  {"head": "back", "body": "back", "right_arm": "back",
              "left_arm": "back", "right_leg": "back", "left_leg": "back"},
    "side":  {"head": "right", "body": "right", "right_arm": "right",
              "left_arm": "right", "right_leg": "right", "left_leg": "right"},
}

# 每个视图里的部件排布（x 偏移格, y 偏移格, 宽, 高），以"头顶为 y=0"
# 玩家 8+12+12 = 32 格高；正面总宽 = 8(躯干) + 4 + 4 = 16 格
LAYOUT = {
    "front": [
        # (部件, dx, dy)  —— dx 相对人体中线左边缘
        ("head", 4, 0),
        ("body", 4, 8),
        ("left_arm", 0, 8),      # 玩家左臂在**画面右侧**
        ("right_arm", 12, 8),
        ("left_leg", 4, 20),
        ("right_leg", 8, 20),
    ],
    "back": [
        ("head", 4, 0),
        ("body", 4, 8),
        ("right_arm", 0, 8),
        ("left_arm", 12, 8),
        ("right_leg", 4, 20),
        ("left_leg", 8, 20),
    ],
    "side": [
        ("head", 2, 0),
        ("body", 4, 8),
        ("left_arm", 2, 8),      # 侧视图手臂贴在躯干侧面
        ("left_leg", 4, 20),
        ("right_leg", 6, 20),
    ],
}

VIEW_W = {"front": 16, "back": 16, "side": 8}
VIEW_H = 32


def render_view(img, view, scale):
    W = VIEW_W[view] * scale
    H = VIEW_H * scale
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for part, dx, dy in LAYOUT[view]:
        face = VIEW_FACES[view].get(part)
        if face is None:
            continue
        tex = crop_face(img, part, face).resize(
            (crop_face(img, part, face).width * scale,
             crop_face(img, part, face).height * scale), Image.NEAREST)
        out.alpha_composite(tex, (dx * scale, dy * scale))
    # 外层叠上去（同位置，只要不透明就盖住）
    for part, dx, dy in LAYOUT[view]:
        face = VIEW_FACES[view].get(part)
        if face is None:
            continue
        ov = crop_face(img, part, face, layer="overlay")
        if ov.getbbox() is None:
            continue
        ovs = ov.resize((ov.width * scale, ov.height * scale), Image.NEAREST)
        out.alpha_composite(ovs, (dx * scale, dy * scale))
    return out


def render_character(img, scale=6):
    """正面 / 侧面 / 背面 三视图并排。"""
    views = [render_view(img, v, scale) for v in ("front", "side", "back")]
    gap = 2 * scale
    W = sum(v.width for v in views) + gap * (len(views) - 1) + gap * 2
    H = views[0].height + gap * 2
    canvas = Image.new("RGBA", (W, H), (26, 28, 34, 255))
    x = gap
    for i, v in enumerate(views):
        canvas.alpha_composite(v, (x, gap))
        x += v.width + gap
    return canvas


# ══════════════════════════════════════════════════════════════
# 六面展开
# ══════════════════════════════════════════════════════════════

PART_COLORS = {
    "head": (255, 90, 90), "body": (90, 255, 140), "right_arm": (90, 170, 255),
    "left_arm": (255, 220, 90), "right_leg": (255, 140, 255),
    "left_leg": (140, 255, 255),
}


def render_atlas(img, scale=8):
    """原图放大 + 每个部件框选标注（实线=基础层，虚线感=外层）。"""
    S = 64 * scale
    dst = Image.new("RGBA", (S, S), (20, 21, 26, 255))
    dst.alpha_composite(img.resize((S, S), Image.NEAREST))
    d = ImageDraw.Draw(dst)
    for part, faces in reg.PART_FACES.items():
        for face, (x, y, w, h) in faces.items():
            d.rectangle([x * scale, y * scale, (x + w) * scale - 1, (y + h) * scale - 1],
                        outline=PART_COLORS[part] + (210,), width=max(1, scale // 4))
    for part, faces in reg.PART_FACES_OVERLAY.items():
        for face, (x, y, w, h) in faces.items():
            d.rectangle([x * scale, y * scale, (x + w) * scale - 1, (y + h) * scale - 1],
                        outline=PART_COLORS[part] + (80,), width=max(1, scale // 8))
    return dst


def render(img, out_path, model=None, scale=8, atlas_only=False):
    atlas = render_atlas(img, scale=scale)
    if atlas_only:
        atlas.save(out_path)
        return out_path
    char = render_character(img, scale=scale)
    gap = 16
    W = atlas.width + gap + char.width
    H = max(atlas.height, char.height) + gap * 2
    canvas = Image.new("RGBA", (W, H), (16, 17, 21, 255))
    canvas.alpha_composite(atlas, (0, (H - atlas.height) // 2))
    canvas.alpha_composite(char, (atlas.width + gap, (H - char.height) // 2))
    canvas.save(out_path)
    return out_path


def selftest():
    import importlib
    gen = importlib.import_module("皮肤生成")

    class A:
        preset = "少女"; model = None
        skin_tone = hair_color = hair = eyes = shirt = pants = shoe = belt = None
        metal = accessories = jacket_color = scarf_color = hat_color = None
        cape_color = glasses_color = None
        seed = 42

    p = gen.resolve(A())
    skin = gen.generate(p)
    out = os.path.join(os.getcwd(), "_selftest_preview.png")
    render(skin, out, scale=5)
    ok = os.path.exists(out) and os.path.getsize(out) > 3000
    # 再断言：人形画布不能几乎全空（第一版就是栽在这）
    ch = render_character(skin, scale=5)
    bg = (26, 28, 34, 255)
    px = ch.load()
    n = sum(1 for y in range(ch.height) for x in range(ch.width) if px[x, y] != bg)
    ratio = n / (ch.width * ch.height)
    if ratio < 0.10:
        ok = False
    print(f"  预览自检：{'通过' if ok else '失败'}  "
          f"（出图 {os.path.getsize(out)} 字节，人形覆盖率 {ratio*100:.1f}%）")
    return ok


def main():
    ap = argparse.ArgumentParser(description="把皮肤渲染成看得懂的预览图")
    ap.add_argument("skin", nargs="?", help="64×64 皮肤 PNG")
    ap.add_argument("-o", "--out")
    ap.add_argument("--scale", type=int, default=8)
    ap.add_argument("--atlas-only", action="store_true")
    ap.add_argument("--model")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return 0 if selftest() else 1
    if not args.skin:
        ap.error("需要给一个皮肤 PNG（或用 --selftest）")
    if not args.out:
        args.out = os.path.join(os.getcwd(), "preview.png")

    img = Image.open(args.skin).convert("RGBA")
    if img.size != (64, 64):
        print(f"⚠️ 尺寸 {img.size} 不是 64×64 —— 预览结果不可靠", file=sys.stderr)
    out = render(img, args.out, model=args.model or reg.detect_model(img),
                 scale=args.scale, atlas_only=args.atlas_only)
    s = Image.open(out).size
    print(f"预览图：{out}  ({s[0]}×{s[1]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

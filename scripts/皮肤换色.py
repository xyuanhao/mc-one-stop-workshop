#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
皮肤换色 —— 已有皮肤 → 换掉指定部位的配色，出一整套变体。

和 `纹理变色.py` 的关系：那个按**整张图的亮度梯度**换色，适合物品贴图；
皮肤必须**按部位分别换**（头发不能跟着衣服一起变），所以单独一个工具。

换色三种模式：
  ramp   源图该部位的亮度 → 目标色的暗→亮梯度（保留明暗骨架与噪点）
  hue    只旋转色相、按比例缩放饱和度，明度完全保留（适合保留原皮肤的打光）
  tint   目标色以乘法叠加在原色上（最轻，适合微调色偏）

部位划分是靠 **UV 区域**判定"这个像素属于哪个部件"，而不是靠颜色相近 —— 
所以换色是精确的，不会把脸和衣服混在一起。

用法：
  python 皮肤换色.py base.png --part shirt --color "#B03030" -o out.png
  python 皮肤换色.py base.png --preset 冬季 -o out/
  python 皮肤换色.py base.png --part hair --colors "#E8C84A,#7A4A9E" --names 金,紫 -o out/
  python 皮肤换色.py --list
"""
from __future__ import annotations

import argparse
import colorsys
import os
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import 皮肤规范 as reg


# ── 部位 → 覆盖哪些 UV 区域（部件 × 层）──────────────────────────
# 部件级划分做不到"头发 vs 脸"（都在 head overlay/base），所以再拆：
#   head_base  = 脸/皮肤
#   head_overlay = 头发/帽子
PART_REGIONS = {
    "skin":    [("head", "base"), ("body", "base"), ("right_arm", "base"),
                ("left_arm", "base"), ("right_leg", "base"), ("left_leg", "base")],
    "hair":    [("head", "overlay")],
    "shirt":   [("body", "base")],
    "sleeve":  [("right_arm", "base"), ("left_arm", "base")],
    "pants":   [("right_leg", "base"), ("left_leg", "base")],
    "jacket":  [("body", "overlay"), ("right_arm", "overlay"), ("left_arm", "overlay")],
    "hat":     [("head", "overlay")],
    "shoes":   [("right_leg", "base"), ("left_leg", "base")],
    "overlay": [("head", "overlay"), ("body", "overlay"), ("right_arm", "overlay"),
                ("left_arm", "overlay"), ("right_leg", "overlay"), ("left_leg", "overlay")],
}

PRESETS = {
    "冬季": {"shirt": "#3A5A80", "sleeve": "#3A5A80", "pants": "#2A3448", "hair": "#D8D8E0"},
    "烈焰": {"shirt": "#B03030", "sleeve": "#B03030", "pants": "#4A2018", "hair": "#E8602A"},
    "森林": {"shirt": "#3A6A3A", "sleeve": "#3A6A3A", "pants": "#2A3A24", "hair": "#5A3A22"},
    "暗夜": {"shirt": "#2A2A38", "sleeve": "#2A2A38", "pants": "#1A1A24", "hair": "#14141C"},
    "奶油": {"shirt": "#F0E0C0", "sleeve": "#F0E0C0", "pants": "#C0A880", "hair": "#8A6A4A"},
}


def hx(s, named=None):
    s = str(s).strip()
    if named and s in named:
        s = named[s]
    if s.startswith("#"):
        s = s[1:]
    if "," in s:
        v = [int(x) for x in s.split(",")]
        return (v[0], v[1], v[2], v[3] if len(v) > 3 else 255)
    if len(s) == 8:
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4, 6))
    if len(s) == 6:
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4)) + (255,)
    raise ValueError(f"颜色格式不对：{s!r}")


def region_mask(img, parts):
    """返回 set，含所有属于这些部位的像素坐标。"""
    mask = set()
    for p in parts:
        for part, layer in PART_REGIONS[p]:
            table = reg.PART_FACES if layer == "base" else reg.PART_FACES_OVERLAY
            for face, (x, y, w, h) in table[part].items():
                for dy in range(h):
                    for dx in range(w):
                        mask.add((x + dx, y + dy))
    return mask


def recolor(img, parts, target, mode="ramp"):
    """把 mask 内所有不透明像素换成 target 色系。"""
    out = img.convert("RGBA").copy()
    px = out.load()
    mask = region_mask(img, parts)
    if not mask:
        return out

    # ramp 模式：用该部位自身的亮度分位做基准，保证对比拉满
    lum_vals = []
    for (x, y) in mask:
        c = px[x, y]
        if c[3] > 0:
            lum_vals.append(_lum(c))
    if not lum_vals:
        return out
    lum_vals.sort()
    lo = lum_vals[int(len(lum_vals) * 0.02)]
    hi = lum_vals[int(len(lum_vals) * 0.98)] or 1.0

    tr, tg, tb, _ = target
    dark = (tr * 0.35, tg * 0.35, tb * 0.35)
    light = (min(255, tr * 1.35), min(255, tg * 1.35), min(255, tb * 1.35))

    for (x, y) in mask:
        r, g, b, a = px[x, y]
        if a == 0:
            continue
        if mode == "ramp":
            t = (_lum((r, g, b)) - lo) / max(1e-6, hi - lo)
            t = min(1.0, max(0.0, t))
            nr = dark[0] + (light[0] - dark[0]) * t
            ng = dark[1] + (light[1] - dark[1]) * t
            nb = dark[2] + (light[2] - dark[2]) * t
        elif mode == "hue":
            h0, s0, v0 = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            h1, s1, _ = colorsys.rgb_to_hsv(tr / 255, tg / 255, tb / 255)
            nr, ng, nb = colorsys.hsv_to_rgb(h1, min(1.0, s0 * (s1 / max(0.05, s0)) if s0 > 0.02 else s1), v0)
            nr, ng, nb = nr * 255, ng * 255, nb * 255
        elif mode == "tint":
            nr, ng, nb = r * (tr / 255) * 1.6, g * (tg / 255) * 1.6, b * (tb / 255) * 1.6
        else:
            raise ValueError(mode)
        px[x, y] = (int(min(255, max(0, nr))), int(min(255, max(0, ng))),
                    int(min(255, max(0, nb))), a)
    return out


def _lum(c):
    r, g, b = c[0], c[1], c[2]
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0


def main():
    ap = argparse.ArgumentParser(description="给已有皮肤换配色",
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split("用法：")[-1])
    ap.add_argument("skin", nargs="?", help="源皮肤 PNG")
    ap.add_argument("--part", action="append",
                    help=f"要换的部位，可重复：{','.join(PART_REGIONS)}")
    ap.add_argument("--color", help="目标色 #RRGGBB")
    ap.add_argument("--colors", help="一组目标色，逗号分隔（配合 --names 批量出）")
    ap.add_argument("--names", help="与 --colors 对应的名字，逗号分隔")
    ap.add_argument("--preset", help=f"一次性换多个部位：{', '.join(PRESETS)}")
    ap.add_argument("--mode", choices=["ramp", "hue", "tint"], default="ramp")
    ap.add_argument("-o", "--out", help="输出文件或目录")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list:
        print("可换的部位：")
        for k, v in PART_REGIONS.items():
            print(f"  {k:9s} 覆盖 {len(v)} 个部件层")
        print(f"\n内置配色预设：{', '.join(PRESETS)}")
        print("模式：ramp（亮度梯度，对比强）/ hue（只转色相）/ tint（乘法叠加）")
        return 0

    if not args.skin:
        ap.error("需要源皮肤 PNG")

    img = Image.open(args.skin).convert("RGBA")
    if img.size != (64, 64):
        print(f"⚠️ 源图 {img.size} 不是 64×64，结果可能不对", file=sys.stderr)

    out_spec = args.out or os.getcwd()

    # 预设：多个部位一起换 → 出单张
    if args.preset:
        if args.preset not in PRESETS:
            ap.error(f"没有预设 {args.preset}；可选 {', '.join(PRESETS)}")
        cur = img
        for part, color in PRESETS[args.preset].items():
            cur = recolor(cur, [part], hx(color), mode=args.mode)
        dst = _resolve_out(out_spec, f"{Path(args.skin).stem}_{args.preset}.png")
        cur.save(dst)
        print(f"{Path(args.skin).name} + 预设「{args.preset}」→ {dst}")
        return 0

    # 批量：一组目标色出多张
    if args.colors:
        colors = [hx(c) for c in args.colors.split(",")]
        names = args.names.split(",") if args.names else [f"v{i+1}" for i in range(len(colors))]
        if len(names) != len(colors):
            ap.error("--names 个数必须和 --colors 一致")
        parts = args.part or ["shirt"]
        for color, name in zip(colors, names):
            res = recolor(img, parts, color, mode=args.mode)
            dst = _resolve_out(out_spec, f"{Path(args.skin).stem}_{name}.png")
            res.save(dst)
            print(f"  {name:8s} {dst}")
        return 0

    # 单个部位单色
    if not args.part or not args.color:
        ap.error("需要 --part 和 --color（或用 --preset / --colors）")
    res = recolor(img, args.part, hx(args.color), mode=args.mode)
    dst = _resolve_out(out_spec, f"{Path(args.skin).stem}_{'_'.join(args.part)}.png")
    res.save(dst)
    print(f"换色完成：{dst}")
    return 0


def _resolve_out(spec, filename):
    p = Path(spec)
    if p.suffix.lower() == ".png":
        p.parent.mkdir(parents=True, exist_ok=True)
        return str(p)
    p.mkdir(parents=True, exist_ok=True)
    return str(p / filename)


if __name__ == "__main__":
    sys.exit(main())

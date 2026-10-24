#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
像素纹理生成器：程序化直接生成 Minecraft 16×16 / 32×32 方块纹理。

程序化直接逐像素算，不走 AI：
- 颜色可控（指定基色/斑色/高光）
- 无缝天然成立（噪声算法周期性，四边必然能平铺）

算法：value noise 有机噪点 → 菌斑/颗粒 → MC 顶光（左上亮右下暗）→ 可选调色板。

用法:
  python 像素纹理生成.py --preset mycelium --seed 42 -o mycelium.png
  python 像素纹理生成.py --preset stone_brick --size 32 -o stone32.png
  python 像素纹理生成.py --preset dirt --base 100,76,52 --spot 60,44,28 -o dirt.png

内置预设：
  mycelium   菌丝块（灰白底+蓝绿斑）       mushroom   蘑菇块（棕底+白点/红点）
  dirt       泥土（棕底+深斑）             grass      草地块（顶绿侧棕需组合）
  stone      石头（灰底+深灰斑）           stone_brick 石砖（砖缝网格）
  obsidian   黑曜石（深紫黑+亮紫斑）       sand       沙子（浅黄+噪点）
  netherrack 下界岩（红棕+深浅斑）         glowstone  萤石（黄底+白黄斑）
"""
import argparse, os, random, sys
from PIL import Image


def value_noise_2d(size, scale, seed):
    """周期性 value noise：输出 size×size 0~1 的二维数组，四边无缝（用模索引）。"""
    rnd = random.Random(seed)
    gs = max(2, size // scale)          # 网格粒度
    grid = [[rnd.random() for _ in range(gs + 1)] for _ in range(gs + 1)]
    out = [[0.0] * size for _ in range(size)]
    for y in range(size):
        for x in range(size):
            gx = x * gs / size
            gy = y * gs / size
            ix, iy = int(gx), int(gy)
            fx, fy = gx - ix, gy - iy
            # 平滑插值
            u = fx * fx * (3 - 2 * fx)
            v = fy * fy * (3 - 2 * fy)
            def g(xx, yy):
                return grid[yy % gs][xx % gs]   # 模索引 → 无缝
            a = g(ix, iy) + (g(ix + 1, iy) - g(ix, iy)) * u
            b = g(ix, iy + 1) + (g(ix + 1, iy + 1) - g(ix, iy + 1)) * u
            out[y][x] = a + (b - a) * v
    return out


def lerp_color(c1, c2, t):
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def add_toplight(px, x, y, size, strength=0.18):
    """MC 顶光惯例：左上亮、右下暗，制造立体感。"""
    # 归一化：左上(0,0)=+strength，右下(1,1)=-strength
    t = ((size - x) + (size - y)) / (2.0 * size) * 2 - 1   # -1(右下) ~ +1(左上)
    f = 1.0 + t * strength
    return tuple(max(0, min(255, int(c * f))) for c in px)


def quantize(px, colors):
    """近似调色板量化（通道向最近档位收拢，产生像素画质感）。"""
    if colors <= 0:
        return px
    step = 255 // (colors - 1)
    return tuple(int(round(c / step) * step) for c in px)


PRESETS = {
    "mycelium":   dict(base=(148, 148, 148), spot=(110, 138, 150), dark=(90, 90, 95),  noise_scale=3, spots=0.20, brick=None),
    "mushroom":   dict(base=(168, 128, 92),  spot=(200, 170, 130), dark=(120, 85, 60),  noise_scale=2, spots=0.35, brick=None),
    "dirt":       dict(base=(134, 96, 67),   spot=(100, 70, 48),   dark=(80, 55, 36),   noise_scale=4, spots=0.45, brick=None),
    "grass_top":  dict(base=(106, 170, 64),  spot=(125, 190, 80),  dark=(85, 135, 50),  noise_scale=3, spots=0.30, brick=None),
    "stone":      dict(base=(125, 125, 125), spot=(105, 105, 110), dark=(90, 90, 95),   noise_scale=4, spots=0.35, brick=None),
    "stone_brick":dict(base=(125, 125, 125), spot=(110, 110, 115), dark=(70, 70, 75),   noise_scale=3, spots=0.25, brick=(6, 6)),
    "obsidian":   dict(base=(25, 20, 40),    spot=(60, 45, 100),   dark=(10, 8, 20),    noise_scale=3, spots=0.15, brick=None),
    "sand":       dict(base=(219, 207, 163), spot=(200, 185, 140), dark=(180, 165, 125), noise_scale=5, spots=0.40, brick=None),
    "netherrack": dict(base=(110, 45, 45),   spot=(135, 60, 50),   dark=(85, 32, 32),   noise_scale=3, spots=0.40, brick=None),
    "glowstone":  dict(base=(205, 168, 96),  spot=(235, 215, 160), dark=(170, 130, 60), noise_scale=2, spots=0.50, brick=None),
}

def generate(preset_name, size=16, seed=0, colors=0, custom=None):
    if custom:
        p = custom
    else:
        p = PRESETS[preset_name]
    base, spot, dark = p["base"], p["spot"], p["dark"]
    noise_scale = p["noise_scale"]
    spots = p["spots"]
    brick = p.get("brick")

    n1 = value_noise_2d(size, noise_scale, seed)
    n2 = value_noise_2d(size, max(2, noise_scale // 2), seed + 9999)   # 大尺度色块
    rnd = random.Random(seed + 12345)

    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            v = n1[y][x]
            w = n2[y][x]
            # 基底色 + 大尺度明暗
            c = lerp_color(dark, base, 0.25 + 0.75 * w)
            # 小尺度噪点抖动
            c = lerp_color(c, spot if v > 0.55 else dark, abs(v - 0.5) * 1.2 if v > 0.5 else abs(v - 0.5) * 0.8)
            # 随机菌斑/颗粒点
            if rnd.random() < spots * 0.5 and v > 0.35:
                c = lerp_color(c, spot, 0.5)
            # 砖缝（可选）
            if brick:
                bw, bh = brick
                if x % (size // bw) == 0 or y % (size // bh) == 0 or (y // (size // bh)) % 2 and x % (size // bw) == (size // bw) // 2:
                    c = lerp_color(c, (0, 0, 0), 0.6)
            # MC 顶光 + 量化
            c = add_toplight(c, x, y, size)
            c = quantize(c, colors) if colors else c
            px[x, y] = c
    return img


def upscale(img, scale):
    from PIL import Image as I
    return img.resize((img.width * scale, img.height * scale), I.NEAREST)


def main():
    ap = argparse.ArgumentParser(description="程序化 MC 像素纹理")
    ap.add_argument("--preset", default="mycelium", help="预设: " + "/".join(PRESETS))
    ap.add_argument("--size", type=int, default=16)
    ap.add_argument("--seed", type=int, default=0, help="随机种子（同种子同结果）")
    ap.add_argument("--colors", type=int, default=0, help="量化色阶数 0=不量化")
    ap.add_argument("--base", default=None, help="自定义基色 如 100,76,52")
    ap.add_argument("--spot", default=None, help="自定义斑色")
    ap.add_argument("--dark", default=None, help="自定义暗色")
    ap.add_argument("-o", "--out", default=None,
                    help="输出 PNG 路径（不传则写当前工作目录 <preset>_<size>px.png）")
    args = ap.parse_args()

    if args.preset not in PRESETS and not args.base:
        print(f"[错误] 未知预设 {args.preset}，可用: {list(PRESETS)}")
        sys.exit(1)

    custom = None
    if args.base:
        def cv(s):
            return tuple(int(x) for x in s.split(","))
        custom = dict(
            base=cv(args.base),
            spot=cv(args.spot or args.base),
            dark=cv(args.dark or args.base),
            noise_scale=3, spots=0.3, brick=None)
        if args.preset in PRESETS:
            custom.update(PRESETS[args.preset])

    img = generate(args.preset, args.size, args.seed, args.colors, custom)
    if not args.out:
        args.out = f"{args.preset}_{args.size}px.png"
    img.save(args.out)
    print(f"输出: {args.out}  ({img.width}x{img.height})  seed={args.seed}")
    # 预览放大
    prev = args.out.replace(".png", "_previewx8.png")
    upscale(img, 8).save(prev)
    print(f"预览: {prev}")
    # 平铺自检
    tile = Image.new("RGB", (size := img.width * 3, img.height * 3))
    for i in range(3):
        for j in range(3):
            tile.paste(img, (i * img.width, j * img.height))
    tp = args.out.replace(".png", "_tile3x3.png")
    upscale(tile, 4).save(tp)
    print(f"平铺自检: {tp}")


if __name__ == "__main__":
    main()

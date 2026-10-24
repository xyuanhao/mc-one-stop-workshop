#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
纹理变色器 —— 形状不动，只换颜色。

专治"一套形状、只差颜色"的物品：盔甲 / 木头全套（门·活板门·栅栏门·木板）/ 锻造模板 /
染料色系 / 金属宝石。给一张基础纹理，一次出满一整套变体。

原理：拿源图的**明暗骨架**映射到目标色的色阶梯度上 ——
亮暗结构、噪点细节、透明镂空(alpha) 全部原样保留，只有色相与饱和度被替换。
基准用源图自身亮度的 2%~98% 分位，所以源图整体偏亮/偏暗也能拉满对比。
（想直接从零造纹理用 `像素纹理生成.py`；本工具管"已有形状 → 换色成一套"。）

用法:
  python 纹理变色.py --list-presets
  python 纹理变色.py oak_door.png --preset woods --prefix door -o out/
  python 纹理变色.py base_armor.png --colors "#B8945F,#7A5A32" --names oak,spruce -o out/
  python 纹理变色.py base.png --preset dyes --mode hue --levels 4 -o out/
  python 纹理变色.py --demo -o out/          # 没源图时先生成一张示例底图看效果

模式:
  ramp（默认）  源图亮度 → 目标色的暗→亮梯度。适合灰度底图或单色底图换整套色系
  hue           只旋转色相、按比例缩放饱和度，原图明度**完全保留**。适合彩色图换色系
"""
import argparse
import colorsys
import sys
from pathlib import Path

from PIL import Image

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

NEAREST = getattr(Image, "Resampling", Image).NEAREST

# ============================ 内置色板 ============================
# woods / copper 两组是**从 26.2 原版门贴图实测采样**出来的真实中调色
# （复现：原版资源提取.py --extract-pattern "textures/item/*_door.png" → 采样取亮度中位）
PRESET_PALETTES = {
    "woods": [
        ("oak",       (154, 128, 77)),   # #9A804D
        ("spruce",    (98, 80, 54)),     # #625036
        ("birch",     (215, 205, 152)),  # #D7CD98
        ("jungle",    (168, 121, 85)),   # #A87955
        ("acacia",    (177, 102, 64)),   # #B16640
        ("dark_oak",  (71, 46, 23)),     # #472E17
        ("mangrove",  (116, 52, 50)),    # #743432
        ("cherry",    (225, 169, 163)),  # #E1A9A3
        ("bamboo",    (187, 170, 74)),   # #BBAA4A
        ("crimson",   (128, 61, 87)),    # #803D57
        ("warped",    (60, 138, 137)),   # #3C8A89
        ("pale_oak",  (199, 185, 184)),  # #C7B9B8（1.21.4+ 苍白橡木）
    ],
    "copper": [
        ("copper",    (200, 112, 81)),   # #C87051 未氧化
        ("exposed",   (154, 127, 102)),  # #9A7F66 斑驳
        ("weathered", (109, 153, 101)),  # #6D9965 风化
        ("oxidized",  (81, 159, 124)),   # #519F7C 氧化
    ],
    "dyes": [
        ("white",      (249, 255, 254)),
        ("orange",     (249, 128, 29)),
        ("magenta",    (199, 78, 189)),
        ("light_blue", (58, 179, 218)),
        ("yellow",     (254, 216, 61)),
        ("lime",       (128, 199, 31)),
        ("pink",       (243, 139, 170)),
        ("gray",       (71, 79, 82)),
        ("light_gray", (157, 157, 151)),
        ("cyan",       (21, 137, 145)),
        ("purple",     (121, 42, 172)),
        ("blue",       (53, 57, 157)),
        ("brown",      (114, 71, 40)),
        ("green",      (93, 124, 21)),
        ("red",        (176, 46, 38)),
        ("black",      (29, 29, 33)),
    ],
    "metals": [
        ("iron",       (216, 216, 216)),
        ("gold",       (252, 238, 75)),
        ("copper",     (192, 107, 79)),
        ("netherite",  (68, 60, 62)),
        ("diamond",    (93, 236, 231)),
        ("emerald",    (23, 213, 105)),
        ("lapis",      (38, 97, 156)),
        ("redstone",   (170, 26, 9)),
        ("quartz",     (235, 229, 222)),
        ("amethyst",   (154, 96, 213)),
    ],
    "stones": [
        ("stone",      (125, 125, 125)),
        ("granite",    (154, 110, 92)),
        ("diorite",    (207, 207, 208)),
        ("andesite",   (136, 136, 137)),
        ("deepslate",  (80, 80, 86)),
        ("blackstone", (48, 39, 44)),
        ("sandstone",  (216, 203, 155)),
        ("red_sand",   (190, 102, 33)),
        ("prismarine", (99, 156, 151)),
        ("end_stone",  (219, 222, 158)),
    ],
}
PRESET_DESC = {
    "woods": "木头 12 种（含 1.21.4+ 苍白橡木；门/活板门/栅栏门/木板通用）",
    "copper": "铜的 4 个氧化阶段（1.21.5+ 铜制物品变体）",
    "dyes": "染料 16 色（羊毛/地毯/床/旗帜）",
    "metals": "金属与宝石 10 种（盔甲/工具/方块）",
    "stones": "石料 10 种（建材变体）",
}


# ============================ 颜色工具 ============================
def hex2rgb(s):
    s = s.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        raise ValueError(f"颜色格式应为 #RRGGBB 或 RRGGBB，收到 {s!r}")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def rgb_to_hsv(rgb):
    r, g, b = (c / 255.0 for c in rgb)
    return colorsys.rgb_to_hsv(r, g, b)


def hsv_to_rgb(h, s, v):
    r, g, b = colorsys.hsv_to_rgb(h, max(0.0, min(1.0, s)), max(0.0, min(1.0, v)))
    return (int(round(r * 255)), int(round(g * 255)), int(round(b * 255)))


def lerp(c1, c2, t):
    return tuple(int(round(c1[i] + (c2[i] - c1[i]) * t)) for i in range(3))


def luma(rgb):
    """感知亮度 Rec.601。"""
    return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]


def make_ramp(base_rgb, levels=0, dark_k=0.55, light_k=1.45):
    """由基色生成"暗 → 亮"色阶梯度。
    levels<=1 时返回连续三档（暗/中/亮）供插值；levels>=2 时返回该档数的离散色板。
    亮端去饱和、暗端略增饱和 —— 这是 MC 材质明暗过渡的惯例，能让立体感自然。"""
    h, s, v = rgb_to_hsv(base_rgb)
    dv = max(0.10, v * dark_k)
    lv = min(1.0, v * light_k + 0.06)
    if lv <= dv + 0.12:                       # 极暗色兜底，保证有可见层次
        lv = min(1.0, dv + 0.18)
    n = levels if levels >= 2 else 3
    ramp = []
    for i in range(n):
        t = i / (n - 1) if n > 1 else 0.5
        vv = dv + (lv - dv) * t
        ss = s * (1.20 - 0.45 * t)
        ramp.append(hsv_to_rgb(h, ss, vv))
    return ramp


def sample_ramp(ramp, t):
    """在色阶梯度上按 t∈[0,1] 取色（阶之间线性插值）。"""
    if len(ramp) == 1:
        return ramp[0]
    t = max(0.0, min(1.0, t))
    pos = t * (len(ramp) - 1)
    i = int(pos)
    if i >= len(ramp) - 1:
        return ramp[-1]
    return lerp(ramp[i], ramp[i + 1], pos - i)


# ============================ 换色核心 ============================
def is_wood_pixel(r, g, b):
    """工具手柄的棕色：R>G>B 且红通道明显高于蓝（金属/石头部分 R≈G≈B，不会命中）。"""
    return r > g > b and (r - b) > 25 and 30 < r < 215


def recolor(img, base_rgb, levels=0, contrast=1.0, gamma=1.0,
            lo_pct=0.02, hi_pct=0.98, alpha_cut=8, keep_wood=False):
    """ramp 模式：源图明暗骨架 → 目标色梯度。形状、alpha 完全保留。

    keep_wood=True 时保留木质手柄的颜色 —— 原版所有材质的工具（木/石/铜/铁/金/钻石）
    手柄都是同一个木色，只有头部换材质色；整体变色会做出"蓝柄剑""红柄镐"这种怪东西。"""
    src = img.convert("RGBA")
    w, h = src.size
    px = src.load()

    # 1) 收集不透明像素亮度，用分位数定基准（抗极值，源图偏亮偏暗都能拉满）
    lums = []
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a > alpha_cut:
                lums.append(luma((r, g, b)))
    if not lums:
        return src.copy()
    lums.sort()
    lo = lums[min(len(lums) - 1, int(len(lums) * lo_pct))]
    hi = lums[min(len(lums) - 1, int(len(lums) * hi_pct))]
    span = max(1.0, hi - lo)

    ramp = make_ramp(base_rgb, levels)
    out = Image.new("RGBA", (w, h))
    opx = out.load()
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a <= alpha_cut:
                opx[x, y] = (0, 0, 0, 0)      # 镂空/半透明原样保留
                continue
            if keep_wood and is_wood_pixel(r, g, b):
                opx[x, y] = (r, g, b, a)      # 木质手柄原色保留
                continue
            t = (luma((r, g, b)) - lo) / span
            t = max(0.0, min(1.0, t))
            if gamma != 1.0:
                t = t ** gamma
            if contrast != 1.0:
                t = max(0.0, min(1.0, 0.5 + (t - 0.5) * contrast))
            opx[x, y] = (*sample_ramp(ramp, t), a)
    return out


def recolor_hue(img, base_rgb, sat_k=None, strength=1.0, alpha_cut=8):
    """hue 模式：只把色相转到目标色相，明度**逐像素保留**。
    适合"红色盔甲 → 蓝色盔甲"这类彩色图换色系（不重排明暗层级）。"""
    src = img.convert("RGBA")
    w, h = src.size
    px = src.load()
    th, ts, _ = rgb_to_hsv(base_rgb)
    # 源图平均饱和度 → 决定饱和度缩放比（让目标色的浓郁程度落地）
    ss = []
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a > alpha_cut:
                ss.append(rgb_to_hsv((r, g, b))[1])
    avg_s = (sum(ss) / len(ss)) if ss else 0.0
    k = sat_k if sat_k else min(3.0, max(0.25, ts / max(0.08, avg_s)))
    out = Image.new("RGBA", (w, h))
    opx = out.load()
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a <= alpha_cut:
                opx[x, y] = (0, 0, 0, 0)
                continue
            hh, s, v = rgb_to_hsv((r, g, b))
            nh = hh + ((th - hh + 0.5) % 1.0 - 0.5) * strength   # 最短路径转色相
            ns = min(1.0, s * k)
            opx[x, y] = (*hsv_to_rgb(nh, ns, v), a)
    return out


def sample_palette(img, alpha_cut=8, bands=3):
    """按亮度分档取平均色（暗→亮）。用来从原版贴图反查真实色值，做同色系新变体。"""
    px = img.convert("RGBA").load()
    w, h = img.size
    pts = []
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a > alpha_cut:
                pts.append((r, g, b))
    if not pts:
        return []
    pts.sort(key=luma)
    names = {3: ["dark", "mid", "light"]}.get(bands, [f"b{i + 1}" for i in range(bands)])
    out = []
    for i in range(bands):
        seg = pts[len(pts) * i // bands: len(pts) * (i + 1) // bands]
        if not seg:
            continue
        avg = tuple(int(round(sum(c[j] for c in seg) / len(seg))) for j in range(3))
        out.append((names[i] if i < len(names) else f"b{i + 1}", avg))
    return out


# ============================ 示例底图 ============================
def make_demo_base(size=16):
    """程序化画一张门形状的**灰度**底图（带边框/内凹面板/把手），用来演示"只差颜色"。"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    px = img.load()

    def put(x, y, v):
        if 0 <= x < size and 0 <= y < size:
            px[x, y] = (v, v, v, 255)

    for y in range(1, size - 1):          # 门主体
        for x in range(1, size - 1):
            put(x, y, 150)
    for x in range(1, size - 1):          # 上沿受光 / 下沿背光
        put(x, 1, 188)
        put(x, size - 2, 108)
    for y in range(1, size - 1):
        put(1, y, 188)
        put(size - 2, y, 108)
    for (px0, py0) in [(3, 2), (9, 2), (3, 8), (9, 8)]:   # 4 个内凹面板
        for yy in range(py0, min(size, py0 + 6)):
            for xx in range(px0, min(size, px0 + 5)):
                v = 128
                if xx == px0 or yy == py0:
                    v = 100                # 凹槽左上更暗
                if xx == px0 + 4 or yy == py0 + 5:
                    v = 162                # 右下接光
                put(xx, yy, v)
    for yy in range(7, 9):                # 把手
        for xx in range(11, 13):
            put(xx, yy, 208)
    return img


# ============================ 对比图 ============================
def make_sheet(imgs, out_path, scale=6, gap=2, per_row=6, bg=(38, 38, 45)):
    """把原图 + 全部变体排成一张对比图（每格放大 scale 倍）。"""
    if not imgs:
        return None
    cw, ch = imgs[0].size
    n = len(imgs)
    cols = min(per_row, n)
    rows = (n + cols - 1) // cols
    step_x, step_y = cw * scale + gap, ch * scale + gap
    W, H = gap + cols * step_x, gap + rows * step_y
    sheet = Image.new("RGB", (W, H), bg)
    for i, im in enumerate(imgs):
        r, c = divmod(i, cols)
        big = im.convert("RGBA").resize((cw * scale, ch * scale), NEAREST)
        sheet.paste(big, (gap + c * step_x, gap + r * step_y), big)
    sheet.save(out_path)
    return out_path


# ============================ 主流程 ============================
def main():
    ap = argparse.ArgumentParser(description="纹理变色器：形状不动，只换颜色")
    ap.add_argument("src", nargs="?", help="源纹理 png（形状底图）")
    ap.add_argument("--preset", choices=list(PRESET_PALETTES),
                    help="内置色板组：" + " / ".join(f"{k}={PRESET_DESC[k]}" for k in PRESET_PALETTES))
    ap.add_argument("--colors", help="自定义色板，逗号分隔 #RRGGBB（与 --names 对应）")
    ap.add_argument("--names", help="自定义色板的名字，逗号分隔；不给则用 c1、c2…")
    ap.add_argument("--list-presets", action="store_true", help="打印全部内置色板")
    ap.add_argument("--sample", action="store_true",
                    help="从源图采样真实色值（按亮度分档打印），不改色 —— 拿原版贴图反查色值用")
    ap.add_argument("--mode", choices=["ramp", "hue"], default="ramp",
                    help="ramp=亮度映射到目标梯度(默认)　hue=只转色相保留明度")
    ap.add_argument("--levels", type=int, default=0, help="色阶数，0=连续(默认)，4/6=像素画量化")
    ap.add_argument("--keep-wood", action="store_true",
                    help="工具贴图用：保留棕色木质手柄原色，只染材质部分"
                         "（原版所有材质的工具都是同一个木柄，整体染会出'蓝柄剑'）")
    ap.add_argument("--contrast", type=float, default=1.0, help="对比增强(>1 拉开明暗)")
    ap.add_argument("--gamma", type=float, default=1.0, help="伽马(<1 提亮暗部)")
    ap.add_argument("--prefix", help="输出文件名前缀，默认取源图文件名")
    ap.add_argument("--out", "-o", default="recolored", help="输出目录")
    ap.add_argument("--demo", action="store_true", help="不用源图，先生成一张门形示例底图")
    ap.add_argument("--scale", type=int, default=6, help="对比图放大倍数")
    ap.add_argument("--preview", action="store_true", help="每个变体额外输出 _previewx8.png")
    args = ap.parse_args()

    if args.list_presets:
        print("内置色板组（--preset）：")
        for k, items in PRESET_PALETTES.items():
            print(f"\n  [{k}] {PRESET_DESC[k]}  ({len(items)} 色)")
            print("    " + "  ".join(f"{n}#{r:02X}{g:02X}{b:02X}" for n, (r, g, b) in items))
        print("\n自定义：--colors \"#B8945F,#7A5A32\" --names oak,spruce")
        return

    if args.sample:
        if args.src:
            base = Image.open(args.src).convert("RGBA")
            label = Path(args.src).stem
        else:
            base = make_demo_base()
            label = "demo"
        pal = sample_palette(base)
        if not pal:
            print("[错误] 源图没有不透明像素")
            sys.exit(1)
        print(f"=== {label} 采样（{base.width}x{base.height}，按亮度分档，暗→亮）===")
        for n, c in pal:
            print(f"  {n:<6} #{c[0]:02X}{c[1]:02X}{c[2]:02X}   rgb({c[0]},{c[1]},{c[2]})")
        mid = next((c for n, c in pal if n == "mid"), pal[len(pal) // 2][1])
        print(f"\n中调色（做同色系新变体就用它）: #{mid[0]:02X}{mid[1]:02X}{mid[2]:02X}")
        print(f"直接可用: --colors \"#{mid[0]:02X}{mid[1]:02X}{mid[2]:02X}\"")
        return

    # 收集色板
    if args.colors:
        rgbs = [hex2rgb(s) for s in args.colors.split(",") if s.strip()]
        names = ([s.strip() for s in args.names.split(",")] if args.names else [])
        palette = [(names[i] if i < len(names) else f"c{i + 1}", c) for i, c in enumerate(rgbs)]
    elif args.preset:
        palette = PRESET_PALETTES[args.preset]
    else:
        palette = PRESET_PALETTES["woods"]
        print("[提示] 未指定 --preset/--colors，默认用 woods（木头条）")

    # 源图
    if args.demo:
        base = make_demo_base()
        origin_label = "demo"
    else:
        if not args.src:
            print("[错误] 需要给源纹理路径，或加 --demo 生成示例底图")
            sys.exit(1)
        base = Image.open(args.src).convert("RGBA")
        origin_label = Path(args.src).stem
    prefix = args.prefix or origin_label

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    sheet_imgs, made = [base], []
    print(f"源图: {origin_label}  {base.width}x{base.height}"
          f"　模式: {args.mode}　色板: {len(palette)} 色"
          f"　色阶: {'连续' if args.levels < 2 else args.levels}")
    for name, rgb in palette:
        if args.mode == "hue":
            im = recolor_hue(base, rgb)
        else:
            im = recolor(base, rgb, args.levels, args.contrast, args.gamma,
                         keep_wood=args.keep_wood)
        fp = out_dir / f"{prefix}_{name}.png"
        im.save(fp)
        if args.preview:
            im.resize((im.width * 8, im.height * 8), NEAREST).save(
                out_dir / f"{prefix}_{name}_previewx8.png")
        sheet_imgs.append(im)
        made.append(f"{name}:#{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}")
        print(f"  [出图] {fp.name}  #{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}")

    if args.demo:
        base.save(out_dir / f"{prefix}_base.png")
        print(f"  [底图] {prefix}_base.png  ← 灰度门形，可直接拿去改或当模板")

    sheet = make_sheet(sheet_imgs, out_dir / f"{prefix}_sheet.png", scale=args.scale)
    print(f"\n共 {len(made)} 张变体 → {out_dir}")
    print(f"对比图(第1格是源图) → {sheet}")
    print("顺序: " + "  ".join(made))


if __name__ == "__main__":
    main()

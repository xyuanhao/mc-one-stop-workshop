#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线等距预览一整片地形/建筑 —— 直接读存档的 .mca，不依赖游戏客户端。

为什么需要它：
    本机 gradlew runClient 只能跑到资源重载阶段（起不到游戏里），
    而「地图 / 建筑 / 自定义地形」这类交付物的成败恰恰是**看起来对不对**
    （形状有没有错位、房子比例是否失衡、边缘是不是碎的）。
    这条路停服后读 region 文件就能出图，改一版重看一张，成本几秒。

用法：
    python 世界预览.py <存档目录> [-o out.png] [--rad 16] [--y0 80] [--y1 115] [--scale 2]
    <存档目录> = 含 level.dat 的那层（saves/<世界名>/ 或服务端下的 <dir>/world/）

已适配 26.1 的存档布局：<world>/dimensions/<命名空间>/<维度>/region/
（同时兼容老布局 <world>/region/ 和直接传某个 region 目录）

依赖：pillow（pip install pillow）
两个写这个脚本时踩到的坑，改代码前务必看一眼：
  ① NBT 的 compound 循环**不能**写成 `d[self.string()] = self.payload(t)` ——
     Python 对 `容器[键] = 值` 是**先算值再算键**（实测 d[key()]=val() 调用顺序 ['val','key']），
     那会变成"先读 payload 再读字段名"，整条流错位，跑到很远处才报
     "unpack requires a buffer of 1 bytes"。必须拆成两行。
  ② section 的方块索引是 `(y*16 + z)*16 + x` → 取 x 要 `rem % 16`、z 要 `rem // 16`。
     写反了整座地形会碎成几块贴错位（不是报错，是"看起来就是坏的"）。
"""
import argparse
import math
import struct
import sys
import zlib
from pathlib import Path

try:
    from PIL import Image, ImageDraw
except ImportError:
    sys.exit("[错误] 缺 Pillow\n  · 装一下：pip install pillow\n  · 本脚本靠它出图，没它没法渲染")

# ---------------------------------------------------------------- NBT 读取
TAG_END, TAG_BYTE, TAG_SHORT, TAG_INT, TAG_LONG = 0, 1, 2, 3, 4
TAG_FLOAT, TAG_DOUBLE, TAG_BYTE_ARR, TAG_STR = 5, 6, 7, 8
TAG_LIST, TAG_COMPOUND, TAG_INT_ARR, TAG_LONG_ARR = 9, 10, 11, 12


class NbtReader:
    def __init__(self, buf: bytes):
        self.b, self.i = buf, 0

    def take(self, n: int) -> bytes:
        s = self.b[self.i:self.i + n]
        self.i += n
        return s

    def num(self, fmt: str):
        return struct.unpack(fmt, self.take(struct.calcsize(fmt)))[0]

    def string(self) -> str:
        return self.take(self.num(">H")).decode("utf-8", "replace")

    def payload(self, tag: int):
        if tag == TAG_BYTE:
            return self.num(">b")
        if tag == TAG_SHORT:
            return self.num(">h")
        if tag == TAG_INT:
            return self.num(">i")
        if tag == TAG_LONG:
            return self.num(">q")
        if tag == TAG_FLOAT:
            return self.num(">f")
        if tag == TAG_DOUBLE:
            return self.num(">d")
        if tag == TAG_BYTE_ARR:
            return self.take(self.num(">i"))
        if tag == TAG_STR:
            return self.string()
        if tag == TAG_LIST:
            et = self.num(">B")
            n = self.num(">i")
            return [self.payload(et) for _ in range(n)]
        if tag == TAG_COMPOUND:
            d = {}
            while True:
                t = self.num(">B")
                if t == TAG_END:
                    return d
                # 🔴 别写成 d[self.string()] = self.payload(t) ！
                #    Python 对 `容器[键表达式] = 值表达式` 是**先算值再算键**
                #    （实测 d[key()] = val() 的调用顺序是 ['val','key']），
                #    那会变成"先读 payload、再读字段名" → 整条流错位，
                #    症状是解析飘到很远的位置才报 "unpack requires a buffer of 1 bytes"。
                nm = self.string()
                d[nm] = self.payload(t)
        if tag == TAG_INT_ARR:
            n = self.num(">i")
            return list(struct.unpack(f">{n}i", self.take(4 * n)))
        if tag == TAG_LONG_ARR:
            n = self.num(">i")
            return list(struct.unpack(f">{n}q", self.take(8 * n)))
        raise ValueError(f"未知 NBT 标签 {tag}")

    def root(self):
        t = self.num(">B")
        self.string()
        return self.payload(t)


def iter_chunks(path: Path):
    """产出 (区块x, 区块z, 解压后的 NBT 字节)"""
    with open(path, "rb") as f:
        header = f.read(8192)
        if len(header) < 8192:
            return
        for i in range(1024):
            off = (header[i * 4] << 16) | (header[i * 4 + 1] << 8) | header[i * 4 + 2]
            sectors = header[i * 4 + 3]
            if off == 0 or sectors == 0:
                continue
            f.seek(off * 4096)
            raw = f.read(5)
            if len(raw) < 5:
                continue
            length, comp = struct.unpack(">IB", raw)
            data = f.read(max(0, length - 1))
            try:
                if comp == 1:
                    import gzip
                    data = gzip.decompress(data)
                elif comp == 2:
                    data = zlib.decompress(data)
                elif comp != 3:
                    continue
            except Exception:
                continue
            yield i % 32, i // 32, data


def find_region_dirs(root: Path):
    """兼容三种布局：新版 <world>/dimensions/<ns>/<dim>/region、老版 <world>/region、直接给 region"""
    hits = []
    if (root / "region").is_dir():
        hits.append((root.name or "world", root / "region"))
    dim = root / "dimensions"
    if dim.is_dir():
        for dd in dim.rglob("region"):
            if dd.is_dir():
                hits.append((dd.parent.name, dd))
    if root.name == "region" and root.is_dir():
        hits.append((root.parent.name, root))
    return hits or ([(root.name, root)] if any(root.glob("*.mca")) else [])


# ---------------------------------------------------------------- 方块解压
def read_blocks(level_root: Path, x0, x1, z0, z1, y_lo, y_hi):
    """返回 {(x,y,z): 方块id}。任何失败都要数出来——静默吞掉等于假阴性。"""
    out = {}
    seen = bad = 0
    for _dim, rdir in find_region_dirs(level_root):
        for rp in sorted(rdir.glob("r.*.*.mca")):
            rx, rz = rp.stem.split(".")[1:]
            rx, rz = int(rx), int(rz)
            for lx, lz, data in iter_chunks(rp):
                cx, cz = rx * 32 + lx, rz * 32 + lz
                if not (x0 // 16 <= cx <= x1 // 16 and z0 // 16 <= cz <= z1 // 16):
                    continue
                seen += 1
                try:
                    nbt = NbtReader(data).root()
                except Exception as e:
                    bad += 1
                    if bad <= 3:
                        print(f"  ✗ 区块 ({cx},{cz}) NBT 解析失败：{e!r}")
                    continue
                for sec in nbt.get("sections", []):
                    sy = sec.get("Y")
                    if sy is None:
                        continue
                    bs = sec.get("block_states")
                    if not bs:
                        continue
                    pal = [p.get("Name") for p in bs.get("palette", [])]
                    if not pal:
                        continue
                    base_y = sy * 16
                    if base_y + 15 < y_lo or base_y > y_hi:
                        continue
                    raw = bs.get("data")
                    bits = max(4, (len(pal) - 1).bit_length())
                    mask = (1 << bits) - 1
                    per = 64 // bits
                    idxs = None
                    if raw:
                        idxs = []
                        for word in raw:
                            w = word & 0xFFFFFFFFFFFFFFFF
                            for k in range(per):
                                idxs.append((w >> (k * bits)) & mask)
                    for k in range(4096):
                        pi = 0 if idxs is None else (
                            idxs[k] if k < len(idxs) else 0)
                        name = pal[pi] if pi < len(pal) else pal[0]
                        if name == "minecraft:air" or name == "minecraft:cave_air":
                            continue
                        y = base_y + (k // 256)
                        rem = k % 256
                        # MC 的 section 索引 = (y*16 + z)*16 + x
                        # → rem = z*16 + x，所以 x 取低位、z 取高位。写反了整座岛会碎成几块。
                        x = rem % 16
                        z = rem // 16
                        wx, wz = cx * 16 + x, cz * 16 + z
                        if not (y_lo <= y <= y_hi and x0 <= wx <= x1 and z0 <= wz <= z1):
                            continue
                        out[(wx, y, wz)] = name
    if seen == 0:
        sys.exit("[错误] 范围内一个区块都没扫到 —— 不许当成\"没问题\"\n"
                 f"  · 找根目录确认：{level_root}\n"
                 f"  · 让 --rad 大一点，或检查 region 文件是否存在")
    if bad:
        print(f"  ! {bad}/{seen} 个区块解析失败（上方已列出前 3 条原因）")
    print(f"  扫到 {seen} 个区块，取出 {len(out)} 个非空方块")
    return out


# ---------------------------------------------------------------- 配色
# (顶面, 侧面)：尽量贴近原版观感，未列出的给个灰
COLORS = {
    "minecraft:grass_block":     ((124, 176, 78), (121, 85, 58)),
    "minecraft:dirt":            ((134, 96, 67), (121, 85, 58)),
    "minecraft:coarse_dirt":     ((120, 86, 60), (110, 78, 54)),
    "minecraft:rooted_dirt":     ((122, 88, 62), (112, 80, 56)),
    "minecraft:podzol":          ((84, 59, 33), (110, 78, 54)),
    "minecraft:stone":           ((125, 125, 125), (115, 115, 115)),
    "minecraft:andesite":        ((138, 138, 138), (128, 128, 128)),
    "minecraft:diorite":         ((188, 190, 192), (172, 174, 176)),
    "minecraft:granite":         ((149, 105, 82), (137, 96, 75)),
    "minecraft:tuff":            ((106, 112, 106), (98, 103, 98)),
    "minecraft:cobblestone":     ((124, 124, 124), (114, 114, 114)),
    "minecraft:mossy_cobblestone": ((108, 130, 108), (99, 118, 99)),
    "minecraft:stone_bricks":    ((125, 125, 125), (115, 115, 115)),
    "minecraft:stone_brick_slab": ((130, 130, 130), (120, 120, 120)),
    "minecraft:oak_planks":      ((168, 132, 78), (155, 121, 71)),
    "minecraft:oak_log":         ((110, 84, 50), (100, 76, 45)),
    "minecraft:stripped_oak_log": ((180, 145, 92), (166, 133, 84)),
    "minecraft:oak_stairs":      ((168, 132, 78), (155, 121, 71)),
    "minecraft:oak_slab":        ((170, 134, 80), (157, 123, 73)),
    "minecraft:oak_leaves":      ((80, 130, 58), (70, 118, 50)),
    "minecraft:oak_door":        ((150, 116, 68), (138, 106, 62)),
    "minecraft:oak_fence":       ((168, 132, 78), (155, 121, 71)),
    "minecraft:spruce_planks":   ((114, 80, 49), (104, 73, 45)),
    "minecraft:spruce_stairs":   ((114, 80, 49), (104, 73, 45)),
    "minecraft:spruce_slab":     ((118, 83, 51), (108, 76, 47)),
    "minecraft:spruce_log":      ((78, 56, 33), (72, 51, 30)),
    "minecraft:glass_pane":      ((210, 235, 245), (190, 215, 228)),
    "minecraft:glass":           ((210, 235, 245), (190, 215, 228)),
    "minecraft:water":           ((60, 110, 200), (52, 96, 178)),
    "minecraft:farmland":        ((115, 79, 52), (105, 72, 47)),
    "minecraft:wheat":           ((190, 168, 88), (176, 155, 80)),
    "minecraft:carrots":         ((110, 158, 70), (100, 145, 62)),
    "minecraft:short_grass":     ((124, 176, 78), (110, 156, 68)),
    "minecraft:fern":            ((112, 168, 74), (100, 152, 66)),
    "minecraft:dandelion":       ((240, 210, 70), (222, 194, 64)),
    "minecraft:poppy":           ((200, 60, 60), (184, 55, 55)),
    "minecraft:cornflower":      ((104, 130, 205), (95, 120, 190)),
    "minecraft:chest":           ((140, 104, 54), (128, 95, 49)),
    "minecraft:crafting_table":  ((140, 104, 54), (124, 92, 48)),
    "minecraft:furnace":         ((122, 122, 122), (112, 112, 112)),
    "minecraft:bookshelf":       ((160, 126, 76), (146, 115, 69)),
    "minecraft:red_bed":         ((170, 62, 62), (156, 57, 57)),
    "minecraft:red_carpet":      ((178, 68, 68), (163, 62, 62)),
    "minecraft:lantern":         ((198, 180, 110), (182, 165, 100)),
    "minecraft:torch":           ((232, 200, 120), (214, 184, 110)),
}
DEFAULT = ((190, 190, 190), (175, 175, 175))

# 手写表只覆盖了常用方块，其余从 block_colors.py 兜底
# （由 生成方块配色.py 从原版客户端材质提取平均色，覆盖 700+ 方块）。
# 没有它的话 smooth_quartz / dark_oak_stairs / calcite 这类会一律画成灰的，
# 预览就完全看不出真实效果。
try:
    from block_colors import BLOCK_COLORS as _AUTO
except Exception:
    _AUTO = {}
COLORS = {**_AUTO, **COLORS}


def shade(c, k):
    return tuple(max(0, min(255, int(v * k))) for v in c)


# ---------------------------------------------------------------- 渲染
def render(blocks, scale=2, bg=(24, 26, 32)):
    if not blocks:
        sys.exit("[错误] 这个范围内没有任何方块 —— 世界是空的？\n"
                 "  · 是不是 /function 没跑成功，或者区块压根没保存\n"
                 "  · 先用扫区域文件.py 确认过里面有方块再来渲染")

    xs = [p[0] for p in blocks]
    ys = [p[1] for p in blocks]
    zs = [p[2] for p in blocks]
    X0, X1, Z0, Z1, Y0, Y1 = min(xs), max(xs), min(zs), max(zs), min(ys), max(ys)

    TW, TH, BH = 16, 8, 12           # 菱形宽/高、方块立面高度
    pad = 24
    W = ((X1 - X0) + (Z1 - Z0) + 2) * TW // 2 + pad * 2
    H = ((X1 - X0) + (Z1 - Z0) + 2) * TH // 2 + (Y1 - Y0 + 2) * BH + pad * 2

    img = Image.new("RGB", (W * scale, H * scale), bg)
    d = ImageDraw.Draw(img)

    def px(x, y, z):
        """世界坐标 → 屏幕；+x 向右下，+z 向左下，+y 向上"""
        sx = ((x - X0) - (z - Z0)) * TW // 2 + (Z1 - Z0) * TW // 2 + pad
        sy = ((x - X0) + (z - Z0)) * TH // 2 - (y - Y0) * BH + (Y1 - Y0) * BH + pad
        return sx * scale, sy * scale

    def poly(pts, color):
        d.polygon([tuple(map(float, p)) for p in pts], fill=color, outline=None)

    order = sorted(blocks, key=lambda p: (p[0] + p[2], p[1]))
    n = len(blocks)
    for i, (x, y, z) in enumerate(order):
        name = blocks[(x, y, z)]
        top, side = COLORS.get(name, DEFAULT)
        # 顶面
        cx, cy = px(x, y, z)
        tw, th, bh = TW * scale, TH * scale, BH * scale
        vs = [
            (cx, cy),                       # 上角（相对：+x+z 方向的最远点）
            (cx + tw / 2, cy + th / 2),
            (cx, cy + th),
            (cx - tw / 2, cy + th / 2),
        ]
        # 左右两个立面（各偏移半个菱形）
        ls = [(cx - tw / 2, cy + th / 2), (cx, cy + th), (cx, cy + th + bh), (cx - tw / 2, cy + th / 2 + bh)]
        rs = [(cx, cy + th), (cx + tw / 2, cy + th / 2), (cx + tw / 2, cy + th / 2 + bh), (cx, cy + th + bh)]
        # 先画侧面（暗），再画顶面（亮），自然形成明暗区分
        poly(ls, shade(side, 0.78))
        poly(rs, shade(side, 0.60))
        poly(vs, top)
        # 顶面描边，让方块边界清楚
        d.line([vs[0], vs[1]], fill=shade(top, 1.18), width=1)
        d.line([vs[1], vs[2]], fill=shade(top, 1.18), width=1)
        d.line([vs[2], vs[3]], fill=shade(top, 0.85), width=1)
        d.line([vs[3], vs[0]], fill=shade(top, 0.85), width=1)
        if i and i % 20000 == 0:
            print(f"  渲染 {i}/{n}")

    # 虚空观测提示
    print(f"[OK] 方块 {n} 个，范围 x[{X0},{X1}] y[{Y0},{Y1}] z[{Z0},{Z1}]")
    print(f"     图像 {W * scale}x{H * scale}")
    return img


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("world", help="存档根目录（含 level.dat 的那层）")
    ap.add_argument("-o", "--out", default="island_preview.png")
    ap.add_argument("--rad", type=int, default=16, help="渲染半径（格），默认 16")
    ap.add_argument("--cx", type=int, default=0, help="渲染中心 x，默认 0（看别人地图时用）")
    ap.add_argument("--cz", type=int, default=0, help="渲染中心 z，默认 0")
    ap.add_argument("--y0", type=int, default=80)
    ap.add_argument("--y1", type=int, default=115)
    ap.add_argument("--scale", type=int, default=2)
    a = ap.parse_args()

    root = Path(a.world)
    if not root.is_dir():
        sys.exit(f"[错误] 找不到存档目录：{root}\n"
                 f"  · 应该是 <实例>/saves/<世界名>/ 或服务端下的 <dir>/world/\n"
                 f"  · 认不准就找含 level.dat 的那层")

    print(f"读取 {root} …")
    blocks = read_blocks(root, a.cx - a.rad, a.cx + a.rad, a.cz - a.rad, a.cz + a.rad, a.y0, a.y1)
    img = render(blocks, scale=a.scale)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    print(f"[OK] 已写出 {out}")


if __name__ == "__main__":
    main()

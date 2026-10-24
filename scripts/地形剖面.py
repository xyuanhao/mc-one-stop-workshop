#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把存档切成横截面 / 纵截面图：**方块=黑 · 液体=灰 · 空气=白**。

为什么需要它：
    判据（"y=2000 那格是草方块"）只能证明单点，看不见**形状** ——
    "洞口是不是上大下小的漏斗""层间台阶有没有露出来""那片空腔到底有多高"
    "主洞的竖井内壁立没立住"。这类问题只有摊成一张截面图才看得见，
    而本机 gradlew runClient 起不到游戏里（见 SKILL.md 工作流 §7⑥），
    所以这是改地形之后**唯一的形状验收手段**。

三种截面（--axis 选法向轴）：
    --axis z --at 0     纵截面：固定 z=0，横轴 x、纵轴 y   ← 最常用的"剖面墙"
    --axis x --at 0     纵截面：固定 x=0，横轴 z、纵轴 y
    --axis y --at 64    横截面：固定 y=64，横轴 x、纵轴 z  ← 俯视平面图（上=北）

用法：
    python 地形剖面.py <存档目录> --axis z --at 0 -o slice.png
    python 地形剖面.py <存档目录> --axis z --at 0 --x0 -600 --x1 600 -o slice.png
    python 地形剖面.py <存档目录> --axis y --at 2000 --span 256 -o top.png
    python 地形剖面.py <存档目录> --dimension minecraft:overworld --axis z --at 0
    python 地形剖面.py <存档目录> --list-dims          # 只列维度，不画图
    python 地形剖面.py --selftest                     # 自检：解码/分类/坐标映射

<存档目录> = 含 level.dat 的那层（实例的 saves/<世界名>/，或服务端的 <目录>/world/）。
已适配 26.1 的 `<world>/dimensions/<命名空间>/<维度>/region/`，兼容老版 `<world>/region/`。

三件必须先知道的事（都是踩过的坑，改代码前看一眼）：
  ① **区块没加载 ≠ 空气**。没生成的区块整列是"空气"，直接画成白会做出
     "这里是个大空腔"的假图，而且**看不出任何异常**。
     本工具默认把"无数据"画成**亮橙**并在报告里给占比，`--pure` 才强制白。
  ② **y 轴别画反**。图像第 0 行必须是**最高处**（y=y1）。写成 `y-y0` 的话图上下颠倒，
     而右边刻度还是按"上=高"画的 —— 图和数据对不上，能把台地看成洞底。
  ③ **section 内索引是 `(y*16+z)*16+x`**，x 取低位、z 取高位。写反了地形会碎成几块
     并错位（不报错，就是"看起来是坏的"）。

退出码：0 = 正常 ｜ 1 = 图出来了但有值得警惕的信号（无数据过多 / 区块缺失）
        ｜ 2 = 参数或路径问题
"""
from __future__ import annotations

import argparse
import gzip
import math
import struct
import sys
import zlib
from pathlib import Path

try:
    import numpy as np
except ImportError:
    # 🔴 不在 import 阶段 sys.exit —— 那会让 `--help` / skill 自检冒烟一起挂掉
    #    （2026-10-01 修：自检报「--help 既没输出用法也没报错」即此）。
    #    真要用到才报缺口（见 main 开头的检查）。
    np = None
try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = ImageDraw = ImageFont = None


# ──────────────────────────────────────────────────────────── 分类与配色

C_SOLID, C_LIQ, C_AIR, C_NODATA = 0, 1, 2, 3
NO_DATA = 255                      # 画布里的"无数据"哨兵值（与上色码分开，别混）

# 用户指定的三色（黑 / 灰 / 白）。无数据单独一色，`--pure` 时并回白。
RGB = {
    C_SOLID:  (0, 0, 0),
    C_LIQ:    (128, 128, 128),
    C_AIR:    (255, 255, 255),
}
RGB_NODATA = (255, 194, 77)
RGB_NODATA_DARK = (238, 162, 58)      # 斜纹的深色条
RGB_NODATA_PURE = (255, 255, 255)

AIR_NAMES = {
    "minecraft:air", "minecraft:cave_air", "minecraft:void_air", "minecraft:structure_void",
}

# 液体判定：先精确白名单，再按**下划线分词**匹配关键字（子串匹配会出事 ——
# 实测 `soul_soil` 会被 "oil" 子串命中、判成灰色液体块）。
LIQ_EXACT = {"water", "lava", "flowing_water", "flowing_lava"}
LIQ_TOKENS = {"water", "lava", "fluid", "molten", "oil", "honey", "milk",
              "soup", "juice", "blood", "brine", "slurry", "acid", "steam",
              "ethanol", "naphtha", "heavy_water", "biofuel"}
LIQ_DENY_TOKENS = {"cauldron", "bucket", "bottle", "potion", "tank", "pipe", "block",
                   "comb", "leaves", "grass", "stain", "log", "planks", "door",
                   "generator", "pump", "can", "cap", "seed", "dust", "powder",
                   "shard", "crystal", "coral", "vine", "bud", "plant", "barrel"}


def is_liquid(name: str, extra: tuple[str, ...] = ()) -> bool:
    """液体判定。判出来的清单会打进报告让用户核对 —— 判错一眼能看出来。"""
    path = name.split(":", 1)[-1]
    if path in LIQ_EXACT:
        return True
    toks = set(path.split("_"))
    if extra and any(k in path for k in extra):
        return True
    if not (toks & LIQ_TOKENS):
        return False
    return not (toks & LIQ_DENY_TOKENS)


def classify(name: str, extra: tuple[str, ...] = ()) -> int:
    if name in AIR_NAMES:
        return C_AIR
    if is_liquid(name, extra):
        return C_LIQ
    return C_SOLID


def fail(msg: str, *hints) -> None:
    """三层报错：什么不对 → 为什么会这样 → 下一步做什么。"""
    print(f"[错误] {msg}")
    for h in hints:
        print(f"  {h}")
    sys.exit(2)


# ──────────────────────────────────────────────────────────── 最小 NBT 解析

TAG_BYTE, TAG_SHORT, TAG_INT, TAG_LONG = 1, 2, 3, 4
TAG_FLOAT, TAG_DOUBLE, TAG_BYTE_ARR, TAG_STR = 5, 6, 7, 8
TAG_LIST, TAG_COMPOUND, TAG_INT_ARR, TAG_LONG_ARR = 9, 10, 11, 12
_FMT_SZ = {">B": 1, ">H": 2, ">i": 4, ">q": 8, ">b": 1, ">h": 2, ">f": 4, ">d": 8}


class NbtReader:
    def __init__(self, buf: bytes):
        self.b, self.i = buf, 0

    def take(self, n: int) -> bytes:
        s = self.b[self.i:self.i + n]
        self.i += n
        return s

    def num(self, fmt: str):
        # 用 unpack_from 直接读，**不要"切片 + unpack"** —— 切片会为每次读取分配一个
        # bytes 对象；横截面要读上千个区块、每次几十万次读取时，这是最大的一笔开销。
        v = struct.unpack_from(fmt, self.b, self.i)[0]
        self.i += _FMT_SZ[fmt]
        return v

    def string(self) -> str:
        n = struct.unpack_from(">H", self.b, self.i)[0]
        self.i += 2
        s = self.b[self.i:self.i + n].decode("utf-8", "replace")
        self.i += n
        return s

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
                if t == 0:                       # TAG_End
                    return d
                # 🔴 别写成 d[self.string()] = self.payload(t)：
                #    Python 对 `容器[键] = 值` 是**先算值再算键**，会变成
                #    "先读 payload 再读字段名" → 整条流错位，飘到很远才报
                #    "unpack requires a buffer of 1 bytes"。必须拆两行。
                nm = self.string()
                d[nm] = self.payload(t)
        if tag == TAG_INT_ARR:
            n = self.num(">i")
            return list(struct.unpack(f">{n}i", self.take(4 * n)))
        if tag == TAG_LONG_ARR:
            n = self.num(">i")
            return list(struct.unpack(f">{n}q", self.take(8 * n)))
        raise ValueError(f"未知 NBT 标签 {tag}")

    def skip(self, tag: int) -> None:
        """按类型直接跳过 —— 只前进指针、不构建对象。

        NBT 解析的瓶颈是"构建 Python 对象"，一个区块 252 个 section 全建一遍要几十毫秒。
        横截面要读上千个区块时，光跳过 heightmaps / Light / entities 这些大块就能省一大半。
        """
        if tag == TAG_BYTE:
            self.i += 1
        elif tag == TAG_SHORT:
            self.i += 2
        elif tag in (TAG_INT, TAG_FLOAT):
            self.i += 4
        elif tag in (TAG_LONG, TAG_DOUBLE):
            self.i += 8
        # 🔴🔴 这四处**必须拆成两步**：`self.i += self.num(">H")` 是错的 ——
        #   `a.attr += expr` 会先取 attr 的**旧值**，再求 expr，最后相加。
        #   而 num() 自己已经把 self.i 前进了（读长度要 2/4 字节），
        #   于是 net 前进只剩"长度"，把长度字段本身那 2/4 字节丢了。
        #   症状：指针整体偏浅，飘到某处报 "未知 NBT 标签 105"（105='i'，说明
        #   正把字段名当 tag id 读）。实测 Status 少跳 2 字节就是这个原因。
        elif tag == TAG_BYTE_ARR:
            n = self.num(">i")
            self.i += n
        elif tag == TAG_STR:
            n = self.num(">H")
            self.i += n
        elif tag == TAG_LIST:
            et = self.num(">B")
            n = self.num(">i")
            for _ in range(n):
                self.skip(et)
        elif tag == TAG_COMPOUND:
            while True:
                t = self.num(">B")
                if t == 0:
                    return
                # 🔴 不能写 `self.i += self.num(">H")`：num() 自己已经把指针前进 2 了，
                #    再 += 长度等于跳过"长度+长度"字节 → 指针整体跑偏，
                #    症状是飘到某处报 "未知 NBT 标签 101"（101 就是字符 'e'）。
                n = self.num(">H")
                self.i += n
                self.skip(t)
        elif tag == TAG_INT_ARR:
            n = self.num(">i")
            self.i += 4 * n
        elif tag == TAG_LONG_ARR:
            n = self.num(">i")
            self.i += 8 * n
        else:
            raise ValueError(f"未知 NBT 标签 {tag}")

    def root(self):
        t = self.num(">B")
        self.string()
        return self.payload(t)


def skim_chunk(data: bytes, want_y):
    """只取出区块的 Status 和**需要的那些** section（其余段只跳指针）。

    want_y(sectionY) 为假就跳过整段 —— 横截面只要含 --at 的那一段，
    252 段里建 1 段，比全套建一遍快一个数量级。返回 (status, [section...])。
    """
    r = NbtReader(data)
    if r.num(">B") != TAG_COMPOUND:
        return "", []
    r.string()
    status, sections = "", []
    while True:
        t = r.num(">B")
        if t == 0:
            break
        name = r.string()
        if name == "Status" and t == TAG_STR:
            status = r.string()
        elif name in ("sections", "Sections") and t == TAG_LIST:
            et = r.num(">B")
            n = r.num(">i")
            for _ in range(n):
                if et != TAG_COMPOUND:
                    r.skip(et)
                    continue
                sec = _skim_section(r, want_y)
                if sec is not None:
                    sections.append(sec)
        else:
            r.skip(t)
    return status, sections


def _skim_section(r: NbtReader, want_y):
    """读一个 section：Y 记下来，block_states 只记偏移（需要才回头解析）。"""
    y = None
    bs_off = None
    while True:
        t = r.num(">B")
        if t == 0:
            break
        nm = r.string()
        if nm == "Y" and t in (TAG_BYTE, TAG_INT):
            y = int(r.payload(t))
        elif nm == "block_states" and t == TAG_COMPOUND:
            bs_off = r.i                      # ← 只记位置，先跳过，别急着建对象
            r.skip(t)
        else:
            r.skip(t)
    end = r.i                                 # ← 本段末尾
    if y is None or bs_off is None:
        return None
    if want_y is not None and not want_y(y):
        return None
    r.i = bs_off                              # 回头解析这一段
    bs = r.payload(TAG_COMPOUND)
    # 🔴🔴 必须把指针恢复到本段末尾！回溯解析会让指针停在 block_states 的**末尾**，
    #   而 block_states 后面还有 block_light / sky_light / biome —— 不恢复的话
    #   下一个段就从**段中间**开始读，症状是"254 段只解出 127 段"，
    #   而且不报任何错（只是图矮了一半、段数少一半）。实测栽在这。
    r.i = end
    return {"Y": y, "block_states": bs}


def iter_chunks(path: Path):
    """产出 (本地区块x, 本地区块z, 解压后的 NBT 字节)。标志：1=gzip 2=zlib 3=未压缩。"""
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
                    data = gzip.decompress(data)
                elif comp == 2:
                    data = zlib.decompress(data)
                elif comp != 3:
                    continue
            except Exception:
                continue
            yield i % 32, i // 32, data


# ──────────────────────────────────────────────────────────── region / 维度定位

def find_dims(root: Path):
    """返回 [(维度名, region目录)]。兼容三种布局，找不到返回空列表。"""
    hits: list[tuple[str, Path]] = []
    dims = root / "dimensions"
    if dims.is_dir():
        for r in sorted(dims.rglob("region")):
            if not r.is_dir():
                continue
            p = r.parent                      # <dim>
            # 26.1 是 dimensions/<命名空间>/<维度>/region/，维度 id 要带命名空间
            name = f"{p.parent.name}:{p.name}" if p.parent != dims else p.name
            hits.append((name, r))
    if (root / "region").is_dir():
        hits.append(("minecraft:overworld", root / "region"))
    if root.name == "region" and root.is_dir():
        hits.append((root.parent.name, root))
    if not hits and any(root.glob("*.mca")):
        hits.append((root.name, root))
    return hits


def pick_region(root: Path, want: str | None, forced: str | None):
    if forced:
        p = Path(forced)
        if not p.is_dir():
            fail(f"region 目录不存在：{p}",
                 "· 传的是含 r.x.z.mca 的目录",
                 "· 一般长这样：<世界>/dimensions/minecraft/overworld/region")
        return (want or p.parent.name), p
    dims = find_dims(root)
    if not dims:
        fail(f"在 {root} 里找不到任何 region 目录",
             "· 传存档目录（含 level.dat 的那层），或直接传 .../region",
             "· 26.1 的路径是 <世界>/dimensions/<命名空间>/<维度>/region/")
    if want:
        for n, p in dims:
            if n == want or (":" not in want and n.split(":")[-1] == want):
                return n, p
        fail(f"没有维度 {want}（这个存档里有：{', '.join(n for n, _ in dims)}）",
             "· 维度名就是 <命名空间>:<维度>，如 minecraft:overworld",
             "· 用 --list-dims 看一眼")
    if len(dims) > 1:
        print(f"[提示] 这个存档有多个维度，默认画 {dims[0][0]}"
              f"（全部：{', '.join(n for n, _ in dims)}）；要别的加 --dimension")
    return dims[0]


# ──────────────────────────────────────────────────────────── 方块解码

def _pal_names(palette):
    out = []
    for p in palette:
        n = p.get("Name", "?") if isinstance(p, dict) else p
        if isinstance(n, bytes):
            n = n.decode("utf-8", "replace")
        out.append(n or "?")
    return out


def section_arrays(sec):
    """section → (palette名单, 打包longs, bits, 是否紧凑打包, 单一名字或None)。

    紧凑打包（1.16+ 的 block_states）：一个值不跨 long 边界。
    老格式（<1.16 的 Palette/BlockStates）：值可跨 long 边界。
    """
    bs = sec.get("block_states")
    if bs:
        pal = bs.get("palette") or []
        raw = bs.get("data")
        compact = True
    else:
        pal = sec.get("Palette") or []
        raw = sec.get("BlockStates")
        compact = False
    if not pal:
        return None
    names = _pal_names(pal)
    if raw is None or len(names) == 1:
        return names, None, 0, compact, names[0]
    bits = max(4, (len(names) - 1).bit_length())
    return names, raw, bits, compact, None


def section_slice(sec, axis: str, fixed: int, liquid_extra: tuple[str, ...] = ()):
    """取这一段的某个截面 → (上色码数组, 行坐标数组, 列坐标数组)。

    - axis='y'：固定 y，返回 [z][x]
    - axis='z'：固定 z，返回 [y][x]
    - axis='x'：固定 x，返回 [y][z]
    fixed 是**段内**局部坐标 0..15。行/列坐标是段内坐标（0..15）。
    """
    parsed = section_arrays(sec)
    if parsed is None:
        return None
    names, raw, bits, compact, single = parsed

    a = np.arange(16, dtype=np.int64)
    if axis == "y":
        yy, zz, xx = np.int64(fixed), a[:, None], a[None, :]
    elif axis == "z":
        yy, zz, xx = a[:, None], np.int64(fixed), a[None, :]
    else:
        yy, zz, xx = a[:, None], a[None, :], np.int64(fixed)

    # 🔴 MC 的 section 索引 = (y*16 + z)*16 + x —— x 取低位、z 取高位。
    k = (yy * 16 + zz) * 16 + xx

    if single is not None:
        codes = np.full(k.shape, classify(single, liquid_extra), dtype=np.uint8)
    else:
        words = np.array([int(v) & 0xFFFFFFFFFFFFFFFF for v in raw], dtype=np.uint64)
        if words.size == 0:
            return None
        mask = np.uint64((1 << bits) - 1)
        per = 64 // bits
        if compact:
            li = np.clip((k // per).astype(np.intp), 0, words.size - 1)
            # 🔴 移位量必须是 uint64：NumPy 2 里 `uint64 >> int64` 会被提升成 float64
            #    并抛 "right_shift not supported"。同理 k % per 也得先转。
            sh = (k % per).astype(np.uint64) * np.uint64(bits)
            idx = (words[li] >> sh) & mask
        else:
            start = k.astype(np.uint64) * np.uint64(bits)
            li = (start >> np.uint64(6)).astype(np.intp)
            off = start & np.uint64(63)
            li_c = np.clip(li, 0, words.size - 1)
            nxt = np.clip(li + 1, 0, words.size - 1)
            spill = np.where(off + np.uint64(bits) > np.uint64(64),
                             (words[nxt] << (np.uint64(64) - off)) & mask,
                             np.uint64(0))
            idx = ((words[li_c] >> off) & mask) | spill
        idx = np.clip(idx.astype(np.int64), 0, len(names) - 1).astype(np.intp)
        pal_codes = np.array([classify(n, liquid_extra) for n in names], dtype=np.uint8)
        codes = pal_codes[idx]

    if axis in ("y", "z"):
        row_c, col_c = (zz, xx) if axis == "y" else (yy, xx)
    else:
        row_c, col_c = yy, zz
    shape = codes.shape
    return codes, np.broadcast_to(row_c, shape), np.broadcast_to(col_c, shape)


# ──────────────────────────────────────────────────────────── 坐标 → 像素

def pixel_rows(axis: str, row_coord, y1: int, hv0: int):
    """纵轴坐标 → 像素行。**图像第 0 行 = 最高处**（axis!=y）或最小 z（axis=y，北在上）。"""
    if axis == "y":
        return row_coord.astype(np.int64) - hv0        # 横截面：行是 z，基准 = z 起点
    return np.int64(y1) - row_coord.astype(np.int64)   # 纵截面：行是 y，第 0 行 = 最高


def pixel_cols(col_coord, hu0: int):
    """横轴坐标 → 像素列。

    🔴 基准**永远**是 hu0（横轴起点），跟 axis 无关 —— 之前写成
    `hv0 if axis=='x' else hu0`，而 axis='x' 时 hv0 是"固定轴坐标"（=at=0），
    于是图上把 z∈[0,575] 的数据当成 z∈[-560,15] 展示（横轴整体错位 560 格），
    图照样出得来、只是内容全错。别再引入 axis 分支。
    """
    return col_coord.astype(np.int64) - hu0


# ──────────────────────────────────────────────────────────── 扫描

def nearby_chunks(axis: str, at: int, hu0: int, hu1: int, hv0: int, hv1: int):
    """返回需要读的 (cx, cz)。

    参数一律按**截面语义**命名，别再混用世界轴名 —— 这里曾经把横轴范围传进了 x 参数位，
    结果 axis='x' 只读了一个区块（1/51），图照样出得来、只是几乎全是"无数据"。

    hu* = 横轴范围（axis='z'/'y' 时指 x；axis='x' 时指 z）
    hv* = 第二横轴（只有 axis='y' 用；另两种轴时它等于 at）
    """
    if axis == "z":                     # 固定 z，横轴 x → 只要一行区块（最省）
        cz = at >> 4
        return [(cx, cz) for cx in range(hu0 >> 4, (hu1 >> 4) + 1)]
    if axis == "x":                     # 固定 x，横轴 z → 只要一列区块
        cx = at >> 4
        return [(cx, cz) for cz in range(hu0 >> 4, (hu1 >> 4) + 1)]
    return [(cx, cz) for cx in range(hu0 >> 4, (hu1 >> 4) + 1)
            for cz in range(hv0 >> 4, (hv1 >> 4) + 1)]


def iter_wanted_chunks(region_dir: Path, specs):
    """产出 (cx, cz, 解压后的NBT字节 或 None)。

    **按 region 文件分组**：同一个 r.x.z.mca 里的多个区块一次 iter_chunks 全取走。
    不分组的话，一个区块开一次文件 —— 横截面 2601 个区块要重复扫 2601 遍 8KB 索引表，
    I/O 白白多花几十秒（实测 2601 区块从 3m35s 降到 1m 级）。
    """
    groups: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for cx, cz in specs:
        groups.setdefault((cx >> 5, cz >> 5), []).append((cx, cz))
    for rx, rz in sorted(groups):
        rp = region_dir / f"r.{rx}.{rz}.mca"
        want = groups[(rx, rz)]
        if not rp.is_file():
            for cx, cz in want:
                yield cx, cz, None
            continue
        wantset = {(cx & 31, cz & 31) for cx, cz in want}
        found: dict[tuple[int, int], bytes] = {}
        for lx, lz, data in iter_chunks(rp):
            if (lx, lz) in wantset:
                found[(lx, lz)] = data
        for cx, cz in want:
            yield cx, cz, found.get((cx & 31, cz & 31))


def is_full(st: str) -> bool:
    if not st:
        return True                    # 老存档可能没这个字段，宽松放行
    return st.split(":")[-1] == "full"


def probe_y_range(region_dir: Path, specs):
    """读若干个区块，取 section Y 的**并集**推算世界全高。

    ⚠️ 别只看第一个区块：未生成完的区块（Status=structure_starts 之类）只带
       下半部分 section，拿它推高度会得出"世界只有一半高"的假范围 ——
       实测就是这么白画了一张 2094 格高的图（真值 4064），而且不报任何错。
    """
    ys: set[int] = set()
    tried = 0
    for cx, cz in specs[:64]:
        if tried >= 12:
            break
        rp = region_dir / f"r.{cx >> 5}.{cz >> 5}.mca"
        if not rp.is_file():
            continue
        for lx, lz, data in iter_chunks(rp):
            if lx != (cx & 31) or lz != (cz & 31):
                continue
            tried += 1
            try:
                st, secs = skim_chunk(data, None)
            except Exception:
                break
            if not is_full(st):
                break                      # 没生成完的不许参与推高度
            ys |= {s["Y"] for s in secs}
            break
    if not ys:
        return None
    return min(ys) * 16, max(ys) * 16 + 15


# ──────────────────────────────────────────────────────────── 渲染

def nice_step(span: int, target: int = 8) -> int:
    span = max(1, int(span))
    raw = max(1.0, span / target)
    e = 10 ** max(0, int(math.floor(math.log10(raw))))
    for m in (1, 2, 5, 10):
        if raw <= m * e:
            return int(m * e)
    return int(10 * e)


def load_font(size: int):
    for p in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
              "/System/Library/Fonts/PingFang.ttc",
              "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
              "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"):
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def render(canvas, axis, title, hu0, hu1, hv0, hv1, y0, y1, scale, out_path,
           pure, grid_step, bare=False):
    """canvas: uint8 二维数组，值 = 上色码，255 = 无数据。

    bare=True：只出纯截面图（无标题/图例/刻度/边距），1 像素就是 1 格 ——
    要拿程序读像素做判据时必须用这个，否则刻度虚线会盖住数据格。
    """
    H, W = canvas.shape
    arr = np.zeros((H, W, 3), dtype=np.uint8)
    for code, rgb in RGB.items():
        arr[canvas == code] = rgb
    nodata = canvas == NO_DATA
    if pure:
        arr[nodata] = RGB_NODATA_PURE
    else:
        # 无数据画成**斜纹**而不是纯色：纯色在"黑块旁边"会被眼睛读成背景/空气，
        # 而它恰恰是"这块不可信"。斜纹是无歧义的"这里不是地形"。
        arr[nodata] = RGB_NODATA
        yy, xx = np.mgrid[0:H, 0:W]
        arr[nodata & (((xx + yy) // 5) % 2 == 0)] = RGB_NODATA_DARK
    pil = Image.fromarray(arr, "RGB")
    if bare:
        scale = max(1, scale)
        if scale != 1:
            pil = pil.resize((W * scale, H * scale), Image.NEAREST)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        pil.save(out_path)
        return pil.size

    pad_l, pad_t, pad_r, pad_b = 10, 36, 82, 26
    if scale != 1:
        pil = pil.resize((W * scale, H * scale), Image.NEAREST)

    img = Image.new("RGB", (pad_l + W * scale + pad_r, pad_t + H * scale + pad_b), (28, 30, 36))
    img.paste(pil, (pad_l, pad_t))
    d = ImageDraw.Draw(img)
    f = load_font(13)
    fs = load_font(11)

    # ── 标题 + 图例（从右往左排）
    d.text((pad_l, 4), title, fill=(230, 230, 236), font=f)
    legend = [("空气", C_AIR), ("液体", C_LIQ), ("方块", C_SOLID)]
    if not pure:
        legend.append(("无数据", NO_DATA))
    lx = img.width - 10
    for text, code in legend:
        tw = d.textlength(text, font=fs)
        lx -= tw
        d.text((lx, 10), text, fill=(214, 214, 220), font=fs)
        lx -= 16
        d.rectangle([lx, 11, lx + 10, 21],
                    fill=RGB_NODATA if code == NO_DATA else RGB[code], outline=(96, 96, 104))
        lx -= 14

    d.rectangle([pad_l - 1, pad_t - 1, pad_l + W * scale, pad_t + H * scale],
                outline=(92, 94, 102))

    # ── 行方向刻度（纵截面 = y；横截面 = z）
    if axis != "y":
        lo, hi, pre = y0, y1, ""
    else:
        lo, hi, pre = hv0, hv1, "z="
    step = nice_step(hi - lo + 1, 10)
    v = ((lo + step - 1) // step) * step
    while v <= hi:
        py = pad_t + (int(pixel_rows(axis, np.array(v), y1, hv0)) * scale)
        if 0 <= py < img.height:
            for px in range(pad_l, pad_l + W * scale, 14):
                img.putpixel((px, py), (176, 176, 184))
            d.text((pad_l + W * scale + 5, py - 6), f"{pre}{v}", fill=(192, 192, 200), font=fs)
        v += step

    # ── 列方向刻度（横轴）
    cstep = nice_step(hu1 - hu0 + 1, 10)
    v = ((hu0 + cstep - 1) // cstep) * cstep
    while v <= hu1:
        px = pad_l + int(pixel_cols(np.array(v), hu0)) * scale
        if 0 <= px < img.width:
            for py in range(pad_t, pad_t + H * scale, 14):
                img.putpixel((px, py), (176, 176, 184))
            lab = str(v)
            d.text((px - d.textlength(lab, font=fs) / 2, pad_t + H * scale + 4),
                   lab, fill=(192, 192, 200), font=fs)
        v += cstep

    # ── 可选网格（每隔 grid_step 个世界格子一条）
    if grid_step > 0 and scale >= 1:
        for gx in range(((hu0 + grid_step - 1) // grid_step) * grid_step, hu1 + 1, grid_step):
            px = pad_l + int(pixel_cols(np.array(gx), hu0)) * scale
            for py in range(pad_t, pad_t + H * scale, 4):
                img.putpixel((px, py), (58, 60, 68))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    return img.size


# ──────────────────────────────────────────────────────────── 自检

def selftest() -> int:
    """手工造一段已知的 section，验解码 / 分类 / 坐标映射。

    为什么必须做：位打包、索引公式 `(y*16+z)*16+x`、y 轴方向这三样
    错了都不报错，只会"图看起来不对"，很难反查。拿已知答案的样本对一遍，
    再注入一个坏样本确认判据报得出来（否则等于没验）。
    """
    print("=== 地形剖面.py 自检 ===")
    bad = 0

    palette = [{"Name": "minecraft:air"}, {"Name": "minecraft:water"}, {"Name": "minecraft:stone"}]
    pts = {                                  # (x, y, z) -> palette 下标
        (3, 7, 5): 1, (15, 0, 5): 2, (0, 15, 5): 2, (9, 9, 5): 0,
        (1, 2, 11): 1, (14, 13, 11): 2, (3, 7, 11): 2,
    }
    sec = _mk_section(0, palette, pts)

    expect = {                               # (axis, fixed) -> {(行, 列): 期望上色码}
        ("z", 5):  {(7, 3): C_LIQ, (0, 15): C_SOLID, (15, 0): C_SOLID, (9, 9): C_AIR},
        ("z", 11): {(2, 1): C_LIQ, (13, 14): C_SOLID, (7, 3): C_SOLID},
        ("x", 3):  {(7, 5): C_LIQ, (7, 11): C_SOLID},
        ("y", 7):  {(5, 3): C_LIQ, (11, 3): C_SOLID, (9, 9): C_AIR},
    }
    for (axis, fixed), want in expect.items():
        got = section_slice(sec, axis, fixed)
        if got is None:
            print(f"  ✗ axis={axis} fixed={fixed}：解码返回空")
            bad += 1
            continue
        codes = got[0]
        for (r, c), exp in want.items():
            if int(codes[r, c]) != exp:
                print(f"  ✗ axis={axis} fixed={fixed} [{r},{c}] 期望 {exp}，实得 {int(codes[r, c])}")
                bad += 1

    # 坏样本：把 (3,7,5) 那格从 water 改成 stone，上面第一条断言必须不再成立。
    # （判据踩不中就说明这套自检是空的。）
    bad_pts = dict(pts)
    bad_pts[(3, 7, 5)] = 2
    got_bad = section_slice(_mk_section(0, palette, bad_pts), "z", 5)
    if got_bad is not None and int(got_bad[0][7, 3]) == C_LIQ:
        print("  ✗ 坏样本未被识别（判据太宽，等于没验）")
        bad += 1

    # 分类
    for name, exp in (("minecraft:air", C_AIR), ("minecraft:cave_air", C_AIR),
                      ("minecraft:void_air", C_AIR),
                      ("minecraft:water", C_LIQ), ("minecraft:lava", C_LIQ),
                      ("tconstruct:molten_iron", C_LIQ), ("create:honey", C_LIQ),
                      ("minecraft:stone", C_SOLID), ("minecraft:grass_block", C_SOLID),
                      ("minecraft:water_cauldron", C_SOLID), ("minecraft:honey_block", C_SOLID),
                      ("minecraft:water_bucket", C_SOLID), ("create:fluid_pipe", C_SOLID),
                      ("minecraft:lily_pad", C_SOLID)):
        if classify(name) != exp:
            print(f"  ✗ 分类错：{name} 期望 {exp}，实得 {classify(name)}")
            bad += 1

    # 坐标映射：纵截面第 0 行 = 最高处；横截面第 0 行 = 最小 z（北在上）
    if int(pixel_rows("z", np.array(100), 100, 0)) != 0:
        print("  ✗ 纵截面 y=y1 不在第 0 行（y 轴画反了）")
        bad += 1
    if int(pixel_rows("z", np.array(0), 100, 0)) != 100:
        print("  ✗ 纵截面 y=y0 不在最后一行")
        bad += 1
    if int(pixel_rows("y", np.array(-5), 0, -5)) != 0:
        print("  ✗ 横截面 z=z0 不在第 0 行")
        bad += 1

    # skip() 与 payload() 的指针一致性 —— 必须毫厘不差地停在字节流末尾
    blob = _mk_probe_nbt()
    r1 = NbtReader(blob)
    r1.num(">B")
    r1.string()
    try:
        r1.skip(TAG_COMPOUND)
    except Exception as e:
        print(f"  ✗ skip() 在全类型样本上抛错：{e!r}")
        bad += 1
    else:
        if r1.i != len(blob):
            print(f"  ✗ skip() 指针停在 {r1.i}，应为 {len(blob)}（差 {len(blob) - r1.i} 字节）")
            bad += 1
    try:
        r2 = NbtReader(blob)
        d = r2.root()
        if r2.i != len(blob):
            print(f"  ✗ payload() 指针停在 {r2.i}，应为 {len(blob)}")
            bad += 1
        elif d.get("lst", [{}])[0].get("Name") != "v0" or d.get("cmp", {}).get("inner") != 5:
            print(f"  ✗ payload() 解出的内容不对：{d}")
            bad += 1
    except Exception as e:
        print(f"  ✗ payload() 在全类型样本上抛错：{e!r}")
        bad += 1

    # skim_chunk 与完整解析必须拿到**同样多**的段（曾经少一半还不报错）
    try:
        fake = _mk_fake_chunk()
        st, secs = skim_chunk(fake, None)
        got = [s["Y"] for s in secs]
        ref = [s["Y"] for s in (NbtReader(fake).root().get("sections") or [])]
        if st != "minecraft:full":
            print(f"  ✗ skim_chunk 读到的 Status 不对：{st!r}")
            bad += 1
        if got != ref or got != [-5, -4, -3]:
            print(f"  ✗ skim_chunk 段数/段号不对：实得 {got}，应为 {ref}（经典症状：指针没归位）")
            bad += 1
    except Exception as e:
        print(f"  ✗ skim_chunk 在假区块上抛错：{e!r}")
        bad += 1

    # 截面轴 → 要读的区块数（这处参数错了只会"少读一大片"，图照样出得来）
    nz = len(nearby_chunks("z", 0, -560, 560, 0, 0))          # 1121 格宽 → 71 区块
    nx = len(nearby_chunks("x", 0, -400, 400, 0, 0))          # 横轴是 z → 51 区块
    ny = len(nearby_chunks("y", 64, -128, 128, -128, 128))    # 257×257 → 17×17
    if (nz, nx, ny) != (71, 51, 289):
        print(f"  ✗ nearby_chunks 区块数不对：z={nz}(应71) x={nx}(应51) y={ny}(应289)")
        bad += 1

    if bad:
        print(f"=== 自检失败：{bad} 项 ===")
        return 1
    print("  ✓ 段解码（紧凑/老格式）· 三色分类 · 坐标映射 全部通过，坏样本可识别")
    print("=== 自检通过 ===")
    return 0


def _nbt_str(s: str) -> bytes:
    b = s.encode("utf-8")
    return struct.pack(">H", len(b)) + b


def _mk_probe_nbt() -> bytes:
    """拼一个含**全部** NBT 标签类型的字节流，用来验 skip() 与 payload() 的指针一致。

    为什么非要有这条：skip() 少跳一个字节不会报错，只会让指针飘到某处才炸
    （实测症状是 "未知 NBT 标签 105"）。而这个 bug 只在**跳过用不到的大字段**时
    才走到 —— 也就是"快路径"，正常解析完全看不出来。必须拿全类型样本对一遍。
    """
    body = b""

    def fld(t, name, payload):
        nonlocal body
        body += struct.pack(">B", t) + _nbt_str(name) + payload

    fld(1, "b", struct.pack(">b", 7))
    fld(2, "s", struct.pack(">h", -300))
    fld(3, "i", struct.pack(">i", 123456))
    fld(4, "l", struct.pack(">q", 1 << 40))
    fld(5, "f", struct.pack(">f", 1.5))
    fld(6, "d", struct.pack(">d", 2.5))
    fld(7, "ba", struct.pack(">i", 5) + b"\x01\x02\x03\x04\x05")
    fld(8, "st", _nbt_str("hello 中文"))
    items = b""
    for k in range(3):
        items += struct.pack(">B", 8) + _nbt_str("Name") + _nbt_str(f"v{k}") + struct.pack(">B", 0)
    fld(9, "lst", struct.pack(">B", 10) + struct.pack(">i", 3) + items)
    fld(11, "ia", struct.pack(">i", 3) + struct.pack(">iii", 1, 2, 3))
    fld(12, "la", struct.pack(">i", 2) + struct.pack(">qq", 9, 10))
    fld(10, "cmp", struct.pack(">B", 3) + _nbt_str("inner") + struct.pack(">i", 5) + struct.pack(">B", 0))
    return struct.pack(">B", 10) + _nbt_str("") + body + struct.pack(">B", 0)


def _mk_fake_chunk() -> bytes:
    """造一个含 3 段的假区块 —— 专抓"段解析后指针没归位"这类静默 bug。

    每段都在 block_states **后面**再放一个字段（block_light），因为真正的
    section 就是这样（block_states / block_light / sky_light / biome）：
    只要回溯解析 block_states 后忘了把指针恢复，下一段就会从段中间开始读，
    结果是段数少一半而**不报任何错**。
    """
    def sec(y: int, names) -> bytes:
        bs = (struct.pack(">B", 9) + _nbt_str("palette")
              + struct.pack(">B", 10) + struct.pack(">i", len(names)))
        for n in names:
            bs += struct.pack(">B", 8) + _nbt_str("Name") + _nbt_str(n) + struct.pack(">B", 0)
        bs += struct.pack(">B", 12) + _nbt_str("data") + struct.pack(">i", 1) + struct.pack(">q", 0)
        body = struct.pack(">B", 1) + _nbt_str("Y") + struct.pack(">b", y)
        body += struct.pack(">B", 10) + _nbt_str("block_states") + bs + struct.pack(">B", 0)
        body += struct.pack(">B", 7) + _nbt_str("block_light") + struct.pack(">i", 3) + b"\x01\x02\x03"
        return body + struct.pack(">B", 0)

    secs = b"".join(sec(y, ("minecraft:stone", "minecraft:air")) for y in (-5, -4, -3))
    top = struct.pack(">B", 8) + _nbt_str("Status") + _nbt_str("minecraft:full")
    top += (struct.pack(">B", 9) + _nbt_str("sections")
            + struct.pack(">B", 10) + struct.pack(">i", 3) + secs)
    top += struct.pack(">B", 4) + _nbt_str("LastUpdate") + struct.pack(">q", 123)
    return struct.pack(">B", 10) + _nbt_str("") + top + struct.pack(">B", 0)


def _mk_section(sec_y: int, palette, pts: dict):
    """按 MC 的紧凑打包造一个假 section，喂给 section_slice 用。"""
    names = _pal_names(palette)
    bits = max(4, (len(names) - 1).bit_length())
    per = 64 // bits
    words = [0] * ((4096 + per - 1) // per)
    for (x, y, z), pi in pts.items():
        k = (y * 16 + z) * 16 + x
        words[k // per] |= pi << ((k % per) * bits)
    return {"Y": sec_y, "block_states": {"palette": palette, "data": words}}


# ──────────────────────────────────────────────────────────── 主流程

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="地形剖面.py", description="把存档切成横/纵截面图：方块=黑 液体=灰 空气=白",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("world", nargs="?", default="", help="存档根目录（含 level.dat 的那层）")
    ap.add_argument("--axis", choices=("x", "y", "z"), default="z",
                    help="截面法向轴：z/x=纵截面（剖面墙），y=横截面（俯视平面）")
    ap.add_argument("--at", type=int, default=0, help="截面所在的坐标（默认 0）")
    ap.add_argument("--x0", type=int, default=None, help="x 下限（axis=y/z 时是横轴）")
    ap.add_argument("--x1", type=int, default=None, help="x 上限")
    ap.add_argument("--z0", type=int, default=None, help="z 下限（axis=x/y 时是横轴）")
    ap.add_argument("--z1", type=int, default=None, help="z 上限")
    ap.add_argument("--span", type=int, default=128, help="以 0 为中心的半宽（默认 128）")
    ap.add_argument("--y0", type=int, default=None, help="纵轴下限（默认自动探世界全高）")
    ap.add_argument("--y1", type=int, default=None, help="纵轴上限")
    ap.add_argument("--scale", type=int, default=0, help="每格像素（0=自适应，默认）")
    ap.add_argument("-o", "--out", default="terrain_slice.png",
                    help="输出 png（默认写当前目录）")
    ap.add_argument("--dimension", default=None, help="维度名，如 minecraft:overworld")
    ap.add_argument("--region", default=None, help="直接指定 region 目录")
    ap.add_argument("--liquid-extra", default="",
                    help="额外当液体的关键字，逗号分隔（如 --liquid-extra 铜,brine）")
    ap.add_argument("--pure", action="store_true",
                    help="严格三色：无数据也画成白（放弃\"这里没数据\"的提示）")
    ap.add_argument("--grid", type=int, default=0, help="每隔 N 格画一条竖网格线")
    ap.add_argument("--no-fill-air", action="store_true",
                    help="严格模式：区块里没写出来的段仍算\"无数据\"（默认按空气处理，"
                         "因为 MC 会省略全空的段）")
    ap.add_argument("--bare", action="store_true",
                    help="只出纯截面图（无标题/图例/刻度/边距），1 像素=1 格；"
                         "程序读像素做判据时必须用它")
    ap.add_argument("--list-dims", action="store_true", help="只列维度，不画图")
    ap.add_argument("--selftest", action="store_true", help="自检，不读存档")
    a = ap.parse_args(argv)

    # 依赖检查放在 parse_args **之后** —— `--help` 归 argparse 管，不能被依赖拦住
    if np is None:
        sys.exit("[错误] 缺 numpy\n  · 装一下：pip install numpy\n  · 本脚本靠它向量化解码方块（纯 Python 逐格太慢）")
    if Image is None:
        sys.exit("[错误] 缺 Pillow\n  · 装一下：pip install pillow\n  · 本脚本靠它出图")

    if a.selftest:
        return selftest()
    if not a.world:
        ap.print_help()
        return 2

    root = Path(a.world)
    if not root.is_dir():
        fail(f"存档目录不存在：{root}",
             "· 应该是 <实例>/saves/<世界名>/ 或服务端下的 <目录>/world/",
             "· 认不准就找含 level.dat 的那层")

    if a.list_dims:
        dims = find_dims(root)
        if not dims:
            fail(f"在 {root} 里找不到 region 目录", "· 确认传的是世界目录（含 level.dat）")
        print(f"{root} 下有 {len(dims)} 个维度：")
        for n, p in dims:
            print(f"  {n:34s} {len(list(p.glob('*.mca'))):>4} 个区域文件   {p}")
        return 0

    dim, rdir = pick_region(root, a.dimension, a.region)

    # ── 范围
    if a.axis == "z":
        hu0 = a.x0 if a.x0 is not None else -a.span
        hu1 = a.x1 if a.x1 is not None else a.span
        hv0 = hv1 = a.at
    elif a.axis == "x":
        hu0 = a.z0 if a.z0 is not None else -a.span
        hu1 = a.z1 if a.z1 is not None else a.span
        hv0 = hv1 = a.at
    else:
        hu0 = a.x0 if a.x0 is not None else -a.span
        hu1 = a.x1 if a.x1 is not None else a.span
        hv0 = a.z0 if a.z0 is not None else -a.span
        hv1 = a.z1 if a.z1 is not None else a.span
    if hu0 > hu1:
        hu0, hu1 = hu1, hu0
    if hv0 > hv1:
        hv0, hv1 = hv1, hv0

    specs = nearby_chunks(a.axis, a.at, hu0, hu1, hv0, hv1)
    if not specs:
        fail("这个范围里一个区块都没有", "· 检查 --at / --span / --x0 --x1")

    # ── 纵轴范围
    if a.y0 is None or a.y1 is None:
        # 探测顺序：**离截面中心最近**的区块优先。
        # 边缘区块常常压根没生成（只 forceload 过中心），按坐标顺序试会白试一大片，
        # 最后得出"读不到任何区块"的假结论（实测横截面 span 400 就是这么挂的）。
        ccx, ccz = ((hu0 + hu1) // 2) >> 4, ((hv0 + hv1) // 2) >> 4
        cand = sorted(specs, key=lambda p: (p[0] - ccx) ** 2 + (p[1] - ccz) ** 2)
        probed = probe_y_range(rdir, cand)
        if probed is None:
            fail(f"在 {rdir} 里读不到任何区块，没法推算世界高度",
                 "· 区块可能还没生成（先起服务端 forceload 一趟）",
                 "· 或者手动给 --y0 / --y1")
        y0 = probed[0] if a.y0 is None else a.y0
        y1 = probed[1] if a.y1 is None else a.y1
    else:
        y0, y1 = a.y0, a.y1
    if y0 > y1:
        fail(f"--y0({y0}) 比 --y1({y1}) 大", "· y0 是低处、y1 是高处")

    W = hu1 - hu0 + 1
    H = (y1 - y0 + 1) if a.axis != "y" else (hv1 - hv0 + 1)

    if a.axis == "y" and not (y0 <= a.at <= y1):
        print(f"[警告] --at {a.at} 不在探测到的世界高度 [{y0},{y1}] 内 —— 这一层多半不存在，"
              f"图会是整片无数据")

    # ── 自适应缩放：宽高和总像素都要克制，否则一张图几十兆
    if a.scale > 0:
        scale = a.scale
    elif a.bare:
        # --bare 的语义是"1 像素 = 1 格"，供程序读像素 —— 不许被自适应缩放破坏
        # （否则用户按 1:1 算坐标会整体错开）
        scale = 1
    else:
        scale = 1
        for s in range(2, 9):
            if W * s <= 2400 and H * s <= 12000 and W * s * H * s <= 2_600_000:
                scale = s
        # 太窄的图放大一点才看得清
        if W * H * 4 <= 2_600_000 and W * 2 <= 2400 and H * 2 <= 12000:
            scale = max(scale, 2)

    liquid_extra = tuple(s.strip() for s in a.liquid_extra.split(",") if s.strip())

    axis_desc = (f"固定 {a.axis}={a.at}，横轴 x∈[{hu0},{hu1}]、纵轴 y∈[{y0},{y1}]"
                 if a.axis in ("x", "z") else
                 f"固定 y={a.at}，横轴 x∈[{hu0},{hu1}]、纵轴 z∈[{hv0},{hv1}]")
    print(f"存档    : {root}")
    print(f"维度    : {dim}")
    print(f"region  : {rdir}")
    print(f"截面    : {axis_desc}")
    print(f"图像    : {W}×{H} 格 × {scale} px/格 → {W * scale}×{H * scale} px")
    print(f"需读区块: {len(specs)} 个")

    # 段级裁剪：横截面只要含 --at 的那一段（252 段里 1 段），
    # 纵截面要 y 范围内的那些。裁在 NBT 解析层，省掉建对象的大头。
    skipped = [0]

    def want_sec(sy: int) -> bool:
        yb = sy * 16
        ok = (yb <= a.at <= yb + 15) if a.axis == "y" else (yb + 15 >= y0 and yb <= y1)
        if not ok:
            skipped[0] += 1
        return ok

    # ── 画布（255 = 无数据）
    canvas = np.full((H, W), NO_DATA, dtype=np.uint8)
    fill_air = not a.no_fill_air

    missing: list[str] = []
    n_chunks = n_sections = 0
    seen_liquid: set[str] = set()
    for cx, cz, data in iter_wanted_chunks(rdir, specs):
        if data is None:
            missing.append(f"({cx},{cz}) 在 region 文件里不存在（这块没生成过）")
            continue
        try:
            status, secs = skim_chunk(data, want_sec)
        except Exception as e:
            missing.append(f"({cx},{cz}) NBT 解析失败：{e!r}")
            continue
        if not is_full(status):
            # ⚠️ 没生成完的区块整列都是空气 —— 不排除会画成大片空腔，
            #    图和判据都"正常"，只有形状是假的。最阴的一类假象。
            missing.append(f"({cx},{cz}) Status={status}（没生成完）")
            continue
        n_chunks += 1
        fixed = a.at & 15
        # 这个区块在图上占的行/列范围 —— 用来把"区块没写出来的空段"补成空气。
        # ⚠️ 区分两件事：**区块不存在** = 无数据（橙，不可信）；
        #    **区块 full 但没这一截 section** = 真的空气（MC 会省略全空的段）。
        #    混为一谈会在图上留一堆橙斑，看着像"这里坏了"。
        if a.axis == "y":
            r_lo, r_hi = (cz << 4) - hv0, (cz << 4) + 15 - hv0
        else:
            r_lo, r_hi = 0, H - 1
        c_lo = (cx << 4) - hu0
        c_hi = c_lo + 15
        for sec in secs:
            ybase = sec["Y"] * 16
            got = section_slice(sec, a.axis, fixed, liquid_extra)
            if got is None:
                continue
            n_sections += 1
            codes, rc, cc = got
            # 行/列坐标的世界基准：纵截面的行是 y（加段基址），横截面的行是 z（加区块 z）
            if a.axis == "y":
                rows = pixel_rows("y", (cz << 4) + rc, y1, hv0)
                cols = pixel_cols((cx << 4) + cc, hu0)
            elif a.axis == "z":
                rows = pixel_rows("z", ybase + rc, y1, hv0)
                cols = pixel_cols((cx << 4) + cc, hu0)
            else:
                rows = pixel_rows("x", ybase + rc, y1, hv0)
                cols = pixel_cols((cz << 4) + cc, hu0)

            ri = np.where((rows[:, 0] >= 0) & (rows[:, 0] < H))[0]
            ci = np.where((cols[0, :] >= 0) & (cols[0, :] < W))[0]
            if ri.size == 0 or ci.size == 0:
                continue
            canvas[np.ix_(rows[ri, 0], cols[0, ci])] = codes[np.ix_(ri, ci)]

            pal = sec.get("block_states", {}).get("palette") or sec.get("Palette") or []
            for name in _pal_names(pal):
                if is_liquid(name, liquid_extra):
                    seen_liquid.add(name)

        if fill_air:
            r0, r1 = max(0, r_lo), min(H - 1, r_hi)
            c0, c1 = max(0, c_lo), min(W - 1, c_hi)
            if r1 >= r0 and c1 >= c0:
                blk = canvas[r0:r1 + 1, c0:c1 + 1]     # numpy 基本切片 = 视图，就地改
                blk[blk == NO_DATA] = C_AIR

    # ── 统计
    total = canvas.size
    cnt = {c: int((canvas == c).sum()) for c in (C_SOLID, C_LIQ, C_AIR)}
    n_nodata = int((canvas == NO_DATA).sum())
    nodata_pct = 100.0 * n_nodata / total

    def pc(n):
        return f"{100.0 * n / total:5.1f}%"

    print("\n--- 截面统计（按像素） ---")
    print(f"  方块  {cnt[C_SOLID]:>9}  {pc(cnt[C_SOLID])}")
    print(f"  液体  {cnt[C_LIQ]:>9}  {pc(cnt[C_LIQ])}")
    print(f"  空气  {cnt[C_AIR]:>9}  {pc(cnt[C_AIR])}")
    print(f"  无数据{n_nodata:>9}  {pc(n_nodata)}"
          + ("   ← ⚠️ 这段没数据，图上那片不是真的空" if n_nodata else ""))
    print(f"  扫到区块 {n_chunks}/{len(specs)}，段 {n_sections} 个"
          + (f"，不需要的段跳过 {skipped[0]} 个" if skipped[0] else ""))
    if seen_liquid:
        s = sorted(seen_liquid)
        print(f"  判为液体的方块（{len(s)} 种）: " + ", ".join(s[:10])
              + ("  …" if len(s) > 10 else ""))
        print("    （判错了加 --liquid-extra 补关键字；灰=液体）")
    else:
        print("  判为液体的方块: 这段里没有")

    # ── 出图
    axis_sym = "x" if a.axis in ("z", "y") else "z"
    title = (f"axis={a.axis} at={a.at}   {axis_sym}[{hu0},{hu1}] "
             + (f"y[{y0},{y1}]" if a.axis != "y" else f"z[{hv0},{hv1}]")
             + f"   {W}x{H} cells, {scale}px/cell"
             + ("   [pure 3-color]" if a.pure else ""))
    size = render(canvas, a.axis, title, hu0, hu1, hv0, hv1, y0, y1, scale,
                  Path(a.out), a.pure, a.grid, a.bare)
    print(f"\n[写出] {Path(a.out).resolve()}  ({size[0]}×{size[1]})")

    if missing:
        print(f"\n⚠️ {len(missing)} 处没有完整数据（图上画成{'白' if a.pure else '亮橙'}）:")
        for m in missing[:8]:
            print("   ", m)
        if len(missing) > 8:
            print(f"    ...还有 {len(missing) - 8} 处")
        print("  · 未加载的区块按空气画 = 假空腔。要么先 forceload 生成，"
              "要么把 --span 收到已生成的范围内")

    if n_chunks == 0:
        print("\n[失败] 一个区块都没读到 —— 不许当成\"没问题\"")
        return 1
    if nodata_pct > 5:
        print(f"\n[注意] 无数据占 {nodata_pct:.1f}% > 5%：这张图只有一部分可信")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

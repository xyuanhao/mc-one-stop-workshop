#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探地表高度 —— 验证「改世界生成」的 mod 到底有没有生效。

为什么需要它：
    `扫区域文件.py` 只能告诉你世界里出现过哪些方块，说不出「地表是几层台阶」。
    而自定义地形 mod（ChunkGenerator / 世界预设）最硬的判据就是**高度规律**：
      台阶地形 → 高度只落在少数几个档位，且两两差值都是 step 的整数倍；
      原版平滑地形 → 高度连续分布，随便取几列就能凑出各种差值。
    本脚本把「离散 or 连续」「差值是否都是 step 的倍数」直接算给你看。

两种数据源（二选一）：
    --rcon HOST:PORT:PASSWORD   在线探测。服务端要跑着；脚本会先 forceload 采样点区块。
                                给出真实 y，且能区分水面 / 地面。
    --mca  <region 目录>         离线直读存档 .mca 里的 Heightmaps，秒出整片统计。
                                高度是 9-bit 打包的原始值（带固定编码偏移），
                                因此只用于判「离散 / 连续」和档位间距，不做绝对 y 解读。

用法：
    # 离线（服务端没开也能跑；存档在 run/<world>/dimensions/<ns>/<dim>/region）
    python 探地表高度.py --mca "run/world/dimensions/minecraft/overworld/region" --step 6

    # 在线（拿真实 y）
    python 探地表高度.py --rcon 127.0.0.1:25575:devtest --points 0,0 40,0 0,40 --step 6

    # 台阶步长未知时先不传 --step，只看离散性；已知地形参数时传上，脚本会额外校验差值整除性
"""

import argparse
import gzip
import os
import struct
import sys
import zlib
from collections import Counter

TAG_END, TAG_BYTE, TAG_SHORT, TAG_INT, TAG_LONG = 0, 1, 2, 3, 4
TAG_FLOAT, TAG_DOUBLE, TAG_BYTE_ARRAY, TAG_STRING = 5, 6, 7, 8
TAG_LIST, TAG_COMPOUND, TAG_INT_ARRAY, TAG_LONG_ARRAY = 9, 10, 11, 12
MASK64 = (1 << 64) - 1


# ----------------------------------------------------------------------------
# 离线：直读 .mca
# ----------------------------------------------------------------------------
def iter_chunks(path):
    """逐个产出 (chunkX, chunkZ, 解压后的 NBT 字节)。表项 = 3 字节偏移 + 1 字节扇区数。"""
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
            if comp == 1:
                data = gzip.decompress(data)
            elif comp == 2:
                data = zlib.decompress(data)
            elif comp != 3:
                continue
            yield i % 32, i // 32, data


def parse_nbt(buf):
    """极简 NBT 解析：够读 Heightmaps 就行。返回根 compound。"""
    def primitive(fmt, i):
        return struct.unpack_from(fmt, buf, i)[0], i + struct.calcsize(fmt)

    def payload(t, i):
        if t == TAG_BYTE:
            return primitive(">b", i)
        if t == TAG_SHORT:
            return primitive(">h", i)
        if t == TAG_INT:
            return primitive(">i", i)
        if t == TAG_LONG:
            return primitive(">q", i)
        if t == TAG_FLOAT:
            return primitive(">f", i)
        if t == TAG_DOUBLE:
            return primitive(">d", i)
        if t == TAG_BYTE_ARRAY:
            n, i = primitive(">i", i)
            return buf[i:i + n], i + n
        if t == TAG_STRING:
            n, i = primitive(">H", i)
            return buf[i:i + n].decode("utf-8", "replace"), i + n
        if t == TAG_LIST:
            et, i = primitive(">b", i)
            n, i = primitive(">i", i)
            out = []
            for _ in range(n):
                v, i = payload(et, i)
                out.append(v)
            return out, i
        if t == TAG_COMPOUND:
            out = {}
            while True:
                tt, i = primitive(">b", i)
                if tt == TAG_END:
                    return out, i
                name, i = payload(TAG_STRING, i)
                v, i = payload(tt, i)
                out[name] = v
        if t == TAG_INT_ARRAY:
            n, i = primitive(">i", i)
            return list(struct.unpack_from(">%di" % n, buf, i)), i + 4 * n
        if t == TAG_LONG_ARRAY:
            n, i = primitive(">i", i)
            return list(struct.unpack_from(">%dq" % n, buf, i)), i + 8 * n
        raise ValueError("unknown tag %d" % t)

    root_type, _ = primitive(">b", 0)
    _, i = payload(TAG_STRING, 1)
    value, _ = payload(root_type, i)
    return value


def unpack_heights(longs, bits=9, count=256):
    """还原 Heightmaps 的 long 数组。

    ⚠️ 编码是 `SimpleBitStorage`：**每个 long 固定塞 `64 // bits` 个值，值不跨 long 边界**
    （9 bits → 7 个/ long，每 long 浪费 1 位，共 ceil(256/7)=37 个 long）。
    按「连续位流 + 跨边界拼接」解会得到一堆 (-4,-7,-13,-25…) 形状的垃圾值，
    而且长得还挺像离散分布，非常容易骗过判定逻辑 —— 这里踩过坑。
    """
    per_long = 64 // bits
    mask = (1 << bits) - 1
    out = []
    for idx in range(count):
        value = (longs[idx // per_long] & MASK64) >> ((idx % per_long) * bits) & mask
        if value >= (1 << (bits - 1)):
            value -= 1 << bits
        out.append(value)
    return out


def collect_from_mca(region_dir, field):
    hist = Counter()
    chunks = 0
    for name in sorted(os.listdir(region_dir)):
        if not name.endswith(".mca"):
            continue
        for cx, cz, data in iter_chunks(os.path.join(region_dir, name)):
            try:
                root = parse_nbt(data)
            except Exception:
                continue
            heights = (root.get("Heightmaps") or {}).get(field)
            if not heights:
                continue
            chunks += 1
            for h in unpack_heights(heights):
                hist[h] += 1
    return chunks, hist


# ----------------------------------------------------------------------------
# 在线：RCON 探测
# ----------------------------------------------------------------------------
def probe_column(conn, rcon, x, z, lo, hi):
    """返回 (最高非空气 y, 地面 y)。地面 = 往下第一个既非空气也非水的方块。"""
    def is_air(y):
        out = rcon.send(conn, "execute if block %d %d %d minecraft:air" % (x, y, z))
        return "passed" in out.lower()

    def is_water(y):
        out = rcon.send(conn, "execute if block %d %d %d minecraft:water" % (x, y, z))
        return "passed" in out.lower()

    top = None
    for y in range(hi, lo - 1, -1):
        if is_air(y):
            continue
        if top is None:
            top = y
        if not is_water(y):
            return top, y
    return top, None


# ----------------------------------------------------------------------------
# 判定
# ----------------------------------------------------------------------------
def report(hist, step, label, min_y=None):
    total = sum(hist.values())
    levels = sorted(hist)
    # 主档位：占比 ≥ 1% 的档位。树叶、水面、结构会把高度顶上去或压下去，
    # 全量判定会被这些离群值带偏（实测：22 个档位里 18 个是树顶/水面）。
    main = [lv for lv in levels if hist[lv] * 100.0 / total >= 1.0]

    def as_y(raw):
        return raw if min_y is None else raw + min_y - 1

    print("\n%s：%d 列采样，出现 %d 个高度档位（其中主档位 %d 个，标 ★）"
          % (label, total, len(levels), len(main)))
    for lv in levels:
        star = "★" if lv in main else " "
        y = as_y(lv)
        ytxt = "" if min_y is None else "  y=%-5d" % y
        bar = "#" * max(1, int(50 * hist[lv] / total))
        print("  %s %-7d%s %6d 列 %5.1f%%  %s" % (star, lv, ytxt, hist[lv],
                                                  100.0 * hist[lv] / total, bar))

    judge = main if len(main) >= 2 else levels
    if len(judge) < 2:
        print("\n判定：只出现一个高度 → 可能是纯平面地形，或者采样范围太小/区块没加载 ❓")
        return
    span = judge[-1] - judge[0] + 1
    ratio = len(judge) / float(span)
    print("\n按主档位判定：档位 %d 个 / 覆盖跨度 %d（密度 %.2f）" % (len(judge), span, ratio))
    if ratio < 0.6:
        print("判定：**离散台阶地形** —— 高度只落在少数档位，量化生效 ✅")
    else:
        print("判定：**连续平滑地形** —— 高度几乎每个整数都有，量化大概率没生效 ❌")

    if step:
        gaps = sorted({judge[i + 1] - judge[i] for i in range(len(judge) - 1)})
        diffs = sorted({abs(a - b) for i, a in enumerate(judge) for b in judge[i + 1:]})
        mods = sorted({d % step for d in diffs if d > 0})
        print("相邻主档位间距：%s" % gaps)
        print("两两差值对 %d 取余：%s" % (step, mods))
        if mods == [0]:
            print("→ 所有主档位差都是 %d 的整数倍，与 terrace_height 一致 ✅" % step)
        elif 0 in mods:
            print("→ 多数差值能被 %d 整除（少数偏离来自水/树/结构）" % step)
        else:
            print("→ 差值无法被 %d 整除：参数可能不匹配，或地形不是这一套" % step)


def main():
    ap = argparse.ArgumentParser(description="探地表高度：验证自定义世界生成是否生效")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--rcon", metavar="HOST:PORT:PASSWORD", help="在线探测，如 127.0.0.1:25575:devtest")
    src.add_argument("--mca", metavar="REGION_DIR", help="离线直读 region 目录")
    ap.add_argument("--points", nargs="+", default=["0,0", "40,0", "0,40", "80,0", "0,80"],
                    help="在线模式采样点，格式 x,z（默认 5 个点）")
    ap.add_argument("--step", type=int, default=0, help="台阶步长（如 terrace_height=6）；不填则只判离散性")
    ap.add_argument("--lo", type=int, default=20, help="在线模式扫描下界 y（默认 20）")
    ap.add_argument("--hi", type=int, default=130, help="在线模式扫描上界 y（默认 130）")
    ap.add_argument("--field", default="MOTION_BLOCKING",
                    help="离线模式取哪张高度图（MOTION_BLOCKING / WORLD_SURFACE / OCEAN_FLOOR）")
    ap.add_argument("--min-y", type=int, default=-64,
                    help="离线模式的维度最低 y（主世界 -64），用来把 raw 换算成真实 y")
    ap.add_argument("--no-forceload", action="store_true", help="在线模式不自动 forceload")
    opts = ap.parse_args()

    if opts.mca:
        if not os.path.isdir(opts.mca):
            raise SystemExit("找不到 region 目录：%s" % opts.mca)
        chunks, hist = collect_from_mca(opts.mca, opts.field)
        if not hist:
            raise SystemExit("没读到高度数据 —— 先让服务端跑一次（或游戏里存档），确保区块已写盘")
        print("扫描区块 %d 个（高度图字段 %s）" % (chunks, opts.field))
        print("raw → y 换算：y = raw + (min_y) - 1，min_y 取 %d（不对就用 --min-y 指定）" % opts.min_y)
        report(hist, opts.step, "离线档位分布", opts.min_y)
        return

    host, port, password = opts.rcon.split(":")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import rcon
    conn = rcon.connect(host, int(port), password)
    print("RCON 已连接 %s:%s" % (host, port))

    heights = Counter()
    if not opts.no_forceload:
        for pt in opts.points:
            x, z = (int(v) for v in pt.split(","))
            rcon.send(conn, "forceload add %d %d" % (x - 16, z - 16))
        print("已 forceload 采样点附近区块")

    print("\n逐列探测（y=%d 往下）：" % opts.hi)
    for pt in opts.points:
        x, z = (int(v) for v in pt.split(","))
        top, ground = probe_column(conn, rcon, x, z, opts.lo, opts.hi)
        if ground is None:
            print("   (%6d,%6d) → 整列都是空气/水，未找到地面" % (x, z))
            continue
        wet = top is not None and top > ground
        print("   (%6d,%6d) → 地面 y=%-5d 最高非空气 y=%-5d %s"
              % (x, z, ground, top, "（水下）" if wet else ""))
        heights[ground] += 1

    if sum(heights.values()) < 2:
        raise SystemExit("有效采样点太少，无法判定")
    report(heights, opts.step, "在线实测地面高度")
    if not opts.no_forceload:
        rcon.send(conn, "forceload remove all")


if __name__ == "__main__":
    main()

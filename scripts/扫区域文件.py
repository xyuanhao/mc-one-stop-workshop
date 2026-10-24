# -*- coding: utf-8 -*-
"""非破坏性地统计「世界存档里真的生成过哪些方块」——直接读 .mca 区域文件。

为什么不进游戏用 `/fill ... air replace <方块>` 数？
    那个命令是**把方块换成空气**，它数完就把东西删了（数一遍少一遍），
    同一片区域第二次统计必然是 0，会得出完全错误的结论。本脚本只读文件。

用法：
    # 给存档根目录 → 自动扫所有维度
    python 扫区域文件.py <存档目录> <关键字> [<关键字> ...]
    # 或只给某个 region 目录
    python 扫区域文件.py <.../region> otherworld:ashen_brick
例：
    python 扫区域文件.py run/world otherworld:ashen_brick otherworld:riftstone minecraft:bell

输出的「区块」坐标 = 全局区块坐标（region*32+c），可直接对上 /forceload 的范围。
"""
import gzip
import os
import re
import struct
import sys
import zlib
from collections import defaultdict


def iter_chunks(path):
    """逐个产出 (区块x, 区块z, 解压后的NBT字节)。表项是 3字节偏移 + 1字节扇区数。"""
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
                try:
                    data = gzip.decompress(data)
                except OSError:
                    continue
            elif comp == 2:
                try:
                    data = zlib.decompress(data)
                except zlib.error:
                    continue
            elif comp != 3:
                continue
            yield i % 32, i // 32, data


def find_region_dirs(root):
    """支持三种布局：<world>/region、<world>/dimensions/<ns>/<dim>/region、直接给 region。"""
    if os.path.basename(os.path.normpath(root)) == "region":
        return [("", os.path.normpath(root))]
    out = []
    base = os.path.join(root, "region")
    if os.path.isdir(base):
        out.append(("minecraft:overworld", base))
    dims = os.path.join(root, "dimensions")
    if os.path.isdir(dims):
        for ns in sorted(os.listdir(dims)):
            for dim in sorted(os.listdir(os.path.join(dims, ns))):
                r = os.path.join(dims, ns, dim, "region")
                if os.path.isdir(r):
                    out.append((f"{ns}:{dim}", r))
    return out


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__)
        return 2
    root, keys = args[0], args[1:]
    if not os.path.exists(root):
        print(f"[错误] 路径不存在：{root}")
        print("  · 传存档根目录（saves/<世界名>/，里面有 region/ 或 dimensions/）")
        print("  · 或直接传某个 region 目录（.../saves/<世界名>/region）")
        return 2
    if not find_region_dirs(root):
        print(f"[错误] 在 {root} 里找不到 region 目录")
        print("  · 确认传的是**存档世界目录**（里面直接有 region/），不是 saves/ 也不要再往下一层")
        print("  · 全新世界还没生成过区块时也会没有 region/ —— 先进游戏跑一圈")
        return 2
    kb = [(k, k.encode()) for k in keys]

    total = defaultdict(int)
    hits = defaultdict(lambda: defaultdict(set))
    chunks_seen = 0
    for dim, rdir in find_region_dirs(root):
        for name in sorted(os.listdir(rdir)):
            if not name.endswith(".mca"):
                continue
            nums = re.findall(r"-?\d+", name)
            if len(nums) < 2:
                continue
            rx, rz = int(nums[0]), int(nums[1])
            for cx, cz, data in iter_chunks(os.path.join(rdir, name)):
                chunks_seen += 1
                for k, b in kb:
                    if b in data:
                        hits[k][(dim, rx, rz)].add((rx * 32 + cx, rz * 32 + cz))
                        total[k] += 1

    print(f"共扫描 {chunks_seen} 个已生成区块")
    if chunks_seen == 0:
        print("  （一个区块都没扫到 —— 整张地图可能还没生成，或 region 文件是空的）")
        return 2
    for k in keys:
        print(f"  {k}: 命中 {total.get(k, 0)} 个区块")
        for (dim, rx, rz), cs in sorted(hits.get(k, {}).items()):
            sample = sorted(cs)[:8]
            print(f"     {dim} 区域({rx},{rz}) 共 {len(cs)} 块：{sample}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

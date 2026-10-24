#!/usr/bin/env python
"""世界生成体检 —— 直接解析存档 .mca，按深度段统计方块构成，定位「世界生成坏了」。

为什么需要这个（本脚本的由来）：
    本项目的 Mod 曾把主世界高度从 -64~320 拉到 -2032~2031。
    改坏之后，**构建成功、数据包加载成功、Mixin 注入成功、高度自检全绿、
    区块 Status 走到 full、矿脉统计正常** —— 所有既有判据全部通过，
    但世界已经毁成一片 111 格深的巨海。
    只有直接看存档里的方块，才发现「y>=64 全是空气、y<63 全是水、零石头」。

比 RCON 探测强在哪：
    · 不用起服务端、不用生成区块、不会超时
    · 全量统计而非单点采样（RCON 点探在稀疏特征上会得出错误结论）
    · 秒级出结果

用法：
    python 世界生成体检.py <region目录或存档根目录> [--all]
    python 世界生成体检.py D:/saves/MyWorld
    python 世界生成体检.py D:/saves/MyWorld/dimensions/minecraft/overworld
    python 世界生成体检.py D:/saves/MyWorld --all      # 扫全部 254 个 section

默认只看 sectionY ∈ [-20, 20]（y ∈ [-320, 335]），覆盖地表 + 上方缓冲区，
够判断「世界像不像一个世界」。要查深层（如扩高世界的最底部）加 `--all`；
此时纯空气的 section 会被折叠成一行汇总，不会刷屏。

⭐ 读结果的两个关键
    1. 先看 Status 分布，把「未生成完的区块」剔掉再算比例 ——
       空壳区块贡献的 pure-air 会稀释一切，不扣除会把「100% 水」算成「20% 水」。
    2. 按 sectionY 分段看，别看总量 —— 水面高度这类信息只有分段才读得出来。
"""
import os
import sys
import zlib
import gzip
import struct
import io
import collections

try:
    import nbtlib
except ImportError:
    # 🔴 不在 import 阶段 sys.exit —— 那会让 `--help` / skill 自检冒烟一起挂掉
    #    （2026-10-01 修：自检报「--help 既没输出用法也没报错」就是这个原因）。
    #    真正要解析 .mca 时才报缺依赖（见 iter_chunk_nbt）。
    nbtlib = None

# 走到这些 Status 的区块才真正有地形（26.x 的顺序）
TERRAIN_DONE = {'minecraft:full', 'minecraft:spawn', 'minecraft:heightmaps',
                'minecraft:light', 'minecraft:initialize_light',
                'minecraft:features', 'minecraft:liquid_carvers', 'minecraft:carvers'}

# 只看这个 sectionY 区间（y = secY*16 ~ secY*16+15）。覆盖原版世界 + 上方缓冲。
SEC_LO, SEC_HI = -20, 20


def find_region_dirs(root):
    """在存档里定位所有 region 目录。"""
    if os.path.isdir(os.path.join(root, 'region')):
        return [os.path.join(root, 'region')]
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        if os.path.basename(dirpath) == 'region' and \
                any(f.endswith('.mca') for f in filenames):
            out.append(dirpath)
    return out


def iter_chunk_nbt(region_dir):
    """逐个产出 (文件名, chunk NBT)。"""
    if nbtlib is None:                  # 真要用到才报缺口（import 阶段不拦，见文件头）
        sys.exit('需要 nbtlib：pip install nbtlib')
    for fn in sorted(os.listdir(region_dir)):
        if not fn.endswith('.mca'):
            continue
        p = os.path.join(region_dir, fn)
        with open(p, 'rb') as f:
            header = f.read(4096)
        for idx in range(1024):
            entry = header[idx * 4: idx * 4 + 4]
            if len(entry) < 4:
                continue
            offset = int.from_bytes(entry[:3], 'big')
            count = entry[3]
            if offset == 0 or count == 0:
                continue
            with open(p, 'rb') as f:
                f.seek(offset * 4096)
                blob = f.read(count * 4096)
            if len(blob) < 5:
                continue
            length = struct.unpack('>I', blob[:4])[0]
            comp = blob[4]
            data = blob[5: 5 + length - 1]
            try:
                if comp == 1:
                    data = gzip.decompress(data)
                elif comp == 2:
                    data = zlib.decompress(data)
                elif comp != 3:
                    continue
                yield fn, nbtlib.File.parse(io.BytesIO(data))
            except Exception:
                continue


def unpack_section(bs, count=4096):
    """把 section 的 block_states 解成方块名列表。

    1.16+ 紧凑打包：bits = max(4, ceil(log2(palette_size)))，每个 long 装
    64//bits 个条目、条目不跨 long 边界。
    """
    names = [str(dict(e).get('Name', '?')).replace('minecraft:', '')
             for e in bs['palette']]
    if 'data' not in bs:
        return [names[0]] * count
    longs = bs['data']
    bits = max(4, (len(names) - 1).bit_length())
    per = 64 // bits
    mask = (1 << bits) - 1
    out = []
    for i in range(count):
        out.append(names[(int(longs[i // per]) >> ((i % per) * bits)) & mask])
    return out


AIRISH = ('__total__', 'air', 'water', 'lava', 'cave_air', 'void_air')


def diagnose(per_y, n_terrain):
    """整体性判据。返回 (problems, warnings, notes)。

    ⭐ 抽成独立函数是为了能被自检脚本覆盖 ——
    新增检测脚本必须双向验证：坏样本要报得出、好样本零误杀。
    见同目录 `世界生成体检_自检.py`。

    这些判据补的是「既有判据全绿但世界已经毁了」这个盲区：
    构建成功、数据包加载成功、区块 Status 走到 full 都不代表世界是正常的。

    problems / warnings 的区分标准（双向验证实测调出来的）：
      · problems —— 有区分度，正常世界不可能出现
      · warnings —— 可能是异常也可能正常，必须人工确认。
        「y>=64 零固体」就属于这类：**纯海洋存档同样满足**，
        单独看分不出「地表被移除」和「本来就是海」，所以不能当硬判据。
    """
    problems, warnings, notes = [], [], []
    if n_terrain == 0:
        problems.append('没有区块走到地形阶段，无法判断（世界刚创建或被中断？）')
        return problems, warnings, notes

    solid_total = 0
    surface_solid = 0
    deep_water_layers = []
    for sy, c in per_y.items():
        denom = c['__total__'] * 4096
        if denom == 0:
            continue
        for k, v in c.items():
            if k in AIRISH:
                continue
            solid_total += v
            if sy >= 4:                     # sectionY 4 => y >= 64
                surface_solid += v
        # 地下的水：y < 0 的层里水占比过半。
        #
        # 这是本轮事故最有区分度的签名：
        # 正常世界里地下以岩石为主，水是零星的洞穴水潭（占比个位数）；
        # 水过半意味着「本该是岩石的地方变成了空腔，然后被填成水」——
        # 也就是地形密度算错了。
        # 海洋存档不会触发它：海水在 y>=40，地下仍然是石头。
        if sy < 0 and c.get('water', 0) / denom > 0.5:
            deep_water_layers.append(sy)

    if solid_total == 0:
        problems.append('已生成区块里一个固体方块都没有 —— 地形完全没生成')
    if deep_water_layers:
        lo, hi = min(deep_water_layers), max(deep_water_layers)
        problems.append(
            f'y<0 的地下出现大规模水体：sectionY {lo}~{hi}'
            f'（y {lo*16}~{hi*16+15}）水占比 >50% —— '
            f'正常世界地下以岩石为主，这通常意味着地形密度算错、空腔被填成水')
    if surface_solid == 0:
        warnings.append('y>=64 区间零固体 —— 地表可能整个消失'
                        '（纯海洋存档也会这样，请结合实际地形确认）')
    if not problems and not warnings:
        notes.append(f'固体方块合计 {solid_total:,}，其中 y>=64 有 {surface_solid:,}')
    return problems, warnings, notes


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2                        # 参数问题 = 2
    if args[0] in ('-h', '--help'):
        print(__doc__)
        return 0
    root = args[0]
    scan_all = '--all' in args[1:]
    sec_lo, sec_hi = (-127, 126) if scan_all else (SEC_LO, SEC_HI)
    dirs = find_region_dirs(root)
    if not dirs:
        print(f'[错误] 在 {root} 下没找到含 .mca 的 region 目录')
        print('  · 传存档根目录（saves/<世界名>/，里面有 region/ 或 dimensions/）')
        print('  · 或直接传某个 region 目录（.../saves/<世界名>/region）')
        return 2
    problems_total = 0

    for region_dir in dirs:
        print(f'\n{"="*72}\n区域目录: {region_dir}\n{"="*72}')
        status = collections.Counter()
        per_y = collections.defaultdict(collections.Counter)
        n_total = 0
        n_terrain = 0

        for fn, nbt in iter_chunk_nbt(region_dir):
            n_total += 1
            st = str(nbt.get('Status', '?'))
            status[st] += 1
            done = st in TERRAIN_DONE
            if not done:
                # ⚠️ 空壳区块**也带 254 个全 air 的 sections**（实测），
                # 所以必须在这里就 continue —— 一旦把它的 section 计入
                # `__total__`，分母会虚高数倍，所有百分比一起被稀释
                # （实测：1089 而非 225，把「100% 水」算成「20.7% 水」）。
                continue
            n_terrain += 1
            for sec in nbt.get('sections', []):
                sy = int(sec['Y'])
                if not (sec_lo <= sy <= sec_hi):
                    continue
                per_y[sy]['__total__'] += 1
                for b in unpack_section(sec['block_states']):
                    per_y[sy][b] += 1
        print(f'\n区块总数 {n_total}，其中走到地形阶段的 {n_terrain}'
              f'（其余是空壳，统计时已剔除）')
        print('Status 分布:', dict(status))
        if scan_all:
            print(f'扫描区间: sectionY {sec_lo} ~ {sec_hi}'
                  f'（y {sec_lo*16} ~ {sec_hi*16+15}，全深度）')
        else:
            print(f'扫描区间: sectionY {sec_lo} ~ {sec_hi}'
                  f'（y {sec_lo*16} ~ {sec_hi*16+15}；加 --all 可扫全深度）')

        if not per_y:
            print('\n[!] 没有任何 section 落在统计区间内')
            continue

        print(f'\n{"secY":>5s} {"y 区间":>13s} {"区块":>5s}  '
              f'{"solid":>7s} {"water":>7s} {"lava":>7s} {"air":>7s}   主要方块')
        print('-' * 96)
        air_only = 0
        for sy in sorted(per_y):
            c = per_y[sy]
            done_here = c['__total__']
            if done_here == 0:
                continue
            solid = sum(v for k, v in c.items()
                        if k not in ('__total__', 'air', 'water', 'lava',
                                     'cave_air', 'void_air'))
            water = c.get('water', 0)
            lava = c.get('lava', 0)
            air = c.get('air', 0) + c.get('cave_air', 0) + c.get('void_air', 0)
            denom = done_here * 4096
            # 纯空气的段（天空、未到达的高度）折叠成一行汇总，
            # 否则全深度扫描会刷出 200+ 行噪声。
            # 判据不受影响：`diagnose()` 独立按总量算。
            if air == denom:
                air_only += 1
                continue
            top = ', '.join(f'{k}:{v}' for k, v in c.most_common(3)
                            if k != '__total__')
            print(f'{sy:>5d} {f"[{sy*16},{sy*16+15}]":>13s} {done_here:>5d}  '
                  f'{solid/denom*100:6.1f}% {water/denom*100:6.1f}% '
                  f'{lava/denom*100:6.1f}% {air/denom*100:6.1f}%   {top}')
        if air_only:
            print(f'  （另有 {air_only} 个 section 是纯空气，已折叠省略）')

        # ---- 自动判据：只在「有地形」的区块上算 ----
        print('\n' + '-' * 72)
        print('自动判据：')
        probs, warns, notes = diagnose(per_y, n_terrain)
        for n in notes:
            print(f'  [OK] {n}')
        for w in warns:
            print(f'  [??] {w}')
        for p in probs:
            print(f'  [!!] {p}')
        if not probs:
            print('  [OK] 未发现整体性异常')
        if per_y and diagnose(per_y, n_terrain)[0]:
            problems_total += 1

    # 退出码约定：0 = 干净 / 1 = 发现问题 / 2 = 参数问题
    return 1 if problems_total else 0


if __name__ == '__main__':
    sys.exit(main())

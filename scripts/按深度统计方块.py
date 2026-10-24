"""
直接解析区域文件（.mca）统计方块类型分布。

为什么不用 RCON 逐点探测：
- 单点 `execute if block` 命中率太低，矿脉是稀疏的，扫几百个点基本都是石头
- 真正的判据是「这一段地层里到底有没有矿石方块」，那就是数方块

为什么必须按用途分层验证（记忆里的教训）：
- "文件存在" ⊂ "JSON 可解析" ⊂ "被 biome 引用" ⊂ "真的出现在存档里"
- 前三层我都验过了，这一层是最后一层，也是最硬的一层

做法：读 region 文件 → 解压每个 chunk 的 NBT → 遍历 sections →
按 palette 下标还原方块名 → 统计每种方块出现次数。

用法：
    python 按深度统计方块.py <存档根目录或 region 目录> [关键字...]

    # 默认关注矿石；也可传自定义关键字
    python 按深度统计方块.py run/world
    python 按深度统计方块.py run/world otherworld:ashen_brick
"""

import os, sys, struct, zlib, glob, json, collections

# 由命令行给，不再硬编码 —— 硬编码会让"传了参数但脚本扫的是别处"这种静默错误发生
REGION_DIR = None


# ---------- 最小 NBT 解析 ----------
class NBT:
    """只解析读取所需的 tag，够用就行。"""

    def __init__(self, data):
        self.d = data
        self.i = 0

    def u1(self):
        v = self.d[self.i]; self.i += 1; return v

    def i1(self):
        v = self.d[self.i]; self.i += 1
        return v - 256 if v > 127 else v

    def i2(self):
        v = struct.unpack_from('>h', self.d, self.i)[0]; self.i += 2; return v

    def u2(self):
        v = struct.unpack_from('>H', self.d, self.i)[0]; self.i += 2; return v

    def i4(self):
        v = struct.unpack_from('>i', self.d, self.i)[0]; self.i += 4; return v

    def i8(self):
        v = struct.unpack_from('>q', self.d, self.i)[0]; self.i += 8; return v

    def f4(self):
        v = struct.unpack_from('>f', self.d, self.i)[0]; self.i += 4; return v

    def f8(self):
        v = struct.unpack_from('>d', self.d, self.i)[0]; self.i += 8; return v

    def s(self):
        n = self.u2()
        v = self.d[self.i:self.i + n].decode('utf-8', 'replace')
        self.i += n
        return v

    def payload(self, t):
        if t == 1: return self.i1()
        if t == 2: return self.i2()
        if t == 3: return self.i4()
        if t == 4: return self.i8()
        if t == 5: return self.f4()
        if t == 6: return self.f8()
        if t == 7:
            n = self.i4()
            v = self.d[self.i:self.i + n]
            self.i += n
            return v
        if t == 8: return self.s()
        if t == 9:
            et = self.u1()
            n = self.i4()
            return [self.payload(et) for _ in range(n)]
        if t == 10:
            out = {}
            while True:
                tt = self.u1()
                if tt == 0:
                    break
                name = self.s()
                out[name] = self.payload(tt)
            return out
        if t == 11:
            n = self.i4()
            v = list(struct.unpack_from(f'>{n}i', self.d, self.i))
            self.i += 4 * n
            return v
        if t == 12:
            n = self.i4()
            v = list(struct.unpack_from(f'>{n}q', self.d, self.i))
            self.i += 8 * n
            return v
        raise ValueError(f'未知 tag 类型 {t}')


def parse_nbt(data):
    n = NBT(data)
    t = n.u1()
    if t != 10:
        raise ValueError('顶层不是 compound')
    n.s()  # 根名，通常为空
    return n.payload(10)


# ---------- 方块调色板 ----------
def block_name(entry):
    """palette 项可能是 'minecraft:stone' 或 {'Name': '...'} 两种写法。"""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, bytes):
        return entry.decode('utf-8', 'replace')
    if isinstance(entry, dict):
        nm = entry.get('Name', '?')
        return nm.decode() if isinstance(nm, bytes) else nm
    return str(entry)


def count_region(path):
    """返回 (方块名计数, {(sectionY, 方块名): 数量}, 段集合)。

    按段（16 格高）分别统计，这样才能看出「哪些深度有矿」——
    总计数只能证明"有矿"，不能证明"深到某处还有矿"。
    """
    counter = collections.Counter()
    per_section = collections.Counter()
    sections_y = []
    with open(path, 'rb') as f:
        header = f.read(8192)
        if len(header) < 8192:
            return counter, per_section, sections_y
        for ci in range(1024):
            raw = struct.unpack_from('>I', header, ci * 4)[0]
            off = raw >> 8
            cnt = raw & 0xFF
            if off == 0 or cnt == 0:
                continue
            pos = off * 4096
            f.seek(pos)
            hdr = f.read(5)
            if len(hdr) < 5:
                continue
            ln, comp = struct.unpack('>IB', hdr)
            blob = f.read(ln - 1)
            if comp == 2:
                try:
                    blob = zlib.decompress(blob)
                except zlib.error:
                    continue
            elif comp != 1:
                continue
            try:
                root = parse_nbt(blob)
            except Exception:
                continue
            sections = root.get('sections') or root.get('Sections') or []
            for sec in sections:
                if not isinstance(sec, dict):
                    continue
                ytag = sec.get('Y')
                sy = ytag if isinstance(ytag, int) else None
                if sy is not None:
                    sections_y.append(sy)
                bs = sec.get('block_states') or {}
                pal = bs.get('palette') or sec.get('Palette')
                if not pal:
                    continue
                names = [block_name(e) for e in pal]
                if len(names) == 1:
                    n = 4096
                    counter[names[0]] += n
                    if sy is not None:
                        per_section[(sy, names[0])] += n
                    continue
                dl = bs.get('data')
                if dl is None:
                    continue
                bits = max(4, (len(names) - 1).bit_length())
                per = 64 // bits
                total_longs = (4096 + per - 1) // per
                idxs = []
                for li in range(min(total_longs, len(dl))):
                    v = dl[li] & 0xFFFFFFFFFFFFFFFF
                    for k in range(per):
                        idxs.append((v >> (k * bits)) & ((1 << bits) - 1))
                for v in idxs[:4096]:
                    if v < len(names):
                        nm = names[v]
                        counter[nm] += 1
                        if sy is not None:
                            per_section[(sy, nm)] += 1
    return counter, per_section, sections_y


def find_region_dirs(root):
    """给存档根目录或 region 目录，返回所有 region 目录。

    找不到必须明确报错 —— 静默返回空列表会让脚本"扫了 0 个文件还说通过"，
    这是以前踩过的假阴性形态之一。
    """
    if not os.path.exists(root):
        raise FileNotFoundError(root)
    if os.path.isdir(os.path.join(root, 'region')):
        return [os.path.join(root, 'region')]
    found = []
    for dirpath, dirnames, _ in os.walk(root):
        if os.path.basename(dirpath) == 'region':
            found.append(dirpath)
            dirnames[:] = []
    if not found:
        # 也许直接给的就是 region 目录本身
        if glob.glob(os.path.join(root, '*.mca')):
            return [root]
    return found


def main():
    global REGION_DIR
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2                        # 参数问题 = 2
    if args[0] in ('-h', '--help'):
        print(__doc__)
        return 0
    root = args[0]
    user_keys = args[1:]

    try:
        dirs = find_region_dirs(root)
    except FileNotFoundError:
        print(f'[ERROR] 路径不存在: {root}')
        return 1
    if not dirs:
        print(f'[ERROR] 在 {root} 里找不到 region 目录（也没直接看到 .mca）')
        print('  · 传存档世界目录（里面有 region/ 或 dimensions/）')
        print('  · 或直接传 .../region 目录')
        return 1

    files = []
    for d in dirs:
        files.extend(sorted(glob.glob(os.path.join(d, '*.mca'))))
    if not files:
        print(f'[ERROR] 找到 region 目录但里面没有 .mca 文件: {dirs}')
        return 1
    print(f'扫描 {len(files)} 个区域文件（来自 {len(dirs)} 个 region 目录）')

    total = collections.Counter()
    per_sec = collections.Counter()
    all_ys = set()
    for p in files:
        c, ps, ys = count_region(p)
        total.update(c)
        per_sec.update(ps)
        all_ys.update(ys)
        print(f'  {os.path.basename(p)}: {sum(c.values()):>9,} 方块, {len(ys)} 个段')

    print(f'\n总方块数: {sum(total.values()):,}')
    print(f'段 y 范围: {min(all_ys) if all_ys else "?"} ~ {max(all_ys) if all_ys else "?"}')

    # 判据：必须真的出现目标方块
    ore_keywords = user_keys or [
        'coal_ore', 'copper_ore', 'iron_ore', 'gold_ore', 'redstone_ore',
        'lapis_ore', 'diamond_ore', 'emerald_ore', 'ancient_debris',
    ]
    print('\n--- 矿石统计（总计） ---')
    ore_total = 0
    for k, v in sorted(total.items(), key=lambda x: -x[1]):
        if any(o in k for o in ore_keywords):
            print(f'  {k:45s} {v:>6,}')
            ore_total += v
    if ore_total == 0:
        print('  (无)')
    print(f'  矿石方块合计: {ore_total:,}')

    print('\n--- 出现最多的 15 种方块 ---')
    for k, v in total.most_common(15):
        print(f'  {k:45s} {v:>9,}')

    # 按段（每段 16 格高）看矿石分布 —— 这才是"不管多深都有矿"的判据
    print('\n--- 矿石按深度分布（段=16格） ---')
    ore_secs = collections.defaultdict(lambda: collections.Counter())
    for (sy, nm), v in per_sec.items():
        if any(o in nm for o in ore_keywords):
            ore_secs[sy][nm] += v

    ys_sorted = sorted(ore_secs)
    if ys_sorted:
        print(f'  含矿石的段: {len(ys_sorted)} 个，段 y 从 {ys_sorted[0]} 到 {ys_sorted[-1]}')
        print(f'  对应世界 y: {ys_sorted[0]*16} ~ {ys_sorted[-1]*16+15}')
        print('  抽样（最深 5 段 / 最浅 3 段）:')
        picks = ys_sorted[:3] + ys_sorted[-5:]
        for sy in picks:
            ores = ore_secs[sy]
            top = ', '.join(f'{k.split(":")[-1]}×{v}' for k, v in ores.most_common(3))
            print(f'    段 {sy:>5} (y {sy*16:>7} ~ {sy*16+15:>7}): {sum(ores.values()):>6} 个矿  [{top}]')
    else:
        print('  (无)')

    # 落到用途层的判据
    print('\n=== 判定 ===')
    has_deepslate = any('deepslate' == k or k.endswith(':deepslate') for k in total)
    deepest_ore_y = ys_sorted[0] * 16 if ys_sorted else None
    print(f'  深层地形(deepslate)存在: {"是" if has_deepslate else "否"}')
    print(f'  矿石真的生成: {"是" if ore_total > 0 else "否 ← 失败"}')
    if deepest_ore_y is not None:
        # 世界底部是 -2032，落在 -2000 以下才算"深到最底也有矿"
        deep_ok = deepest_ore_y <= -1900
        print(f'  最深处矿石 y={deepest_ore_y}（要求 ≤ -1900）: {"是" if deep_ok else "否 ← 失败"}')
    ok = has_deepslate and ore_total > 0 and (deepest_ore_y is not None and deepest_ore_y <= -1900)
    return 0 if ok else 2


if __name__ == '__main__':
    sys.exit(main())

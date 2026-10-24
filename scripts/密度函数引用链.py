#!/usr/bin/env python
"""密度函数引用链审计 —— 改世界生成前，先把「谁引用了谁」画清楚。

⭐ 为什么需要这个（本脚本的由来）
    本项目的 Mod 改了 `overworld/depth.json` 的一个 y 梯度。
    改完之后所有既有判据全绿，但世界变成了一片 111 格深的巨海。
    反查时才发现：`depth` 被 `sloped_cheese` 引用、`sloped_cheese` 又被
    `final_density` 和 `preliminary_surface_level` 引用，
    而后者直接决定含水层怎么填水 —— 一整条污染链没人看见。

    更坑的是：**第一版审计脚本报「noise_router.depth 没变」**。
    因为 `noise_router` 的字段值可以写成**纯字符串引用**：

        "depth": "minecraft:overworld/depth"      ← 引用，不是内联

    `Either<DensityFunction, ResourceKey>` 的语法糖。
    只按 JSON 内容比对的脚本，会把「引用串相同」判成「这个字段没动」，
    而被引用的文件内容早被改了。
    → 本脚本把两种形态都展开，递归追到底。

用法：
    python 密度函数引用链.py <resources目录> [minecraft.jar 或 versions/26.1 目录]

    python 密度函数引用链.py ./src/main/resources
    python 密度函数引用链.py ./src/main/resources <jar路径>

不传 jar 时，只做「resources 内部的引用关系」分析（也能用）。
"""
import os
import sys
import json
import zipfile
import collections

DENSITY_DIR = 'worldgen/density_function'


class JarSource:
    """从 jar 里读原版数据。"""

    def __init__(self, jar_path):
        self.z = zipfile.ZipFile(jar_path)
        self.names = set(self.z.namelist())

    def read(self, rel):
        if rel not in self.names:
            return None
        return json.loads(self.z.read(rel).decode('utf-8'))


class DirSource:
    """从目录里读（用于没有 jar 的情况）。"""

    def __init__(self, root):
        self.root = root

    def read(self, rel):
        p = os.path.join(self.root, rel.replace('/', os.sep))
        if not os.path.isfile(p):
            return None
        with open(p, encoding='utf-8') as f:
            return json.load(f)


def collect_override_paths(res_root):
    """收集 resources 里所有 worldgen JSON 的相对路径。"""
    out = set()
    for dirpath, dirnames, filenames in os.walk(res_root):
        for fn in filenames:
            if not fn.endswith('.json'):
                continue
            p = os.path.join(dirpath, fn).replace(os.sep, '/')
            i = p.find('/data/')
            if i >= 0:
                out.add(p[i + 1:])
    return out


def refs_of(node, acc):
    """收集节点里所有密度函数引用。

    两种形态都要认：
      · {"type": "minecraft:reference", "id": "..."}     对象式
      · "minecraft:overworld/depth"                      纯字符串式
    字符串式必须靠「含冒号且像资源路径」来识别，否则会把
    "minecraft:noise" 这类 type 值也当成引用。
    """
    if isinstance(node, dict):
        if node.get('type') == 'minecraft:reference':
            acc.append(node.get('id'))
        for k, v in node.items():
            if k == 'type':
                continue
            refs_of(v, acc)
    elif isinstance(node, list):
        for x in node:
            refs_of(x, acc)
    elif isinstance(node, str):
        if ':' in node and '/' in node:
            acc.append(node)


def rid_to_jar_path(rid):
    ns, _, path = rid.partition(':')
    return f'data/{ns}/{DENSITY_DIR}/{path}.json'


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2                        # 参数问题 = 2
    if args[0] in ('-h', '--help'):
        print(__doc__)
        return 0
    res_root = args[0]
    jar = args[1] if len(args) > 1 else None

    src = JarSource(jar) if jar else None
    overridden = collect_override_paths(res_root)
    if not overridden:
        print(f'[错误] {res_root} 下没找到 data/**/*.json')
        return 2

    print('=' * 76)
    print(f'resources: {res_root}')
    if jar:
        print(f'原版 jar:  {jar}')
    print('=' * 76)

    # ---------- 1. 列出被覆盖的造世界文件 ----------
    wg = sorted(p for p in overridden if 'worldgen' in p)
    print(f'\n[1] 被覆盖的 worldgen 文件（{len(wg)} 个）')
    for p in wg:
        print('   ', p)

    # ---------- 2. noise_router 各字段的引用 ----------
    ns_rel = 'data/minecraft/worldgen/noise_settings/overworld.json'
    ns_ours = os.path.join(res_root, ns_rel.replace('/', os.sep))
    if not os.path.isfile(ns_ours):
        print('\n[2] 没找到自定义的 noise_settings/overworld.json，跳过')
        return

    with open(ns_ours, encoding='utf-8') as f:
        ours = json.load(f)

    print('\n[2] noise_router 各字段引用了谁（★ = 引用了被我们改写的文件）')
    dirty_map = collections.defaultdict(list)
    for k, v in sorted(ours.get('noise_router', {}).items()):
        acc = []
        refs_of(v, acc)
        if not acc:
            print(f'   {k:36s} (纯内联，无外部引用)')
            continue
        hits = []
        for r in sorted(set(acc)):
            jp = rid_to_jar_path(r)
            if jp in overridden:
                hits.append(r)
                dirty_map[jp].append(f'noise_router.{k}')
        flag = f'   ★ {sorted(set(hits))}' if hits else ''
        print(f'   {k:36s} {sorted(set(acc))}{flag}')

    # ---------- 3. 建引用图（正向 + 反向） ----------
    #
    # ⭐ 反向图才是审计的核心 —— 改了某个文件后，需要知道「谁会被我影响」。
    # 本轮事故正是栽在这：改了 depth.json，直接后果只是 biome 参数，
    # 但它经 sloped_cheese 一路传到 final_density / preliminary_surface_level，
    # 最终炸在含水层填水上。只列「直接引用」看不出这条链。
    all_rids = set()
    if src:
        for n in src.names:
            if DENSITY_DIR in n and n.endswith('.json'):
                path = n.split(f'data/minecraft/{DENSITY_DIR}/')[-1][:-5]
                all_rids.add(f'minecraft:{path}')

    graph = {}
    for rid in all_rids:
        doc = src.read(rid_to_jar_path(rid))
        if doc is None:
            continue
        acc = []
        refs_of(doc, acc)
        graph[rid] = set(acc)

    dirty_rids = set()
    for p in wg:
        if DENSITY_DIR in p:
            dirty_rids.add('minecraft:' + p.split(DENSITY_DIR + '/')[-1][:-5])

    # 反向边：ref -> {引用了它的}
    rev = collections.defaultdict(set)
    for a, bs in graph.items():
        for b in bs:
            rev[b].add(a)

    print('\n[3] 上游影响面：我改了这个文件，谁会受牵连（反向 BFS）')
    print('    注：density_function 再往上的消费者是 noise_settings 的字段名'
          '（不是资源文件），这里追不到 —— 见 [4]')
    if not src:
        print('   （需要提供原版 jar 才能算）')
    else:
        for d in sorted(dirty_rids):
            print(f'\n   ★ {d}   （已被改写）')
            seen, frontier, level = set(), [d], 0
            while frontier:
                level += 1
                nxt = []
                for cur in frontier:
                    for up in sorted(rev.get(cur, ())):
                        if up in seen or up == d:
                            continue
                        seen.add(up)
                        nxt.append(up)
                        jar_p = rid_to_jar_path(up)
                        tag = ' ★也被改写' if up in dirty_rids else ''
                        print(f'      {"  " * (level - 1)}← L{level} {up}{tag}')
                frontier = nxt
                if level > 6:
                    break
            if not seen:
                print('      （没有上游引用）')

    # ---------- 4. 顶层消费者 ----------
    print('\n[4] noise_router 字段的「是否间接依赖被改写文件」')
    ns_rids = {}
    for k, v in sorted(ours.get('noise_router', {}).items()):
        acc = []
        refs_of(v, acc)
        ns_rids[k] = set(acc)

    # 传递闭包：从每个 router 字段的引用出发，能到达哪些 rid
    def closure(rids):
        out, stack = set(), list(rids)
        while stack:
            cur = stack.pop()
            if cur in out:
                continue
            out.add(cur)
            stack.extend(graph.get(cur, ()))
        return out

    for k, rids in ns_rids.items():
        touched = sorted(closure(rids) & dirty_rids)
        if touched:
            print(f'   {k:36s} ★ 间接依赖被改写文件: {touched}')
    print('   （未列出的字段不受本次改写影响）')

    # ---------- 5. biome source 侧 ----------
    print('\n[5] 提醒：density_function 还可能被 biome source 引用')
    print('    （如 overworld/depth 同时喂给 multi_noise_biome_source_parameter_list）')
    if src:
        for d in sorted(dirty_rids):
            hits = []
            for n in src.names:
                if not n.endswith('.json'):
                    continue
                try:
                    txt = src.z.read(n).decode('utf-8')
                except Exception:
                    continue
                if d in txt:
                    hits.append(n)
            print(f'\n    {d}')
            for h in sorted(hits):
                mark = ' ★被覆盖' if h in overridden else ''
                print(f'        {h}{mark}')


if __name__ == '__main__':
    sys.exit(main())

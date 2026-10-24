# -*- coding: utf-8 -*-
"""从原版客户端 jar 的方块材质里自动提取「方块名 → 平均色」，
生成 block_colors.py 给 世界预览.py 兜底。

从此预览不再把 smooth_quartz / dark_oak_stairs / calcite 这类
内置表没写的方块一律画成灰的 —— 看不见真实效果等于白渲染。

用法：python 生成方块配色.py [--jar <客户端jar>] [-o block_colors.py]
"""
import os, re, io, sys, zipfile, argparse, json
from collections import defaultdict

DEF_JAR = os.path.expanduser(
    "~/.gradle/caches/neoformruntime/artifacts/minecraft_26.1_client.jar")
OUT_DEF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "block_colors.py")

# 楼梯/台阶/门…这些方块没有自己的材质，借用基础方块的
DECO_SUFFIX = ("_stairs", "_slab", "_wall", "_fence_gate", "_fence", "_door",
               "_trapdoor", "_button", "_pressure_plate", "_carpet", "_pane",
               "_sign", "_hanging_sign", "_bars", "_grindstone", "_anvil")


# 名字和材质对不上的特例（都是实测查过的）
SPECIAL = {
    "smooth_quartz": "quartz_block_bottom",
    "smooth_quartz_stairs": "quartz_block_bottom",
    "smooth_quartz_slab": "quartz_block_bottom",
    "quartz_block": "quartz_block_side",
    "quartz_stairs": "quartz_block_side",
    "quartz_slab": "quartz_block_side",
    "quartz_pillar": "quartz_pillar",
    "smooth_stone": "smooth_stone",
    "grass_block": "grass_block_top",
    "podzol": "podzol_top",
    "mycelium": "mycelium",
    "dirt_path": "dirt_path_top",
    "farmland": "farmland",
    "oak_leaves": "oak_leaves",
    "spruce_leaves": "spruce_leaves",
    "birch_leaves": "birch_leaves",
    "water": "water_still",
    "lava": "lava_still",
    "iron_bars": "iron_bars",
    "cauldron": "cauldron_side",
    "water_cauldron": "cauldron_side",
    "glass_pane": "glass",
    "short_grass": "short_grass",
    "tall_grass_top": "tall_grass_top",
    "stonecutter": "stonecutter_saw",
    "grindstone": "grindstone_side",
    "smithing_table": "smithing_table_side",
    "cartography_table": "cartography_table_side1",
    "fletching_table": "fletching_table_side",
    "loom": "loom_side",
    "composter": "composter_side",
    "barrel": "barrel_side",
    "lectern": "lectern_sides",
    "brewing_stand": "brewing_stand_base",
    "bell": "bell_body",
    "anvil": "anvil_top",
    "campfire": "campfire_log_lit",
    "torch": "torch",
    "lantern": "lantern",
    "soul_lantern": "soul_lantern",
    "hay_block": "hay_block_side",
    "bookshelf": "bookshelf",
    "crafting_table": "crafting_table_front",
    "furnace": "furnace_front_on",
    "blast_furnace": "blast_furnace_front_on",
    "smoker": "smoker_front_on",
    "chest": "chest_side",
    "trapped_chest": "trapped_chest_side",
    "ladder": "ladder",
    "scaffolding": "scaffolding_side",
}


def variants(name):
    """给出方块名可能对应的材质名候选（按优先级）"""
    out = []
    if name in SPECIAL:
        out.append(SPECIAL[name])
    out.append(name)
    for suf in DECO_SUFFIX:
        if name.endswith(suf):
            b = name[:-len(suf)]
            out += [b, b + "s", b + "_bricks", b + "_planks", b + "_log",
                    b + "_block", b + "_stone"]
    # 常见特例
    if name.startswith("smooth_") and name.endswith("s"):
        out.append(name[:-1])
    if name.endswith("_tile_stairs") or name.endswith("_tiles_stairs"):
        out.append(name.replace("_stairs", "s"))
    return out


def avg_color(png_bytes):
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    except Exception:
        return None
    px = list(im.getdata())
    tot = [0, 0, 0]
    n = 0
    for r, g, b, a in px:
        if a < 40:
            continue
        tot[0] += r; tot[1] += g; tot[2] += b
        n += 1
    if n < 8:
        return None
    return (tot[0] // n, tot[1] // n, tot[2] // n)


def main():
    ap = argparse.ArgumentParser(
        description="从原版客户端 jar 提取「方块名 → 平均色」，生成 block_colors.py")
    ap.add_argument("--jar", default=DEF_JAR,
                    help="原版客户端 jar（默认 %s）" % DEF_JAR)
    ap.add_argument("-o", "--out", default=OUT_DEF)
    a = ap.parse_args()

    # ⚠️ jar 不在给的是「路径不存在的默认值」时的表现 —— 曾经直接 zipfile 抛 OSError，
    #    看起来像脚本坏了。改成明确报错 + 退出码 2（自检.py 也要求无参调用必须体面退出）。
    if not os.path.isfile(a.jar):
        print("找不到客户端 jar：%s" % a.jar)
        print("用法：python 生成方块配色.py --jar <原版客户端 jar> [-o block_colors.py]")
        print("提示：本机 gradle 缓存可能在别的盘（如 D:\\AI\\gradle\\caches\\neoformruntime\\artifacts\\）")
        return 2

    z = zipfile.ZipFile(a.jar)
    tex = {}                       # 材质名 → 平均色
    for n in z.namelist():
        if not (n.startswith("assets/minecraft/textures/block/") and n.endswith(".png")):
            continue
        tname = n[len("assets/minecraft/textures/block/"):-4]
        c = avg_color(z.read(n))
        if c:
            tex[tname] = c
    print("提取到 %d 个方块材质" % len(tex))

    blocks = set()
    for n in z.namelist():
        if n.startswith("assets/minecraft/blockstates/") and n.endswith(".json"):
            blocks.add(n[len("assets/minecraft/blockstates/"):-5])
    print("原版方块 %d 个" % len(blocks))

    hit = miss = 0
    table = {}
    for b in sorted(blocks):
        for cand in variants(b):
            if cand in tex:
                c = tex[cand]
                table[b] = (c, tuple(max(0, int(v * 0.86)) for v in c))
                hit += 1
                break
        else:
            miss += 1
    print("配色命中 %d / 缺失 %d" % (hit, miss))

    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        f.write("# -*- coding: utf-8 -*-\n")
        f.write('"""自动生成 —— 由 生成方块配色.py 从原版客户端材质提取，请勿手改。"""\n\n')
        f.write("BLOCK_COLORS = {\n")
        for k in sorted(table):
            top, side = table[k]
            f.write('    "minecraft:%s": (%s, %s),\n' % (k, top, side))
        f.write("}\n")
    print("已写出 %s" % a.out)


if __name__ == "__main__":
    sys.exit(main())

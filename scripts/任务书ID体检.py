#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""任务书ID体检.py —— FTB Quests 任务书 item id 的**存在性**检查与修复。

## 为什么需要（两次实战翻车）

**第一次（2026-09-18 上午）—— 崩服**：玩家一进服就崩：

    net.minecraft.ResourceLocationException:
      Non [a-z0-9_.-] character in namespace of location: deep_Aether:clorite_pillar
        at dev.ftb.mods.ftbquests.item.MissingItem.readItem(MissingItem.java:31)
        at dev.ftb.mods.ftbquests.quest.ServerQuestFile.load(...)
        at dev.ftb.mods.ftbquests.FTBQuestsEventHandler.serverStarted(...)

FTB Quests 在 `ServerStartedEvent` 里读任务书，`MissingItem.readItem` 会
`new ResourceLocation(id)` —— **语法非法直接抛异常**，不降级。整个服务端 tick loop 崩。

**第二次（2026-09-18 晚上）—— 一堆紫黑问号**：服务端不崩了，但任务书里
7 个图标显示成 `?`（FTB 的「未知物品」兜底）。这些 id **语法合法、命名空间也存在**，
所以第一次的检查全放行了 —— 但**物品本身不存在**。

## 三类错，判据逐级收紧（缺一不可）

| 级别 | 类型 | 例子 | 检测依据 | 后果 |
|---|---|---|---|---|
| 1 | **语法非法** | `deep_Aether:clorite_pillar` | ns ∉ `[a-z0-9_.-]+` | `new ResourceLocation` **抛异常 → 崩服** |
| 2 | **命名空间不存在** | `held:two_handed` | ns ∉ jar 内 `assets/`+`data/` 一级目录 | 不崩，紫黑块 |
| 3 | **物品不存在** ★ | `mekanism:steel_ingot`（真名 `ingot_steel`） | 该 id 在 jar 的 `models/item/`+`blockstates/`+`data/*/recipe(s)/` 里都找不到 | 不崩，**紫黑问号** |

> ★ 第 3 类是最隐蔽的 —— 前两级全过。**根因是「从 lang 文件推 id」这个方法本身不可靠**：
> mod 的 lang key 顺序/命名习惯**经常和真实注册名不一致**，实测踩到的模式：
>
> | lang key | 推出来的伪 id | 真实注册名 | 错法 |
> |---|---|---|---|
> | `item.mekanism.steel_ingot` | `mekanism:steel_ingot` | `mekanism:ingot_steel` | **词序颠倒** |
> | `block.toms_storage.ts.crafting_terminal` | `toms_storage:ts` | `toms_storage:crafting_terminal` | 前缀没剥 |
> | `item.runes.rune_pouch` | `runes:rune_pouch` | `runes:small_rune_pouch` | 缺档位后缀 |
> | `item.theurgy.alchemical_sulfur` | `theurgy:alchemical_sulfur` | `theurgy:alchemical_sulfur_iron` | 缺材料后缀 |
> | `item.simplyswords.ActiveDefenceSwordItem` | `simplyswords:activedefencesworditem` | `simplyswords:iron_longsword` | **类名当 id** |
> | （作者遗留 lang）| `deep_aether:adibium_block` | **无此物，jar 内 0 文件** | lang 有键但内容被删 |
>
> **结论：权威源只能是 jar 里的资源文件，不能是 lang。**

## 权威源优先级（按可信度）

1. `assets/<ns>/models/item/<id>.json`      → 物品（最准）
2. `assets/<ns>/blockstates/<id>.json`      → 方块
3. `data/<ns>/recipe*/<id>.json`、`loot_table*/blocks/<id>.json` → 补充（少数物品无模型）
4. `data/<ns>/worldgen/structure/<id>.json` → 结构
5. `assets/<ns>/lang/en_us.json`            → **仅用于兜底报错信息，不作为 id 来源**

## 用法

  python 任务书ID体检.py "<实例目录>"                    # 只报告
  python 任务书ID体检.py "<实例目录>" --apply             # 落地修复
  python 任务书ID体检.py "<实例目录>" --rebuild-index      # 用 jar 重建权威 id 索引
  python 任务书ID体检.py --mods <mods> --quests <ftbquests>
  python 任务书ID体检.py "<实例>" --replace bad:item=good:item   # 手工指定替代

退出码：0 = 全通过 / 1 = 有问题 / 2 = 参数或路径问题
"""
import argparse
import json
import re
import sys
import zipfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LEGAL_NS = re.compile(r"^[a-z0-9_.-]+$")
LEGAL_PATH = re.compile(r"^[a-z0-9_./-]+$")
# 只匹配字符串字面量里的 id，避免碰到 description 里的普通文字
ID_LIT = re.compile(r'"([A-Za-z0-9_.\-]+:[A-Za-z0-9_./\-:]+)"')

# 已知「lang key 误收 / 注册名对不上」的替代表。可用 --replace 覆盖。
KNOWN_FIX = {
    "held:two_handed": "minecraft:iron_sword",              # Better Combat tooltip key
    "toms_storage:ts": "toms_storage:ts.crafting_terminal",  # ts. 是**注册名的一部分**，不能剥
    "theurgy:alchemical_sulfur": "theurgy:alchemical_sulfur_iron",  # 该物品有材料后缀
    "deep_aether:adibium_block": "deep_aether:skyjade_block",  # jar 内 0 文件（作者遗留 lang）
    "deep_aether:a_moring_wish_enchanting": "deep_aether:music_disc_a_morning_wish",  # 拼错
    "mmorpg:currency": "mmorpg:relic",                      # currency 是内部路径，非注册物品
    "mmorpg:empty_favor": "mmorpg:relic",                   # favor 是内部 UI 图标，非注册物品
    "psi:_standard_cad": "psi:cad",                          # `_` 前缀 = 内部预设模型
    "evilcraft:blood_wand_cap": "evilcraft:vein_sword",      # blood_wand_cap = 扫帚帽部件，非武器
    "simplyswords:activedefencesworditem": "simplyswords:iron_longsword",  # 类名被当 id
    "simplyswords:adamantite_longsword": "simplyswords:mythicmetals_compat/adamantite/adamantite_claymore",
    "runes:rune_pouch": "runes:small_rune_pouch",            # 只有 small/medium/large
    "mekanism:steel_ingot": "mekanism:ingot_steel",          # 词序颠倒
    "evilcraft:blood_orb": "evilcraft:blood_orb_empty",      # 只有 empty/filled 两态
    "bloodmagic:advance_cutting_fluid_sigil": "bloodmagic:advancedcuttingfluid",  # 真名无下划线
    "cataclysm:abandoned": "cataclysm:abyss_eye",            # 结构名，非物品
    "deeperdarker:ancient_temple_apex": "deeperdarker:ancient_vase",  # 结构名，非物品
    "enigmaticlegacy:3d": "enigmaticlegacy:the_cube",        # 模型子目录名被当 id
    "enigmaticlegacy:3d/animal_guidebook_3d": "enigmaticlegacy:animal_guidebook",  # 模型变体
    # 2026-10-01 玩家「物品缺失」实战新增（都可 --apply 自动修）
    "explorerscompass:explorerscompass_00": "explorerscompass:explorerscompass",  # `_00` 是模型变体编号
    "naturescompass:naturescompass_00": "naturescompass:naturescompass",
    "minecraft:nether_star_block": "minecraft:beacon",       # 原版无此方块，信标是最接近的
}


# ---------------------------------------------------------------------------
# 原版物品池（2026-10-01 新增）
# ---------------------------------------------------------------------------
# 🔴 旧版对 `minecraft:` 前缀**无脑放行**，理由写的是"原版由游戏本体保证，jar 里按定义没有"。
#    这个假设是错的 —— **原版物品照样会改名、会删**：
#      · `minecraft:fire_resistance_potion`  1.20.5 起药水统一成 `potion` + `potion_contents`
#        组件，专用物品 id 已删；
#      · `minecraft:nether_star_block`        原版**从来没有**这个方块。
#    两个都被写进了任务书 → FTB 解析不了 → 塞成 `ftbquests:missing_item` 占位写回文件，
#    玩家打开任务书看到的就是「有很多物品缺失」。
#
# 后来者注意：这类 bug **整个判据链都绿**（icon 有模型、ns 存在、服务端照常 Done），
# 唯一现形的地方就是游戏里的一行占位提示 —— 所以必须在**静态检查**里拦住。
VANILLA: set[str] = set()
VANILLA_LOADED = False

# class 常量池注册索引（下界集：命中 = 一定是注册物品；不命中 = 不一定）
# 用途：**兜底放行**「真物品但资源判据看不清」的情况，见 exists_in_index 末尾。
REGISTERED: set[str] = set()


def find_vanilla_jar(inst: Path, explicit: str | None = None) -> Path | None:
    """找原版 patched jar（`minecraft-server-patched-<ver>.jar`）。

    它是**唯一**能拿到原版 `assets/minecraft/lang/en_us.json` 的地方 ——
    整合包实例自己的 `versions/<名>/<名>.jar` 是**合并包**，里面查不到原版语言文件。
    """
    if explicit:
        p = Path(explicit)
        return p if p.is_file() else None
    rels = ("libraries/net/neoforged/minecraft-server-patched",
            "server/libraries/net/neoforged/minecraft-server-patched")
    for base in (Path.cwd(), Path.cwd().parent, inst, inst.parent):
        for rel in rels:
            d = base / rel
            if d.is_dir():
                hits = sorted(d.glob("*/minecraft-server-patched-*.jar"))
                if hits:
                    return hits[-1]
    return None


def build_vanilla_index(jar: Path | None, verbose: bool = False) -> set[str]:
    """从原版 jar 的语言文件建「真实存在的原版物品/方块」裸名集合。

    🔴 取 key 时要**跳过带点号的**：原版 lang 里
       `item.minecraft.potion` = Potion（真物品）
       `item.minecraft.potion.effect.fire_resistance` = 药水效果显示名（**不是物品**）
       不跳的话 `potion.effect.fire_resistance` 这种垃圾名会进池，反而放行坏 id。
    """
    global VANILLA, VANILLA_LOADED
    if jar is None:
        VANILLA, VANILLA_LOADED = set(), False
        return VANILLA
    try:
        z = zipfile.ZipFile(jar)
        raw = z.read("assets/minecraft/lang/en_us.json").decode("utf-8", "replace")
        data = json.loads(raw)
    except Exception as e:
        if verbose:
            print(f"  [原版池] 读取失败 {jar}: {e}")
        VANILLA, VANILLA_LOADED = set(), False
        return VANILLA

    pool = set()
    for k in data:
        for pre in ("item.minecraft.", "block.minecraft."):
            if k.startswith(pre):
                name = k[len(pre):]
                if "." not in name:          # 跳过 `potion.effect.xxx` 这类子 key
                    pool.add(name)
    VANILLA, VANILLA_LOADED = pool, True
    if verbose:
        print(f"  [原版池] {jar.name} → {len(pool)} 个原版物品/方块")
    return pool


def is_legal(i: str) -> bool:
    if i.count(":") != 1:
        return False
    n, _, p = i.partition(":")
    return bool(LEGAL_NS.match(n) and LEGAL_PATH.match(p))


def build_registered_index(mods_dir: Path, verbose=False) -> dict:
    """扫 jar 的 **class 常量池**，收集「真的被注册过的物品 id」。

    🔴🔴 2026-09-18 第四轮玩家反馈（第三次修图标问题）后新增的第 ⑥ 层判据。

    为什么必须再深一层：
      `models/item/<id>.json` 存在 **不代表** id 是注册物品。模组作者会把
      「内部素材」也放进 `models/item/` —— 手工画的预设、部件、UI 图标。
      它们有模型、有贴图（部分还有 lang 键），但**没有 Item 注册**，
      FTB 拿它当 icon 就是**紫黑问号**，且日志不报任何错。

    可靠判据是 **class 常量池**：任何注册物品都必须走
      `ITEMS.register("xxx", ...)` / `new Item(...)` 之类调用，
      id 字符串**必然以 UTF-8 常量形式出现在某个 .class 里**。
      内部素材只在 JSON/贴图层面存在，class 里搜不到。

    实测分离度（正/负样本 100% 正确）：
      mmorpg:empty_favor      ✗ 内部 UI 图标（favor 数值）   → 不在常量池
      mmorpg:tutorial_book    ✗ 有 lang 键但未注册          → 不在常量池
      mmorpg:relic / map / omen / prefix  ✓                → 在常量池
      psi:_standard_cad       ✗ 内部预设                    → 不在常量池
      psi:cad / cad_battery_basic  ✓                       → 在常量池
      evilcraft:blood_wand_cap ✗ 扫帚帽**部件**（有 lang 键！）→ 不在常量池
      evilcraft:vein_sword / broom / dark_gem  ✓           → 在常量池

    反例警示：**不要用 lang 键当判据**。BloodMagic 439 个顶层模型里
    221 个（50%）正常物品没有 lang 键（它的键用显示名而非注册名），
    用 lang 判会大面积误杀。

    返回 ns → 注册 id 集合。注意这是**下界**（有些模组用非字面量拼 id，
    会漏），所以判定时只做「**负面**证据」用：不在集合里 → 可疑，
    在集合里 → 一定没问题。为避免误杀，仅当 `models/item/` 命中、
    且**不在**注册集合、且满足内部素材特征时才报。
    """
    reg: dict[str, set[str]] = {}
    if not mods_dir or not mods_dir.exists():
        return reg
    for j in sorted(mods_dir.glob("*.jar")):
        try:
            z = zipfile.ZipFile(j)
        except Exception:
            continue
        classes = [n for n in z.namelist() if n.endswith(".class")]
        if not classes:
            continue
        blob = b""
        for n in classes:
            try:
                blob += z.read(n)
            except Exception:
                continue
        # 候选 id：class 里出现的 `[a-z0-9_]{2,48}` 短标识符
        # （注册调用里的字面量必然长这样；用长正则一次扫全 blob）
        for m in re.finditer(rb"[a-z][a-z0-9_]{2,47}", blob):
            s = m.group().decode("ascii", "ignore")
            if s:
                reg.setdefault(s, set()).add(j.name)
        if verbose:
            print(f"  {j.name}: class {len(classes)}")
    # 展平成 ns → id 不好做（class 里没有 ns 信息），这里返回**全局 id 集合**
    # 另附 `__by_jar__`：id → 它出现在哪个 jar 的常量池里。
    #   🔴 用途：全局裸名集合有**跨 ns 混名**问题（`book` 这种大众名在任何 mod 里
    #      注册过都会让全局池放行）。per-jar 池能识别「本 mod 自己没注册这个 id」。
    by_jar: dict[str, set[str]] = {}
    for s, jars in reg.items():
        for jn in jars:
            by_jar.setdefault(jn, set()).add(s)
    return {"__all__": set(reg), "__by_jar__": by_jar} if reg else {}


def build_real_index(mods_dir: Path, verbose=False) -> dict:
    """扫 jar 资源文件建**权威** ns → {items, blocks, recipes, structures}。

    关键：**不碰 lang**（lang 的命名习惯与真实注册名不一致，是伪 id 的根源）。
    """
    idx: dict[str, dict[str, set]] = {}
    if not mods_dir or not mods_dir.exists():
        return idx
    for j in sorted(mods_dir.glob("*.jar")):
        try:
            z = zipfile.ZipFile(j)
        except Exception:
            continue
        for n in z.namelist():
            parts = n.split("/")
            if len(parts) < 3:
                continue
            top, ns = parts[0], parts[1]
            if top not in ("assets", "data"):
                continue
            d = idx.setdefault(ns, {"items": set(), "blocks": set(),
                                    "recipes": set(), "structures": set(),
                                    "biomes": set(), "__jars__": set()})
            # 记录该 ns 出现在哪些 jar —— 供 check_icons 的「per-jar 注册池」判据用。
            # 🔴 全局裸名池有**跨 ns 混名**问题：`book` 这个裸名在别的 mod 里注册过，
            #    于是 `apotheosis:book`（apotheosis 自己的物品已被删、只剩孤儿模型）
            #    会被全局池放行 → 漏判。per-jar 池能把它揪出来。
            d["__jars__"].add(j.name)
            if top == "assets":
                if len(parts) >= 5 and parts[2] == "models" and parts[3] == "item":
                    # 🔴🔴 2026-09-18 修 bug：`parts[4]` 是**子目录名**，
                    #   不是 id。`models/item/3d/the_cube_3d.json` 的真实 id 是
                    #   `3d/the_cube_3d`，旧写法只取 `parts[4]` → 收进一个假的 `3d`，
                    #   真 id 反而漏掉 → 任务书用了它 → 图标渲染不出（紫黑块）。
                    #   正确做法：取 `item/` 之后的**整段**相对路径（去掉 .json）。
                    if parts[-1].endswith(".json"):
                        rel = "/".join(parts[4:])[:-5]
                        if rel and not rel.endswith("/"):
                            d["items"].add(rel)
                elif len(parts) >= 4 and parts[2] == "blockstates" and parts[-1].endswith(".json"):
                    d["blocks"].add(parts[-1][:-5])
            else:  # data
                p2 = parts[2] if len(parts) > 2 else ""
                if p2.startswith("recipe") and parts[-1].endswith(".json"):
                    d["recipes"].add(parts[-1][:-5])
                elif p2.startswith("loot_table") and len(parts) >= 4 and parts[-1].endswith(".json"):
                    d["recipes"].add(parts[-1][:-5])       # 战利品表名 ≈ 方块名，放进 recipes 池
                elif p2 == "worldgen" and len(parts) >= 4 and parts[-1].endswith(".json"):
                    # worldgen/structure/xxx.json  → 结构 id
                    # worldgen/biome/xxx.json      → 群系 id
                    #   🔴 biome 必须单独建池：任务书的 biome 任务写的是**群系 id**，
                    #      拿「物品」判据去查必然报「物品不存在」——实测 terralith / BOP
                    #      的三个群系被误报过（它们在 data/<ns>/worldgen/biome/ 下真实存在）。
                    if parts[3] == "structure":
                        d["structures"].add(parts[-1][:-5])
                    elif parts[3] == "biome":
                        d["biomes"].add(parts[-1][:-5])
        if verbose:
            print(f"  {j.name}")
    return idx


def exists_in_index(i: str, idx: dict, kind: str = "any") -> tuple[bool, str]:
    """返回 (是否存在, 命中位置)。

    `kind` 指定**判据池**，因为不同类型的 id 存在形态不同：
      · `"any"`（默认）→ 物品/方块/配方/结构 四池，用于 icon、item 任务
      · `"biome"`      → 只查 worldgen/biome（群系任务写的是群系 id，不是物品）
      · `"structure"`  → 只查 worldgen/structure

    🔴 别拿物品判据去查群系 —— 实测 `terralith:sakura_grove` 等被误报「物品不存在」。
    """
    n, _, p = i.partition(":")
    if n == "minecraft":
        # 🔴 原版**不能无脑放行**（见文件上方 VANILLA 段的说明）
        if not VANILLA_LOADED:
            return True, "vanilla(未校验)"    # 拿不到原版 jar 时放行，但在报告里标明
        if p in VANILLA:
            return True, "vanilla"
        return False, "原版没有这个物品/方块"
    d = idx.get(n)
    if not d:
        return False, "ns 不存在"
    if "/" in p:
        # 带路径的（mmorpg:currency/chaos_orb 这类），只在极少见时命中
        head = p.split("/")[0]
        if any(head in s or s.startswith(head) for s in d["items"] | d["blocks"]):
            return True, "path"
        return False, "path 未命中"
    if kind == "biome":
        return (True, "worldgen/biome") if p in d.get("biomes", set()) \
            else (False, "群系不存在")
    if kind == "structure":
        return (True, "worldgen/structure") if p in d["structures"] \
            else (False, "结构不存在")
    for k, label in (("items", "model/item"), ("blocks", "blockstate"),
                     ("recipes", "recipe/loot"), ("structures", "structure")):
        if p in d[k]:
            return True, label
    # 🔴 注册索引兜底（2026-10-01 新增）：**模型可能全在变体子目录里**。
    #    `explorerscompass:explorerscompass` 的模型是
    #    `models/item/explorerscompass_00.json` … `_17.json`（18 个变体），
    #    **顶层没有 `explorerscompass.json`** → 四类资源判据全落空 → 误报"物品不存在"。
    #    但 class 常量池里 `explorerscompass` 有注册记录 → 它是真物品。
    #    （`naturescompass:naturescompass` 同款。）
    #    注意：这个兜底**只放行、不放坏**——坏 id（fire_resistance_potion /
    #    nether_star_block）不在注册池里，照样被拦。
    if p in REGISTERED:
        return True, "注册索引（模型为变体）"
    return False, "四类资源都没命中"


def suggest(i: str, idx: dict, limit=6) -> list[str]:
    """给不存在的 id 找相近候选。"""
    n, _, p = i.partition(":")
    if n == "minecraft":
        # 原版也要给候选 —— 坏 id 多半是"改名/漏后缀"，从原版池里捞最有用
        pool = sorted(VANILLA)
        key = p.split("_")[0]
        cands = [x for x in pool if key and key in x]
        if not cands:
            cands = [x for x in pool if any(t in x for t in p.split("_") if len(t) > 2)]
        return [f"minecraft:{x}" for x in cands[:limit]]
    d = idx.get(n)
    if not d:
        return []
    pool = sorted(d["items"] | d["blocks"])
    key = p.split("_")[0]
    cands = [x for x in pool if key and key in x]
    # 再按「词序颠倒」试探（steel_ingot ↔ ingot_steel）
    if not cands and "_" in p:
        a, _, b = p.partition("_")
        cands = [x for x in pool if x.startswith(b + "_") or x == f"{b}_{a}"]
    return cands[:limit]


def quest_files(root: Path):
    """
    列出任务书章节文件，**SNBT 与 JSON5 都要认**。

    🔴 26.1.2.1 起 FTB Quests 全面改用 JSON5（`chapters/*.json5`），
       旧代码写死 `*.snbt` → 在 26.x 实例上 glob 恒返回空 → 各检查**静默通过**。
       实测踩过：26.1.2 实例上「章节分组检查」报"未扫到任何章节文件"。
       （判据同 `check_groups` 的思路：扫到 0 个必须由调用方报错，不能当通过。）
    """
    return sorted(list(root.glob("*.snbt")) + list(root.glob("*.json5")))


def groups_file(root: Path) -> Path | None:
    """chapter_groups 的路径，SNBT / JSON5 都认。"""
    for name in ("chapter_groups.snbt", "chapter_groups.json5"):
        p = root / name
        if p.exists():
            return p
    return None


def run_checks(quests: Path, idx: dict):
    """返回 [(文件, id, 问题, 候选)]。

    🔴 判据要**跟着任务类型走**：`biome` / `structure` 任务的字面量是群系/结构 id，
       不是物品 id —— 拿物品池去查必然误报（实测 terralith 与 BOP 的群系中招）。
       所以先按 `type: "biome"` 之类的上下文把 id 归到对应的判据池。
    """
    out = []
    # 先扫出「任务类型 → 该类型下出现的 id」的归属
    kind_of: dict[str, str] = {}
    ctx_rx = re.compile(r'type:\s*"(biome|structure)"[^{}]*?'
                        r'(?:biome|structure)\s*:\s*"([^"]+)"', re.S)
    for f in [p for p in quests.rglob("*") if p.suffix in (".snbt", ".json5")]:
        for m in ctx_rx.finditer(f.read_text(encoding="utf-8")):
            kind_of[m.group(2)] = m.group(1)

    def _skip_spans(t: str) -> list[tuple[int, int]]:
        """标出所有 `components: { ... }` 块的 [起, 止) 区间（按括号配对，不依赖缩进）。

        🔴 块内全是**组件名与组件值**，不是物品 id。实测被误报的：
           `"minecraft:potion_contents": { potion: "minecraft:fire_resistance" }`
           `"patchouli:book": "apotheosis:apoth_chronicle"`
           `"ftbquests:missing_item": "minecraft:fire_resistance_potion"`
        """
        spans = []
        for m in re.finditer(r"components\s*:\s*\{", t):
            i, depth = m.end() - 1, 0
            while i < len(t):
                if t[i] == "{":
                    depth += 1
                elif t[i] == "}":
                    depth -= 1
                    if depth == 0:
                        spans.append((m.end() - 1, i + 1))
                        break
                i += 1
        return spans

    # ---- item 任务的物品 id（唯一该查**原版**的物品语境）----
    #   🔴🔴 2026-10-01 补漏（玩家反馈「任务书有很多物品缺失」时才发现）：
    #      旧版只查 `icon:`，`item: { id: "..." }`（任务**需求**的物品）
    #      **从来没被查过** —— 注释里写"由 check_item_ids() 做"，
    #      但那个函数**根本不存在**（从未实现）。
    #      后果：`12_ascend.json5` 里的 `minecraft:nether_star_block`
    #      （原版压根没这个方块）静态检查全绿，直到玩家进游戏才暴露。
    #   两种写法都要认：`item: { id: "ns:path", count: 1 }` 与 `item: "ns:path"`。
    ITEM_BLK_RX = re.compile(r'item\s*:\s*\{[^{}]*?\bid\s*:\s*"([^"]+)"', re.S)
    ITEM_STR_RX = re.compile(r'\bitem\s*:\s*"([^"]+)"')

    for f in [p for p in quests.rglob("*") if p.suffix in (".snbt", ".json5")]:
        text = f.read_text(encoding="utf-8")
        # ---- ① item 任务的物品 id（**含原版** —— 泛扫里 minecraft 是跳过的）----
        for rx in (ITEM_BLK_RX, ITEM_STR_RX):
            for m in rx.finditer(text):
                i = m.group(1)
                if not is_legal(i):
                    continue
                ok, where = exists_in_index(i, idx, "any")
                if not ok:
                    out.append((f.name, i, f"物品不存在（{where}）", suggest(i, idx)))
        # ---- ② 泛扫全文（补 item 语境漏掉的角落：string/biome/structure 等）----
        skip = _skip_spans(text)
        for m in ID_LIT.finditer(text):
            i = m.group(1)
            if re.fullmatch(r"[0-9A-F]{16}", i.split(":", 1)[-1]):
                continue                     # quest / reward 的 16 位 hex id
            # ① **对象 key**（后面紧跟 `:`）是字段名，不是 id
            #    —— `"patchouli:book": "apotheosis:apoth_chronicle"` 里两个都是 key/value
            if re.match(r"\s*:", text[m.end():m.end() + 32]):
                continue
            # ② **落在 components 块内**的是组件名/组件值，不是物品 id
            if any(s <= m.start() < e for s, e in skip):
                continue
            if not is_legal(i):
                out.append((f.name, i, "语法非法", []))
                continue
            n = i.split(":", 1)[0]
            if n not in idx and n != "minecraft":
                out.append((f.name, i, "命名空间不存在", []))
                continue
            if n == "minecraft":
                # 🔴 这里是**泛扫**（正则捞全文所有 `"ns:path"` 字面量），
                #    原版那一堆字面量里混着 **维度 id（minecraft:the_nether）、
                #    实体 id（minecraft:wither）、药水效果（minecraft:fire_resistance）、
                #    组件名（minecraft:potion_contents）** —— 拿"原版物品池"去查必然误报。
                #    原版的物品校验只在水到渠成的**物品语境**里做：
                #    `check_icons()`（icon:）和 `check_item_ids()`（item: { id: }）。
                #    这是 2026-10-01 实测出来的：第一版没跳过，一口气误报 7 条。
                continue
            ok, where = exists_in_index(i, idx, kind_of.get(i, "any"))
            if not ok:
                out.append((f.name, i, f"物品不存在（{where}）", suggest(i, idx)))
    return out


def check_missing_item_placeholders(quests: Path) -> dict[str, list[str]]:
    """查出 FTB 已经写进文件的 `ftbquests:missing_item` 占位。

    🔴🔴 2026-10-01 玩家反馈「任务书有很多物品缺失」后新增 —— 这是**玩家侧唯一
       看得见的现形证据**，也是整个判据链最容易漏的一环。

    FTB Quests 在加载任务书时，凡是**解析不了**的物品 id，会：
      ① 替换成占位 id `ftbquests:missing_item`；
      ② 把**原始 id** 塞进组件里；
      ③ **写回文件**（所以玩家实例里的文件把这些占位「固化」了）。
    最终形态：

        id: "ftbquests:missing_item",
        components: {
          "ftbquests:missing_item": "minecraft:fire_resistance_potion",   ← 原始 id
          "ftbquests:missing_item_count": 1,
        },

    玩家打开任务书，看到的就是一行行「缺失」占位。

    ⚠️ 为什么必须单独查：**源文件层与运行层是两种形态**
      · 整合包/工作区那份 → 还是原始坏 id（`id: "minecraft:nether_star_block"`）
        → 由 `run_checks()` 抓（且它现在会**跳过 components 块**，所以抓不到占位形态）
      · 跑过游戏的实例那份 → 已变成上面的占位形态 → **只能由本函数抓**
    两层都查，才不会出现「静态检查全绿、游戏里一片缺失」。

    返回 {文件名: [原始 id, ...]}。
    """
    out: dict[str, list[str]] = {}
    rx = re.compile(r'"ftbquests:missing_item"\s*:\s*"([^"]+)"')
    for f in [p for p in quests.rglob("*") if p.suffix in (".snbt", ".json5")]:
        ids = rx.findall(f.read_text(encoding="utf-8"))
        if ids:
            out[f.name] = ids
    return out


def check_icons(quests: Path, idx: dict, reg: dict | None = None):
    """🔴🔴 icon 层检查（第 ④⑤⑥ 层）。

    判据比 `exists_in_index` 更窄：**只有 `models/item/<id>.json` 存在，
    FTB 才渲染得出图标**。id 在 block/blockstate/recipe 池里"存在"不作数。

    另：带斜杠的 id 是「模型变体」（`3d/xxx_3d`），不是独立物品，也判为问题。

    第 ⑥ 层（2026-09-18 第四轮玩家反馈后新增）：**文件在 `models/item/` 顶层
    ≠ 是注册物品**。jar 里混入了大量「内部素材 —— 作者手工画的预设/部件/UI 图标」，
    它们**有模型、有贴图，部分还有 lang 键**，但没有 Item 注册，
    做图标必然紫黑问号，且日志一个错都不报。
    实测三例：psi:_standard_cad / mmorpg:empty_favor / evilcraft:blood_wand_cap

    判定用一个**两条件合取**，避免误杀（reg 是下界集合，单独用会漏判）：
      (a) 裸名以 `_` 开头                       → 作者内部预设的统一习惯
      (b) 不在 class 常量池的注册 id 集合里      → 从未被 register()
    (a) 单独成立即可报（强信号）；(b) 单独成立**不报**（怕漏注册拼串的模组）。
    返回 [(文件, id, 原因)]。
    """
    out = []
    registered: set[str] = (reg or {}).get("__all__", set())
    icon_rx = re.compile(r'icon:\s*"([^"]+)"')
    for f in [p for p in quests.rglob("*") if p.suffix in (".snbt", ".json5")]:
        for m in icon_rx.finditer(f.read_text(encoding="utf-8")):
            i = m.group(1)
            n, _, p = i.partition(":")
            if n == "minecraft":
                # 🔴 原版 icon 也会画不出来：`nether_star_block` 这种**不存在**的方块，
                #    以前这里直接 continue 放行，等于漏检。原版方块走 blockstate 渲染，
                #    不必查 models/item，但**必须查"原版到底有没有这个 id"**。
                if VANILLA_LOADED and p not in VANILLA:
                    out.append((f.name, i, "原版没有这个物品/方块，图标渲染不出来"))
                continue
            d = idx.get(n)
            if not d:
                continue                      # ns 不存在已由 run_checks 报过
            if "/" in p:
                out.append((f.name, i, "模型变体（子目录），非独立物品 id"))
                continue
            # ⑦ 变体**后缀** `_00` … `_99`（2026-10-01 新增）
            #    🔴 实测：`explorerscompass:explorerscompass_00` 与
            #       `naturescompass:naturescompass_00` 就是任务书里真出现过的坏 id ——
            #       这两个 mod 的模型是 18 个变体 `_00`…`_17`，**主 id 反倒没有顶层模型**，
            #       所以「子目录变体」判据抓不到，而「缺 models/item」判据也放行。
            #    判据带**正向条件**（去后缀后确实存在）→ 不会误杀真叫 `xxx_00` 的物品。
            vm = re.fullmatch(r"(.+)_(\d{2})", p)
            if vm and (vm.group(1) in d["items"] or vm.group(1) in registered):
                out.append((f.name, i,
                            f"模型变体（`_{vm.group(2)}` 后缀），非独立物品 id"))
                continue
            if p not in d["items"]:
                # 🔴 注册索引兜底：模型可能全在**变体子目录**里
                #    （`explorerscompass_00` … `_17`），顶层没有 `<id>.json`。
                #    注册池里有记录 → 是真物品，FTB 按 ItemStack 渲染照样出图。
                if registered and p in registered:
                    pass                       # 放行
                else:
                    hit = ("blockstate" if p in d["blocks"] else
                           "recipe" if p in d["recipes"] else
                           "structure" if p in d["structures"] else "无")
                    out.append((f.name, i, f"缺 models/item/（仅命中 {hit}）"))
                    continue
            # ⑥-a 内部预设：`_` 前缀（作者内部命名的统一习惯，强信号）
            if p.startswith("_"):
                out.append((f.name, i, "内部预设模型（`_` 前缀），非注册物品"))
                continue
            # ⑥-b 未注册（弱信号）：只在「本模组确实有注册类」时才采信。
            #     判据：该 ns 的顶层 id 大都能在常量池找到 → 说明扫描有效。
            #     反例：mine_and_slash 用字符串拼接造 id，命中率仅 43% →
            #     扫描无效 → 本判据**自动放弃**（宁可漏报，不可误杀）。
            if registered and p not in registered and _ns_scan_ok(n, idx, registered):
                out.append((f.name, i, "疑似内部素材（class 常量池无注册记录）"))
                continue
            # ⚠️ 2026-10-01 试过又**撤掉**的判据：per-jar 注册池
            #   （「该 id 是否出现在本 ns 所属 jar 的常量池里」）
            #   想法是解决跨 ns 混名（`book` 这种大众名在别的 mod 注册过 →
            #   全局池放行 → `apotheosis:book` 孤儿模型漏判）。
            #   **实测两个方向都不成立**：
            #     · 漏判照旧：`book` 出现在 `PotionCharmRecipe.class` 里作为普通
            #       单词，照样进了 per-jar 池 → 拦不住；
            #     · 还会**误杀**：`ender_lead`（apotheosis 的正常物品，模型/lang 齐全）
            #       的 id 字符串**根本不在 jar 任何 class 里**（拼串或数据驱动注册）
            #       → 会被判成"未注册"。
            #   结论：**静态扫描分不清「注册名」与「普通字符串」**，
            #   这类「有模型、没注册」的孤儿 id **只能靠运行时检查兜底** ——
            #   见 `check_missing_item_placeholders()`：让 FTB 加载一次，
            #   它会把所有解析不了的 id 写成 `missing_item` 占位并回写文件，
            #   那是**零误报的地面真值**。
            #   （`build_registered_index` 仍返回 `__by_jar__`，仅供人工排查用。）
    return out


def _ns_scan_ok(ns: str, idx: dict, registered: set[str], sample: int = 12) -> bool:
    """判断对 ns 的「class 常量池」扫描是否有效（防止漏扫导致误杀）。

    取该 ns 前 sample 个顶层 models/item 项，统计有多少在常量池里。
    命中率 >= 60% → 认为这个 mod 的注册走字面量，扫描可信，可以用⑥-b判据。
    """
    items = [i for i in sorted(idx.get(ns, {}).get("items", ()))
             if i and "/" not in i and not i.startswith("_")]
    if len(items) < 5:
        return False                       # 样本太少，不下判断
    probe = items[:sample]
    hit = sum(1 for i in probe if i in registered)
    return hit / len(probe) >= 0.6


def apply_fix(quests: Path, manual: dict, apply: bool):
    idx = None
    changed, log = 0, []
    for f in [p for p in quests.rglob("*") if p.suffix in (".snbt", ".json5")]:
        text = orig = f.read_text(encoding="utf-8")

        def sub(m: re.Match) -> str:
            i = m.group(1)
            if re.fullmatch(r"[0-9A-F]{16}", i.split(":", 1)[-1]):
                return m.group(0)
            good = manual.get(i)
            if good:
                log.append(f"  {f.name}: {i} → {good}")
                return f'"{good}"'
            n, _, p = i.partition(":")
            if not is_legal(i):
                log.append(f"  {f.name}: {i} → minecraft:stone（语法非法）")
                return '"minecraft:stone"'
            return m.group(0)

        text = ID_LIT.sub(sub, text)
        if text != orig:
            changed += 1
            if apply:
                f.write_text(text, encoding="utf-8")
    return changed, log


def check_groups(quests: Path):
    """章节分组一致性检查（2026-09-18 第五轮实测新增）。

    🔴 症状：玩家反馈「**为什么一章后面是十章**」。
       实际是 `10_life`（第十章）的 `group` 被写成了**序章组的 id** ——
       章节不丢、日志全正常（`Loaded 6 chapter groups, 12 chapters, 84 quests`），
       只有翻分组时才看出顺序乱。

    🔴🔴 判据（第六轮修正，**必须用严格判据**）：
       FTB 的 `order_index` 是**组内排序**，所以**每个组的 order_index 必须
       恰好是 `0..n-1` 这段整数**（n = 该组章节数）。

       为什么不能用「跨度 spread > n+2」这种宽松启发式：
       本实例的 order 原本是**全局序号**（0..11），10_life 串进序章组后
       该组是 {0, 1}，spread=1，任何宽松阈值都判不出来 → **假阴性静默通过**。
       实测确认：注入坏样本后旧判据报「✓ 分组归属正常」。

    返回 ([(group_id, [(order_index, title, filename)])], {group_id: title})。
    还会返回第三个值 `scanned`：实际扫到的章节文件数。
    🔴 `scanned == 0` 表示**路径没找对**，调用方必须报错，不能当「检查通过」——
       实测踩过：`quests` 传的是 `config/ftbquests`，而章节在
       `config/ftbquests/quests/chapters`，旧代码写死 `quests/"chapters"` →
       glob 返回空 → **静默通过**（假阴性）。
    """
    # ---- 定位真正的 quests 根（兼容传 `config/ftbquests` 或 `.../quests`）----
    cands = [quests]
    if (quests / "quests").is_dir():
        cands.append(quests / "quests")
    root = next((c for c in cands if (c / "chapters").is_dir()), quests)

    groups: dict[str, list[tuple[int, str, str]]] = {}
    titles: dict[str, str] = {}
    cg = groups_file(root)
    if cg is not None:
        # 行内式（SNBT）与多行式（JSON5）都要能读：
        #   SNBT   { id: "xx", title: "yy" }
        #   JSON5  { \n id: "xx", \n title: "yy", \n }
        for m in re.finditer(
                r'id:\s*"([^"]+)"\s*,\s*title:\s*"([^"]+)"'
                r'|id:\s*"([^"]+)"[^{}]*?title:\s*"([^"]+)"',
                cg.read_text(encoding="utf-8"), re.S):
            gid = m.group(1) or m.group(3)
            titles[gid] = m.group(2) or m.group(4)
    ch_files = quest_files(root / "chapters")
    for f in ch_files:
        t = f.read_text(encoding="utf-8")
        # ^\s* 而非 ^\t —— JSON5 用空格缩进，SNBT 用 tab
        g = re.search(r'^\s*group:\s*"([^"]*)"', t, re.M)
        o = re.search(r'^\s*order_index:\s*(\d+)', t, re.M)
        ti = re.search(r'^\s*title:\s*"([^"]*)"', t, re.M)
        if not g or not o:
            continue
        gid = g.group(1)
        if not gid:
            continue
        groups.setdefault(gid, []).append(
            (int(o.group(1)), ti.group(1) if ti else f.stem, f.name))

    out = []
    for gid, members in sorted(groups.items()):
        members.sort()
        idxs = [x[0] for x in members]
        # 严格判据：组内 order_index 必须恰好 == 0..n-1（集合相等）
        if idxs != list(range(len(idxs))):
            out.append((gid, members))
    return out, titles, len(ch_files)


def check_title_colors(quests: Path):
    """同组章节的**标题颜色**必须一致（2026-09-19 玩家反馈新增）。

    🔴 症状：玩家说「第十章的颜色，跟同一分类的其他章不一样」。
       实测 `10_life` 属冒险组，组内 07/08/09 都是 `&a`（绿），只有它是 `&e`（黄）。

    判据：同一 `group` 下所有章节的 `title` **颜色前缀必须相同**。
    注意：
      · 只比**颜色码**，不比文字（各章文字本来就不同）
      · 无颜色前缀记作 `(无)`，也算一种"颜色" —— 组里有人有颜色有人没有，同样报
      · 组内只有 1 章时天然通过（无对比对象）

    🔴 2026-09-28 修 bug：**26.1.2 的章节标题以 `lang/<locale>/*.json5` 为准**
       （章节 json5 里的 `title` 字段 FTB 根本不读，只是留给人看/旧版兼容）。
       旧版只读章节文件 → 读到的是「生成器写死的那份」，**即使 lang 里已统一也
       照样报不一致**（实测假阳性：lang 里 `&a落地` / 实读章文件 `&e落地`）。
       现在：① 有 lang 就先读 lang（真实生效值）；② 没有 lang 再退回读章节文件。

    返回 [(group_id, {颜色: [章节名…]})]，只含不一致的组。
    """
    g: dict[str, list[tuple[str, str]]] = {}          # gid -> [(章节, 颜色)]
    # 🔴 先找根再读 chapter_groups —— 传进来的可能是 `config/ftbquests`，
    #    而 chapter_groups.snbt 在 `config/ftbquests/quests/` 下。
    #   （同一个坑在 check_groups 里踩过一次，这里别再犯）
    root = next((c for c in (quests, quests / "quests") if (c / "chapters").is_dir()),
                quests)
    grp_titles: dict[str, str] = {}
    cg = groups_file(root)
    if cg is not None:
        for m in re.finditer(
                r'id:\s*"([^"]+)"\s*,\s*title:\s*"([^"]+)"'
                r'|id:\s*"([^"]+)"[^{}]*?title:\s*"([^"]+)"',
                cg.read_text(encoding="utf-8"), re.S):
            gid = m.group(1) or m.group(3)
            grp_titles[gid] = m.group(2) or m.group(4)

    # ---- ① 优先取 lang：`chapter.<id>.title`（26.1.2 实际生效值）----
    lang_titles: dict[str, str] = {}                  # 章节 id -> 标题（带色）
    lang_dir = root / "lang"
    if lang_dir.is_dir():
        for lf in sorted(x for x in lang_dir.rglob("*")
                         if x.suffix in (".json5", ".json", ".snbt")):
            lt = lf.read_text(encoding="utf-8")
            # key 两种写法都要认：合规的带引号 `"chapter.xx.title":`，以及
            # 旧生成器写出的裸 key `chapter.xx.title:`（严格 JSON5 会拒，但
            # 体检脚本仍应能读出来并报「格式非法」）。
            for m in re.finditer(
                    r'"chapter\.([0-9A-Fa-f]{16})\.title"\s*:\s*"([^"]*)"'
                    r'|chapter\.([0-9A-Fa-f]{16})\.title\s*:\s*"([^"]*)"', lt):
                lid = m.group(1) or m.group(3)
                lang_titles[lid] = m.group(2) or m.group(4)

    n = 0
    for f in quest_files(root / "chapters"):
        n += 1
        t = f.read_text(encoding="utf-8")
        # ^\s* 而非 ^\t —— 26.1.2 的 JSON5 用空格缩进
        gm = re.search(r'^\s*group:\s*"([^"]*)"', t, re.M)
        tm = re.search(r'^\s*title:\s*"([^"]*)"', t, re.M)
        if not gm or not tm or not gm.group(1):
            continue
        cid = re.search(r'^\s*id:\s*"([0-9A-Fa-f]{16})"', t, re.M)
        # 有 lang 就用 lang 的标题（真实生效），没有才退回落盘的 title 字段
        title = lang_titles.get(cid.group(1), tm.group(1)) if cid else tm.group(1)
        col = re.match(r"\s*(&[0-9a-f])", title)
        g.setdefault(gm.group(1), []).append(
            (f.stem, col.group(1) if col else "(无)"))

    bad = []
    for gid, items in sorted(g.items()):
        if len({c for _, c in items}) > 1:
            bycol: dict[str, list[str]] = {}
            for name, c in items:
                bycol.setdefault(c, []).append(name)
            bad.append((gid, bycol))
    return bad, grp_titles, n


def check_ids_and_deps(quests: Path):
    """任务 id 唯一性 + 依赖完整性（2026-09-18 第六轮实测新增）。

    🔴 抓的是一类**服务端一行错都不报、离线也不报**的 bug：

    症状：任务书能开、章节都在、任务数量对，但**依赖箭头乱指**、
    玩家进度对不上、FTB 会在保存时**自行重排 id**（玩家实例的 snbt 被整批重写）。

    真因：任务 id **跨章节重复**。实测旧生成器公式
    `f"{cid[:2]}1{n:013d}"` 里 `cid[:2]` 恒为 "7B"、`n` 是章内计数器 →
    **12 个章节各自的「第 1 个任务」全都是 `7B10000000000001`**：
    84 个任务只产出 **9 个不同 id**，8 个依赖引用了重复 id。

    ⚠️ 这个 bug 的隐蔽性：`Loaded 12 chapters, 84 quests` 照打、icon 检查全绿、
    服务端无异常 —— **必须专门数唯一性才能发现**。

    判据：
      ① 任务级 `id`（16 位 hex）在**全部章节范围内**唯一
      ② `dependencies` 引用的每个 id 都能解析到真实存在的任务 id（否则悬空）
      ③ 奖励级 `id` 同样唯一

    🔴 2026-09-28 修 bug（本函数曾整体假阴性）：旧版用
       `QID = re.compile(r'^\\t\\tid:...')` —— **写死「两个硬 tab 缩进」**。
       SNBT（1.20.x）确实是 tab 缩进，但 26.1.2 的 JSON5 是 **2 空格缩进** →
       正则在 JSON5 上 **0 命中**（实测 `01_first_night.json5`：旧正则 0 个，
       新正则 26 个）→ `seen_q` 基本空 → 「悬空依赖」把**所有真实任务 id**
       都当成不存在的（实测报 46 处，全是假阳性）。
       教训同「判据要按用途分层」：**别用缩进当结构判据**，改用**括号配对**取
       任务/奖励块（`{ ... }` 深度），缩进只做兜底。

    返回 (重复任务id dict, 重复奖励id dict, 悬空依赖 list, 统计 dict)。
    """
    import collections

    def _blocks(t: str, key: str) -> list[str]:
        """取 `quests: [ ... ]` / `rewards: [ ... ]` 里的**顶层对象块**（按括号配对）。

        SNBT 与 JSON5 都适用：都靠 `{` / `}` 深度切块，不依赖缩进。
        """
        out: list[str] = []
        for m in re.finditer(rf'{key}\s*:\s*\[', t):
            i = m.end()
            depth = 1                      # 已经在最外层 `[` 里
            start = None
            while i < len(t) and depth > 0:
                c = t[i]
                if c == '[':
                    depth += 1
                elif c == ']':
                    depth -= 1
                elif c == '{':
                    if depth == 1 and start is None:
                        start = i
                    depth += 1
                elif c == '}':
                    depth -= 1
                    if depth == 1 and start is not None:
                        out.append(t[start:i + 1])
                        start = None
                i += 1
        return out

    ID_RX = re.compile(r'(?<![A-Za-z_])id\s*:\s*"([0-9A-F]{16})"')

    seen_q: dict[str, list[str]] = collections.defaultdict(list)
    seen_r: dict[str, list[str]] = collections.defaultdict(list)
    deps: list[tuple[str, str]] = []
    n_files = 0
    # 同 check_groups：自己找根，兼容传 config/ftbquests 或 .../quests
    root = next((c for c in (quests, quests / "quests") if (c / "chapters").is_dir()),
                quests)
    for f in quest_files(root / "chapters"):
        n_files += 1
        t = f.read_text(encoding="utf-8")
        for qb in _blocks(t, "quests"):
            # 任务块第一个 `id:` 就是任务 id；块内 `[ ... ]` children 形式（SNBT）另算
            for m in ID_RX.finditer(qb.split("tasks:")[0].split("tasks :")[0]):
                seen_q[m.group(1)].append(f.stem)
                break
            for rb in _blocks(qb, "rewards"):
                m = ID_RX.search(rb)
                if m:
                    seen_r[m.group(1)].append(f.stem)
            # 依赖两种写法都要认：SNBT 的 `dependencies: [ "xx", "yy" ]`
            # 与 JSON5 的 `dependencies: [ "xx", ]`
            for dm in re.finditer(r'(?:dependencies|deps)\s*:\s*\[([^\]]*)\]', qb, re.S):
                for h in re.findall(r'"([0-9A-F]{16})"', dm.group(1)):
                    deps.append((f.stem, h))

    dup_q = {k: v for k, v in seen_q.items() if len(v) > 1}
    dup_r = {k: v for k, v in seen_r.items() if len(v) > 1}
    dangling = sorted({(f, h) for f, h in deps if h not in seen_q})
    stat = {"tasks": sum(len(v) for v in seen_q.values()), "uniq": len(seen_q),
            "rewards": sum(len(v) for v in seen_r.values()), "deps": len(deps),
            "files": n_files}
    return dup_q, dup_r, dangling, stat


def find_paths(inst: Path, mods, quests):
    """解析 mods / quests 目录。

    🔴 2026-09-18 修 bug：`--quests` 传相对路径（如 `config/ftbquests`）时，
       旧代码直接用 `Path(rel)` → 相对 **cwd** 解析 → 目录不存在 →
       `rglob` 返回空 → **检查静默通过**（假阴性，最危险的一种）。
       现在：相对路径先尝试挂在 `inst` 下；找不到再退回纯 cwd 解析。
       并且**找不到就明确报错**，不再静默跳过。
    """
    def _resolve(p: Path, base: Path | None) -> Path | None:
        if p.is_absolute():
            return p if p.is_dir() else None
        if base is not None and (base / p).is_dir():
            return base / p
        return p if p.is_dir() else None

    if quests is not None:
        quests = _resolve(Path(quests), inst)
    if mods is not None:
        mods = _resolve(Path(mods), inst)

    if inst:
        if not mods:
            for c in (inst / "mods", inst / ".minecraft/mods"):
                if c.is_dir():
                    mods = c
                    break
        if not quests:
            for c in (inst / "config/ftbquests", inst / ".minecraft/config/ftbquests"):
                if c.is_dir():
                    quests = c
                    break
    return mods, quests


def main() -> int:
    ap = argparse.ArgumentParser(description="FTB Quests 任务书 item id 体检（jar 资源文件为权威源）")
    ap.add_argument("instance", nargs="?")
    ap.add_argument("--mods")
    ap.add_argument("--quests")
    ap.add_argument("--replace", action="append", default=[],
                    help="bad_id=good_id，可重复")
    ap.add_argument("--rebuild-index", action="store_true",
                    help="把权威索引导出成 real_ids.json（供生成器使用）")
    ap.add_argument("--index-out", default="real_ids.json")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--vanilla-jar", default=None,
                    help="原版 patched jar（提供原版物品池；不给则自动搜，搜不到会警告）")
    a = ap.parse_args()
    apply = a.apply and not a.check

    inst = Path(a.instance) if a.instance else None
    mods = Path(a.mods) if a.mods else None
    quests = Path(a.quests) if a.quests else None
    mods, quests = find_paths(inst, mods, quests)
    if not mods:
        print("✗ 找不到 mods 目录（用 --mods 指定）", file=sys.stderr)
        return 2

    # ---- 原版物品池（新增：以前对 minecraft: 无脑放行，漏掉了改名/已删的原版物品）----
    vjar = find_vanilla_jar(inst or mods, a.vanilla_jar)
    build_vanilla_index(vjar, verbose=True)
    if not VANILLA_LOADED:
        print("⚠ 未找到原版 patched jar → **原版物品校验未启用**"
              "（`minecraft:` 下的坏 id 会被放过）")
        print("  用 --vanilla-jar <minecraft-server-patched-*.jar> 指定；"
              "通常在 <实例>/libraries/net/neoforged/minecraft-server-patched/<ver>/ 下")

    print(f"mods: {mods}")
    print(f"quests: {quests or '(未指定，仅重建索引)'}")
    print("建立权威索引（扫 jar 的 model/blockstate/recipe/structure/biome，**不看 lang**）…")
    idx = build_real_index(mods)
    n_items = sum(len(v["items"]) for v in idx.values())
    n_blocks = sum(len(v["blocks"]) for v in idx.values())
    n_biomes = sum(len(v.get("biomes", ())) for v in idx.values())
    print(f"  {len(idx)} 个命名空间 / {n_items} 物品模型 / {n_blocks} 方块状态"
          f" / {n_biomes} 群系")
    print("建立注册索引（扫 jar 的 class 常量池，判「是否真被 register()」）…")
    reg = build_registered_index(mods)
    global REGISTERED
    REGISTERED = reg.get("__all__", set())        # 供 exists_in_index / check_icons 兜底放行
    n_reg = len(REGISTERED)
    print(f"  {n_reg} 个注册 id\n")

    if a.rebuild_index:
        out = Path(a.index_out)
        ser = {ns: {"items": sorted(v["items"]), "blocks": sorted(v["blocks"]),
                    "recipes": sorted(v["recipes"]), "structures": sorted(v["structures"]),
                    "biomes": sorted(v.get("biomes", ()))}
               for ns, v in sorted(idx.items())}
        out.write_text(json.dumps(ser, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"✓ 权威索引已写出：{out}（{out.stat().st_size // 1024} KB）")

    if not quests:
        return 0

    manual = dict(KNOWN_FIX)
    for kv in a.replace:
        k, _, v = kv.partition("=")
        if k and v:
            manual[k.strip()] = v.strip()

    print("=== 任务书检查 ===")
    probs = run_checks(quests, idx)
    if not probs:
        print("  ✓ 所有引用都存在（语法 + ns + 物品）")
    seen = set()
    for f, i, why, cands in probs:
        if (f, i) in seen:
            continue
        seen.add((f, i))
        line = f"  ✗ {f:24} {i:36} {why}"
        if cands:
            line += f"\n      候选: {cands}"
        if i in manual:
            line += f"\n      → 已定替代: {manual[i]}"
        print(line)
    print(f"\n  共 {len(seen)} 处唯一问题\n")

    # ---- 🔴 FTB 已写入的 missing_item 占位（玩家侧现形证据，2026-10-01 新增）----
    #   跑过游戏的实例里，解析不了的 id 已被 FTB 换成 `ftbquests:missing_item`
    #   并**写回文件** —— 这种形态 run_checks 抓不到（它跳过 components 块），
    #   必须由本检查单独抓。玩家原话：「任务书有很多物品缺失」。
    misses = check_missing_item_placeholders(quests)
    if misses:
        n_miss = sum(len(v) for v in misses.values())
        print(f"  ✗ {n_miss} 处 `ftbquests:missing_item` 占位"
              f"（FTB 解析不了这些 id → 玩家看到「物品缺失」）：")
        for fn, ids in sorted(misses.items()):
            print(f"      {fn}")
            for i in ids:
                print(f"          {i}")
        print("      → 原始 id 仍留在组件里；换成真 id 后 FTB 下次加载即恢复正常\n")
    else:
        print("  ✓ 无 `ftbquests:missing_item` 占位（FTB 全部解析成功）\n")

    # ---- 🔴🔴 icon 层检查（第 ④⑤⑥ 层）：id 都存在，但图标渲染不出来 ----
    #   判据比上面更窄：必须 `models/item/<id>.json` 存在，且 id 不能是子目录变体，
    #   且不能是「内部预设模型」（`_` 前缀 —— 有模型没注册，必然紫黑问号）。
    #   实测场景：id 混进了 block/blockstate/recipe 池、是 `3d/xxx_3d` 模型变体、
    #   或是 `psi:_standard_cad` / `mmorpg:empty_favor` / `evilcraft:blood_wand_cap`
    #   这类「有模型没注册」的内部素材
    #   → 服务端不崩、日志不报错，只有玩家肉眼看到紫黑块。
    icon_probs = check_icons(quests, idx, reg)
    if icon_probs:
        print(f"  ⚠ {len(icon_probs)} 个 icon 无法渲染（会显示紫黑块）：")
        for f, i, why in icon_probs[:30]:
            print(f"      {f:24} {i:40} {why}")
        if len(icon_probs) > 30:
            print(f"      …… 还有 {len(icon_probs) - 30} 个")
        print()
    else:
        print("  ✓ 所有 icon 均为已注册物品且有 models/item/ 模型（不会紫黑块）\n")

    # ---- 🔴 章节分组一致性（第五轮实测新增，第六轮改严格判据）：防「一章后面是十章」 ----
    #   章节不丢、日志全正常，只有翻分组才看出顺序乱 —— 极难自查。
    #   判据：**每组 order_index 必须恰好是 0..n-1**（FTB 的 order_index 是组内排序）。
    group_probs, g_titles, g_scanned = check_groups(quests)
    if g_scanned == 0:
        print("  ✗ 章节分组检查**未扫到任何章节文件**（路径不对，不能当通过！）")
        print(f"      quests = {quests}")
        print("      → 检查 --quests / instance 路径是否指向 ftbquests/quests\n")
        return 2
    if group_probs:
        print(f"  ⚠ {len(group_probs)} 个章节分组的 order_index 不是 0..n-1（章节串组/排序错）：")
        for gid, members in group_probs:
            gt = g_titles.get(gid, gid)
            idxs = [o for o, _, _ in members]
            seq = ", ".join(f"{name.strip()}={o}" for o, name, _ in members)
            print(f"      组 {gt}  ({gid})")
            print(f"          期望 0..{len(members)-1}   实得 {idxs}")
            print(f"          {seq}")
        print("      → FTB 的 order_index 是**组内排序**："
              "① 检查这些章节的 `group:` 是否误填了别的组 id；"
              "② 检查 order 是否是全局序号（必须每组从 0 重数）\n")
    else:
        print(f"  ✓ 章节分组归属正常（扫到 {g_scanned} 章，每组 order_index 恰为 0..n-1）\n")

    # ---- 🔴 任务 id 唯一性 + 依赖完整性（第六轮实测新增）----
    #   防的是「服务端不报错、离线也不报错」的那一类：id 跨章节重复 →
    #   FTB 自行重排 id → 依赖指错章、进度对不上。
    dup_q, dup_r, dangling, idstat = check_ids_and_deps(quests)
    if idstat["files"] == 0:
        print("  ✗ id 唯一性检查**未扫到任何章节文件**（路径不对，不能当通过！）\n")
        return 2
    if dup_q or dup_r or dangling:
        if dup_q:
            print(f"  ⚠ {len(dup_q)} 个任务 id **跨章节重复**"
                  f"（FTB 会重排 id，依赖会指到别的章节）：")
            for k in sorted(dup_q)[:8]:
                print(f"      {k}  出现于 {dup_q[k]}")
            print(f"      共 {sum(len(v) for v in dup_q.values())} 处 / "
                  f"不同 id 仅 {idstat['uniq']} 个（任务总数 {idstat['tasks']}）")
        if dup_r:
            print(f"  ⚠ {len(dup_r)} 个奖励 id 重复：{sorted(dup_r)[:5]}")
        if dangling:
            print(f"  ⚠ {len(dangling)} 处**悬空依赖**（指向不存在的任务 id）：")
            for f, h in dangling[:8]:
                print(f"      {f} 依赖 {h}")
        print("      → 任务 id 要全局唯一（如 `7B1` + <章序号 2 位> + <章内序号 11 位>）；"
              "依赖请用生成器的返回值，别手写 id 常量\n")
    else:
        print(f"  ✓ 任务 id 全局唯一（{idstat['uniq']} 个 / {idstat['tasks']} 个任务）"
              f"且无悬空依赖（{idstat['deps']} 处引用全部命中）\n")

    # ---- 🔴 同组章节标题颜色一致性（2026-09-19 玩家反馈新增）----
    #   玩家原话：「第十章的颜色，跟同一分类的其他章不一样」。
    #   实测 10_life 属冒险组，组内 07/08/09 都是 &a，只有它是 &e。
    col_probs, col_grp_titles, col_scanned = check_title_colors(quests)
    if col_scanned == 0:
        print("  ✗ 标题配色检查**未扫到任何章节文件**（路径不对，不能当通过！）\n")
        return 2
    if col_probs:
        print(f"  ⚠ {len(col_probs)} 个章节分组内**标题颜色不一致**：")
        for gid, bycol in col_probs:
            gt = col_grp_titles.get(gid, gid)
            print(f"      组 {gt}  ({gid})")
            for c, names in sorted(bycol.items()):
                print(f"          {c:>5}  ← {names}")
        print("      → 同组章节的标题色应当统一；建议**按组推导颜色**，"
              "别在各章 title 里手写（手写常量一定会漂）\n")
    else:
        print(f"  ✓ 同组章节标题颜色一致（扫到 {col_scanned} 章）\n")

    if (not probs and not icon_probs and not group_probs and not dup_q
            and not dup_r and not dangling and not col_probs and not misses):
        return 0

    if misses and not probs:
        print("=== 修复 ===")
        print("  这些是 FTB 加载时**写回文件的占位** → 要改的是**源文件**里对应的"
              "原始 id（占位里的组件值就是它），改完把章节文件同步到实例，"
              "FTB 下次加载会自行恢复正常")
        return 1

    if not probs:
        return 1   # 只有 icon/分组/id 问题：需人工换 id（替换表未必覆盖），返回 1 让调用方注意

    print("=== 修复 ===")
    n, log = apply_fix(quests, manual, apply)
    for l in log:
        print(l)
    if not log:
        print("  （无可自动替换项；用 --replace 指定）")

    if apply:
        print("\n=== 复核 ===")
        left = run_checks(quests, idx)
        left = [x for x in left if x[1] not in manual]
        if not left:
            print("  ✓ 全部通过")
            return 0
        for f, i, why, _ in left:
            print(f"  ✗ {f}: {i} {why}")
        return 1

    print("\n（dry-run；加 --apply 落地）")
    return 1


if __name__ == "__main__":
    sys.exit(main())

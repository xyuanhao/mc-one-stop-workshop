#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Minecraft 模组工程检查器

检查项：
  1. 平台/版本识别（Forge / NeoForge / Fabric）
  2. modid 合法性
  3. Java 版本与 MC 版本匹配
  4. 依赖写法（26.x 是否还在用 modImplementation/remapJar 等已废弃写法）
  5. 资源完整性（注册 id ↔ 模型/方块状态/战利品表/语言文件）
  6. 反模式扫描（照抄老教程导致编译失败的写法）
  7. JSON 严格性（1.21.6+ 不容许注释与尾逗号）

用法:
  python 检查工程.py <工程目录>
"""

import argparse
import json
import re
import sys
from pathlib import Path

ERRORS, WARNS, INFOS = [], [], []

def err(m): ERRORS.append(m)
def warn(m): WARNS.append(m)
def info(m): INFOS.append(m)

# ---------- 版本工具 ----------
def vkey(v):
    """把版本字符串转成可比较元组。26.1 → (26,1)，1.21.11 → (1,21,11)。新体系 major=26 > 旧体系 major=1，天然有序。"""
    parts = []
    for p in str(v).split("."):
        m = re.match(r"^(\d+)", p)
        parts.append(int(m.group(1)) if m else 0)
    return tuple(parts)

def ge(a, b): return vkey(a) >= vkey(b)

JAVA_REQUIRED = {
    "1.20.1": 17, "1.20.2": 17, "1.20.3": 17, "1.20.4": 17,
    "1.20.5": 21, "1.20.6": 21,
    "1.21": 21, "1.21.1": 21, "1.21.2": 21, "1.21.3": 21, "1.21.4": 21,
    "1.21.5": 21, "1.21.6": 21, "1.21.7": 21, "1.21.8": 21,
    "1.21.9": 21, "1.21.10": 21, "1.21.11": 21,
    "26.1": 25, "26.2": 25,
}

# ---------- 反模式规则 (正则, 命中提示, 从哪个版本起失效, 影响平台) ----------
# 说明：正则里的 (?:[\w.]+\.)? 用于匹配全限定名写法（如 new net.minecraft...ResourceLocation(）。
ANTIPATTERNS = [
    (r"new\s+(?:[\w.]+\.)?Identifier\s*\(",      "Identifier 构造器已失效，改用 Identifier.fromNamespaceAndPath(...)", "1.21", "fabric"),
    (r"new\s+(?:[\w.]+\.)?ResourceLocation\s*\(","ResourceLocation 构造器已失效，改用 ResourceLocation.fromNamespaceAndPath(...) 或 .parse(...)", "1.21", "both"),
    (r"\bRegistry\.(BLOCK|ITEM|FLUID)\b",        "Registry.BLOCK/ITEM 已移除，改用 Registries.BLOCK/ITEM", "1.21", "both"),
    (r"getOrCreateTag\s*\(\s*\)",                "物品 NBT 已被 Data Components 取代，改用 DataComponentType + stack.set(...)", "1.20.5", "both"),
    (r"\bSimpleChannel\b",                       "SimpleChannel 已废弃，改用 CustomPacketPayload.Type + StreamCodec + RegisterPayloadHandlersEvent", "1.20.5", "neoforge"),
    (r"Recipe\s*<\s*(?:[\w.]+\.)?Container\s*>", "Container 已被 RecipeInput 取代", "1.21", "both"),
    (r"FabricBlockEntityType\.Builder",          "FabricBlockEntityType.Builder 已移除，改用 FabricBlockEntityTypeBuilder", "1.21.2", "fabric"),
    (r"\bmodImplementation\b|\bmodCompileOnly\b|\bmodApi\b", "26.x 不再 remap，改用 implementation / compileOnly / api", "26.1", "fabric"),
    (r"\bremapJar\b",                            "26.x 不再 remap，改用 jar", "26.1", "fabric"),
    (r"\bItemGroupEvents\b",                     "Fabric API 已更名为 CreativeModeTabEvents", "26.1", "fabric"),
    (r"\bHudRenderCallback\b",                   "HudRenderCallback 已移除，改用 HudElementRegistry", "26.1", "fabric"),
    (r"fabric-convention-tags-v1|fabric-loot-api-v2", "该 Fabric API 模块已在 26.1 移除", "26.1", "fabric"),
    (r"accessWidener\s+v\d+\s+named",            "26.x 改用 .classtweaker，头部写 classTweaker v2 official", "26.1", "fabric"),
    (r"\brenderBackground\s*\(|\brenderBg\s*\(|\brenderLabels\s*\(",
                                                 "26.1 起这些方法改名为 extractBackground / extractLabels", "26.1", "neoforge"),
    (r"new\s+(?:[\w.]+\.)?ChunkPos\s*\(",        "26.1 起改用 ChunkPos.containing(...) / ChunkPos.unpack(...)", "26.1", "neoforge"),
    (r"\bChunkPos\.asLong\s*\(",                 "26.1 起 ChunkPos.asLong 改为 ChunkPos.pack", "26.1", "neoforge"),
]

# 注册调用里提取 id 的正则：① 调用者（ITEMS / BLOCKS / CREATIVE_MODE_TABS ...）
#                        ② 注册方法名（registerSimpleBlockItem 既是方块物品也是物品）
#                        ③ 注册 id
# 2026-09-09 修：早期版本不区分注册表种类，把 CREATIVE_MODE_TABS.register("modid") 的
# 标签页 id、DATA_COMPONENTS.register("xxx") 的组件 id 全当成物品，报一堆"缺模型"误报。
REG_CALL_RE = re.compile(
    r"(?:([\w.$]+)\s*\.\s*)?"
    r"\b(registerSimpleBlockItem|registerSimpleBlock|registerSimpleItem|registerBlock|registerItem|register)"
    r"\s*\(\s*[\"']([a-z0-9_./]+)[\"']",
    re.M)
REG_IDENT_RE = re.compile(r"fromNamespaceAndPath\s*\(\s*[\w.]*MOD_?ID\w*\s*,\s*[\"']([a-z0-9_./]+)[\"']")

# 这些注册表里的 id 不是物品/方块，扫描模型时要跳过
# 2026-09-11 修：原来漏了 FEATURE / BIOME / DIMENSION 等世界生成注册表，
#   导致 FEATURES.register("shifting_tree", ...) 这种被当成物品报"缺模型"。
# 2026-09-12 修：再补 CHUNK_GENERATOR —— 只写世界生成器的工程
#   （CHUNK_GENERATORS.register("terraced", ...)）会被报 "物品 'terraced' 缺模型"。
# 2026-09-27 修：再补 ATTACHMENT —— NeoForge 1.21+ 的数据附件注册
#   ATTACHMENT_TYPES.register("upgrades", ...) 会被报 "物品 'upgrades' 缺模型"
#   （DeferredRegister<AttachmentType<?>> 的变量名千变万化，所以只匹配关键词而非变量全名）。
# 2026-10-11 修：再补 STRUCTURE_PIECE / STRUCTURE_TYPE / PIECES —— 只写世界生成的工程
#   （PIECES.register("city_building", ...) 会被报 "物品 'city_building' 缺模型"）。
#   注意 "STRUCTURE" 已有，但变量名常见写作 `PIECES` / `STRUCTURE_TYPES` / `PIECE_TYPES`，
#   其中 PIECES 完全不含 STRUCTURE 关键字，所以必须单独列出；
#   同时补 PROCESSOR / TEMPLATE_POOL 之外常用的 STRUCTURE_SET / STRUCTURE_PIECE。
# 2026-10-14 修：再补裸的 EFFECT / EFFECTS —— 状态效果注册表的变量名常常不带 MOB 前缀
#   （`DeferredRegister<MobEffect> EFFECTS = DeferredRegister.create(ForgeRegistries.MOB_EFFECTS, MODID);`
#    是极常见的写法），这时 EFFECTS.register("astral_corrosion", ...) 会被报 "物品缺模型"。
#   原来只有 MOB_?EFFECT，匹配不到裸 EFFECTS。
NON_ITEM_REGISTRY_RE = re.compile(
    r"(TABS?\b|CREATIVE|COMPONENT|ENTITY|SOUND|MOB_?EFFECT|EFFECTS?\b|FLUID|ENCHANT|PARTICLE|MENU|"
    r"RECIPE|PROFESSION|VILLAGER|COMMAND_ARGUMENT|DAMAGE_TYPE|TRIM|ATTRIBUTE|POI|"
    r"SENSOR|SCHEDULE|MEMORY|BANNER|CAT|PAINTING|JUKEBOX|ATTACHMENT|"
    r"FEATURE|CONFIGURED_FEATURE|PLACED_FEATURE|BIOME|DIMENSION|STRUCTURE|TEMPLATE_POOL|"
    r"WORLD_PRESET|NOISE|DENSITY_FUNCTION|CARVER|PLACER|DECORATOR|BLOCK_STATE_PROVIDER|"
    r"CHUNK_STATUS|LEVEL_STEM|CHUNK_GENERATOR|RULE_TEST|PROCESSOR|LOOT_TABLE|"
    r"STRUCTURE_PIECE|STRUCTURE_TYPE|STRUCTURE_SET|PIECES?\b)",
    re.I)
# 行内出现这些，说明该 id 是 tag / ResourceKey 而不是物品
NON_ITEM_CONTEXT_RE = re.compile(r"(TagKey|ResourceKey|createTagKey|createRegistryKey|Identifier|Codec)", re.I)

JAVA_FILE_SUFFIX = ".java"


def read_text(p: Path):
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return ""


def detect_project(root: Path):
    """识别平台、MC 版本、modid、包名、Java 版本。"""
    info = {"platform": None, "mc": None, "modid": None, "package": None,
            "java": None, "props": {}, "gradle": "", "modmeta": ""}

    props_p = root / "gradle.properties"
    if props_p.exists():
        for line in read_text(props_p).splitlines():
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k, v = s.split("=", 1)
                info["props"][k.strip()] = v.strip()

    gradle_p = root / "build.gradle"
    if gradle_p.exists():
        info["gradle"] = read_text(gradle_p)

    g = info["gradle"]
    if "net.neoforged" in g:
        info["platform"] = "neoforge"
    elif "fabric-loom" in g:
        info["platform"] = "fabric"
    elif "net.minecraftforge.gradle" in g or "minecraftforge" in g:
        info["platform"] = "forge"

    info["mc"] = info["props"].get("minecraft_version")
    info["modid"] = info["props"].get("mod_id")
    info["package"] = info["props"].get("mod_group_id")

    # Fabric 从 fabric.mod.json 读
    fmod = root / "src" / "main" / "resources" / "fabric.mod.json"
    if fmod.exists():
        try:
            d = json.loads(read_text(fmod))
            info["modid"] = d.get("id")
            eps = d.get("entrypoints", {})
            main = (eps.get("main") or [None])[0]
            if main:
                info["package"] = ".".join(main.split(".")[:-1])
            for dep, rng in (d.get("depends") or {}).items():
                if dep == "minecraft":
                    info["mc"] = str(rng).lstrip("~>=^ ")
            info["modmeta"] = fmod.name
        except json.JSONDecodeError as e:
            err(f"fabric.mod.json 不是合法 JSON：{e}")
    for meta in (root / "src/main/resources/META-INF/neoforge.mods.toml",
                 root / "src/main/templates/META-INF/neoforge.mods.toml",
                 root / "src/main/resources/META-INF/mods.toml"):
        if meta.exists():
            txt = read_text(meta)
            info["modmeta"] = meta.name
            m = re.search(r'^\s*modId\s*=\s*"([^"]+)"', txt, re.M)
            if m and not info["modid"] or (m and "${" not in m.group(1)):
                info["modid"] = m.group(1)
            break

    # Java 版本
    m = re.search(r"JavaLanguageVersion\.of\((\d+)\)", g)
    if m:
        info["java"] = int(m.group(1))
    m = re.search(r"options\.release\s*=\s*(\d+)", g)
    if m:
        info["java"] = int(m.group(1))
    m = re.search(r"sourceCompatibility\s*=\s*JavaVersion\.VERSION_(\d+)", g)
    if m:
        info["java"] = int(m.group(1))
    return info


def check_basics(root: Path, pinfo):
    if not pinfo["platform"]:
        err("无法识别加载器（build.gradle 里没找到 forge/neoforge/fabric 插件）")
        return
    if not pinfo["mc"]:
        err("无法识别目标 MC 版本（gradle.properties 缺 minecraft_version）")
        return
    info(f"平台: {pinfo['platform']}   MC: {pinfo['mc']}   Java: {pinfo['java'] or '未指定'}")

    modid = pinfo["modid"]
    if not modid:
        err("无法识别 modid")
    elif not re.match(r"^[a-z][a-z0-9_]{1,63}$", modid):
        err(f"modid 不合法: '{modid}'（小写开头，仅 a-z/0-9/_，长度 2-64）")
    elif "${" in modid:
        err(f"modid 仍是未替换的占位符: {modid}")

    # Java 版本
    need = JAVA_REQUIRED.get(pinfo["mc"])
    if need and pinfo["java"]:
        if pinfo["java"] < need:
            err(f"Java 版本不足：MC {pinfo['mc']} 需要 Java {need}，工程配置的是 {pinfo['java']}")
        elif pinfo["java"] > need:
            warn(f"Java 版本偏高：MC {pinfo['mc']} 标准是 Java {need}，工程配置 {pinfo['java']}（一般无害，但打包目标版本别设错）")

    # 26.x 依赖写法
    mc = pinfo["mc"]
    if pinfo["platform"] == "fabric" and ge(mc, "26.1"):
        g = pinfo["gradle"]
        if "net.fabricmc.fabric-loom" not in g:
            err("26.x 的 Loom 插件 id 应为 'net.fabricmc.fabric-loom'（旧的 'fabric-loom' 会 remap）")
        if re.search(r"^\s*mappings\s", g, re.M):
            warn("26.x 无 mappings，应删除 mappings 依赖行")
    if pinfo["platform"] == "neoforge" and ge(mc, "26.1"):
        g = pinfo["gradle"]
        if "net.neoforged.moddev" in g and "net.neoforged:neoforge:" in g:
            err("ModDevGradle 不应再声明 net.neoforged:neoforge 依赖（写在 neoForge { version = ... } 里）")
        if "Parchment" in pinfo["props"] or "parchment" in g.lower():
            info("检测到 Parchment 配置：26.1 起可移除（Mojang 参数名已内置），保留可获额外 javadoc")


def check_scaffold_gaps(root: Path, pinfo):
    """检查脚手架/模板层面的坑（2026-09-10 实测踩出来的三条）。"""
    # 1) minecraft_version_range 写成 [26.1] 是「精确等于」，而实际跑的是 26.1.2
    props = pinfo.get("props") or {}
    rng = props.get("minecraft_version_range") if isinstance(props, dict) else None
    if isinstance(rng, str):
        rng = rng.strip()
        if re.match(r"^\[[^,\]]+\]$", rng):
            warn(f"minecraft_version_range={rng} 是「精确等于」而不是区间。\n"
                 f"          NeoForge 26.1.2.x 实际带的是 MC 26.1.2，会直接报\n"
                 f"          'Mod xxx requires minecraft 26.1' 拒绝加载；\n"
                 f"          应写成 [26.1,26.2) 这种形式（上界放开到下一个次版本）。")

    # 2) 没有 gradle wrapper → ./gradlew 直接 No such file
    if not ((root / "gradlew").exists()
            and (root / "gradle" / "wrapper" / "gradle-wrapper.jar").exists()):
        warn("缺少 gradle wrapper（gradlew / gradle/wrapper/gradle-wrapper.jar）。\n"
             "          ./gradlew 会直接报 No such file；本机装了 Gradle 的话可以跑\n"
             "          `gradle wrapper --gradle-version 9.6.1` 生成一份。")

    # 3) 官方示例代码引用了 Config 类，但模板漏了这个文件 → 开箱编译失败
    for main_cls in root.rglob("ExampleMod.java"):
        if main_cls.with_name("Config.java").exists():
            continue
        try:
            text = main_cls.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if re.search(r"\bConfig\.[A-Za-z_]", text):
            warn(f"[模板残留] {main_cls.relative_to(root)} 引用了 Config 类，但同包没有 Config.java\n"
                 f"          → 示例代码直接编译失败。官方 MDK 自带这个文件\n"
                 f"          （NeoForge 用 ModConfigSpec / Forge 用 ForgeConfigSpec 两种版本）。")

    # 4) 把「编译用的构建号」当成 mods.toml 的依赖下限 → 玩家实例直接拒载
    #    2026-09-27 实测踩到：编译取最新正式版 26.1.2.109，模板把 ${neo_version} 直接塞进
    #    versionRange，于是产物声明 [26.1.2.109,)；而玩家实例停在 26.1.0.19-beta，
    #    启动即 "Mod xxx requires neoforge 26.1.2.109 or above" —— 连加载都不给。
    neo_ver = props.get("neo_version") if isinstance(props, dict) else None
    for meta in root.rglob("neoforge.mods.toml"):
        txt = read_text(meta)
        m = re.search(r'modId\s*=\s*"neoforge"[\s\S]*?versionRange\s*=\s*"([^"]+)"', txt)
        if not m:
            continue
        lower = m.group(1).strip()
        if "$" in lower:                      # 模板里还没展开
            if not (isinstance(props, dict) and props.get("neo_version_range")):
                err(f"{meta.relative_to(root)} 的 neoforge 依赖用了 ${{neo_version_range}}，\n"
                    f"          但 gradle.properties 里没有 neo_version_range 这个键 → 构建会因缺属性失败。\n"
                    f"          （该键应当存在，且是小版本起点，如 [26.1.0,)）")
            continue
        m2 = re.match(r"^\[([^,\]]+)", lower)
        if not m2:
            continue
        start = m2.group(1).strip()
        seg = re.split(r"[.\-+]", start)
        if neo_ver and start == neo_ver:
            err(f"{meta.relative_to(root)} 的 neoforge 依赖下限写成了编译构建号 {start}：\n"
                f"          玩家实例常停在同小版本更老的构建（如 26.1.0.19-beta），启动会直接报\n"
                f"          'Mod xxx requires neoforge {start} or above' 拒绝加载（实测踩过）。\n"
                f"          改成小版本起点（如 [26.1.0,)）——同小版本内 API 一致，一个产物覆盖全部 26.1.x；\n"
                f"          上限交给 minecraft 的 versionRange 卡。")
        elif len(seg) >= 3 and seg[2] != "0":
            warn(f"{meta.relative_to(root)} 的 neoforge 依赖下限 {start} 第三段不是 0：\n"
                 f"          下限应按小版本起点写（如 [26.1.0,)），否则停在旧构建的实例会被挡在门外。")


def check_antipatterns(root: Path, pinfo):
    mc = pinfo["mc"]
    if not mc:
        return
    hits = {}
    for f in list(root.rglob("*.java")) + list(root.rglob("*.gradle")):
        txt = read_text(f)
        for pat, msg, since, plat in ANTIPATTERNS:
            if plat not in ("both", pinfo["platform"]):
                continue
            if not ge(mc, since):
                continue
            if re.search(pat, txt):
                rel = f.relative_to(root)
                hits.setdefault((msg, since), []).append(str(rel))
    # 对 gradle 文件里的 fabric 规则做平台豁免
    for (msg, since), files in sorted(hits.items()):
        warn(f"[反模式] {msg}\n          自 {since} 起失效，命中 {len(files)} 处：{', '.join(files[:3])}"
             + (f" 等" if len(files) > 3 else ""))


def collect_registered_ids(root: Path):
    items, blocks = set(), set()
    for f in root.rglob("*.java"):
        txt = read_text(f)
        for m in REG_CALL_RE.finditer(txt):
            receiver = m.group(1) or ""
            method = m.group(2)
            rid = m.group(3)
            if "/" in rid:
                continue
            # 只看当前行判断是否 BLOCK/ITEM；窗口取前 N 字符会越界吃到上一行的关键字，导致误判
            line_start = txt.rfind("\n", 0, m.start()) + 1
            line = txt[line_start:m.start()]
            ctx = receiver + " " + line
            # 创造栏 / 数据组件 / 方块实体 等注册表不是物品 → 跳过
            if NON_ITEM_REGISTRY_RE.search(ctx) and not re.search(r"\b(ITEMS?|BLOCKS?)\b", receiver, re.I):
                continue
            # 方法名优先：registerSimpleBlockItem 归物品（它注册进物品表），
            # registerSimpleBlock/registerBlock 归方块，避免被 "BLOCK" 抢先命中
            if re.search(r"(SimpleBlockItem|SimpleItem|registerItem)", method):
                items.add(rid)
            elif re.search(r"(SimpleBlock|registerBlock)", method):
                blocks.add(rid)
            elif re.search(r"ITEM", ctx):
                items.add(rid)
            elif re.search(r"BLOCK", ctx):
                blocks.add(rid)
            else:
                # 行内无信号（如跨行链式调用）时，退化为就近窗口判断
                near = txt[max(0, m.start() - 120):m.start()]
                if re.search(r"BLOCK", near):
                    blocks.add(rid)
                else:
                    items.add(rid)
        for m in REG_IDENT_RE.finditer(txt):
            # fromNamespaceAndPath 也可能在造 tag / ResourceKey，别当物品
            line_start = txt.rfind("\n", 0, m.start()) + 1
            line = txt[line_start:m.start() + len(m.group(0))]
            if NON_ITEM_CONTEXT_RE.search(line):
                continue
            items.add(m.group(1))
    return items, blocks


def ids_with_no_loot(root: Path):
    """找出显式声明了 noLootTable() 的方块 id。

    2026-09-11 加：传送门/火这类 .noLootTable() 的方块本来就不该有战利品表，
    原来一律报"缺战利品表"是误报。
    """
    out = set()
    for f in root.rglob("*.java"):
        txt = read_text(f)
        for m in re.finditer(r"[\"']([a-z0-9_./]+)[\"']", txt):
            rid = m.group(1)
            if "/" in rid:
                continue
            tail = txt[m.end():m.end() + 600]
            nxt = tail.find("registerBlock")   # 别吃到下一个方块注册
            if nxt != -1:
                tail = tail[:nxt]
            if "noLootTable()" in tail:
                out.add(rid)
    return out


def check_resources(root: Path, pinfo):
    modid = pinfo["modid"]
    if not modid or "${" in modid:
        return
    mc = pinfo["mc"] or "1.20.1"
    assets = root / "src" / "main" / "resources" / "assets" / modid
    data = root / "src" / "main" / "resources" / "data" / modid

    if not assets.exists():
        warn(f"缺少资源目录：{assets.relative_to(root)}（没有模型/材质/语言文件）")
        return

    items, blocks = collect_registered_ids(root)
    if not items and not blocks:
        info("未在源码中识别到注册项（若用自定义注册方式属正常，请手动核对资源）")
        return

    no_loot_ids = ids_with_no_loot(root)

    def report(level, msg):
        """模板自带的 example_* 示例内容缺资源只提醒，不算错误（用户本来就会删掉）。"""
        (warn if "example" in msg else level)(msg)

    use_item_defs = ge(mc, "1.21.4")
    for iid in sorted(items):
        legacy = assets / "models" / "item" / f"{iid}.json"
        newdef = assets / "items" / f"{iid}.json"
        if not legacy.exists() and not newdef.exists():
            report(err, f"物品 '{iid}' 缺模型：应放 {legacy.relative_to(root)}")
        elif use_item_defs and not newdef.exists() and legacy.exists():
            info(f"物品 '{iid}' 只有旧式 models/item 模型；1.21.4+ 建议加 {newdef.relative_to(root)} 的模型定义")

    for bid in sorted(blocks):
        bs = assets / "blockstates" / f"{bid}.json"
        if not bs.exists():
            report(err, f"方块 '{bid}' 缺 blockstate：应放 {bs.relative_to(root)}")
        loot_new = data / "loot_table" / "blocks" / f"{bid}.json"
        loot_old = data / "loot_tables" / "blocks" / f"{bid}.json"
        if not loot_new.exists() and not loot_old.exists():
            if bid in no_loot_ids:
                info(f"方块 '{bid}' 声明了 noLootTable()，本来就不需要战利品表（跳过）")
            else:
                report(warn, f"方块 '{bid}' 缺战利品表 → 挖掘不掉落任何东西（应放 {loot_new.relative_to(root)}）")
        if ge(mc, "1.21") and loot_old.exists():
            err(f"战利品表目录用了旧名 loot_tables/，1.21+ 应为 loot_table/")

    # 语言文件
    for lang in ("en_us.json", "zh_cn.json"):
        lf = assets / "lang" / lang
        if lf.exists():
            try:
                d = json.loads(read_text(lf))
            except json.JSONDecodeError as e:
                err(f"{lang} 不是合法 JSON：{e}")
                continue
            # 方块对应的 BlockItem 与方块同名，显示名走 block.* 键（原版也是这样），
            # 所以同名 id 只要求 item.* 与 block.* 二选一存在即可（2026-09-09 修）
            missing = [f"item.{modid}.{i}" for i in sorted(items)
                       if f"item.{modid}.{i}" not in d and not (i in blocks and f"block.{modid}.{i}" in d)]
            missing += [f"block.{modid}.{b}" for b in sorted(blocks) if f"block.{modid}.{b}" not in d]
            if missing:
                warn(f"{lang} 缺少 {len(missing)} 个翻译键（前5个）：{', '.join(missing[:5])}")
        else:
            info(f"没有 {lang}（lang 目录下应有 en_us.json）")

    # 数据目录单复数
    for old, new in (("recipes", "recipe"), ("loot_tables", "loot_table"),
                     ("advancements", "advancement"), ("structures", "structure")):
        if (data / old).exists() and ge(mc, "1.21"):
            err(f"数据目录 '{old}/' 是旧名，1.21+ 应为 '{new}/'")
    for old, new in (("tags/blocks", "tags/block"), ("tags/items", "tags/item"),
                     ("tags/entity_types", "tags/entity_type"), ("tags/fluids", "tags/fluid")):
        if (data / old).exists() and ge(mc, "1.21"):
            err(f"标签目录 '{old}/' 是旧名，1.21+ 应为 '{new}/'")


def check_template_leftovers(root: Path, pinfo):
    """检测官方模板的示例内容是否被替换掉。"""
    modid = pinfo["modid"] or ""
    marks = ["example_item", "example_block", "example_tab", "examplemod",
             "Example Mod", "YourNameHere", "OtherNameHere", "modid.mixins",
             "com.example"]
    # 工程实际包名本身就是 com.example.*（脚手架默认包名）时，
    # "com.example" 不是残留而是合法包名，误报剔除（2026-09-07 实测）
    pkg = pinfo.get("package") or ""
    if pkg.startswith("com.example"):
        marks.remove("com.example")
    hits = {}
    for f in list(root.rglob("*.java")) + list(root.rglob("*.json")) + \
             [root / "gradle.properties", root / "build.gradle"]:
        if not f.is_file():
            continue
        txt = read_text(f)
        for m in marks:
            if m in txt:
                rel = str(f.relative_to(root))
                if m == "modid.mixins" and modid and f"{modid}.mixins" in txt:
                    continue
                hits.setdefault(m, []).append(rel)
    if hits:
        detail = "；".join(f"{k}（{len(v)} 处）" for k, v in sorted(hits.items()))
        warn(f"[模板残留] 仍是官方示例内容：{detail}\n"
             f"          这些是模板示例代码，正式发布前应替换成自己的内容")


def check_version(root: Path, pinfo):
    """版本号三处一致性：gradle.properties ↔ build/libs 产物 ↔ jar 内元数据。

    逻辑不在这里重写一遍 —— 复用 版本号.py（两边逐字同步的公共资产），
    免得「检查工程说没问题 / 版本号.py 说不一致」这种自相矛盾。
    """
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        import importlib
        vmod = importlib.import_module("版本号")
    except Exception as e:
        warn(f"[版本] 版本号.py 载入失败，跳过版本检查：{e}")
        return
    try:
        errs, wrns, infos = vmod.audit(root)
    except Exception as e:
        warn(f"[版本] 版本检查异常（不影响其它检查）：{e}")
        return
    for x in errs:
        err("[版本] " + x)
    for x in wrns:
        warn("[版本] " + x)
    for x in infos:
        info("[版本] " + x)


def check_provenance(root: Path, pinfo):
    """鉴别工程出身：MCreator 产物 / lowcodefml 数据包 / 手写。"""
    # MCreator：源码里出现 net/mcreator 包 + 扁平 Init 类
    mcreator = False
    for f in root.rglob("*.java"):
        txt = read_text(f)
        if "net.mcreator" in txt or "net/mcreator" in txt:
            mcreator = True
            break
    if mcreator:
        warn("[出身] 检测到 net.mcreator 包 → 这是 MCreator 生成的工程。\n"
             f"          MCreator 代码与手写范式差异大，若需迁移建议整体重写而非逐行移植（见 references/实战拆解-真实mod架构.md §4）")
    # lowcodefml：mods.toml 里 modLoader=lowcodefml
    for cand in (root / "src" / "main" / "resources" / "META-INF" / "mods.toml",
                 root / "src" / "main" / "resources" / "META-INF" / "neoforge.mods.toml"):
        if cand.is_file():
            txt = read_text(cand)
            if "lowcodefml" in txt:
                info("出身：lowcodefml 数据包式 mod（无 Java 逻辑）→ 内容在 data/ 目录，当作 datapack 维护即可")
            elif "javafml" in txt:
                pass  # 正常手写


def check_json_strict(root: Path, pinfo):
    """1.21.6+ 严格 JSON：不容许注释与尾逗号。"""
    strict = ge(pinfo["mc"] or "1.20.1", "1.21.6")
    res = root / "src" / "main" / "resources"
    for f in res.rglob("*.json"):
        if ".cache" in f.parts or "generated" in f.parts:
            continue
        txt = read_text(f)
        if not txt:
            continue
        try:
            json.loads(txt)
        except json.JSONDecodeError as e:
            if strict:
                err(f"JSON 解析失败（1.21.6+ 为严格模式）：{f.relative_to(root)} → {e}")
            else:
                warn(f"JSON 解析失败：{f.relative_to(root)} → {e}")


def main():
    ap = argparse.ArgumentParser(
        description="源码工程体检：反模式 / 资源完整性 / 版本匹配 / 出身鉴别（MCreator 等）",
        epilog="退出码：0=干净 / 1=发现错误（可串进流水线当闸门）")
    ap.add_argument("project", help="mod 源码工程目录")
    args = ap.parse_args()
    root = Path(args.project).resolve()
    if not root.is_dir():
        print(f"[错误] 目录不存在：{root}")
        print("  · 传 mod 源码工程目录（有 build.gradle / src/ 的那一层）")
        print("  · 若是整合包实例（只有 mods/ config/），本 skill 不管 —— 走 MC一站式工坊（mc-one-stop-workshop）")
        sys.exit(2)

    pinfo = detect_project(root)
    check_basics(root, pinfo)
    check_antipatterns(root, pinfo)
    check_template_leftovers(root, pinfo)
    check_resources(root, pinfo)
    check_json_strict(root, pinfo)
    check_provenance(root, pinfo)
    check_scaffold_gaps(root, pinfo)
    check_version(root, pinfo)

    print("=" * 64)
    print(f"工程检查报告：{root}")
    print("=" * 64)
    if ERRORS:
        print(f"\n❌ 错误 {len(ERRORS)} 项（会导致编译失败或运行时失效）：")
        for e in ERRORS:
            print(f"   • {e}")
    if WARNS:
        print(f"\n⚠️  警告 {len(WARNS)} 项：")
        for w in WARNS:
            print(f"   • {w}")
    if INFOS:
        print(f"\nℹ️  提示 {len(INFOS)} 项：")
        for i in INFOS:
            print(f"   • {i}")
    if not ERRORS and not WARNS:
        print("\n✅ 未发现问题。")
    print("\n注意：本检查为静态扫描，不能替代 gradlew build。"
          "\n     资源遗漏类问题建议进游戏用 F3+T 重载资源后逐一核对。")
    sys.exit(1 if ERRORS else 0)


if __name__ == "__main__":
    main()

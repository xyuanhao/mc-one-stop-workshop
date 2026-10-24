#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Minecraft 模组迁移检查器（低版本 → 高版本）

用法:
  python 迁移检查.py <工程目录> --to 26.1
  python 迁移检查.py <工程目录> --to 1.21.1 --from 1.20.1
  python 迁移检查.py <工程目录> --list

作用：
  1. 识别工程当前版本（源版本）
  2. 扫描 (源版本, 目标版本] 区间内所有已知破坏性变更
  3. 输出「迁移清单」：每条列出需要处理/已满足/无法自动判断
  4. 代码层变更会实际扫描源码，报告是否命中

与 检查工程.py 的区别：
  检查工程.py  = 当前工程有没有病（静态体检）
  迁移检查.py  = 从 A 版迁到 B 版要动哪些地方（迁移路线图）
"""

import argparse
import json
import re
import sys
from pathlib import Path

DONE, TODO, UNSURE, NOP = [], [], [], []

def done(msg): DONE.append(msg)
def todo(msg): TODO.append(msg)
def unsure(msg): UNSURE.append(msg)
def nop(msg): NOP.append(msg)

def vkey(v):
    parts = []
    for p in str(v).split("."):
        m = re.match(r"^(\d+)", p)
        parts.append(int(m.group(1)) if m else 0)
    return tuple(parts)

def le(a, b): return vkey(a) <= vkey(b)
def gt(a, b): return vkey(a) > vkey(b)

def read_text(p: Path):
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return ""

def scan_tree(root: Path, pattern: re.Pattern):
    """在整个工程文本文件里找正则命中，返回命中的相对路径列表。"""
    hits = []
    for f in list(root.rglob("*.java")) + list(root.rglob("*.json")) + \
             list(root.rglob("*.gradle")) + list(root.rglob("*.toml")) + \
             list(root.rglob("*.properties")) + list(root.rglob("*.mcmeta")) + \
             list(root.rglob("*.cfg")) + list(root.rglob("*.tweaker")):
        if not f.is_file():
            continue
        txt = read_text(f)
        if txt and pattern.search(txt):
            hits.append(str(f.relative_to(root)))
    return hits

def detect(root: Path):
    """识别平台、MC 版本、modid、构建脚本内容。"""
    pinfo = {"platform": None, "mc": None, "modid": None, "gradle": "",
             "props": {}, "modmeta": ""}
    gp = root / "gradle.properties"
    if gp.exists():
        for line in read_text(gp).splitlines():
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k, v = s.split("=", 1)
                pinfo["props"][k.strip()] = v.strip()
    g = read_text(root / "build.gradle")
    pinfo["gradle"] = g
    if "net.neoforged" in g:
        pinfo["platform"] = "neoforge"
    elif "fabric-loom" in g:
        pinfo["platform"] = "fabric"
    elif "net.minecraftforge.gradle" in g:
        pinfo["platform"] = "forge"
    pinfo["mc"] = pinfo["props"].get("minecraft_version")
    pinfo["modid"] = pinfo["props"].get("mod_id")
    fmod = root / "src" / "main" / "resources" / "fabric.mod.json"
    if fmod.exists():
        try:
            d = json.loads(read_text(fmod))
            pinfo["modid"] = d.get("id")
            for dep, rng in (d.get("depends") or {}).items():
                if dep == "minecraft":
                    pinfo["mc"] = str(rng).lstrip("~>=^ ")
        except Exception:
            pass
    return pinfo

# ============================================================
# 规则库
# 每项: (since, platform, 标题, 代码正则或None, 说明)
#   since     = 该变更生效的 MC 版本（迁移过此版本才需要处理）
#   platform  = "both"/"neoforge"/"fabric"/"forge"
#   scan      = None 表示无法代码扫描，总是提示；否则扫源码判断命中
# ============================================================

# 代码层变更（可扫描）
CODE_RULES = [
    ("1.20.5", "both", "物品 NBT → Data Components",
     re.compile(r"getOrCreateTag\s*\(\s*\)|getOrCreateTagElement|\.getTag\s*\(\s*\)\s*\.(get|put|has|remove|set)"),
     "stack.getOrCreateTag()... 已废，改 DataComponentType。见 迁移指南 §3.2"),
    ("1.20.5", "neoforge", "网络包 → CustomPacketPayload + StreamCodec",
     re.compile(r"SimpleChannel|RegisterPayloadHandlerEvent|NetworkDirection\.PLAY_TO_SERVER"),
     "1.20.5 起 SimpleChannel 废弃。见 代码片段 §6"),
    ("1.21", "both", "数据目录去复数 (recipes/→recipe/ 等)",
     None,
     "检查 data/ 下旧复数目录。见 迁移指南 §3.16"),
    ("1.21", "both", "Registry.X → Registries.X",
     re.compile(r"Registry\.(?:BLOCK|ITEM|FLUID|ENTITY_TYPE|MENU|BLOCK_ENTITY_TYPE)\b"),
     "Registry 类已拆成 Registries。"),
    ("1.21", "both", "Recipe<Container> → Recipe<RecipeInput>",
     re.compile(r"implements\s+Recipe\s*<\s*(?:[\w.]+\.)?Container\s*>|Recipe\s*<\s*(?:[\w.]+\.)?Container"),
     "matches(RecipeInput, Level)。"),
    ("1.21", "both", "附魔/属性/药水 Holder 化",
     None,
     "Enchantments.XXX 变成 ResourceKey，需 Holder<Enchantment>。见 版本差异矩阵 §3.10"),
    ("1.21", "neoforge", "ResourceLocation 构造器失效",
     re.compile(r"new\s+(?:[\w.]+\.)?ResourceLocation\s*\("),
     "改 fromNamespaceAndPath/parse。"),
    ("1.21", "fabric", "Identifier 构造器失效",
     re.compile(r"new\s+(?:[\w.]+\.)?Identifier\s*\("),
     "改 Identifier.of(...)/fromNamespaceAndPath(...)。"),
    ("1.21.2", "both", "注册表方法改名",
     re.compile(r"\.getHolderOrThrow\s*\(|\.registryOrThrow\s*\(|\.getEntry\s*\(|\.entryOf\s*\("),
     "getHolderOrThrow→getOrThrow、registryOrThrow→lookupOrThrow 等。"),
    ("1.21.2", "neoforge", "BlockEntityType 移除 Builder",
     re.compile(r"BlockEntityType\.Builder|\.build\s*\(\s*null\s*\)"),
     "直接 new BlockEntityType<>(...)。"),
    ("1.21.2", "fabric", "FabricBlockEntityType.Builder 改名",
     re.compile(r"FabricBlockEntityType\.Builder"),
     "用 FabricBlockEntityTypeBuilder。"),
    ("1.21.4", "both", "物品模型定义迁移 (items/*.json)",
     None,
     "新增 assets/<ns>/items/<id>.json；旧 models/item overrides 失效。见 资源文件 §5.3"),
    ("1.21.5", "both", "武器/工具/盔甲基类移除",
     re.compile(r"extends\s+(?:[\w.]+\.)?(?:SwordItem|DiggerItem|ArmorItem)\b|extends\s+(?:[\w.]+\.)?(?:PickaxeItem|AxeItem|ShovelItem|HoeItem)"),
     "SwordItem/DiggerItem/ArmorItem 已删除，改数据组件。见 迁移指南 §3.9"),
    ("1.21.5", "both", "NBT API 返回 Optional",
     re.compile(r"\.getString\s*\(\s*\)|\.getInt\s*\(\s*\)|\.getBoolean\s*\(\s*\)|\.getList\s*\(|\.getCompound\s*\(|\.getFloat\s*\(|\.getDouble\s*\("),
     "改 get*Or(key, 默认值) 或 .orElse(...)。见 迁移指南 §3.10"),
    ("1.21.9", "neoforge", "FML 环境 API 改名",
     re.compile(r"FMLEnvironment\.(?:dist|production)\b|FMLLoader\.getGamePath\s*\("),
     "FMLEnvironment.getDist()/isProduction()、FMLLoader.getCurrent().getGameDir()。"),
    ("1.21.9", "neoforge", "附加运行时类路径移除",
     re.compile(r"additionalRuntimeClasspath"),
     "1.21.9 起禁止，改 jarJar(implementation(...))。"),
    ("1.21.9", "both", "按键类别 KeyMapping.Category",
     re.compile(r"key\.categories|KeyMapping\.Category"),
     "String 类别改 record + registerCategory。"),
    ("1.21.11", "both", "ResourceLocation → Identifier (Mojang 侧)",
     re.compile(r"\bResourceLocation\b"),
     "纯机械替换类名。"),
    ("1.21.11", "both", "RenderType → RenderTypes / javax → JSpecify",
     re.compile(r"\bRenderType\.(?:GUI|SOLID|TRANSLUCENT)|javax\.annotation\.Nullable"),
     "RenderType 静态成员移入 RenderTypes；@Nullable 改 org.jspecify。"),
    ("26.1", "both", "ChunkPos 构造/转换改名",
     re.compile(r"new\s+(?:[\w.]+\.)?ChunkPos\s*\(|ChunkPos\.asLong\s*\("),
     "ChunkPos.containing/pack/unpack。"),
    ("26.1", "neoforge", "ItemStack 早期实例化 → ItemStackTemplate",
     None,
     "注册表加载前表示物品用 ItemStackTemplate。见 版本差异矩阵 §9.3"),
    ("26.1", "both", "注册 API lambda 化",
     None,
     "1.21.x 传 Properties 实例，26.x 传 lambda。见 版本差异矩阵 §9.3 ⑤"),
    ("26.1", "both", "GUI 渲染方法改名",
     re.compile(r"\brenderBackground\s*\(|\brenderBg\s*\(|\brenderLabels\s*\(|Screen#render\b"),
     "26.1 起改 extractBackground/extractLabels。"),
    ("26.1", "fabric", "Fabric API 改名对齐官方名",
     re.compile(r"ItemGroupEvents|HudRenderCallback|fabric-convention-tags-v1|fabric-loot-api-v2"),
     "ItemGroupEvents→CreativeModeTabEvents、HudRenderCallback→HudElementRegistry。"),
    ("26.1", "fabric", "Fabric mod id 'fabric' 依赖已移除",
     re.compile(r'"fabric"\s*:\s*"'),
     "fabric.mod.json 的 depends 用 fabric-api 不是 fabric。"),
]

# 构建层变更（无法靠扫描命中语义，列出提醒项）
BUILD_RULES = [
    ("1.20.2", "forge", "Forge → NeoForge 生态切换",
     "1.20.2 起两生态分离；1.21+ Forge 基本无构建。若目标 ≥1.21 且原用 Forge：换 NeoForge 模板（本 skill templates/neoforge-*），imports net.minecraftforge.* → net.neoforged.*"),
    ("1.21.9", "neoforge", "Gradle 插件最低版本",
     "MDG ≥ 2.0.112 / NG ≥ 7.0.192（1.21.9 支持）"),
    ("26.1", "neoforge", "Java 25 + Gradle ≥ 9.1.0",
     "java.toolchain.languageVersion = JavaLanguageVersion.of(25)；gradle wrapper 升到 9.1.0+"),
    ("26.1", "neoforge", "Parchment 可移除",
     "26.1 起 Mojang 参数名内置，Parchment 不再是必需"),
    ("26.1", "fabric", "Loom 插件换代",
     "插件 id：fabric-loom → net.fabricmc.fabric-loom；modImplementation → implementation；remapJar → jar；删除 mappings 行"),
    ("26.1", "fabric", "Java 25 + Loom 1.15+",
     "loom ≥1.15、loader ≥0.18.4；IntelliJ 需 2025.3+"),
    ("26.1", "neoforge", "datagen run: data() → clientData()",
     "1.21.4 起 GatherDataEvent 拆 Client/Server；26.x 模板已用 clientData()"),
]

# 资源/数据层（人工核对）
RESOURCE_RULES = [
    ("1.20.5", "both", "give 命令/物品数据语法",
     "旧 NBT 字符串 → [组件=值] 方括号语法；自定义 NBT 数据可能丢"),
    ("1.21", "both", "数据目录名去复数",
     "tags/blocks→tags/block、recipes→recipe、loot_tables→loot_table、advancements→advancement、structures→structure"),
    ("1.21", "both", "附魔 JSON 数据驱动化",
     "data/minecraft/enchantment 结构变化；代码侧 Holder"),
    ("1.21.2", "both", "Ingredient JSON 简化",
     "{item:...} → \"minecraft:diamond\"；{tag:...} → \"#tag\""),
    ("1.21.4", "both", "物品模型定义 items/*.json",
     "新目录 assets/<ns>/items/；custom_model_data overrides → range_dispatch"),
    ("1.21.6", "both", "JSON 严格解析",
     "所有 JSON 不能带注释/尾逗号"),
    ("1.21.9", "both", "pack.mcmeta 小数化",
     "pack_format 改小数 + min_format/max_format"),
    ("26.1", "both", "世界存储重大变更",
     "升级存档前备份；旧存档可能不兼容"),
]


def main():
    ap = argparse.ArgumentParser(
        description="MC 模组迁移检查器（低版本→高版本）",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir", nargs="?", help="工程目录")
    ap.add_argument("--to", required=False, help="目标 MC 版本，如 26.1 / 1.21.1")
    ap.add_argument("--from", dest="src", required=False, help="源 MC 版本（默认自动从工程检测）")
    ap.add_argument("--list", action="store_true", help="列出规则库")
    args = ap.parse_args()

    if args.list:
        print("== 代码层规则 ==")
        for since, plat, title, _, _ in sorted(CODE_RULES, key=lambda x: vkey(x[0])):
            print(f"  [{since:7s}] {title}")
        print("\n== 构建层规则 ==")
        for since, plat, title, _ in sorted(BUILD_RULES, key=lambda x: vkey(x[0])):
            print(f"  [{since:7s}] {title}")
        print("\n== 资源层规则 ==")
        for since, plat, title, _ in sorted(RESOURCE_RULES, key=lambda x: vkey(x[0])):
            print(f"  [{since:7s}] {title}")
        return

    if not args.dir or not args.to:
        ap.print_help()
        sys.exit(2)

    root = Path(args.dir).resolve()
    if not root.is_dir():
        print(f"[错误] 工程目录不存在：{root}")
        print("  · 传 mod 源码工程目录（有 build.gradle / gradle.properties 的那一层）")
        print("  · 若是整合包实例（只有 mods/ config/），该用 MC一站式工坊 的 整合包体检.py")
        sys.exit(2)
    to = args.to
    pinfo = detect(root)
    plat = pinfo["platform"] or "unknown"

    def is_placeholder(v):
        return not v or "@" in str(v) or "${" in str(v) or not re.search(r"\d", str(v))

    if args.src:
        src = args.src
    elif pinfo["mc"] and not is_placeholder(pinfo["mc"]):
        src = pinfo["mc"]
        if not le(src, to):
            print(f"[提示] 工程当前版本 {src} 已 ≥ 目标 {to}，无需迁移。")
            return
    else:
        print("[错误] 无法从工程识别源版本"
              + (f"（gradle.properties 的 minecraft_version 是占位符 '{pinfo['mc']}'，"
                 f"请先替换成实际版本或用 --from 手动指定）" if pinfo["mc"] else ""))
        print("  · 手动指定：--from 1.20.1")
        print("  · 确认工程根目录下有 gradle.properties（模板占位符需先替换）")
        sys.exit(2)

    print("=" * 66)
    print(f"迁移路线图：{pinfo['platform'] or '未知平台'}  {src} → {to}")
    print(f"工程：{root}")
    print("=" * 66)

    # 平台不匹配检查
    if plat == "forge" and vkey(to) >= vkey("1.21"):
        line = f"  ● [平台] Forge 在 1.21+ 基本无构建 → 目标 {to} 需要先切 NeoForge（或 Fabric）"
        unsure(line)
        print(line)

    active = [r for r in CODE_RULES
              if gt(r[0], src) and le(r[0], to) and r[1] in ("both", plat)]
    print(f"\n[代码层] 区间 {src} → {to} 的破坏性变更共 {len(active)} 条：\n")
    for since, plat2, title, scan_re, note in sorted(active, key=lambda x: vkey(x[0])):
        if scan_re is None:
            line = f"  ◇ [{since}] {title}\n         ↳ {note}（需人工核查代码）"
            unsure(line)
            print(line)
            continue
        hits = scan_tree(root, scan_re)
        if hits:
            line = (f"  ● [{since}] {title} ← 命中 {len(hits)} 处：{', '.join(sorted(set(hits))[:3])}"
                    + (" 等" if len(hits) > 3 else "")
                    + f"\n         ↳ {note}")
            todo(line)
            print(line)
        else:
            line = f"  ○ [{since}] {title}（未在源码中命中，应无需处理）"
            done(line)
            print(line)

    b_active = [r for r in BUILD_RULES
                if gt(r[0], src) and le(r[0], to) and r[1] in ("both", plat)]
    if b_active:
        print(f"\n[构建层] 必须调整（{len(b_active)} 项）\n")
        for since, plat2, title, note in b_active:
            line = f"  ● [{since}] {title}\n         ↳ {note}"
            todo(line)
            print(line)

    r_active = [r for r in RESOURCE_RULES
                if gt(r[0], src) and le(r[0], to) and r[1] in ("both", plat)]
    if r_active:
        print(f"\n[资源/数据层] 人工核对（{len(r_active)} 项）\n")
        for since, plat2, title, note in r_active:
            line = f"  ◇ [{since}] {title}\n         ↳ {note}"
            unsure(line)
            print(line)

    print("\n" + "=" * 66)
    print(f"汇总：需处理 {len(TODO)} 项 | 需人工核实 {len(UNSURE)} 项 | 已满足/未命中 {len(DONE)} 项")
    print("=" * 66)
    print("\n下一步建议：")
    print("  1. 复制目标版本的官方模板覆盖构建文件（templates/ 目录有现成的）")
    print("  2. 升级 JDK / Gradle wrapper")
    print("  3. gradlew build → 逐个编译错误用『双工作区对照法』修复（见 迁移指南.md §2）")
    print("  4. 按上面的清单逐项核对，别只依赖编译通过")
    print("  5. gradlew runClient + runServer 双端验证")
    print("\n详细对照见 references/迁移指南.md 与 references/版本差异矩阵.md")
    sys.exit(1 if TODO else 0)


if __name__ == "__main__":
    main()

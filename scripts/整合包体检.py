#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
整合包体检：扫描 Minecraft 整合包实例目录，输出盘点报告 + 问题清单。

能查什么：
- 识别 MC 版本 / 加载器（Forge / NeoForge / Fabric / Quilt）
- mods 目录全量清单：modid、版本、名称、平台、体积
- 自动分类（内容/库/性能优化/客户端体验/API/世界生成/任务书...）
- **冲突与问题检测**：
  - 同 modid 装了多个版本（重复 jar 残留）
  - Fabric mod 混进 Forge 实例（需要 Sinytra Connector，否则加载失败）
  - Forge mod 混进 Fabric 实例
  - 声明的 MC 版本与实例版本不一致
  - 必需的依赖 mod 未安装
  - 被禁用（.jar.disabled）但被别的 mod 依赖
  - 超大 mod（>50MB）提示
- 配置目录盘点：config / kubejs / datapacks / resourcepacks / shaderpacks / scripts

用法:
  python 整合包体检.py <整合包目录>
  python 整合包体检.py <目录> --md 报告.md      # 同时输出 Markdown 报告
  python 整合包体检.py <目录> --mc 1.20.1       # 手动指定实例 MC 版本
"""
import os, re, sys, json, zipfile, argparse
from pathlib import Path
from collections import Counter, defaultdict

# ---------- 分类关键词（小写匹配 modid + 文件名）----------
CATEGORIES = [
    ("性能优化", ["sodium", "lithium", "ferrite", "radium", "xenon", "embeddium", "culling",
                 "optimization", "optimi", "modernfix", "servercore", "spark", "smoothchunk",
                 "immediatelyfast", "entityculling", "badoptimizations", "alltheleaks",
                 "memory", "leak", "fix-", "chunksending", "fastasync", "packetfix",
                 "gpumemleak", "toofast", "nochatreports", "connectivity", "recipeessentials"]),
    ("渲染/光影", ["iris", "oculus", "shader", "dynamiclights", "enhancedvisuals", "presencefootsteps",
                  "sound", "physics", "notenoughanimations", "playeranim", "firstperson",
                  "entity_model", "entity_texture", "acceleratedrendering", "watut"]),
    ("库/API/前置", ["lib", "api", "core", "architectury", "bookshelf", "kiwi", "zeta", "curios",
                    "geckolib", "azurelib", "patchouli", "cloth", "trinkets", "caelus",
                    "forgeconfig", "resourceful", "structure_gel", "terrablender", "citadel",
                    "config", "cupboard", "craterlib", "kotlin", "fabric-api", "connector",
                    "moonlight", "insanelib", "coroutil", "atlaslib", "iceberg", "wrapperlib",
                    "puzzleslib", "obscure", "owo", "smartbrainlib", "txnilib", "melody",
                    "konkrete", "fancymenu", "collective", "enderio", "ftblibrary"]),
    ("世界/结构生成", ["yungs", "biomes", "regions", "terralith", "biome", "structure", "dungeon",
                      "formations", "bunkers", "stony", "hearths", "worldgen", "lithostitched"]),
    ("UI/信息", ["jei", "rei", "jade", "hwyla", "waila", "searchables", "controlling", "bhmenu",
                 "betteradvancements", "ftbquests", "ftb", "pickupnotifier", "bettermodsbutton",
                 "paintings", "almanac", "jeresources", "justenough", "tooltips", "hud"]),
    ("任务书/指南", ["patchouli", "ftbquests", "betonquest", "necronomicon"]),
    ("科技/魔法", ["create", "immersive", "thermal", "mekanism", "botania", "ars", "bloodmagic",
                   "industrial", "applied", "refined", "powah", "pneumaticcraft", "draconic"]),
    ("战斗/装备", ["tacz", "spore", "difficult", "monster", "zombie", "caelus", "elytraslot",
                   "accessories", "bettercombat", "spartan", "weapon", "mutant"]),
    ("杂项内容", ["quark", "supplementaries", "sophisticated", "carryon", "corpse", "twilight",
                  "storagedrawers", "ironchest", "chipped", "handcrafted", "rechiseled"]),
]

# Forge loader 主版本 ↔ MC 版本 双向映射
# ⚠️ 关键：mods.toml 的 loaderVersion="[40,)" 是**最低要求**（Forge ≥40 都能跑），
#    不是"该 mod 只支持 1.18.2"。所以只能用来判断"实例 loader 够不够新"，不能当 mod 的 MC 版本。
LOADER_MC = {
    39: "1.18.1", 40: "1.18.2", 41: "1.19", 42: "1.19.1", 43: "1.19.2",
    44: "1.19.3", 45: "1.19.4", 46: "1.20", 47: "1.20.1", 48: "1.20.2",
    49: "1.20.4", 50: "1.20.6", 51: "1.21", 52: "1.21.1", 36: "1.16.5",
    37: "1.17.1",
}
MC_LOADER = {v: k for k, v in LOADER_MC.items()}

# ---------- jar 元数据读取 ----------
def read_jar_meta(path):
    """读取一个 mod jar 的元数据，返回 dict。"""
    info = {"file": os.path.basename(path), "size": os.path.getsize(path),
            # mc = 目标 MC 版本（来自 minecraft 依赖声明/文件名），None 表示未知
            "mc": None,
            "platform": None, "modid": None, "name": None, "version": None,
            # min_loader = Forge 最低 loader 要求（数字），min_mc = 其对应 MC 版本
            "min_loader": None, "min_mc": None,
            "deps": {}, "loader": None, "disabled": False, "modLoader": None,
            # 加载器插件：本身不是 mod，而是给 Forge/Fabric 提供能力的服务 jar
            # （如 Connector = Fabric 转译层、kotlinforforge = Kotlin 语言提供器）
            "is_plugin": False, "plugin_kind": None}
    raw_forge = ""          # mods.toml 原文，后处理阶段还要用
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()

            # ---------- ① 加载器插件识别（优先于 mod 元数据）----------
            # 这类 jar 没有 mods.toml / fabric.mod.json，靠 META-INF/services 里的
            # ServiceLoader 声明工作，不是"识别失败"，不该报警。
            # 注意：ServiceLoader 的"接口名"是**文件名**，文件内容是实现类名。
            # 判插件必须看文件名，看内容会全部漏掉。
            svc = [n for n in names if n.startswith("META-INF/services/")]
            svc_iface = " ".join(svc)      # 接口名清单
            svc_impl = ""
            for n in svc:
                try: svc_impl += z.read(n).decode("utf-8", "replace") + "\n"
                except Exception: pass
            if svc and re.search(r"(cpw\.mods\.modlauncher|forgespi\.locating|"
                                 r"net\.fabricmc\.loader|org\.quiltmc)", svc_iface):
                info["is_plugin"] = True
                if re.search(r"forgespi\.locating\.IModLocator", svc_iface):
                    info["plugin_kind"] = "mod locator（扩展 Forge 加载能力，如 Connector 转译层）"
                elif re.search(r"ITransformationService|ILanguageProvider", svc_iface):
                    info["plugin_kind"] = "语言/转换服务（如 KotlinForge）"
                else:
                    info["plugin_kind"] = "加载器服务"
                info["platform"] = "forge-plugin"
                # 插件仍尝试读 modid（部分插件同时是 mod），读不到用文件名兜底
                info["modid"] = info["modid"] or re.sub(
                    r"[-_ ]?\d[\w.\-+]*$", "", info["file"][:-4]).lower()[:32]

            # Forge / NeoForge
            toml_path = None
            for cand in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml"):
                if cand in names:
                    toml_path = cand
                    break
            if toml_path:
                raw_forge = z.read(toml_path).decode("utf-8", "replace")
                raw = raw_forge
                info["platform"] = "neoforge" if "neoforge" in toml_path else "forge"
                try:
                    import tomllib
                    d = tomllib.loads(raw)
                    mods = d.get("mods") or []
                    if mods:
                        m0 = mods[0]
                        info["modid"] = m0.get("modId") or m0.get("modid")
                        info["name"] = m0.get("displayName")
                        info["version"] = m0.get("version")
                    # ⚠️ loaderVersion / modLoader 是**顶层字段**，不在 [[mods]] 里
                    info["modLoader"] = d.get("modLoader")
                    lv = d.get("loaderVersion") or d.get("loader_version")
                    if lv is not None:
                        info["loader"] = str(lv)
                    d_deps = d.get("dependencies")
                    # [[dependencies.foo]] 在 tomllib 里解析成 {foo: [ {...} ]}，需展平
                    if isinstance(d_deps, dict):
                        for k, v in d_deps.items():
                            if isinstance(v, list):
                                for item in v:
                                    if isinstance(item, dict):
                                        info["deps"][item.get("modId", k)] = {
                                            "mandatory": bool(item.get("mandatory", True)),
                                            "range": item.get("versionRange", "*"),
                                        }
                            elif isinstance(v, dict):
                                info["deps"][k] = {
                                    "mandatory": bool(v.get("mandatory", True)),
                                    "range": v.get("versionRange", "*"),
                                }
                    elif isinstance(d_deps, list):
                        for dep in d_deps:
                            if isinstance(dep, dict) and dep.get("modId"):
                                info["deps"][dep["modId"]] = {
                                    "mandatory": bool(dep.get("mandatory", True)),
                                    "range": dep.get("versionRange", "*"),
                                }
                except Exception:
                    # 退回正则
                    m = re.search(r'^\s*modId\s*=\s*"([^"]+)"', raw, re.M)
                    if m: info["modid"] = m.group(1)
                    m = re.search(r'^\s*displayName\s*=\s*"([^"]+)"', raw, re.M)
                    if m: info["name"] = m.group(1)
                    m = re.search(r'^\s*version\s*=\s*"([^"]+)"', raw, re.M)
                    if m: info["version"] = m.group(1)
                dep_blocks = re.findall(
                    r'\[\[dependencies\.([^\]]+)\]\]\s*(.*?)(?=\n\[\[|\Z)', raw, re.S)
                for modid_ref, body in dep_blocks:
                    dm = re.search(r'modId\s*=\s*"([^"]+)"', body)
                    if not dm: continue
                    dep_id = dm.group(1)
                    mand = re.search(r'mandatory\s*=\s*(true|false)', body)
                    vr = re.search(r'versionRange\s*=\s*"([^"]+)"', body)
                    info["deps"][dep_id] = {
                        "mandatory": (mand.group(1) == "true") if mand else True,
                        "range": vr.group(1) if vr else "*",
                    }
            # Fabric / Quilt
            elif "fabric.mod.json" in names:
                raw = z.read("fabric.mod.json").decode("utf-8", "replace")
                try:
                    d = json.loads(raw)
                    info["platform"] = "fabric"
                    info["modid"] = d.get("id")
                    info["name"] = d.get("name")
                    info["version"] = d.get("version")
                    for k, v in (d.get("depends") or {}).items():
                        info["deps"][k] = {"mandatory": True, "range": str(v)}
                except json.JSONDecodeError:
                    info["platform"] = "fabric-broken"
            # Forge 的 MANIFEST 声明式 mod：没有 mods.toml，靠 MANIFEST 里的
            # FMLModType 声明（LIBRARY / LANGPROVIDER / MOD），如 kotlinforforge。
            elif "META-INF/MANIFEST.MF" in names:
                mf = z.read("META-INF/MANIFEST.MF").decode("utf-8", "replace")
                mt = re.search(r"^FMLModType\s*:\s*(\S+)", mf, re.M | re.I)
                if mt:
                    info["platform"] = "forge"
                    info["fmlmodtype"] = mt.group(1).strip().upper()
                    am = re.search(r"^Automatic-Module-Name\s*:\s*(\S+)", mf, re.M)
                    if am:
                        info["modid"] = am.group(1).strip()
                    info["modLoader"] = "javafml"
    except Exception as e:
        info["error"] = str(e)

    # 是否内置了 jarJar 依赖（用于判断"依赖缺失"是否为误报）
    try:
        with zipfile.ZipFile(path) as z:
            info["jarjar"] = any(n.startswith("META-INF/jarjar/") or
                                 n.startswith("META-INF/jars/") for n in z.namelist())
    except Exception:
        info["jarjar"] = False

    # 2.5) mixin compatibilityLevel：级别高于加载器自带的 Mixin 版本认可范围时，
    #      mod 加载阶段直接 MixinInitialisationError 崩（不写崩溃报告，只在启动器输出里）。
    #      实测：1.20.1 的 Mixin 0.8.5 只认到 JAVA_17，morecogwheels 声明 JAVA_21 → 启动必崩。
    try:
        with zipfile.ZipFile(path) as z:
            lv = []
            for n in z.namelist():
                if n.endswith("mixins.json") and n.count("/") <= 1:
                    try:
                        txt = z.read(n).decode("utf-8", "replace")
                    except Exception:
                        continue
                    mm = re.search(r'"compatibilityLevel"\s*:\s*"JAVA_(\d+)"', txt)
                    if mm:
                        lv.append(int(mm.group(1)))
            if lv:
                info["mixin_level"] = max(lv)
    except Exception:
        pass

    # 1) Forge 最低 loader 要求：mods.toml 顶层 loaderVersion（如 "[40,)" = 要求 Forge≥40）
    lv_raw = info.get("loader") or ""
    m = re.search(r"(\d+)", str(lv_raw))
    if not m and raw_forge:
        m = re.search(r'^\s*(?:loaderVersion|loader_version)\s*=\s*"\[?(\d+)', raw_forge, re.M)
    if m:
        try:
            lv = int(m.group(1))
            info["min_loader"] = lv
            if lv in LOADER_MC:
                info["min_mc"] = LOADER_MC[lv]
        except ValueError:
            pass

    # 2.5) 版本号占位符兜底：部分 mod 发布时没替换 ${file.jarVersion} 之类，
    #     显示成占位符没意义，从文件名里抓一个像版本号的串。
    ver = info.get("version") or ""
    if "${" in ver or not ver.strip():
        stem = info["file"][:-4]
        mv = re.search(r"(\d+\.\d+(?:\.\d+)?(?:[.\-]\w+)?)", stem)
        info["version"] = mv.group(1) if mv else ver

    # 2) 目标 MC 版本：解析声明的 minecraft 依赖范围 → 文件名推断
    lo = hi = None
    for k, v in (info.get("deps") or {}).items():
        if k in ("minecraft", "minecraft_version"):
            rng = v.get("range", "") if isinstance(v, dict) else str(v)
            lo, hi = parse_mc_range(rng)
            break
    if lo is None:
        lo = guess_mc_from_name(info["file"])
    info["mc"], info["mc_min"], info["mc_max"] = (lo or hi), lo, hi
    return info


def verkey(v):
    """版本号转可比较元组：1.20.1 → (1,20,1)"""
    try:
        return tuple(int(x) for x in str(v).split("."))
    except ValueError:
        return (0,)


def parse_mc_range(rng):
    """解析 MC 版本范围字符串，返回 (下界, 上界)（都可能为 None）。

    常见写法与语义：
      ">=1.18.2" / "[1.19,)" / "1.20.1"  → 只有下界（最低要求），上界 None
      "1.17-1.21.8" / ">=1.19 <=1.20.1"  → 有区间
      "[26.1]"                           → **恰好 26.1**（下界=上界，见下）
    取区间内所有合法 MC 版本号，最小为下界、最大为上界。

    🔴 2026-09-28 修 bug（**放过了一次真实崩溃**）：
      旧版对 `[26.1]` 返回 (26.1, None) —— 当成「26.1 及以上」，
      于是实例是 26.1.2 时判定「在范围内」，**不报警**。
      而 Maven 语义里 `[x]` 是**精确钉版**（只等于 x），
      该构建在 26.1.2 上必崩：

        Failed to start FML: Mod controlling requires minecraft 26.1
          Currently, minecraft is 26.1.2

      修法：明确识别「单个方括号、无逗号」= 精确钉版 → (v, v)，
      并置 `exact=True` 让调用方能给出更准的提示。
    """
    s = str(rng).strip().replace(" ", "")
    # `[26.1]` / `(26.1)` —— 单值方括号 = 精确钉版
    m = re.fullmatch(r"[\[\(]\s*(1\.\d+(?:\.\d+)?|2[2-9]\.\d+(?:\.\d+)?)\s*[\]\)]", s)
    if m and is_mc_version(m.group(1)):
        v = m.group(1)
        return v, v                      # 下界 == 上界 == 恰好这个版本

    vers = re.findall(r"(1\.\d+(?:\.\d+)?|2[2-9]\.\d+(?:\.\d+)?)", s)
    vers = [v for v in vers if is_mc_version(v)]
    if not vers:
        return None, None
    lo = min(vers, key=verkey)
    hi = max(vers, key=verkey)
    # 只有一个版本号 且 原文含下界符号 → 纯下界，不设上界
    if len(vers) == 1:
        return lo, None
    return lo, hi


def guess_mc_from_name(fname):
    """从文件名推断 MC 版本。
    策略：优先 `mc1.20.1` 形式 → 否则收集所有合法 MC 版本候选：
    - 0 个候选 → None
    - 恰好 1 个 → 用它
    - ≥2 个 → 跨版本兼容包（如 packetfixer-3.3.1-1.18-1.20.4-merged）→ None（不报冲突）
    """
    # 优先 mc 前缀
    m = re.search(r"mc(1\.\d+(?:\.\d+)?|2[2-9]\.\d+(?:\.\d+)?)", fname, re.I)
    if m:
        return m.group(1)
    # 收集所有候选
    cands = re.findall(r"(?<![\d.])(1\.\d+(?:\.\d+)?|2[2-9]\.\d+(?:\.\d+)?)(?![\d.])", fname)
    cands = [c for c in cands if is_mc_version(c)]
    if len(cands) == 1:
        return cands[0]
    return None


def is_mc_version(v):
    """粗略判断是否是真实存在的 MC 版本号（过滤 mod 自身版本号如 1.1.136）。"""
    parts = str(v).split(".")
    if len(parts) < 2:
        return False
    try:
        major, minor = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    if major == 1:
        return 7 <= minor <= 21
    return major >= 22


def classify(meta):
    """按 modid + 文件名分类。"""
    # 加载器插件单独一类（Connector / KotlinForge 之类，不是常规 mod）
    if meta.get("is_plugin"):
        return "加载器插件"
    # Forge MANIFEST 声明的库（FMLModType: LIBRARY），如 kotlinforforge
    if (meta.get("fmlmodtype") or "").upper() == "LIBRARY":
        return "库/API/前置"
    key = f"{meta.get('modid') or ''} {meta['file']}".lower()
    for cat, kws in CATEGORIES:
        for kw in kws:
            if kw in key:
                return cat
    return "其他内容"


def detect_instance_mc(root, mods_meta):
    """推断实例 MC 版本：优先实例配置 → mod 元数众数。"""
    # CurseForge/Prism 实例配置
    for cfg in ("minecraftinstance.json", "mmc-pack.json", "instance.cfg"):
        p = root / cfg
        if p.exists():
            try:
                txt = p.read_text(encoding="utf-8", errors="replace")
                m = re.search(r'"(?:gameVersion|mcVersion|minecraft)"\s*:\s*"([^"]+)"', txt)
                if m: return m.group(1), f"{cfg} 声明"
                m = re.search(r"IntendedVersion=([^\s]+)", txt)
                if m: return m.group(1), f"{cfg} 声明"
            except Exception:
                pass
    # 从 mod 元数据取众数
    vers = [m["mc"] for m in mods_meta if m.get("mc")]
    if vers:
        top, cnt = Counter(vers).most_common(1)[0]
        return top, f"从 {cnt} 个 mod 的元数推断"
    return None, "未能确定"


def detect_instance_platform(root, mods_meta):
    """推断加载器：看目录特征 + mod 平台众数。"""
    if (root / "mods" / ".connector").exists() or any(
            "connector" in (m.get("modid") or "").lower() for m in mods_meta):
        has_connector = True
    else:
        has_connector = False
    plats = Counter(m["platform"] for m in mods_meta if m.get("platform"))
    if not plats:
        return "unknown", has_connector
    top = plats.most_common(1)[0][0]
    label = {"forge": "Forge", "neoforge": "NeoForge", "fabric": "Fabric"}.get(top, top)
    if has_connector and top in ("forge", "neoforge"):
        label += " + Sinytra Connector（混装 Fabric mod）"
    return label, has_connector


def scan_config_dirs(root):
    """盘点配置目录。"""
    out = {}
    for d, desc in [("config", "mod 配置文件"),
                    ("kubejs", "KubeJS 脚本"),
                    ("datapacks", "数据包（世界级）"),
                    ("resourcepacks", "资源包"),
                    ("shaderpacks", "光影包"),
                    ("scripts", "脚本（CraftTweaker 等）"),
                    ("defaultconfigs", "默认配置（新世界用）"),
                    ("saves", "存档")]:
        p = root / d
        if p.is_dir():
            files = [f for f in p.rglob("*") if f.is_file()]
            out[d] = {"desc": desc, "count": len(files)}
    return out


def main():
    ap = argparse.ArgumentParser(description="整合包体检")
    ap.add_argument("dir", help="整合包实例目录（如 .minecraft/versions/xxx）")
    ap.add_argument("--md", default=None, help="同时输出 Markdown 报告到该文件")
    ap.add_argument("--mc", default=None, help="手动指定实例 MC 版本")
    args = ap.parse_args()

    root = Path(args.dir).resolve()
    if not root.is_dir():
        print(f"[错误] 目录不存在：{root}")
        print("  · 整合包实例目录长这样（PCL/HMCL）：")
        print("      <你的 .minecraft>/versions/<整合包名>/   ← 里面有 mods/ config/")
        print("  · 只有 mods 目录也可以：直接把这个 mods 路径传进来就行了")
        sys.exit(2)

    mods_dir = root / "mods"
    if not mods_dir.is_dir():
        # 也许直接指向了 mods 目录
        if root.name == "mods":
            mods_dir = root
            root = root.parent
        else:
            print(f"[错误] 在 {root} 里找不到 mods 目录 → 这看起来不是整合包实例目录")
            print("  · 确认路径末尾是 versions/<整合包名>（不是 .minecraft 根目录）")
            print("  · 若是**源码工程**（有 build.gradle / src/），该用 检查工程.py，不是本脚本")
            sys.exit(2)
            sys.exit(2)

    # ---- 扫描 mods ----
    jars = sorted(list(mods_dir.glob("*.jar")) + list(mods_dir.glob("*.jar.disabled")))
    metas = []
    print(f"扫描 {len(jars)} 个 jar ...")
    for j in jars:
        m = read_jar_meta(j)
        if j.name.endswith(".disabled"):
            m["disabled"] = True
        m["cat"] = classify(m)
        metas.append(m)

    mc, mc_src = (args.mc, "手动指定") if args.mc else detect_instance_mc(root, metas)
    platform, has_connector = detect_instance_platform(root, metas)

    # ---- 问题检测 ----
    errors, warns, infos = [], [], []

    # 1. 同 modid 多个 jar
    by_id = defaultdict(list)
    for m in metas:
        if m.get("modid"):
            by_id[m["modid"]].append(m)
    for mid, lst in by_id.items():
        if len(lst) > 1:
            files = ", ".join(f"{x['file']}({x.get('version') or '?'})" for x in lst)
            errors.append(f"重复 modid '{mid}'：{files} → 版本冲突，游戏会加载失败或用错版本，删掉多余的")

    # 2. 平台混装
    fabric_mods = [m for m in metas if m.get("platform") == "fabric" and not m["disabled"]]
    forge_mods = [m for m in metas if m.get("platform") in ("forge", "neoforge") and not m["disabled"]]
    if platform.startswith("Forge") or platform.startswith("NeoForge"):
        if fabric_mods and not has_connector:
            names = ", ".join(x["file"][:40] for x in fabric_mods[:5])
            errors.append(f"Forge 实例里有 {len(fabric_mods)} 个 Fabric mod 但**没装 Sinytra Connector** → 加载失败。涉及: {names}")
        elif fabric_mods and has_connector:
            infos.append(f"检测到 Connector 混装：{len(fabric_mods)} 个 Fabric mod 通过转译运行（注意 Connector 兼容表，部分 mod 不支持）")
    if platform.startswith("Fabric") and forge_mods:
        names = ", ".join(x["file"][:40] for x in forge_mods[:5])
        errors.append(f"Fabric 实例里有 {len(forge_mods)} 个 Forge mod → 无法加载: {names}")

    # 3. 版本兼容性：只在**实例版本超出 mod 支持范围**时报警
    #    （很多 mod 声明的是最低要求如 ">=1.18.2"，在更高的实例版本上完全正常，不该报）
    if mc:
        v_inst = verkey(mc)
        for m in metas:
            if m["disabled"]:
                continue
            lo, hi = m.get("mc_min"), m.get("mc_max")
            # 🔴 精确钉版（`[26.1]` → lo==hi，见 parse_mc_range）必须单独判：
            #    「上下界相等且不等于实例版本」= 该构建只能跑那一个版本，必崩。
            #    实测：Controlling 26.1.0.3 声明 minecraft [26.1]，在 26.1.2 上
            #    FML 直接 ModLoadingException，而旧版体检因为把 [26.1] 当
            #    「26.1 及以上」而**放过**了它。
            if lo and hi and lo == hi and verkey(lo) != v_inst:
                errors.append(
                    f"MC 版本精确不匹配: {m['file'][:45]} 声明 minecraft [{lo}]"
                    f"（= 恰好 {lo}），实例是 {mc} → FML 启动即拒（必崩，换该 MC 线的构建）")
                continue
            if hi and verkey(hi) < v_inst:
                warns.append(f"版本超出支持范围: {m['file'][:45]} 最高支持 {hi}，实例是 {mc} → 可能崩溃")
            elif lo and verkey(lo) > v_inst:
                warns.append(f"版本过低: {m['file'][:45]} 需要 ≥{lo}，实例是 {mc} → 无法加载")

    # 3a. mixin 兼容级别过高 → MixinInitialisationError，mod 加载阶段直接崩
    #     Mixin 认得的最高级别由 MC 版本决定（1.20.x 用 Mixin 0.8.5，只到 17）
    if mc:
        limit = 25 if re.match(r"^2[6-9]\.", mc) else (21 if mc.startswith("1.21") else 17)
        for m in metas:
            lvl = m.get("mixin_level")
            if lvl and lvl > limit:
                errors.append(
                    f"Mixin 兼容级别过高: {m['file'][:45]} 的 mixins.json 声明 JAVA_{lvl}，"
                    f"而 MC {mc} 的 Mixin 只认到 JAVA_{limit} → 启动即 MixinInitialisationError（必崩，移除该 mod）")

    # 3b. Forge loader 版本不满足（mod 要求的最低 Forge 比实例新）
    inst_loader = MC_LOADER.get(mc)
    if inst_loader:
        for m in metas:
            if m["disabled"] or not m.get("min_loader"):
                continue
            if m["min_loader"] > inst_loader:
                errors.append(
                    f"Forge 版本过低: {m['file'][:45]} 要求 Forge≥{m['min_loader']}"
                    f"（{m.get('min_mc') or '?'}），实例 {mc} 的 loader 是 {inst_loader}")

    # 4. 依赖缺失
    #    注意：很多 mod 用 jarJar 把依赖内置进 jar（META-INF/jarjar），或声明的是软依赖，
    #    所以这里只提示、不当错误——以"整合包能否实际启动"为准。
    installed_ids = set(by_id.keys())
    for m in metas:
        for dep_id, dep in (m.get("deps") or {}).items():
            if dep_id in ("minecraft", "forge", "neoforge", "fabricloader", "fabric",
                          "fabric-api", "java", "quilt_loader", "kotlinforforge"):
                continue
            if dep.get("mandatory") and dep_id not in installed_ids:
                if m.get("jarjar"):
                    infos.append(f"依赖 '{dep_id}' 未单独安装，但 {m['file'][:38]} 内置了 jarJar 依赖 → 通常没问题")
                else:
                    warns.append(f"依赖可能缺失: {m['file'][:40]} 需要 '{dep_id}'（{dep.get('range','*')}）"
                                 f" — 若整合包能正常启动则忽略（多为可选/别名/内置依赖）")

    # 5. 被禁用但被依赖
    disabled_ids = {m["modid"] for m in metas if m.get("disabled") and m.get("modid")}
    for m in metas:
        if m.get("disabled"): continue
        for dep_id in (m.get("deps") or {}):
            if dep_id in disabled_ids:
                warns.append(f"依赖被禁用: {m['file'][:45]} 需要 '{dep_id}'，但它被 .disabled 禁用了")

    # 6. 大问题
    for m in metas:
        if m.get("error"):
            errors.append(f"无法读取: {m['file']} → {m['error']}")
        # 加载器插件（Connector / KotlinForge 等）本来就没有 mod 元数据，不算异常
        if m.get("platform") is None and not m["disabled"] and not m.get("is_plugin"):
            warns.append(f"无法识别平台（可能非标准 mod 或数据包式 mod）: {m['file'][:50]}")

    # 7. 超大 mod
    big = [m for m in metas if m["size"] > 40 * 1024 * 1024]
    for m in big:
        infos.append(f"大体积 mod: {m['file'][:45]} ({m['size']//1024//1024}MB) — 加载/分发时留意")

    # ---- 输出报告 ----
    L = []
    L.append("=" * 70)
    L.append(f"整合包体检报告：{root}")
    L.append("=" * 70)
    L.append(f"MC 版本: {mc}  （{mc_src}）")
    L.append(f"加载器:  {platform}")
    n_plugin = sum(1 for m in metas if m.get("is_plugin"))
    L.append(f"mod 数量: {len(metas) - n_plugin} 个"
             + (f" + 加载器插件 {n_plugin} 个" if n_plugin else "")
             + f"（启用 {sum(1 for m in metas if not m['disabled'])} / "
               f"禁用 {sum(1 for m in metas if m['disabled'])}）")
    L.append(f"总体积: {sum(m['size'] for m in metas)//1024//1024} MB")

    L.append("\n【分类统计】")
    cats = Counter(m["cat"] for m in metas)
    for cat, cnt in cats.most_common():
        L.append(f"  {cat:14s} {cnt:3d} 个")

    cfg = scan_config_dirs(root)
    if cfg:
        L.append("\n【配置目录】")
        for d, v in cfg.items():
            L.append(f"  {d:16s} {v['count']:4d} 文件   {v['desc']}")

    if errors:
        L.append(f"\n【❌ 错误 {len(errors)} 项 — 会导致启动失败】")
        for e in errors: L.append(f"   • {e}")
    if warns:
        L.append(f"\n【⚠️  警告 {len(warns)} 项】")
        for w in warns: L.append(f"   • {w}")
    if infos:
        L.append(f"\n【ℹ️  提示 {len(infos)} 项】")
        for i in infos: L.append(f"   • {i}")
    if not (errors or warns):
        L.append("\n✅ 未发现明显冲突")

    # mod 清单（按分类分组）
    L.append("\n【mod 清单】")
    for cat in cats:
        L.append(f"\n  ── {cat} ──")
        for m in sorted([x for x in metas if x["cat"] == cat], key=lambda x: x["file"].lower()):
            flag = " [禁用]" if m["disabled"] else ""
            if m.get("is_plugin"):
                # 插件不是 mod，标注它的作用而不显示没意义的版本号
                L.append(f"    {m.get('modid') or '?':28s} {'(插件)':18s} "
                         f"{m['file'][:52]}  → {m.get('plugin_kind') or '加载器服务'}{flag}")
                continue
            L.append(f"    {m.get('modid') or '?':28s} {str(m.get('version') or '?')[:18]:18s} {m['file'][:52]}{flag}")

    report = "\n".join(L)
    print(report)

    if args.md:
        Path(args.md).write_text(report, encoding="utf-8")
        print(f"\nMarkdown 报告已写入: {args.md}")

    # 有 ❌ 错误时退出码 1，与 检查工程.py / 迁移检查.py 一致，方便脚本化串联
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()

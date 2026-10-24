#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""优秀模组清单生成器（沉淀自 2026-09-08 实战）：Modrinth 下载榜 → 按版本划分的候选池。

产出（--out 目录）：
  优秀模组清单.json           全量主库（每条含 deps/family/is_lib/各版本适配标记）
  优秀模组清单_<MC>.json      按版本玩法清单（可直接喂 下载模组.py，libs 字段另列前置库）
  优秀模组清单.md             汇总（按版本 → 家族分组表格）

方法：
  1) /v2/search 按 project_type:mod + versions 过滤、downloads 排序取 top100
  2) 多版本并集去重 → 下载量门槛自动由"该版本是否入榜 top100"决定
  3) 逐项请求 version 接口取 required 依赖(project_id)，缓存到 <out>/_deps_cache.json 可断点续跑
  4) id→slug 批量 /v2/projects?ids=（ids 必须无空格 JSON，否则 urllib InvalidURL）
  5) 家族/中文名/备注词典人工维护（下方 DICT 段）；附属与主模组同组、前置库独立组
用法：
  python 优秀模组清单.py                       # 默认版本 26.2,26.1,1.20.1，输出到当前目录
  python 优秀模组清单.py --versions 26.2,1.20.1 --out ./x
  python 优秀模组清单.py --refresh             # 忽略缓存重拉依赖
"""
import argparse
import json
import time
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

MR = "https://api.modrinth.com/v2"
UA = {"User-Agent": "modpack-builder/1.0 (contact: local)"}

# ================= DICT：人工维护词典 =================
FAMILIES = {
    "光影渲染": ["sodium", "sodium-extra", "reeses-sodium-options", "iris",
                 "euphoria-patches", "embeddium", "oculus", "indium", "continuity",
                 "athena-ctm"],
    "实体与模型视觉": ["entitytexturefeatures", "entity-model-features", "modelfix",
                     "not-enough-animations", "3dskinlayers", "visuality", "fallingleaves"],
    "性能优化": ["lithium", "ferrite-core", "immediatelyfast", "entityculling", "moreculling",
               "dynamic-fps", "badoptimizations", "modernfix", "memoryleakfix", "krypton",
               "c2me-fabric", "language-reload", "clumps", "debugify", "packet-fixer"],
    "地图与原理图": ["xaeros-minimap", "xaeros-world-map", "litematica"],
    "物品与配方管理": ["jei", "emi", "rei", "inventory-profiles-next", "mouse-tweaks",
                    "controlling", "shulkerboxtooltip", "appleskin"],
    "信息显示 HUD": ["jade", "betterf3", "enchantment-descriptions", "better-mount-hud"],
    "音效与语音": ["simple-voice-chat", "sound-physics-remastered", "presence-footsteps",
                 "ambientsounds", "melody"],
    "内容扩展": ["create", "supplementaries", "biomes-o-plenty", "handcrafted",
               "visual-workbench", "carry-on", "waystones", "natures-compass", "cobblemon",
               "timeless-and-classics-zero", "netherportalfix", "better-advancements",
               "veinminer", "physicsmod", "e4mc", "morechathistory", "cherished-worlds",
               "chat-heads", "optigui", "cubes-without-borders", "rrls", "zoomify",
               "controlify", "fancymenu", "no-chat-reports", "capes", "customskinloader",
               "essential", "terralith", "fast-ip-ping"],
    "前置库": ["fabric-api", "fabric-language-kotlin", "kotlin-for-forge", "cloth-config",
              "yacl", "architectury-api", "forge-config-api-port", "geckolib", "collective",
              "placeholder-api", "puzzles-lib", "balm", "creativecore", "owo-lib",
              "bookshelf-lib", "terrablender", "fzzy-config", "resourceful-lib",
              "supermartijn642s-config-lib", "midnightlib", "iceberg", "moonlight",
              "patchouli", "yungs-api", "coroutil", "forgified-fabric-api",
              "resourceful-config", "puzzle", "malilib", "searchables", "libipn",
              "konkrete", "veinminer-client", "yosbr"],
}
FAMILY_OF = {s: f for f, lst in FAMILIES.items() for s in lst}
ZH = {
    "sodium": "钠 Sodium", "iris": "Iris 光影", "sodium-extra": "Sodium Extra",
    "reeses-sodium-options": "Reese's Sodium 选项", "embeddium": "Embeddium",
    "oculus": "Oculus 光影", "indium": "Indium", "continuity": "Continuity 连接纹理",
    "athena-ctm": "Athena CTM", "entitytexturefeatures": "ETF 实体纹理",
    "entity-model-features": "EMF 实体模型", "modelfix": "Model Gap Fix",
    "not-enough-animations": "更多动作动画", "3dskinlayers": "3D 皮肤层",
    "visuality": "Visuality 粒子", "fallingleaves": "落叶", "lithium": "Lithium",
    "ferrite-core": "FerriteCore", "immediatelyfast": "ImmediatelyFast",
    "entityculling": "实体剔除", "moreculling": "MoreCulling", "dynamic-fps": "Dynamic FPS",
    "badoptimizations": "BadOptimizations", "modernfix": "ModernFix",
    "memoryleakfix": "内存泄漏修复", "krypton": "Krypton 网络", "c2me-fabric": "C2ME 区块并发",
    "language-reload": "语言重载加速", "clumps": "经验球合并", "debugify": "Debugify",
    "packet-fixer": "Packet Fixer", "xaeros-minimap": "Xaero 小地图",
    "xaeros-world-map": "Xaero 大地图", "litematica": "Litematica 原理图",
    "jei": "JEI", "emi": "EMI", "rei": "REI", "inventory-profiles-next": "一键整理 IPN",
    "mouse-tweaks": "鼠标整理", "controlling": "键位搜索", "shulkerboxtooltip": "潜影盒预览",
    "appleskin": "饱食度显示", "jade": "Jade 方块信息", "betterf3": "BetterF3",
    "enchantment-descriptions": "附魔描述", "better-mount-hud": "坐骑 HUD",
    "simple-voice-chat": "简单语音", "sound-physics-remastered": "声音物理重制",
    "presence-footsteps": "脚步声", "ambientsounds": "环境音效", "melody": "Melody 背景乐",
    "create": "机械动力 Create", "supplementaries": "Supplementaries",
    "biomes-o-plenty": "BOP 群系", "handcrafted": "Handcrafted 家具",
    "visual-workbench": "可视化工作台", "carry-on": "Carry On 搬箱",
    "waystones": "传送石碑", "natures-compass": "自然指南针", "cobblemon": "Cobblemon",
    "timeless-and-classics-zero": "TaCZ 枪械", "netherportalfix": "下界门修复",
    "better-advancements": "进度界面美化", "veinminer": "连锁挖矿",
    "veinminer-client": "连锁热键(客户端)", "physicsmod": "物理引擎",
    "e4mc": "e4mc 免费联机", "morechathistory": "更多聊天记录",
    "cherished-worlds": "世界收藏", "chat-heads": "聊天头像", "optigui": "OptiGUI",
    "cubes-without-borders": "无边框全屏", "rrls": "跳过资源重载", "zoomify": "缩放",
    "controlify": "手柄支持", "fancymenu": "FancyMenu 主菜单", "no-chat-reports": "无聊天举报",
    "capes": "披风", "customskinloader": "自定义皮肤加载", "essential": "Essential 联机",
    "terralith": "Terralith 群系", "fast-ip-ping": "Fast IP Ping",
    "fabric-api": "Fabric API", "fabric-language-kotlin": "Fabric Kotlin",
    "kotlin-for-forge": "Kotlin for Forge", "cloth-config": "Cloth Config",
    "yacl": "YACL 配置库", "architectury-api": "Architectury",
    "forge-config-api-port": "Forge 配置移植", "geckolib": "GeckoLib 动画库",
    "collective": "Collective", "placeholder-api": "占位符 API", "puzzles-lib": "Puzzles Lib",
    "balm": "Balm", "creativecore": "CreativeCore", "owo-lib": "owo-lib",
    "bookshelf-lib": "Bookshelf", "terrablender": "TerraBlender 群系合并",
    "fzzy-config": "Fzzy Config", "resourceful-lib": "Resourceful Lib",
    "supermartijn642s-config-lib": "SM 配置库", "midnightlib": "MidnightLib",
    "iceberg": "Iceberg", "moonlight": "Moonlight Lib", "patchouli": "Patchouli 手册",
    "yungs-api": "YUNG's API", "coroutil": "CoroUtil",
    "forgified-fabric-api": "Forgified Fabric API", "resourceful-config": "Resourceful Config",
    "puzzle": "Puzzle", "malilib": "MaLiLib", "searchables": "Searchables",
    "libipn": "libIPN", "konkrete": "Konkrete", "euphoria-patches": "Euphoria 光影补丁",
    "yosbr": "yosbr 配置层",
}
NOTE = {
    "modernfix": "无 26.2 文件（最新到 26.1.2）", "noisium": "最新停 1.21.6",
    "memoryleakfix": "连 1.21.1 都没跟上（停 v1.1.5）", "emi": "最新停 1.21.1",
    "indium": "Fabric+Sodium 渲染兼容补丁，仅 Fabric",
    "embeddium": "Sodium 的 Forge 移植（1.20.1 用）", "oculus": "Iris 的 Forge 移植（1.20.1 用）",
    "euphoria-patches": "Iris 光影补丁（Complementary 系）", "c2me-fabric": "仅 Fabric",
    "veinminer-client": "VeinMiner 热键客户端组件",
    "athena-ctm": "CTM 连接纹理(forge/neoforge 向，Continuity 同类)",
    "yosbr": "配置默认值分层机制（FO 系），其下 config/ 才是真配置",
    "terralith": "群系大改（与 BOP 二选一或注意群系重叠）",
}
FAM_ORDER = ["光影渲染", "实体与模型视觉", "性能优化", "地图与原理图", "物品与配方管理",
             "信息显示 HUD", "音效与语音", "内容扩展", "前置库"]
# ================= DICT 结束 =================


def http_json(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def top100(ver):
    facets = json.dumps([["project_type:mod"], [f"versions:{ver}"]])
    u = f"{MR}/search?" + urllib.parse.urlencode(
        {"limit": 100, "index": "downloads", "facets": facets})
    return {h["slug"]: h for h in http_json(u)["hits"]}


def main():
    ap = argparse.ArgumentParser(description="优秀模组清单生成器（下载榜 + 家族 + 前置标注）")
    ap.add_argument("--versions", default="26.2,26.1,1.20.1", help="MC 版本列表，逗号分隔")
    ap.add_argument("--out", default=".", help="输出目录（默认当前目录）")
    ap.add_argument("--refresh", action="store_true", help="忽略 deps 缓存重拉")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    vers = [v.strip() for v in args.versions.split(",") if v.strip()]

    # 1) 各版本 top100 并集
    pools = {v: top100(v) for v in vers}
    slugs = list(dict.fromkeys(s for v in vers for s in pools[v]))
    base = {}
    for s in slugs:
        src = next(pools[v][s] for v in vers if s in pools[v])
        base[s] = {"slug": s, "downloads": src["downloads"], "title": src["title"],
                   "categories": src.get("categories", []),
                   **{f"ver_{v}": (s in pools[v]) for v in vers}}
    print(f"版本 {vers} 并集: {len(slugs)} 项")

    # 2) 逐项 required 依赖（缓存断点续跑）
    cache_f = out / "_deps_cache.json"
    dep_ids = {}
    if cache_f.exists() and not args.refresh:
        dep_ids = json.loads(cache_f.read_text(encoding="utf-8"))
        print(f"[缓存] 已 {len(dep_ids)} 项，续拉其余")
    for i, s in enumerate(slugs, 1):
        if s in dep_ids:
            continue
        got = set()
        for v in vers:
            if s not in pools[v]:
                continue
            try:
                vs = http_json(f"{MR}/project/{s}/version?"
                               + urllib.parse.urlencode({"game_versions": json.dumps([v])}))
            except Exception:
                continue
            if not vs:
                continue
            vs.sort(key=lambda x: x["date_published"], reverse=True)
            for dep in vs[0].get("dependencies", []):
                if dep.get("dependency_type") == "required" and dep.get("project_id"):
                    got.add(dep["project_id"])
        dep_ids[s] = sorted(got)
        if i % 10 == 0:
            cache_f.write_text(json.dumps(dep_ids, ensure_ascii=False), encoding="utf-8")
            print(f"  [{i}/{len(slugs)}]")
        time.sleep(0.03)
    cache_f.write_text(json.dumps(dep_ids, ensure_ascii=False), encoding="utf-8")

    # 3) id -> slug（ids 无空格 JSON，空格会 InvalidURL）
    all_ids = sorted({i for v in dep_ids.values() for i in v})
    id2slug = {}
    for k in range(0, len(all_ids), 100):
        chunk = all_ids[k:k + 100]
        ps = http_json(f"{MR}/projects?ids="
                       + urllib.parse.quote(json.dumps(chunk, separators=(",", ":"))))
        for p in ps:
            id2slug[p["id"]] = p["slug"]

    # 4) 组装主库
    mods = []
    for s in slugs:
        d = base[s]
        mods.append({
            "slug": s, "title": ZH.get(s, d["title"]), "downloads": d["downloads"],
            "family": FAMILY_OF.get(s, "未分组"), "is_lib": FAMILY_OF.get(s) == "前置库",
            **{f"ver_{v}": d[f"ver_{v}"] for v in vers},
            "categories": d["categories"][:4],
            "deps": sorted({id2slug.get(i, str(i)) for i in dep_ids[s]}),
            "note": NOTE.get(s, ""),
        })
    sortkey = tuple(-mods[0]["ver_" + v] for v in vers)
    mods.sort(key=lambda m: sortkey + (-m["downloads"],))
    (out / "优秀模组清单.json").write_text(
        json.dumps({"generated": time.strftime("%Y-%m-%d"), "versions": vers,
                    "source": "Modrinth top100 并集, 门槛=总下载榜", "mods": mods},
                   ensure_ascii=False, indent=1), encoding="utf-8")

    # 5) 按版本 JSON（可直接喂 下载模组.py）
    for v in vers:
        key = f"ver_{v}"
        play = [m for m in mods if m[key] and not m["is_lib"]]
        libs = [m for m in mods if m[key] and m["is_lib"]]
        (out / f"优秀模组清单_{v}.json").write_text(json.dumps({
            "name": f"优秀模组池 {v}", "mc": v, "loader": "见条目 loader 说明",
            "note": "候选池：同族/同功能二选一（sodium vs embeddium、iris vs oculus、jei/emi/rei 只装一个）。"
                    "libs 段为前置库，下载脚本会按需递归，不必全装。",
            "mods": [{"slug": m["slug"], "cat": m["family"], "note": m.get("note", ""),
                      "downloads": m["downloads"]} for m in play],
            "libs": [{"slug": m["slug"], "cat": "前置库", "note": m.get("note", "")} for m in libs],
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  优秀模组清单_{v}.json: 玩法 {len(play)} + 库 {len(libs)}")

    # 6) 汇总 MD
    byfam = {}
    for m in mods:
        byfam.setdefault(m["family"], []).append(m)
    L = ["# 优秀模组清单（按适配版本划分）\n",
         f"> 来源：Modrinth 下载榜（{time.strftime('%Y-%m-%d')}），{'/'.join(vers)} top100 并集。"
         "下载量 = Modrinth 项目总下载。前置经官方 version dependencies 实拉标注，附属与主模组同组。\n",
         "> 冲突提醒：**同组同功能只装一个**——渲染组 sodium/embeddium、iris/oculus 按 loader 选；"
         "配方 JEI/EMI/REI 只装一个；Indium 仅 Fabric+Sodium 需要。\n",
         "> 前置说明：required 依赖取该模组适配构建的最新版实测（未限定 loader，个别条目随构建略有差异，"
         "下载脚本会自动递归补齐）。\n"]
    for v in vers:
        L.append(f"\n## MC {v}\n")
        for fam in FAM_ORDER:
            rows = [m for m in byfam.get(fam, []) if m[f"ver_{v}"]]
            if not rows:
                continue
            head = f"### {fam}\n" if fam != "前置库" else \
                f"### {fam}（其余条目依赖它们，装玩法时会自动带上，一般不必手动装）\n"
            L.append(f"\n{head}")
            L.append("| 模组 | 总下载 | 前置（required） | 备注 |\n|---|---|---|---|")
            for m in sorted(rows, key=lambda x: -x["downloads"]):
                deps = "、".join(f"`{x}`" for x in m["deps"]) if m["deps"] else "—"
                L.append(f"| {m['slug']}（{m['title']}） | {m['downloads'] / 1e6:.1f}M | {deps} | {m.get('note', '')} |")
    (out / "优秀模组清单.md").write_text("\n".join(L), encoding="utf-8")
    print(f"完成 → {out}/优秀模组清单.json / _<版本>.json / .md")


if __name__ == "__main__":
    main()

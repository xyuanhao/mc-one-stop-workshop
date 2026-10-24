#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
原版资源提取器 —— "拿原版改一改就能用"的取料台。

MC 实例里的 `<版本>.jar` 就是**原版客户端 jar**（PCL/HMCL 的 NeoForge/Forge 实例也一样，
原版 jar 被改名成实例名）。素材分两处：

  ① jar 内（主力）：
     assets/minecraft/textures/item|block|entity|gui|particle   贴图
     assets/minecraft/models/ 、items/ 、blockstates/           模型 / 物品模型定义 / 方块状态
     assets/minecraft/lang/en_us.json                           英文语言（键名直接抄）
     assets/minecraft/sounds.json 、assets/minecraft/sounds/    音效表与音效
     data/minecraft/recipe|loot_table|tags|advancement|...      数据包（配方/战利品表/标签/进度）
     net/minecraft/**                                           原版类（26.1+ 无混淆，可直接查签名）
  ② assets/objects（补充）：语言文件除 en_us 外**全部**在这里（zh_cn 等），按 hash 存放。
     脚本会自动读 assets/indexes/<id>.json 建立"逻辑路径 → 实体文件"映射，与 jar 合并检索。

用法:
  python 原版资源提取.py --scan                                  # 找出本机所有原版 jar
  python 原版资源提取.py --search door                           # 跨类型搜关键词
  python 原版资源提取.py --search cow --kind entity              # 只在实体贴图里搜
  python 原版资源提取.py --extract assets/minecraft/textures/item/acacia_door.png -o out/
  python 原版资源提取.py --extract-pattern "textures/item/*_door.png" -o out/
  python 原版资源提取.py --extract assets/minecraft/lang/zh_cn.json -o out/lang/
  python 原版资源提取.py --list-classes net/minecraft/world/entity/ai/navigation
  python 原版资源提取.py --stats

提取默认保留 `assets/...` 原始相对路径，直接就能当资源包 / mod 的 assets 目录用。
常用组合：提取原版贴图 → `纹理变色.py` 换色 → 放进 `assets/<modid>/textures/`。
"""
import argparse
import fnmatch
import json
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

KINDS = {
    "item":       "assets/minecraft/textures/item/",
    "block":      "assets/minecraft/textures/block/",
    "entity":     "assets/minecraft/textures/entity/",
    "gui":        "assets/minecraft/textures/gui/",
    "particle":   "assets/minecraft/textures/particle/",
    "trims":      "assets/minecraft/textures/trims/",
    "model":      "assets/minecraft/models/",
    "itemdef":    "assets/minecraft/items/",        # 1.21.4+ 物品模型定义
    "blockstate": "assets/minecraft/blockstates/",
    "equipment":  "assets/minecraft/equipment/",    # 1.21.5+ 装备定义
    "atlas":      "assets/minecraft/atlases/",
    "lang":       "assets/minecraft/lang/",
    "sound":      "assets/minecraft/sounds",
    "shader":     "assets/minecraft/shaders/",
    "data":       "data/minecraft/",
    "class":      "net/minecraft/",
}
KIND_DESC = {
    "item": "物品贴图 → 改色即可用", "block": "方块贴图", "entity": "实体贴图（生物皮肤）",
    "gui": "界面贴图", "particle": "粒子贴图", "trims": "盔甲纹饰贴图",
    "model": "模型 JSON", "itemdef": "物品模型定义（1.21.4+）",
    "blockstate": "方块状态 JSON", "equipment": "装备定义（1.21.5+）",
    "atlas": "图集定义", "lang": "语言文件（键名直接抄）", "sound": "音效表/文件",
    "shader": "核心着色器", "data": "数据包（配方/战利品表/标签/进度）",
    "class": "原版类", "other": "其他",
}
DEFAULT_MC_DIRS = [
    Path.home() / "AppData/Roaming/.minecraft",
    Path("D:/.minecraft"), Path("E:/.minecraft"), Path("C:/.minecraft"),
]


def classify(name):
    for k, pre in KINDS.items():
        if name.startswith(pre):
            return k
    return "other"


def mc_dir_of(jar_path: Path):
    p = jar_path
    for _ in range(3):
        p = p.parent
        if p.name in (".minecraft", "minecraft"):
            return p
    return None


def read_ver_json(jar_path: Path):
    for j in jar_path.parent.glob("*.json"):
        try:
            d = json.loads(j.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(d, dict) and ("clientVersion" in d or "assetIndex" in d):
            return d
    return {}


def scan_jars(mc_dirs):
    """扫出所有像原版 jar 的文件（含 assets/minecraft 资源即认定）。"""
    out, seen = [], set()
    for d in mc_dirs:
        d = Path(d)
        if not d.is_dir():
            continue
        for jar in sorted(d.glob("versions/*/*.jar")):
            if jar in seen:
                continue
            seen.add(jar)
            try:
                with zipfile.ZipFile(jar) as z:
                    names = z.namelist()
            except Exception:
                continue
            n_tex = sum(1 for n in names if n.startswith("assets/minecraft/textures/"))
            if n_tex < 50:
                continue
            vj = read_ver_json(jar)
            n_cls = sum(1 for n in names if n.startswith("net/minecraft/") and n.endswith(".class"))
            out.append({
                "path": jar, "mc": mc_dir_of(jar),
                "ver": vj.get("clientVersion") or vj.get("id") or jar.parent.name,
                "index": (vj.get("assetIndex") or {}).get("id"),
                "mb": jar.stat().st_size / 1048576, "n_tex": n_tex, "n_cls": n_cls,
                # 1.20.x 及以前带混淆：原版类都躺在 a/b/c 之类的包里
                "obfuscated": n_cls < 200,
            })
    return out


def pick(entries, want_ver=None, want_jar=None):
    if want_jar:
        p = Path(want_jar)
        for e in entries:
            if e["path"] == p:
                return e
        if p.is_file():
            vj = read_ver_json(p)
            return {"path": p, "mc": mc_dir_of(p),
                    "ver": vj.get("clientVersion") or vj.get("id") or p.parent.name,
                    "index": (vj.get("assetIndex") or {}).get("id"),
                    "mb": p.stat().st_size / 1048576, "n_tex": -1, "n_cls": -1,
                    "obfuscated": False}
        print(f"[错误] 找不到 jar: {want_jar}")
        sys.exit(1)
    if want_ver:
        hit = [e for e in entries if e["ver"] == want_ver or str(e["ver"]).startswith(want_ver)]
        if hit:
            return sorted(hit, key=lambda e: -e["mb"])[0]
        print(f"[错误] 没有版本匹配 {want_ver}，可用: {[e['ver'] for e in entries]}")
        sys.exit(1)
    if not entries:
        print("[错误] 没找到原版 jar，用 --mc-dir 指定 .minecraft 目录")
        sys.exit(1)
    return sorted(entries, key=lambda e: -e["mb"])[0]


def load_objects(entry):
    """读 assets/indexes/<id>.json，返回 {逻辑路径: 实体文件路径}（语言文件等都在这里）。"""
    mc, idx = entry.get("mc"), entry.get("index")
    if not mc or not idx:
        return {}
    f = Path(mc) / "assets" / "indexes" / f"{idx}.json"
    if not f.is_file():
        return {}
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out = {}
    for logical, meta in (d.get("objects") or {}).items():
        h = meta.get("hash", "")
        real = Path(mc) / "assets" / "objects" / h[:2] / h
        if real.is_file():
            # 索引里的键是 `minecraft/lang/zh_cn.json`（不带 assets/ 前缀），
            # 而 jar 内是 `assets/minecraft/lang/en_us.json` —— 补前缀才能两套路径统一检索。
            out["assets/" + logical] = real
    return out


def main():
    ap = argparse.ArgumentParser(description="原版资源提取器（拿原版改一改就能用）")
    ap.add_argument("--scan", action="store_true", help="列出本机所有可用原版 jar")
    ap.add_argument("--stats", action="store_true", help="统计所选 jar 的可复用素材")
    ap.add_argument("--search", help="按关键词搜资源路径（支持 * 通配）")
    ap.add_argument("--kind", choices=list(KINDS), help="限定资源类型")
    ap.add_argument("--extract", nargs="+", help="提取指定路径（可多个）")
    ap.add_argument("--extract-pattern", help="按通配符批量提取，如 \"textures/item/*_door.png\"")
    ap.add_argument("--list-classes", help="列出某包下的原版类（仅 26.1+ 无混淆版本有效）")
    ap.add_argument("--list-kind", choices=list(KINDS), help="列出某类型下的全部条目")
    ap.add_argument("--ver", help="选哪个 MC 版本的原版 jar")
    ap.add_argument("--jar", help="直接指定 jar 路径")
    ap.add_argument("--mc-dir", action="append", default=[], help="指定 .minecraft 目录（可多次）")
    ap.add_argument("-o", "--out", default="vanilla_extract", help="输出目录")
    ap.add_argument("--flatten", action="store_true", help="提取时不保留目录层级")
    ap.add_argument("--limit", type=int, default=60, help="列表类输出上限")
    args = ap.parse_args()

    dirs = [Path(d) for d in args.mc_dir] or DEFAULT_MC_DIRS
    entries = scan_jars(dirs)

    if args.scan:
        if not entries:
            print("没找到原版 jar。用 --mc-dir 指定 .minecraft 目录。")
            return
        print(f"找到 {len(entries)} 个原版 jar：\n")
        for e in sorted(entries, key=lambda x: -x["mb"]):
            ob = "类名混淆：资源可抄，类名不可照搬" if e["obfuscated"] else "无混淆：类名可直接查"
            print(f"  MC {e['ver']:<10} {e['mb']:5.1f}MB  贴图{e['n_tex']:>5}  原版类{e['n_cls']:>6}  （{ob}）")
            print(f"      {e['path']}")
        return

    jar = pick(entries, args.ver, args.jar)
    lbl = f"MC {jar['ver']}" + ("（混淆）" if jar.get("obfuscated") else "")
    zipf = zipfile.ZipFile(jar["path"])
    jar_names = {n for n in zipf.namelist() if not n.endswith("/")}
    obj_map = load_objects(jar)
    all_names = jar_names | set(obj_map)

    if args.stats:
        c_jar = Counter(classify(n) for n in jar_names)
        c_obj = Counter(classify(n) for n in obj_map)
        print(f"=== {lbl} —— {jar['path'].name} ({jar['mb']:.1f}MB) ===")
        print(f"    jar 内 {len(jar_names)} 条目 ＋ assets/objects {len(obj_map)} 条目\n")
        print(f"  {'类型':<12}{'jar 内':>8}{'objects':>9}   说明")
        for k in sorted(set(c_jar) | set(c_obj), key=lambda x: -(c_jar[x] + c_obj[x])):
            print(f"  {k:<12}{c_jar[k]:>8}{c_obj[k]:>9}   {KIND_DESC.get(k, '')}")
        return

    if args.list_classes:
        pre = args.list_classes.replace(".", "/").rstrip("/") + "/"
        hits = sorted(n for n in jar_names
                      if n.startswith(pre) and n.endswith(".class") and "$" not in n)
        print(f"=== {lbl} · {args.list_classes} 下的类（{len(hits)}）===")
        if jar.get("obfuscated"):
            print("  ⚠ 该版本带混淆，net/minecraft 下几乎没有可用类名 —— 查类请换 26.1+ 版本。")
        for h in hits[:args.limit]:
            print("  " + h[len(pre):-6].replace("/", "."))
        if len(hits) > args.limit:
            print(f"  ... 还有 {len(hits) - args.limit} 个（--limit 调整）")
        return

    if args.search or args.list_kind:
        if args.search:
            kw = args.search
            pat = kw if any(ch in kw for ch in "*?") else f"*{kw}*"
            low = pat.lower()
            hits = [n for n in all_names
                    if fnmatch.fnmatch(n, pat) or fnmatch.fnmatch(n.lower(), low)]
        else:
            hits = [n for n in all_names if n.startswith(KINDS[args.list_kind])]
        if args.kind:
            hits = [n for n in hits if classify(n) == args.kind]
        if not hits:
            print("没有匹配项。")
            return
        grp = defaultdict(list)
        for n in hits:
            grp[classify(n)].append(n)
        print(f"=== {lbl} · 命中 {len(hits)} 条（按类型分组）===")
        for k in sorted(grp, key=lambda x: -len(grp[x])):
            print(f"\n  [{k}] {len(grp[k])} 条")
            for n in grp[k][:args.limit]:
                print("    " + n + ("" if n in jar_names else "   ← assets/objects"))
            if len(grp[k]) > args.limit:
                print(f"    ... 还有 {len(grp[k]) - args.limit} 条")
        return

    if args.extract or args.extract_pattern:
        todo = list(args.extract or [])
        if args.extract_pattern:
            pat = args.extract_pattern
            if not pat.startswith(("assets/", "data/", "net/", "META-INF")):
                pat = "*" + pat
            todo += [n for n in all_names if fnmatch.fnmatch(n, pat)]
        todo = sorted(set(todo))
        if not todo:
            print("[错误] 没有匹配到任何条目")
            sys.exit(1)
        out = Path(args.out)
        ok, src_jar, src_obj, miss = 0, 0, 0, []
        for n in todo:
            dst = out / (Path(n).name if args.flatten else n)
            dst.parent.mkdir(parents=True, exist_ok=True)
            if n in jar_names:
                with zipf.open(n) as src, open(dst, "wb") as f:
                    f.write(src.read())
                src_jar += 1
            elif n in obj_map:
                dst.write_bytes(Path(obj_map[n]).read_bytes())
                src_obj += 1
            else:
                miss.append(n)
                continue
            ok += 1
        print(f"提取 {ok} 个文件 → {out}   （jar 内 {src_jar} ＋ objects {src_obj}）")
        if miss:
            print(f"未找到 {len(miss)} 个：{miss[:5]}")
        print("\n下一步：改色用  python 纹理变色.py <提取的.png> --preset woods -o out/")
        return

    ap.print_help()


if __name__ == "__main__":
    main()

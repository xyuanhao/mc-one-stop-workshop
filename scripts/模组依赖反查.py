#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模组依赖反查 —— 换 / 删某个 mod 之前，先问一句"谁依赖它"。

【为什么需要这个】
想替换或删掉实例里的一个 mod 时，最大的风险不是这个 mod 本身，
而是**别的 mod 正依赖着它**。删完开游戏直接崩在依赖解析阶段，
而且报错信息往往指向依赖方，不指向你删的那个 mod，排查很绕。

`整合包体检.py` 查的是**正向**依赖（"我这个 mod 需要谁"）；
本脚本查**反向**依赖（"谁需要我"），是换 mod 前的必做一步。

【用法】
    python 模组依赖反查.py <实例目录|mods目录> <modid 或 jar 文件名>
    python 模组依赖反查.py <实例> --list            # 列出所有被依赖的 mod（找出哪些是前置库）
    python 模组依赖反查.py <实例> veinminer --md 报告.md
    python 模组依赖反查.py <实例> veinminer --root D:/另存的实例   # 另指定查配置残留的根目录

【输出四块】
    ① 目标定位    —— 匹配到哪个 jar，modid / 显示名 / 版本 / 平台
    ② 反向依赖    —— 谁依赖它，分 required（会拦流水线）/ optional（通常可忽略）
                     含 jarJar 内嵌（把目标 mod 打进自己 jar 里的情况）
    ③ 配置残留    —— config/<Name>/ 、defaultconfigs 等，删 mod 后建议一并处理
    ④ 数据包/任务 —— data / config / saves 里提到目标 id 的文件（FTB 任务书等）

【退出码】发现 required 依赖 → 1；干净（含只有 optional）→ 0
          可直接串进「反查 → 备份 → 替换 → 验证」流水线当闸门。
"""
import argparse
import os
import re
import sys
import zipfile
from pathlib import Path

# 复用体检脚本里已经验证过的 jar 元数据解析（覆盖 4 种格式）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import 整合包体检 as TB
except Exception as e:  # pragma: no cover
    print("无法导入 整合包体检.py（应与本脚本同目录）：%s" % e)
    sys.exit(2)

read_jar_meta = TB.read_jar_meta


def find_mods_dir(root: Path):
    """给实例目录或 mods 目录，返回 mods 目录；找不到返回 None。"""
    if root.name.lower() == "mods" and root.is_dir():
        return root
    m = root / "mods"
    return m if m.is_dir() else None


def scan_all(mods_dir: Path):
    """解析 mods 目录下所有 jar，返回 meta 列表。"""
    metas = []
    for f in sorted(mods_dir.iterdir()):
        if f.suffix.lower() == ".jar":
            metas.append(read_jar_meta(str(f)))
    return metas


def match_targets(metas, query: str):
    """按 modid 精确 → 文件名精确 → 文件名/modid 子串，逐级匹配。"""
    q = query.lower().removesuffix(".jar")
    for getter in (
        lambda m: (m.get("modid") or "").lower() == q,
        lambda m: m["file"].lower().removesuffix(".jar") == q,
        lambda m: q in (m.get("modid") or "").lower() or q in m["file"].lower(),
    ):
        hit = [m for m in metas if getter(m)]
        if hit:
            return hit
    return []


def candidate_ids(meta):
    """列出一个 jar 所有可能的 id 写法，用于反查匹配。

    ⚠️ 为什么不能只用 meta['modid']：
    MANIFEST 声明式 mod（`FMLModType: LIBRARY`，如 kotlinforforge）和部分
    加载器插件解析不出 modid（返回 None）。只看 modid 会让目标 id 集合为空，
    反查**全部落空**——产生"没有 mod 依赖它"的危险假阴性
    （实测：kotlinforforge 明明被 libIPN 依赖，却报无依赖）。
    所以用文件名按分隔符逐段展开兜底。
    """
    ids = set()
    if meta.get("modid"):
        ids.add(meta["modid"].lower())
    fname = meta["file"].lower().removesuffix(".jar")
    ids.add(fname)
    parts = re.split(r"[-_]", fname)
    for i in range(1, min(3, len(parts)) + 1):
        head = parts[:i]
        ids.add("-".join(head))
        ids.add("_".join(head))
    return {i for i in ids if i and not i.isdigit()}


def jarjar_embedded(jar_path: str, target_ids):
    """检查 jar 的 META-INF/jarjar/ 里是否内嵌了目标 mod（内嵌依赖删不掉）。"""
    found = []
    try:
        with zipfile.ZipFile(jar_path) as z:
            for n in z.namelist():
                if not (n.startswith("META-INF/jarjar/") and n.endswith(".jar")):
                    continue
                try:
                    import io
                    with zipfile.ZipFile(io.BytesIO(z.read(n))) as inner:
                        for cand in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml"):
                            if cand in inner.namelist():
                                txt = inner.read(cand).decode("utf-8", "replace")
                                for tid in target_ids:
                                    if re.search(r'modId\s*=\s*"%s"' % re.escape(tid), txt):
                                        found.append((tid, n))
                except Exception:
                    pass
    except Exception:
        pass
    return found


def reverse_deps(metas, target_ids, mods_dir, target_files):
    """找出所有声明依赖目标 mod 的 mod。"""
    results = []
    for m in metas:
        if m["file"] in target_files:
            continue                                   # 跳过目标自己
        for dep_id, dep in (m.get("deps") or {}).items():
            if dep_id.lower() not in target_ids:
                continue
            results.append({
                "mod": m["file"], "modid": m.get("modid"), "name": m.get("name"),
                "dep": dep_id, "mandatory": dep.get("mandatory", True),
                "range": dep.get("range", "*"), "kind": "声明依赖",
            })
        # jarJar 内嵌算另一种"绑死"
        for tid, inner in jarjar_embedded(str(mods_dir / m["file"]), target_ids):
            results.append({
                "mod": m["file"], "modid": m.get("modid"), "name": m.get("name"),
                "dep": tid, "mandatory": False, "range": "内嵌",
                "kind": "jarJar 内嵌（%s）" % os.path.basename(inner),
            })
    return results


def config_leftovers(root: Path, target_ids):
    """找 config/ defaultconfigs/ 里以目标 mod 命名的目录或文件。"""
    hits = []
    for sub in ("config", "defaultconfigs"):
        d = root / sub
        if not d.is_dir():
            continue
        for entry in sorted(d.iterdir()):
            low = entry.name.lower()
            if any(t in low for t in target_ids):
                hits.append(str(entry))
    return hits


def datapack_refs(root: Path, target_ids):
    """扫数据包/任务/配置文本里提到目标 id 的文件。"""
    exts = (".json", ".snbt", ".toml", ".txt", ".js", ".zs", ".properties", ".mcfunction")
    hits = []
    for sub in ("data", "config", "defaultconfigs", "patchouli_books", "kubejs"):
        base = root / sub
        if not base.is_dir():
            continue
        for dirpath, _dirs, files in os.walk(base):
            for fn in files:
                if not fn.lower().endswith(exts):
                    continue
                p = Path(dirpath) / fn
                try:
                    txt = p.read_text(encoding="utf-8", errors="replace").lower()
                except Exception:
                    continue
                if any(t in txt for t in target_ids):
                    hits.append(str(p.relative_to(root)))
    # 存档里的任务书（saves/<世界>/ftbquests/）也真扫一遍 —— 任务会引用 mod 的物品 id
    saves = root / "saves"
    if saves.is_dir():
        for w in sorted(saves.iterdir()):
            q = w / "ftbquests"
            if not q.is_dir():
                continue
            for dirpath, _dirs, files in os.walk(q):
                for fn in files:
                    if not fn.lower().endswith(exts):
                        continue
                    p = Path(dirpath) / fn
                    try:
                        txt = p.read_text(encoding="utf-8", errors="replace").lower()
                    except Exception:
                        continue
                    if any(t in txt for t in target_ids):
                        hits.append(str(p.relative_to(root)))
    return hits


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="换/删 mod 前的反向依赖反查",
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("instance", help="实例目录或 mods 目录")
    ap.add_argument("target", nargs="?", help="modid 或 jar 文件名（--list 时可省略）")
    ap.add_argument("--list", action="store_true", help="列出所有被依赖的 mod（找出前置库）")
    ap.add_argument("--root", help="另指定查配置残留的根目录（默认同 instance）")
    ap.add_argument("--md", help="把报告写到指定 md 文件")
    args = ap.parse_args()

    root = Path(args.instance).expanduser()
    mods_dir = find_mods_dir(root)
    if not mods_dir:
        print("✗ 在 %s 下找不到 mods 目录" % root)
        return 2
    conf_root = Path(args.root).expanduser() if args.root else (root if root.name != "mods" else root.parent)

    print("扫描 %s ..." % mods_dir, flush=True)
    metas = scan_all(mods_dir)
    print("共 %d 个 jar\n" % len(metas), flush=True)

    # ---- --list：列出所有被别的 mod 依赖的前置库 ----
    if args.list or not args.target:
        deps_count = {}
        for m in metas:
            for dep_id, dep in (m.get("deps") or {}).items():
                if dep_id.lower() in ("minecraft", "neoforge", "forge", "fabricloader", "java"):
                    continue
                deps_count.setdefault(dep_id, []).append(m["file"])
        print("=== 被依赖的 mod（前置库）===")
        print("删这些要格外小心，它们不是给人用的，是给别的 mod 用的\n")
        for dep_id, users in sorted(deps_count.items(), key=lambda kv: -len(kv[1])):
            print("  %-28s ← %d 个 mod 依赖" % (dep_id, len(users)))
            for u in users[:4]:
                print("      · %s" % u)
            if len(users) > 4:
                print("      · ...另 %d 个" % (len(users) - 4))
        print("\n（想查具体某个，用：python 模组依赖反查.py <实例> <modid>）")
        return 0

    # ---- 定位目标 ----
    targets = match_targets(metas, args.target)
    if not targets:
        print("✗ 没找到匹配 '%s' 的 mod" % args.target)
        return 2
    target_ids = set()
    for m in targets:
        target_ids |= candidate_ids(m)
    target_files = {m["file"] for m in targets}

    lines = []

    def emit(s=""):
        print(s, flush=True)
        lines.append(s)

    emit("=== ① 目标定位 ===")
    for m in targets:
        emit("  %s" % m["file"])
        emit("     modid=%s  显示名=%s  版本=%s  平台=%s"
             % (m.get("modid"), m.get("name"), m.get("version"), m.get("platform")))
        emit("     反查用 id 候选: %s" % ", ".join(sorted(target_ids)))
    emit()

    # ---- ② 反向依赖 ----
    emit("=== ② 反向依赖（谁依赖它）===")
    rdeps = reverse_deps(metas, target_ids, mods_dir, target_files)
    required = [r for r in rdeps if r["mandatory"]]
    optional = [r for r in rdeps if not r["mandatory"]]
    if not rdeps:
        emit("  ✓ 没有任何 mod 依赖它 —— 可以安全替换/删除")
    else:
        for r in required:
            emit("  ⛔ [必需] %s" % r["mod"])
            emit("        依赖 %s（要求 %s）%s" % (r["dep"], r["range"],
                                                 "" if r["kind"] == "声明依赖" else " " + r["kind"]))
        for r in optional:
            emit("  ○ [可选] %s" % r["mod"])
            emit("        %s（要求 %s）%s" % (r["dep"], r["range"],
                                            "" if r["kind"] == "声明依赖" else " " + r["kind"]))
        if optional and not required:
            emit("  → 只有可选依赖，删掉通常没事（除非目标 mod 正是那个可选功能）")
    emit()

    # ---- ③ 配置残留 ----
    emit("=== ③ 配置残留 ===")
    leftovers = config_leftovers(conf_root, target_ids)
    if leftovers:
        for h in leftovers:
            emit("  %s" % h)
        emit("  → 删 mod 后这些配置不再被读取，建议连备份一起处理掉")
    else:
        emit("  ✓ 没有配置残留")
    emit()

    # ---- ④ 数据包/任务引用 ----
    emit("=== ④ 数据包 / 任务书引用 ===")
    refs = datapack_refs(conf_root, target_ids)
    if refs:
        for r in refs[:30]:
            emit("  %s" % r)
        if len(refs) > 30:
            emit("  ...另 %d 处" % (len(refs) - 30))
        emit("  → 这些地方引用了该 mod，删掉可能让任务/配方失效")
    else:
        emit("  ✓ 没有数据包/任务引用")
    emit()

    # ---- 结论 ----
    emit("=== 结论 ===")
    if required:
        emit("  ⛔ 有 %d 个 mod【必需】依赖它 —— 直接删会崩，先处理依赖方" % len(required))
        verdict = 1
    elif rdeps:
        emit("  ⚠️ 只有可选依赖，可以删；建议删完启动一次确认")
        verdict = 0
    else:
        emit("  ✓ 无依赖，可安全替换/删除")
        verdict = 0

    if args.md:
        Path(args.md).write_text("\n".join(lines), encoding="utf-8")
        print("\n报告已写入 %s" % args.md)
    return verdict


if __name__ == "__main__":
    sys.exit(main())

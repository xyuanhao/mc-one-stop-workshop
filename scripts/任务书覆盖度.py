#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""任务书覆盖度.py —— 整合包任务书「覆盖度 + 递进 + 引用一致性」体检。

设计依据：references/任务书设计.md 的两条硬指标
  1) 覆盖度 100%：mods/ 里每个**内容模组**都要在任务书里出现过至少一次
  2) 循序渐进：依赖链不许断，任务 id 不许重复

三项检查：
  A. 覆盖度   —— 逐个 modId 对账：任务书里有没有 `namespace:path` 引用到它。
                 模组按「内容模组 / 库·QoL」分层（看 jar 里有没有自己的方块/物品/群系资源），
                 只有**内容模组零提及**才影响退出码（库漏了是 warning，--strict 才升级为问题）。
  B. 反查     —— 任务书引用了、但 mods/ 里没有的命名空间（mod 被移除后没同步改任务书，
                 或拼错了 id —— 这类错误会让任务永远做不了，且不报任何错）
  C. 依赖链   —— dependencies 引用的 quest id 是否存在（断链）/ quest id 是否重复 / 无前置任务数

用法：
  python 任务书覆盖度.py "<实例目录>"                    # 自动找 mods/ 与 config/ftbquests/
  python 任务书覆盖度.py --mods <mods目录> --quests <config/ftbquests目录>
  python 任务书覆盖度.py <实例目录> --md 覆盖度报告.md
  python 任务书覆盖度.py <实例目录> --chain              # 只跑依赖链检查
  python 任务书覆盖度.py <实例目录> --strict             # 库/QoL 零提及也算失败

无 mods/ 时可用清单代替：--modlist <清单.json>（items[].modid / .slug / CF manifest 形式都试）

退出码：0 = 达标 / 1 = 有内容模组零提及、断链或 id 重复 / 2 = 参数或路径问题
"""
import argparse
import json
import re
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

TOML_NAMES = ("META-INF/neoforge.mods.toml", "META-INF/mods.toml")
FABRIC_NAMES = ("fabric.mod.json",)
QUEST_FILE_GLOBS = ("**/*.snbt", "**/*.json5", "**/*.json")

# 这些命名空间不是模组，不参与覆盖度对账
NON_MOD_NS = {
    "minecraft", "ftbquests", "neoforge", "forge", "fml", "javafml", "lowcodefml",
    "https", "http", "image", "c", "tag", "data", "assets", "file",
}

NS_RE = re.compile(
    r"(?<![A-Za-z0-9_./:\-])([a-z][a-z0-9_]{1,40}):([a-z0-9_][a-z0-9_./\-]{0,150})"
)
ID_RE = re.compile(r'(?:"id"|\bid)\s*:\s*"([0-9A-Fa-f]{16})"')
DEPEND_RE = re.compile(
    r'(?:"dependencies"|\bdependencies)\s*:\s*\[([^\]]{0,4000})\]', re.S
)
HEX16_RE = re.compile(r"([0-9A-Fa-f]{16})")


# ---------------------------------------------------------------- 读 jar：modId + 内容/库判定

def _toml_modids(txt: str):
    """只取 `[[mods]]` 段里的 modId。

    ⚠️ 关键：`[[dependencies.neoforge]]` / `[[dependencies.placebo]]` 段里也有 `modId = "..."`，
    整篇正则会把**依赖**误当成这个 jar 提供的 modId（实测：ApothicAttributes 一个 jar 就"提供"了
    neoforge + placebo + minecraft 三个假 modId）。必须按 TOML 段切开取。

    ⚠️ 第二个坑（2026-09-13 实测）：**被注释掉的段头**。有些 jar 把废段整段注释：
        #[[dependencies.entity_texture_features]]
        #modId = "neoforge"
    若段头正则不管 `#`，这段注释会被归到**下一个真实段头**名下，
    于是 `[[dependencies.entity_texture_features]]` 段里就混进了 `neoforge`。
    → 段头必须要求行首（除空白外）**没有 `#`**。
    """
    ids = []
    for seg in re.split(r"^\s*\[\[", txt, flags=re.M)[1:]:
        # re.split 按 `[[` 切，段头行如果原本带 # 前缀，切割点之前就出现了 #
        head, _, body = seg.partition("]]")
        if "#" in head:
            continue                              # 注释掉的段，跳过
        if head.strip().split(".")[0].strip() != "mods":
            continue
        # 段体里同样要剔掉整行注释（#modId = "..."），只认未注释的赋值
        clean = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#"))
        ids += re.findall(r'''modId\s*=\s*['"]([^'"]+)['"]''', clean)
    if not ids:                                    # 内联数组写法 mods=[{modId="x"}]
        m = re.search(r"\bmods\s*=\s*\[(.*?)\]", txt, re.S)
        if m:
            clean = "\n".join(ln for ln in m.group(1).splitlines() if not ln.lstrip().startswith("#"))
            ids += re.findall(r'''modId\s*=\s*['"]([^'"]+)['"]''', clean)
    return ids


def jar_content_hint(names, modids):
    """判断这个 jar 是「内容模组」还是「库/QoL」——单次遍历计数，别对每个 ns 反复扫。

    内容信号（命中任一阈值才算内容模组，**带数量阈值**是为了不被"顺手塞了个测试物品"的库骗到；
    实测调过：Patchouli / Titanium / TerraBlender 这类库会被「只要有 models/item 就算」的宽松规则误判）：
      blockstates ≥ 1        有方块
      models/item ≥ 3        有自己的物品
      textures/block+item ≥ 6  有自己的贴图
      recipe(s) ≥ 3          加了自己的配方
      worldgen 群系/结构 ≥ 3  群系/地形 mod
    否则视为库 / QoL / 性能 / 框架（无自己的玩法内容）。
    """
    nsdirs, cnt = set(), defaultdict(int)
    for n in names:
        parts = n.split("/", 3)
        if len(parts) < 3:
            continue
        top, ns, sub = parts[0], parts[1], parts[2]
        if top not in ("assets", "data"):
            continue
        nsdirs.add(ns)
        rest = parts[3] if len(parts) > 3 else ""
        if top == "assets":
            if sub == "blockstates":
                cnt[(ns, "bs")] += 1
            elif sub == "models" and rest.startswith("item"):
                cnt[(ns, "mi")] += 1
            elif sub == "textures" and rest.split("/", 1)[0] in ("block", "item"):
                cnt[(ns, "tex")] += 1
        else:
            if sub in ("recipe", "recipes"):        # 1.21+ 单数 / ≤1.20.1 复数
                cnt[(ns, "rec")] += 1
            elif sub == "worldgen" and rest.split("/", 1)[0] in (
                    "biome", "structure", "configured_feature", "placed_feature"):
                cnt[(ns, "wg")] += 1
    for ns in {m.lower() for m in modids} | nsdirs:
        if (cnt[(ns, "bs")] >= 1 or cnt[(ns, "mi")] >= 3 or cnt[(ns, "tex")] >= 6
                or cnt[(ns, "rec")] >= 3 or cnt[(ns, "wg")] >= 3):
            return "content"
    return "lib"


def modids_from_jar(p: Path):
    """返回 (modids, hint, 错误)。

    ⚠️ 平台元数据只能取**一家**，不能合并（2026-09-13 实测踩坑）：
    有些 jar 同时带 `META-INF/*.mods.toml`（Forge/NeoForge）和 `fabric.mod.json`（Fabric），
    两边声明的 modId 可能**不一样**。若两个都收，NeoForge 包里会凭空多出幽灵 modId：
      · HopoBetterRuinedPortals → toml=`hoporp` / fabric=`hopobetterruinedportal`（多一个假 id）
      · entity_texture_features → fabric 元数据把 `neoforge` 当成自己的 id（污染更严重）
    幽灵 id 永远不可能被任务书"提及"，会**永久压低覆盖率**，还会被误报成"内容模组零提及"。

    优先级：Forge/NeoForge toml > Fabric json（本工具主战场是 NeoForge/Forge；Fabric 包没 toml 才落到 json）。
    """
    out = []
    try:
        with zipfile.ZipFile(p) as zf:
            names = zf.namelist()
            nameset = set(names)
            for tn in TOML_NAMES:
                if tn in nameset:
                    out += _toml_modids(zf.read(tn).decode("utf-8", "ignore"))
                    break
            if not out:                            # 只有没读到 toml 时才认 fabric 元数据
                for fn in FABRIC_NAMES:
                    if fn in nameset:
                        try:
                            d = json.loads(zf.read(fn).decode("utf-8", "ignore"))
                            if isinstance(d, dict) and d.get("id"):
                                out.append(str(d["id"]))
                        except Exception:
                            pass
            hint = jar_content_hint(names, out)
    except Exception as e:
        return [], "lib", f"读取失败：{e}"
    seen, uniq = set(), []
    for m in out:
        if m not in seen:
            seen.add(m)
            uniq.append(m)
    return uniq, hint, None


def modids_from_list(path: Path):
    """从清单 JSON 尽量抠 modid/slug（不同 schema 都试）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return [], f"清单读取失败：{e}"
    found = []

    def walk(o):
        if isinstance(o, dict):
            for k in ("modid", "modId", "slug", "id", "projectID", "name"):
                v = o.get(k)
                if isinstance(v, str) and re.fullmatch(r"[a-z][a-z0-9_\-]{1,40}", v):
                    found.append(v)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    return sorted(set(found)), None


# ---------------------------------------------------------------- 解析任务书

def collect_quest_files(root: Path):
    files = []
    for g in QUEST_FILE_GLOBS:
        files.extend(root.glob(g))
    return sorted({f for f in files if f.is_file()})


def _array_span(txt: str, start: int):
    """从 txt[start] 处的 '[' / '{' 出发，做括号平衡扫描（跳过字符串），返回结束下标（不含）。"""
    depth, j, in_str = 0, start, False
    n = len(txt)
    while j < n:
        c = txt[j]
        if in_str:
            if c == "\\":
                j += 2
                continue
            if c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c in "[{":
                depth += 1
            elif c in "]}":
                depth -= 1
                if depth == 0:
                    return j + 1
        j += 1
    return n


def _spans(txt: str, key: str):
    """找 `key: [ ... ]` 的区间（支持 snbt 与 json5 两种键写法）。"""
    spans = []
    pat = re.compile(r'(?:"%s"|\b%s\b)\s*:\s*\[' % (key, key))
    for m in pat.finditer(txt):
        i = txt.index("[", m.start())
        spans.append((i, _array_span(txt, i)))
    return spans


def _block_spans(txt: str, a: int, b: int):
    """在 [a,b) 范围内找出**顶层** `{ ... }` 对象的区间。"""
    out, i = [], a
    while i < b:
        c = txt[i]
        if c == '"':                       # 跳过字符串（描述文本里的括号不算）
            j = i + 1
            while j < b:
                if txt[j] == "\\":
                    j += 2
                    continue
                if txt[j] == '"':
                    break
                j += 1
            i = j + 1
            continue
        if c == "{":
            j = _array_span(txt, i)
            out.append((i, j))
            i = j
            continue
        i += 1
    return out


def iter_quests(txt: str):
    """产出 (quest_id, 是否有 dependencies, [被依赖的 id...])。

    只认 **quest 层级** 的 id：落在 `quests: [...]` 的顶层对象里、且不在 `tasks`/`rewards` 内。
    （task / reward / 章节的 id 同样是 16 位 hex，不排除会把统计口径搞乱。）
    """
    for a, b in _spans(txt, "quests"):
        for i, j in _block_spans(txt, a + 1, b - 1):
            seg = txt[i:j]
            sub = _spans(seg, "tasks") + _spans(seg, "rewards")
            ids = [m.group(1).upper() for m in ID_RE.finditer(seg)
                   if not any(x <= m.start() < y for x, y in sub)]
            if not ids:
                continue
            refs = []
            for m in DEPEND_RE.finditer(seg):
                refs += [h.group(1).upper() for h in HEX16_RE.finditer(m.group(1))]
            yield ids[0], bool(refs), refs


def scan_quest_texts(root: Path):
    """返回 (ns_counter, quest_id_files, dep_refs, dup_ids, has_dep_ids, 全文, 文件数)"""
    ns_counter = defaultdict(int)
    id_files = defaultdict(set)
    deps = defaultdict(set)
    has_dep = set()
    files = collect_quest_files(root)
    chunks = []

    for f in files:
        try:
            txt = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        chunks.append(txt)
        for m in NS_RE.finditer(txt):
            ns = m.group(1).lower()
            if ns in NON_MOD_NS:
                continue
            ns_counter[ns] += 1
        for qid, with_dep, refs in iter_quests(txt):
            id_files[qid].add(str(f))
            if with_dep:
                has_dep.add(qid)
            for r in refs:
                deps[r].add(str(f))

    # 同一个 quest id 出现在多个文件里 = 真重复（同文件内多次出现属正常写法，不计）
    dup = {qid: fs for qid, fs in id_files.items() if len(fs) > 1}
    return ns_counter, id_files, deps, dup, has_dep, "\n".join(chunks), len(files)


def text_mentions(modids, all_text: str):
    """裸 modid 文本提及（描述里写「本章用到：kotlinforforge」这种，没有冒号）。

    只认 **长度 ≥4 且独立成词** 的 modid，避免 `create` / `ae2` 在英文句子里乱撞。
    中文译名（如「机械动力」）无法识别——所以规范是描述里带上 modid 原文。

    ⚠️ 关键：先剥掉 Minecraft 颜色码（`&8` / `§e` / `&l` 这种两字符标记）。
    否则 `&8alexsmobs` 里的 `8` 属于 [A-Za-z0-9]，前边界不成立 → 明明写了却报"零提及"。
    实测用户几乎都会写 `subtitle: "&8Better Combat"` 这种形式，不剥离就会大面积假阴性。
    """
    hits = set()
    if not all_text:
        return hits
    # 剥颜色码：& 或 § 后跟一个 [0-9a-fk-orx]；剥完用空格占位以保住词边界
    plain = re.sub(r"[&§][0-9a-fk-orx]", " ", all_text, flags=re.I)
    for mid in modids:
        if len(mid) < 4 or mid.lower() in NON_MOD_NS:
            continue
        if re.search(r"(?<![A-Za-z0-9_\-])%s(?![A-Za-z0-9_\-])" % re.escape(mid),
                     plain, re.I):
            hits.add(mid)
    return hits


# ---------------------------------------------------------------- 匹配

def norm(s: str) -> str:
    return re.sub(r"[-_]", "", s.lower())


# 这些后缀在 modId 里有、命名空间里没有（如 kotlinforforge → kotlin）
_SUFFIX_RE = re.compile(r"(neoforge|forge|fabric|common|core|lib|mod)$")


def _strip(s: str) -> str:
    return _SUFFIX_RE.sub("", norm(s))


def match_ns(ns: str, modids: set):
    """返回 (命中modId, 是否模糊)。

    只做三级匹配，**故意不做子串匹配**——`create` 不应当被当作 `createaddition` 的命中
    （子串匹配会掩盖真正零提及的模组，属假阴性，比漏报更危险）。
    """
    if ns in modids:
        return ns, False
    nn = norm(ns)
    for mid in modids:
        if norm(mid) == nn:
            return mid, True
    sn = _strip(ns)
    if len(sn) >= 4:
        for mid in modids:
            if _strip(mid) == sn:
                return mid, True
    return None, False


def resolve_paths(a):
    mods_dir, quests_dir = None, None
    if a.mods:
        mods_dir = Path(a.mods)
    if a.quests:
        quests_dir = Path(a.quests)
    if a.target:
        t = Path(a.target)
        if mods_dir is None and t.name.lower() != "mods":
            cand = t / "mods"
            if cand.is_dir():
                mods_dir = cand
        elif mods_dir is None and t.name.lower() == "mods":
            mods_dir = t
        if quests_dir is None:
            for cand in (t / "config" / "ftbquests", t if t.name == "ftbquests" else None):
                if cand and cand.is_dir():
                    quests_dir = cand
                    break
    return mods_dir, quests_dir


def main():
    ap = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="任务书覆盖度 / 引用一致性 / 依赖链 体检",
        epilog="退出码：0 达标 / 1 有问题 / 2 参数错误",
    )
    ap.add_argument("target", nargs="?", help="实例目录（自动找 mods/ 与 config/ftbquests/）")
    ap.add_argument("--mods", help="mods 目录（或单个 jar）")
    ap.add_argument("--quests", help="config/ftbquests 目录（或 quests 目录）")
    ap.add_argument("--modlist", help="无 mods/ 时用清单 JSON 代替")
    ap.add_argument("--md", help="同时写一份 markdown 报告到该路径")
    ap.add_argument("--chain", action="store_true", help="只跑依赖链检查（C）")
    ap.add_argument("--strict", action="store_true", help="库/QoL 模组零提及也算失败")
    ap.add_argument("--quiet", action="store_true", help="只输出结论行")
    a = ap.parse_args()

    mods_dir, quests_dir = resolve_paths(a)
    if not quests_dir:
        print("[错误] 找不到任务书目录")
        print("  · 用 --quests 显式指定，通常是 <实例目录>/config/ftbquests")
        print("  · 若整合包还没写过任务书，这个脚本查不了 —— 先按 references/任务书设计.md 写一版")
        return 2

    L = []
    problems = 0

    # ---------- 读 modid + 内容/库分层 ----------
    modid_files = defaultdict(list)
    modid_hint = {}
    errs = []
    jars = []
    if mods_dir and mods_dir.is_dir():
        jars = sorted(mods_dir.glob("*.jar"))
    elif mods_dir and mods_dir.suffix.lower() == ".jar" and mods_dir.is_file():
        jars = [mods_dir]
    if jars:
        for j in jars:
            ids, hint, err = modids_from_jar(j)
            if err:
                errs.append(f"{j.name}: {err}")
            for m in ids:
                modid_files[m].append(j.name)
                modid_hint[m] = hint
        src = f"{mods_dir}（{len(jars)} 个 jar）"
    elif a.modlist:
        ids, err = modids_from_list(Path(a.modlist))
        if err:
            print(f"[错误] {err}")
            print("  · 清单需为 JSON 数组或含 mods[] 的对象，元素里带 modid / slug")
            return 2
        for m in ids:
            modid_files[m].append("(清单)")
            modid_hint[m] = "content"       # 清单没有 jar，无法判定，保守按内容模组算
        src = f"清单 {a.modlist}（{len(ids)} 条，无法判库/内容，全部按内容模组算）"
    else:
        src = "（未提供 mods/ 或清单，跳过覆盖度对账）"

    modid_files = {k: v for k, v in modid_files.items() if k.lower() not in NON_MOD_NS}
    modids = set(modid_files)
    content_ids = {m for m in modids if modid_hint.get(m) == "content"}
    lib_ids = modids - content_ids

    ns_counter, id_files, deps, dup, has_dep, all_text, nfiles = scan_quest_texts(quests_dir)

    L.append("# 任务书覆盖度体检报告")
    L.append("")
    L.append(f"- 模组来源：{src}")
    L.append(f"- 任务书目录：`{quests_dir}`（{nfiles} 个文件）")
    L.append(f"- 读到 modId：**{len(modids)}** 个（内容模组 {len(content_ids)} / 库·QoL {len(lib_ids)}）"
             f"；任务书引用到的命名空间：**{len(ns_counter)}** 个")
    L.append("")

    # ---------- A. 覆盖度 ----------
    covered, fuzzy, text_only = {}, {}, {}
    uncovered_content, uncovered_lib = [], []
    if modids:
        for ns in ns_counter:
            hit, fz = match_ns(ns, modids)
            if hit:
                covered[hit] = ns_counter[ns]
                if fz:
                    fuzzy[hit] = ns
        for m in text_mentions(modids, all_text):
            if m not in covered:
                text_only[m] = True
        miss = sorted(m for m in modids if m not in covered and m not in text_only)
        uncovered_content = [m for m in miss if m in content_ids]
        uncovered_lib = [m for m in miss if m in lib_ids]
        hit_n = len(modids) - len(miss)
        rate = 100.0 * hit_n / max(1, len(modids))
        L.append(f"## A. 覆盖度：{hit_n}/{len(modids)} = **{rate:.1f}%**"
                 f"（内容模组 {len(content_ids) - len(uncovered_content)}/{len(content_ids)}"
                 f" 已覆盖；{len(text_only)} 个仅文本提及）")
        L.append("")

        if uncovered_content:
            problems += 1
            L.append(f"### ❌ 内容模组零提及（{len(uncovered_content)} 个）—— 玩家根本不知道这包有它")
            L.append("")
            L.append("| modId | jar |")
            L.append("|---|---|")
            for m in uncovered_content:
                L.append(f"| `{m}` | {modid_files[m][0]} |")
            L.append("")
            L.append("> 按 `references/任务书设计.md` §1.1 给它们各自安排落点："
                     "有玩法的开独立章节/任务链，玩法轻的至少一条任务 + 说明。")
            L.append("")
        elif content_ids:
            L.append("内容模组全部有落点。✔")
            L.append("")

        if uncovered_lib:
            if a.strict:
                problems += 1
            L.append(f"### ○ 库 / QoL / 性能模组零提及（{len(uncovered_lib)} 个）"
                     f"{'　← --strict 已计入失败' if a.strict else '（不计入失败）'}")
            L.append("")
            L.append("| modId | jar |")
            L.append("|---|---|")
            for m in uncovered_lib[:60]:
                L.append(f"| `{m}` | {modid_files[m][0]} |")
            if len(uncovered_lib) > 60:
                L.append(f"| … | 另有 {len(uncovered_lib) - 60} 个 |")
            L.append("")
            L.append("> 这些没有自己的玩法，但**最好也在某章 description 的「本章用到」里出现一次**"
                     "（玩家会问「我这 JEI 怎么用」）。最省事的做法：P0 开局做一个「这包有什么」的任务，"
                     "把它们按类列一遍。")
            L.append("")

        if text_only:
            L.append("### ○ 仅靠描述文本提及（未出现在任何物品/任务 id 里）")
            L.append("")
            L.append("| modId | 类型 | 说明 |")
            L.append("|---|---|---|")
            for m in sorted(text_only):
                kind = "内容模组" if m in content_ids else "库/QoL"
                note = "玩法没被带进流程，建议开任务" if m in content_ids else "这样处理是够的"
                L.append(f"| `{m}` | {kind} | {note} |")
            L.append("")

        if fuzzy:
            L.append("### ⚠ 命名空间与 modId 不一致（人工确认）")
            L.append("")
            for m, ns in sorted(fuzzy.items()):
                L.append(f"- `{m}` ← 任务书里用的是 `{ns}:`")
            L.append("")

        L.append("> 中文译名（如「机械动力」）无法被脚本识别——描述里带上 modid 原文，"
                 "既能让脚本认出来，玩家也能搜到。")
        L.append("")

    # ---------- B. 反查 ----------
    unknown = (sorted(ns for ns in ns_counter if not match_ns(ns, modids)[0])
               if modids else sorted(ns_counter))
    if unknown:
        L.append("## B. 任务书引用了、但当前 mods/ 里没有的命名空间")
        L.append("")
        L.append("| 命名空间 | 出现次数 | 影响 |")
        L.append("|---|---|---|")
        for ns in unknown:
            L.append(f"| `{ns}:` | {ns_counter[ns]} | "
                     f"物品/结构 id 拼错 → 任务**永远做不了**且不报错；若是已移除的 mod → 引用要同步清掉 |")
        L.append("")
        L.append("> 跨 mod 共用物品（如 `c:` tag）、整合包自带数据包的命名空间会正常出现在这里，"
                 "判据是 id 在游戏里能不能解析出来。")
        L.append("")

    # ---------- C. 依赖链 ----------
    if dup:
        problems += 1
        L.append(f"## C0. ❌ quest id 重复（{len(dup)} 个）—— 会被后加载的覆盖")
        L.append("")
        for qid in sorted(dup):
            L.append(f"- `{qid}` 出现在 {len(dup[qid])} 个文件：" +
                     "、".join(f"`{Path(p).name}`" for p in sorted(dup[qid])))
        L.append("")

    missing = {k: v for k, v in deps.items() if k not in id_files}
    if missing:
        problems += 1
        L.append(f"## C1. ❌ 断链：dependencies 引用了不存在的 id（{len(missing)} 个）")
        L.append("")
        L.append("| 被引用的 id | 谁在引用 |")
        L.append("|---|---|")
        for qid, fs in sorted(missing.items()):
            L.append(f"| `{qid}` | " + "、".join(f"`{Path(p).name}`" for p in sorted(fs)) + " |")
        L.append("")
        L.append("> 断链 = 该任务**永久锁死**（前置永远不成立），玩家看不到也做不了。")
        L.append("")
    else:
        L.append(f"## C1. 依赖链：{len(deps)} 条依赖引用全部存在。✔")
        L.append("")

    if not a.chain:
        orphans = sorted(k for k in id_files if k not in has_dep)
        L.append(f"## C2. 递进概览：任务 {len(id_files)} 个，其中**无前置** {len(orphans)} 个")
        L.append("")
        L.append(f"- 有前置（接在链上）：{len(id_files) - len(orphans)} 个")
        L.append(f"- 无前置（章节起点 / 里程碑）：{len(orphans)} 个")
        if orphans:
            L.append(f"  - `{'`、`'.join(orphans[:12])}`" + ("…" if len(orphans) > 12 else ""))
        L.append("")
        L.append("> 只有当**差不多每个任务都无前置**时才说明链没搭起来；健康包的无前置任务"
                 "应集中在各阶段的起点/里程碑上（`references/任务书设计.md` §2 的阶段表）。")
        L.append("")

    report = "\n".join(L)
    if a.quiet:
        total = len(modids)
        print(f"覆盖度 {total - len(uncovered_content) - len(uncovered_lib)}/{total}"
              f"（内容模组漏 {len(uncovered_content)}，库漏 {len(uncovered_lib)}），"
              f"断链 {len(missing)}，id 重复 {len(dup)}")
    else:
        print(report)

    if errs:
        print(f"[!] {len(errs)} 个 jar 读取异常（不影响结论）：{errs[0]}")
    if a.md:
        Path(a.md).write_text(report, encoding="utf-8")
        print(f"[√] 报告已写入 {a.md}")

    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

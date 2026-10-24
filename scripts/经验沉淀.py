#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""经验沉淀.py —— skill 的「自我优化闭环」工具（2026-10-12 加）。

为什么需要它
------------
这个 skill 的知识 95% 来自实测踩坑，但沉淀环节此前全靠自觉：
「解决后回头补进 SKILL.md」写在那儿，漏没漏全凭记性。三类实际发生过的腐烂：

  ① 引用悬空 —— SKILL.md 引用 `references/验证判据设计.md`，文件只在姊妹 skill
     那边（2026-10-01 实测），实战时「按 SKILL.md 去找文档，找不到」；
  ② 速查表过期 —— 新写了一个脚本，SKILL.md 的表格没登记，等于功能不存在；
  ③ 生态断言过期 —— 「快照（2026-09 实测，会变）」类内容没人追责，
     半年后照着做就翻车。

三个模式
--------
  python 经验沉淀.py --find <关键词> [关键词...]   # ① 定位：新坑该记到哪 + 是否已记录过
  python 经验沉淀.py --doctor                      # ② 体检：文档引用完整性（发现问题退出码 1）
  python 经验沉淀.py --stale [--days 90] [--strict] # ③ 时效：过期「快照/生态断言」清单
  python 经验沉淀.py                               # doctor + stale 一起跑

用法细则
--------
--find  按关键词在 SKILL.md + references/ 全文检索，按命中数排序给候选落点；
        同一行（或同一个小节）命中**全部**关键词 → 标「疑似已记录」，只补增量别重写。
        输出带「最近小节标题 + 行号」，直接 Edit 定位用。
--doctor 四项检查（全部只读，无副作用）：
        H1 显式路径引用存在（references/…、scripts/…、templates/…）；
        H2 §锚点存在（同一行出现 `xxx.md` 与 `§N[.M]` 时，核对该文档真有该编号标题）；
        H3 双向漂移（scripts/ 与 references/ 里有、但全部文档都没提过的文件 = 速查表过期；
            白名单见 SKIP_DOCUMENTED）；
        H4 归档配对（`xxx.pack.txt` 与实体 `xxx` 必须同时存在——上架分发契约）。
--stale 扫描自带日期戳、且落在「快照/生态/会变/重新核实」语境里的断言，
        按「该小节内最新日期」算年龄，超过 --days（默认 90）的列出。
        报告型检查，默认退出 0；--strict 时超龄算 1（可串流水线）。

退出码：0 = 干净 / 1 = doctor 发现问题（或 --stale --strict 超龄）/ 2 = 用法问题

⚠️ 本脚本**两个 skill 共用同一份**。改动后记得：
   python scripts/交叉同步.py --sync --force   # 推到姊妹 skill
   python scripts/自检.py                      # 全链复检
"""
import argparse
import datetime
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ──────────────────────────────────────────────────────────────────────
# 常量
# ──────────────────────────────────────────────────────────────────────

# H3 白名单：确实不需要被任何文档提及的文件（生成物 / 测试副本 / 环境文件）
# 往这里加之前先想清楚：是不是应该反过来把它的用途写进 SKILL.md？
SKIP_DOCUMENTED = {
    "__pycache__",
    "block_colors.py",       # 生成方块配色.py 的产物，世界预览.py 直接 import；出处文档已记载生成器
}

# H1 白名单：形如 `scripts/…` 但**不是本 skill 资产**的提法（行文里的实例目录/用户脚本示例）
# 往这里加之前先想清楚：会不会其实是真引用写错了？
IGNORE_REFS = {
    "scripts/kubejs",       # 整合包实例里的 KubeJS 数据目录（白名单列举，非 skill 资产）
    "scripts/pack_lite.py", # 用户工程里的轻量打包脚本示例（skill 里对应的是 增量重打包.py）
}

# 路径引用里允许出现的字符（含 CJK；排除 CJK 标点与空白；`*` 容纳 `templates/neoforge-*` 这类通配提法）
_PATHCH = r"[A-Za-z0-9_\-\.\u4e00-\u9fff*]"
# (?<![\w/]) —— 排除 `src/main/templates/META-INF/...` 这类**源码树路径**（前缀不是 skill 根）
PATH_REF_RE = re.compile(r"(?<![\w/])(?:references|scripts|templates)/" + _PATHCH + r"+")
# 同一行「文件名 + §编号」：用于 H2 锚点核对
SEC_REF_RE = re.compile(r"(" + _PATHCH + r"+\.(?:md|json))")
SEC_NUM_RE = re.compile(r"§(\d+(?:\.\d+)?)")
# 标题编号：## 17. xxx / ### 17.3 xxx（也容忍 「## 2.5」紧贴）
HEAD_NUM_RE = re.compile(r"^#{2,6}\s*(\d+(?:\.\d+)?)\s*[\.、．\s）)]")
# 日期戳：2026-09-12 / 2026-09（月精度两种都认）
DATE_RE = re.compile(r"(202[5-9])-(\d{2})(?:-(\d{2}))?")
# 「易腐」语境关键词：命中才进 --stale 报告（纯踩坑教训带日期不算腐，事实断言才算）
VOLATILE_MARKERS = (
    "快照", "生态", "会变", "重新核实", "截至", "目前最新",
    "当前实际", "最新正式版", "求最新", "直连", "镜像",
)
HEADING_RE = re.compile(r"^#{1,6}\s+(.*)$")


# ──────────────────────────────────────────────────────────────────────
# 基础工具
# ──────────────────────────────────────────────────────────────────────

def read_norm(p: Path) -> str:
    for enc in ("utf-8", "utf-8-sig", "gbk"):
        try:
            return p.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return p.read_text(encoding="utf-8", errors="replace")


def doc_files(root: Path):
    """SKILL.md + references/ 下全部 .md。"""
    out = []
    sk = root / "SKILL.md"
    if sk.exists():
        out.append(sk)
    rd = root / "references"
    if rd.is_dir():
        out.extend(sorted(p for p in rd.iterdir() if p.suffix == ".md"))
    return out


def headings_with_lines(text: str):
    """[(行号0基, 标题文本)]，供「命中行属于哪个小节」定位。"""
    out = []
    for i, line in enumerate(text.split("\n")):
        m = HEADING_RE.match(line)
        if m:
            out.append((i, m.group(1).strip()))
    return out


def heading_nums(text: str):
    """文档里实际存在的编号标题集合（'17'、'17.3'……）。"""
    nums = set()
    for line in text.split("\n"):
        m = HEAD_NUM_RE.match(line)
        if m:
            nums.add(m.group(1))
    return nums


def nearest_heading(hs, lineno):
    cur = "（文档开头）"
    for i, t in hs:
        if i <= lineno:
            cur = t
        else:
            break
    return cur


# ──────────────────────────────────────────────────────────────────────
# 模式 ①：--find 定位与查重
# ──────────────────────────────────────────────────────────────────────

def cmd_find(root: Path, keywords, quiet=False):
    if not keywords:
        print("[用法错误] --find 至少要给一个关键词", file=sys.stderr)
        return 2
    kws = [k.lower() for k in keywords]
    results = []  # (命中行数, 全关键词命中数, path)
    for p in doc_files(root):
        orig = read_norm(p)
        text = orig.lower()
        if not any(k in text for k in kws):
            continue
        lines = orig.split("\n")
        lowered = text.split("\n")
        hs = headings_with_lines(orig)
        hits, full_hits = [], 0
        for i, (line, low) in enumerate(zip(lines, lowered)):
            matched = [k for k in kws if k in low]
            if matched:
                hits.append((i, line, len(matched)))
                if len(matched) == len(kws):
                    full_hits += 1
        if hits:
            results.append((len(hits), full_hits, p, hits, hs))
    results.sort(key=lambda r: (-r[1], -r[0]))

    if not results:
        print(f"✓ 没有任何文档命中 {keywords} —— 这是一个新坑，走四步闭环沉淀它：")
        print("  ① 已定位：无先例 → 通常进 SKILL.md 的「症状 → 去哪查」表或对应 26.x-* 文档")
        print("  ② 写入「症状(可grep原文) → 根因 → 判据(可执行)」，带 YYYY-MM-DD 实测戳")
        print("  ③ python scripts/经验沉淀.py --doctor   # 验证引用没写坏")
        print("  ④ python scripts/交叉同步.py --sync && python scripts/跨agent同步.py --sync")
        return 0

    print(f"# 「{' '.join(keywords)}」的候选落点（按相关度排序）\n")
    dup_found = False
    for n, full, p, hits, hs in results[:6]:
        rel = p.relative_to(root)
        tag = ""
        if full:
            tag = "  ⚠ 疑似已记录（同小节命中全部关键词）——只补增量，别重写"
            dup_found = True
        print(f"## {rel}  （命中 {n} 行，其中整行全关键词 {full} 行）{tag}")
        shown = 0
        # 优先展示全关键词命中的行
        for i, line, c in sorted(hits, key=lambda h: -h[2])[:5]:
            snip = line.strip()
            if len(snip) > 120:
                snip = snip[:117] + "..."
            print(f"  - L{i + 1} 【{nearest_heading(hs, i)}】 {snip}")
            shown += 1
        print()
    if dup_found:
        print("> ⚠ 有小节已命中全部关键词：先读那一节，确认你遇到的是**同一个坑**"
              "（补数据/边界）还是**新坑**（另立条目），再动笔。")
    else:
        print("> 没有小节同时命中全部关键词 —— 大概率是新坑，挑最相关的落点写入。")
    return 0


# ──────────────────────────────────────────────────────────────────────
# 模式 ②：--doctor 文档引用完整性
# ──────────────────────────────────────────────────────────────────────

def doctor(root: Path, quiet=False):
    problems, details = [], []
    docs = doc_files(root)
    if not docs:
        return ["连 SKILL.md 都没找到——root 指错了吧"], ["  ✗ 无文档"]

    _cache = {}

    def cached_read(p: Path) -> str:
        if p not in _cache:
            _cache[p] = read_norm(p)
        return _cache[p]

    # ---- H1 显式路径引用存在 ----
    h1 = []
    for p in docs:
        text = cached_read(p)
        for m in PATH_REF_RE.finditer(text):
            ref = m.group(0).rstrip(".")
            if "*" in ref:
                continue  # `templates/neoforge-*` 这类通配提法，不指向具体资产
            if ref in IGNORE_REFS:
                continue
            if not (root / ref).exists():
                h1.append(f"{p.relative_to(root)}: 引用 `{ref}` 但文件不存在")
    if h1:
        problems.append(f"H1 显式路径引用悬空：{len(h1)} 处")
        details += [f"  ✗ {x}" for x in h1[:15]]
        if len(h1) > 15:
            details.append(f"  ……共 {len(h1)} 处")
    else:
        details.append("  ✓ H1 显式路径引用（references/… scripts/… templates/…）全部存在")

    # ---- H2 §锚点存在 ----
    # 归属规则：只有紧跟在 `.md` 文件名**之后 ~20 字符内**的 §N 才算指向该文档。
    # （写成「同行出现 .md 就核对该行所有 §」会误杀：SKILL.md 里「程序化生成（§4.1）
    #   / 变色批量（§4.2）/ 抄原版改造（references/改造原版复用.md）」——§4.1/§4.2 是
    #   SKILL.md 自己的小节，跟同行末尾那个 .md 毫无关系。宁可漏报，不可误杀。）
    h2 = []
    for p in docs:
        rel = p.relative_to(root)
        text = cached_read(p)
        for line in text.split("\n"):
            for fm in SEC_REF_RE.finditer(line):
                fname = fm.group(1)
                # 该文件名后 20 字符窗口内的全部 §N
                nums = SEC_NUM_RE.findall(line[fm.end():fm.end() + 20])
                if not nums:
                    continue
                if fname.lower() == "skill.md":
                    target, trel = root / "SKILL.md", "SKILL.md"
                else:
                    cands = [root / "references" / fname, root / fname]
                    target = next((c for c in cands if c.exists()), None)
                    trel = f"references/{fname}"
                if target is None:
                    continue  # H1 已报过存在性，这里不重复
                have = heading_nums(cached_read(target))
                for n in nums:
                    if n not in have:
                        h2.append(f"{rel}: 引用 `{trel}` 的 §{n}，但该文档没有编号为 {n} 的标题")
    if h2:
        problems.append(f"H2 §锚点悬空：{len(h2)} 处")
        details += [f"  ✗ {x}" for x in h2[:15]]
    else:
        details.append("  ✓ H2 §锚点（文件名+§编号同行出现处）全部能对上真实标题")

    # ---- H3 双向漂移：文件存在但没被记载 ----
    corpus = "\n".join(read_norm(p) for p in docs)
    h3 = []
    for sub, label in (("scripts", "脚本"), ("references", "文档")):
        d = root / sub
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if not p.is_file() or p.name in SKIP_DOCUMENTED:
                continue
            if p.name in ("__pycache__",) or p.suffix in (".pyc",):
                continue
            if p.name.endswith(".pack.txt"):
                continue  # 归档伴生文件由 H4 查配对，不要求被文档提及
            if p.name not in corpus:
                h3.append(f"{sub}/{p.name}")
    if h3:
        problems.append(f"H3 速查表过期：{len(h3)} 个文件没被任何文档提及")
        details += [f"  ✗ {x}  → 写进 SKILL.md 的脚本速查表，或加进 SKIP_DOCUMENTED 白名单（想清楚再加）"
                   for x in h3]
    else:
        details.append("  ✓ H3 scripts/ 与 references/ 的文件都在文档里被提及（或已白名单）")

    # ---- H4 归档配对 ----
    h4 = []
    sdir = root / "scripts"
    if sdir.is_dir():
        packs = {p.name[:-len(".pack.txt")]: p for p in sdir.glob("*.pack.txt")}
        for base, pp in packs.items():
            if not (sdir / base).exists():
                h4.append(f"scripts/{pp.name} 的实体 `scripts/{base}` 不存在（归档悬空）")
        # 根目录的 templates.pack.txt 单独看
        root_pack = root / "templates.pack.txt"
        if root_pack.exists() and not (root / "templates").exists():
            h4.append("templates.pack.txt 存在但 templates/ 目录不存在")
        # 已知散文件契约：.gradle / .ps1 必须有 .pack.txt 伴生（上架排除清单的补偿）
        for scatter in ("游戏内注入.gradle", "读图文字.ps1"):
            if (sdir / scatter).exists() and not (sdir / (scatter + ".pack.txt")).exists():
                h4.append(f"scripts/{scatter} 存在但缺 {scatter}.pack.txt（.packignore 排除后无法上架分发）")
    if h4:
        problems.append(f"H4 归档配对断裂：{len(h4)} 处")
        details += [f"  ✗ {x}" for x in h4]
    else:
        details.append("  ✓ H4 .pack.txt 归档与实体一一配对")

    return problems, details


# ──────────────────────────────────────────────────────────────────────
# 模式 ③：--stale 生态断言时效
# ──────────────────────────────────────────────────────────────────────

def cmd_stale(root: Path, days=90, strict=False, quiet=False):
    today = datetime.date.today()
    rows = []
    for p in doc_files(root):
        rel = p.relative_to(root)
        text = read_norm(p)
        lines = text.split("\n")
        # 按小节聚合：小节内命中易腐关键词 + 有日期 → 取最新日期算龄
        sec_start = 0
        sec_title = "（文档开头）"
        sec_volatile = False
        sec_latest = None  # (date, line_no)

        def flush(end):
            if sec_volatile and sec_latest:
                d = sec_latest[0]
                age = (today - d).days
                if age > days:
                    rows.append((age, rel, sec_title, d.isoformat(), sec_latest[1] + 1))

        for i, line in enumerate(lines):
            if HEADING_RE.match(line):
                flush(i)
                sec_start, sec_title = i, line.lstrip("# ").strip()
                sec_volatile = any(m in line for m in VOLATILE_MARKERS)
                sec_latest = None
                continue
            dm = DATE_RE.search(line)
            if dm:
                y, mo, dd = int(dm.group(1)), int(dm.group(2)), int(dm.group(3) or 15)
                try:
                    d = datetime.date(y, mo, dd)
                except ValueError:
                    continue
                if sec_latest is None or d > sec_latest[0]:
                    sec_latest = (d, i)
            if any(m in line for m in VOLATILE_MARKERS):
                sec_volatile = True
        flush(len(lines))

    rows.sort(key=lambda r: -r[0])
    print(f"# 易腐断言时效体检（阈值 {days} 天，共 {len(rows)} 个小节超龄）")
    print(f"- 扫描对象：SKILL.md + references/*.md")
    print(f"- 只报「快照/生态/会变/镜像」语境下带日期戳的小节，纯踩坑教训不报\n")
    if not rows:
        print("✓ 没有超龄的易腐断言。")
    else:
        for age, rel, title, d, ln in rows[:25]:
            print(f"  ⚠ {age:>4} 天前 · {rel} :: 「{title[:50]}」（断言日期 {d}，L{ln}）")
        if len(rows) > 25:
            print(f"  ……共 {len(rows)} 个，仅列前 25")
        print("\n> 这些内容自称「会变」。超龄不是错误，是提醒：下次用到先重新核实，"
              "顺手把新日期戳盖上去。")
    if strict and rows:
        return 1
    return 0


# ──────────────────────────────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        prog="经验沉淀.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="skill 自我优化闭环工具：--find 定位落点+查重 / --doctor 文档引用体检 / --stale 时效体检",
        epilog="退出码：0=干净 / 1=doctor发现问题（或 --stale --strict 超龄）/ 2=用法问题")
    ap.add_argument("--find", nargs="+", metavar="关键词",
                    help="定位：新坑该记到哪个文档、是否已记录过")
    ap.add_argument("--doctor", action="store_true", help="体检：文档引用完整性（四项检查）")
    ap.add_argument("--stale", action="store_true", help="时效：超龄的「快照/生态」断言清单")
    ap.add_argument("--days", type=int, default=90, help="--stale 的超龄阈值（默认 90 天）")
    ap.add_argument("--strict", action="store_true", help="--stale 超龄时退出码置 1（默认报告型退出 0）")
    ap.add_argument("--root", help="指定 skill 根目录（默认：脚本所在目录的上一级；测试沙箱用）")
    ap.add_argument("--quiet", action="store_true", help="doctor 模式只打印问题")
    a = ap.parse_args()

    root = Path(a.root).resolve() if a.root else SKILL_ROOT

    if a.find:
        return cmd_find(root, a.find, a.quiet)

    rc_stale = 0
    if a.stale:
        rc_stale = cmd_stale(root, a.days, a.strict, a.quiet)
        if not a.doctor:
            return rc_stale

    # 默认 / --doctor：跑体检
    problems, details = doctor(root, a.quiet)
    print(f"# {root.name} 文档引用完整性体检（--doctor）\n")
    if problems:
        print(f"## ⚠ {len(problems)} 类问题\n")
        for p in problems:
            print(f"- **{p}**")
        print()
        if not a.quiet:
            for d in details:
                print(d)
        print()
        print("> 修复后重跑本命令直到退出码 0；再跑 `python scripts/自检.py` 全链复检。")
        return 1
    print("## ✅ 全部通过\n")
    for d in details:
        print(d)
    return rc_stale


if __name__ == "__main__":
    sys.exit(main())

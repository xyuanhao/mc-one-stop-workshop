#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交叉同步.py —— MC 双 skill 的「mod 公共资产」对齐检查与补齐。

背景
----
`MC一站式工坊`（mc-one-stop-workshop）和 `MC模组工坊`（mc-mod-workshop）
覆盖了**同一批 mod 开发知识**：两边各存一份 `references/` 文档和 `scripts/`
工具。复制成两份之后就必然开始漂 —— 实测（2026-10-01）就抓到：

  · `代码片段.md` 模组工坊已写到 2043 行，一站式还停在 1438 行（丢 §18/§19 两章）
  · `26.x-自定义世界生成器.md` 模组工坊多出 §15/§16（一体腔 + 判据方向性）
  · `改造原版复用.md` 反过来是一站式新（多 §7/§8）
  · **一站式 SKILL.md 引用了 `references/验证判据设计.md`，但那个文件只存在于模组工坊** ——
    引用悬空，实战时会「按 SKILL.md 去找文档，找不到」
  · 一站式独有的 `26.x-自定义升级与材料系统.md` / `26.x-自定义容器界面贴图拼装.md` /
    `26.x-跨模组方块识别与运行时探针.md` 三篇 mod 文档，模组工坊完全没收到

所以定了这套规矩：**mod 公共资产两边必须逐字一致；任何一边升级了 mod 相关内容，
顺手把对方补齐**。本脚本就是执行这件事的工具。

用法
----
    python 交叉同步.py                  # 只检查，报告差异（默认）
    python 交叉同步.py --sync           # 把「仅一方有」的资产补到另一方（纯加法，安全）
    python 交叉同步.py --sync --force   # 连「漂移」的也统一（仅当一边是另一边的超集；分叉的会拒绝）
    python 交叉同步.py --take A         # 人工确认「一站式是最新版」→ 所有差异一键推过去
    python 交叉同步.py --take B         # 同上，以「模组工坊」为准
    python 交叉同步.py --diff 代码片段.md
    python 交叉同步.py --list           # 列清单
    python 交叉同步.py --audit          # 额外报「未归类文件」（清单过期检测）
    python 交叉同步.py --md 报告.md

退出码：0=已对齐 / 1=有差异待处理 / 2=用法或路径问题

为什么不自动合并「分叉」
----------------------
两边内容互不包含时（A 有 A 的增补、B 有 B 的增补），自动覆盖**一定会丢内容**。
这时脚本拒绝执行并让你人工合并 —— 宁可停下来，不要静默丢东西。
（这条是被 2026-09-28 那次「改完逻辑没重跑存量产物」教出来的：自动化的错误比手工慢更贵。）

「替换型修改」（改了某一行、而不是在末尾追加）天然会被判成分叉 —— 这是**故意的**：
看一眼 `--diff` 确认哪边是新版，然后用 `--take A` / `--take B` 显式覆盖。
"""
import argparse
import hashlib
import os
import shutil
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ══════════════════════════════════════════════════════════════════════
# ① 资产清单（两边逐字一致的那部分）
# ══════════════════════════════════════════════════════════════════════

SKILL_A = "MC一站式工坊"
SKILL_B = "MC模组工坊"

# mod 公共文档：两边都必须有，且内容一致
MOD_REFERENCES = [
    # —— 通用 mod 开发 ——
    "代码片段.md",
    "版本差异矩阵.md",
    "工程最佳实践.md",
    "资源文件.md",
    "迁移指南.md",
    "改造原版复用.md",
    "实战拆解-真实mod架构.md",
    "forge-neoforge.md",
    "fabric-architectury.md",
    # —— 验证与排障 ——
    "专用服务端自动化验证.md",
    "验证判据设计.md",
    "多代理协作.md",         # 大任务 DAG / 文件所有权 / 子代理回收与整体验收（2026-10-24 归类）
    "方块光照与透光.md",     # noOcclusion 是透光的守门条件；遮挡/LightEngine 判定链（2026-10-14 归类）
    # —— 客户端侧自动化（真实客户端 + 进程内代理）——
    "26.x-游戏内自动化测试.md",
    # —— 26.x 专题 ——
    "26.x-自定义世界生成器.md",
    "26.x-维度高度与垂直边界.md",
    "26.x-雾效与深层结构改法.md",
    "26.x-自定义群系注入.md",
    "26.x-数据包地形生成.md",   # 数据包级 density_function 改地形（和代码级生成器并列的那条路）
    "26.x-按键系统改造.md",
    "26.x-自定义升级与材料系统.md",
    "26.x-自定义容器界面贴图拼装.md",
    "26.x-跨模组方块识别与运行时探针.md",
]

# mod 公共脚本：两边都必须有，且内容一致
MOD_SCRIPTS = [
    # 建工程 / 写内容
    "新建工程.py",
    "检查工程.py",
    "版本号.py",            # 版本号查看/递增/一致性门禁/实例查重（检查工程.py 也 import 它）
    "迁移检查.py",
    "渲染API核对.py",
    # 资源 / 贴图 / 模型
    "像素纹理生成.py",
    "纹理变色.py",
    "原版资源提取.py",
    "实体模型预览.py",
    "体素模型生成.py",      # 16x16 贴图 -> 3D 体素物品模型
    "物品模型预览.py",      # 离线渲染物品模型（runClient 看不到画面时的验收手段）
    "物品模型校验.py",      # 按 MC schema 静态校验 element 模型
    "资源引用校验.py",      # 模型/贴图的静态引用完整性（紫黑方块防线；2026-10-14 归类）
    # 读 jar / class 做判断（mod 二进制分析，两边都用得上）
    "JarSig.py",
    "mixin查源.py",
    "scan_null_blockitem.py",
    "模组兼容扫描.py",
    # 世界生成取证链
    "密度函数引用链.py",
    "探地表高度.py",
    "按深度统计方块.py",
    "扫区域文件.py",
    "世界生成体检.py",
    "世界生成体检_自检.py",
    "世界预览.py",
    "生成方块配色.py",       # 从原版客户端材质提平均色 → block_colors.py
    "block_colors.py",       # 生成物，但两边都要有（世界预览.py 直接 import 它）
    "地形剖面.py",
    "rcon.py",
    # 客户端侧自动化（真实客户端 + 进程内代理；代理源码在 templates/测试代理-26.1/）
    "游戏内测试.py",
    "游戏内注入.gradle",
    # 工程维护
    "还原templates.py",
    # 模型看不了图时的兜底（Windows 自带 OCR 转文字，通用工具，两边都用得上）
    "读图文字.ps1",
    # 双 skill 基建（自举：这几个也靠本脚本保持两边一致）
    "自检.py",
    "交叉同步.py",
    "跨agent同步.py",        # 同一 skill 在多个 agent 目录（.workbuddy/.codebuddy/…）的副本对齐
    "经验沉淀.py",            # 自我优化闭环：--find 定位落点+查重 / --doctor 文档引用体检 / --stale 时效体检
    "多代理任务板.py",        # 大任务拆分的 DAG / 文件所有权 / 交付证据门禁（2026-10-24 归类）
]

# 目录型公共资产（递归比对文件清单 + 每个文件 md5）
MOD_TREES = [
    "templates",            # 脚手架模板（5 个平台 × 版本）
    "templates.pack.txt",   # 模板归档（打包分发形态）
]

# 散文件归档（上架用文本形态，两边都要有且一致）
MOD_ARCHIVES = [
    "scripts/游戏内注入.gradle.pack.txt",
    "scripts/读图文字.ps1.pack.txt",
]

# 两边都有、也应当一致的杂项
MOD_MISC = [
    ".packignore",
]

# 只在某一方的专属资产（**绝不往对方搬**）
EXCLUSIVE = {
    SKILL_A: [
        # 整合包 / 任务书 / 分发
        "references/整合包格式规范.md",
        "references/整合包实战拆解.md",
        "references/任务书设计.md",
        "references/FTB_Quests任务编辑.md",
        "references/优秀模组清单-按版本.md",
        "references/空岛与单方块开局.md",
        "references/基础模组清单_1.20.1.json",
        "references/基础模组清单_26.2.json",
        "scripts/下载模组.py",
        "scripts/制作整合包.py",
        "scripts/整合包体检.py",
        "scripts/整合包验证.py",
        "scripts/模组依赖反查.py",
        "scripts/模组版本谱系.py",
        "scripts/模组汉化.py",
        "scripts/模组汉化词库.json",
        "scripts/增量重打包.py",
        "scripts/jar差异比对.py",
        "scripts/json5语法自检.py",
        "scripts/扫必填字段.py",
        # 任务书工具链
        "scripts/任务书ID体检.py",
        "scripts/任务书校验.py",
        "scripts/任务书章节比对.py",
        "scripts/任务书覆盖度.py",
        "scripts/任务书质量自查.py",
        "scripts/优秀模组清单.py",
        # 玩家皮肤
        "references/皮肤制作.md",
        "scripts/皮肤生成.py",
        "scripts/皮肤换色.py",
        "scripts/皮肤校验.py",
        "scripts/皮肤规范.py",
        "scripts/皮肤规范_自检.py",
        "scripts/皮肤预览.py",
    ],
    SKILL_B: [],
}

# 目录扫描时忽略的东西（不进「未归类」报告）
IGNORE_PATTERNS = [
    "__pycache__",
    ".pyc",
    ".bak",
    "SKILL.md",          # 单独走标记区检查
    ".git",
]

# 资产容器目录（扫根目录时别把它们自己当「未归类文件」报出来）
CONTAINER_DIRS = {"references", "scripts"}

# SKILL.md 内「同步区」标记（两边逐字一致的那一段）
MARK_BEGIN = "<!-- CROSS-SYNC:BEGIN"
MARK_END = "<!-- CROSS-SYNC:END"


# ══════════════════════════════════════════════════════════════════════
# ② 基础工具
# ══════════════════════════════════════════════════════════════════════

def read_norm(p: Path):
    """读文本并归一化行尾（CRLF / CR → LF）。行尾不一致不该算「内容漂移」。"""
    for enc in ("utf-8", "utf-8-sig", "gbk"):
        try:
            return p.read_text(encoding=enc).replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError:
            continue
    return p.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


def body_lines(text: str):
    return [l for l in text.split("\n") if l.strip()]


def md5_of(p: Path) -> str:
    h = hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def rel(base: Path, name: str) -> Path:
    """资产名 → 绝对路径（资产名可能带 references/ 前缀，也可能不带）。"""
    return base / name


def find_in(base: Path, name: str):
    """按清单里的名字找到文件（先按原样，再在 references/ 与 scripts/ 里找）。"""
    cands = [base / name, base / "references" / name, base / "scripts" / name]
    for c in cands:
        if c.exists():
            return c
    return None


class Report:
    def __init__(self):
        self.ok = []
        self.missing = []      # (资产, 有的一方, 缺的一方)
        self.drift = []        # (资产, 状态, 说明)
        self.fork = []         # (资产, 说明)
        self.stale = []        # 清单里的资产两边都没有
        self.unclassified = []  # 未归类
        self.fixed = []
        self.refused = []

    @property
    def problems(self):
        return self.missing + self.drift + self.fork + self.stale


# ══════════════════════════════════════════════════════════════════════
# ③ 单文件比对
# ══════════════════════════════════════════════════════════════════════

def copy_asset(src, dst):
    """把一个资产（**文件或目录**）复制到 dst。

    ⚠️ 别退回 shutil.copy2 —— 它只能复制文件，遇到目录型资产（templates/）
    会抛 PermissionError: [Errno 13]。2026-10-04 实测踩到：`--take A` 同步
    templates/ 时直接崩，而且报错长得像「权限不足 / 文件被占用」，极易误判。
    """
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if Path(src).is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dst)


def compare_text(pa: Path, pb: Path):
    """返回 (状态, 说明)。

    状态：
      same           —— 内容一致
      superset_a     —— A ⊇ B 且 A 更长（A 是新版，可安全用 A 覆盖 B）
      superset_b     —— B ⊇ A（B 是新版）
      fork           —— 互不包含（人工合并）
    """
    ta, tb = read_norm(pa), read_norm(pb)
    if ta == tb:
        return "same", ""
    la, lb = body_lines(ta), body_lines(tb)
    sa, sb = set(la), set(lb)
    na, nb = len(ta.split("\n")), len(tb.split("\n"))
    a_has_b = sb.issubset(sa)
    b_has_a = sa.issubset(sb)
    if a_has_b and na > nb:
        return "superset_a", f"一站式 是新版（{nb} → {na} 行，+{na - nb}）"
    if b_has_a and nb > na:
        return "superset_b", f"模组工坊 是新版（{na} → {nb} 行，+{nb - na}）"
    if a_has_b and b_has_a:
        # 行集合相同但文本不同（行序/重复行差异）—— 按行数多的当新版
        if na >= nb:
            return "superset_a", f"行集合相同、文本有差异，取行数多的一侧（一站式 {na} 行）"
        return "superset_b", f"行集合相同、文本有差异，取行数多的一侧（模组工坊 {nb} 行）"
    return "fork", (f"两边各有独有内容（一站式独有 {len(sa - sb)} 行 / "
                    f"模组工坊独有 {len(sb - sa)} 行）—— 必须人工合并，不自动覆盖")


def compare_tree(pa: Path, pb: Path):
    """目录 / 单文件组的比对。返回 (状态, 说明)。"""
    if pa.is_file() and pb.is_file():
        if md5_of(pa) == md5_of(pb):
            return "same", ""
        return "fork", f"二进制/整块内容不同（{pa.stat().st_size} B vs {pb.stat().st_size} B）"

    def snap(root: Path):
        out = {}
        for p in sorted(root.rglob("*")):
            if p.is_file():
                out[p.relative_to(root).as_posix()] = md5_of(p)
        return out

    sa, sb = snap(pa), snap(pb)
    if sa == sb:
        return "same", ""
    only_a = sorted(set(sa) - set(sb))
    only_b = sorted(set(sb) - set(sa))
    diff = sorted(k for k in set(sa) & set(sb) if sa[k] != sb[k])
    det = []
    if only_a:
        det.append(f"仅一站式有 {len(only_a)} 个：{only_a[:5]}")
    if only_b:
        det.append(f"仅模组工坊有 {len(only_b)} 个：{only_b[:5]}")
    if diff:
        det.append(f"{len(diff)} 个同名文件内容不同：{diff[:5]}")
    return "fork", "；".join(det)


# ══════════════════════════════════════════════════════════════════════
# ④ SKILL.md 同步区
# ══════════════════════════════════════════════════════════════════════

def extract_zone(text: str):
    """取出标记区（含标记行）。找不到返回 None。"""
    lines = text.split("\n")
    si = ei = None
    for i, l in enumerate(lines):
        if si is None and l.startswith(MARK_BEGIN):
            si = i
        elif si is not None and l.startswith(MARK_END):
            ei = i
            break
    if si is None or ei is None:
        return None
    return "\n".join(lines[si:ei + 1])


def replace_zone(text: str, new_zone: str):
    lines = text.split("\n")
    si = ei = None
    for i, l in enumerate(lines):
        if si is None and l.startswith(MARK_BEGIN):
            si = i
        elif si is not None and l.startswith(MARK_END):
            ei = i
            break
    if si is None or ei is None:
        return None
    return "\n".join(lines[:si] + new_zone.split("\n") + lines[ei + 1:])


# ══════════════════════════════════════════════════════════════════════
# ⑤ 主流程
# ══════════════════════════════════════════════════════════════════════

def locate_peer(self_dir: Path, peer_opt: str):
    """确定姊妹 skill 目录。"""
    if peer_opt:
        p = Path(peer_opt).expanduser().resolve()
        return p if p.is_dir() else None
    me = self_dir.name
    peer_name = SKILL_B if me == SKILL_A else SKILL_A
    cand = self_dir.parent / peer_name
    return cand if cand.is_dir() else None


def emit(report: Report, a: bool, quiet: bool, title: str, text: str):
    if not text:
        return
    report.ok.append(text)
    if not quiet or a:
        pass


def main():
    ap = argparse.ArgumentParser(
        prog="交叉同步.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="MC 双 skill（MC一站式工坊 ↔ MC模组工坊）mod 公共资产的交叉同步检查与补齐",
        epilog="退出码：0=已对齐 / 1=有差异待处理 / 2=用法或路径错误")
    ap.add_argument("--peer-dir", help="显式指定姊妹 skill 目录（默认取同级同名 skill）")
    ap.add_argument("--sync", action="store_true",
                    help="把「仅一方有」的 mod 资产补到另一方（纯加法，安全）")
    ap.add_argument("--force", action="store_true",
                    help="连「漂移」的也统一（仅当一边是另一边的超集；分叉的拒绝并提示人工合并）")
    ap.add_argument("--take", metavar="A|B",
                    help="人工确认的覆盖方向：A=MC一站式工坊 / B=MC模组工坊。"
                         "把**所有**差异项（含「分叉」）都以它为准覆盖另一方（替换型修改靠这个推）")
    ap.add_argument("--diff", metavar="资产", help="显示某资产的完整差异（给文件名或相对路径）")
    ap.add_argument("--list", action="store_true", help="列出资产清单")
    ap.add_argument("--audit", action="store_true", help="额外报「未归类文件」（清单过期检测）")
    ap.add_argument("--quiet", action="store_true", help="只打印问题")
    ap.add_argument("--md", metavar="路径", help="同时写一份 markdown 报告")
    args = ap.parse_args()

    # --take：人工确认「哪一边是最新版」。归一化成 "A" / "B"。
    take = args.take
    if take:
        t = take.strip().lower()
        if t in ("a", "一站式", "mc一站式工坊"):
            take = "A"
        elif t in ("b", "模组工坊", "mc模组工坊"):
            take = "B"
        else:
            print(f"[用法错误] --take 只接受 A / B（或 一站式 / 模组工坊），收到：{args.take}", file=sys.stderr)
            return 2

    self_dir = Path(__file__).resolve().parent.parent
    me = self_dir.name
    if me not in (SKILL_A, SKILL_B):
        # 允许从别处调用：按目录名推断失败时要求显式指定
        me = SKILL_A if (self_dir / "references" / "皮肤制作.md").exists() else SKILL_B

    peer_dir = locate_peer(self_dir, args.peer_dir)
    peer_name = SKILL_B if me == SKILL_A else SKILL_A

    # 方向：a = 一站式, b = 模组工坊
    A = self_dir if me == SKILL_A else peer_dir
    B = peer_dir if me == SKILL_A else self_dir

    L = []
    L.append("# MC 双 skill 交叉同步检查")
    L.append("")
    L.append(f"- 本 skill：**{me}**（`{self_dir}`）")
    if peer_dir is None:
        L.append(f"- 姊妹 skill：**未发现**（同级目录里没有 `{peer_name}`）")
        L.append("")
        L.append("## 结论：⏭ 单机模式，跳过交叉检查")
        L.append("")
        L.append("> 两个 skill 只有同时存在时才需要交叉对齐。单独分发/单装的情况下本脚本无事可做。")
        out = "\n".join(L)
        print(out)
        if args.md:
            Path(args.md).write_text(out, encoding="utf-8")
        return 0
    L.append(f"- 姊妹 skill：**{peer_name}**（`{peer_dir}`）")
    L.append("")

    rep = Report()

    # ---------- 清单模式 ----------
    if args.list:
        L.append("## mod 公共文档（两边必须一致）")
        L += [f"- {x}" for x in MOD_REFERENCES]
        L.append("")
        L.append("## mod 公共脚本（两边必须一致）")
        L += [f"- {x}" for x in MOD_SCRIPTS]
        L.append("")
        L.append("## 公共目录 / 杂项")
        L += [f"- {x}" for x in MOD_TREES + MOD_ARCHIVES + MOD_MISC]
        L.append("")
        L.append("## 各自专属（不往对方搬）")
        for k, v in EXCLUSIVE.items():
            L.append(f"### {k}（{len(v)} 项）")
            L += [f"- {x}" for x in v]
            L.append("")
        print("\n".join(L))
        return 0

    # ---------- 逐资产检查 ----------
    assets = ([("references", n) for n in MOD_REFERENCES]
              + [("scripts", n) for n in MOD_SCRIPTS]
              + [("", n) for n in MOD_TREES + MOD_MISC]
              + [("", Path(n).name) for n in MOD_ARCHIVES])

    if args.diff:
        tgt = args.diff.replace("\\", "/")
        pa, pb = find_in(A, tgt), find_in(B, tgt)
        if not pa or not pb:
            print(f"[!] 找不到资产：{tgt}（一站式有={bool(pa)} 模组工坊有={bool(pb)}）", file=sys.stderr)
            return 2
        if pa.is_file() and pb.is_file() and not tgt.endswith(("/",)) and pa.is_file():
            import difflib
            d = difflib.unified_diff(read_norm(pa).split("\n"), read_norm(pb).split("\n"),
                                     fromfile=f"一站式/{tgt}", tofile=f"模组工坊/{tgt}", lineterm="")
            print("\n".join(d) or "（无差异）")
        else:
            st, det = compare_tree(pa, pb)
            print(f"{tgt}: {st} —— {det}")
        return 0

    for kind, name in assets:
        pa = find_in(A, name)
        pb = find_in(B, name)
        if pa is None and pb is None:
            rep.stale.append(name)
            continue
        if pa is None:
            rep.missing.append((name, "模组工坊", "一站式"))
            if args.sync or take:
                dst = (A / kind / name) if kind else (A / name)
                dst.parent.mkdir(parents=True, exist_ok=True)
                if pb.is_dir():
                    shutil.copytree(pb, dst, dirs_exist_ok=True)
                else:
                    copy_asset(pb, dst)
                rep.fixed.append(f"{name}：模组工坊 → 一站式")
            continue
        if pb is None:
            rep.missing.append((name, "一站式", "模组工坊"))
            if args.sync or take:
                dst = (B / kind / name) if kind else (B / name)
                dst.parent.mkdir(parents=True, exist_ok=True)
                if pa.is_dir():
                    shutil.copytree(pa, dst, dirs_exist_ok=True)
                else:
                    copy_asset(pa, dst)
                rep.fixed.append(f"{name}：一站式 → 模组工坊")
            continue

        if pa.is_dir() or pb.is_dir():
            st, det = compare_tree(pa, pb)
        else:
            st, det = compare_text(pa, pb)

        if st == "same":
            rep.ok.append(name)
        elif st == "fork":
            if take:
                src, dst, who = (pa, pb, "一站式") if take == "A" else (pb, pa, "模组工坊")
                copy_asset(src, dst)
                rep.fixed.append(f"{name}：{who} → 另一方（--take 人工确认覆盖）")
            else:
                rep.fork.append((name, det))
                if args.force:
                    rep.refused.append(name)
        else:
            src, dst = (pa, pb) if st == "superset_a" else (pb, pa)
            who = "一站式" if st == "superset_a" else "模组工坊"
            rep.drift.append((name, f"{who} 是新版", det))
            if args.force:
                copy_asset(src, dst)
                rep.fixed.append(f"{name}：{who} → {'模组工坊' if st == 'superset_a' else '一站式'}（覆盖）")

    # ---------- SKILL.md 同步区 ----------
    za = extract_zone(read_norm(A / "SKILL.md")) if (A / "SKILL.md").exists() else None
    zb = extract_zone(read_norm(B / "SKILL.md")) if (B / "SKILL.md").exists() else None
    zone_state = None
    if za is None or zb is None:
        zone_state = "absent"
    elif za == zb:
        zone_state = "same"
    else:
        zone_state = "drift"
        if take:
            src_dir, dst_dir, who = (A, B, "一站式") if take == "A" else (B, A, "模组工坊")
            src_zone = za if take == "A" else zb
            newb = replace_zone(read_norm(dst_dir / "SKILL.md"), src_zone)
            if newb is not None:
                (dst_dir / "SKILL.md").write_text(newb if newb.endswith("\n") else newb + "\n",
                                                 encoding="utf-8", newline="\n")
                rep.fixed.append(f"SKILL.md 同步区：{who} → 另一方（--take 人工确认覆盖）")
        elif args.force:
            # 以标记区更长的一方为准（通常是有新增规则的那一方）
            src_dir, dst_dir, who = (A, B, "一站式") if len(za) >= len(zb) else (B, A, "模组工坊")
            newb = replace_zone(read_norm(dst_dir / "SKILL.md"), za if who == "一站式" else zb)
            if newb is not None:
                (dst_dir / "SKILL.md").write_text(newb if newb.endswith("\n") else newb + "\n",
                                                 encoding="utf-8", newline="\n")
                rep.fixed.append(f"SKILL.md 同步区：{who} → 另一方")

    # ---------- 未归类审计 ----------
    if args.audit:
        known = (set(MOD_REFERENCES) | set(MOD_SCRIPTS) | set(MOD_TREES) | set(MOD_MISC)
                 | {Path(n).name for n in MOD_ARCHIVES})
        for side in EXCLUSIVE.values():
            known |= {Path(x).name for x in side}
        for root, label in ((A, "一站式"), (B, "模组工坊")):
            if not root.exists():
                continue
            for sub in ("references", "scripts", ""):
                d = root / sub if sub else root
                if not d.is_dir():
                    continue
                for p in sorted(d.iterdir()):
                    n = p.name
                    if p.is_dir():
                        if (n in CONTAINER_DIRS or n in {Path(t).name for t in MOD_TREES}
                                or n in IGNORE_PATTERNS):
                            continue
                        if n not in known and n not in IGNORE_PATTERNS:
                            rep.unclassified.append(f"{label}: {sub + '/' if sub else ''}{n}（目录）")
                        continue
                    if any(pat in n for pat in IGNORE_PATTERNS):
                        continue
                    base = n
                    if base in known:
                        continue
                    rep.unclassified.append(f"{label}: {sub + '/' if sub else ''}{n}")

    # ---------- 输出 ----------
    def sec(title, rows, empty="✓ 无"):
        L.append(f"## {title}")
        L.append("")
        if rows:
            L.extend(rows)
        else:
            L.append(empty)
        L.append("")

    total = len(assets)
    L.append(f"- 检查资产：**{total}** 项（{len(MOD_REFERENCES)} 文档 + {len(MOD_SCRIPTS)} 脚本 "
             f"+ {len(MOD_TREES) + len(MOD_MISC)} 目录/杂项）")
    L.append(f"- 一致：**{len(rep.ok)}** 项")
    L.append("")

    if rep.ok and not args.quiet:
        sec("✅ 一致", [f"- {x}" for x in rep.ok])

    if rep.stale:
        sec("⚠ 清单陈旧（两边都找不到，资产可能被删或改名）",
            [f"- {x} → 请更新 `交叉同步.py` 里的 MOD_REFERENCES / MOD_SCRIPTS" for x in rep.stale])

    if rep.missing:
        rows = [f"- **{n}** ：只有 {has_} 有，**{lack} 缺**" for n, has_, lack in rep.missing]
        sec("⚠ 单边独有（mod 公共资产缺了一边）", rows)

    if rep.drift:
        sec("⚠ 内容漂移（两边都有，但内容不一致）",
            [f"- **{n}**：{why} —— {det}" for n, why, det in rep.drift])

    if rep.fork:
        sec("🔴 分叉（各有独有内容，必须人工合并 —— 自动覆盖会丢东西）",
            [f"- **{n}**：{det}" for n, det in rep.fork])

    if rep.unclassified:
        sec("📋 未归类文件（清单过期，请在 `交叉同步.py` 里归类：公共 or 专属）",
            [f"- {x}" for x in rep.unclassified])

    if zone_state == "absent":
        sec("⚠ SKILL.md 同步区", ["- 至少一方缺 `CROSS-SYNC:BEGIN/END` 标记区（守则无法同步）"])
    elif zone_state == "same":
        sec("✅ SKILL.md 同步区", [])
    else:
        sec("⚠ SKILL.md 同步区", ["- 两边的「双 skill 交叉补全守则」不一致"])

    # ---------- 结论 ----------
    L.append("## 结论")
    L.append("")
    if rep.fixed:
        L.append(f"### 已执行 {len(rep.fixed)} 项同步")
        L += [f"- {x}" for x in rep.fixed]
        L.append("")
    if rep.refused:
        L.append(f"### ⏭ {len(rep.refused)} 项分叉被拒绝自动合并（需人工）")
        L += [f"- {x}" for x in rep.refused]
        L.append("")

    left = len(rep.problems) + (1 if zone_state != "same" else 0)
    if left == 0:
        L.append("✅ **两边 mod 公共资产已对齐。**")
        L.append("")
        L.append("> 提醒：本脚本只比 `mod 公共资产`。整合包 / 任务书 / 皮肤等专属资产各管各的，不互搬。")
    else:
        L.append(f"⚠ **还有 {left} 项待处理。**")
        L.append("")
        if rep.missing or rep.drift or rep.fork:
            L.append("```")
            L.append("python 交叉同步.py --sync           # 先补「单边独有」（安全）")
            L.append("python 交叉同步.py --sync --force   # 再把「漂移」按超集方向统一")
            L.append("python 交叉同步.py --diff 代码片段.md  # 分叉的看差异，确认真源")
            L.append("python 交叉同步.py --take A         # 确认「一站式是最新版」后一键覆盖（替换型修改走这条）")
            L.append("```")
    L.append("")
    L.append("---")
    L.append("")
    L.append("**规矩**：任何一边升级 mod 相关内容后，顺手跑一次本脚本把对方补齐。"
             "判据是这里全绿，不是「我记得同步过了」。")
    L.append("")

    out = "\n".join(L)
    print(out)
    if args.md:
        Path(args.md).write_text(out, encoding="utf-8")
        print(f"[√] 报告已写入 {args.md}")
    return 0 if left == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跨agent同步.py —— 把本 skill 的更新同步到「同机其他 agent 目录」里的同名副本。

背景
----
本机可能同时装着多个 agent（WorkBuddy / CodeBuddy / ...），每个 agent 有各自的
skills 目录。同一个 skill 被复制进多个目录后，只在常用的那一个里编辑，其余副本
会越来越旧 —— 表现是「同一个 skill，换个 agent 用行为不一样」，排查成本全在
「差异藏在哪」上。

实测（2026-10-09）：`.workbuddy/skills/` 的 MC 双 skill 与 `.codebuddy/skills/`
的同名副本并存，此前靠手工复制保持同步，漏没漏全凭记性。所以固化成本脚本。

与 交叉同步.py 的分工
---------------------
  · 交叉同步.py   管「同一个 skills 目录里，两个 skill 之间」的 mod 公共资产对齐
                  （同一母版的两份知识，谁改了推给对方）
  · 本脚本        管「同一个 skill，在不同 agent 的 skills 目录之间」的副本对齐
                  （一个母版的多份拷贝，以唯一编辑点为准推出去）

规矩
----
- **唯一编辑点在主目录**（哪个目录跑本脚本，哪个就是源）。副本侧一律只读不编辑。
- 默认**纯加法 + 覆盖**：新增 / 更新推给副本；副本独有的文件（例如
  `_skillhub_meta.json` 这类该 agent 自己的安装元数据）**保留**并报告 —— 不碰
  目标环境自己的东西。
- `--mirror` 才会删除副本里源没有的文件（危险，先看报告确认再用）。
- 二进制原样复制（copy2，保留时间戳），不做行尾 / 编码转换 —— 源侧怎么规范，
  副本就怎么拿到。

用法
----
    python 跨agent同步.py                    # 只检查（默认），列出各副本差异
    python 跨agent同步.py --sync             # 把新增/更新推到各副本（纯加法，安全）
    python 跨agent同步.py --sync --mirror    # 完全镜像（含删除副本多余文件，谨慎）
    python 跨agent同步.py --list             # 列本机发现的所有 agent skills 目录
    python 跨agent同步.py --root D:/x/skills # 额外扫描目录（可重复；补 home 扫不到的）
    python 跨agent同步.py --md 报告.md       # 同时写一份 markdown 报告

退出码：0=所有副本已一致 / 1=有差异（或同步后仍有差异）/ 2=用法或路径错误
"""
import argparse
import hashlib
import shutil
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HOME = Path.home()

# 不参与同步的东西（各 agent 环境自己的缓存 / 临时物）
EXCLUDE_DIRS = {"__pycache__", ".git", ".idea", ".vscode", ".pytest_cache", ".mypy_cache"}
EXCLUDE_SUFFIX = {".pyc", ".pyo", ".tmp", ".swp"}
EXCLUDE_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}


def excluded(rel: Path) -> bool:
    if any(part in EXCLUDE_DIRS for part in rel.parts):
        return True
    if rel.name in EXCLUDE_NAMES:
        return True
    if rel.suffix.lower() in EXCLUDE_SUFFIX:
        return True
    if rel.name.startswith("~$") or rel.name.startswith("."):
        # 点开头的一律不动：`.packignore` 例外（它是 skill 自己的资产）
        return rel.name != ".packignore"
    return False


def md5_of(p: Path) -> str:
    h = hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot(root: Path):
    """{相对路径: md5}，已剔除不参与同步的文件。"""
    files = {}
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if excluded(rel):
            continue
        files[rel] = md5_of(p)
    return files


def discover_roots(extra):
    """本机所有「agent skills 根目录」。
    做法：扫 home 一层，凡是有 skills/ 子目录的（含隐藏目录）都算一个 agent 根。
    再加上 --root 显式给的。单机装一个 agent 时结果就一个。"""
    roots = []
    seen = set()
    try:
        for d in sorted(HOME.iterdir()):
            if not d.is_dir():
                continue
            sk = d / "skills"
            if sk.is_dir():
                key = str(sk.resolve()).lower()
                if key not in seen:
                    seen.add(key)
                    roots.append(sk)
    except OSError:
        pass
    for x in extra:
        p = Path(x).expanduser()
        try:
            p = p.resolve()
        except OSError:
            continue
        if p.is_dir():
            key = str(p).lower()
            if key not in seen:
                seen.add(key)
                roots.append(p)
    return roots


def compare(src, dst):
    added = sorted(set(src) - set(dst))          # 源有 · 副本缺
    unique = sorted(set(dst) - set(src))         # 副本独有（默认保留）
    updated = sorted(r for r in (set(src) & set(dst)) if src[r] != dst[r])
    same = len(set(src) & set(dst)) - len(updated)
    return added, updated, unique, same


def copy_one(src_root: Path, dst_root: Path, rel: Path):
    s, d = src_root / rel, dst_root / rel
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(s, d)


def sync_dirs(src_root: Path, dst_root: Path):
    """把源里的目录结构（含空目录）在副本侧补齐。"""
    for p in src_root.rglob("*"):
        if p.is_dir():
            rel = p.relative_to(src_root)
            if any(part in EXCLUDE_DIRS for part in rel.parts):
                continue
            (dst_root / rel).mkdir(parents=True, exist_ok=True)


def main():
    ap = argparse.ArgumentParser(
        prog="跨agent同步.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="把本 skill 的更新同步到同机其他 agent 目录里的同名副本"
                    "（默认只检查；--sync 推送新增/更新；副本独有文件默认保留）",
        epilog="退出码：0=所有副本已一致 / 1=有差异 / 2=用法或路径错误")
    ap.add_argument("--sync", action="store_true", help="执行同步（把新增/更新推到副本）")
    ap.add_argument("--mirror", action="store_true",
                    help="配合 --sync：完全镜像（删除副本里源没有的文件；谨慎，先看检查报告）")
    ap.add_argument("--list", action="store_true", help="列出本机发现的所有 agent skills 目录")
    ap.add_argument("--root", action="append", default=[], metavar="目录",
                    help="额外扫描的 skills 根目录（可重复；用于 home 扫不到的位置）")
    ap.add_argument("--src", metavar="目录", help="显式指定源 skill 目录（默认=本脚本所在的 skill）")
    ap.add_argument("--quiet", action="store_true", help="只打印问题与结果")
    ap.add_argument("--md", metavar="路径", help="同时写一份 markdown 报告")
    args = ap.parse_args()

    if args.mirror and not args.sync:
        print("[用法错误] --mirror 只配合 --sync 使用（先跑无参数的检查模式看报告）", file=sys.stderr)
        return 2

    self_dir = (Path(args.src).expanduser().resolve() if args.src
                else Path(__file__).resolve().parent.parent)
    if not (self_dir / "SKILL.md").exists():
        print(f"[路径错误] 源目录里没有 SKILL.md：{self_dir}", file=sys.stderr)
        return 2
    me = self_dir.name
    self_root = self_dir.parent

    roots = discover_roots(args.root)
    others = [r for r in roots if str(r.resolve()).lower() != str(self_root.resolve()).lower()]
    copies = [(r, r / me) for r in others]
    found = [(r, t) for r, t in copies if t.is_dir()]

    L = []
    L.append(f"# {me} · 跨 Agent 副本同步检查")
    L.append("")
    L.append(f"- 源（本 skill）：`{self_dir}`")
    L.append(f"- 扫描到 agent skills 目录 {len(others)} 个，其中同名副本 {len(found)} 个")
    L.append("")

    # ---------- --list ----------
    if args.list:
        L.append("## 本机 agent skills 目录")
        L.append("")
        L.append(f"- `{self_root}` ← 源所在（当前 skill：{me}）")
        for r in others:
            has = "✓ 有同名副本" if (r / me).is_dir() else "– 无同名副本"
            L.append(f"- `{r}`　{has}")
        L.append("")
        L.append("> 副本发现规则：home 下凡有 `skills/` 子目录的都算一个 agent 根；"
                 "扫不到的用 `--root` 补。")
        out = "\n".join(L)
        print(out)
        if args.md:
            Path(args.md).write_text(out, encoding="utf-8")
        return 0

    # ---------- 无副本：单机模式 ----------
    if not found:
        L.append("## 结论：⏭ 未发现其他 agent 的同名副本，无需同步")
        L.append("")
        if others:
            L.append("> 扫到的 skills 目录里都没有同名副本：")
            L += [f"> - `{r}`" for r in others]
        else:
            L.append("> （也没扫到别的 skills 目录 —— 本机只有当前这一个 agent 装了本 skill）")
        out = "\n".join(L)
        print(out)
        if args.md:
            Path(args.md).write_text(out, encoding="utf-8")
        return 0

    src = snapshot(self_dir)
    report = []
    total_pending = 0   # 待处理项数（镜像模式把「副本独有」也算上）
    plan = []           # [(dst_dir, added, updated, unique, same)]

    for i, (root, dst_dir) in enumerate(found, 1):
        dst = snapshot(dst_dir)
        added, updated, unique, same = compare(src, dst)
        pending = len(added) + len(updated) + (len(unique) if args.mirror else 0)
        total_pending += pending
        plan.append((dst_dir, added, updated, unique, same))
        block = []
        block.append(f"## 副本 {i}/{len(found)}：`{dst_dir}`")
        if pending == 0:
            block.append(f"  ✓ 已一致（{same} 个文件相同"
                         + (f"，副本独有 {len(unique)} 个未动" if unique else "") + "）")
        else:
            extra = (f" / 副本独有 {len(unique)}"
                     + ("（--mirror 将删）" if args.mirror else "（保留）")) if unique else ""
            block.append(f"  ⚠ 有差异（新增 {len(added)} / 更新 {len(updated)}"
                         f" / 相同 {same}{extra}）")
            if added:
                block.append("  · 新增（源有 · 副本缺）：")
                block += [f"    + {r}" for r in added[:20]]
                if len(added) > 20:
                    block.append(f"    … 另有 {len(added) - 20} 个")
            if updated:
                block.append("  · 更新（两边不同 · 以源为准）：")
                block += [f"    ~ {r}" for r in updated[:20]]
                if len(updated) > 20:
                    block.append(f"    … 另有 {len(updated) - 20} 个")
        if unique:
            tip = "镜像将删" if args.mirror else "保留，不动"
            block.append(f"  · 副本独有（{tip}）：")
            block += [f"    = {r}" for r in unique[:10]]
            if len(unique) > 10:
                block.append(f"    … 另有 {len(unique) - 10} 个")
        block.append("")
        report += block

    L += report

    # ---------- 同步动作 ----------
    if args.sync and total_pending:
        L.append("## 同步动作")
        L.append("")
        fail = 0
        for dst_dir, added, updated, unique, same in plan:
            for rel in added + updated:
                tag = "新增" if rel in added else "更新"
                src_p = self_dir / rel
                try:
                    copy_one(self_dir, dst_dir, rel)
                    L.append(f"  [{tag}] {rel}  →  `{dst_dir}`")
                except OSError as e:
                    fail += 1
                    L.append(f"  [失败] {rel}：{e}")
            try:
                sync_dirs(self_dir, dst_dir)
            except OSError as e:
                fail += 1
                L.append(f"  [失败] 目录结构：{e}")
            # mirror：删除副本里源没有的文件（副本独有）
            if args.mirror and unique:
                for rel in unique:
                    try:
                        (dst_dir / rel).unlink()
                        L.append(f"  [镜像删] {rel}  ←  `{dst_dir}`")
                    except OSError as e:
                        fail += 1
                        L.append(f"  [失败] 删除 {rel}：{e}")
        if fail:
            L.append(f"  ⚠ 有 {fail} 项失败（多为文件被占用，关掉占用的程序重跑）")
        L.append("")

        # 复核
        L.append("## 复核（同步后重新比对）")
        L.append("")
        remain = 0
        for dst_dir, *_ in plan:
            dst = snapshot(dst_dir)
            added, updated, unique, same = compare(src, dst)
            remain += len(added) + len(updated) + (len(unique) if args.mirror else 0)
            if args.mirror:
                state = "✓ 完全一致" if not (added or updated or unique) else \
                        f"⚠ 仍有差异（新增 {len(added)} / 更新 {len(updated)} / 独有 {len(unique)}）"
            else:
                state = "✓ 已同步（副本独有文件按设计保留）" if not (added or updated) else \
                        f"⚠ 仍有差异（新增 {len(added)} / 更新 {len(updated)}）"
            L.append(f"- `{dst_dir}`：{state}")
        L.append("")
        if remain or fail:
            L.append("## 结论：⚠ 未完全同步 —— 看上面失败项 / 差异项，处理后重跑")
            code = 1
        else:
            L.append("## 结论：✓ 全部同步完成")
            code = 0
    else:
        if total_pending:
            L.append("## 结论：⚠ 存在差异，待同步")
            L.append("")
            L.append("> 跑 `python scripts/跨agent同步.py --sync` 补齐"
                     "（纯加法：新增/更新推过去，副本独有文件保留）")
            code = 1
        else:
            L.append("## 结论：✓ 所有副本已与源一致")
            code = 0

    out = "\n".join(L)
    if args.quiet:
        keep = [ln for ln in L if ("差异" in ln or "结论" in ln or "失败" in ln)]
        out = "\n".join(keep) if keep else f"{me}：所有副本已一致"
    print(out)
    if args.md:
        Path(args.md).write_text("\n".join(L), encoding="utf-8")
    return code


if __name__ == "__main__":
    sys.exit(main())

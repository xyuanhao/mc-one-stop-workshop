#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""增量重打包 —— 只替换 zip 里变了的条目，其余条目**字节不动**。

【为什么需要这个】
整合包 zip 动辄 700 MB+，其中 99% 是 mods/*.jar（从不改）。修完任务书/配置
几个小文件后，如果整包重打：
  ① 慢 —— 要重新压缩全部 150+ 个 jar；
  ② **有风险** —— 重压缩会让「本来没动的 jar」字节也变了，
      无法再用 md5 向玩家证明"我只改了任务书"；
  ③ 无法自查 —— 打完你不知道到底动了哪些条目。

本脚本逐条从源 zip `read()` → 原样 `writestr()`（保留 compress_type /
external_attr / date_time），只把指定条目换成新内容 → **未替换的条目字节完全一致**，
可事后逐条 md5 证明。

【用法】
    # ① 手动指定要替换的条目（相对 overrides/ 之后，也是相对 --from 的路径）
    python 增量重打包.py 万象物语.zip -o 新包.zip --from <实例目录> \
        config/ftbquests/quests/chapters/10_life.snbt ...

    # ② 自动比对：扫描 --from 下所有能对上 zip 内 overrides/ 的文件，只替换有差异的
    python 增量重打包.py 万象物语.zip -o 新包.zip --from <实例目录> --auto

    # ③ 只看会改什么，不写文件
    python 增量重打包.py 万象物语.zip -o 新包.zip --from <实例> --auto --dry-run

    # ④ 校验 zip 内的内容与磁盘一致（交付前终检）
    python 增量重打包.py 万象物语.zip --from <实例> --verify

【设计要点】
  * `--auto` 只比对 zip 里**已经存在**的 `overrides/**` 条目 —— **不会新增条目**。
    想加新文件请走 `制作整合包.py` 全量打包（新文件往往还牵涉 manifest 声明）。
  * `--verify` 报「不一致 / 磁盘缺失」两类，退出码 1；全一致退出码 0。
  * 替换数 = 0 时也会正常出包（等价于复制），并明确告诉你「没有条目需要替换」，
    避免"以为改了其实没改"。

【退出码】0 = 成功 / 干净 ；1 = 发现问题（源 zip 缺目标条目、校验不一致）；2 = 参数或路径问题
"""
import argparse
import hashlib
import sys
import zipfile
from pathlib import Path

OVR = "overrides/"


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def collect_auto(zin: zipfile.ZipFile, root: Path) -> tuple[dict[str, bytes], list[str]]:
    """扫描 zip 内 overrides/** 条目 → 与 root 下对应文件比对，返回 (需替换, 磁盘缺失)。"""
    repl: dict[str, bytes] = {}
    missing: list[str] = []
    for name in zin.namelist():
        if not name.startswith(OVR) or name.endswith("/"):
            continue
        rel = name[len(OVR):]
        disk = root / rel
        if not disk.is_file():
            missing.append(rel)
            continue
        data = disk.read_bytes()
        if md5(data) != md5(zin.read(name)):
            repl[name] = data
    return repl, missing


def main() -> int:
    ap = argparse.ArgumentParser(
        description="增量重打包：只替换 zip 里变了的条目，其余条目字节不动",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="例：python 增量重打包.py 包.zip -o 新.zip --from <实例> --auto")
    ap.add_argument("zip", help="源 zip（如 万象物语_1_20_1-curseforge-fixed.zip）")
    ap.add_argument("-o", "--out", help="输出 zip 路径（--verify 时不需要）")
    ap.add_argument("--from", dest="root", required=True,
                    help="新内容根目录（对应 zip 内 overrides/ 的那一层，通常直接给实例目录）")
    ap.add_argument("paths", nargs="*",
                    help="要替换的条目（相对 overrides/ 的路径），留空需配 --auto")
    ap.add_argument("--auto", action="store_true",
                    help="自动比对 zip 内 overrides/** 与磁盘，只替换有差异的")
    ap.add_argument("--dry-run", action="store_true", help="只报告，不写文件")
    ap.add_argument("--verify", action="store_true", help="只校验 zip 与磁盘是否一致")
    a = ap.parse_args()

    src = Path(a.zip)
    root = Path(a.root)
    if not src.is_file():
        print(f"✗ 源 zip 不存在：{src}", file=sys.stderr)
        return 2
    if not root.is_dir():
        print(f"✗ --from 目录不存在：{root}", file=sys.stderr)
        return 2
    if not a.verify and not a.out:
        print("✗ 非 --verify 模式必须给 -o/--out", file=sys.stderr)
        return 2
    if not a.verify and not a.auto and not a.paths:
        print("✗ 请给要替换的条目路径，或用 --auto", file=sys.stderr)
        return 2

    zin = zipfile.ZipFile(src)
    names = set(zin.namelist())

    # ---------------- verify ----------------
    if a.verify:
        same = diff = miss = 0
        for name in sorted(n for n in names if n.startswith(OVR)):
            rel = name[len(OVR):]
            disk = root / rel
            if not disk.is_file():
                print(f"  ✗ 磁盘缺失  {rel}")
                miss += 1
            elif md5(zin.read(name)) != md5(disk.read_bytes()):
                print(f"  ✗ 内容不一致 {rel}")
                diff += 1
            else:
                same += 1
        total = same + diff + miss
        print(f"\n校验 overrides 条目 {total} 个：一致 {same} / 不一致 {diff} / 磁盘缺失 {miss}")
        zin.close()
        return 1 if (diff or miss) else 0

    # ---------------- 决定替换集 ----------------
    if a.auto:
        repl, missing = collect_auto(zin, root)
        if missing:
            print(f"  ⚠ 磁盘上找不到 {len(missing)} 个 zip 内已有的 overrides 条目"
                  f"（不会删除 zip 里的，仅提示）")
            for m in missing[:10]:
                print(f"      - {m}")
            if len(missing) > 10:
                print(f"      …… 还有 {len(missing) - 10} 个")
    else:
        repl = {}
        for rel in a.paths:
            rel = rel.replace("\\", "/").lstrip("/")
            name = OVR + rel
            disk = root / rel
            if not disk.is_file():
                print(f"✗ 磁盘上找不到 {disk}", file=sys.stderr)
                zin.close()
                return 2
            if name not in names:
                print(f"✗ 源 zip 里没有条目 {name}", file=sys.stderr)
                print("   → 新增文件请走 制作整合包.py 全量打包", file=sys.stderr)
                zin.close()
                return 1
            repl[name] = disk.read_bytes()

    print(f"源条目 {len(names)}   需替换 {len(repl)}")
    for n in sorted(repl):
        print(f"  ~ {n[len(OVR):]}")

    if a.dry_run:
        print("\n--dry-run：未写文件")
        zin.close()
        return 0
    if not repl:
        print("  ℹ 没有条目需要替换（内容已是最新）")

    # ---------------- 逐条写出 ----------------
    with zipfile.ZipFile(a.out, "w", zipfile.ZIP_DEFLATED) as zout:
        for it in zin.infolist():
            d = repl.get(it.filename)
            if d is None:
                zout.writestr(it, zin.read(it.filename))     # 原样复制 → 字节不动
            else:
                ni = zipfile.ZipInfo(it.filename, date_time=it.date_time)
                ni.compress_type = it.compress_type
                ni.external_attr = it.external_attr
                zout.writestr(ni, d)
    zin.close()

    out = Path(a.out)
    # 立即自查：非替换条目字节是否真的没动
    if repl:
        za, zb = zipfile.ZipFile(src), zipfile.ZipFile(out)
        moved = [n for n in za.namelist()
                 if n not in repl and md5(za.read(n)) != md5(zb.read(n))]
        print(f"\n输出：{out}  ({out.stat().st_size / 1024 / 1024:.1f} MB)")
        print(f"非替换条目被动过：{len(moved)}" + ("  ← 不该发生！" if moved else "  ✓"))
        za.close()
        zb.close()
        if moved:
            return 1
    else:
        print(f"\n输出：{out}  ({out.stat().st_size / 1024 / 1024:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

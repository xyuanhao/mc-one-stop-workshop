#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 FTB Quests / FTB Library 的 class 字节码里扫出 readData 读取的字段，
区分「必填（orElseThrow / 直接 getAsString）」和「可选（ifPresent / orElse）」。

背景：FTB Quests 26.x 的 data.json5 有一批字段是 .orElseThrow() 硬取，
缺一个就整本任务书加载失败（日志只有一句 "No value present"，不带字段名）。
用它一次扫全，别补一个崩一个。

用法:
  python 扫必填字段.py <jar> <类全名> [方法名片段...]
例:
  python 扫必填字段.py instance/mods/ftb-quests-neoforge-26.1.2.8.jar \
      dev.ftb.mods.ftbquests.quest.BaseQuestFile readData
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


def _resolve_javap() -> str:
    """按 JAVAP → JAVA_HOME → PATH 的顺序找 javap，避免写死某台机器的路径。"""
    cand = os.environ.get("JAVAP", "").strip()
    if cand and os.path.exists(cand):
        return cand
    jh = os.environ.get("JAVA_HOME", "").strip()
    if jh:
        for name in ("javap.exe", "javap"):
            p = os.path.join(jh, "bin", name)
            if os.path.exists(p):
                return p
    return shutil.which("javap") or ""


JAVAP = _resolve_javap()


def dump(jar: Path, cls: str) -> list[str]:
    if not JAVAP:
        sys.exit("找不到 javap：设置 JAVA_HOME 或 JAVAP 环境变量后重试")
    with zipfile.ZipFile(jar) as z:
        data = z.read(cls.replace(".", "/") + ".class")
    d = Path(tempfile.mkdtemp())
    (d / "X.class").write_bytes(data)
    p = subprocess.run([JAVAP, "-c", "-l", "-p", "X.class"], cwd=d,
                       capture_output=True)
    # javap 在 Windows 下输出 \r\n，必须剥掉，否则正则的 $ 永远吃不到
    return [l.rstrip() for l in
            (p.stdout + p.stderr).decode("utf-8", "replace").split("\n")]


def main() -> int:
    ap = argparse.ArgumentParser(
        description="从 jar 字节码扫出 readData 字段的「必填 / 可选」",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("用法:")[-1])
    ap.add_argument("jar", help="FTB Quests / FTB Library 的 jar")
    ap.add_argument("cls", help="类全名，如 dev.ftb.mods.ftbquests.quest.BaseQuestFile")
    ap.add_argument("method", nargs="*", default=None,
                    help="方法名片段（默认 readData）")
    ap.add_argument("--javap", default=JAVAP,
                    help="javap 可执行文件路径（默认取本机 Minecraft 运行时里的）")
    args = ap.parse_args()

    jar = Path(args.jar)
    if not jar.is_file():
        print(f"[!] 找不到 jar：{jar}", file=sys.stderr)
        return 2
    if not Path(args.javap).is_file():
        print(f"[!] 找不到 javap：{args.javap}\n"
              f"    用 --javap <路径> 指定，或装了 JDK 后指向其 bin/javap.exe",
              file=sys.stderr)
        return 2
    globals()["JAVAP"] = args.javap
    cls = args.cls
    want = args.method or ["readData"]
    lines = dump(jar, cls)

    starts = [i for i, l in enumerate(lines)
              if l.startswith("  ") and l.rstrip().endswith(";") and ":" not in l]
    if not starts:
        print("[!] 没切出任何方法，javap 输出异常？前 5 行：")
        print("\n".join(lines[:5]))
        return 1

    hit = 0
    for a, b in zip(starts, starts[1:] + [len(lines)]):
        sig = lines[a].strip()
        if not any(w in sig for w in want):
            continue
        hit += 1
        seg = lines[a:b]
        print("\n=== " + sig[:110])
        seen: set[tuple[str, str]] = set()
        n_req = 0
        for i, l in enumerate(seg):
            m = re.search(r"// String (\S+)$", l)
            if not m:
                continue
            key = m.group(1)
            ctx = "\n".join(seg[i:i + 10])
            if "orElseThrow" in ctx:
                tag = "必填"
            elif "getAsString" in ctx:
                tag = "必填(字符串)"
            elif "ifPresent" in ctx:
                tag = "可选"
            elif "orElse" in ctx:
                tag = "可选(默认)"
            else:
                tag = "?"
            mk = re.search(r"Json5Util\.(\w+)", ctx)
            if mk:
                kind = mk.group(1)
            elif "NameMap" in ctx:
                kind = "NameMap"
            elif "getAsString" in ctx:
                kind = "String"
            else:
                kind = "?"
            if (key, tag) in seen:
                continue
            seen.add((key, tag))
            if tag.startswith("必填"):
                n_req += 1
            print(f"  [{tag:11}] {key:36} {kind}")
        print(f"  -> 必填 {n_req} 项")
    if not hit:
        print(f"[!] 没匹配到方法 {want}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

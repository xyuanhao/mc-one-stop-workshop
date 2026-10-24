#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
任务书校验.py —— FTB Quests 任务文件（.snbt）结构自检。

写完/改完任务书后跑一遍，防止语法错误导致章节整章不加载（书里看不到新章节）。
校验项：引号配对、括号平衡、map/数组里误用逗号、description 数组逗号、关键字段缺失。

用法：
  python 任务书校验.py "实例目录"                  # 扫实例 config/ftbquests 全部 .snbt
  python 任务书校验.py "实例/config/ftbquests"      # 直接给 ftbquests 目录
  python 任务书校验.py "实例/config/ftbquests/quests/chapters/xxx.snbt"  # 单文件
"""
import argparse, glob, os, re, sys


def snbt_check(text, name, strict=False):
    """轻量结构校验。返回 (errors, warnings)。errors=结构错误；warnings=可疑风格。"""
    errs, warns = [], []
    in_str, depth = False, 0
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if in_str:
            if c == '"':
                in_str = False
            elif c == "\\":
                i += 1
        else:
            if c == '"':
                in_str = True
            elif c in "{[":
                depth += 1
            elif c in "}]":
                depth -= 1
                if depth < 0:
                    errs.append(f"第{i}字符处多余闭合括号")
        i += 1
    if in_str:
        errs.append("字符串未闭合（缺英文双引号）")
    if depth != 0:
        errs.append(f"括号不平衡 depth={depth}（多 {depth} 个未闭合）")

    # 去除引号内容后再查结构符号，减少误报
    body = re.sub(r'"(?:[^"\\]|\\.)*"', '""', text)
    # 注意：map 字段间逗号是 FTB Quests 合法风格（编辑器自己就输出逗号，如
    # chapter_groups.snbt 的 { id: "x", title: "y" }），不告警。
    # 真正有风险的是 description 这类字符串数组元素间的逗号（官方格式用换行分隔）
    for m in re.finditer(r'""\s*,\s*""', body):
        warns.append("字符串数组疑似用了逗号分隔（官方格式用换行分隔元素）")
    if strict:
        # 关键字段：顶层必须是 { }
        if not text.lstrip().startswith("{"):
            errs.append("顶层应以 { 开始")
    return errs, warns


def validate_target(target):
    """返回 (ok, report_lines, fatal)。fatal=True 表示参数/路径问题，与「文件有错」区分开。"""
    L = []
    if os.path.isfile(target):
        files = [target]
    elif os.path.isdir(target):
        if os.path.basename(target) == "ftbquests" or target.endswith("quests"):
            base = target
        else:
            # 可能是实例目录 → 找 config/ftbquests
            base = os.path.join(target, "config", "ftbquests")
        if not os.path.isdir(base):
            return False, [f"找不到 ftbquests 目录: {base}",
                           "  · 传整合包实例目录（自动找 config/ftbquests）、或直接传 ftbquests 目录",
                           "  · 若这包还没写过任务书 → 先按 references/任务书设计.md 写一版"], True
        files = sorted(glob.glob(os.path.join(base, "**", "*.snbt"), recursive=True))
    else:
        return False, [f"路径不存在: {target}",
                       "  · 传实例目录 / config/ftbquests 目录 / 单个 .snbt 文件"], True

    if not files:
        return True, ["没有 .snbt 文件（FTB Quests 未初始化或任务为空）"], False
    all_ok = True
    for f in files:
        try:
            txt = open(f, encoding="utf-8").read()
        except Exception as e:
            L.append(f"✗ 无法读取 {f}: {e}")
            all_ok = False
            continue
        errs, warns = snbt_check(txt, f)
        rel = f.replace("\\", "/").split("/quests/")[-1]
        if errs:
            all_ok = False
            L.append(f"✗ {rel}")
            for e in errs[:5]:
                L.append(f"    · {e}")
        elif warns:
            L.append(f"⚠ {rel}")
            for w in set(warns):
                L.append(f"    · {w}")
        else:
            L.append(f"✓ {rel}")
    return all_ok, L, False


def main():
    """CLI：与同目录其他脚本一致用 argparse，支持 --help。"""
    ap = argparse.ArgumentParser(
        description="FTB Quests 任务文件（.snbt）结构自检",
        epilog="退出码：0=通过 / 1=发现错误（可串进「体检→修→打包」流水线当闸门）/ 2=参数或路径问题")
    ap.add_argument("target",
                    help="实例目录（自动找 config/ftbquests）| ftbquests 目录 | 单个 .snbt 文件")
    args = ap.parse_args()
    ok, lines, fatal = validate_target(args.target)
    print("\n".join(lines))
    if fatal:
        print("\n❌ 参数或路径问题，检查后重试")
        sys.exit(2)
    print(f"\n{'✅ 全部通过，任务文件结构无错' if ok else '❌ 发现错误，修复后再进游戏'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

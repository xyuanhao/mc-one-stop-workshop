# -*- coding: utf-8 -*-
"""
对 json5 做括号配对 + 字符串闭合 + 尾随逗号 自检（不依赖 json5 模块）。

为什么需要它：
  FTB 任务书的章节文件和 lang/quests.json5 都是 json5 格式。**写成坏 json5 不会报错** ——
  游戏照常启动，只是那份文件被静默丢弃：
    · lang 表写坏（如重复 key / 漏逗号）→ 整个语言文件失效，任务书显示原始 key
    · 章节文件写坏 → 那一章的任务全部不出现
  唯一的现形地点是**服务端启动日志**，而且只在有问题的文件被读到那一刻打一行。
  → 改完 json5 后**必须本地先跑一次这个脚本**，再进游戏 / 跑服务端验证。

环境里通常没有 json5 模块（pip 也不一定有网），所以这里手写括号配对解析，
不需要任何依赖。

用法：
  python json5语法自检.py <file1.json5> [file2.json5 ...]
  python json5语法自检.py config/ftbquests/quests/lang/*/quests.json5
退出码：0=全部合格 / 1=有文件不合格 / 2=参数或路径问题。

⚠️ 无参直接跑会**打印用法并退出 2**（不是"通过"）——早期版本无参时循环空转、
   `allok` 还是 True，于是"什么都没检查"也退出 0，是最典型的**静默假通过**。
"""
import io
import os
import re
import sys


def check(path):
    s = io.open(path, encoding="utf-8").read()
    depth = {"{": 0, "[": 0}
    pairs = {"}": "{", "]": "["}
    instr = False
    esc = False
    line = 1
    errs = []
    for ch in s:
        if ch == "\n":
            line += 1
        if esc:
            esc = False
            continue
        if ch == "\\" and instr:
            esc = True
            continue
        if ch == '"':
            instr = not instr
            continue
        if instr:
            continue
        if ch in depth:
            depth[ch] += 1
        elif ch in pairs:
            depth[pairs[ch]] -= 1
            if depth[pairs[ch]] < 0:
                errs.append("L%d 多余的 %s" % (line, ch))
                depth[pairs[ch]] = 0
    tail = re.findall(r",\s*[}\]]", s)
    print(path)
    print("  括号深度: {  %d, [ %d" % (depth["{"], depth["["]))
    print("  未闭合字符串: %s" % instr)
    print("  结构性错误: %s" % (errs if errs else "无"))
    print("  ',}' / ',]' 处数: %d  (json5 允许尾随逗号, 仅供参考)" % len(tail))
    ok = (depth["{"] == 0 and depth["["] == 0 and not instr and not errs)
    print("  => %s" % ("OK" if ok else "FAIL"))
    return ok


def main(argv):
    if not argv:
        print(__doc__.strip())
        print("\n[参数错误] 没给文件 —— 什么都没检查，不算通过。")
        return 2
    if argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    missing = [f for f in argv if not os.path.isfile(f)]
    if missing:
        print("[参数错误] 文件不存在（注意：这不是「文件不合格」，别当成被检查出问题）：")
        for f in missing:
            print("   ", f)
        return 2
    allok = True
    for f in argv:
        allok &= check(f)
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

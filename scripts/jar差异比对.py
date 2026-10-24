#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
两个 jar 的结构差异比对 —— 回答"这个模组新构建到底改了什么"。

为什么需要它
------------
本地编译的模组（`gradlew build` 出的 `build/libs/*.jar`）每次更新，
数量级往往只是几十 KB 的字节差，但背后可能是「改了机制」也可能是「只修了个文案」。
靠 jar 体积/时间戳完全判断不出来，而这两种情况的**后续动作天差地别**：

  · 只改文案/修 bug → 整合包的任务书**一个字都不用动**，同步 jar 重打包即可；
  · 改了机制（技能等级、材料、数值）→ 任务书里对应的说明**全部变成错的**，
    玩家照着做会走错路，必须逐条核对。

本脚本把差异拆成四类，一眼看出属于哪种：

  1. **新增 / 删除条目** —— 有新 class 出现，通常意味着新功能（如新增一个渲染器）
  2. **大小变化的条目** —— 涨得多的那个 class 就是改动重心
  3. **lang 文案逐条 diff** —— 游戏内提示的变化往往**直接暴露机制改动**
     （例：`上限提升到 %s` → `永久解锁等级上限`，就是"每颗 +10 改成一颗解锁"）
  4. 条目总数 / 文件清单对比

用法：
  python jar差异比对.py <旧.jar> <新.jar>

> ⚠️ 先看 lang 那一段。模组的机制改动**几乎都会先在语言文件里露出马脚** ——
> 比反编译 class 快得多，也比"读体积差"可靠得多。
>
> ⚠️ 但 lang 只是**线索不是结论**。确认机制必须回源码（或反编译）看常量：
> 数改变了没有 / 枚举项改了没有 / 判断分支改了没有。
> 本工作区的惯例：源码在场就直接读源码，那是最准的。
"""
import io
import json
import sys
import zipfile


def entries(path):
    z = zipfile.ZipFile(path)
    return {i.filename: i.file_size for i in z.infolist()}, z


def diff_lang(zo, zn, key):
    """逐条 diff 一个 lang json；返回是否打出了内容。"""
    try:
        do = json.loads(zo.read(key).decode("utf-8", "replace"))
        dn = json.loads(zn.read(key).decode("utf-8", "replace"))
    except Exception as e:
        print("  [%s] 读取/解析失败: %s" % (key, e))
        return True

    changed = [k for k in sorted(set(do) | set(dn)) if do.get(k) != dn.get(k)]
    if not changed:
        return False

    print("  [%s] 旧 %d key / 新 %d key，变化 %d 条" % (key, len(do), len(dn), len(changed)))
    for k in changed:
        print("     ~ %s" % k)
        print("         旧: %s" % do.get(k, "<无>"))
        print("         新: %s" % dn.get(k, "<无>"))
    return True


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2

    old_p, new_p = sys.argv[1], sys.argv[2]
    o, zo = entries(old_p)
    n, zn = entries(new_p)
    so, sn = set(o), set(n)

    added = sorted(sn - so)
    removed = sorted(so - sn)
    changed = sorted(k for k in so & sn if o[k] != n[k])

    print("=" * 68)
    print("旧: %s   条目 %d" % (old_p, len(o)))
    print("新: %s   条目 %d" % (new_p, len(n)))
    print("=" * 68)

    print("\n--- 新增条目 (%d) ---" % len(added))
    for k in added:
        print("  + %-72s %9d" % (k, n[k]))
    if not added:
        print("  (无)")

    print("\n--- 删除条目 (%d) ---" % len(removed))
    for k in removed:
        print("  - %-72s %9d" % (k, o[k]))
    if not removed:
        print("  (无)")

    print("\n--- 大小变化的条目 (%d) ---" % len(changed))
    for k in changed:
        print("  ~ %-72s %9d -> %9d  (%+d)" % (k, o[k], n[k], n[k] - o[k]))
    if not changed:
        print("  (无)")

    print("\n--- lang 文案变化（机制改动的第一线索）---")
    hit = False
    for k in sorted(so & sn):
        if k.endswith(".json") and "/lang/" in k:
            hit |= diff_lang(zo, zn, k)
    if not hit:
        print("  (无变化)")

    print("\n" + "=" * 68)
    print("提醒：lang 只是线索。确认机制请回源码看常量/枚举/分支。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

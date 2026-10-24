#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""任务书质量自查：查「描述里出现不该出现的东西」。

这几条都是**生成器 6 项终检查不出来**的（它只管 id 合法、图标能渲染、依赖不悬空）：
  ① 名字重复词尾（`究极精华精华` —— mod 自带翻译的机械拼接错译）
  ② 裸 `#标签`（`#dusts/redstone`，新手看不懂）
  ③ 裸英文 id（`oil_sand`，看起来像坏掉的物品）
  ④ 属于**未安装 mod** 的物品（条件配方污染，玩家永远找不到）

用法：python 任务书质量自查.py <实例或工作区目录>
"""
import argparse
import pathlib
import re
import sys
import zipfile


def installed_namespaces(mods: pathlib.Path) -> set[str]:
    ns = set()
    for jar in sorted(mods.glob("*.jar")):
        try:
            z = zipfile.ZipFile(jar)
        except Exception:
            continue
        for n in z.namelist():
            if n.startswith("assets/") and n.count("/") >= 2:
                ns.add(n.split("/")[1])
            elif n.startswith("data/") and n.count("/") >= 1:
                ns.add(n.split("/")[1])
    return ns | {"minecraft", "forge", "neoforge", "c", "fabric"}


def broken_icon_report(root: pathlib.Path, chdir: pathlib.Path) -> list[tuple[str, str]]:
    """挑出「模型文件存在但模型是坏的」图标 —— 游戏里显示**问号**。

    🔴 只查 `models/item/<path>.json` 存不存在是不够的（那是前几轮的判据，会漏）。
       实测 `ars_nouveau:glyph`：模型文件在，内容却只有 `{"textures":{...}}`，
       既无 `parent` 也无 `elements` → 渲染成问号（玩家截图反馈）。
       合法模型至少有下面之一：
         · parent    普通物品
         · elements  手工建模
         · overrides 状态变体（bloodmagic:sigilofsuppression）
         · loader    OBJ 模型（immersiveengineering:fluid_pipe）
    """
    models: dict[str, object] = {}
    for jar in sorted((root / "mods").glob("*.jar")):
        try:
            z = zipfile.ZipFile(jar)
        except Exception:
            continue
        for n in z.namelist():
            if not (n.startswith("assets/") and "/models/item/" in n and n.endswith(".json")):
                continue
            seg = n.split("/models/item/")
            iid = f"{seg[0][len('assets/'):]}:{seg[1][:-5]}"
            if iid in models:
                continue
            try:
                models[iid] = json.loads(z.read(n).decode("utf-8-sig"))
            except Exception:
                models[iid] = "PARSE_FAIL"

    txt = "\n".join(f.read_text(encoding="utf-8") for f in chdir.glob("*.snbt"))
    bad: list[tuple[str, str]] = []
    for iid in sorted(set(re.findall(r'\n\t\ticon:\s*"([^"]+)"', txt))):
        if iid.split(":")[0] == "minecraft":
            continue
        d = models.get(iid)
        if d is None:
            bad.append((iid, "无 models/item 文件"))
        elif d == "PARSE_FAIL" or not isinstance(d, dict):
            bad.append((iid, "模型 JSON 坏了"))
        elif not any(k in d for k in ("parent", "elements", "overrides", "loader")):
            bad.append((iid, f"模型只有 {sorted(d.keys())} —— 渲染成问号"))
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(
        description="任务书内容质量自查：重复词尾 / 裸标签 / 裸英文 id / 未安装 mod 的物品")
    ap.add_argument("instance", nargs="?", default=".",
                    help="实例目录（或工作区的 instance 目录）")
    a = ap.parse_args()
    root = pathlib.Path(a.instance)
    chdir = root / "config" / "ftbquests" / "quests" / "chapters"
    if not chdir.is_dir():
        print(f"✗ 找不到章节目录：{chdir}", file=sys.stderr)
        return 2

    # 🔴 2026-10-03 修：只 glob "*.snbt"，而 26.1.2 用 **.json5** → 读到 0 个文件，
    #   后面所有检查**全部空转报绿**（实测输出「任务 0 描述行 0 ✓ 全部通过」）。
    #   对应 SKILL「读 .mca / 读任务书的脚本」的通用纪律：扫完必须把「扫到几个」
    #   交出去，0 一律报错，绝不能当通过。
    ch_files = sorted(chdir.glob("*.snbt")) + sorted(chdir.glob("*.json5"))
    if not ch_files:
        print(f"✗ 章节目录里既无 .snbt 也无 .json5：{chdir}", file=sys.stderr)
        return 2
    txt = "\n".join(f.read_text(encoding="utf-8") for f in ch_files)
    # 任务对象：SNBT 是 tab 缩进、JSON5 是 2 空格 → **不能按缩进取**
    #   （同一份脚本吃两种格式时，这处出过错两次），改用与缩进无关的 id 字段判据。
    n_quest = len(re.findall(r'^\s+id:\s*"[0-9A-F]{16}"', txt, re.M))
    # 描述文本在 lang 表里（26.1.2 起文本不进章节文件），两处都扫
    lang = root / "config" / "ftbquests" / "quests" / "lang"
    ltxt = ""
    for lf in sorted(lang.rglob("*.json5")) + sorted(lang.rglob("*.snbt")):
        ltxt += lf.read_text(encoding="utf-8") + "\n"
    desc = re.findall(r'"(&[78][^"]*)"', txt + "\n" + ltxt)
    if n_quest == 0:
        print(f"  ✗ 扫到 {len(ch_files)} 个章节文件但一个任务都没解析出来"
              f"（格式判据不匹配），不能当成「检查通过」", file=sys.stderr)
        return 2

    dup = sorted({m.group(1) + m.group(1) for m in re.finditer(r"([\u4e00-\u9fff]{2})", txt)
                  if (m.group(1) + m.group(1)) in txt})
    tags = [d for d in desc if "#" in d]
    rawid = [d for d in desc
             if re.search(r"(材料：|用于：|合成：).*[a-z]{3,}_[a-z]{2,}", d)]

    inst = installed_namespaces(root / "mods")
    used = set(re.findall(r'icon:\s*"([^:"]+):', txt))
    used |= set(re.findall(r'item:\s*\{[^}]*id:\s*"([^:"]+):', txt))
    orphan = sorted(used - inst)

    print(f"任务 {n_quest}   描述行 {len(desc)}")
    icons_bad = broken_icon_report(root, chdir)
    rows = [("① 重复词尾", dup), ("② 裸标签", tags),
            ("③ 裸英文 id", rawid), ("④ 未安装 mod 的物品", orphan),
            ("⑤ 图标渲染不出来", [f"{i} （{w}）" for i, w in icons_bad])]
    ok = True
    for name, bad in rows:
        flag = "✓" if not bad else "✗"
        if bad:
            ok = False
        print(f"  {flag} {name:<20} {len(bad)}")
        for b in bad[:5]:
            print(f"        {b}")
    print("\n" + ("✅ 全部通过" if ok else "❌ 有问题"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""任务书章节比对 —— 判断两处（或多处）的 FTB Quests 章节是否**语义一致**。

【为什么不能比字节】
`config/ftbquests/quests/chapters/*.snbt` 有**两种合法形态**：

| 形态 | 谁写的 | 特征 |
|---|---|---|
| **源**（生成器产物） | 我写的生成器 | 章节字段 1 tab / 任务字段 2 tab / 任务字段 3 tab；无 housekeeping 字段 |
| **FTB 规范化**（玩家玩过之后） | FTB Quests 自己保存时重写 | 补 `default_hide_dependency_lines` / `default_quest_shape` / `quest_links`；<br>任务字段 3 tab / 任务字段 4 tab；<br>**给 `tasks` 里的每个 task 补一个随机 id**（生成器没写） |

实测（2026-09-18）：把生成器产物同步进玩家实例后，玩家一进游戏，
FTB 就把 12 个章节全部重写成规范化形态 —— **同一个任务书，字节完全不同**。
所以「三处 MD5 一致」这个验收判据**只在源目录之间成立**（工作区 ↔ 服务端），
对**玩家实例**必须换成语义比对，否则会得到大量假差异。

【比对什么（都是 FTB 真正在乎的）】
  ① 章节集合（文件名）
  ② 章节级字段：`filename` / `group` / `order_index` / `id` / `title` / `icon`（1 tab 深度）
  ③ 每章的**任务 id 集合**（生成器产出的 16 位 hex；**排除 task 的 id** —— 那是 FTB 生成的）
  ④ 每章的**依赖 id 集合**（`dependencies`）
  ⑤ `chapter_groups.snbt` 的组表（若存在）

【用法】
    python 任务书章节比对.py <目录A> <目录B> [<目录C> ...]
    # 目录可以是 .../quests 也可以是 .../quests/chapters（自动识别）

【退出码】0 = 全部语义一致 / 1 = 有差异 / 2 = 参数或路径问题
"""
import argparse
import re
import sys
from pathlib import Path

HEX16 = set("0123456789ABCDEF")


def _find_quests_root(p: Path) -> Path | None:
    """把传入路径归一成「含 chapters/ 的那一层」。

    兼容传 `config/ftbquests`、`.../ftbquests/quests`、`.../quests` 三种写法。
    """
    for c in (p, p / "quests", p / "ftbquests", p / "ftbquests" / "quests"):
        if (c / "chapters").is_dir():
            return c
    return None


def _balanced_block(text: str, key: str) -> list[str]:
    """取出 `key:` 后面那个 `[...]` 或 `{...}` 块的**全部内容**（支持多行、支持嵌套）。

    🔴 为什么不能用 `key:\\s*\\[(.*?)\\]` 了事：
       我的生成器把 `dependencies: ["a", "b"]` 写在**一行**，
       而 **FTB 规范化后会改写成多行**：
       ```
       dependencies: [
           "7B10..."
       ]
       ```
       实测踩过：用单行匹配（只扫 `startswith("dependencies:")` 的那一行）
       → 在 FTB 侧**一个依赖都收不到** → 报出一堆假差异。
       所以必须**按括号配对**取块，而不是按行取。
    """
    return [text[s:e] for s, e in _block_spans(text, key)]


def _block_spans(text: str, key: str) -> list[tuple[int, int]]:
    """`key:` 后面那个块的 (起, 止) 区间（内容不含外层括号）。"""
    out: list[tuple[int, int]] = []
    # 注意必须有 `:` —— 曾经漏了它写成 `re.escape(key) + r"\s*([\[{])"`，
    # 于是 `tasks: [` 这种带冒号的写法一个都匹配不到，**静默返回空列表**。
    for m in re.finditer(re.escape(key) + r"\s*:\s*([\[{])", text):
        open_ch = m.group(1)
        close_ch = "]" if open_ch == "[" else "}"
        i = m.end() - 1
        depth = 0
        in_str = False
        prev = ""
        for j in range(i, len(text)):
            c = text[j]
            if c == '"' and prev != "\\":
                in_str = not in_str
            elif not in_str:
                if c == open_ch:
                    depth += 1
                elif c == close_ch:
                    depth -= 1
                    if depth == 0:
                        out.append((i + 1, j))
                        break
            prev = c
    return out


def _hex_ids(s: str) -> set[str]:
    """从字符串里抠出 16 位 hex 的 id（形如 7B10000000000001）。"""
    return {t for t in re.findall(r"[0-9A-F]{16}", s) if all(c in HEX16 for c in t)}


def parse_chapter(text: str) -> dict:
    """解析一个章节文件 → 语义字典。

    🔴 **不能靠固定缩进深度取字段**：源形态任务 id 在 2 tab，FTB 规范化后在 3 tab。
       踩过这个坑：写死 `^\\t\\tid:` 会把 FTB 侧的 quest id 全判成"缺失"，报出一堆假差异。

    做法：**按区间排除** ——
      · `dependencies: [...]` 区间   → 依赖集合
      · `rewards: [...]` 区间        → 奖励 id 集合（稳定，参与比对）
      · `tasks: [...]` 区间          → **FTB 会生成随机 task id，不参与比对**
      · 上述三个区间**之外**的 `id: "…"` → 章节 id + 任务 id

    🔴 为什么不能用「集合相减」（我第一版就是这么写的，错）：
       任务 id 既可能写在 `id:` 上，又可能被别的任务 `dependencies` 引用 ——
       用 `全部id − 依赖id` 会把**被依赖的任务整个减掉**，任务数凭空少一批。
       区间排除法没有这个问题。
    """
    dep_spans = _block_spans(text, "dependencies")
    rew_spans = _block_spans(text, "rewards")
    task_spans = _block_spans(text, "tasks")
    excl = [(s, e) for s, e in dep_spans + rew_spans + task_spans]

    dep_ids: set[str] = set()
    for s, e in dep_spans:
        dep_ids |= _hex_ids(text[s:e])
    rew_ids: set[str] = set()
    for s, e in rew_spans:
        rew_ids |= _hex_ids(text[s:e])
    task_ids: set[str] = set()
    for s, e in task_spans:
        task_ids |= _hex_ids(text[s:e])

    # 章节级字段：按「恰好 1 个 tab 缩进」取（源与 FTB 格式在这一层是一致的）
    ch_fields: dict[str, str] = {}
    for f in ("filename", "group", "order_index", "id", "title", "icon"):
        m = re.search(r"^\t" + f + r':\s*(.*)$', text, re.M)
        if m:
            v = m.group(1).strip().rstrip(",")
            if v.startswith('"') and v.endswith('"'):
                v = v[1:-1]
            ch_fields[f] = v

    # 任务 id = 三个区间**之外**的 id 值
    quest_ids: set[str] = set()
    for m in re.finditer(r'id:\s*"([0-9A-F]{16})"', text):
        if any(s <= m.start() < e for s, e in excl):
            continue
        quest_ids.add(m.group(1))
    if ch_fields.get("id"):
        quest_ids.discard(ch_fields["id"])      # 章节自己的 id 不算任务

    return {"fields": ch_fields, "quest_ids": quest_ids, "deps": dep_ids,
            "rewards": rew_ids, "n_lines": len(text.splitlines())}


def parse_dir(root: Path) -> dict:
    ch_dir = root / "chapters"
    out = {}
    for f in sorted(ch_dir.glob("*.snbt")):
        out[f.name] = parse_chapter(f.read_text(encoding="utf-8", errors="replace"))
    return out


def parse_groups(root: Path) -> dict:
    p = root / "chapter_groups.snbt"
    if not p.is_file():
        return {}
    t = p.read_text(encoding="utf-8", errors="replace")
    import re
    return dict(re.findall(r'id:\s*"([^"]+)"\s*,\s*title:\s*"([^"]+)"', t))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="判断多处 FTB Quests 章节是否语义一致（不比字节，见文件头说明）")
    ap.add_argument("dirs", nargs="+", help="两处或多处任务书目录（quests 或其父目录）")
    a = ap.parse_args()
    if len(a.dirs) < 2:
        print("✗ 至少给两个目录", file=sys.stderr)
        return 2

    roots = []
    for d in a.dirs:
        p = Path(d)
        if not p.is_dir():
            print(f"✗ 目录不存在：{p}", file=sys.stderr)
            return 2
        r = _find_quests_root(p)
        if r is None:
            print(f"✗ 找不到 chapters/ 子目录：{p}", file=sys.stderr)
            return 2
        roots.append(r)

    base = parse_dir(roots[0])
    base_g = parse_groups(roots[0])
    nq = sum(len(v["quest_ids"]) for v in base.values())
    print(f"基准 {roots[0]}")
    print(f"     {len(base)} 章 / {nq} 个任务 id / {len(base_g)} 个分组\n")

    bad = 0
    for r in roots[1:]:
        other = parse_dir(r)
        g = parse_groups(r)
        problems: list[str] = []

        only_a = sorted(set(base) - set(other))
        only_b = sorted(set(other) - set(base))
        if only_a:
            problems.append(f"仅基准有章节：{only_a}")
        if only_b:
            problems.append(f"仅此处有章节：{only_b}")
        for name in sorted(set(base) & set(other)):
            A, B = base[name], other[name]
            for k in ("filename", "group", "order_index", "id", "title", "icon"):
                if A["fields"].get(k) != B["fields"].get(k):
                    problems.append(f"{name}  {k}: {A['fields'].get(k)!r} ≠ {B['fields'].get(k)!r}")
            if A["quest_ids"] != B["quest_ids"]:
                sa, sb = A["quest_ids"] - B["quest_ids"], B["quest_ids"] - A["quest_ids"]
                problems.append(f"{name}  任务 id 集合不同（仅A {len(sa)} / 仅B {len(sb)}）"
                                f"  例：{'、'.join(sorted(sa)[:2]) or '—'} | {'、'.join(sorted(sb)[:2]) or '—'}")
            if A["deps"] != B["deps"]:
                problems.append(f"{name}  依赖 id 集合不同"
                                f"（仅A {sorted(A['deps']-B['deps'])[:2]} / 仅B {sorted(B['deps']-A['deps'])[:2]}）")
        if g != base_g:
            problems.append(f"chapter_groups 不同：基准 {len(base_g)} 组 / 此处 {len(g)} 组")

        if problems:
            bad += 1
            print(f"✗ {r}   {len(problems)} 处差异")
            for x in problems[:20]:
                print(f"      {x}")
            if len(problems) > 20:
                print(f"      …… 还有 {len(problems)-20} 处")
        else:
            print(f"✓ {r}   语义一致（{len(other)} 章）")

    print("\n" + ("✓ 全部语义一致" if not bad else f"✗ {bad} 处有差异"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

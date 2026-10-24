# -*- coding: utf-8 -*-
"""校验 assets 下所有 JSON 的资源引用是否都能落到实际文件上（引用完整性静态检查）。

## 为什么需要它

模型 JSON 引用了不存在的贴图 → 游戏里是**紫黑棋盘格**，客户端只在日志里留一行
`Missing textures in model <ns>:<name>#:` —— 而**编译、静态门禁、服务端 selftest 全都不报**。
本工具把这类「引用悬空」变成可静态检出的错误。

真实案例（2026-10-14，位面模组）：`models/block/plane_core.json` 引用了
`planes:block/plane_core`，但 `textures/block/plane_core.png` 从来没生成过；
另外 4 个盔甲 item 贴图同样缺失。补完之后用本工具做反证（临时挪走贴图 → 如期报错）。

## 覆盖四类引用

1. models 里 `"<key>": "<ns>:block/xxx"` / `<ns>:item/xxx` / `<ns>:models/armor/xxx`
     → `textures/block/xxx.png` / `textures/item/xxx.png` / `textures/models/armor/xxx.png`
2. models 里 `"parent": "<ns>:block/xxx"`（含 `<ns>:item/xxx`）→ `models/block/xxx.json`
3. blockstates 的 `variants` / `multipart` 里 `"model": "<ns>:block/xxx"` → `models/block/xxx.json`
4. JSON 解析失败本身也算问题（严格模式会静默丢模型）

## 用法

  python 校验资源引用.py <mod工程目录>              # 例：D:/…/位面模组
  python 校验资源引用.py <工程> --modid 自定义id    # 推断不出来时手动指定

modid 推断顺序：`--modid` 参数 → `gradle.properties` 的 `mod_id`
→ `assets/` 下唯一的非 `minecraft` 目录。

退出码：0 = 全部落到实际文件；1 = 有悬空引用；2 = 参数/环境问题。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def 推断_modid(工程: Path, 指定: str | None) -> str | None:
    if 指定:
        return 指定
    gp = 工程 / "gradle.properties"
    if gp.is_file():
        for line in gp.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("mod_id"):
                _, _, v = line.partition("=")
                v = v.strip()
                if v and "$" not in v:
                    return v
    assets = 工程 / "src" / "main" / "resources" / "assets"
    if assets.is_dir():
        cands = [d.name for d in assets.iterdir() if d.is_dir() and d.name != "minecraft"]
        if len(cands) == 1:
            return cands[0]
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="assets 资源引用完整性校验")
    ap.add_argument("project", help="mod 工程目录（含 src/main/resources）")
    ap.add_argument("--modid", help="手动指定 modid（默认从 gradle.properties 推断）")
    ap.add_argument("--max-report", type=int, default=40, help="最多列出多少条问题")
    a = ap.parse_args()

    工程 = Path(a.project)
    root = 工程 / "src" / "main" / "resources" / "assets"
    if not root.is_dir():
        print(f"[错] 找不到 {root}")
        return 2

    ns = 推断_modid(工程, a.modid)
    if not ns:
        print("[错] 推断不出 modid —— 用 --modid 指定")
        return 2
    print(f"modid = {ns}｜assets 根 = {root}")

    tex_re = re.compile(rf"^{re.escape(ns)}:(block|item|models/armor)/(.+)$")
    problems: list[str] = []
    checked = 0

    def rel(p: Path) -> str:
        return p.relative_to(root).as_posix()

    def check_tex(ref: str, src: Path) -> None:
        nonlocal checked
        m = tex_re.match(ref)
        if not m:
            return
        sub, name = m.group(1), m.group(2)
        checked += 1
        png = root / ns / "textures" / sub / (name + ".png")
        if not png.is_file():
            problems.append(
                f"{rel(src)}: 贴图引用悬空 → {ref}"
                f"（缺 {png.relative_to(root).as_posix()}）")

    def check_model(ref: str, src: Path) -> None:
        nonlocal checked
        if not ref.startswith(ns + ":"):
            return
        checked += 1
        js = root / ns / "models" / (ref[len(ns) + 1:] + ".json")
        if not js.is_file():
            problems.append(
                f"{rel(src)}: 模型引用悬空 → {ref}"
                f"（缺 {js.relative_to(root).as_posix()}）")

    for js in sorted(root.rglob("*.json")):
        try:
            data = json.loads(js.read_text(encoding="utf-8"))
        except Exception as e:                     # noqa: BLE001
            problems.append(f"{rel(js)}: JSON 解析失败 —— {e}")
            continue
        if not isinstance(data, dict):
            continue

        # 1) 贴图引用（models 的 textures 段）
        for v in (data.get("textures") or {}).values():
            if isinstance(v, str):
                check_tex(v, js)
        # 2) 父模型
        par = data.get("parent")
        if isinstance(par, str):
            check_model(par, js)
        # 3) blockstates 的 variants / multipart
        for section in ("variants", "multipart"):
            node = data.get(section)
            if node is None:
                continue
            stack = [node]
            while stack:
                cur = stack.pop()
                if isinstance(cur, dict):
                    mod = cur.get("model")
                    if isinstance(mod, str):
                        check_model(mod, js)
                    stack.extend(cur.values())
                elif isinstance(cur, list):
                    stack.extend(cur)

    print(f"扫描完成：共检查 {checked} 条 {ns}: 引用。")
    if problems:
        print(f"\n❌ 发现 {len(problems)} 处悬空引用：")
        for p in problems[:a.max_report]:
            print("   •", p)
        if len(problems) > a.max_report:
            print(f"   … 还有 {len(problems) - a.max_report} 条")
        return 1
    print("✅ 所有资源引用都能落到实际文件。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

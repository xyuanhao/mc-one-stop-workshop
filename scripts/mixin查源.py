# -*- coding: utf-8 -*-
"""mixin查源.py —— 从一个崩溃堆栈里的 mixin handler 名，反查它属于哪个 mod。

用途（2026-09-18 实战动机）：
  整合包出现「不崩服但世界是坏的」类问题时，异常栈里会有
      at net.minecraft.world.entity.LivingEntity.handler$zpd000$onConstructed(LivingEntity.java:9055)
  `<hash>` 是运行时随机分配的，没法直接查；但 **`onConstructed` 是 mixin handler 的方法名**，
  拿它去全量扫 mods/*.jar 就能精确定位注入方。

  比「看异常里出现的包名前缀然后猜谁负责」可靠得多 —— 实战中那条路会把你引到
  无辜的 mod（比如看到 `apoth_` 就怪 Apotheosis，其实真正的注入方是 spell_power）。

用法：
  # 1) 从堆栈里拿 handler 名
  python mixin查源.py --mods <mods目录> --handler onConstructed

  # 2) 想看某个类具体注入了什么（method / at / 行为）
  python mixin查源.py --mods <mods目录> --class net/spell_power/mixin/LivingEntityMixin.class

  # 3) 想找「所有注入了某个目标类构造函数的 mixin」
  python mixin查源.py --mods <mods目录> --ctor net/minecraft/world/entity/LivingEntity

输出：jar 名 | 内部路径 | 判据
"""
from __future__ import annotations

import argparse
import os
import re
import struct
import sys
import zipfile

# 常量池各 tag 的大小（不占 2 个槽位的按 1 计数）
_CP_SIZES = {
    7: 3, 8: 3, 16: 3, 19: 3, 20: 3,          # Class/String/MethodType/Module/Package
    15: 4,                                      # MethodHandle
    3: 5, 4: 5, 9: 5, 10: 5, 11: 5, 12: 5, 17: 5, 18: 5,   # 各类数值/引用
}
_CP_TWO_SLOT = (5, 6)                           # Long / Double 占两个槽位


def parse_utf8(data: bytes) -> list[str]:
    """从 class 文件常量池里抽取全部 Utf8 字符串。"""
    cp_count = struct.unpack(">H", data[8:10])[0]
    i = 10
    out: list[str] = []
    n = 1
    while n < cp_count:
        tag = data[i]
        if tag == 1:
            ln = struct.unpack(">H", data[i + 1:i + 3])[0]
            out.append(data[i + 3:i + 3 + ln].decode("utf-8", "replace"))
            i += 3 + ln
        elif tag in _CP_SIZES:
            i += _CP_SIZES[tag]
        elif tag in _CP_TWO_SLOT:
            i += 9
            n += 1
        else:
            i += 1
        n += 1
    return out


def iter_jars(mods_dir: str):
    for name in sorted(os.listdir(mods_dir)):
        if not name.endswith(".jar"):
            continue
        path = os.path.join(mods_dir, name)
        try:
            yield name, zipfile.ZipFile(path)
        except Exception as exc:                      # noqa: BLE001
            print(f"[跳过] {name}: {exc}", file=sys.stderr)


def by_handler(mods_dir: str, handler: str) -> int:
    """扫所有 class，找含 handler 方法名的。

    直接用常量池里的 Utf8 精确比对（handler 名一定会作为字符串出现在常量池里），
    不要用 bytes 粗暴 `in` — 那样会命中字节巧合，噪音很大。
    """
    hits = 0
    for jar, z in iter_jars(mods_dir):
        for nm in z.namelist():
            if not nm.endswith(".class"):
                continue
            try:
                strs = parse_utf8(z.read(nm))
            except Exception:                          # noqa: BLE001
                continue
            if handler in strs:
                print(f"{jar} | {nm} | HANDLER:{handler}")
                hits += 1
        z.close()
    if not hits:
        print(f"没找到含 handler 名 '{handler}' 的 class。"
              f"检查拼写，或该 handler 来自非 jar 来源（如 KubeJS）。", file=sys.stderr)
    return hits


def by_class(mods_dir: str, cls: str) -> int:
    """打印指定 class 的常量池（看 @Inject 的 method / at / 引用的方法名）。"""
    cls = cls.replace(".", "/")
    hits = 0
    for jar, z in iter_jars(mods_dir):
        if cls not in z.namelist():
            z.close()
            continue
        print(f"===== {jar} :: {cls} =====")
        for s in parse_utf8(z.read(cls)):
            print(" ", repr(s))
        hits += 1
        z.close()
    if not hits:
        print(f"没找到 {cls}（注意用 / 分隔的内部路径）", file=sys.stderr)
    return hits


def by_ctor(mods_dir: str, target: str, want_callback: bool = True) -> int:
    """找注入了 <target>.<init> 的 mixin。

    判据：常量池里同时出现构造签名 + CallbackInfo（@Inject 的 handler 一定有）。
    """
    target = target.replace(".", "/")
    sig = f"(L{target};"
    # 简化：找「(Lnet/minecraft/world/entity/EntityType;Lnet/minecraft/world/level/Level;)V」这类
    # 直接扫所有 mixin 包下的类，看是否含构造描述符 + CallbackInfo
    ctor_desc = "(Lnet/minecraft/world/entity/EntityType;Lnet/minecraft/world/level/Level;)V"
    hits = 0
    for jar, z in iter_jars(mods_dir):
        for nm in z.namelist():
            if not nm.endswith(".class"):
                continue
            low = nm.lower()
            if "mixin" not in low:
                continue
            try:
                strs = parse_utf8(z.read(nm))
            except Exception:                          # noqa: BLE001
                continue
            joined = "\n".join(strs)
            if ctor_desc in joined and (not want_callback or "CallbackInfo" in joined):
                # 只报真正 @Mixin 了 target 的（常量池里有 target 全名）
                if f"L{target};" in joined or target in joined:
                    print(f"{jar} | {nm} | INJECTS_CTOR_OF:{target}")
                    hits += 1
        z.close()
    if not hits:
        print(f"没找到注入 {target} 构造函数的 mixin", file=sys.stderr)
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description="从 mixin handler 名反查归属 mod")
    ap.add_argument("--mods", required=True, help="mods 目录")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--handler", help="堆栈里的 handler 方法名，如 onConstructed")
    g.add_argument("--class", dest="cls", help="要看常量池的 class 内部路径")
    g.add_argument("--ctor", help="找注入了哪个目标类构造函数的 mixin")
    args = ap.parse_args()

    if args.handler:
        return 0 if by_handler(args.mods, args.handler) else 1
    if args.cls:
        return 0 if by_class(args.mods, args.cls) else 1
    return 0 if by_ctor(args.mods, args.ctor) else 1


if __name__ == "__main__":
    sys.exit(main())

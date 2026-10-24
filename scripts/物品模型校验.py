# -*- coding: utf-8 -*-
"""
按 Minecraft 自身模型解析规则的静态校验器（针对 element 风格的手写模型）。

MC 的 ModelElement / BlockFace 反序列化时的硬性要求，写错了客户端资源重载会
直接失败（但只报在客户端日志里，服务端验证看不见）：
  · from / to 各 3 个数字，取值 -16..32
  · 逐轴 from < to（否则 "Elements must have at least one dimension"）
  · faces 至少一个，面名只能是 up/down/north/south/east/west
  · 每个面 uv 是 4 个数字、取值 0..16
  · face.texture 以 # 开头且引用的键必须在本模型 textures 里存在
  · rotation 存在时只能绕单轴（angle 为 45 的倍数，axis 是 x/y/z）
"""
import argparse
import json
from pathlib import Path

FACES = {"up", "down", "north", "south", "east", "west"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("--max-report", type=int, default=20)
    a = ap.parse_args()

    data = json.loads(Path(a.model).read_text(encoding="utf-8"))
    problems = []
    tex = data.get("textures", {})
    if not tex:
        problems.append("模型没有任何 textures")

    elements = data.get("elements")
    if not elements:
        problems.append("没有 elements（那就不该用这个校验器）")
        elements = []

    face_count = {f: 0 for f in FACES}
    for idx, el in enumerate(elements):
        frm, to = el.get("from"), el.get("to")
        if not (isinstance(frm, list) and len(frm) == 3):
            problems.append(f"#{idx} from 不是 3 个数字")
            continue
        if not (isinstance(to, list) and len(to) == 3):
            problems.append(f"#{idx} to 不是 3 个数字")
            continue
        for ax, name in enumerate("xyz"):
            if not (-16 <= frm[ax] <= 32) or not (-16 <= to[ax] <= 32):
                problems.append(f"#{idx} {name} 坐标越界 (-16..32): "
                                f"{frm[ax]}..{to[ax]}")
            if frm[ax] > to[ax]:
                problems.append(f"#{idx} {name} 轴 from > to ({frm[ax]} > {to[ax]})")
        faces = el.get("faces")
        if not faces:
            problems.append(f"#{idx} 没有任何面")
            continue
        for fname, fdef in faces.items():
            if fname not in FACES:
                problems.append(f"#{idx} 非法面名 {fname!r}")
                continue
            face_count[fname] += 1
            uv = fdef.get("uv")
            if uv is None:
                uv = [0, 0, 16, 16]
            if not (isinstance(uv, list) and len(uv) == 4):
                problems.append(f"#{idx}/{fname} uv 不是 4 个数字：{uv}")
            elif any(not (0 <= v <= 16) for v in uv):
                problems.append(f"#{idx}/{fname} uv 越界 (0..16)：{uv}")
            t = fdef.get("texture", "")
            if not isinstance(t, str) or not t.startswith("#"):
                problems.append(f"#{idx}/{fname} texture 必须以 # 开头：{t!r}")
            elif t[1:] not in tex:
                problems.append(f"#{idx}/{fname} 引用了不存在的纹理键 {t!r}")
            if "rotation" in fdef:
                r = fdef["rotation"]
                if r % 45:
                    problems.append(f"#{idx}/{fname} rotation {r} 不是 45 的倍数")

    print(f"模型        : {a.model}")
    print(f"element 数  : {len(elements)}")
    print(f"面数        : {sum(face_count.values())}   "
          + "  ".join(f"{k}={v}" for k, v in face_count.items()))
    print(f"纹理键      : {sorted(tex)}")
    if "gui_light" in data:
        print(f"gui_light   : {data['gui_light']}")
    if "parent" in data:
        print(f"parent      : {data['parent']}")
    print()
    if problems:
        print(f"❌ 发现 {len(problems)} 处问题：")
        for p in problems[:a.max_report]:
            print("   ·", p)
        if len(problems) > a.max_report:
            print(f"   ...（共 {len(problems)} 处）")
        raise SystemExit(1)
    print("✅ 全部 element 符合 MC 模型 schema")
    return 0


if __name__ == "__main__":
    main()

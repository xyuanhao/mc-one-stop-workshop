# -*- coding: utf-8 -*-
"""
物品模型离线渲染器 —— 不开游戏看 3D 模型长什么样。

读 MC 物品模型 JSON（elements 格式）+ 贴图，正交投影渲染成 PNG。
用来验收「体素化 / 3D 物品模型」这类只有进游戏才看得见的东西。

支持：背面剔除 / 方向光照（MC 的 up1.0 down0.5 南北0.8 东西0.6）/ 画家算法排序 /
多视角拼图 / UV 按面中心采样。
"""
import argparse
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw

# MC 的模型面方向光照（DOWN, UP, NORTH, SOUTH, WEST, EAST 的常量表）
SHADE = {"up": 1.0, "down": 0.5, "north": 0.8, "south": 0.8,
         "west": 0.6, "east": 0.6}
# 每个面的 4 个顶点（模型单位 0..16，右手系：x 右 / y 上 / z 出屏）
FACE_VERTS = {
    "north": [(0, 1, 0), (1, 1, 0), (1, 0, 0), (0, 0, 0)],
    "south": [(0, 1, 1), (0, 0, 1), (1, 0, 1), (1, 1, 1)],
    "west":  [(0, 1, 0), (0, 0, 0), (0, 0, 1), (0, 1, 1)],
    "east":  [(1, 1, 1), (1, 0, 1), (1, 0, 0), (1, 1, 0)],
    "up":    [(0, 1, 1), (0, 1, 0), (1, 1, 0), (1, 1, 1)],
    "down":  [(0, 0, 0), (0, 0, 1), (1, 0, 1), (1, 0, 0)],
}
# 每个面的 UV 基准角（MC 的 face uv 顺序：ul, ur, lr, ll）
UV_ROT = {
    "north": [(0, 0), (1, 0), (1, 1), (0, 1)],
    "south": [(0, 0), (0, 1), (1, 1), (1, 0)],
    "west":  [(0, 0), (0, 1), (1, 1), (1, 0)],
    "east":  [(0, 0), (0, 1), (1, 1), (1, 0)],
    "up":    [(0, 1), (0, 0), (1, 0), (1, 1)],
    "down":  [(0, 0), (1, 0), (1, 1), (0, 1)],
}


def rot_mat(rx, ry, rz):
    rx, ry, rz = map(math.radians, (rx, ry, rz))
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    # R = Ry * Rx * Rz
    return [
        [cy * cz + sy * sx * sz, -cy * sz + sy * sx * cz, sy * cx],
        [cx * sz, cx * cz, -sx],
        [-sy * cz + cy * sx * sz, sy * sz + cy * sx * cz, cy * cx],
    ]


def apply(m, p):
    return (sum(m[r][c] * p[c] for c in range(3)) for r in range(3))


def shade_color(rgb, f):
    return tuple(max(0, min(255, int(v * f))) for v in rgb)


def render(model_path, tex_path, out_path, views, size=420, zoom=9.0,
           bg=(24, 26, 30, 255)):
    model = json.loads(Path(model_path).read_text(encoding="utf-8"))
    tex = Image.open(tex_path).convert("RGBA")
    tw, th = tex.size
    tp = tex.load()
    elements = model.get("elements", [])
    if not elements:
        raise SystemExit("模型里没有 elements（只有 parent 引用？）")

    panels = []
    for (rx, ry, name) in views:
        m = rot_mat(rx, ry, 0)
        polys = []
        for el in elements:
            frm, to = el["from"], el["to"]
            for d, face in el["faces"].items():
                f = SHADE.get(d, 0.8)
                # UV 中心采样
                u1, v1, u2, v2 = face["uv"]
                px = min(tw - 1, max(0, int((u1 + u2) / 2 / 16 * tw)))
                py = min(th - 1, max(0, int((v1 + v2) / 2 / 16 * th)))
                color = shade_color(tp[px, py][:3], f)
                pts, depth = [], 0.0
                for (cx, cy, cz), (uu, vv) in zip(FACE_VERTS[d], UV_ROT[d]):
                    p = (frm[0] + (to[0] - frm[0]) * cx - 8,
                         frm[1] + (to[1] - frm[1]) * cy - 8,
                         frm[2] + (to[2] - frm[2]) * cz - 8)
                    q = tuple(apply(m, p))
                    pts.append((size / 2 + q[0] * zoom,
                                size / 2 - q[1] * zoom))
                    depth += q[2]
                polys.append((depth / 4, pts, color, d))
        # 画家算法：远 -> 近
        polys.sort(key=lambda t: t[0])
        img = Image.new("RGBA", (size, size), bg)
        dr = ImageDraw.Draw(img)
        for _, pts, color, _d in polys:
            dr.polygon(pts, fill=color + (255,))
        dr.text((10, 8), name, fill=(200, 210, 225, 255))
        panels.append(img)

    W = size * len(panels) + 8 * (len(panels) - 1)
    canvas = Image.new("RGBA", (W, size), bg)
    for k, p in enumerate(panels):
        canvas.paste(p, (k * (size + 8), 0))
    canvas.save(out_path)
    print(f"渲染 {len(elements)} 个 element -> {out_path}  ({canvas.size})")
    return canvas


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--tex", required=True)
    ap.add_argument("-o", "--out", default="preview.png")
    ap.add_argument("--size", type=int, default=420)
    ap.add_argument("--zoom", type=float, default=9.0)
    ap.add_argument("--bg", default="24,26,30",
                    help="背景色 R,G,B。**验收实心感时必须换浅底色**："
                         "保留全部面的模型缝里是暗缝，而剔除过共面的会透出背景色，"
                         "深色背景下两者几乎分不出来（2026-10-04 实测）")
    ap.add_argument("--views", default="0,0;30,225;20,135;65,200",
                    help="视角列表 rx,ry;rx,ry;…（默认四个常用视角；"
                         "近看手持感用大 zoom + 25,205 之类）")
    a = ap.parse_args()
    views = []
    for spec in a.views.split(';'):
        rx, ry = (float(v) for v in spec.strip().split(','))
        views.append((rx, ry, spec.strip()))
    bg = tuple(int(v) for v in a.bg.split(',')) + (255,)
    render(a.model, a.tex, a.out, views, size=a.size, zoom=a.zoom, bg=bg)

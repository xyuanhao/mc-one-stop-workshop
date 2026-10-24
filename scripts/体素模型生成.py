# -*- coding: utf-8 -*-
"""
体素模型生成器 —— 把一张 16x16 的物品贴图「立体化」成由成百上千个体素立方体
拼成的 3D 模型（Minecraft Java 版物品模型 JSON）。

原理
----
1. 取贴图的 alpha 作为轮廓掩码，按 `--scale` 放大成 GW x GH 的体素网格。
2. 对掩码做**欧氏距离变换** d(i,j) = 到最近背景格的距离。
3. 沿厚度（模型 Z 轴）铺若干层，第 |k| 层的掩码 = { d >= 0.5 + |k| * step }。
   —— 越靠外要求离边界越远，于是自动形成「中间厚、边缘薄」的倒角，
      而且细长剑身天然只有中间几层、粗护手能铺满所有层，不需要手写形状规则。
4. 每个格子生成一个独立的小立方体 element：位置居中、边长 = 体素边长 * shrink，
   于是方块之间留出细缝 —— 这就是「由一个个方块拼成」的视觉来源。
5. **相邻共面：无缝时剔、留缝时留**（这是本项目最容易搞反的一处）。
   · `shrink = 1.0`（默认）相邻方块严丝合缝，**必须剔除共面** —— 两个体素的接触面
     完全重合，都留着会 Z-fighting 闪烁。
   · `shrink < 1` 留了缝，**必须保留全部面** —— 剔掉后缝里什么都没有，会形成贯通
     空腔直接透出背景，看着像一堆悬浮的空心小方块（元 2026-10-04 实机反馈两次：
     先「太虚」后「应该是一体」）。
   脚本会自动检测并纠正「留缝 + 剔面」这个错误组合。

输出：模型 JSON + 一张 GW x GH 的贴图（每格一色，带轻微亮度扰动 → 方块颗粒感）
"""
import argparse
import json
import math
import random
from pathlib import Path

from PIL import Image

# MC 模型空间是 16x16x16（单位为 1/16 像素），Z 中心恒为 8
MODEL_SIZE = 16.0
Z_CENTER = 8.0


def load_mask(tex_path, scale):
    """读 16x16 贴图 -> (GW, GH, mask[y][x], rgb[y][x], W, H)"""
    img = Image.open(tex_path).convert("RGBA")
    W, H = img.size
    px = img.load()
    gw, gh = W * scale, H * scale
    mask = [[False] * gw for _ in range(gh)]
    rgb = [[(0, 0, 0)] * gw for _ in range(gh)]
    for y in range(gh):
        for x in range(gw):
            r, g, b, a = px[x // scale, y // scale]
            if a > 0:
                mask[y][x] = True
                rgb[y][x] = (r, g, b)
    return gw, gh, mask, rgb


def distance_transform(gw, gh, mask):
    """欧氏距离变换：每个前景格到最近背景格的欧氏距离。"""
    bg = [(x, y) for y in range(gh) for x in range(gw) if not mask[y][x]]
    if not bg:  # 全实心，退化成「到外框的距离」
        bg = [(x, y) for y in range(-1, gh + 1)
              for x in (-1, gw)] + [(x, y) for x in range(-1, gw + 1)
                                    for y in (-1, gh)]
    dist = [[0.0] * gw for _ in range(gh)]
    for y in range(gh):
        for x in range(gw):
            if not mask[y][x]:
                continue
            best = 1e18
            for bx, by in bg:
                dd = (x - bx) ** 2 + (y - by) ** 2
                if dd < best:
                    best = dd
            dist[y][x] = math.sqrt(best)
    return dist


def build_voxels(gw, gh, mask, dist, side, step):
    """
    构造体素集合 {(i, j, k)}。
    side = 每侧层数（总共 2*side+1 层）；第 |k| 层阈值 = 0.5 + |k| * step
    """
    voxels = set()
    for k in range(-side, side + 1):
        th = 0.5 + abs(k) * step
        for y in range(gh):
            for x in range(gw):
                if mask[y][x] and dist[y][x] >= th:
                    voxels.add((x, y, k))
    return voxels


def neighbors_present(vox, x, y, k):
    return all((x + dx, y + dy, k + dk) in vox
               for dx, dy, dk in ((1, 0, 0), (-1, 0, 0), (0, 1, 0),
                                  (0, -1, 0), (0, 0, 1), (0, 0, -1)))


def make_element(i, j, k, gw, gh, side, shrink):
    """生成一个体素立方体的 element（只输出有暴露的面）。"""
    vs = MODEL_SIZE / gw                      # 体素边长（模型单位）
    half = vs * shrink / 2.0
    cx = (i + 0.5) * vs
    cy = MODEL_SIZE - (j + 0.5) * vs          # 贴图行号从上往下，模型 Y 往上
    cz = Z_CENTER + k * vs
    frm = [round(cx - half, 4), round(cy - half, 4), round(cz - half, 4)]
    to = [round(cx + half, 4), round(cy + half, 4), round(cz + half, 4)]
    # 该体素占用的贴图像素 -> UV（UV 空间恒为 0..16）
    u1 = i * MODEL_SIZE / gw
    v1 = j * MODEL_SIZE / gh
    u2 = (i + 1) * MODEL_SIZE / gw
    v2 = (j + 1) * MODEL_SIZE / gh
    uv = [round(u1, 4), round(v1, 4), round(u2, 4), round(v2, 4)]
    face = {"uv": uv, "texture": "#all"}
    return {"from": frm, "to": to,
            "faces": {d: dict(face) for d in
                      ("up", "down", "north", "south", "east", "west")}}


def make_voxel_texture(gw, gh, mask, rgb, jitter, seed):
    """
    生成 GW x GH 的贴图：每格 = 原像素色 + 轻微亮度扰动。
    透明区域用「最近实心像素」扩散填充（防止 mipmap 采样出杂边）。
    """
    rnd = random.Random(seed)
    out = Image.new("RGBA", (gw, gh), (0, 0, 0, 0))
    op = out.load()

    def jit(c):
        f = 1.0 + rnd.uniform(-jitter, jitter)
        return tuple(max(0, min(255, int(round(v * f)))) for v in c)

    filled = [[False] * gw for _ in range(gh)]
    for y in range(gh):
        for x in range(gw):
            if mask[y][x]:
                op[x, y] = jit(rgb[y][x]) + (255,)
                filled[y][x] = True
    # BFS 扩散填充空洞
    frontier = [(x, y) for y in range(gh) for x in range(gw) if filled[y][x]]
    while frontier:
        nxt = []
        for x, y in frontier:
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < gw and 0 <= ny < gh and not filled[ny][nx]:
                    r, g, b, _ = op[x, y]
                    op[nx, ny] = (r, g, b, 255)
                    filled[ny][nx] = True
                    nxt.append((nx, ny))
        frontier = nxt
    return out


def main():
    ap = argparse.ArgumentParser(
        description="把 16x16 物品贴图体素化成 3D 模型（MC 物品模型 JSON）")
    ap.add_argument("--tex", required=True, help="输入贴图（16x16 PNG）")
    ap.add_argument("--out-model", required=True, help="输出模型 JSON 路径")
    ap.add_argument("--out-texture", required=True, help="输出贴图 PNG 路径")
    ap.add_argument("--scale", type=int, default=2,
                    help="放大倍数 S：每个原版像素切成 SxS 个体素（默认 2）")
    ap.add_argument("--side", type=int, default=2,
                    help="厚度方向每侧层数，总层数 = 2*side+1（默认 2 -> 5 层）")
    ap.add_argument("--step", type=float, default=0.7,
                    help="层间距离阈值间距，越大外围越窄（默认 0.7）")
    ap.add_argument("--shrink", type=float, default=1.0,
                    help="体素缩放：1.0 = 相邻方块严丝合缝（默认，看起来是一整块）；"
                         "<1 则在方块之间留缝、能看出一块块独立的方块")
    ap.add_argument("--jitter", type=float, default=0.05,
                    help="贴图每格亮度扰动幅度（默认 0.05，让每块颜色略有差别）")
    ap.add_argument("--seed", type=int, default=20261004)
    ap.add_argument("--no-cull", dest="cull", action="store_false",
                    help="保留相邻共面（面数 ×5）。⚠️ 只在留缝（--shrink<1）时才该用；"
                         "无缝时保留会 Z-fighting 闪烁。留缝 + 剔面 = 缝里透光发虚。")
    ap.add_argument("--ns", default="emeraldsword", help="命名空间")
    ap.add_argument("--name", default="emeraldsword", help="模型/贴图名")
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()

    # 🔴 自动纠正「留缝 + 剔面」这个致命组合：
    #    缝 + 剔面 = 缝里形成贯通空腔、直接透出背景 —— 就是元实机反馈的「太虚」。
    #    反过来无缝 + 不剔 = 相邻面完全重合，Z-fighting 闪烁。
    if args.shrink < 0.999 and args.cull:
        print("⚠️ 检测到 --shrink < 1 却要剔共面：缝里会透光发虚，"
              "已自动改为保留全部面。（想省面数请用 --shrink 1.0 的无缝模式）")
        args.cull = False

    gw, gh, mask, rgb = load_mask(args.tex, args.scale)
    dist = distance_transform(gw, gh, mask)
    vox = build_voxels(gw, gh, mask, dist, args.side, args.step)
    if not vox:
        raise SystemExit("体素集合为空：检查贴图 alpha 或减小 --step")

    # 逐层统计
    per_layer = {}
    for (_, _, k) in vox:
        per_layer[k] = per_layer.get(k, 0) + 1

    # 生成 elements
    elements, faces, skipped = [], 0, 0
    for (i, j, k) in sorted(vox, key=lambda t: (t[2], t[1], t[0])):
        el = make_element(i, j, k, gw, gh, args.side, args.shrink)
        if args.cull:
            # 无缝模式（shrink=1.0）下剔面：相邻面完全重合，留着必然 Z-fighting。
            # 此时「六面全被包住的内部格子整块跳过」是安全的 —— 无缝就看不到内部。
            # （留缝模式下 args.cull 已被上面的自动纠错强制关掉，走不到这里。）
            for d, (dx, dy, dk) in (("east", (1, 0, 0)), ("west", (-1, 0, 0)),
                                    ("down", (0, 1, 0)), ("up", (0, -1, 0)),
                                    ("south", (0, 0, 1)), ("north", (0, 0, -1))):
                if (i + dx, j + dy, k + dk) in vox:
                    el["faces"].pop(d, None)
            if not el["faces"]:
                skipped += 1
                continue
        faces += len(el["faces"])
        elements.append(el)

    model = {
        "parent": "minecraft:item/handheld",
        # 🔴 必须显式覆盖：item/handheld -> item/generated 带 "gui_light": "front"，
        # 那是给扁平贴图用的正面平光；继承下来物品栏里 3D 模型会被照成一片死板色块，
        # 方块感和立体感全部消失。3D 模型必须用 "side"（按面朝向打光 + AO）。
        "gui_light": "side",
        "textures": {
            "particle": f"{args.ns}:item/{args.name}",
            "all": f"{args.ns}:item/{args.name}",
        },
        "elements": elements,
    }
    outp = Path(args.out_model)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(model, separators=(",", ":")), encoding="utf-8")

    tex = make_voxel_texture(gw, gh, mask, rgb, args.jitter, args.seed)
    texp = Path(args.out_texture)
    texp.parent.mkdir(parents=True, exist_ok=True)
    tex.save(texp)

    print(f"网格          : {gw} x {gh}   (scale={args.scale})")
    print(f"体素总数      : {len(vox)}   层数 {len(per_layer)}  "
          f"({args.side * 2 + 1} 层)")
    print("  逐层格数    : " + "  ".join(f"k={k}:{per_layer[k]}"
                                          for k in sorted(per_layer)))
    if args.cull:
        note = (f"（无缝模式 shrink={args.shrink:g}：相邻面完全重合，剔共面避免 "
                f"Z-fighting；内部整块省略 {skipped} 个）")
    else:
        note = f"（留缝模式 shrink={args.shrink:g}：保留全部面，缝里是邻居侧壁）"
    print(f"输出的 element: {len(elements)}   面数 {faces}   {note}")
    print(f"模型文件      : {outp}  ({outp.stat().st_size / 1024:.1f} KB)")
    print(f"贴图文件      : {texp}  ({gw}x{gh})")
    if args.stats:
        print(f"厚度          : {(2 * args.side) * (MODEL_SIZE / gw) + MODEL_SIZE / gw * args.shrink:.2f} 单位")


if __name__ == "__main__":
    main()

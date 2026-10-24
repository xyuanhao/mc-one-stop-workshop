"""实体模型等距预览器 —— 不开游戏就能看模型长什么样、有没有浮空、UV 对不对。

## 为什么需要它

MC 的实体模型问题（浮空、比例失调、UV 错位、贴图串色）只有进游戏才看得出来，
在没有图形界面的环境里根本没法自查，来回试错成本极高。这个脚本用一份
**与 Java 同步的盒子表** + 真实贴图采样，直接在本地出等距投影图，并做数值自检。

实测价值：靠它发现过"整只生物浮空 1.5 格"（脚底没放在 y=24）。

## 用法

    # 用内置示例（一艘四足晶兽）
    python 实体模型预览.py -o preview.png

    # 用自己的盒子表
    python 实体模型预览.py boxes.json -o preview.png --tex path/to/entity.png

`boxes.json` 格式（数组，每项一个立方体）：
```json
[
  {"name": "body",  "x": -6, "y": 8,  "z": -8,  "w": 12, "h": 9,  "d": 16, "u": 0,  "v": 0},
  {"name": "head",  "x": -4, "y": 8,  "z": -15, "w": 8,  "h": 7,  "d": 7,  "u": 0,  "v": 27}
]
```
* `x/y/z` = 该立方体在**绝对模型坐标**里的最小角
  （即 `PartPose.offset` + `addBox(x,y,z,...)` 相加后的结果）
* `w/h/d` = 尺寸，与 `addBox` 的后三个参数一致
* `u/v`  = `texOffs(u, v)`

## 自检会检查什么

1. **脚底高度**：所有盒子的 max(y+h) 必须等于 24 —— MC 铁律，见下
2. 模型的高度 / 宽度 / 长度（用来定 `EntityType.Builder.sized()`）
3. **UV 越界**：`texOffs + 立方体展开所需尺寸` 是否超出贴图范围
4. **UV 重叠**：两个盒子抢同一块贴图（尺寸与 texOffs 都相同的视为"故意共用"，如四条腿）

## 🔴 铁律：脚底必须落在模型坐标 y = 24

MC 渲染实体时执行 `poseStack.scale(-1,-1,1)` 然后 `poseStack.translate(0, -1.501, 0)`，
模型点 p 的世界高度 = `-(p.y/16 - 1.501)`：

| 脚底模型 y | 世界高度 | 结果 |
|---|---|---|
| 24 | ≈ 0 | ✅ 正常踩地 |
| 0  | 1.501 格 | ❌ 整只浮空 1.5 格 |

原版 `CowModel` 就是 `PartPose.offset(±4, 12, ±7)` + `addBox(-2, 0, -2, 4, 12, 4)` → 落在 24。
玩家 / 僵尸 / 狼全都一样。**建任何实体模型，先把脚底钉在 24，再往上长。**

另外模型坐标 **y 向下为正**：数值小的在上面；`addBox(x,y,z,w,h,d)` 的 (x,y,z) 是
**最小角**，h 朝 +y 生长。
"""
import argparse
import json
import math
import os
import sys

try:
    from PIL import Image
except ImportError:
    print('需要 Pillow：pip install pillow')
    sys.exit(2)

HERE = os.path.dirname(os.path.abspath(__file__))
GROUND = 24.0        # MC 约定的脚底高度
CELLS = 6            # 每个面切几格采样
PX_PER_UNIT = 11     # 1 模型单位画多少像素

# 内置示例：一只四足晶兽（来自 Emberling 模组的烬灵）
DEMO = [
    {"name": "body",            "x": -6.0, "y": 8.0, "z": -8.0, "w": 12, "h": 9, "d": 16, "u": 0,  "v": 0},
    {"name": "head",            "x": -4.0, "y": 8.0, "z": -15.0, "w": 8, "h": 7, "d": 7, "u": 0,  "v": 27},
    {"name": "snout",           "x": -3.0, "y": 11.0, "z": -18.0, "w": 6, "h": 4, "d": 3, "u": 32, "v": 27},
    {"name": "eye_left",        "x": -3.0, "y": 10.5, "z": -15.5, "w": 2, "h": 3, "d": 1, "u": 32, "v": 36},
    {"name": "eye_right",       "x": 1.0, "y": 10.5, "z": -15.5, "w": 2, "h": 3, "d": 1, "u": 40, "v": 36},
    {"name": "spike_a",         "x": -5.0, "y": 2.0, "z": -5.5, "w": 3, "h": 6, "d": 3, "u": 56, "v": 0},
    {"name": "spike_b",         "x": 0.5, "y": -2.0, "z": -0.5, "w": 3, "h": 10, "d": 3, "u": 56, "v": 11},
    {"name": "spike_c",         "x": -5.5, "y": 4.0, "z": 3.0, "w": 2, "h": 4, "d": 2, "u": 72, "v": 0},
    {"name": "spike_d",         "x": 3.0, "y": 5.0, "z": 5.0, "w": 2, "h": 3, "d": 2, "u": 72, "v": 8},
    {"name": "tail",            "x": -1.5, "y": 10.0, "z": 8.0, "w": 3, "h": 3, "d": 5, "u": 84, "v": 0},
    {"name": "leg_front_left",  "x": -7.0, "y": 17.0, "z": -8.0, "w": 4, "h": 7, "d": 4, "u": 84, "v": 10},
    {"name": "leg_front_right", "x": 3.0, "y": 17.0, "z": -8.0, "w": 4, "h": 7, "d": 4, "u": 84, "v": 10},
    {"name": "leg_back_left",   "x": -7.0, "y": 17.0, "z": 4.0, "w": 4, "h": 7, "d": 4, "u": 84, "v": 10},
    {"name": "leg_back_right",  "x": 3.0, "y": 17.0, "z": 4.0, "w": 4, "h": 7, "d": 4, "u": 84, "v": 10},
]

VISIBLE = ('top', 'front', 'right')   # 等距视角下能看到的三个面
BG = (26, 26, 30, 255)


# ---------------------------------------------------------------- 几何

def face_uv(u, v, w, h, d, face):
    """MC 的立方体 UV 展开规则，返回 (u0, v0, uw, vh)。"""
    if face == 'top':
        return u + d, v, w, d
    if face == 'bottom':
        return u + d + w, v, w, d
    if face == 'front':                                  # -z
        return u + d, v + d, w, h
    if face == 'back':                                   # +z
        return u + 2 * d + w, v + d, w, h
    if face == 'right':                                  # +x
        return u, v + d, d, h
    return u + d + w, v + d, d, h                        # -x


def face_corners(x0, y0, z0, x1, y1, z1, face):
    """面的 4 个角，顺序对应 UV 的 (0,0) (1,0) (1,1) (0,1)。"""
    if face == 'top':
        return [(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)]
    if face == 'front':
        return [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0)]
    return [(x1, y0, z0), (x1, y0, z1), (x1, y1, z1), (x1, y1, z0)]


def project(x, y, z):
    """模型坐标 -> 屏幕坐标。

    等距投影，屏幕上方向取世界 +Y；模型 y 向下为正，故 y_world = -y_model，
    代入 sy = (x+z)·sin30° − y_world 得下式。

    ⚠️ 写成 y − (x+z)·sin30° 会让各部件沿对角散开 —— 踩过这个坑。
    """
    sx = (x - z) * math.cos(math.radians(30))
    sy = (x + z) * math.sin(math.radians(30)) + y
    return sx, sy


def fill_quad(px, quad, col, W, H):
    """扫描线填充凸四边形（端点外扩 1px，避免采样缝隙）。"""
    ys = [p[1] for p in quad]
    y_start = max(0, int(min(ys)))
    y_end = min(H - 1, int(max(ys)) + 1)
    for y in range(y_start, y_end + 1):
        xs = []
        for i in range(4):
            x1, y1 = quad[i]
            x2, y2 = quad[(i + 1) % 4]
            if (y1 <= y < y2) or (y2 <= y < y1):
                t = (y - y1) / (y2 - y1)
                xs.append(x1 + (x2 - x1) * t)
        if len(xs) < 2:
            continue
        for x in range(max(0, int(min(xs))), min(W - 1, int(max(xs))) + 1):
            px[x, y] = (col[0], col[1], col[2], 255)


# ---------------------------------------------------------------- 主流程

def render(boxes, tex_path, out_path):
    tex = None
    tex_w = tex_h = 0
    if tex_path and os.path.isfile(tex_path):
        tex = Image.open(tex_path).convert('RGBA')
        tex_w, tex_h = tex.size
    tp = tex.load() if tex else None

    polys = []
    view = (1.0, -1.0, -1.0)                   # 物体 -> 相机

    for b in boxes:
        x0, y0, z0 = b['x'], b['y'], b['z']
        w, h, d = b['w'], b['h'], b['d']
        u, v = b['u'], b['v']
        x1, y1, z1 = x0 + w, y0 + h, z0 + d

        for face in VISIBLE:
            uu, vv, uw, vh = face_uv(u, v, w, h, d, face)
            corners = face_corners(x0, y0, z0, x1, y1, z1, face)
            col_base = (150, 150, 155) if tp is None else None

            for i in range(CELLS):
                for j in range(CELLS):
                    a, bnd = i / CELLS, (i + 1) / CELLS
                    c, e = j / CELLS, (j + 1) / CELLS

                    def lerp(p, q, t):
                        return tuple(p[k] + (q[k] - p[k]) * t for k in range(3))

                    p00 = lerp(lerp(corners[0], corners[1], a), lerp(corners[3], corners[2], a), c)
                    p10 = lerp(lerp(corners[0], corners[1], bnd), lerp(corners[3], corners[2], bnd), c)
                    p11 = lerp(lerp(corners[0], corners[1], bnd), lerp(corners[3], corners[2], bnd), e)
                    p01 = lerp(lerp(corners[0], corners[1], a), lerp(corners[3], corners[2], a), e)

                    if tp is None:
                        col = col_base
                    else:
                        tx = int(uu + (a + bnd) / 2 * uw)
                        ty = int(vv + (c + e) / 2 * vh)
                        tx = max(0, min(tex_w - 1, tx))
                        ty = max(0, min(tex_h - 1, ty))
                        r, g, bb, al = tp[tx, ty]
                        if al < 8:
                            continue
                        col = (r, g, bb)

                    shade = 1.0 if face == 'top' else (0.86 if face == 'front' else 0.70)
                    col = tuple(min(255, int(cc * shade)) for cc in col)

                    mid = tuple((p00[k] + p11[k]) / 2 for k in range(3))
                    polys.append((sum(mid[k] * view[k] for k in range(3)),
                                  [p00, p10, p11, p01], col))

    polys.sort(key=lambda t: t[0])             # 画家算法：远的先画

    pts = [project(*p) for _d, ps, _c in polys for p in ps]
    if not pts:
        print('!! 没有可渲染的面（检查 box 尺寸/UV 是否合法）')
        return False
    minx, maxx = min(p[0] for p in pts), max(p[0] for p in pts)
    miny, maxy = min(p[1] for p in pts), max(p[1] for p in pts)

    pad = 30
    W = int((maxx - minx) * PX_PER_UNIT) + pad * 2
    H = int((maxy - miny) * PX_PER_UNIT) + pad * 2
    img = Image.new('RGBA', (W, H), BG)
    d = img.load()

    def to_screen(p):
        sx, sy = project(*p)
        return (pad + (sx - minx) * PX_PER_UNIT, pad + (sy - miny) * PX_PER_UNIT)

    for _dep, corners, col in polys:
        fill_quad(d, [to_screen(p) for p in corners], col, W, H)

    # 绿色横线 = 地面（y=24），用来目测脚底有没有贴地
    g0 = to_screen((-16, GROUND, 0))
    g1 = to_screen((16, GROUND, 0))
    fill_quad(d, [(g0[0], g0[1]), (g1[0], g1[1]), (g1[0], g1[1] + 3), (g0[0], g0[1] + 3)],
              (0, 200, 120), W, H)

    img.save(out_path)
    print('模型预览已保存:', out_path, img.size)
    return True


def check(boxes, tex_path):
    """数值自检：脚底高度 / 模型尺寸 / UV 越界 / UV 重叠。"""
    ok = True

    lowest = max(b['y'] + b['h'] for b in boxes)
    highest = min(b['y'] for b in boxes)
    if abs(lowest - GROUND) < 0.001:
        print('脚底最低点 y = %.1f（应为 %.1f）✓' % (lowest, GROUND))
    else:
        print('脚底最低点 y = %.1f（应为 %.1f）  ✗ 会浮空/陷地！'
              % (lowest, GROUND))
        print('   需要整体平移 %+.1f 个单位才贴地' % (GROUND - lowest))
        ok = False

    height = (lowest - highest) / 16.0
    width = (max(b['x'] + b['w'] for b in boxes) - min(b['x'] for b in boxes)) / 16.0
    length = (max(b['z'] + b['d'] for b in boxes) - min(b['z'] for b in boxes)) / 16.0
    print('模型尺寸 = 高 %.2f / 宽 %.2f / 长 %.2f 格' % (height, width, length))
    print('   -> EntityType.Builder.sized(底面, %.2f)，底面是正方形取 %.2f 左右'
          % (height, min(width, length)))

    if not (tex_path and os.path.isfile(tex_path)):
        print('（未提供贴图，跳过 UV 检查：--tex path/to/x.png）')
        return ok

    tw, th = Image.open(tex_path).size
    used = []
    for b in boxes:
        need_w, need_h = 2 * (b['w'] + b['d']), b['d'] + b['h']
        if b['u'] + need_w > tw or b['v'] + need_h > th:
            print('  ✗ UV 越界: %s 需要 (%d,%d)+%dx%d，贴图只有 %dx%d'
                  % (b['name'], b['u'], b['v'], need_w, need_h, tw, th))
            ok = False
        for oname, ou, ov, ow, oh, bw, bh in used:
            if (b['u'] < ou + ow and ou < b['u'] + need_w
                    and b['v'] < ov + oh and ov < b['v'] + need_h):
                # texOffs 与尺寸都相同 = 故意共用同一块 UV（如四条一样的腿）
                if (ou, ov, bw, bh) == (b['u'], b['v'], b['w'], b['h']):
                    continue
                print('  ✗ UV 重叠: %s 与 %s' % (b['name'], oname))
                ok = False
        used.append((b['name'], b['u'], b['v'], need_w, need_h, b['w'], b['h']))

    if ok:
        print('UV 自检: %d 个盒子全部落在 %dx%d 贴图内、彼此不重叠 ✓' % (len(boxes), tw, th))
    else:
        print('UV 自检未通过 —— 进游戏会出现错位色块或紫黑面')
    return ok


def main():
    ap = argparse.ArgumentParser(description='实体模型等距预览 + 自检')
    ap.add_argument('boxes', nargs='?', help='盒子表 JSON（不传则用内置示例）')
    ap.add_argument('-o', '--out', default='model_preview.png',
                    help='输出 PNG 路径（默认写到当前工作目录，不污染 skill 目录）')
    ap.add_argument('--tex', help='实体贴图路径（用于采样颜色 + UV 检查）')
    ap.add_argument('--no-render', action='store_true', help='只跑自检不出图')
    args = ap.parse_args()

    if args.boxes:
        with open(args.boxes, 'r', encoding='utf-8') as f:
            boxes = json.load(f)
        print('已载入 %d 个盒子: %s' % (len(boxes), args.boxes))
    else:
        boxes = DEMO
        print('未指定盒子表，使用内置示例（四足晶兽，%d 个盒子）' % len(boxes))

    ok = check(boxes, args.tex)
    if not args.no_render:
        render(boxes, args.tex, args.out)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())

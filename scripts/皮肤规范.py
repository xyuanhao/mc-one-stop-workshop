#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
皮肤 UV 规范核 —— 64×64 玩家皮肤的**唯一权威 UV 表**。

生成器 / 预览器 / 校验器三方共用本模块，避免"三处各写一张表然后漂移"。
（这是本 skill 反复踩过的坑：两个真相源必然漂移。）

⭐ 单位：**贴图像素（1 格 = 1 像素）**。表面矩形直接存 `(x, y, w, h)`，
   不存"层宽/层高 + 固定步长"—— 因为不同部件的面尺寸不同（头 8×8，臂 4×4），
   早先版本把层尺寸当面尺寸用，直接越界（`right_arm/back` 算到 (68,36)）。

📐 展开图规律（决定了下面所有坐标）：
   第 1 行：`top(w×d)` `bottom(w×d)` 并排
   第 2 行：`right(d×h)` `front(w×h)` `left(d×h)` `back(w×h)` 并排
   起点由官方给出的 6 个 base 左上角锚定：
       head (8,8)  body (20,20)  right_arm (44,20)
       right_leg (4,20)  left_arm (36,52)  left_leg (20,52)
   注意这些锚点是 **front 面**的左上角，不是整块区域的左上角。

导出：
  PART_SIZE / PART_ANCHOR   部件尺寸与 front 面锚点
  PART_FACES                部件 → 面 → (x, y, w, h)   基础层
  PART_FACES_OVERLAY        同上，外层
  OVERLAY_OFFSET            外层相对基础层的位移规则
  detect_model(img)         按游戏规则判定 slim / classic
  all_regions()             展平成 [(部件, 层, 面, x, y, w, h)] 供校验遍历
"""
from __future__ import annotations

# ── 部件尺寸 (宽, 高, 深) 与 front 面锚点 ─────────────────────────
PART_SIZE = {
    "head":      (8, 8, 8),
    "body":      (8, 12, 4),
    "right_arm": (4, 12, 4),
    "left_arm":  (4, 12, 4),
    "right_leg": (4, 12, 4),
    "left_leg":  (4, 12, 4),
}

PART_ANCHOR = {          # front 面左上角（官方 UV 表给的基准）
    "head":      (8, 8),
    "body":      (20, 20),
    "right_arm": (44, 20),
    "left_arm":  (36, 52),
    "right_leg": (4, 20),
    "left_leg":  (20, 52),
}


def _expand(anchor, size):
    """由 front 锚点 + 部件尺寸推出六个面的矩形。

    锚点 = front 左上角。反推：
      front  x0 = ax,                  y0 = ay
      right  x0 = ax - d,              y0 = ay          （front 左边紧邻 right）
      left   x0 = ax + w,              y0 = ay
      back   x0 = ax + w + d,          y0 = ay
      top    x0 = ax,                  y0 = ay - d      （top 在 front 正上方）
      bottom x0 = ax + w,              y0 = ay - d
    """
    w, h, d = size
    ax, ay = anchor
    return {
        "front":  (ax,           ay,      w, h),
        "right":  (ax - d,       ay,      d, h),
        "left":   (ax + w,       ay,      d, h),
        "back":   (ax + w + d,   ay,      w, h),
        "top":    (ax,           ay - d,  w, d),
        "bottom": (ax + w,       ay - d,  w, d),
    }


PART_FACES = {p: _expand(PART_ANCHOR[p], PART_SIZE[p]) for p in PART_SIZE}

# ── 外层（第二层）位移 ───────────────────────────────────────────
# ⚠️ 关键坑：**不是"一律向下 16"**。
#    躯干 / 右臂 / 右腿 的外层在正下方 16；
#    左臂外层 (52,52) 是向**右** 16；左腿外层 (0,52) 是向**左** 16；
#    帽子层 (40,8) 是向**右** 32（跟头同一行）。
#    当成"都向下 16"会静默落到**别的部件**上 —— 而且不报错。
OVERLAY_OFFSET = {
    "head":      ("right", 32),
    "body":      ("down",  16),
    "right_arm": ("down",  16),
    "right_leg": ("down",  16),
    "left_arm":  ("right", 16),
    "left_leg":  ("left",  16),
}

# 外层体积膨胀（贴图格／每边）：头 8 格放大 1.125 倍 → 每边多 0.5 格
OVERLAY_PADDING = {
    "head": 0.5, "body": 0.25,
    "right_arm": 0.25, "left_arm": 0.25,
    "right_leg": 0.25, "left_leg": 0.25,
}


def _shift(rect, mode, amount):
    x, y, w, h = rect
    if mode == "down":
        return (x, y + amount, w, h)
    if mode == "right":
        return (x + amount, y, w, h)
    if mode == "left":
        return (x - amount, y, w, h)
    raise ValueError(mode)


def build_overlay_faces():
    """由基础层表 + 位移规则算外层表（不手写第二张表，避免漂移）。"""
    return {
        part: {f: _shift(r, *OVERLAY_OFFSET[part]) for f, r in faces.items()}
        for part, faces in PART_FACES.items()
    }


PART_FACES_OVERLAY = build_overlay_faces()

# ── slim / classic 判定 ──────────────────────────────────────────
# 官方判据（唯一窗口，别自己扩大）：
#     sample  x 46–47, y 20–31   —— 2 宽 × 12 高 = 24 像素
#     count   该窗口内 alpha > 0 的像素
#     slim    if count < 6
# ⚠️ 阈值是 6/24 而非 1：只描了几笔边的 slim 皮不该被误判成 classic。
# ⚠️ 窗口**跨在右臂"前/后/左/右"四张面的列边界上**（右臂 front 从 x=44 起，
#    每面 4 列：44-47 是 front，48-51 是 left……），所以它**不是**"某个面的第 4 列"，
#    就是右臂矩形里那一条竖直窄带。自己按"面的第 4 列"重算会算成 96 个像素。
# ⚠️ PNG 本身**不存模型**；上传时由玩家自己选 Classic/Slim。
DETECT_WINDOW = [(x, y) for y in range(20, 32) for x in (46, 47)]
CLASSIC_COLUMN_THRESHOLD = 6


def detect_model(img):
    """按游戏规则判定这张皮该用哪个模型。返回 'classic' / 'slim'。"""
    if img.size != (64, 64):
        return None                      # 64×32 旧版不支持 slim
    px = img.convert("RGBA").load()
    filled = sum(1 for x, y in DETECT_WINDOW if px[x, y][3] > 0)
    return "classic" if filled >= CLASSIC_COLUMN_THRESHOLD else "slim"


def all_regions(with_overlay=True):
    """展平成 [(部件, 层, 面, x, y, w, h)]，给校验器遍历用。"""
    rows = [(p, "base", f, *r) for p, fs in PART_FACES.items() for f, r in fs.items()]
    if with_overlay:
        rows += [(p, "overlay", f, *r)
                 for p, fs in PART_FACES_OVERLAY.items() for f, r in fs.items()]
    return rows


def selfcheck():
    """自洽断言：每个面都必须落在 64×64 内、且六面面积与尺寸一致。"""
    errs = []
    for part, layer, face, x, y, w, h in all_regions():
        if x < 0 or y < 0 or x + w > 64 or y + h > 64:
            errs.append(f"{part}/{layer}/{face} 越界 ({x},{y},{w},{h})")
    # 基础层六面面积应为 2*(w*d) + 2*(d*h) + 2*(w*h)（按贴图格算）
    for part, (w, h, d) in PART_SIZE.items():
        got = sum(r[2] * r[3] for r in PART_FACES[part].values())
        want = 2 * (w * d) + 2 * (d * h) + 2 * (w * h)
        if got != want:
            errs.append(f"{part} 面积 {got} != 期望 {want}")
    return errs


if __name__ == "__main__":
    import argparse
    import sys as _sys

    ap = argparse.ArgumentParser(
        description="皮肤 UV 规范核：打印权威 UV 表并自洽断言；给了皮肤就顺便报判定结果。"
                    "（本文件主要是**被其它脚本 import 的共享库**，同时也当自检工具用）",
        epilog="生成/预览/校验三方都 import 本模块，改表请只改这里。")
    ap.add_argument("skin", nargs="?", help="可选：一张 64×64 皮肤 PNG，报 slim/classic")
    ap.add_argument("--table-only", action="store_true", help="只打印 UV 表")
    args = ap.parse_args()

    print("部件尺寸 / front 锚点 / 六面矩形：")
    for part in PART_SIZE:
        w, h, d = PART_SIZE[part]
        print(f"  {part:10s} {w}×{h}×{d}  front@{PART_ANCHOR[part]}")
        for face, (x, y, fw, fh) in PART_FACES[part].items():
            ox, oy, _, _ = PART_FACES_OVERLAY[part][face]
            print(f"      {face:6s} base({x:2d},{y:2d},{fw:2d},{fh:2d})  "
                  f"overlay({ox:2d},{oy:2d})")

    print("\n外层位移（⚠️ 不是一律向下 16）：")
    _zh = {"down": "下", "right": "右", "left": "左"}
    for k, (mode, n) in OVERLAY_OFFSET.items():
        print(f"  {k:10s} {_zh[mode]}移 {n}")

    errs = selfcheck()
    print("\n自洽检查：" + ("全部通过" if not errs else "发现问题"))
    for e in errs:
        print("  ✗ " + e)

    print(f"\n判定窗口像素数：{len(DETECT_WINDOW)}（阈值 {CLASSIC_COLUMN_THRESHOLD}）")
    print(f"总区域数（含外层）：{len(all_regions())}")

    if not args.table_only and args.skin:
        from PIL import Image

        img = Image.open(args.skin)
        print(f"\n{img.size[0]}×{img.size[1]}  → 判定模型：{detect_model(img)}")
    _sys.exit(1 if errs else 0)

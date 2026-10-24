#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
皮肤生成器 —— 程序化拼出一张完整的 64×64 Minecraft 玩家皮肤。

不是 AI 生图：皮肤只有 64×64 像素，程序化逐像素画才可控、可复现、可调参。
所有 UV 坐标来自 `皮肤规范.py`（唯一权威表），改表这里自动跟着变。

═══ 输出保证 ═══
  ① 尺寸严格 64×64 PNG
  ② **基础层六个部件的每个面都填满不透明**（游戏把基础层按不透明画，
     留透明会渲染成黑块 —— 这是新手最常见的翻车点）
  ③ 外层默认全透明，只有显式开启的配件才画
  ④ 脚底 / 头顶 / 手臂上下端这些"跳起来才看见"的面也处理了

═══ 用法 ═══
  # 全默认（classic 男体）
  python 皮肤生成.py -o skin.png

  # 指定配色 + 发型 + 配件
  python 皮肤生成.py --model slim --skin-tone "#F2D5B8" --hair-color "#2B2B38" \\
      --hair ponytail --eyes purple --shirt "#3A6EA5" --pants "#2C2C40" \\
      --glasses --scarf -o out/me.png

  # 直接给一套完整参数
  python 皮肤生成.py --preset 少女 -o out/girl.png

  # 列出所有可选值
  python 皮肤生成.py --list

  # 生成一张预览图（六面展开 + 等距 3D 视角）
  python 皮肤生成.py -o skin.png --preview preview.png
"""
from __future__ import annotations

import argparse
import os
import random
import sys

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import 皮肤规范 as reg


# ══════════════════════════════════════════════════════════════
# 配色
# ══════════════════════════════════════════════════════════════

def hx(s, named=None):
    """'#RRGGBB' / '#RRGGBBAA' / 'r,g,b' / 具名（如 'purple'）→ RGBA 元组。

    ⚠️ 长度必须显式判：早先版本只判 `len(s)==6`，传 'black'（5 字符）会掉进
       这个分支，`int('bl',16)` 报错 —— 而报错信息只说"颜色格式不对"，看不出原因。
    """
    s = str(s).strip()
    if named and s in named:
        s = named[s]
    if s.startswith("#"):
        s = s[1:]
    if "," in s:
        v = [int(x) for x in s.split(",")]
        if len(v) not in (3, 4):
            raise ValueError(f"颜色分量个数应为 3 或 4：{s}")
        return (v[0], v[1], v[2], v[3] if len(v) > 3 else 255)
    if len(s) == 8:
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4, 6))
    if len(s) == 6:
        r, g, b = (int(s[i:i + 2], 16) for i in (0, 2, 4))
        return (r, g, b, 255)
    raise ValueError(f"颜色格式不对（应 #RRGGBB / #RRGGBBAA / r,g,b）：{s!r}")


def shade(c, f):
    """按比例调明暗：f<1 变暗、f>1 变亮，保持 alpha。"""
    r, g, b, a = c
    if f <= 1:
        return (int(r * f), int(g * f), int(b * f), a)
    d = f - 1
    return (min(255, int(r + (255 - r) * d)),
            min(255, int(g + (255 - g) * d)),
            min(255, int(b + (255 - b) * d)), a)


# ══════════════════════════════════════════════════════════════
# 画布：把 UV 表包成"能按面/按像素画"的对象
# ══════════════════════════════════════════════════════════════

class Canvas:
    def __init__(self, chance_fn=None):
        self.im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        self.px = self.im.load()
        self.chance_fn = chance_fn or (lambda n: False)
        self.touched = set()

    # ── 基础 ────────────────────────────────────────────────
    def set(self, x, y, c):
        if 0 <= x < 64 and 0 <= y < 64:
            self.px[x, y] = c
            self.touched.add((x, y))

    def get(self, x, y):
        return self.px[x, y]

    def rect(self, x, y, w, h, c):
        for dy in range(h):
            for dx in range(w):
                self.set(x + dx, y + dy, c)

    def face(self, part, face, c, layer="base"):
        table = reg.PART_FACES if layer == "base" else reg.PART_FACES_OVERLAY
        x, y, w, h = table[part][face]
        self.rect(x, y, w, h, c)

    def face_span(self, part, face, layer="base"):
        table = reg.PART_FACES if layer == "base" else reg.PART_FACES_OVERLAY
        return table[part][face]

    def part_rects(self, part, layer="base"):
        table = reg.PART_FACES if layer == "base" else reg.PART_FACES_OVERLAY
        return list(table[part].values())


# ══════════════════════════════════════════════════════════════
# 程序化底纹
# ══════════════════════════════════════════════════════════════

def _noise(seed):
    rnd = random.Random(seed)

    def f(n_pct):
        return rnd.random() < n_pct / 100.0
    return f


def fill_noisy(cv, part, base, layer="base", amp=0.06, seed=1, density=30,
               protect=None):
    """整件部件铺底色 + 轻微噪点（避免纯色塑料感）。

    amp      —— 明暗浮动幅度
    density  —— 噪点密度（百分比）
    protect  —— 回调 (part, face, x, y) → True 则跳过（留给后续细节）
    """
    rnd = random.Random(seed)
    table = reg.PART_FACES if layer == "base" else reg.PART_FACES_OVERLAY
    for face, (fx, fy, w, h) in table[part].items():
        for dy in range(h):
            for dx in range(w):
                gx, gy = fx + dx, fy + dy
                if protect and protect(part, face, gx, gy):
                    continue
                c = base
                if rnd.random() < density / 100.0:
                    c = shade(base, 1 - amp if rnd.random() < 0.5 else 1 + amp * 0.7)
                cv.set(gx, gy, c)


def add_edge_shade(cv, part, layer="base", strength=0.88):
    """给每个面加"外圈压暗、内部稍亮"的立体感（MC 顶光一致性）。"""
    table = reg.PART_FACES if layer == "base" else reg.PART_FACES_OVERLAY
    for face, (fx, fy, w, h) in table[part].items():
        for dy in range(h):
            for dx in range(w):
                gx, gy = fx + dx, fy + dy
                edge = (dx in (0, w - 1)) or (dy in (0, h - 1))
                if edge:
                    c = cv.get(gx, gy)
                    if c[3] > 0:
                        camt = 1 - (1 - strength) * (0.6 if face in ("top",) else 1.0)
                        cv.set(gx, gy, shade(c, camt))
        # 顶部略提亮
        if face == "top":
            for dy in range(h):
                for dx in range(w):
                    c = cv.get(fx + dx, fy + dy)
                    if c[3] > 0:
                        cv.set(fx + dx, fy + dy, shade(c, 1.05))


# ══════════════════════════════════════════════════════════════
# 各部件绘制
# ══════════════════════════════════════════════════════════════

HAIR_STYLES = ("none", "short", "bob", "long", "ponytail", "spiky", "bald")
EYE_COLORS = {
    "black": "#1A1A22", "brown": "#4A2C17", "blue": "#3E6FA8",
    "green": "#3C7A4A", "purple": "#7A4A9E", "red": "#9E3A3A",
    "amber": "#B8862A", "gray": "#8A8A96",
}


def draw_body(cv, p, protect_face=None):
    """躯干：8×12 前面画衣服，加领口 / 下摆 / 腰带。"""
    shirt = p["shirt"]
    fill_noisy(cv, "body", shirt, amp=0.07, seed=p["seed"] + 11, density=22,
               protect=protect_face)
    _, _, w, h = cv.face_span("body", "front")

    # 左右侧面压暗（体积感）
    for face in ("right", "left"):
        x, y, fw, fh = cv.face_span("body", face)
        for dy in range(fh):
            for dx in range(fw):
                c = cv.get(x + dx, y + dy)
                if c[3] > 0:
                    cv.set(x + dx, y + dy, shade(c, 0.9))

    # 腰带（下摆两行）
    front = cv.face_span("body", "front")
    fx, fy, fw, fh = front
    belt = p["belt"] or shade(p["pants"], 0.75)
    for dx in range(fw):
        cv.set(fx + dx, fy + fh - 2, belt)
        cv.set(fx + dx, fy + fh - 1, shade(belt, 0.85))

    # 领口
    neck = shade(p["skin"], 0.92)
    for dx in range(2, fw - 2):
        cv.set(fx + dx, fy, neck)
        cv.set(fx + dx, fy + 1, shade(neck, 0.95))

    # 正面中心一道布料暗折（避免大平板）
    for dy in range(3, fh - 3):
        c = cv.get(fx + fw // 2, fy + dy)
        if c[3] > 0:
            cv.set(fx + fw // 2, fy + dy, shade(c, 0.94))


def draw_head(cv, p):
    """头部：脸（眼/眉/嘴）+ 头发 + 耳朵阴影。"""
    skin, hair = p["skin"], p["hair_color"]
    fill_noisy(cv, "head", skin, amp=0.05, seed=p["seed"] + 21, density=14)

    # 侧面/后面压暗一点
    for face in ("right", "left"):
        x, y, w, h = cv.face_span("head", face)
        for dy in range(h):
            for dx in range(w):
                c = cv.get(x + dx, y + dy)
                if c[3] > 0:
                    cv.set(x + dx, y + dy, shade(c, 0.93))

    # 下巴阴影（front 最下两行）
    x, y, w, h = cv.face_span("head", "front")
    for dx in range(w):
        for dy in (h - 1, h - 2):
            c = cv.get(x + dx, y + dy)
            if c[3] > 0:
                cv.set(x + dx, y + dy, shade(c, 0.9))

    # 五官（8×8 的脸，眼在第 3~4 行）
    eye_c = p["eye_color"]
    ex1, ex2 = 1, 5          # 左右眼的起始 x（含眼白/瞳孔两格）
    ey = 3
    for ex in (ex1, ex2):
        cv.set(x + ex, y + ey, shade(eye_c, 1.25))       # 外侧亮（眼白感）
        cv.set(x + ex + 1, y + ey, eye_c)                # 瞳孔
        cv.set(x + ex, y + ey + 1, shade(eye_c, 0.8))
        cv.set(x + ex + 1, y + ey + 1, shade(eye_c, 0.85))
    # 眉毛
    brow = shade(hair, 0.85)
    for ex in range(ex1, ex2 + 2):
        cv.set(x + ex, y + ey - 1, brow)
    # 嘴
    mouth = shade(skin, 0.72)
    for dx in range(3, 5):
        cv.set(x + dx, y + 6, mouth)
    # 鼻影
    cv.set(x + 4, y + 5, shade(skin, 0.88))

    # 前额被头发遮住两行
    if p["hair"] != "bald":
        for dx in range(w):
            for dy in range(0, 1):
                cv.set(x + dx, y + dy, hair)

    # 耳朵（侧面靠前）
    for face in ("right", "left"):
        fx, fy, fw, fh = cv.face_span("head", face)
        ear = shade(skin, 0.88)
        cv.rect(fx + fw // 2 - 1, fy + 4, 2, 2, ear)


def draw_hair(cv, p):
    """头发：画在**外层**（帽子层）。none/bald 不画。"""
    style = p["hair"]
    if style in ("none", "bald"):
        return
    hair = p["hair_color"]
    hair_d = shade(hair, 0.82)
    hair_l = shade(hair, 1.12)

    def ov(part, face):
        return cv.face_span(part, face, layer="overlay")

    # 所有发型共同：头顶 + 后脑 + 两侧上半 + 额头刘海
    for face, base_c in (("top", hair_l), ("back", hair_d),
                         ("right", hair), ("left", hair), ("front", hair)):
        x, y, w, h = ov("head", face)
        cv.rect(x, y, w, h, base_c)

    # 前脸：只留下刘海（上面 N 行），下面清空露出脸
    x, y, w, h = ov("head", "front")
    fringe = {"short": 1, "bob": 2, "long": 2, "ponytail": 1, "spiky": 1}[style]
    for dy in range(fringe, h):
        for dx in range(w):
            cv.set(x + dx, y + dy, (0, 0, 0, 0))
    # 刘海做点锯齿，别是一刀平
    rnd = random.Random(p["seed"] + 31)
    for dx in range(w):
        if rnd.random() < 0.4:
            cv.set(x + dx, y + fringe, (0, 0, 0, 0))
    # 鬓角：只盖住最外侧各一列，且从第 2 行起（不糊脸）
    if style in ("bob", "long"):
        for dy in range(fringe + 1, h):
            cv.set(x, y + dy, hair_d)
            cv.set(x + w - 1, y + dy, hair_d)

    # 侧面：短发只盖上 2/3，长发盖满
    cover = {"short": 4, "bob": 5, "long": 7, "ponytail": 4, "spiky": 3}[style]
    for face in ("right", "left"):
        fx, fy, fw, fh = ov("head", face)
        for dy in range(cover, fh):
            for dx in range(fw):
                cv.set(fx + dx, fy + dy, (0, 0, 0, 0))

    # 头顶加高光
    tx, ty, tw, th = ov("head", "top")
    rnd = random.Random(p["seed"] + 41)
    for dy in range(th):
        for dx in range(tw):
            if rnd.random() < 0.18:
                cv.set(tx + dx, ty + dy, hair_l)

    # ── 长发垂肩：画在 **body 的 overlay** 上是游戏里的正解 ──
    #   ⚠️ 不要去"头 overlay 下方"补 —— 头 overlay 只有 8×8，往下那些格子
    #   属于 **body base 的 top/bottom**，画上去会盖掉躯干自己的贴图（实测踩过）。
    if style in ("bob", "long", "ponytail"):
        depth = {"bob": 2, "long": 5, "ponytail": 2}[style]
        # 后脑垂发：body overlay / back 的上部
        bx, by, bw, bh = ov("body", "back")
        for dy in range(min(depth, bh)):
            for dx in range(bw):
                cv.set(bx + dx, by + dy,
                       hair_d if rnd.random() < 0.7 else hair)
        # 前胸两缕（只在两侧，别糊住衣服）
        fx, fy, fw, fh = ov("body", "front")
        for dy in range(min(depth, fh)):
            for dx in (0, 1, fw - 2, fw - 1):
                cv.set(fx + dx, fy + dy, hair_d)
        # 两侧垂发：body overlay / right|left 的上部
        for face in ("right", "left"):
            sx, sy, sw, sh = ov("body", face)
            for dy in range(min(depth, sh)):
                for dx in range(sw):
                    cv.set(sx + dx, sy + dy, hair_d if (dx + dy) % 3 else hair)

    # 双马尾：后脑两侧各加一束（贴在 body overlay back 的两角）
    if style == "ponytail":
        bx, by, bw, bh = ov("body", "back")
        for side, cx in (("l", 1), ("r", bw - 2)):
            for dy in range(7):
                for dx in range(2):
                    cv.set(bx + cx + dx, by + dy, hair if dy % 2 else hair_d)


def draw_arms(cv, p):
    """手臂：袖子 + 手（露出皮肤）。"""
    sleeve, skin = p["shirt"], p["skin"]
    for part in ("right_arm", "left_arm"):
        fill_noisy(cv, part, sleeve, amp=0.07, seed=p["seed"] + 51, density=20)
        # 手腕往下 3 行露出皮肤（手）
        x, y, w, h = cv.face_span(part, "front")
        for dy in range(h - 3, h):
            for dx in range(w):
                cv.set(x + dx, y + dy, skin)
        # 侧面同步
        for face in ("right", "left", "back"):
            fx, fy, fw, fh = cv.face_span(part, face)
            for dy in range(fh - 3, fh):
                for dx in range(fw):
                    cv.set(fx + dx, fy + dy, skin)
        # 底部（手心/袖口截面）
        bx, by, bw, bh = cv.face_span(part, "bottom")
        cv.rect(bx, by, bw, bh, shade(skin, 0.9))
        # 袖口一道深色
        for face in ("front", "right", "left", "back"):
            fx, fy, fw, fh = cv.face_span(part, face)
            cc = shade(sleeve, 0.78)
            for dx in range(fw):
                cv.set(fx + dx, fy + fh - 4, cc)
        # 侧面压暗
        for face in ("right", "left"):
            fx, fy, fw, fh = cv.face_span(part, face)
            for dy in range(fh):
                for dx in range(fw):
                    c = cv.get(fx + dx, fy + dy)
                    if c[3] > 0:
                        cv.set(fx + dx, fy + dy, shade(c, 0.9))


def draw_legs(cv, p):
    """腿：裤子 + 鞋。"""
    pants, shoe = p["pants"], p["shoe"]
    for part in ("right_leg", "left_leg"):
        fill_noisy(cv, part, pants, amp=0.06, seed=p["seed"] + 61, density=18)
        # 鞋：底部 3 行
        for face in ("front", "right", "left", "back"):
            fx, fy, fw, fh = cv.face_span(part, face)
            for dy in range(fh - 3, fh):
                for dx in range(fw):
                    cv.set(fx + dx, fy + dy, shoe)
        # 鞋底
        bx, by, bw, bh = cv.face_span(part, "bottom")
        cv.rect(bx, by, bw, bh, shade(shoe, 0.7))
        # 裤缝（正面中间一列压暗）
        fx, fy, fw, fh = cv.face_span(part, "front")
        for dy in range(fh - 3):
            c = cv.get(fx + fw // 2, fy + dy)
            if c[3] > 0:
                cv.set(fx + fw // 2, fy + dy, shade(c, 0.92))
        # 侧面压暗
        for face in ("right", "left"):
            sx, sy, sw, sh = cv.face_span(part, face)
            for dy in range(sh):
                for dx in range(sw):
                    c = cv.get(sx + dx, sy + dy)
                    if c[3] > 0:
                        cv.set(sx + dx, sy + dy, shade(c, 0.9))


# ── 配件（全画在**外层**）─────────────────────────────────────

def acc_glasses(cv, p):
    """眼镜：横跨两眼的一条带 + 镜片。"""
    x, y, w, h = cv.face_span("head", "front", layer="overlay")
    frame = p["glasses_color"]
    lens = (60, 70, 90, 150)
    ey = 3
    for dx in range(w):
        cv.set(x + dx, y + ey - 1, frame)
        cv.set(x + dx, y + ey + 2, frame)
    for ex in (1, 5):
        for dx in range(2):
            cv.set(x + ex + dx, y + ey, lens)
            cv.set(x + ex + dx, y + ey + 1, lens)
    cv.set(x + 0, y + ey, frame)
    cv.set(x + w - 1, y + ey, frame)
    # 镜腿：绕到两侧
    for face in ("right", "left"):
        fx, fy, fw, fh = cv.face_span("head", face, layer="overlay")
        for dx in range(fw):
            for dy in range(ey, ey + 3):
                cv.set(fx + dx, fy + dy, frame)


def acc_hat(cv, p):
    """帽子：头顶 + 一圈帽檐（外层头）。"""
    c = p["hat_color"]
    x, y, w, h = cv.face_span("head", "top", layer="overlay")
    cv.rect(x, y, w, h, c)
    band = shade(c, 0.75)
    for face in ("front", "right", "left", "back"):
        fx, fy, fw, fh = cv.face_span("head", face, layer="overlay")
        for dy in range(2):
            for dx in range(fw):
                cv.set(fx + dx, fy + dy, c if dy == 0 else band)


def acc_earrings(cv, p):
    """耳饰：两侧各一颗亮点。"""
    c = p["metal"]
    for face in ("right", "left"):
        fx, fy, fw, fh = cv.face_span("head", face, layer="overlay")
        cv.set(fx + fw // 2 - 1, fy + 6, c)
        cv.set(fx + fw // 2 - 1, fy + 7, shade(c, 0.8))


def acc_scarf(cv, p):
    """围巾：绕脖子一圈（外层 body 顶行）+ 垂在前胸。"""
    c = p["scarf_color"]
    for face in ("front", "back", "right", "left"):
        fx, fy, fw, fh = cv.face_span("body", face, layer="overlay")
        for dx in range(fw):
            for dy in range(2):
                cv.set(fx + dx, fy + dy, c if dy == 0 else shade(c, 0.85))
    # 前胸垂下一段
    fx, fy, fw, fh = cv.face_span("body", "front", layer="overlay")
    for dy in range(2, 6):
        for dx in range(fw // 2 - 1, fw // 2 + 1):
            cv.set(fx + dx, fy + dy, shade(c, 0.9))


def acc_jacket(cv, p):
    """夹克：外层躯干 + 袖子，中间敞开露出内层。"""
    c = p["jacket_color"]
    for face in ("back", "right", "left", "top"):
        x, y, w, h = cv.face_span("body", face, layer="overlay")
        cv.rect(x, y, w, h, c)
    x, y, w, h = cv.face_span("body", "front", layer="overlay")
    cv.rect(x, y, w, h, c)
    # 前面中间敞开两列
    for dy in range(h):
        cv.set(x + w // 2 - 1, y + dy, (0, 0, 0, 0))
        cv.set(x + w // 2, y + dy, (0, 0, 0, 0))
    # 袖子（外层手臂）
    for part in ("right_arm", "left_arm"):
        for face in ("front", "back", "right", "left", "top"):
            ax, ay, aw, ah = cv.face_span(part, face, layer="overlay")
            cv.rect(ax, ay, aw, ah, c)
        for face in ("front", "back", "right", "left"):
            ax, ay, aw, ah = cv.face_span(part, face, layer="overlay")
            for dy in range(ah - 3, ah):
                for dx in range(aw):
                    cv.set(ax + dx, ay + dy, (0, 0, 0, 0))     # 露出下面的手


def acc_cape(cv, p):
    """披风：只画外层躯干的 back 面（游戏里披风是独立贴图，这里是"贴背披风"效果）。"""
    c = p["cape_color"]
    x, y, w, h = cv.face_span("body", "back", layer="overlay")
    cv.rect(x, y, w, h, c)
    for face in ("top",):
        tx, ty, tw, th = cv.face_span("body", face, layer="overlay")
        cv.rect(tx, ty, tw, th, shade(c, 0.85))


def acc_belt(cv, p):
    """腰带 + 扣（外层躯干下两行）。"""
    c = p["belt"] or shade(p["pants"], 0.7)
    for face in ("front", "back", "right", "left"):
        fx, fy, fw, fh = cv.face_span("body", face, layer="overlay")
        for dx in range(fw):
            cv.set(fx + dx, fy + fh - 2, c)
            cv.set(fx + dx, fy + fh - 1, shade(c, 0.85))
    fx, fy, fw, fh = cv.face_span("body", "front", layer="overlay")
    cv.set(fx + fw // 2, fy + fh - 2, p["metal"])
    cv.set(fx + fw // 2 - 1, fy + fh - 1, shade(p["metal"], 0.8))


ACCESSORIES = {
    "glasses": (acc_glasses, "眼镜（需要 glasses_color）"),
    "hat": (acc_hat, "帽子（需要 hat_color）"),
    "earrings": (acc_earrings, "耳饰（需要 metal）"),
    "scarf": (acc_scarf, "围巾（需要 scarf_color）"),
    "jacket": (acc_jacket, "夹克（需要 jacket_color）"),
    "cape": (acc_cape, "披风（需要 cape_color）"),
    "belt": (acc_belt, "腰带（需要 belt）"),
}


# ══════════════════════════════════════════════════════════════
# 预设
# ══════════════════════════════════════════════════════════════

PRESETS = {
    "经典": dict(skin_tone="#D8A878", hair_color="#4A3423", hair="short",
              eye_color="black", shirt="#00838F", pants="#3B3B8F",
              shoe="#4A3A2A", metal="#C8A83C"),
    "少女": dict(model="slim", skin_tone="#F5DCC4", hair_color="#2B2B38",
              hair="long", eye_color="purple", shirt="#7A6FA8", pants="#3A3A52",
              shoe="#2A2A38", accessories="scarf,earrings", scarf_color="#C85A7A",
              metal="#E8D48A"),
    "工装": dict(skin_tone="#C89468", hair_color="#1E1E24", hair="bob",
              eye_color="brown", shirt="#4A5A3A", pants="#3A4030",
              shoe="#2A2418", accessories="belt,jacket", jacket_color="#6A5A3A",
              belt="#3A2A1A", metal="#A88A3A"),
    "法师": dict(skin_tone="#E8C8A8", hair_color="#6A4A8A", hair="long",
              eye_color="amber", shirt="#3A2A5A", pants="#2A1A3A",
              shoe="#1A1220", accessories="cape,hat", cape_color="#5A3A7A",
              hat_color="#3A2A5A", metal="#C8A83C"),
    "机械师": dict(skin_tone="#D0A070", hair_color="#3A3A44", hair="short",
              eye_color="gray", shirt="#B06020", pants="#3A3A46",
              shoe="#2A2418", accessories="glasses,jacket",
              glasses_color="#C8C8D0", jacket_color="#5A4A32",
              metal="#B0B0B8"),
    "黑长直": dict(model="slim", skin_tone="#F2DCC8", hair_color="#14141C",
              hair="long", eye_color="purple", shirt="#2A2A38", pants="#1A1A24",
              shoe="#101018", accessories="scarf", scarf_color="#8A3A4A",
              metal="#C0C0C8"),
}


def resolve(args):
    """把命令行参数解析成一份完整配置。"""
    p = dict(
        model=args.model or "classic",
        skin="#D8A878" if not args.skin_tone else None,
        skin_tone=args.skin_tone or "#D8A878",
        hair_color=args.hair_color or "#4A3423",
        hair=args.hair or "short",
        eye_color=EYE_COLORS[args.eyes] if args.eyes in EYE_COLORS else (args.eyes or "#1A1A22"),
        shirt=args.shirt or "#00838F",
        pants=args.pants or "#3B3B8F",
        shoe=args.shoe or "#4A3A2A",
        belt=args.belt,
        metal=args.metal or "#C8A83C",
        jacket_color=args.jacket_color or "#6A5A3A",
        scarf_color=args.scarf_color or "#C85A7A",
        hat_color=args.hat_color or "#3A2A5A",
        cape_color=args.cape_color or "#5A3A7A",
        glasses_color=args.glasses_color or "#C8C8D0",
        seed=args.seed,
    )
    if args.preset:
        if args.preset not in PRESETS:
            raise SystemExit(f"没有预设 {args.preset}；可选：{', '.join(PRESETS)}")
        pr = PRESETS[args.preset]
        for k, v in pr.items():
            if k == "accessories":
                p["accessories"] = [a for a in v.split(",") if a]
            else:
                p[k] = v
        if "skin_tone" in pr:
            p["skin_tone"] = pr["skin_tone"]

    if args.accessories is not None:
        p["accessories"] = [a for a in args.accessories.split(",") if a]
    p.setdefault("accessories", [])

    # ★ 颜色统一在**最后**转换 —— 预设分支会覆盖上面的初值，
    #   早先版本在预设之前转，被预设的字符串（"purple"）盖回，报 unpack 错误。
    p["eye_color"] = hx(p["eye_color"], named=EYE_COLORS)
    p["skin"] = hx(p["skin_tone"])
    for k in ("hair_color", "shirt", "pants", "shoe", "metal", "jacket_color",
              "scarf_color", "hat_color", "cape_color", "glasses_color"):
        p[k] = hx(p[k])
    p["belt"] = hx(p["belt"]) if p["belt"] else None

    for a in p["accessories"]:
        if a not in ACCESSORIES:
            raise SystemExit(f"没有配件 {a}；可选：{', '.join(ACCESSORIES)}")
    return p


# ══════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════

def generate(p):
    cv = Canvas()
    draw_head(cv, p)
    draw_body(cv, p)
    draw_arms(cv, p)
    draw_legs(cv, p)
    draw_hair(cv, p)
    for a in p["accessories"]:
        ACCESSORIES[a][0](cv, p)

    # ★ 硬性收尾：基础层绝不透明（游戏把基础层按不透明画，留 alpha 会变黑块）
    #   但判定窗口要按模型要求处理：slim 必须清空，classic 必须填满
    _enforce_base_opaque(cv)
    _enforce_model_window(cv, p["model"])
    for face in ("front", "back", "right", "left", "top", "bottom"):
        pass
    for part in reg.PART_SIZE:
        add_edge_shade(cv, part, strength=0.9)
    return cv.im


def _enforce_base_opaque(cv):
    """基础层全填：任何仍透明的基础层像素用邻域色或兜底色补上。"""
    fallback = (200, 160, 130, 255)
    for part, face, x, y, w, h in [(r[0], r[1], r[3], r[4], r[5], r[6])
                                   for r in reg.all_regions(with_overlay=False)]:
        for dy in range(h):
            for dx in range(w):
                if cv.get(x + dx, y + dy)[3] == 0:
                    # 找邻域第一个不透明的
                    got = None
                    for ox, oy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                        cx, cy = x + dx + ox, y + dy + oy
                        if 0 <= cx < 64 and 0 <= cy < 64:
                            c = cv.get(cx, cy)
                            if c[3] > 0:
                                got = c
                                break
                    cv.set(x + dx, y + dy, got or fallback)


def _enforce_model_window(cv, model):
    """slim 必须清空判定窗口，classic 必须填满 —— 否则游戏会自动判成另一个模型。"""
    sub = _common_arm_color(cv, model)
    for x, y in reg.DETECT_WINDOW:
        if model == "slim":
            cv.set(x, y, (0, 0, 0, 0))
        else:
            if cv.get(x, y)[3] == 0 or True:
                cv.set(x, y, sub)


def _common_arm_color(cv, model):
    """取右臂正面一个像素的颜色，作为补窗口的基准。"""
    x, y, w, h = cv.face_span("right_arm", "front")
    for dy in range(h):
        for dx in range(w):
            c = cv.get(x + dx, y + dy)
            if c[3] > 0:
                return c
    return (60, 90, 160, 255)


def main():
    ap = argparse.ArgumentParser(
        description="生成 64×64 Minecraft 玩家皮肤（程序化像素画）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("═══ 用法 ═══")[-1])
    ap.add_argument("--preset", help=f"用内置预设：{', '.join(PRESETS)}")
    ap.add_argument("--model", choices=["classic", "slim"], help="体型（默认 classic）")
    ap.add_argument("--skin-tone", help="肤色 #RRGGBB")
    ap.add_argument("--hair-color", help="发色 #RRGGBB")
    ap.add_argument("--hair", choices=HAIR_STYLES, help="发型")
    ap.add_argument("--eyes", help=f"瞳色：{'/'.join(EYE_COLORS)} 或 #RRGGBB")
    ap.add_argument("--shirt", help="上衣 #RRGGBB")
    ap.add_argument("--pants", help="裤子 #RRGGBB")
    ap.add_argument("--shoe", help="鞋 #RRGGBB")
    ap.add_argument("--belt", help="腰带 #RRGGBB")
    ap.add_argument("--metal", help="金属色（扣/耳饰）#RRGGBB")
    ap.add_argument("--accessories", help=f"配件，逗号分隔：{','.join(ACCESSORIES)}")
    ap.add_argument("--jacket-color", help="夹克色")
    ap.add_argument("--scarf-color", help="围巾色")
    ap.add_argument("--hat-color", help="帽子色")
    ap.add_argument("--cape-color", help="披风色")
    ap.add_argument("--glasses-color", help="镜框色")
    ap.add_argument("--seed", type=int, default=42, help="噪点随机种子（同一种子结果可复现）")
    ap.add_argument("-o", "--out", help="输出 PNG 路径")
    ap.add_argument("--preview", help="同时输出预览图（六面展开 + 等距 3D）")
    ap.add_argument("--list", action="store_true", help="列出预设/发型/瞳色/配件后退出")
    args = ap.parse_args()

    if args.list:
        print("预设：")
        for k, v in PRESETS.items():
            print(f"  {k:6s} {v.get('model','classic'):7s} 发{v.get('hair','short'):8s} "
                  f"配件={v.get('accessories','—')}")
        print(f"\n发型：{', '.join(HAIR_STYLES)}")
        print(f"瞳色：{', '.join(EYE_COLORS)}")
        print(f"配件：{', '.join(ACCESSORIES)}")
        return 0

    p = resolve(args)
    img = generate(p)

    if not args.out:
        args.out = os.path.join(os.getcwd(), "skin.png")
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    img.save(args.out)

    # 顺带报一下判定结果，让用户知道该选哪个模型
    got = reg.detect_model(img)
    sz = img.size
    print(f"已生成：{args.out}  ({sz[0]}×{sz[1]})")
    print(f"  体型设定：{p['model']}")
    print(f"  判定结果：{got}" + ("  ✓ 一致" if got == p["model"] else "  ✗ 不一致（上传时会掉臂宽）"))
    print(f"  发型/配件：{p['hair']}" + (f" + {', '.join(p['accessories'])}" if p["accessories"] else ""))

    if args.preview:
        import importlib
        pv = importlib.import_module("皮肤预览")
        pv.render(img, args.preview, model=p["model"])
        print(f"预览图：{args.preview}")

    return 0 if got == p["model"] else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
皮肤体检 / 转换 —— 交付前把关 + 旧版升级。

═══ 判据分层（这是本 skill 一贯的做法）═══
  「能打开」 ⊂ 「尺寸对」 ⊂ 「基础层铺满」 ⊂ 「判定窗口与所选模型一致」 ⊂ 「游戏里真能穿上」
本脚本查到第三、四层；第五层（上传到 Mojang 后客户端是否显示）没法离线验，
只能靠 `--report` 把该选哪个模型、去哪上传讲清楚。

检查项（`--check`）：
  ① 尺寸必须 64×64（或 64×32 旧版，会被提示转换）
  ② 必须是真 PNG（不是改名成 .png 的 JPEG）  ← 这是上传失败最常见的原因
  ③ 基础层每个面必须铺满不透明（留透明 → 游戏里渲染成黑块）
     ⚠️ 例外：slim 的第四列本来就该透明（slim 不采样那列）
  ④ 判定窗口与声明模型是否一致（不一致 → 上传后掉臂宽）
  ⑤ 外层是否有半透明像素（Java 1.9+ 允许；Bedrock 会丢弃 alpha≤25）
  ⑥ 未使用的区域是否残留脏像素（容易在新版 UV 下突然显形）

转换（`--convert`）：
  64×32 → 64×64：右侧肢体镜像补全（旧版格式没有左臂/左腿，只能镜像，
  所以**旧皮必然左右对称** —— 这是格式限制，不是选择）

用法：
  python 皮肤校验.py skin.png                    # 体检
  python 皮肤校验.py skin.png --model slim       # 按 slim 检查
  python 皮肤校验.py legacy.png --convert -o new.png
  python 皮肤校验.py --dir <皮肤目录> --md 报告.md
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import 皮肤规范 as reg


def is_real_png(path):
    """按文件头判真 PNG。改名 JPEG 是这个格式最经典的坑。"""
    try:
        with open(path, "rb") as f:
            sig = f.read(8)
    except OSError as e:
        return False, f"读不了：{e}"
    if sig == b"\x89PNG\r\n\x1a\n":
        return True, "PNG"
    if sig[:2] == b"\xff\xd8":
        return False, "JPEG（只是改了扩展名，上传会被拒 / 透明区会变黑）"
    if sig[:3] == b"GIF":
        return False, "GIF（不支持）"
    return False, f"未知格式（文件头 {sig[:4]!r}）"


def check(img, path="<内存>", model=None, strict=False, strict_info=False):
    """返回 (错误表, 警告表, 信息表)。错误=不能用，警告=用起来有问题。"""
    errs, warns, infos = [], [], []

    if img.size == (64, 32):
        warns.append("是 64×32 旧版格式：只有单层、左右镜像、无 slim。"
                     "建议 `--convert` 升到 64×64")
        model = "classic"
    elif img.size != (64, 64):
        errs.append(f"尺寸 {img.size[0]}×{img.size[1]} 非法；Java 只收 64×64 或 64×32")
        return errs, warns, infos

    px = img.convert("RGBA").load()

    # ③ 基础层铺满（slim 第四列豁免）
    exempt = set()
    if model != "classic":
        exempt = set(reg.DETECT_WINDOW)
    holed = []
    for part, layer, face, x, y, w, h in reg.all_regions(with_overlay=False):
        if y + h > img.size[1]:
            continue
        n = sum(1 for dy in range(h) for dx in range(w)
                if px[x + dx, y + dy][3] > 0 or (x + dx, y + dy) in exempt)
        if n < w * h:
            holed.append(f"{part}/{face}({w*h-n}格)")
    if holed:
        errs.append("基础层有透明缺口（游戏会把基础层按不透明画 → 显示成黑块）："
                    + "、".join(holed[:8]) + ("…" if len(holed) > 8 else ""))

    # ④ 判定窗口 vs 声明模型
    detected = reg.detect_model(img)
    if model and detected and detected != model:
        errs.append(f"判定窗口算出来是 **{detected}**，但声明用 **{model}** —— "
                    f"上传后会自动变成 {detected}，臂宽会差一格")
    elif detected:
        infos.append(f"按官方判据这张皮是 **{detected}**（上传时请选这个模型）")

    # ⑤ 外层半透明
    semi = 0
    for part, layer, face, x, y, w, h in reg.all_regions(with_overlay=True):
        if layer != "overlay" or y + h > img.size[1]:
            continue
        for dy in range(h):
            for dx in range(w):
                a = px[x + dx, y + dy][3]
                if 0 < a < 255:
                    semi += 1
    if semi:
        warns.append(f"外层有 {semi} 个半透明像素：Java 1.9+ 正常，"
                     f"但 **Bedrock 会直接丢掉 alpha≤25 的像素**，跨版本用会有差异")

    # ⑥ 空档区脏像素 —— ⚠️ 只做**低置信提示**，不算错误也不算警告
    #    踩过的坑：第一版把"不在我六面表里的格子"全判成空档，结果 64×32 旧皮的
    #    帽子层（旧版 head overlay 布局与新版不同）和 (0,0)-(7,7) 一带被误报 544 格。
    #    官方对未使用区域的要求只是"应当透明或保持原样"——**不是硬性错误**。
    #    所以这里只在**明确超出所有已知区域**时才提示，且不改退出码。
    if img.size == (64, 64) and strict_info:
        used = set()
        for part, layer, face, x, y, w, h in reg.all_regions(with_overlay=True):
            for dy in range(h):
                for dx in range(w):
                    used.add((x + dx, y + dy))
        dirty = [(x, y) for y in range(64) for x in range(64)
                 if (x, y) not in used and px[x, y][3] > 0]
        if len(dirty) > 200:
            infos.append(f"有 {len(dirty)} 个像素在 UV 空档区（如 {dirty[0]}）。"
                         f"官方对未使用区域的要求只是「应当透明或保持原样」，不是错误；"
                         f"但若某些版本 UV 变动，这些残留可能突然显形")

    return errs, warns, infos


def convert_legacy(img):
    """64×32 → 64×64：右侧肢体镜像补到左下（旧版格式的唯一做法）。"""
    if img.size != (64, 32):
        raise ValueError(f"只支持 64×32 → 64×64，收到 {img.size}")
    src = img.convert("RGBA")
    out = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    out.alpha_composite(src, (0, 0))

    # 右臂 (40,16,16,16) → 左臂 (32,48)，水平镜像
    arm = src.crop((40, 16, 56, 32)).transpose(Image.FLIP_LEFT_RIGHT)
    out.alpha_composite(arm, (32, 48))
    # 右腿 (0,16,16,16) → 左腿 (16,48)，水平镜像
    leg = src.crop((0, 16, 16, 32)).transpose(Image.FLIP_LEFT_RIGHT)
    out.alpha_composite(leg, (16, 48))

    # 补外层（旧版没有外层，用基础层复制，让新格式不空着）
    for part, (mode, amt) in reg.OVERLAY_OFFSET.items():
        for face, (x, y, w, h) in reg.PART_FACES[part].items():
            if y + h > 32 and y < 32:
                continue
            try:
                tile = out.crop((x, y, x + w, y + h))
            except Exception:
                continue
            ox, oy, _, _ = reg.PART_FACES_OVERLAY[part][face]
            out.alpha_composite(tile, (ox, oy))
    return out


def main():
    ap = argparse.ArgumentParser(
        description="64×64 皮肤体检 / 旧版转换",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("═══ 判据分层")[0])
    ap.add_argument("skin", nargs="?", help="皮肤 PNG")
    ap.add_argument("--dir", help="批量体检一个目录")
    ap.add_argument("--model", choices=["classic", "slim"], help="按该模型检查")
    ap.add_argument("--convert", action="store_true", help="64×32 → 64×64")
    ap.add_argument("-o", "--out", help="转换输出路径")
    ap.add_argument("--md", help="同时写一份 markdown 报告")
    ap.add_argument("--strict", action="store_true", help="警告也算失败（退出码 1）")
    ap.add_argument("--strict-info", action="store_true",
                    help="额外提示 UV 空档区的残留像素（误报率较高，默认关）")
    args = ap.parse_args()

    targets = []
    if args.dir:
        d = Path(args.dir)
        if not d.is_dir():
            print(f"不是目录：{args.dir}", file=sys.stderr)
            return 2
        targets = sorted([p for p in d.iterdir()
                          if p.suffix.lower() in (".png", ".jpg", ".jpeg")])
        if not targets:
            print(f"目录里没有图片：{args.dir}", file=sys.stderr)
            return 2
    elif args.skin:
        targets = [Path(args.skin)]
    else:
        ap.error("给一个皮肤文件，或用 --dir")

    lines = ["# 皮肤体检报告", ""]
    bad = 0
    for p in targets:
        if not p.exists():
            print(f"❌ 文件不存在：{p}", file=sys.stderr)
            bad += 1
            continue

        real, how = is_real_png(str(p))
        print(f"\n=== {p.name} ===")
        lines += [f"## {p.name}", ""]
        if not real:
            print(f"  ❌ 不是有效 PNG：{how}")
            lines.append(f"- ❌ 不是有效 PNG：{how}")
            bad += 1
            continue

        img = Image.open(p)
        if args.convert:
            if img.size != (64, 32):
                print(f"  ⚠️ {img.size} 不需要转换（--convert 只处理 64×32）")
                continue
            out = Image.open(p).convert("RGBA")
            newimg = convert_legacy(out)
            dst = args.out or str(p.with_name(p.stem + "_64x64.png"))
            newimg.save(dst)
            print(f"  ✓ 已转换 → {dst}（左臂/左腿由右侧镜像补全，"
                  f"所以旧皮转出来必然左右对称）")
            lines += [f"- ✓ 已转换 → `{dst}`（镜像补全，必然左右对称）", ""]
            continue

        errs, warns, infos = check(img, str(p), model=args.model, strict=args.strict,
                                   strict_info=args.strict_info)
        for e in errs:
            print(f"  ❌ {e}")
        for w in warns:
            print(f"  ⚠️  {w}")
        for i in infos:
            print(f"  · {i}")
        if not errs and not warns:
            print("  ✓ 干净")
        lines += [f"- {'❌' if e else ''} {e}" for e in errs]
        lines += [f"- ⚠️ {w}" for w in warns]
        lines += [f"- {i}" for i in infos]
        lines.append("")
        if errs or (args.strict and warns):
            bad += 1

    if args.md:
        Path(args.md).write_text("\n".join(lines), encoding="utf-8")
        print(f"\n报告：{args.md}")

    print(f"\n结论：{len(targets)-bad}/{len(targets)} 通过")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

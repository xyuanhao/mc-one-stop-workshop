#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""造样本皮肤，双向验证 `皮肤规范.detect_model` 的判据（坏样本必报 + 好样本零误杀）。

样本：
  A_classic  判定窗口全填        → 必须判 classic
  B_slim     判定窗口全透明      → 必须判 slim
  C_stray5   只填 5 个（阈值下沿）→ 必须判 slim（容差生效，描边不误判）
  D_stray6   只填 6 个（阈值上沿）→ 必须判 classic
  另验：64×32 旧版必须返回 None（不支持 slim）
"""
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import importlib
reg = importlib.import_module("皮肤规范")

OUT = Path("/tmp/skin_samples")
OUT.mkdir(exist_ok=True)

SKIN = (200, 160, 130, 255)
SHIRT = (60, 90, 160, 255)
PANTS = (40, 40, 60, 255)


def base_skin():
    """按 UV 表把六个部件的基础层全填成不透明（游戏要求基础层必须满）。"""
    im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    px = im.load()
    for part, faces in reg.PART_FACES.items():
        color = SKIN
        if part == "body":
            color = SHIRT
        elif "leg" in part:
            color = PANTS
        for face, (x, y, w, h) in faces.items():
            for dy in range(h):
                for dx in range(w):
                    px[x + dx, y + dy] = color
    return im, px


def build(mode):
    im, px = base_skin()
    for x, y in reg.DETECT_WINDOW:
        px[x, y] = (0, 0, 0, 0)          # 先清空判定窗口
    n = {"A_classic": 24, "B_slim": 0, "C_stray5": 5, "D_stray6": 6}[mode]
    for x, y in reg.DETECT_WINDOW[:n]:
        px[x, y] = SKIN
    return im


expect = {
    "A_classic": "classic",
    "B_slim": "slim",
    "C_stray5": "slim",
    "D_stray6": "classic",
}

ok = True
print("【判定判据双向验证】")
for mode, want in expect.items():
    p = OUT / f"{mode}.png"
    build(mode).save(p)
    got = reg.detect_model(Image.open(p))
    hit = got == want
    ok &= hit
    n = sum(1 for x, y in reg.DETECT_WINDOW
            if Image.open(p).convert("RGBA").load()[x, y][3] > 0)
    print(f"  {'✓' if hit else '✗'} {mode:10s} 窗口={n:2d}/24  期望={want:8s} 实得={got}")

# 64×32 旧版
legacy = Image.new("RGBA", (64, 32), SKIN)
lp = OUT / "E_legacy.png"
legacy.save(lp)
got = reg.detect_model(Image.open(lp))
hit = got is None
ok &= hit
print(f"  {'✓' if hit else '✗'} E_legacy   64×32 旧版      期望=None     实得={got}")

print("\n" + ("全部通过" if ok else "有失败项"))
sys.exit(0 if ok else 1)

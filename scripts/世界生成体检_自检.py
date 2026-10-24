#!/usr/bin/env python
"""世界生成体检 —— 判据的双向验证（坏样本必须报出 + 好样本零误杀）。

⭐ 为什么必须有这个文件
    「新增检测脚本要双向验证」是硬规矩：只测「它能通过」不算数，
    要证明「它真会报错」、且「它不会误杀正常产出」。
    本项目已有两次假阴性教训（判据太宽 / 路径解析静默为空）。

用法：python 世界生成体检_自检.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from importlib import import_module
# 文件名含中文，用 importlib 按路径加载
import importlib.util

_spec = importlib.util.spec_from_file_location(
    'worldgen_check',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '世界生成体检.py'))
W = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(W)

SECTIONS = 225          # 参与统计的区块数（合成样本用）


def mk(layers):
    """layers: {secY: {block: 占比}}，占比不足的部分补 air。

    返回体检脚本内部用的 per_y 结构。
    """
    per_y = {}
    for sy, mix in layers.items():
        c = {}
        c['__total__'] = SECTIONS
        used = 0
        for blk, frac in mix.items():
            n = int(round(frac * SECTIONS * 4096))
            c[blk] = n
            used += n
        c['air'] = SECTIONS * 4096 - used
        per_y[sy] = c
    return per_y


def sample_normal():
    """正常世界：地下岩石、地表草、地上空气，带一点洞穴水。"""
    return mk({
        4:  {'grass_block': 0.10, 'dirt': 0.06, 'stone': 0.04},   # y 64~79 有地表
        5:  {'grass_block': 0.02},
        3:  {'stone': 0.80, 'water': 0.05},                       # y 48~63 石头为主
        0:  {'stone': 0.88, 'deepslate': 0.05, 'water': 0.02},
        -1: {'stone': 0.60, 'deepslate': 0.35, 'water': 0.03},
        -4: {'deepslate': 0.99},                                  # 深部实心
        -10: {'deepslate': 0.99},
    })


def sample_normal_ocean():
    """正常海洋存档：海底在 y=40，其上有水；y<0 仍是岩石。"""
    return mk({
        4:  {'water': 0.90},                                      # 海面之上是水
        3:  {'water': 0.95},
        2:  {'water': 0.90},
        1:  {'water': 0.60, 'stone': 0.40},
        0:  {'stone': 0.99},
        -1: {'stone': 0.70, 'deepslate': 0.29},
        -4: {'deepslate': 0.99},
    })


def sample_flat_void():
    """坏样本 A：地形完全没生成（全空气）。"""
    return mk({0: {}, 4: {}, -4: {}})


def sample_no_surface():
    """坏样本 B：地下有水有石，但 y>=64 全空气 —— 地表消失。"""
    return mk({
        4:  {},                                                   # 全空气
        0:  {'stone': 0.85, 'water': 0.10},
        -1: {'stone': 0.90},
        -4: {'deepslate': 0.99},
    })


def sample_buried_ocean():
    """坏样本 C：y<0 的地下一半以上是水（本轮事故的签名）。"""
    return mk({
        4:  {},
        2:  {'water': 1.0},
        0:  {'water': 1.0},
        -1: {'water': 1.0},
        -3: {'water': 1.0},
        -4: {'water': 0.95, 'deepslate': 0.02},
    })


CASES = [
    # (名称, per_y, 期望有 problem, 期望有 warning)
    ('正常陆地世界', sample_normal(), False, False),
    ('正常海洋存档', sample_normal_ocean(), False, True),
    ('坏样本 A：全空气', sample_flat_void(), True, True),
    ('坏样本 B：没有地表', sample_no_surface(), False, True),
    ('坏样本 C：地下水淹没（本轮事故）', sample_buried_ocean(), True, True),
]


def main():
    print('=' * 74)
    print('世界生成体检 · 判据双向验证')
    print('=' * 74)
    print('判据标准：好样本不许出现 problem；坏样本必须至少报出 problem 或 warning')
    print('=' * 74)
    fails = 0
    for name, per_y, want_problem, want_warning in CASES:
        probs, warns, notes = W.diagnose(per_y, SECTIONS)
        got_problem, got_warning = bool(probs), bool(warns)
        bad = []
        if want_problem and not got_problem:
            bad.append('该报 problem 却没报')
        if not want_problem and got_problem:
            bad.append('**误杀**：不该有 problem 却报了')
        if want_warning and not got_warning and not got_problem:
            bad.append('该报 warning 却没报')
        if bad:
            fails += 1
            verdict = '✗ ' + '；'.join(bad)
        else:
            verdict = '✓'
        tag = '报出' if (want_problem or want_warning) else '通过'
        print(f'\n[{name}] 期望={tag}  {verdict}')
        for n in notes:
            print(f'    · {n}')
        for w in warns:
            print(f'    ?? {w}')
        for p in probs:
            print(f'    !! {p}')

    print()
    print('=' * 74)
    if fails:
        print(f'✗ {fails} / {len(CASES)} 个用例不符合预期')
        raise SystemExit(1)
    good = sum(1 for _, _, p, _ in CASES if not p)
    print(f'✓ 全部 {len(CASES)} 个用例符合预期'
          f'（好样本 {good} 个零误杀，坏样本 {len(CASES) - good} 个全部报出）')


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自检.py —— 全量脚本的「还能不能跑」体检（2026-09-15 加；数量动态统计，别在文档里写死）。

覆盖：A 语法 / B --help 与无参退出码 / C 脚本间交叉引用 / D 退出码约定 / E 无副作用。
用法：python 自检.py  （在 skill 根目录或任意位置均可）

为什么需要它：这个 skill 的脚本会随 MC/NeoForge 生态变化被反复改（SKILL.md 里
一堆「实测」「踩坑」就是这么来的）。改完一个脚本，很容易漏掉它和其它脚本的
接口约定（比如 模组依赖反查.py 要 import 整合包体检.py）。这个脚本一次跑完：

  A. 语法    —— 每个 .py 能不能 import（AST 解析 + 编译）
  B. 冒烟    —— 无参 / --help 调用能否正常退出（不吞异常、不裸 traceback）
  C. 交叉引用 —— 脚本之间互相 import 的是不是还在（改名/删除会静默失效）
  D. 退出码  —— 参数错误是否统一为 2（0=干净 / 1=发现问题 / 2=用法问题）
  E. 无副作用 —— 跑 --help / 无参不许往 skill 目录里落文件
  F. 交叉同步 —— 双 skill（MC一站式工坊 ↔ MC模组工坊）的 mod 公共资产是否已对齐
  G. 网络    —— 可选 `--net` 额外探测 Modrinth / MCIM 镜像可达性（默认跳过，不白等）
  H. 文档完整性 —— SKILL.md/references 的路径引用、§锚点、速查表登记、归档配对
              （实现在 经验沉淀.py --doctor；自我优化闭环的「验证」步）

用法：
  python 自检.py                       # 全部检查（不联网）
  python 自检.py --net                 # 额外测下载通道可达性
  python 自检.py --no-peer             # 跳过 F（单装一个 skill 时）
  python 自检.py --quiet               # 只打印问题
  python 自检.py --md 自检报告.md

退出码：0 = 全通过 / 1 = 有问题

⚠️ 本脚本**两个 skill 共用同一份**（靠 `HERE.parent.name` 取 skill 名，不硬编码）。
   改动它之后记得跑 `python scripts/交叉同步.py --sync --force` 把另一边也更新。
"""
import argparse
import ast
import io
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_NAME = HERE.parent.name          # MC一站式工坊 / MC模组工坊
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 期望「无参即打印用法并退出 2」的脚本（其余脚本无参时行为各异，只查不崩）
# 注：清单里某个脚本若在当前 skill 不存在，会自动跳过（两个 skill 的脚本集不同）
USAGE_EXIT2 = {
    "下载模组.py", "整合包体检.py", "任务书覆盖度.py", "任务书校验.py",
    "整合包验证.py", "检查工程.py", "版本号.py", "迁移检查.py", "扫区域文件.py",
    "任务书ID体检.py", "增量重打包.py", "任务书章节比对.py",
    "交叉同步.py", "json5语法自检.py", "游戏内测试.py", "多代理任务板.py",
}


def py_files():
    return sorted(p for p in HERE.glob("*.py") if p.name != Path(__file__).name)


def run(cmd, timeout=25, cwd=None):
    """跑一个子进程，返回 (returncode, stdout+stderr)。超时算失败。

    cwd 默认走**临时目录**（不是 HERE）——脚本里有「不传 -o 就写当前工作目录」的，
    从 HERE 起会把产物落进 skill 目录。临时目录跑实现两个目的：
    ① 冒烟测试不污染 skill ② 顺便验证脚本确实尊重工作目录。"""
    import tempfile
    if cwd is None:
        cwd = tempfile.gettempdir()
    try:
        r = subprocess.run([sys.executable] + cmd, cwd=str(cwd), capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return None, f"超时（>{timeout}s）—— 可能卡在网络请求上"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def check_syntax(files):
    """A. 语法：AST 解析 + compile。"""
    problems, details = [], []
    for f in files:
        try:
            src = io.open(f, encoding="utf-8").read()
            ast.parse(src, filename=str(f))
            compile(src, str(f), "exec")
            details.append(f"  ✓ {f.name}")
        except SyntaxError as e:
            problems.append(f"{f.name}: 语法错误 第{e.lineno}行 {e.msg}")
            details.append(f"  ✗ {f.name}: 第{e.lineno}行 {e.msg}")
        except Exception as e:
            problems.append(f"{f.name}: 无法读取/解析 {type(e).__name__}")
            details.append(f"  ✗ {f.name}: {type(e).__name__}: {e}")
    return problems, details


def check_smoke(files):
    """B. 冒烟：--help 必须成功；无参不能裸 traceback。"""
    problems, details = [], []
    for f in files:
        rc, out = run([str(f), "--help"])
        has_help_flag = "usage:" in out or "用法" in out
        if rc is None:
            problems.append(f"{f.name}: --help 超时")
            details.append(f"  ✗ {f.name}: --help 超时")
            continue
        traceback_in_out = "Traceback (most recent call last)" in out
        # 不带 argparse 的脚本用 __doc__ 当 help，rc=2 属正常设计
        if not has_help_flag and rc != 0 and not traceback_in_out:
            problems.append(f"{f.name}: --help 既没输出用法也没报错（rc={rc}）")
            details.append(f"  ✗ {f.name}: --help 无输出 rc={rc}")
        elif traceback_in_out and f.name not in ("模组依赖反查.py",):
            problems.append(f"{f.name}: --help 直接抛未捕获异常")
            details.append(f"  ✗ {f.name}: --help 抛异常")
        else:
            details.append(f"  ✓ {f.name}")

        # 无参：不允许裸 traceback（用户会看到一堆栈，不知道咋办）
        rc2, out2 = run([str(f)])
        if "Traceback (most recent call last)" in out2:
            problems.append(f"{f.name}: 无参调用抛未捕获异常（应打印用法并退出）")
            details.append(f"  ✗ {f.name}: 无参抛异常")
    return problems, details


def check_cross_imports(files):
    """C. 交叉引用：脚本 import 的本地模块是否都存在。"""
    problems, details = [], []
    local = {f.stem for f in files}
    for f in files:
        try:
            tree = ast.parse(io.open(f, encoding="utf-8").read())
        except Exception:
            continue
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                imported.add(node.module.split(".")[0])
        missing = {m for m in imported if m in KNOWN_LOCAL_MISSING and m not in local}
        if missing:
            problems.append(f"{f.name}: import 了不存在的本地模块 {sorted(missing)}")
            details.append(f"  ✗ {f.name}: 缺 {sorted(missing)}")
    if not details:
        details.append("  ✓ 无脚本间失效引用")
    return problems, details


# 这些名字若被 import 且同名 .py 不在目录里，就是引用失效
KNOWN_LOCAL_MISSING = {"整合包体检", "整合包验证", "任务书覆盖度", "下载模组", "模组依赖反查",
                       "rcon", "检查工程", "制作整合包", "还原templates", "实体模型预览"}


def check_exit_codes(files):
    """D. 退出码：参数/路径错误应为 2，不能和「发现问题」的 1 撞。"""
    problems, details = [], []
    for f in files:
        if f.name not in USAGE_EXIT2:
            continue
        rc, out = run([str(f), "__definitely_missing_path__"])
        if rc != 2:
            problems.append(f"{f.name}: 路径不存在时退出码 {rc}（应为 2，避免与「有问题」的 1 混淆）")
            details.append(f"  ✗ {f.name}: rc={rc}")
        else:
            details.append(f"  ✓ {f.name}")
        if "Traceback" in out:
            problems.append(f"{f.name}: 路径不存在时抛裸异常")
    return problems, details


def check_peer_sync():
    """F. 交叉同步：两个 MC skill 的 mod 公共资产是否已对齐。

    为什么放进自检：这套「一边改、另一边也得跟着改」的约定，光靠记忆一定会漏
    （实测 2026-10-01：一站式 `代码片段.md` 落后 605 行、`验证判据设计.md` 整篇缺失，
    而且 SKILL.md 还在引用它 —— 引用悬空）。所以做成**每次自检顺带验一次**。

    姊妹 skill 不存在时 交叉同步.py 自己会报「单机模式」并退出 0，这里不拦。"""
    peer_script = HERE / "交叉同步.py"
    if not peer_script.exists():
        return [], ["  – 未发现 交叉同步.py（跳过）"]
    rc, out = run([str(peer_script), "--quiet"], timeout=90)
    if rc is None:
        return ["交叉同步.py 执行失败/超时"], [f"  ✗ 交叉同步.py: {out}"]
    if rc == 0:
        # 顺手确认「未归类文件」也没冒出来（清单过期＝下次同步会漏东西）
        rc2, out2 = run([str(peer_script), "--quiet", "--audit"], timeout=90)
        if rc2 == 0 and "未归类文件" in out2:
            rows = [l for l in out2.splitlines() if l.startswith("- ") and "：" in l]
            return ([f"交叉同步.py 报出未归类文件：清单需要更新"],
                    ["  ✗ 清单过期，以下文件未归类（公共 or 专属）："] + [f"    {r}" for r in rows[:10]])
        return [], ["  ✓ 双 skill 的 mod 公共资产已对齐"]
    rows = [l.strip() for l in out.splitlines()
            if l.strip().startswith(("- **", "- 至少一方缺", "- 两边"))]
    return ([f"双 skill 的 mod 公共资产有未对齐项（跑 `python scripts/交叉同步.py` 看详情）"],
            ["  ✗ 交叉同步未对齐："] + [f"    {r}" for r in rows[:12]])


def check_doc_integrity():
    """H. 文档引用完整性：SKILL.md ↔ 文件系统有没有互相说谎。

    为什么放进自检：脚本能跑≠文档没烂。实测（2026-10-12 首跑）抓到：
    SKILL.md 引用 `工程最佳实践.md §5`（该文档根本没有 §5）、
    `改造原版复用.md` 引用 `SKILL.md §16`（内容早搬到 代码片段.md §16 了）、
    7 个脚本/文档存在但 SKILL.md 从没记载（速查表过期）。
    引用悬空在实战里的表现是「按 SKILL.md 去找资料，找不到」。

    实现在 经验沉淀.py --doctor（双向验证过：坏样本必报、好样本零误杀）。"""
    doc_script = HERE / "经验沉淀.py"
    if not doc_script.exists():
        return ["经验沉淀.py 不在 scripts/ 里（单侧手滑删了？）"], ["  ✗ 缺 经验沉淀.py"]
    rc, out = run([str(doc_script), "--doctor", "--quiet"], timeout=60)
    if rc is None:
        return ["经验沉淀.py --doctor 执行失败/超时"], [f"  ✗ {out}"]
    if rc == 0:
        return [], ["  ✓ 文档引用完整性（路径引用/§锚点/速查表/归档配对）全部通过"]
    rows = [l.strip() for l in out.splitlines() if l.strip().startswith(("✗", "- **"))]
    return ([f"文档引用完整性有问题（跑 `python scripts/经验沉淀.py --doctor` 看详情）"],
            ["  ✗ 文档引用完整性："] + [f"    {r}" for r in rows[:12]])


def check_network():
    """E. 网络（可选）：下载通道可达性。"""
    import urllib.request, urllib.error, json as _json
    targets = [
        ("Modrinth API", "https://api.modrinth.com/v2/project/sodium"),
        ("MCIM 镜像(CF)", "https://mod.mcimirror.top/curseforge/v1/mods/328085"),
    ]
    details = []
    for name, url in targets:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "mc-workshop-selftest/1.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                ok = r.status == 200
            details.append(f"  {'✓' if ok else '✗'} {name}: HTTP {r.status}")
        except urllib.error.HTTPError as e:
            details.append(f"  ✗ {name}: HTTP {e.code}")
        except Exception as e:
            details.append(f"  ✗ {name}: {type(e).__name__} —— 国内网络下 Modrinth 直连不稳属常见，"
                           f"下载可改用 CF 走 MCIM 镜像")
    return [], details


def check_no_pollution(files, before):
    """E. 副作用：跑 --help / 无参 不许往 skill 目录里落文件。

    2026-09-15 加：实体模型预览.py / 像素纹理生成.py 的 -o 默认值原本指向 HERE(scripts/)，
    跑一次自检就往 skill 里丢了 3 个 png —— 谁跑都污染一次。

    ⚠️ `before` 必须由 main() 在**任何检查跑之前**抓取并传进来。
    早期写成函数内部抓取 → check_smoke(B) 先跑已经污染了，E 再抓快照就把污染文件
    当成了"本来就有的"，检测形同虚设（自测时注入了回归却报 clean 才发现）。
    子进程一律 cwd=临时目录，否则「写当前工作目录」的脚本还是写进 scripts/。"""
    import tempfile
    problems, details = [], []
    with tempfile.TemporaryDirectory() as td:
        for f in files:
            for extra in (["--help"], []):
                run([str(f)] + extra, cwd=td)
    after = {p.name for p in HERE.iterdir() if p.is_file()}
    new_files = sorted(n for n in (after - before) if not n.endswith(".pyc"))
    if new_files:
        problems.append(f"脚本在 skill 目录里新建了文件：{new_files} → 默认输出路径应指向当前工作目录")
        details.append(f"  ✗ 新建 {new_files}")
    else:
        details.append(f"  ✓ skill 目录无新增文件（{len(files)} 个脚本跑过 --help 与无参）")
    return problems, details


def main():
    ap = argparse.ArgumentParser(
        description=f"{SKILL_NAME} 脚本自检：语法 / 冒烟 / 交叉引用 / 退出码 / 无副作用 "
                    f"/ 双 skill 交叉同步（+可选网络探测）",
        epilog="退出码：0=全通过 / 1=有问题")
    ap.add_argument("--net", action="store_true", help="额外探测下载通道（Modrinth / MCIM 镜像）可达性")
    ap.add_argument("--no-peer", action="store_true", help="跳过「双 skill 交叉同步」检查（单装一个 skill 时）")
    ap.add_argument("--quiet", action="store_true", help="只打印问题")
    ap.add_argument("--md", help="同时写一份 markdown 报告到该路径")
    a = ap.parse_args()

    files = py_files()
    L, all_problems = [], []

    # ⚠️ 必须在任何检查跑之前抓（check_smoke 会起子进程，可能污染 skill 目录）
    skill_snapshot = {p.name for p in HERE.iterdir() if p.is_file()}

    def section(title, res, show_ok=True):
        probs, det = res
        all_problems.extend(probs)
        L.append(f"## {title}")
        L.append("")
        if probs or show_ok:
            L.extend(det)
        L.append("")

    L.append(f"# {SKILL_NAME} 脚本自检报告")
    L.append("")
    L.append(f"- 脚本目录：`{HERE}`")
    L.append(f"- 脚本数量：**{len(files)}** 个")
    L.append("")

    section(f"A. 语法（{len(files)} 个）", check_syntax(files), show_ok=not a.quiet)
    section("B. 冒烟（--help / 无参）", check_smoke(files), show_ok=not a.quiet)
    section("C. 脚本间交叉引用", check_cross_imports(files), show_ok=not a.quiet)
    section("D. 退出码约定（用法错误 = 2）", check_exit_codes(files), show_ok=not a.quiet)
    section("E. 无副作用（不许污染 skill 目录）", check_no_pollution(files, skill_snapshot), show_ok=not a.quiet)
    if not a.no_peer:
        section("F. 双 skill 交叉同步（mod 公共资产是否对齐）", check_peer_sync(), show_ok=not a.quiet)
    section("H. 文档引用完整性（SKILL.md ↔ 文件系统）", check_doc_integrity(), show_ok=not a.quiet)
    if a.net:
        section("G. 网络通道（可选）", check_network(), show_ok=True)

    if all_problems:
        L.append(f"## 结论：⚠ {len(all_problems)} 项问题")
        L.append("")
        for p in all_problems:
            L.append(f"- {p}")
    else:
        L.append("## 结论：✅ 全部通过")
    L.append("")

    report = "\n".join(L)
    print(report)
    if a.md:
        Path(a.md).write_text(report, encoding="utf-8")
        print(f"[√] 报告已写入 {a.md}")
    return 1 if all_problems else 0


if __name__ == "__main__":
    sys.exit(main())

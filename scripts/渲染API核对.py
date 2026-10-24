#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
渲染 API 核对（26.x GUI / 渲染重构专用）

干什么：
  编译通过 ≠ 画得出来。26.1 起 GUI 绘制是「GuiElementRenderState + RenderPipeline」两段式，
  编译期只检查方法签名，**不检查顶点元素是否写全**。典型事故：

      gfx.fill(RenderPipelines.GUI_TEXTURED, TextureSetup.singleTexture(v, s), x0,y0,x1,y1)
      → 生成 ColoredRectangleRenderState（只写 position+color）
      → GUI_TEXTURED 的顶点格式是 POSITION_TEX_COLOR（要 UV0）
      → 进世界第一帧崩：IllegalStateException: Missing elements in vertex: UV0

  本脚本把「pipeline 需要哪些顶点元素」和「*RenderState.buildVertices 实际写了哪些」对起来，
  在写代码之前就把这类崩溃挑出来。

用法：
  # 自动从工程里找 patched sources jar（推荐）
  python 渲染API核对.py --project ../Minimap
  # 或者直接给 jar
  python 渲染API核对.py build/moddev/artifacts/minecraft-patched-26.1.0.19-beta-sources.jar
  # 只看某个 pipeline 的顶点格式
  python 渲染API核对.py --project ../Minimap --list
  python 渲染API核对.py --project ../Minimap --pipeline GUI_TEXTURED_SNIPPET
  # 只看某个 render state
  python 渲染API核对.py --project ../Minimap --state Blit

退出码：0 = 没发现问题；1 = 发现"某 state 配某 pipeline 会缺元素"；2 = 参数/输入错误。
"""

import argparse
import glob
import os
import re
import sys
import zipfile

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 顶点格式元素名 -> 该元素由哪个 setter 写入
ELEMENT_SETTERS = {
    "POSITION": ("addVertex", "addVertexWith2DPose"),
    "COLOR": ("setColor",),
    "TEX": ("setUv", "setUv0"),
    "LIGHT": ("setLight", "setUv1"),
    "NORMAL": ("setNormal",),
}

# DefaultVertexFormat 里的别名（名字 -> 元素）
FORMAT_ALIAS = {
    "POSITION": "POSITION",
    "COLOR": "COLOR",
    "TEX": "TEX",
    "UV0": "TEX",
    "UV1": "LIGHT",
    "LIGHT": "LIGHT",
    "NORMAL": "NORMAL",
}


def fail(msg, code=2):
    print("!! " + msg)
    sys.exit(code)


def find_sources_jar(project):
    pats = [
        os.path.join(project, "build", "moddev", "artifacts", "minecraft-patched-*-sources.jar"),
        os.path.join(project, "**", "minecraft-patched-*-sources.jar"),
    ]
    for p in pats:
        hits = sorted(glob.glob(p, recursive=True))
        if hits:
            return hits[-1]
    # 退回 gradle 缓存里官方 client jar（无源码，只能查 pipeline 常量，够用一半）
    cache = os.path.expanduser("~/.gradle/caches/neoformruntime/artifacts")
    hits = sorted(glob.glob(os.path.join(cache, "minecraft_*_client.jar")))
    if hits:
        print("!! 没找到 patched sources jar，退回官方 client jar（只能查 RenderPipelines 常量）")
        return hits[-1]
    fail("找不到任何 MC jar：先跑一次 gradlew build 生成 build/moddev/artifacts/*-sources.jar")


def read_text(zf, path):
    try:
        return zf.read(path).decode("utf-8", errors="ignore")
    except KeyError:
        return None


# ---------------------------------------------------------------- pipelines

def parse_pipelines(src):
    """从 RenderPipelines.java 里抓 `<NAME>_SNIPPET = ... withVertexFormat(DefaultVertexFormat.<FMT>)`"""
    out = {}
    # 每个 snippet 定义块
    for m in re.finditer(
        r"public static final RenderPipeline\.Snippet\s+(\w+)\s*=(.*?);\s*\n", src, re.S
    ):
        name, body = m.group(1), m.group(2)
        fm = re.search(r"withVertexFormat\(\s*DefaultVertexFormat\.(\w+)", body)
        sh = re.search(r'withVertexShader\("([^"]+)"\)', body)
        out[name] = {
            "format": fm.group(1) if fm else None,
            "shader": sh.group(1) if sh else None,
        }
    # 常量（GUI / GUI_TEXTURED ...）指向的 snippet
    for m in re.finditer(
        r"public static final RenderPipeline\s+(\w+)\s*=\s*register\((.*?)\);\s*\n", src, re.S
    ):
        name, body = m.group(1), m.group(2)
        sm = re.search(r"RenderPipeline\.builder\((\w+)_SNIPPET", body) or re.search(
            r"builder\(\s*(\w+)_SNIPPET", body
        )
        if sm:
            out.setdefault(name, {})["snippet"] = sm.group(1) + "_SNIPPET"
    return out


def format_elements(fmt):
    if not fmt:
        return set()
    els = set()
    for part in fmt.split("_"):
        els.add(FORMAT_ALIAS.get(part, part))
    return els


# ---------------------------------------------------------------- render states

def parse_states(zf, filter_kw=None):
    """扫所有 *RenderState.java，抓 buildVertices 里实际调用的 setter"""
    out = []
    for name in zf.namelist():
        if not name.endswith("RenderState.java"):
            continue
        cls = os.path.basename(name)[:-5]
        if filter_kw and filter_kw.lower() not in cls.lower():
            continue
        src = read_text(zf, name)
        if src is None:
            continue
        # 跳过接口/抽象类（它们只有声明，顶点在实现类里写）
        head = src[:600]
        if re.search(r"\binterface\s+" + cls + r"\b", head) or re.search(
            r"\babstract\s+class\s+" + cls + r"\b", head
        ):
            continue
        i = src.find("buildVertices")
        if i < 0:
            continue
        body = src[i:i + 2500]
        provided = set()
        for el, setters in ELEMENT_SETTERS.items():
            for s in setters:
                if re.search(r"\." + s + r"\s*\(", body):
                    provided.add(el)
                    break
        if not provided:
            # 方法体里没有任何 setter：多半是委托给别的类画（如 GlyphRenderState），
            # 不能判定为"缺元素"，跳过以免误报
            continue
        out.append({"class": cls, "provides": provided})
    return out


def main():
    ap = argparse.ArgumentParser(description="26.x 渲染 API 核对：pipeline 顶点格式 × RenderState 实际写入")
    ap.add_argument("jar", nargs="?", help="minecraft-patched-*-sources.jar 路径")
    ap.add_argument("--project", help="工程目录，自动找 patched sources jar")
    ap.add_argument("--list", action="store_true", help="只列出 pipeline snippet 的顶点格式")
    ap.add_argument("--pipeline", help="只看某个 pipeline / snippet（子串匹配）")
    ap.add_argument("--state", help="只看某个 RenderState（子串匹配）")
    args = ap.parse_args()

    jar = args.jar
    if not jar:
        if not args.project:
            fail("给 jar 路径或 --project <工程目录>")
        jar = find_sources_jar(args.project)
    if not os.path.isfile(jar):
        fail("jar 不存在: " + jar)

    zf = zipfile.ZipFile(jar)
    rp_src = read_text(zf, "net/minecraft/client/renderer/RenderPipelines.java")
    if rp_src is None:
        fail("jar 里没有 RenderPipelines.java（给的是 class jar 而非 sources jar？）")

    pipelines = parse_pipelines(rp_src)
    if args.pipeline:
        pipelines = {k: v for k, v in pipelines.items() if args.pipeline.upper() in k.upper()}

    print("=" * 78)
    print("jar:", jar)
    print("=" * 78)

    if args.list:
        print("\n-- pipeline / snippet 顶点格式 --")
        for name, info in sorted(pipelines.items()):
            fmt = info.get("format")
            snip = info.get("snippet")
            if not fmt and snip and snip in pipelines:
                fmt = pipelines[snip].get("format")
            els = sorted(format_elements(fmt)) if fmt and fmt != "EMPTY" else []
            print("  {:<34} {:<26} {}{}".format(
                name, fmt or "-", ",".join(els), ("  (来自 " + snip + ")") if not info.get("format") and snip else ""))
        return 0

    states = parse_states(zf, args.state)
    if not states:
        fail("没匹配到任何 *RenderState（--state 过滤太严？）")

    print("\n-- *RenderState 实际写入的顶点元素 --")
    for st in sorted(states, key=lambda x: x["class"]):
        print("  {:<40} {}".format(st["class"], ",".join(sorted(st["provides"])) or "(无)"))

    # 只核对「GUI 系」pipeline（mod 写 HUD 会碰到的）
    gui_pipes = {
        k: v for k, v in pipelines.items()
        if k.startswith("GUI") or "GUI" in k
    }
    problems = 0
    print("\n-- 匹配检查（哪些 state 配哪些 pipeline 会缺元素） --")
    checked = 0
    for pname, pinfo in sorted(gui_pipes.items()):
        fmt = pinfo.get("format")
        if not fmt:
            continue
        need = format_elements(fmt)
        for st in states:
            missing = need - st["provides"]
            checked += 1
            if missing:
                problems += 1
                print("  ❌ {} + {}：缺 {}".format(
                    st["class"], pname, ",".join(sorted(missing))))
    if problems == 0:
        print("  ✅ 已检查 {} 组组合，没有缺元素的情况".format(checked))
    print("\n提示：上面 ❌ 的组合只是「数学上不兼容」，不代表原版一定在用；")
    print("      真正要防的是「你自己的绘制调用」落在这些组合上——写代码前先对照。")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

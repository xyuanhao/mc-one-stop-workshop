#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Minecraft 模组工程脚手架生成器

从 skill 自带的官方模板生成工程，自动完成：
  - 复制对应平台/版本的模板
  - 包名、modid、模组名、版本替换
  - 源码目录重组到用户包路径
  - Forge 1.20.1 模板的 @TOKEN@ 占位符替换

用法:
  python 新建工程.py --platform neoforge --mc 26.1 --mdg --modid mymod \
                     --name "我的模组" --package com.yuan.mymod --dir ./MyMod

  python 新建工程.py --platform fabric   --mc 26.2 --modid mymod --dir ./MyMod
  python 新建工程.py --platform neoforge --mc 1.21.1 --modid mymod --dir ./MyMod
  python 新建工程.py --platform forge    --mc 1.20.1 --modid mymod --dir ./MyMod
  python 新建工程.py --list                      # 查看可用模板
"""

import argparse
import io
import os
import re
import shutil
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
TEMPLATES = SKILL_DIR / "templates"

# 平台 + 版本 → 模板目录
TEMPLATE_MAP = {
    ("forge", "1.20.1"): "forge-1.20.1",
    ("neoforge", "1.21.1"): "neoforge-1.21.1",
    ("neoforge", "26.1"): "neoforge-26.1-mdg",     # 默认 ModDevGradle
    ("neoforge", "26.2"): "neoforge-26.1-mdg",
    ("fabric", "26.1"): "fabric-26.2",
    ("fabric", "26.2"): "fabric-26.2",
}

# NeoGradle 版（--ng 切换）
NEOGRADLE_MAP = {
    ("neoforge", "26.1"): "neoforge-26.1",
    ("neoforge", "26.2"): "neoforge-26.1",
}

# 各 MC 版本要求的 Java
JAVA_VERSION = {
    "1.20.1": 17, "1.20.2": 17, "1.20.3": 17, "1.20.4": 17,
    "1.20.5": 21, "1.20.6": 21,
    "1.21": 21, "1.21.1": 21, "1.21.2": 21, "1.21.3": 21, "1.21.4": 21,
    "1.21.5": 21, "1.21.6": 21, "1.21.7": 21, "1.21.8": 21,
    "1.21.9": 21, "1.21.10": 21, "1.21.11": 21,
    "26.1": 25, "26.2": 25,
}

# Forge 1.20.1 模板的占位符 → 实际值（按 MC 版本取）
FORGE_TOKENS = {
    "1.20.1": {
        "@MC_VERSION@": "1.20.1",
        "@MC_NEXT_VERSION@": "1.21",
        # ⚠️ 只写 Forge 自己的版本号，**不要带 MC 前缀**：
        # 模板 build.gradle 拼的是 "${minecraft_version}-${forge_version}"，
        # 写成 1.20.1-47.3.0 会变成 1.20.1-1.20.1-47.3.0，构建时报
        # "Could not resolve dependency: net.minecraftforge:forge:1.20.1-1.20.1-47.3.0:userdev"
        # （2026-10-02 实测踩到）。
        "@FORGE_VERSION@": "47.3.0",
        "@FORGE_SPEC_VERSION@": "47",
        "@MAPPING_CHANNEL@": "official",
        "@MAPPING_VERSION@": "1.20.1",
    }
}

MODID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
PKG_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")

# 文本替换时跳过的二进制/非文本后缀
BINARY_SUFFIX = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".ogg", ".mp3",
                 ".jar", ".zip", ".class", ".ttf", ".bin", ".nbt"}


def die(msg):
    print(f"[错误] {msg}", file=sys.stderr)
    sys.exit(1)


def validate(modid, package):
    if not MODID_RE.match(modid):
        die(f"modid 不合法: '{modid}'。规则：小写字母开头，只能含 a-z / 0-9 / 下划线，长度 2-64。")
    if not PKG_RE.match(package):
        die(f"包名不合法: '{package}'。规则：小写，点分隔，每段为合法 Java 标识符。")
    if package.split(".")[-1] in {"java", "javax", "net", "com", "org"}:
        print(f"[警告] 包名末段 '{package.split('.')[-1]}' 容易与原版/库冲突，建议加后缀。")


def pick_template(platform, mc, use_ng):
    if use_ng and (platform, mc) in NEOGRADLE_MAP:
        return NEOGRADLE_MAP[(platform, mc)]
    key = (platform, mc)
    if key not in TEMPLATE_MAP:
        avail = ", ".join(f"{p} {v}" for p, v in sorted(TEMPLATE_MAP))
        die(f"没有 ({platform}, {mc}) 对应的模板。可用组合：{avail}\n"
            f"      若目标版本不在列表，可先生成最接近的版本再手动调 build.gradle。")
    return TEMPLATE_MAP[key]


def build_replacements(platform, mc, modid, name, package, version, author):
    """返回有序的 (old, new) 列表。长串在前，避免短串误伤。"""
    reps = []

    # 包名（模板里 NeoForge/Forge 是 com.example.examplemod，Fabric 是 com.example）
    reps.append(("com.example.examplemod", package))
    if platform == "fabric":
        reps.append(("com.example", package))

    # modid
    if platform == "fabric":
        reps.append(("modid", modid))
    else:
        reps.append(("examplemod", modid))

    # 显示名 / 作者 / 版本
    reps.append(("Example Mod", name))
    reps.append(("YourNameHere, OtherNameHere", author))
    reps.append(("examplemod.mixins.json", f"{modid}.mixins.json"))
    reps.append(("examplemod.client.mixins.json", f"{modid}.client.mixins.json"))
    reps.append(("examplemod.refmap.json", f"{modid}.refmap.json"))
    return reps


def apply_text_replacements(path: Path, reps, forge_tokens=None):
    if path.suffix.lower() in BINARY_SUFFIX:
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return False
    original = text
    for old, new in reps:
        text = text.replace(old, new)
    if forge_tokens:
        for tok, val in forge_tokens.items():
            text = text.replace(tok, val)
    if text != original:
        path.write_text(text, encoding="utf-8")
        return True
    return False


def escape_properties_non_ascii(out_dir):
    """把 gradle.properties 里**非注释行**的非 ASCII 字符转成 \\uXXXX 转义。

    为什么必须做：.properties 格式规定按 ISO-8859-1 读取，Gradle 也守这个规矩。
    直写中文会让取到的值变成乱码（"ç»¿å®ç³å"），而且这个乱码会顺着
    build.gradle 的 `archivesName = mod_name`（→ 产物 jar 文件名）和
    mods.toml 的 `displayName="${mod_name}"`（→ 游戏里模组名）一起继承过去。
    实测过：转义后 jar 名和 displayName 都正常，不转义两边一起烂。

    注释行不动 —— Gradle 不解析注释值，留着中文更好读。

    返回改写的行数（0 = 没有非 ASCII，无需转义）。
    """
    p = Path(out_dir) / "gradle.properties"
    if not p.exists():
        return 0
    lines = p.read_text(encoding="utf-8").splitlines()
    n = 0
    for i, line in enumerate(lines):
        s = line.lstrip()
        if s.startswith("#") or s.startswith("!"):
            continue
        if any(ord(c) > 127 for c in line):
            lines[i] = "".join(
                c if ord(c) < 128 else "\\u%04X" % ord(c) for c in line)
            n += 1
    if n:
        p.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return n


def fetch_latest_neo_version(mc: str, timeout: float = 8.0):
    """从 Maven 元数据拿该 MC 版本对应的最新 NeoForge 构建号。

    26.x 的 NeoForge 构建号滚动更新（26.2.0.7x/8x），且**编译目标必须和用户实际
    安装的 loader 一致**（低一档就会 "requires neoforge X or above"），所以脚手架
    生成时能查到就该写进去。查不到返回 None，由调用方提示手工核对。
    """
    import urllib.request
    url = "https://maven.neoforged.net/releases/net/neoforged/neoforge/maven-metadata.xml"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            xml = resp.read().decode("utf-8", "replace")
    except Exception:
        return None
    pattern = re.compile(r"<version>(%s\.\d+(?:\.\d+)?)\.(\d+)(?:-(\w+))?</version>" % re.escape(mc))
    best = None
    for m in pattern.finditer(xml):
        prefix, build, qualifier = m.group(1), int(m.group(2)), m.group(3)
        if qualifier:            # 跳过 26.2.0.8-beta 这类预发布
            continue
        cand = f"{prefix}.{build}"
        if best is None or version_tuple(cand) > version_tuple(best):
            best = cand
    return best


def version_tuple(v: str):
    parts = []
    for chunk in re.split(r"[.\-+]", v):
        parts.append(int(chunk) if chunk.isdigit() else 0)
    return tuple(parts)


def mc_version_range(mc: str) -> str:
    """26.1 → [26.1,26.2)，1.20.1 → [1.20.1,1.21)，1.21.1 → [1.21.1,1.22)。

    实测坑（2026-09-10）：模板里写的是 `[26.1]`，这是「精确等于 26.1」。
    而 NeoForge 26.1.2.108 实际带的是 **MC 26.1.2**，加载时直接
    `Mod xxx requires minecraft 26.1` 起不来。上界要放开到下一个次版本。
    """
    parts = mc.split(".")
    if len(parts) >= 2:
        try:
            return f"[{mc},{parts[0]}.{int(parts[1]) + 1})"
        except ValueError:
            pass
    return f"[{mc},)"


def detect_modded_jdk(want_java):
    """从官方启动器自带的运行时里找一个够用的 JDK（26.x 需要 Java 25）。

    本机默认 java 常常是老版本（比如 21），而 Gradle toolchain 自动下载走
    GitHub Releases，国内基本下不动。官方启动器的 runtime 目录里躺着完整 JDK
    （含 javac.exe），直接指过去最省事 —— 实测 java-runtime-epsilon 就是 25.0.1。
    """
    if not want_java:
        return None
    root = Path(os.environ.get("APPDATA", "")) / ".minecraft" / "runtime"
    if not root.is_dir():
        return None
    # ⚠️ 必须**优先精确匹配**，不能只挑"版本够高的那个"：
    # 启动器里同时躺着 17 / 21 / 25 三个 JDK，而 1.20.1 要的是 17 ——
    # 按"够用就行"去挑会挑到 25，于是 Gradle toolchain 找不到 17、
    # 报一个指向完全无关的 "Cannot find a Java installation ... languageVersion=17"
    # （2026-10-02 实测踩到，当时脚手架把 1.20.1 工程的 JDK 指到了 java-runtime-epsilon=25）。
    exact, exact_ver = None, 0            # 精确等于 want_java
    fallback, fallback_ver = None, 10 ** 6  # 够用里**最低**的那个
    for d in sorted(root.iterdir()):
        if not d.is_dir() or not (d / "bin" / "javac.exe").exists():
            continue
        ver = 0
        rel = d / "release"
        if rel.exists():
            for line in rel.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("JAVA_VERSION="):
                    raw = line.split("=", 1)[1].strip().strip('"')
                    try:
                        ver = int(raw.split(".")[0])
                    except ValueError:
                        ver = 0
                    break
        if ver == want_java:
            if exact is None or ver > exact_ver:
                exact, exact_ver = d, ver
        elif ver > want_java and ver < fallback_ver:
            fallback, fallback_ver = d, ver
    return exact or fallback


def neo_range(neo_version: str) -> str:
    """由 NeoForge 构建号推出「声明给玩家的版本下限」。

    2026-09-27 补：neo_version 是**编译用的构建号**（联网查最新正式版，如 26.1.2.109），
    但它不能直接当 mods.toml 的依赖下限 —— 玩家实例常停在同小版本更老的构建
    （如 26.1.0.19-beta），写成 [26.1.2.109,) 会让它们启动即报
    "Mod xxx requires neoforge 26.1.2.109 or above" 直接拒载（实测踩过）。
    同小版本内 API 一致，所以下限只取小版本起点，一个产物覆盖全部 26.1.x。
    """
    parts = re.split(r"[.\-+]", neo_version)
    if len(parts) >= 2:
        return f"[{parts[0]}.{parts[1]}.0,)"
    return f"[{neo_version},)"


def rewrite_gradle_properties(path: Path, platform, mc, modid, name, package, version, neo_version=None):
    """直接改 gradle.properties 的 mod 元信息，比字符串替换更可靠。

    2026-09-09 补：模板自带的 minecraft_version / minecraft_version_range / neo_version
    是**模板**的版本（如 -m 26.2 拿到的是 26.1 模板，里面还写着 26.1），不同步会导致
    编出来的是 26.1 模组。生成时必须按 -m 覆盖。
    """
    if not path.exists():
        return
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    want = {
        "mod_id": modid,
        "mod_name": name,
        "mod_group_id": package,
        "mod_version": version,
        "version": version,   # Fabric 用 version / group
        "group": package,
    }
    if platform == "neoforge":
        want["minecraft_version"] = mc
        want["minecraft_version_range"] = mc_version_range(mc)
        if neo_version:
            want["neo_version"] = neo_version
            want["neo_version_range"] = neo_range(neo_version)
    elif platform == "fabric":
        want["minecraft_version"] = mc
    seen = set()
    changed_versions = []
    out = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in want and key not in seen:
                if key in ("minecraft_version", "minecraft_version_range", "neo_version",
                           "neo_version_range"):
                    old = stripped.split("=", 1)[1].strip()
                    if old != want[key]:
                        changed_versions.append((key, old, want[key]))
                out.append(f"{key}={want[key]}\n")
                seen.add(key)
                continue
        out.append(line)
    # 补上模板里没有的键
    missing = [k for k in want if k not in seen]
    if missing:
        out.append("\n# 由脚手架生成补充\n")
        for k in missing:
            out.append(f"{k}={want[k]}\n")
    path.write_text("".join(out), encoding="utf-8")
    return changed_versions


def relocate_java_sources(root: Path, platform, package):
    """把 src/*/java/com/example[/examplemod] 搬到用户包路径。

    坑点（2026-09-07 实测修复）：
    - 默认包名 com.example.<modid> 时目标目录在旧目录**内部**
      （com/example → com/example/testmod），整目录 shutil.move 会报
      "Cannot move a directory into itself"，必须逐文件搬。
    - 每个源码集只处理一个旧包根（examplemod 优先，否则 example），
      不能两个候选都跑——跑完 examplemod 后再跑 example 会把已搬好的
      文件再搬一次，且 rglob 惰性求值 + 边搬边遍历会 FileNotFoundError。
    """
    pkg_path = package.replace(".", "/")
    for src_dir in list(root.rglob("java")):
        if not src_dir.is_dir():
            continue
        old_base = src_dir / "com" / "example" / "examplemod"
        if not old_base.is_dir():
            old_base = src_dir / "com" / "example"
        if not old_base.is_dir():
            continue
        target = src_dir / pkg_path
        if old_base == target:
            continue
        # 目标已存在、或目标在旧目录内部（如 com.example.<modid>）→ 逐文件搬
        if target.exists() or old_base in target.parents:
            # 先物化文件列表，避免边移动边遍历
            for f in sorted(p for p in old_base.rglob("*") if p.is_file()):
                rel = f.relative_to(old_base)
                dst = target / rel
                if f.resolve() == dst.resolve():
                    continue  # 已在目标位置
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(f), str(dst))
            # 自底向上清空目录壳
            for d in sorted((p for p in old_base.rglob("*") if p.is_dir()),
                            key=lambda p: len(p.parts), reverse=True):
                try:
                    d.rmdir()
                except OSError:
                    pass
            if old_base != target and target not in old_base.parents:
                try:
                    old_base.rmdir()
                except OSError:
                    pass
        else:
            shutil.move(str(old_base), str(target))
        # 清理空的 com/example、com 残留
        for d in (src_dir / "com" / "example", src_dir / "com"):
            try:
                d.rmdir()
            except OSError:
                pass


def rename_mixin_files(root: Path, modid):
    for f in list(root.rglob("modid.mixins.json")):
        f.rename(f.with_name(f"{modid}.mixins.json"))
    for f in list(root.rglob("modid.client.mixins.json")):
        f.rename(f.with_name(f"{modid}.client.mixins.json"))


def main():
    ap = argparse.ArgumentParser(
        description="Minecraft 模组工程脚手架生成器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n"
               "  python 新建工程.py -p neoforge -m 26.1 --modid mymod --dir ./MyMod\n"
               "  python 新建工程.py -p fabric -m 26.2 --modid mymod --package com.yuan.mymod --dir ./MyMod\n")
    ap.add_argument("-p", "--platform", choices=["forge", "neoforge", "fabric"])
    ap.add_argument("-m", "--mc", help="目标 Minecraft 版本，如 1.20.1 / 1.21.1 / 26.2")
    ap.add_argument("--mdg", action="store_true", help="NeoForge 用 ModDevGradle（26.x 默认）")
    ap.add_argument("--ng", action="store_true", help="NeoForge 用 NeoGradle 而非 ModDevGradle")
    ap.add_argument("--modid", help="mod id，小写，如 mymod")
    ap.add_argument("--name", help="模组显示名，默认取 modid 首字母大写")
    ap.add_argument("--package", help="Java 包名，默认 com.example.<modid>")
    ap.add_argument("--version", default="1.0.0", help="模组版本，默认 1.0.0")
    ap.add_argument("--author", default="YourName", help="作者名")
    ap.add_argument("--dir", help="输出目录")
    ap.add_argument("--neo-version", help="NeoForge 构建号（如 26.2.0.82）。不填且联网可达时自动查询最新正式版")
    ap.add_argument("--list", action="store_true", help="列出可用模板后退出")
    args = ap.parse_args()

    if args.list:
        print("可用模板（平台 + MC 版本 → 模板目录）：")
        for (p, v), d in sorted(TEMPLATE_MAP.items()):
            extra = []
            if (p, v) in NEOGRADLE_MAP:
                extra.append(f"--ng 则用 {NEOGRADLE_MAP[(p, v)]}")
            print(f"  {p:9s} {v:7s} → {d}" + (f"   ({'; '.join(extra)})" if extra else ""))
        print("\nJava 版本要求：")
        for v in ["1.20.1", "1.20.5", "1.21.1", "1.21.11", "26.1", "26.2"]:
            print(f"  MC {v:8s} → Java {JAVA_VERSION.get(v, '?')}")
        return

    if not all([args.platform, args.mc, args.modid, args.dir]):
        die("缺少必需参数：--platform / --mc / --modid / --dir（用 --help 看示例）")

    modid = args.modid
    validate(modid, args.package or "com.example.x")
    package = args.package or f"com.example.{modid}"
    name = args.name or modid.replace("_", " ").title()
    mc = args.mc

    tpl_name = pick_template(args.platform, mc, args.ng)
    tpl_dir = TEMPLATES / tpl_name
    if not tpl_dir.is_dir():
        die(f"模板目录不存在：{tpl_dir}")

    out_dir = Path(args.dir).resolve()
    if out_dir.exists() and any(out_dir.iterdir()):
        die(f"输出目录非空：{out_dir}（换一个目录，或先清空）")
    out_dir.mkdir(parents=True, exist_ok=True)

    shutil.copytree(tpl_dir, out_dir, dirs_exist_ok=True)

    # 1) 移动 Java 源码到用户包路径
    relocate_java_sources(out_dir, args.platform, package)

    # 2) 文本替换
    reps = build_replacements(args.platform, mc, modid, name, package, args.version, args.author)
    forge_tokens = FORGE_TOKENS.get(mc) if args.platform == "forge" else None
    changed = 0
    for f in out_dir.rglob("*"):
        if f.is_file():
            if apply_text_replacements(f, reps, forge_tokens):
                changed += 1

    # 3) mixin 配置文件重命名
    rename_mixin_files(out_dir, modid)

    # 4) gradle.properties 精确写入（含 MC / NeoForge 版本同步）
    neo_version = args.neo_version
    if args.platform == "neoforge" and not neo_version:
        neo_version = fetch_latest_neo_version(mc)
    changed_versions = rewrite_gradle_properties(
        out_dir / "gradle.properties", args.platform,
        mc, modid, name, package, args.version, neo_version)

    # 4b) 中文显示名转义（必须排在上面之后：rewrite_gradle_properties 会把
    #     未转义的 name 重新写回 properties，先转义等于白转）
    escaped = escape_properties_non_ascii(out_dir)

    java = JAVA_VERSION.get(mc)

    # 5) 26.x 要 Java 25 而本机默认 java 往往更老：把官方启动器自带的 JDK
    #    写进 gradle.properties，省得 Gradle toolchain 去 GitHub 下载（国内下不动）
    jdk_note = None
    if java:
        jdk = detect_modded_jdk(java)
        if jdk:
            with io.open(out_dir / "gradle.properties", "a", encoding="utf-8", newline="\n") as fh:
                fh.write("\n# 由脚手架生成：指向官方启动器自带的 JDK\n")
                fh.write("# （Gradle toolchain 自动下载走 GitHub Releases，国内常失败）\n")
                fh.write(f"org.gradle.java.installations.paths={jdk.as_posix()}\n")
            jdk_note = jdk.as_posix()

    # 6) runServer 第一次会卡在 eula 询问，先备好运行目录
    #    server.properties 直接写成「能跑自动化验证」的样子（2026-09-12）：
    #    pause-when-empty-seconds=0 是关键——默认 60 秒无玩家整个服务端会暂停，
    #    RCON 驱动的实体/时间类验证会全部得到"没反应"的假结果，而且日志只有一行提示。
    run_dir = out_dir / "run"
    run_dir.mkdir(exist_ok=True)
    (run_dir / "eula.txt").write_text("eula=true\n", encoding="utf-8", newline="\n")
    (run_dir / "server.properties").write_text(
        "# 开发用服务端配置（由脚手架生成，可直接配合 scripts/rcon.py 做自动化验证）\n"
        "level-name=world\n"
        "online-mode=false\n"
        "# 无玩家时不暂停：否则 RCON 驱动的验证会得到「没反应」的假结果\n"
        "pause-when-empty-seconds=0\n"
        "# 开 RCON：python scripts/rcon.py \"list\" --port 25575 --password devtest\n"
        "enable-rcon=true\n"
        "rcon.port=25575\n"
        "rcon.password=devtest\n"
        "# 单次卡顿不判定为 crash，避免调试时被 watchdog 打断\n"
        "max-tick-time=-1\n"
        "view-distance=6\n",
        encoding="utf-8", newline="\n")

    # 7) 校验 gradle wrapper（早期脚手架漏带，生成完直接 ./gradlew 报 No such file）
    wrapper_ok = ((out_dir / "gradlew").exists()
                  and (out_dir / "gradle" / "wrapper" / "gradle-wrapper.jar").exists())

    print(f"[完成] 工程已生成：{out_dir}")
    print(f"  模板     : {tpl_name}")
    print(f"  平台     : {args.platform}    MC {mc}    Java {java or '请自行确认'}")
    print(f"  modid    : {modid}")
    print(f"  包名     : {package}")
    print(f"  显示名   : {name}   版本 {args.version}")
    if escaped:
        print(f"  产物文件名 : {name}-{args.version}.jar  （mod_name 已 \\uXXXX 转义，"
              f"否则 jar 名会是乱码）")
    else:
        print("  产物文件名 : 用 modid（显示名是纯 ASCII，未涉及转义）")
    print(f"  替换文件 : {changed} 个")
    print(f"  gradle wrapper : {'已就绪' if wrapper_ok else '缺失（模板里没有 gradlew，需手动 gradle wrapper）'}")
    if jdk_note:
        print(f"  JDK {java} : 已指向 {jdk_note}")
    for key, old, new in changed_versions:
        print(f"  [版本同步] {key}: {old} → {new}")
    if args.platform == "neoforge" and not neo_version:
        print("  [!] 未能自动获取 NeoForge 构建号（离线或网络不通），"
              "请手动填 gradle.properties 的 neo_version")
    print()
    print("下一步：")
    print(f"  1. cd \"{out_dir}\"")
    if java:
        print(f"  2. 确认本机 JDK 为 {java} 或更高（java -version）")
    print("  3. 到官方源核对依赖版本是否为最新：")
    print("       NeoForge : https://projects.neoforged.net/neoforged/neoforge")
    print("       Fabric   : https://fabricmc.net/develop/")
    print("       Forge    : https://files.minecraftforge.net/")
    print("  4. gradlew build（首次会下载并反编译 MC，可能要几十分钟）")
    print(f"  5. 跑一次检查：python 检查工程.py \"{out_dir}\"")
    if args.platform in ("forge", "neoforge") and java:
        print()
        print("  提示：主类里 @Mod 注解的值需与 modid 一致，脚手架已自动替换；")
        print("        若后续手动改 modid，记得同步 @Mod 注解。")


if __name__ == "__main__":
    main()

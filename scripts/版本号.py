#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""版本号.py —— mod 版本号的 查看 / 递增 / 一致性门禁 / 实例查重。

【为什么需要这个】
2026-10-22 数了一下本机 8 个实例：`mod_version` 清一色 `1.0.0`（唯一例外是
修仙体系的 0.1.0），`build/libs` 里躺着的也全是 `<显示名>-1.0.0.jar`。
后果不是「不优雅」，是会真的骗人：

  ① **看不出换了版** —— jar 名一样、`mods.toml` 里 version 一样、游戏 mod 列表里
     显示的版本也一样。改了三版功能，玩家（和自己）都以为还是第一版。
  ② **「改了没生效」的头号伪故障** —— 新 jar 和旧 jar **同名**。手动往 `mods/`
     拷的时候，资源管理器问「是否替换」，点否 / 或者压根没覆盖（文件被游戏占用），
     进游戏看到的还是旧内容，然后开始怀疑 Mixin 没生效、datagen 没跑。
  ③ **同 modid 双版本共存** —— 文件名不同（`xxx-1.0.0.jar` 和 `xxx-1.0.1.jar`
     同时躺在 mods/），NeoForge 只会加载其中一个且不保证是新的，表现是随机的
     「有时是新的有时是旧的」。这是最恶心的一种。
  ④ **占位符没展开** —— toml/json 里 version 还是字面 `${mod_version}`，说明
     processResources 没跑（或手工 zip 出的包），游戏里版本号显示成一串美元符号，
     严重的直接拒载。

【用法】
    # ① 看一眼：properties 版本 / build/libs 产物 / jar 内实际元数据，三处并列
    python 版本号.py <工程目录>

    # ② 交付门禁：三处不一致就退出码 1（可串进流水线）
    python 版本号.py <工程目录> --check

    # ③ 递增（改完功能、重新打包之前跑一次）
    python 版本号.py <工程目录> --bump patch     # 1.0.0 → 1.0.1  修 bug
    python 版本号.py <工程目录> --bump minor     # 1.0.1 → 1.1.0  加功能
    python 版本号.py <工程目录> --bump major     # 1.1.0 → 2.0.0  破坏性改动

    # ④ 直接指定（支持预发布/构建号后缀）
    python 版本号.py <工程目录> --set 1.2.0-beta
    python 版本号.py <工程目录> --set 0.3.0+26.1

    # ⑤ 连实例一起查：mods/ 里这个 mod 有几个版本、是不是当前版本
    python 版本号.py <工程目录> --check --mods "D:/实例/mods"

    # ⑥ 递增顺手清掉 build/libs 里的旧版产物（只删构建目录，可重建）
    python 版本号.py <工程目录> --bump patch --clean

【改哪些地方】
  只改「源头」两处，其余全是它派生出来的：
    * gradle.properties 的 `mod_version`（NeoForge/Forge）/ `version`（Fabric）
    * 万一 build.gradle 里把 version **写死**了（不是 `version = mod_version`），也给改了
    * 万一 toml / fabric.mod.json 里把 version **写死**了（不是 `${...}` 占位符），也给改了
      —— 这两种「写死」本身就是 bug，脚本会同时告警（模板默认是占位符，写死说明被人手改过）
  **不动** `neo_version` / `minecraft_version` / `fabric_api_version` —— 那些是环境版本，
  和模组自己的版本号是两件事，混改会把产物挡在玩家实例门外（见 新建工程.py 的 neo_range 注释）。

【退出码】0 = 一致/成功 ；1 = 发现问题（--check） ；2 = 参数或工程路径问题
"""
import argparse
import json
import re
import sys
import zipfile
from pathlib import Path

SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.\-]+))?(?:\+([0-9A-Za-z.\-]+))?$")
PROPS_VERSION_KEYS = ("mod_version", "version")   # NeoForge/Forge 用前者，Fabric 用后者
META_CANDIDATES = (
    "META-INF/neoforge.mods.toml",
    "META-INF/mods.toml",
    "fabric.mod.json",
)


# ---------------------------------------------------------------- 基础工具

def unescape_props(s: str) -> str:
    """把 .properties 里的 \\uXXXX 还原成真字符（中文 mod_name 就是这形式存的）。

    别用 codecs.decode(s, 'unicode_escape') —— 它按 latin-1 解读，
    混进真中文时会直接炸或变乱码。这里只逐段替换 \\uXXXX。
    """
    return re.sub(r"\\u([0-9A-Fa-f]{4})", lambda m: chr(int(m.group(1), 16)), s)


def read_props(p: Path):
    """返回 {key: (raw_value, 行号)}。raw_value 是未反转义的原值。"""
    out = {}
    if not p.exists():
        return out
    # newline='' 保原行尾符：默认 newline=None 会把 LF 悄悄变 CRLF，
    # 之后按行回写会让整个文件「看起来」全变了（元 2026-10-04 踩过）。
    with open(p, "r", encoding="utf-8", newline="") as f:
        for i, line in enumerate(f.read().splitlines()):
            s = line.strip()
            if s and not s.startswith("#") and not s.startswith("!") and "=" in s:
                k, v = s.split("=", 1)
                out[k.strip()] = (v.strip(), i)
    return out


def parse_semver(v: str):
    m = SEMVER_RE.match(v or "")
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)),
            m.group(4), m.group(5))


def bump_semver(v: str, part: str):
    """按 part 递增并**丢掉预发布后缀**（1.0.0-beta --bump patch → 1.0.1，不是 1.0.0）。"""
    p = parse_semver(v)
    if not p:
        return None
    major, minor, patch, _pre, _build = p
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def detect_platform(root: Path) -> str:
    g = ""
    for name in ("build.gradle", "build.gradle.kts"):
        p = root / name
        if p.exists():
            g = p.read_text(encoding="utf-8", errors="replace")
            break
    if "fabric-loom" in g or "fabric.mod.json" in g:
        return "fabric"
    if "net.neoforged" in g:
        return "neoforge"
    if "net.minecraftforge" in g or "minecraftforge" in g:
        return "forge"
    # build.gradle 缺失时按元数据文件猜
    if (root / "src/main/resources/fabric.mod.json").exists():
        return "fabric"
    return "unknown"


def source_version(props, platform):
    """取「源头版本号」。返回 (key, value) 或 (None, None)。

    Fabric 只认 `version`；NeoForge/Forge 认 `mod_version`。
    两个都有且不一样 → 以平台该用的那个为准，另一处由 audit 报不一致。
    """
    order = ("version", "mod_version") if platform == "fabric" else ("mod_version", "version")
    for k in order:
        if k in props:
            return k, unescape_props(props[k][0])
    return None, None


def archives_name(root: Path, props) -> str:
    """产物 jar 的名字前缀（build.gradle 里 `archivesName = mod_name`）。"""
    for name in ("build.gradle", "build.gradle.kts"):
        p = root / name
        if not p.exists():
            continue
        txt = p.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"archivesName\s*=\s*(.+)", txt)
        if m:
            expr = m.group(1).strip().strip("\"'")
            if expr in props:
                return unescape_props(props[expr][0])
            # archivesName = "写死的名字"
            return expr
        m = re.search(r"base\s*\{\s*archivesName\s*=\s*(.+)", txt)
        if m:
            expr = m.group(1).strip().strip("\"'")
            if expr in props:
                return unescape_props(props[expr][0])
            return expr
    # 没声明 → Gradle 默认用 project name，来自 settings.gradle
    sg = root / "settings.gradle"
    if sg.exists():
        m = re.search(r"rootProject\.name\s*=\s*['\"]([^'\"]+)", sg.read_text(encoding="utf-8", errors="replace"))
        if m:
            return m.group(1)
    return root.name


def read_jar_meta(jar: Path):
    """读 jar 内的 modid / version / 元数据文件名。返回 dict 或 None。"""
    try:
        with zipfile.ZipFile(jar) as z:
            names = set(z.namelist())
            for cand in META_CANDIDATES:
                if cand not in names:
                    continue
                txt = z.read(cand).decode("utf-8", "replace")
                if cand.endswith(".json"):
                    try:
                        d = json.loads(txt)
                    except json.JSONDecodeError:
                        return {"file": cand, "modid": None, "version": None,
                                "bad": "fabric.mod.json 不是合法 JSON"}
                    return {"file": cand, "modid": d.get("id"),
                            "version": str(d.get("version")), "bad": None}
                m_id = re.search(r'^\s*modId\s*=\s*"([^"]*)"', txt, re.M)
                # 只取第一个 version=（[[dependencies]] 里的 versionRange 不匹配这个正则）
                m_v = re.search(r'^\s*version\s*=\s*"([^"]*)"', txt, re.M)
                return {"file": cand,
                        "modid": m_id.group(1) if m_id else None,
                        "version": m_v.group(1) if m_v else None, "bad": None}
    except (zipfile.BadZipFile, OSError, KeyError):
        return None
    return None


# ---------------------------------------------------------------- 体检

def audit(root: Path, mods_dir: Path | None = None):
    """全量核对。返回 (errors, warns, infos)，每条都是纯文本。"""
    errors, warns, infos = [], [], []
    props_p = root / "gradle.properties"
    if not props_p.exists():
        return [f"没有 gradle.properties：{root}"], warns, infos

    props = read_props(props_p)
    platform = detect_platform(root)
    key, version = source_version(props, platform)
    if not version:
        return [f"gradle.properties 里找不到版本键（NeoForge/Forge 要 mod_version，Fabric 要 version）"], warns, infos
    if version == "${version}" or "${" in version:
        errors.append(f"{key} 还是占位符 {version} —— 模板没被替换，产物版本号不可用")

    aname = archives_name(root, props)
    expect_jar = f"{aname}-{version}.jar"
    infos.append(f"平台 {platform}   版本来源 gradle.properties:{key}={version}")
    infos.append(f"产物应为 build/libs/{expect_jar}")

    # ① `mod_version` 与 `version` 同时存在却不同 → 后续 whichever 用错就出事
    if "mod_version" in props and "version" in props:
        a = unescape_props(props["mod_version"][0])
        b = unescape_props(props["version"][0])
        if a != b:
            warns.append(f"gradle.properties 里 mod_version={a} 但 version={b}，两处不一致"
                         f"（build.gradle 用哪个，产物就是哪个；bump 会一起改）")

    # ② build.gradle 是否把 version 写死
    for name in ("build.gradle", "build.gradle.kts"):
        p = root / name
        if not p.exists():
            continue
        txt = p.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"^\s*version\s*=\s*[\"']([^\"']+)[\"']", txt, re.M)
        if m and "${" not in m.group(1):
            hard = m.group(1)
            if hard != version:
                errors.append(f"{name} 把 version 写死成 '{hard}'，和 gradle.properties 的 {version} 不一致"
                              f"（应改成 version = mod_version，否则 bump 无效）")
            else:
                warns.append(f"{name} 里 version 是写死的 '{hard}'，不是 version = mod_version —— "
                             f"下次改版本号容易漏改这里")

    # ③ 源头元数据里 version 是不是占位符
    for rel in ("src/main/resources/META-INF/neoforge.mods.toml",
                "src/main/resources/META-INF/mods.toml",
                "src/main/templates/META-INF/neoforge.mods.toml",
                "src/main/resources/fabric.mod.json"):
        p = root / rel
        if not p.exists():
            continue
        txt = p.read_text(encoding="utf-8", errors="replace")
        if rel.endswith(".json"):
            m = re.search(r'"version"\s*:\s*"([^"]*)"', txt)
        else:
            m = re.search(r'^\s*version\s*=\s*"([^"]*)"', txt, re.M)
        if not m:
            continue
        val = m.group(1)
        if "${" in val:
            infos.append(f"{rel} 的 version 用占位符 {val}（正确，由 processResources 展开）")
        else:
            errors.append(f"{rel} 把 version 写死成 '{val}'，和源头 {version} 断了联系"
                          f"（模板默认是占位符，写死=人工改过。改回占位符，或每次 bump 都要手改这里）")

    # ④ build/libs 产物
    libs = root / "build" / "libs"
    if libs.is_dir():
        jars = sorted(libs.glob("*.jar"))
        same = [j.name for j in jars if j.name == expect_jar]
        stale = [j.name for j in jars
                 if j.name.startswith(aname + "-") and j.name != expect_jar
                 and not j.name.endswith("-sources.jar") and not j.name.endswith("-javadoc.jar")]
        if same:
            jp = libs / expect_jar
            meta = read_jar_meta(jp)
            if meta is None:
                errors.append(f"{expect_jar} 读不出元数据（包坏了？）")
            else:
                mv = (meta.get("version") or "").strip()
                if "${" in mv:
                    errors.append(f"jar 内 {meta['file']} 的 version 仍是字面 {mv} —— "
                                  f"processResources 没生效（手工改名/手工 zip 的包？）")
                elif mv != version:
                    errors.append(f"jar 内 {meta['file']} 的 version='{mv}'，和 gradle.properties 的 {version} 不一致"
                                  f"（产物是旧的，重新 build）")
                else:
                    infos.append(f"产物核对通过：{expect_jar} 内 version={mv}")
                mid = (meta.get("modid") or "")
                if "${" in mid:
                    errors.append(f"jar 内 {meta['file']} 的 modId 仍是 {mid} —— 占位符没展开")
        else:
            if jars:
                warns.append(f"build/libs 里没有 {expect_jar}（现有：{', '.join(j.name for j in jars[:4])}）"
                             f"—— 产物是旧版本或还没 build")
            else:
                infos.append("build/libs 为空（还没 build 过）")
        if stale:
            warns.append(f"build/libs 里还有旧版产物：{', '.join(stale)} —— "
                         f"往 mods/ 拷的时候极易拷错（按名字挑就会挑到旧的），"
                         f"确认后删掉，或 --bump 时加 --clean")
    else:
        infos.append("build/libs 不存在（还没 build 过）")

    # ⑤ 默认 1.0.0
    if version == "1.0.0":
        warns.append("版本号仍是默认的 1.0.0 —— 一旦对外发布过、或要区分「这版修了什么」，"
                     "就该 bump（--bump patch/minor/major）")

    # ⑥ 实例 mods/ 查重（要知道 modid 才查得动）
    if mods_dir is not None:
        modid = unescape_props(props.get("mod_id", ("", 0))[0]) if "mod_id" in props else None
        if not modid:
            fmod = root / "src/main/resources/fabric.mod.json"
            if fmod.exists():
                try:
                    modid = json.loads(fmod.read_text(encoding="utf-8")).get("id")
                except Exception:
                    modid = None
        if not modid:
            warns.append("--mods 检查跳过：读不到本工程的 modid")
        else:
            e2, w2, i2 = audit_mods_for(mods_dir, modid, version)
            errors += e2
            warns += w2
            infos += i2
    return errors, warns, infos


def audit_mods_for(mods_dir: Path, modid: str, version: str):
    """扫实例 mods/，找同 modid 的 jar：几个版本、是否当前版本。"""
    errors, warns, infos = [], [], []
    if not mods_dir.is_dir():
        return [f"--mods 目录不存在：{mods_dir}"], warns, infos
    hits = []
    for j in sorted(mods_dir.glob("*.jar")):
        meta = read_jar_meta(j)
        if not meta:
            continue
        if (meta.get("modid") or "") != modid:
            continue
        hits.append((j.name, (meta.get("version") or "").strip(), platform_of(meta.get("file"))))
    if not hits:
        warns.append(f"mods/ 里没找到 modid={modid} 的 jar —— 还没装？")
        return errors, warns, infos
    versions = {h[1] for h in hits}
    if len(hits) > 1:
        errors.append(f"mods/ 里同名 mod 有 {len(hits)} 个 jar："
                      + "、".join(f"{n}(v{v})" for n, v, _ in hits)
                      + " —— 同 modid 多版本共存，加载哪个不确定，删到只剩一个")
    if version not in versions:
        warns.append(f"mods/ 里的版本 {sorted(versions)} 不是工程当前版本 {version} —— "
                     f"游戏里跑的是旧包")
    else:
        infos.append(f"mods/ 里已是当前版本 {version}")
    return errors, warns, infos


def platform_of(meta_file):
    return "fabric" if (meta_file or "").endswith(".json") else "neoforge"


# ---------------------------------------------------------------- 写回

def write_version(root: Path, platform: str, old: str, new: str):
    """把 old → new 写进源头。返回 [(文件, 说明)]；写不进去抛异常。"""
    changed = []
    props_p = root / "gradle.properties"
    with open(props_p, "r", encoding="utf-8", newline="") as f:
        raw = f.read()
    eol = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.splitlines()
    n = 0
    for i, line in enumerate(lines):
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("!") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        k, v = k.strip(), v.strip()
        if k in PROPS_VERSION_KEYS and unescape_props(v) == old:
            lines[i] = f"{k}={new}"
            n += 1
    if n == 0:
        raise RuntimeError(f"gradle.properties 里没有值为 {old} 的 {'/'.join(PROPS_VERSION_KEYS)} 键")
    with open(props_p, "w", encoding="utf-8", newline="") as f:
        f.write(eol.join(lines) + eol)
    changed.append(("gradle.properties", f"{n} 处 {old} → {new}"))

    # build.gradle 写死的 version
    for name in ("build.gradle", "build.gradle.kts"):
        p = root / name
        if not p.exists():
            continue
        txt = p.read_text(encoding="utf-8")
        new_txt, cnt = re.subn(r"(^\s*version\s*=\s*[\"'])%s([\"'])" % re.escape(old),
                               r"\g<1>%s\g<2>" % new, txt, flags=re.M)
        if cnt:
            p.write_text(new_txt, encoding="utf-8")
            changed.append((name, f"写死的 version {old} → {new}（建议改成 version = mod_version）"))

    # 元数据写死的 version
    for rel in ("src/main/resources/META-INF/neoforge.mods.toml",
                "src/main/resources/META-INF/mods.toml",
                "src/main/templates/META-INF/neoforge.mods.toml"):
        p = root / rel
        if not p.exists():
            continue
        txt = p.read_text(encoding="utf-8")
        new_txt, cnt = re.subn(r'(^\s*version\s*=\s*")%s(")' % re.escape(old), r"\g<1>%s\g<2>" % new,
                               txt, flags=re.M)
        if cnt:
            p.write_text(new_txt, encoding="utf-8")
            changed.append((rel, f"写死的 version {old} → {new}（建议改回 ${{mod_version}}）"))
    p = root / "src/main/resources/fabric.mod.json"
    if p.exists():
        txt = p.read_text(encoding="utf-8")
        new_txt, cnt = re.subn(r'("version"\s*:\s*")%s(")' % re.escape(old), r"\g<1>%s\g<2>" % new, txt)
        if cnt:
            p.write_text(new_txt, encoding="utf-8")
            changed.append(("src/main/resources/fabric.mod.json", f"写死的 version {old} → {new}"))
    return changed


def clean_stale_libs(root: Path, aname: str, keep: str):
    """删 build/libs 里同前缀的其它版本 jar（只碰构建产物，可重建）。"""
    libs = root / "build" / "libs"
    if not libs.is_dir():
        return []
    keep_name = f"{aname}-{keep}.jar"
    removed = []
    for j in sorted(libs.glob("*.jar")):
        if j.name == keep_name or j.name.startswith(aname + "-sources") or j.name.startswith(aname + "-javadoc"):
            continue
        if j.name.startswith(aname + "-"):
            j.unlink()
            removed.append(j.name)
    return removed


# ---------------------------------------------------------------- 展示

def show(root: Path, mods_dir=None):
    props = read_props(root / "gradle.properties")
    platform = detect_platform(root)
    key, version = source_version(props, platform)
    aname = archives_name(root, props)
    print("=" * 64)
    print(f"版本号总览：{root}")
    print("=" * 64)
    print(f"  平台              = {platform}")
    print(f"  版本来源          = gradle.properties  {key} = {version}")
    print(f"  产物 jar 名       = build/libs/{aname}-{version}.jar")
    print(f"  游戏内显示        = {aname}  {version}")
    for k in ("mod_id", "mod_name", "minecraft_version", "neo_version"):
        if k in props:
            print(f"  {k:<18}= {unescape_props(props[k][0])}")
    libs = root / "build" / "libs"
    if libs.is_dir():
        jars = sorted(p.name for p in libs.glob("*.jar"))
        print(f"  build/libs        = {', '.join(jars) if jars else '（空）'}")
    else:
        print("  build/libs  : 不存在（还没 build）")
    print()


def main():
    ap = argparse.ArgumentParser(
        description="mod 版本号：查看 / 递增 / 一致性门禁 / 实例查重",
        epilog="退出码：0=一致 ；1=发现问题 ；2=参数或路径问题")
    ap.add_argument("project", help="mod 源码工程目录（有 gradle.properties 的那一层）")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--bump", choices=["patch", "minor", "major"], help="递增版本并写回源头")
    g.add_argument("--set", dest="set_to", metavar="X.Y.Z[-pre]", help="直接指定版本号")
    ap.add_argument("--check", action="store_true", help="一致性门禁（不一致退出码 1）")
    ap.add_argument("--mods", metavar="目录", help="实例的 mods/ 目录，顺带查重复版本")
    ap.add_argument("--clean", action="store_true", help="删掉 build/libs 里同前缀的旧版产物")
    a = ap.parse_args()

    root = Path(a.project).resolve()
    if not (root / "gradle.properties").exists():
        print(f"[错误] 不是 mod 工程目录（缺 gradle.properties）：{root}", file=sys.stderr)
        print("      传源码工程根目录；整合包实例没有 gradle.properties，这个脚本不管。", file=sys.stderr)
        sys.exit(2)
    mods_dir = Path(a.mods).resolve() if a.mods else None

    if a.set_to:
        if not parse_semver(a.set_to):
            print(f"[错误] 版本号格式不对：{a.set_to}（要 MAJOR.MINOR.PATCH，可带 -beta / +build）", file=sys.stderr)
            sys.exit(2)

    if not a.bump and not a.set_to:
        show(root, mods_dir)
        errs, warns, infos = audit(root, mods_dir)
        _print_report(errs, warns, infos)
        if a.check:
            sys.exit(1 if errs else 0)
        sys.exit(0)

    # ---------- 改版本 ----------
    props = read_props(root / "gradle.properties")
    platform = detect_platform(root)
    key, old = source_version(props, platform)
    if not old:
        print("[错误] 读不到当前版本号", file=sys.stderr)
        sys.exit(2)
    new = a.set_to or bump_semver(old, a.bump)
    if not new:
        print(f"[错误] 当前版本 '{old}' 不是三段式 semver，没法自动递增；请用 --set 指定", file=sys.stderr)
        sys.exit(2)

    changed = write_version(root, platform, old, new)
    aname = archives_name(root, read_props(root / "gradle.properties"))
    print("=" * 64)
    print(f"版本号：{old}  →  {new}")
    print("=" * 64)
    for f, d in changed:
        print(f"  ✓ {f}：{d}")
    removed = clean_stale_libs(root, aname, new) if a.clean else []
    for r in removed:
        print(f"  ✓ 删除旧产物 build/libs/{r}")
    print(f"\n下一步：")
    print(f"  1. gradlew build          # 产物变成 {aname}-{new}.jar")
    print(f"  2. 把新 jar 拷进实例 mods/，**先删掉里面旧的 {aname}-*.jar**")
    print(f"     （同名覆盖不可靠：文件被游戏占用时会静默失败，表现就是「改了没生效」）")
    print(f"  3. python 版本号.py {root} --check --mods <实例/mods>   # 装完核对")
    sys.exit(0)


def _print_report(errs, warns, infos):
    if errs:
        print(f"❌ 错误 {len(errs)} 项：")
        for e in errs:
            print(f"   • {e}")
    if warns:
        print(f"⚠️  警告 {len(warns)} 项：")
        for w in warns:
            print(f"   • {w}")
    if infos:
        print(f"ℹ️  提示 {len(infos)} 项：")
        for i in infos:
            print(f"   • {i}")
    if not errs and not warns:
        print("✅ 版本号三处一致。")
    print()


if __name__ == "__main__":
    main()

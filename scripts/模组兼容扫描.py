#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
模组 API 指纹兼容扫描（纯标准库）
=================================

用途
----
解决"整合包里某个附属模组是用旧版 API 编译的，装上去一启动就
NoClassDefFoundError"这类问题。典型场景：Create 0.5.1 时代的附属
装到 Create 6.0.x 上，会引用 `com.jozufozu.flywheel.core.PartialModel`
（Flywheel 0.6）而 Create 6 用的是 `dev.engine_room.flywheel.**`
（Flywheel 1.0，并入 Create 本体）。mods.toml 里的 versionRange 常常
只写 `>=0.5.1` 不写上界，Forge 拦不住，只能靠字节码比对。

原理
----
1. 从"提供方"jar（如 create.jar、某个库 jar）里收集它**实际拥有**的类名；
2. 扫描"使用方"jar 里所有 .class 的常量池，抽取**被引用**的类名
   （限定在指定的包名前缀内，如 com/simibubi/create、flywheel 包名）；
3. 引用了、但提供方里没有 → 判定为 API 断裂（可能 NoClassDefFoundError）。

同时给出"世代指纹"：按 jar 引用的是旧包还是新包，判断它属于哪一代 API。

用法
----
    python 模组兼容扫描.py <mods目录或单个jar> [选项]

常用选项
--------
    --provider <jar>     指定"提供方"jar，可重复（默认自动识别 create/ae2 等）
    --prefix <pkg>       只关注该包前缀（斜杠形式），可重复
                         默认: com/simibubi/create
                               com/jozufozu/flywheel
                               dev/engine_room/flywheel
    --era                额外打印"世代指纹"判定
    --json <path>        输出 JSON 报告
    --quiet              只打印有问题的

退出码
------
    0 无断裂   1 发现断裂   2 参数/运行错误
"""
import argparse
import io
import json
import os
import re
import sys
import zipfile

# ---------------------------------------------------------------- 常量

# 默认关注的包前缀（内部形式，用斜杠）
DEFAULT_PREFIXES = [
    "com/simibubi/create/",
    "com/jozufozu/flywheel/",
    "dev/engine_room/flywheel/",
    "com/tterrag/registrate/",
]

# 提供方识别规则：文件名正则 -> 该 jar 提供哪些包
PROVIDER_RULES = [
    (r"^create-1\.20\.1-6\.", ["com/simibubi/create/", "dev/engine_room/flywheel/"]),
    (r"^create-1\.20\.1-0\.5", ["com/simibubi/create/", "com/jozufozu/flywheel/"]),
    (r"^create-", ["com/simibubi/create/"]),
    (r"flywheel.*1\.20", ["dev/engine_room/flywheel/", "com/jozufozu/flywheel/"]),
]

# 类名抽取：类文件常量池里的 Utf8 项，形如 com/simibubi/create/Foo$Bar
CLASS_RE_CACHE = {}


def _class_regex(prefixes):
    key = tuple(prefixes)
    if key not in CLASS_RE_CACHE:
        alt = "|".join(re.escape(p) for p in prefixes)
        CLASS_RE_CACHE[key] = re.compile(
            rb"(?:%s)[A-Za-z0-9_$]+(?:/[A-Za-z0-9_$]+)*" % alt.encode("utf-8").replace(rb"/", rb"/")
        )
    return CLASS_RE_CACHE[key]


# ---------------------------------------------------------------- 工具


def iter_class_bytes(zf):
    """产出 jar 内每个 .class 条目的字节。"""
    for name in zf.namelist():
        if not name.endswith(".class"):
            continue
        try:
            yield name, zf.read(name)
        except Exception:
            continue


def _collect_classes(zf, out, depth=0):
    """收集一个已打开的 zip 里的类名；递归展开 META-INF/jarjar/*.jar。"""
    for name in zf.namelist():
        if name.endswith(".class"):
            out.add(name[:-6])
    # Forge 的 jarJar：依赖被塞进 META-INF/jarjar/，运行时同样在类路径上。
    # 不展开就会把"其实存在"的类误判成缺失（Create 6 就内嵌了 Registrate/Flywheel/Ponder）。
    if depth >= 3:
        return
    for name in zf.namelist():
        if not (name.startswith("META-INF/jarjar/") and name.endswith(".jar")):
            continue
        try:
            data = zf.read(name)
        except Exception:
            continue
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as inner:
                _collect_classes(inner, out, depth + 1)
        except Exception:
            continue


def provided_classes(jar_path):
    """jar 实际提供的类名集合（内部形式），含 jarJar 内嵌依赖提供的类。"""
    out = set()
    try:
        with zipfile.ZipFile(jar_path) as zf:
            _collect_classes(zf, out)
    except Exception:
        pass
    return out


def referenced_classes(jar_path, prefixes):
    """
    扫描 jar 内所有 .class，抽取常量池中落在 prefixes 下的被引用类名。
    返回 {类名: 引用它的 class 条目集合}，便于判断"是不是只被数据生成类引用"。

    实现上用字节级正则扫常量池 Utf8 项——比完整解析常量池快得多，
    代价是会把"字符串字面量里恰好长得像类名"的也捞进来，
    所以后面要做一次"提供方里确实没有"的交叉验证来降噪。
    """
    rx = _class_regex(prefixes)
    out = {}
    try:
        with zipfile.ZipFile(jar_path) as zf:
            for _name, data in iter_class_bytes(zf):
                for m in rx.findall(data):
                    try:
                        c = m.decode("utf-8")
                    except Exception:
                        continue
                    if c.endswith("/"):
                        continue
                    out.setdefault(c, set()).add(_name)
    except Exception:
        pass
    return out


# 数据生成（datagen）类的特征：只在 `gradlew runData` 时跑，游戏运行时不加载。
# 这类类里引用了旧 API 不会导致启动崩溃，属于可忽略的误报。
DATAGEN_PAT = re.compile(
    r"(?:/datagen/|/data/|.*(?:RecipeGen|Datagen|DataGen|.*Provider|.*Generator)\.class)$"
)


def _all_datagen(class_entries):
    return bool(class_entries) and all(DATAGEN_PAT.search(c) for c in class_entries)


# 平台命名空间根：这些类由 MC / Forge / 运行时库提供，不在 mods 目录里，
# 全局比对时必须排除，否则每个 mod 都会被报"缺几百个 net/minecraft 类"。
PLATFORM_ROOTS = (
    "java/", "javax/", "jdk/", "sun/", "com/sun/",
    "net/minecraft/", "net/minecraftforge/", "net/neoforged/", "cpw/",
    "com/mojang/", "com/google/", "com/ibm/", "com/ctc/",
    "org/apache/", "org/slf4j/", "org/objectweb/", "org/joml/", "org/lwjgl/",
    "org/spongepowered/", "org/yaml/", "org/intellij/", "org/jetbrains/annotations/",
    "io/netty/", "it/unimi/", "com/typesafe/", "org/antlr/", "org/checkerframework/",
    "net/sf/", "com/lmax/", "org/xerial/", "org/mozilla/", "com/flowpowered/",
)


def _is_platform_ref(c):
    return c.startswith(PLATFORM_ROOTS)


def jar_meta(jar_path):
    """读 mods.toml / fabric.mod.json 的基本信息，用于报告的可读性。"""
    info = {"modid": None, "name": None, "version": None, "deps": []}
    try:
        with zipfile.ZipFile(jar_path) as zf:
            names = zf.namelist()
            if "META-INF/mods.toml" in names:
                txt = zf.read("META-INF/mods.toml").decode("utf-8", "ignore")
                m = re.search(r"^\s*modId\s*=\s*\"([^\"]+)\"", txt, re.M)
                if m:
                    info["modid"] = m.group(1)
                m = re.search(r"^\s*displayName\s*=\s*\"([^\"]+)\"", txt, re.M)
                if m:
                    info["name"] = m.group(1)
                m = re.search(r"^\s*version\s*=\s*\"([^\"]+)\"", txt, re.M)
                if m:
                    info["version"] = m.group(1)
                for dm in re.finditer(
                    r"\[\[dependencies\.[^\]]+\]\]\s*(.*?)(?=\n\[\[|\Z)", txt, re.S
                ):
                    blk = dm.group(1)
                    mid = re.search(r"modId\s*=\s*\"([^\"]+)\"", blk)
                    vr = re.search(r"versionRange\s*=\s*\"([^\"]+)\"", blk)
                    if mid:
                        info["deps"].append(
                            (mid.group(1), vr.group(1) if vr else "*")
                        )
            elif "fabric.mod.json" in names:
                data = json.loads(zf.read("fabric.mod.json").decode("utf-8", "ignore"))
                info["modid"] = data.get("id")
                info["name"] = data.get("name")
                info["version"] = data.get("version")
    except Exception:
        pass
    return info


def era_of(refs):
    """按引用的包名判断 API 世代。"""
    old_fw = any(c.startswith("com/jozufozu/flywheel/") for c in refs)
    new_fw = any(c.startswith("dev/engine_room/flywheel/") for c in refs)
    if old_fw and not new_fw:
        return "旧世代(Flywheel 0.6 / Create 0.5.x)"
    if new_fw and not old_fw:
        return "新世代(Flywheel 1.0 / Create 6.x)"
    if old_fw and new_fw:
        return "混合(同时引用新旧 flywheel，可疑)"
    return "未知(未直接引用 flywheel)"


def guess_modid(jar_path):
    return os.path.basename(jar_path)[:-4] if jar_path.endswith(".jar") else os.path.basename(jar_path)


def deep_referenced_classes(jar_path):
    """
    深度模式引用抽取：扫 jar 内所有 .class 里"长得像类路径"的串。

    为什么不用常量池精确解析：javac 对只在方法描述符里出现的类型
    （作为参数/返回值的类）**不生成 CONSTANT_Class 项**，只写在
    descriptor 的 Utf8 里。Journeymap 那类"整棵 API 被删"的断裂，
    引用方往往只在描述符里带它——精确解析会漏，必须上宽正则。
    宽正则的代价是可能捞进字符串字面量里的假类名，靠调用方的
    "平台根排除 + 命名空间占用 + 提供方交叉验证"三重过滤降噪。
    """
    rx = _class_regex_broad()
    out = {}
    try:
        with zipfile.ZipFile(jar_path) as zf:
            for _name, data in iter_class_bytes(zf):
                for m in rx.findall(data):
                    try:
                        c = m.decode("utf-8")
                    except Exception:
                        continue
                    out.setdefault(c, set()).add(_name)
    except Exception:
        pass
    return out


_CLASS_RE_BROAD_CACHE = None


def _class_regex_broad():
    """任意 3 段以上、斜杠分隔的类路径形态（a/b/C）。"""
    global _CLASS_RE_BROAD_CACHE
    if _CLASS_RE_BROAD_CACHE is None:
        _CLASS_RE_BROAD_CACHE = re.compile(
            rb"(?:[A-Za-z_$][A-Za-z0-9_$]*/){2,}[A-Za-z_$][A-Za-z0-9_$]*"
        )
    return _CLASS_RE_BROAD_CACHE
    """按引用的包名判断 API 世代。"""
    old_fw = any(c.startswith("com/jozufozu/flywheel/") for c in refs)
    new_fw = any(c.startswith("dev/engine_room/flywheel/") for c in refs)
    if old_fw and not new_fw:
        return "旧世代(Flywheel 0.6 / Create 0.5.x)"
    if new_fw and not old_fw:
        return "新世代(Flywheel 1.0 / Create 6.x)"
    if old_fw and new_fw:
        return "混合(同时引用新旧 flywheel，可疑)"
    return "未知(未直接引用 flywheel)"


# ---------------------------------------------------------------- 主流程


def scan(target, prefixes, provider_jars=None, show_era=False, quiet=False, deep=False):
    if os.path.isfile(target) and target.endswith(".jar"):
        jars = [target]
        base = os.path.dirname(target) or "."
    else:
        base = target
        jars = sorted(
            os.path.join(target, f) for f in os.listdir(target) if f.endswith(".jar")
        )
    if not jars:
        print("未找到任何 jar")
        return 2, []

    # 1) 提供方集合。
    #    口径 = mods 目录里所有 jar 提供的类的并集（含 jarJar 内嵌依赖），
    #    判某个 jar 时再减掉它自己提供的——这就等价于运行时的真实类路径。
    own = {}
    for j in jars:
        own[j] = provided_classes(j)
    for p in (provider_jars or []):
        own[p] = provided_classes(p)
        if p not in jars:
            jars.append(p)

    all_provided = set()
    for s in own.values():
        all_provided |= s

    provider_names = sorted(os.path.basename(p) for p in (provider_jars or []))
    if not provider_names:
        provider_names = [
            os.path.basename(j)
            for j in jars
            if any(re.match(pat, os.path.basename(j)) for pat, _ in PROVIDER_RULES)
        ]
    if not provider_names and not deep:
        print("!! 没识别出提供方 jar（请用 --provider 指定，例如 create.jar）")
        return 2, []

    print("=" * 78)
    print("模组 API 指纹兼容扫描" + ("【深度模式 --deep】" if deep else ""))
    print("=" * 78)
    print(f"扫描目录: {os.path.abspath(base)}")
    print(f"jar 数量: {len(jars)}")
    print(f"提供方  : {', '.join(provider_names) if provider_names else '（全目录并集）'}")
    print(f"提供类数: {len(all_provided)}")
    print(f"关注前缀: {', '.join(prefixes)}")
    print("=" * 78)

    broken = []
    rows = []
    # --deep：占位判定用"顶段包名"。任何被引用但没人提供的类，若其顶段包名
    # 恰好被某个已装 mod 占用（如 journeymap 命名空间），就认为是跨 mod API 断裂。
    # 平台命名空间（net/minecraft、com/mojang 等）由 MC/Forge 提供、不在 mods 目录，
    # 用 _is_platform_ref 直接排除；jarJar 库（registrate/flywheel 等）在并集里算提供。
    occ_top1 = {c.split("/", 1)[0] for c in all_provided if "/" in c} if deep else set()
    for j in jars:
        b = os.path.basename(j)
        refs_scoped = referenced_classes(j, prefixes)
        if not refs_scoped and not deep:
            continue
        # 判定集合：优先用常量池精确引用（--deep 时），否则退回正则
        judge = dict(refs_scoped)
        if deep:
            for c, srcs in deep_referenced_classes(j).items():
                if _is_platform_ref(c) or c in all_provided:
                    continue
                if c.endswith(("/package", "/package-info", "/module-info")):
                    continue
                if c.split("/", 1)[0] in occ_top1:
                    judge.setdefault(c, set()).update(srcs)
        if not judge:
            continue
        # 可用类 = 全部 jar 的并集。不要减掉自己提供的类：
        # 一个 jar 引用自己定义的类是天经地义的，减掉会把自己报成 2824 处缺失。
        available = all_provided
        hard, soft = [], []
        for c, srcs in judge.items():
            if c in available:
                continue
            # 去掉 package-info / module-info 之类（不是类引用）
            if c.endswith(("/package", "/package-info", "/module-info")):
                continue
            (soft if _all_datagen(srcs) else hard).append((c, srcs))
        missing = sorted(c for c, _ in hard)
        datagen_only = sorted(c for c, _ in soft)
        meta = jar_meta(j)
        era = era_of(refs_scoped)
        rows.append(
            {
                "jar": b,
                "modid": meta["modid"],
                "name": meta["name"],
                "version": meta["version"],
                "era": era,
                "refs": len(refs_scoped),
                "missing": missing,
                "datagen_only": datagen_only,
            }
        )
        if missing:
            broken.append((b, meta, missing, era))
        elif datagen_only and not quiet:
            print(
                f"  [可忽略] {b}: {len(datagen_only)} 处旧 API 只出现在数据生成类里"
                f"（例：{datagen_only[0].replace('/', '.')}），运行时不加载"
            )

    # 打印
    print(f"\n引用了目标 API 的模组: {len(rows)} 个\n")
    if show_era:
        print("-" * 78)
        print("【世代指纹】")
        for r in rows:
            flag = "  << 旧世代，可能不兼容" if "旧世代" in r["era"] else ""
            print(f"  {r['jar'][:52]:<52} {r['era']}{flag}")
        print("-" * 78)

    if broken:
        print(f"\n!! 发现 {len(broken)} 个模组引用了不存在的类（启动会 NoClassDefFoundError）：\n")
        for b, meta, missing, era in broken:
            label = meta["modid"] or meta["name"] or b
            ver = meta["version"] or "?"
            print(f"  [{label} {ver}]  {b}")
            print(f"      世代: {era}")
            deps = [f"{d[0]} {d[1]}" for d in meta["deps"] if d[0] in ("create", "flywheel")]
            if deps:
                print(f"      mods.toml 声明: {', '.join(deps)}  <- 声明不代表真的兼容")
            print(f"      缺失类 {len(missing)} 个，前 8 个：")
            for c in missing[:8]:
                print(f"        - {c.replace('/', '.')}")
            print()
    else:
        print("OK 未发现 API 断裂。")

    return (1 if broken else 0), rows


def main():
    ap = argparse.ArgumentParser(
        description="模组 API 指纹兼容扫描：比对引用类 vs 提供方实际类，揪出旧版附属"
    )
    ap.add_argument("target", help="mods 目录或单个 jar")
    ap.add_argument("--provider", action="append", default=[], help="提供方 jar，可重复")
    ap.add_argument("--prefix", action="append", default=[], help="关注包前缀(斜杠形式)，可重复")
    ap.add_argument("--era", action="store_true", help="打印世代指纹")
    ap.add_argument("--deep", action="store_true",
                    help="深度模式：精确解析常量池，全局比对跨 mod API 引用"
                         "（抓 create 前缀之外的断裂，如 Journeymap 老 API 整棵被删）")
    ap.add_argument("--json", dest="json_out", help="输出 JSON 报告路径")
    ap.add_argument("--quiet", action="store_true", help="只打印有问题的")
    args = ap.parse_args()

    prefixes = args.prefix or DEFAULT_PREFIXES
    try:
        code, rows = scan(
            args.target, prefixes, args.provider or None, args.era, args.quiet, args.deep
        )
    except Exception as e:
        print(f"运行失败: {e}")
        return 2

    if args.json_out:
        try:
            with open(args.json_out, "w", encoding="utf-8") as f:
                json.dump(rows, f, ensure_ascii=False, indent=2)
            print(f"JSON 报告: {args.json_out}")
        except Exception as e:
            print(f"写 JSON 失败: {e}")

    return code


if __name__ == "__main__":
    sys.exit(main())

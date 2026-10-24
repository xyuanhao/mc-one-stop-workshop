#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
制作整合包.py —— 把配好的 MC 实例（mods + config 等）打成可分发的整合包。

与「整合包体检.py」配对：体检 → 修 → 打。输入一个实例/.minecraft 版本目录
（PCL / HMCL / 官方启动器 versions/<名>/ 布局均可），输出三种主流格式：

  --format curseforge    CurseForge 格式 zip（默认）：manifest.json + overrides/**（本地 jar 在 overrides/mods/）
                         PCL / HMCL / Prism / CurseForge 启动器都可导入
  --format mrpack        Modrinth .mrpack：默认全部本地分发（jar 放 overrides/mods/）；
                         加 --online 联网按 sha1 反查下载地址，查不到的仍本地分发
  --format instance      PCL 备份式 zip：整个"可分发子集"保持原目录布局，
                         导入方解压到 versions/<名字>/ 即用
  --format all           三种全出

用法示例：
  python 制作整合包.py "D:/.minecraft/versions/我的包" --name "My Pack" -o 打包产出/
  python 制作整合包.py "实例目录" --format mrpack --online -o 打包产出/
  python 制作整合包.py "实例目录" --format all --with-saves --author 元 -o 打包产出/
"""
import argparse, hashlib, json, os, re, shutil, sys, time, zipfile

try:
    from urllib.request import Request, urlopen
except Exception:
    urlopen = None

# ---------- 打包目录规则 ----------
# 必带（走 overrides / 原布局）——mod 的配置与内容都在这些目录里
KEEP_DIRS = {
    "mods", "config", "defaultconfigs", "resourcepacks", "shaderpacks",
    "scripts", "kubejs", "openloader", "datapacks", "global_data_packs",
    "global_packs", "patchouli_books", "moonlight-global-datapacks",
    "fancymenu_data", "config_packs", "iconpacks", "drippyloadingscreen",
    "ftbquests", "ftbteams", "screenshake", "waystones",
    # mod 专属数据目录（漏带功能直接坏，宁可多带）
    "tacz", "data", "ironchests", "tombstone", "curios", "twilightforest",
}
# 必排除——运行时产物 / 个人数据 / 启动器垃圾
SKIP_DIRS = {
    "logs", "crash-reports", ".mixin.out", "local", "moddata", "modernfix",
    "saves", "screenshots", "PCL", "backups", "cache", ".cache",
    "tacz_backup", "schematics", "journeymap", "xaeroworldmap", "xaerominimap",
    "blueprint", "The_Wall", "structures", "serverconfig",
}
SKIP_FILES = {
    "options.txt", "servers.dat", "usercache.json", "usernamecache.json",
    "patchouli_data.json", "log4j2.xml", "crash-reports", "preferences.txt",
}
# 已知的运行时产物目录（上面黑名单没覆盖到时，靠特征识别）——保守起见仍给提示
CACHE_HINTS = ("cache", "backup", "temp", "tmp")

def norm(v):
    return re.sub(r"[^\w\u4e00-\u9fff]+", "_", str(v)).strip("_") or "pack"

def find_version_json(root):
    """实例的版本 json：<目录名>.json 或任意 *.json（排除 PCL 内部的）。"""
    cands = [os.path.join(root, os.path.basename(root) + ".json")]
    cands += [os.path.join(root, f) for f in sorted(os.listdir(root))
              if f.endswith(".json") and not f.startswith(".")]
    seen = set()
    for p in cands:
        p = os.path.normpath(p)
        if not os.path.isfile(p) or p in seen:
            continue
        seen.add(p)
        try:
            d = json.load(open(p, encoding="utf-8"))
            if isinstance(d, dict) and ("libraries" in d or "id" in d):
                return p, d
        except Exception:
            continue
    return None, None

def detect_mc_and_loader(root, json_path=None, version_json=None):
    """返回 (mc, platform, loader_id)。loader_id 形如 forge-47.4.16 / neoforge-21.1.x / fabric-0.15.x。"""
    mc = platform = None
    if json_path is None and version_json is None:
        json_path, version_json = find_version_json(root)
    vj = version_json or {}
    # 1) 顶层声明字段
    if not mc:
        mc = vj.get("clientVersion") or vj.get("minecraftVersion")
    # 2) libraries 里的版本特征串
    forge_ver = fabric_ver = neo_ver = None
    for lib in vj.get("libraries", []):
        n = lib.get("name", "") if isinstance(lib, dict) else str(lib)
        # fmlloader 版本形如 "1.20.1-47.4.16"（mc-forgebuild），需拆开
        mm = re.search(r"net\.minecraftforge:fmlloader:(\d+\.\d+(?:\.\d+)?)(?:-(\d+\.\d+\.\d+))?", n)
        if mm:
            if mm.group(2):
                mc = mc or mm.group(1)
                forge_ver = mm.group(2)
            else:
                forge_ver = mm.group(1)
            continue
        mm = re.search(r"net\.fabricmc:fabric-loader:([\w.]+)", n)
        if mm:
            fabric_ver = mm.group(1)
            continue
        mm = re.search(r"net\.neoforged:neoforge:(?:[\w.\-]+-)?(\d+\.\d+(?:\.\d+){1,3})", n)
        if mm:
            neo_ver = mm.group(1)
            continue
        mm = re.search(r"net\.minecraft:client:(\d+\.\d+(?:\.\d+)?)", n)
        if mm and not mc:
            mc = mm.group(1)
    # fancymodloader 结构（NeoForge 新加载器，26.2+）：net.neoforged.fancymodloader:loader
    # 注意其版本（11.0.16）≠ NeoForge 版本（26.2.0.76），只作平台标志
    has_fancy = any(re.search(r"net\.neoforged\.fancymodloader:(loader|client|server):[\d.]+",
                              lib.get("name", "") if isinstance(lib, dict) else str(lib))
                    for lib in vj.get("libraries", []))
    if not mc and forge_ver:
        mm = re.match(r"(\d+\.\d+(?:\.\d+)?)", forge_ver)
        if mm: mc = mm.group(1)
    # 3) 顶层 json 的 id（如 "1.20.1-forge-47.2.0" / "26.2-NeoForge_26.2.0.76"）
    if not mc:
        mm = re.search(r"(\d+\.\d+(?:\.\d+)?)", vj.get("id", ""))
        if mm: mc = mm.group(1)
    # 4) 兜底：没有 version.json（例如手工攒的 mods 目录 / 纯 instance 镜像），
    #    从任一 mod 的 mods.toml 里读 minecraft 依赖与平台。
    #    这是"从零搭整合包"最常见的目录形态，不兜底就只能靠用户手填 --mc/--loader。
    loader_id = None
    if not mc or not loader_id:
        fmc, fplat, flow = _detect_from_mods(root)
        mc = mc or fmc
        if not platform:
            platform = fplat
        if fplat and flow:
            loader_id = f"{fplat}-{flow}"
    # 平台与 loader id
    if forge_ver:
        platform, loader_id = "forge", f"forge-{forge_ver}"
    elif neo_ver or has_fancy:
        platform = "neoforge"
        # NeoForge 版本号：主库直接带 → 无则从 id/目录名找 "NeoForge_26.2.0.76"
        if not neo_ver:
            mm = re.search(r"[Nn]eo[Ff]orge[-_](\d+(?:\.\d+){1,3})",
                           vj.get("id", "") + " " + os.path.basename(root))
            if mm: neo_ver = mm.group(1)
        if neo_ver:
            loader_id = f"neoforge-{neo_ver}"
    elif fabric_ver:
        platform, loader_id = "fabric", f"fabric-{fabric_ver}"
    else:
        # 走到这里说明 version.json 里没线索；别把兜底阶段推断出的平台清掉
        platform = platform or None
    return mc, platform, loader_id


def _detect_from_mods(root):
    """
    兜底推断：没有 version.json 时，从 mods/*.jar 的元数据反推 (mc, platform, loader下限)。

    只认三件事，都不确定就返回 (None, None, None) 让上层继续提示用户手填：
      - Forge/NeoForge 1.20.1 及以前：META-INF/mods.toml
      - NeoForge 1.20.2+：META-INF/neoforge.mods.toml
      - Fabric：fabric.mod.json 的 schemaVersion / depends.minecraft

    返回 loader 版本可能是下限（如 mods.toml 写 `forge [47,)` → "47"），
    上层会据此生成 forge-47 这类近似 id，调用方应提示用户用 --loader 指定精确版本。
    """
    mods_dir = os.path.join(root, "mods")
    if not os.path.isdir(mods_dir):
        return None, None, None
    mc_votes = {}
    has_forge = has_neo = has_fabric = False
    neo_votes = forge_votes = 0
    low = None
    for fn in sorted(os.listdir(mods_dir))[:60]:
        if not fn.endswith(".jar"):
            continue
        try:
            with zipfile.ZipFile(os.path.join(mods_dir, fn)) as zf:
                names = zf.namelist()
                # 注意：Architectury / Balm 这类跨平台 jar 里 mods.toml 和
                # fabric.mod.json 是并存的。所以不能"撞见 fabric.mod.json 就早退"，
                # 那会把 Forge 包误判成 Fabric（实测踩过）。
                # 取证顺序：neoforge > forge > fabric，且必须扫完再判。
                if "fabric.mod.json" in names:
                    has_fabric = True
                    try:
                        data = json.loads(zf.read("fabric.mod.json").decode("utf-8", "ignore"))
                        v = _range_lower(str((data.get("depends") or {}).get("minecraft", "")))
                        if v:
                            mc_votes[v] = mc_votes.get(v, 0) + 1
                    except Exception:
                        pass
                # 🔴 双平台 jar 陷阱（2026-09-18 实测）：不少 mod 同一个 jar 里**同时**放了
                #   META-INF/mods.toml 和 META-INF/neoforge.mods.toml
                #   （例：create_things_and_misc / Terralith / yet_another_config_lib_v3 ...）。
                #   早先优先读 neoforge.mods.toml → 读到 neoforge 依赖 → has_neo=True
                #   → **整个 Forge 包被判成 NeoForge**（manifest 里 loader 写错）。
                #   正确做法：**两个都读**，投两份票；平台判定靠"谁投票多"而不是"谁在场"。
                toml_names = [n for n in ("META-INF/mods.toml",
                                          "META-INF/neoforge.mods.toml") if n in names]
                if not toml_names:
                    continue
                for toml_name in toml_names:
                    txt = zf.read(toml_name).decode("utf-8", "ignore")
                    # 关键：Forge mods.toml 的块标题是【自己的 modid】，不是依赖的！
                    #   [[dependencies.create_hypertube]]   <- 这是本 mod
                    #       modId = "forge"                 <- 这才是依赖
                    # 早先按标题取名字，导致一个 forge 依赖都没识别到，平台全判成 fabric。
                    for dm in re.finditer(
                        r"\[\[dependencies\.[^\]]+\]\]\s*(.*?)(?=\n\[\[|\Z)", txt, re.S
                    ):
                        blk = dm.group(1)
                        m = re.search(r'modId\s*=\s*"([^"]+)"', blk)
                        if not m:
                            continue
                        kind = m.group(1)
                        vr = re.search(r'versionRange\s*=\s*"([^"]+)"', blk)
                        if kind == "neoforge":
                            has_neo = True
                            neo_votes += 1
                        elif kind == "forge":
                            has_forge = True
                            forge_votes += 1
                        if not vr:
                            continue
                        lb = _range_lower(vr.group(1))
                        if not lb:
                            continue
                        if kind == "minecraft":
                            mc_votes[lb] = mc_votes.get(lb, 0) + 1
                        elif kind in ("forge", "neoforge"):
                            if low is None or _vkey(lb) > _vkey(low):
                                low = lb
        except Exception:
            continue
    mc = max(mc_votes, key=lambda k: (mc_votes[k], _vkey(k))) if mc_votes else None
    # 🔴 平台取**多数票**，不是「有 neoforge 就算 neoforge」（2026-09-18 实测教训）。
    #   真实分布：107 个 jar 声明 forge、仅 2 个声明 neoforge —— 那 2 个其实是 Forge jar
    #   顺带塞了个 neoforge.mods.toml 存根（文件名叫 -forge-1.20.1.jar）。
    #   用「任一命中即 neoforge」会把整个 Forge 包判错，进而写坏 manifest 的 loader。
    if neo_votes > forge_votes:
        plat = "neoforge"
    elif forge_votes > 0:
        plat = "forge"
    elif has_fabric:
        plat = "fabric"
    else:
        plat = None
    return mc, plat, low


def _vkey(v):
    """把 '1.20.1' / '47' 之类的版本串转成可比较元组。"""
    parts = re.findall(r"\d+", v or "")
    return tuple(int(p) for p in parts) or (0,)


def _range_lower(s):
    """从 Forge 版本区间串里取下限：'[1.20.1,1.21)' -> '1.20.1'；'[47,)' -> '47'。"""
    s = (s or "").strip().strip("[]()")
    if not s:
        return None
    first = s.split(",")[0].strip()
    m = re.match(r"(\d+(?:\.\d+)*)", first)
    return m.group(1) if m else None

def scan_keep_items(root, with_saves=False, with_options=False, verbose=False):
    """收集要打包的 (相对路径) 清单。返回 (items, skipped, unknown_dirs)。"""
    items, skipped, unknown_dirs = [], [], []
    entries = sorted(os.listdir(root))
    for name in entries:
        p = os.path.join(root, name)
        rel = name
        if os.path.isfile(p):
            # 特例必须先于通用黑名单判断 —— options.txt 同时在 SKIP_FILES 里，
            # 若先查黑名单，--with-options 永远不生效（2026-09-17 实测踩到）
            if name == "options.txt":
                (items if with_options else skipped).append(rel)
                continue
            if name in SKIP_FILES:
                skipped.append(rel); continue
            # 版本 jar / 版本 json / natives / 启动器自己的文件——不属于整合包内容
            if name.endswith(("-natives",)) or name in ("natives",):
                skipped.append(rel); continue
            if re.search(r"\.(jar|json)$", name) and name not in SKIP_FILES:
                # 版本 jar（与目录同名的大 jar）与版本 json 不打包
                skipped.append(rel); continue
            items.append(rel)
            continue
        if os.path.isdir(p):
            if name.endswith("-natives"):
                skipped.append(rel); continue
            # 同理：saves 也在 SKIP_DIRS 里，特例必须先判
            if name == "saves":
                (items if with_saves else skipped).append(rel)
                continue
            if name in SKIP_DIRS:
                skipped.append(rel); continue
            if name in KEEP_DIRS:
                items.append(rel)
                continue
            # 未知目录：保守包含（漏带功能坏，多带顶多体积大），但提示
            unknown_dirs.append(rel)
            items.append(rel)
    return items, skipped, unknown_dirs

def zip_tree(zf, root, items, arc_prefix=""):
    """把 root 下的 items 相对路径写入 zip。已压缩后缀用 STORED 提速，其余 DEFLATED。"""
    stored_ext = (".jar", ".zip", ".png", ".jpg", ".jpeg", ".webp", ".ogg", ".mp3",
                  ".mcmeta", ".nbt", ".dat", ".lz4", ".gz")
    total = 0
    for rel in items:
        src = os.path.join(root, rel)
        if os.path.isdir(src):
            for base, dirs, files in os.walk(src):
                dirs[:] = [d for d in dirs if d not in ("__pycache__",)]
                for f in sorted(files):
                    fp = os.path.join(base, f)
                    if f.endswith((".disabled",)) or f.startswith("."):
                        continue
                    try:
                        st = os.stat(fp)
                        arc = os.path.join(arc_prefix, os.path.relpath(fp, root))
                        zi = zipfile.ZipInfo(arc.replace("\\", "/"),
                                             date_time=time.localtime(st.st_mtime))
                        ext = os.path.splitext(f)[1].lower()
                        zi.compress_type = (zipfile.ZIP_STORED if ext in stored_ext
                                            else zipfile.ZIP_DEFLATED)
                        zf.writestr(zi, open(fp, "rb").read())
                        total += 1
                    except Exception as e:
                        print(f"  ! 跳过 {fp}: {e}")
        else:
            try:
                # 顶层散文件（如 README）也必须带上 arc_prefix：
                # CF/mrpack 的 overrides 才是启动器会解到游戏目录的地方，
                # 落到 zip 根等于白打包（PCL 只解 overrides/**）。
                arc = os.path.join(arc_prefix, rel) if arc_prefix else rel
                zi = zipfile.ZipInfo(arc.replace("\\", "/"))
                zi.compress_type = zipfile.ZIP_STORED
                zf.writestr(zi, open(src, "rb").read())
                total += 1
            except Exception as e:
                print(f"  ! 跳过 {src}: {e}")
    return total

def sha1_file(path, chunk=1 << 20):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b: break
            h.update(b)
    return h.hexdigest()

MR_UA = "mc-modpack-tool/1.0 (workbuddy skill)"

def modrinth_lookup(sha1_hex, timeout=4):
    """按 sha1 反查 Modrinth 版本。返回 dict(url, filename, project) 或 None。

    注意：version_file/{sha1} 返回的是**单个 version 对象**（dict），不是数组。
    （早期版本可能返回数组，兼容两种结构。）
    """
    if urlopen is None:
        return None
    try:
        req = Request(
            f"https://api.modrinth.com/v2/version_file/{sha1_hex}?algorithm=sha1",
            headers={"User-Agent": MR_UA})
        with urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        if isinstance(data, dict):
            v = data                      # 新版：单版本对象
        elif isinstance(data, list) and data:
            v = data[0]                   # 旧版兼容：取第一个
        else:
            return None
        files = v.get("files") or []
        if not files:
            return None
        f0 = files[0]
        return {"url": f0.get("url"), "filename": f0.get("filename"),
                "project": (v.get("project_id") or "")[:24]}
    except Exception:
        return None

def build_cf_manifest(name, version, author, mc, loader_id):
    return {
        "minecraft": {"version": mc, "modLoaders": [{"id": loader_id, "primary": True}]},
        "manifestType": "minecraftModpack", "manifestVersion": 1,
        "name": name, "version": version, "author": author or "",
        "files": [],          # 本地 jar 在 overrides/mods/ 随包分发；CF 官方启动器需另行上传
        "overrides": "overrides",
    }

def build_mr_index(name, version, summary, mc, loader_id):
    dep = {"minecraft": mc}
    if loader_id:
        plat, ver = (loader_id.split("-", 1) + [""])[:2]
        key = {"forge": "forge", "neoforge": "neoforge",
               "fabric": "fabric-loader"}.get(plat)
        if key and ver:
            dep[key] = ver
    return {
        "formatVersion": 1, "game": "minecraft",
        "versionId": version, "name": name,
        "summary": summary or "",
        "files": [], "dependencies": dep,
    }

def make_curseforge(out_path, root, items, mods_jars, name, version, author, mc, loader_id):
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        manifest = build_cf_manifest(name, version, author, mc, loader_id)
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        # 本地 jar 放 overrides/mods/（2026-09-07 实测修正）：
        # PCL2 导入 CF 包时只按游戏目录镜像解 overrides/**，zip 根的 mods/ 会被忽略，
        # 表现为"导入成功但 mods 一个没有"。overrides/mods/ 三家（PCL/HMCL/Prism）都认。
        for jp in mods_jars:
            zi = zipfile.ZipInfo(f"overrides/mods/{os.path.basename(jp)}")
            zi.compress_type = zipfile.ZIP_STORED
            zf.writestr(zi, open(jp, "rb").read())
        n_ov = zip_tree(zf, root, [i for i in items if i != "mods"], "overrides/")
    return len(mods_jars), n_ov

def make_mrpack(out_path, root, items, mods_jars, name, version, summary,
                mc, loader_id, online):
    index = build_mr_index(name, version, summary, mc, loader_id)
    n_linked = n_local = 0
    local_mods = []           # 查不到下载 → overrides/mods/
    online = online and urlopen is not None
    if online:
        print("  反查 Modrinth（按 sha1）...")
    fail = 0                  # 断路器：连续失败 3 次视为网络不通，剩余全部本地分发
    for jar in mods_jars:
        digest = None
        info = None
        if online and fail < 3:
            digest = sha1_file(jar)
            info = modrinth_lookup(digest)
            if info is None:
                fail += 1
        if online and fail >= 3 and not info:
            if fail == 3:
                print("  ! Modrinth 反查连续失败，网络可能不通 → 剩余 mod 全部本地分发")
            fail += 1
            local_mods.append(jar)
            n_local += 1
            continue
        if info and info.get("url"):
            index["files"].append({
                "path": f"mods/{os.path.basename(jar)}",
                "hashes": {"sha1": digest},
                "env": {"client": "required", "server": "required"},
                "downloads": [info["url"]],
                # 官方规范可选字段，写上便于安装器预检（见 references/整合包格式规范.md）
                "fileSize": os.path.getsize(jar),
            })
            n_linked += 1
        else:
            local_mods.append(jar)
            n_local += 1
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("modrinth.index.json", json.dumps(index, ensure_ascii=False, indent=2))
        # 查不到下载的 mod 走 overrides/mods/（安装时同样落到 mods/）
        for jar in local_mods:
            zi = zipfile.ZipInfo(f"overrides/mods/{os.path.basename(jar)}")
            zi.compress_type = zipfile.ZIP_STORED
            zf.writestr(zi, open(jar, "rb").read())
        zip_tree(zf, root, [i for i in items if i != "mods"], "overrides/")
    return n_linked, n_local

def make_instance(out_path, root, items):
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        n = zip_tree(zf, root, items, "")
    return n

def main():
    ap = argparse.ArgumentParser(description="把 MC 实例打成可分发整合包")
    ap.add_argument("instance", help="实例目录（versions/<名字>/ 布局）")
    ap.add_argument("--name", help="包名（默认取目录名）")
    ap.add_argument("--version", default="1.0.0", help="包版本（默认 1.0.0）")
    ap.add_argument("--author", default="", help="作者")
    ap.add_argument("--summary", default="", help="一句话简介（mrpack 用）")
    ap.add_argument("--mc", help="MC 版本（默认自动推断）")
    ap.add_argument("--loader", help="loader id，如 forge-47.4.16 / neoforge-21.1.123 / fabric-0.15.11（默认自动推断）")
    ap.add_argument("--format", default="curseforge",
                    choices=["curseforge", "mrpack", "instance", "all"])
    ap.add_argument("-o", "--out", help="输出文件或目录（默认 ./打包产出/）")
    ap.add_argument("--with-saves", action="store_true", help="连存档一起打包")
    ap.add_argument("--with-options", action="store_true", help="连 options.txt 一起打包")
    ap.add_argument("--online", action="store_true",
                    help="mrpack 联网按 sha1 反查下载地址（默认本地分发，省流量且更稳）")
    args = ap.parse_args()

    root = os.path.abspath(args.instance)
    if not os.path.isdir(root):
        sys.exit(f"实例目录不存在: {root}")

    name = args.name or os.path.basename(root)
    out_dir = args.out or os.path.join(os.getcwd(), "打包产出")
    out_dir = out_dir if os.path.isdir(out_dir) or not out_dir.lower().endswith(".zip") \
              else os.path.dirname(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    safe = norm(name)

    # ---- 元信息推断 ----
    mc, platform, loader_id = detect_mc_and_loader(root)
    if args.mc: mc = args.mc
    if args.loader: loader_id = args.loader
    print(f"实例: {root}")
    print(f"推断: MC={mc or '未知'}, 平台={platform or '未知'}, loader={loader_id or '未知'}")
    if not loader_id:
        print("! 无法推断 loader（forge/fabric 版本），用 --loader 指定，如 --loader forge-47.4.16")
    elif not args.loader and not find_version_json(root)[0]:
        # 兜底推断出来的 loader 是"mod 们声明的最低要求"，会比实际装的旧
        print(f"  i loader 由 mods 目录反推（{loader_id}），这是 mod 声明的下限而非实际安装版本；"
              f"要精确请用 --loader 指定")
    if not mc:
        print("! 无法推断 MC 版本，用 --mc 指定，如 --mc 1.20.1")
        return

    # ---- 打包清单 ----
    items, skipped, unknown = scan_keep_items(root, args.with_saves, args.with_options)
    print(f"打包目录/文件: {len(items)} 项；跳过运行时产物: {len(skipped)} 项")
    for u in unknown:
        print(f"  ? 未知目录（已包含，如属缓存可用代码 SKIP_DIRS 排除）: {u}")

    mod_dir = os.path.join(root, "mods")
    mods_jars = []
    skipped_mods = []
    if os.path.isdir(mod_dir):
        for f in sorted(os.listdir(mod_dir)):
            fp = os.path.join(mod_dir, f)
            if not os.path.isfile(fp):
                continue
            if f.endswith(".disabled"):
                skipped_mods.append(f); continue
            if f.endswith(".jar"):
                mods_jars.append(fp)
    print(f"mod jar: {len(mods_jars)} 个" + (f"（跳过 .disabled {len(skipped_mods)} 个: " +
          ", ".join(s[:36] for s in skipped_mods[:3]) + "..." if skipped_mods else ""))

    # ---- 各格式 ----
    fmts = (["curseforge", "mrpack", "instance"] if args.format == "all" else [args.format])
    made = []
    for fmt in fmts:
        out = os.path.join(out_dir, f"{safe}-{fmt}.zip")
        print(f"\n=== 制作 {fmt} → {out}")
        t0 = time.time()
        if fmt == "curseforge":
            n_mod, n_ov = make_curseforge(out, root, items, mods_jars, name,
                                          args.version, args.author, mc, loader_id)
            print(f"  ✓ mods/{n_mod} 个 jar + overrides/ {n_ov} 个文件 "
                  f"({(os.path.getsize(out)/1048576):.1f} MB, {time.time()-t0:.1f}s)")
        elif fmt == "mrpack":
            n_l, n_lc = make_mrpack(out, root, items, mods_jars, name, args.version,
                                    args.summary, mc, loader_id, args.online)
            print(f"  ✓ 引用下载 {n_l} + 本地分发 overrides/mods/ {n_lc} "
                  f"({(os.path.getsize(out)/1048576):.1f} MB, {time.time()-t0:.1f}s)"
                  + ("  [--online 已反查]" if args.online else "  [本地分发，--online 可反查 Modrinth]"))
        elif fmt == "instance":
            n = make_instance(out, root, items)
            print(f"  ✓ 共 {n} 项 ({(os.path.getsize(out)/1048576):.1f} MB, {time.time()-t0:.1f}s)")
        made.append(out)

    print("\n=== 完成 ===")
    for m in made:
        print(f"  {m}  ({os.path.getsize(m)//1024} KB)")
    if loader_id:
        print("导入提示: PCL2/HMCL 直接拖 zip 导入；CF 官方启动器导入需要文件上传 CurseForge。")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模组版本谱系研究器 —— 回答"同一个模组，不同版本到底有什么不同"。

两个层次：
  ① API 层（默认，秒级）：拉该模组全部版本，出谱系表 + 相邻版本差异
     （目标 MC / loader 变化、required 依赖增删、正式版↔测试版转正、体积突变）
  ② jar 层（--fetch-jar N，需下载）：下最近 N 个版本的 jar，直接读包内元数据比对
     （依赖 versionRange、内嵌 jarJar 库、顶层包名前缀、mixin compatibilityLevel）
     —— 这一层才能抓到"换了渲染后端"这类 Java 侧大改：如 Create 0.5.x 引
     com/jozufozu/flywheel 而 Create 6 引 dev/engine_room/flywheel。

用法:
  python 模组版本谱系.py create --mc 1.20.1 --loader forge
  python 模组版本谱系.py journeymap --mc 1.20.1 --loader forge --limit 12
  python 模组版本谱系.py create sodium --mc 26.2 --loader neoforge --md 谱系.md
  python 模组版本谱系.py create --mc 1.20.1 --fetch-jar 3         # 下最近3版 jar 做字节级比对
  python 模组版本谱系.py sodium --all-mc --limit 30               # 跨 MC 大版本看升级脉络
"""
import argparse
import json
import re
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

MR = "https://api.modrinth.com/v2"
UA = {"User-Agent": "modpack-builder/1.0 (contact: local)"}
TAG = {"release": "正式版", "beta": "测试版", "alpha": "内测版"}


def http_json(url, timeout=40):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def http_get(url, dst: Path, timeout=180, retries=2):
    """下载带重试。实测老版本 jar 可能落在冷 CDN 上，首连会长时间无数据。"""
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=UA)
            written, expect = 0, None
            with urllib.request.urlopen(req, timeout=timeout) as r, open(dst, "wb") as f:
                cl = r.headers.get("Content-Length")
                expect = int(cl) if cl else None
                while True:
                    c = r.read(1 << 20)
                    if not c:
                        break
                    f.write(c)
                    written += len(c)
            if expect is not None and written != expect:
                raise IOError(f"下载不完整（{written}/{expect} 字节）")
            return written
        except Exception as e:
            if i == retries:
                raise
            print(f"      [重试{i + 1}] {e}")


def get_versions(slug, mc=None, loader=None):
    p = {}
    if mc:
        p["game_versions"] = json.dumps([mc])
    if loader:
        p["loaders"] = json.dumps([loader])
    u = f"{MR}/project/{slug}/version"
    if p:
        u += "?" + urllib.parse.urlencode(p)
    return http_json(u)


def resolve_ids(ids):
    """project_id → slug 批量解析（ids 必须是无空格 JSON，带空格会 InvalidURL）"""
    ids = sorted({i for i in ids if i})
    out = {}
    for k in range(0, len(ids), 100):
        chunk = ids[k:k + 100]
        try:
            ps = http_json(f"{MR}/projects?ids="
                           + urllib.parse.quote(json.dumps(chunk, separators=(",", ":"))))
            for p in ps:
                out[p["id"]] = p["slug"]
        except Exception as e:
            print(f"  [依赖名解析失败] {e}")
    return out


# ------------------------- jar 层解析 -------------------------
def parse_mods_toml(txt):
    """从 mods.toml / neoforge.mods.toml 里抽 (依赖modId → versionRange)。
    坑：块标题 [[dependencies.<自己的modid>]] 里才是依赖，不能按 [[dependencies.forge]] 匹配。"""
    deps = {}
    for blk in re.split(r"\[\[dependencies\.[^\]]*\]\]", txt)[1:]:
        mid = re.search(r'modId\s*=\s*"([^"]+)"', blk)
        vrg = re.search(r'versionRange\s*=\s*"([^"]*)"', blk)
        typ = re.search(r'type\s*=\s*"([^"]+)"', blk)
        if mid:
            deps[mid.group(1)] = {
                "range": vrg.group(1) if vrg else "",
                "type": typ.group(1) if typ else "required",
            }
    return deps


def zip_ok(p: Path):
    try:
        with zipfile.ZipFile(p) as z:
            return z.testzip() is None
    except Exception:
        return False


def jar_facts(path: Path):
    """读 jar 包内的结构性事实（不依赖 Java）。"""
    f = {"size": path.stat().st_size, "deps": {}, "embeds": [], "pkgs": {},
         "mixin_levels": set(), "mod_version": None}
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            for mf in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml"):
                if mf in names:
                    txt = z.read(mf).decode("utf-8", "replace")
                    f["deps"].update(parse_mods_toml(txt))
                    vm = re.search(r'version\s*=\s*"([^"$]*)".*\n', txt)
                    if vm:
                        f["mod_version"] = vm.group(1).strip()
                    break
            if "fabric.mod.json" in names:
                try:
                    d = json.loads(z.read("fabric.mod.json"))
                    for k, v in (d.get("depends") or {}).items():
                        f["deps"].setdefault(k, {"range": str(v), "type": "required"})
                    f["mod_version"] = f["mod_version"] or d.get("version")
                except Exception:
                    pass
            for n in names:
                # 内嵌库（jarJar / Fabric JIJ）
                if (n.startswith("META-INF/jarjar/") or n.startswith("META-INF/jars/")) \
                        and n.endswith(".jar"):
                    f["embeds"].append(n.rsplit("/", 1)[-1])
                # 顶层包前缀（渲染后端/大重构的信号）
                if n.endswith(".class"):
                    seg = n.split("/")
                    if len(seg) >= 3 and seg[0] not in ("META-INF",):
                        f["pkgs"]["/".join(seg[:2])] = f["pkgs"].get("/".join(seg[:2]), 0) + 1
            # mixin 兼容级别
            for n in names:
                if n.endswith(".json") and n.count("/") <= 1:
                    try:
                        d = json.loads(z.read(n))
                    except Exception:
                        continue
                    if isinstance(d, dict) and d.get("compatibilityLevel"):
                        f["mixin_levels"].add(str(d["compatibilityLevel"]))
    except Exception as e:
        f["error"] = str(e)
    f["pkgs"] = dict(sorted(f["pkgs"].items(), key=lambda x: -x[1])[:4])
    return f


class DepNames:
    """project_id → slug 的懒解析缓存。"""

    def __init__(self):
        self.m = {}

    def name(self, pid, fallback=None):
        if pid in self.m:
            return self.m[pid]
        try:
            self.m[pid] = http_json(f"{MR}/project/{pid}")["slug"]
        except Exception:
            self.m[pid] = fallback or f"id:{pid}"
        return self.m[pid]


def shortver(s):
    return s if len(s) <= 30 else s[:27] + "..."


def _major(range_str):
    """取 versionRange 里的第一个数字当"主版本"：'[0.6.11,0.6.12)'→'0'，'[1.0.0,2.0)'→'1'。
    用来判断依赖是否跨大版本（跨了 = 附属大概率不兼容）。"""
    m = re.search(r"(\d+)", range_str or "")
    return m.group(1) if m else ""


def brief_deps(vs):
    """一版版本的 required 依赖名字集合（不含可选）"""
    out = []
    for d in vs.get("dependencies", []):
        if d.get("dependency_type") == "required":
            out.append(d.get("project_id"))
    return out


def main():
    ap = argparse.ArgumentParser(description="模组版本谱系研究器（同名模组不同版本差异）")
    ap.add_argument("slugs", nargs="+", help="模组 slug（可多个）")
    ap.add_argument("--mc", help="只看某 MC 版本，如 1.20.1；不给则看全部历史版本")
    ap.add_argument("--loader", help="过滤 loader，如 forge / neoforge / fabric")
    ap.add_argument("--limit", type=int, default=15, help="最多展示多少版（默认 15，从新到旧）")
    ap.add_argument("--all-mc", action="store_true", help="显式声明看跨 MC 全历史（默认就是）")
    ap.add_argument("--fetch-jar", type=int, default=0, metavar="N",
                    help="额外下载最近 N 个版本的 jar 做包内结构比对（默认 0，不下）")
    ap.add_argument("--jar-pick", metavar="V1,V2",
                    help="指定要比对的版本号（逗号分隔，需与谱系里的版本号完全一致）；"
                         "跨大版本对比用，如 --jar-pick 1.20.1-5.10.3-forge,1.20.1-6.0.5+forge")
    ap.add_argument("--keep", action="store_true", help="--fetch-jar 时保留下载的 jar")
    ap.add_argument("--md", help="把报告写到指定 md 文件")
    args = ap.parse_args()

    L = []

    def say(s=""):
        print(s)
        L.append(s)

    names = DepNames()
    for slug in args.slugs:
        try:
            proj = http_json(f"{MR}/project/{slug}")
        except Exception as e:
            say(f"\n## {slug} —— 查询失败: {e}")
            continue
        if proj.get("project_type") != "mod":
            say(f"\n## {slug} —— 不是 mod（project_type={proj.get('project_type')}），跳过")
            continue
        vs = get_versions(slug, args.mc, args.loader)
        if not vs:
            say(f"\n## {slug} —— 无 {args.mc or '任意 MC'} / {args.loader or '任意 loader'} 文件")
            continue
        vs.sort(key=lambda x: x["date_published"], reverse=True)
        shown = vs[:args.limit]
        rel = [v for v in vs if v["version_type"] == "release"]
        say(f"\n## {slug}（{proj.get('title', slug)}）")
        say(f"- 总下载 {proj.get('downloads', 0):,}｜版本总数 {len(vs)}"
            f"｜其中正式版 {len(rel)}、测试版 {sum(1 for v in vs if v['version_type'] == 'beta')}、"
            f"内测版 {sum(1 for v in vs if v['version_type'] == 'alpha')}"
            f"（统计范围：{args.mc or '全部 MC'}／{args.loader or '全部 loader'}）")
        if rel:
            say(f"- **最新正式版**：`{rel[0]['version_number']}`（{rel[0]['date_published'][:10]}）"
                f"　｜　最新版本：`{vs[0]['version_number']}`（{TAG.get(vs[0]['version_type'])}，"
                f"{vs[0]['date_published'][:10]}）")
        if proj.get("source_url"):
            say(f"- 源码/主页：{proj['source_url']}")

        # ---- 谱系表 ----
        say(f"\n### 版本谱系（最近 {len(shown)} 版，从新到旧）\n")
        say("| 版本号 | 类型 | 发布 | 目标 MC | loader | 体积 | 下载 | required 依赖 |")
        say("|---|---|---|---|---|---|---|---|")
        for v in shown:
            f = next((x for x in v["files"] if x.get("primary")), v["files"][0])
            mcs = ",".join(v["game_versions"])
            mcs = mcs if len(mcs) <= 26 else mcs[:23] + "..."
            dcount = len(brief_deps(v))
            say(f"| `{shortver(v['version_number'])}` | {TAG.get(v['version_type'], v['version_type'])} "
                f"| {v['date_published'][:10]} | {mcs} | {','.join(v['loaders'])} "
                f"| {f['size'] / 1048576:.1f}M | {v.get('downloads', 0):,} | {dcount} |")

        # ---- 相邻版本 diff ----
        say(f"\n### 相邻版本差异（新版 ← 旧版）\n")
        chrono = list(reversed(shown))          # 从旧到新
        any_diff = False
        for i in range(1, len(chrono)):
            old, new = chrono[i - 1], chrono[i]
            f_new = next((x for x in new["files"] if x.get("primary")), new["files"][0])
            f_old = next((x for x in old["files"] if x.get("primary")), old["files"][0])
            ds = []
            if set(old["game_versions"]) != set(new["game_versions"]):
                ds.append(f"目标 MC `{','.join(sorted(set(old['game_versions'])))}`"
                          f" → `{','.join(sorted(set(new['game_versions'])))}`")
            if set(old["loaders"]) != set(new["loaders"]):
                ds.append(f"loader `{','.join(old['loaders'])}` → `{','.join(new['loaders'])}`")
            if old["version_type"] != new["version_type"]:
                ds.append(f"类型 {TAG.get(old['version_type'])} → **{TAG.get(new['version_type'])}**")
            # 依赖增删（id 转名字）
            oids, nids = set(brief_deps(old)), set(brief_deps(new))
            if oids != nids:
                add = [names.name(x) for x in nids - oids]
                rem = [names.name(x) for x in oids - nids]
                if add:
                    ds.append("新增依赖 " + "、".join(f"`{a}`" for a in add))
                if rem:
                    ds.append("移除依赖 " + "、".join(f"`{r}`" for r in rem))
            # 体积突变（换内嵌库/大重构的信号）
            if f_old["size"] and abs(f_new["size"] - f_old["size"]) / f_old["size"] > 0.25:
                pct = (f_new["size"] - f_old["size"]) / f_old["size"] * 100
                ds.append(f"体积 {f_old['size'] / 1048576:.1f}M → {f_new['size'] / 1048576:.1f}M"
                          f"（{pct:+.0f}%，可能换了内嵌库或大改）")
            if ds:
                any_diff = True
                say(f"- `{new['version_number']}` ← `{old['version_number']}`"
                    f"（{new['date_published'][:10]}）：" + "；".join(ds))
        if not any_diff:
            say("- 最近若干版之间没有检测到目标 MC / loader / 依赖 / 体积层面的变化"
                "（改动可能在代码或数值里，用 `--fetch-jar` 看包内结构）")

        # ---- jar 层比对 ----
        if args.fetch_jar > 0 or args.jar_pick:
            if args.jar_pick:
                want = {x.strip() for x in args.jar_pick.split(",") if x.strip()}
                picks = [v for v in vs if v["version_number"] in want]
                picks.sort(key=lambda x: x["date_published"], reverse=True)
                miss = want - {v["version_number"] for v in picks}
                if miss:
                    say(f"\n（找不到版本号：{'、'.join(sorted(miss))}——"
                        f"需与谱系表里的版本号完全一致，可加大 --limit 查看）")
            else:
                picks = shown[:args.fetch_jar]
            n = len(picks)
            tmp = Path(tempfile.gettempdir()) / f"modlineage_{slug}"
            tmp.mkdir(parents=True, exist_ok=True)
            say(f"\n### jar 包内结构比对（{n} 版，从新到旧）\n")
            facts = []
            for v in picks:
                f = next((x for x in v["files"] if x.get("primary")), v["files"][0])
                jar = next((x for x in v["files"] if x["filename"].endswith(".jar")), f)
                p = tmp / jar["filename"]
                # 已存在也要校验：半截 jar（上次下载被中断）复用会让解析结果全是空的
                if not p.exists() or not zip_ok(p):
                    p.unlink(missing_ok=True)
                    try:
                        http_get(jar["url"], p)
                        print(f"      [下载] {jar['filename']} ({p.stat().st_size / 1048576:.1f}M)")
                    except Exception as e:
                        p.unlink(missing_ok=True)
                        say(f"- `{v['version_number']}`：下载失败 {e}"
                            f"（老版本文件可能落在冷门 CDN 上，慢或偶发失败；重跑可续传）")
                        continue
                fa = jar_facts(p)
                facts.append((v, fa))
            if facts:
                say("| 版本号 | 包大小 | 目标 MC | 声明的依赖范围 | 内嵌库(jarJar) | 顶层包前缀 | mixin 级别 |")
                say("|---|---|---|---|---|---|---|")
                for v, fa in facts:
                    deps = "；".join(
                        f"{k}{' ' + d['range'] if d.get('range') else ''}"
                        for k, d in list(fa["deps"].items())[:6]) or "—"
                    emb = "、".join(fa["embeds"]) or "—"
                    pk = "、".join(f"{k}({c})" for k, c in fa["pkgs"].items()) or "—"
                    mx = "、".join(sorted(fa["mixin_levels"])) or "—"
                    say(f"| `{shortver(v['version_number'])}` | {fa['size'] / 1048576:.1f}M "
                        f"| {','.join(v['game_versions'])[:22]} | {deps} | {emb} | {pk} | {mx} |")
                # 跨版本结构断层分析（新 ← 旧）
                if len(facts) >= 2:
                    (odv, odf), (ndv, ndf) = facts[-1], facts[0]
                    say(f"\n**结构断层分析**（`{ndv['version_number']}` ← `{odv['version_number']}`）\n")
                    # ① 内嵌库(jarJar)集合变化 —— 换代最直接的证据
                    oe, ne = set(odf["embeds"]), set(ndf["embeds"])
                    if oe != ne:
                        bits = []
                        for x in sorted(ne - oe):
                            bits.append(f"新增 `{x}`")
                        for x in sorted(oe - ne):
                            bits.append(f"移除 `{x}`")
                        say(f"- **内嵌库变化**：" + "；".join(bits))
                    # ② 同一依赖的 versionRange 变化 —— 预测附属兼容性的硬判据
                    PLATFORM = {"minecraft", "forge", "neoforge", "fabricloader", "fabric", "java"}
                    chg, broke = [], []
                    for k in sorted(set(odf["deps"]) & set(ndf["deps"])):
                        o_r, n_r = odf["deps"][k]["range"], ndf["deps"][k]["range"]
                        if o_r != n_r:
                            chg.append(f"`{k}` `{o_r or '(空)'}` → `{n_r or '(空)'}`")
                            if k.lower() not in PLATFORM and _major(o_r) != _major(n_r):
                                broke.append(k)
                    if chg:
                        say("- **依赖范围变化**：" + "；".join(chg))
                    if broke:
                        say(f"- 🔴 **关键依赖跨大版本**：{'、'.join(f'`{b}`' for b in broke)}"
                            f"　→ 依赖该库**旧版**编译的附属/联动 mod 大概率启动即 "
                            f"`NoClassDefFoundError`（它们常只写下限不设上限，加载器不会拦），"
                            f"升级前务必逐个核对")
                    # ③ 顶层包名断层 = 代码级重写
                    gone = set(odf["pkgs"]) - set(ndf["pkgs"])
                    fresh = set(ndf["pkgs"]) - set(odf["pkgs"])
                    if gone and fresh:
                        say(f"- 🔴 **顶层包名断层**：`{'、'.join(sorted(gone))}` → "
                            f"`{'、'.join(sorted(fresh))}`（代码级大重构/换了命名空间）")
                    if not (oe != ne or chg or (gone and fresh)):
                        say("- 未发现内嵌库 / 依赖范围 / 顶层包名层面的变化（差异在代码或数值里）")
                say("")
            if not args.keep:
                for p in tmp.glob("*.jar"):
                    try:
                        p.unlink()
                    except Exception:
                        pass
            else:
                say(f"（jar 保留在 {tmp}）")

    text = "\n".join(L)
    if args.md:
        Path(args.md).write_text("# 模组版本谱系报告\n\n" + text, encoding="utf-8")
        print(f"\n报告 → {args.md}")


if __name__ == "__main__":
    main()

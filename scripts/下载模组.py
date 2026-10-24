#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
整合包模组下载器（Modrinth 直连 + CurseForge 走 MCIM 国内镜像）

用法:
  python 下载模组.py manifest.json --dir instance
  python 下载模组.py manifest.json --dir instance --only create,jei
  python 下载模组.py manifest.json --dir instance --clean
  python 下载模组.py manifest.json --search sodium          # 按名搜（PCL 同款 /v2/search），打印候选
  python 下载模组.py manifest.json --search sodium --pick 1 # 直接下载第 1 个候选
  python 下载模组.py manifest.json --channel latest          # 忽略"正式版优先"，取最新（旧行为）
  python 下载模组.py manifest.json --channel release         # 只用正式版，无正式版判缺位（严格）
  python 下载模组.py manifest.json --channel beta            # 强制 beta 通道

清单格式见 manifest.json：
  mods: [{"slug": ...} 或 {"type": "curseforge", "project": id, "file": id|null}]
  shaderpacks: [{"slug": ...}]   # 光影不带 loader 过滤
  可选字段（条目级）：
    "version": "1.20.1-5.10.3-forge"   锁死具体版本号（优先级最高，高于通道策略）
    "channel": "latest|stable|release|beta|alpha"  覆盖全局通道
特性:
  - **正式版优先**：默认 stable 通道 release > beta > alpha 逐级回退，回退时打印原因
  - Modrinth 自动递归解析 required 依赖（如中央厨房→农夫乐事）
  - CurseForge 全程走 mod.mcimirror.top，无需 API key；CF 侧按 releaseType 优先正式版
  - 精确文件名去重，重复执行幂等；失败重试 2 次
  - 报告记录每项 version_type，末尾汇总"包里有多少 beta/alpha"
"""
import argparse
import hashlib
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

try:  # Windows 终端中文兜底
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

MR = "https://api.modrinth.com/v2"
MCIM = "https://mod.mcimirror.top/curseforge/v1"
UA = {"User-Agent": "modpack-builder/1.0 (contact: local)"}

# ------------------------- 版本通道策略 -------------------------
# Modrinth version_type: release / beta / alpha
# CurseForge releaseType: 1=release 2=beta 3=alpha
CHANNEL_ORDER = ("release", "beta", "alpha")
CF_REL_NAME = {1: "release", 2: "beta", 3: "alpha"}
# CurseForge modLoaderType: 1=Forge 4=Fabric 5=Quilt 6=NeoForge
CF_LOADER_ID = {"forge": 1, "fabric": 4, "quilt": 5, "neoforge": 6}


def pick_version(vs, channel="stable"):
    """从版本列表里按通道策略挑一个。vs 元素为 Modrinth version 对象（或 CF file 对象需另走 cf_pick）。

    返回 (version, note)：
      channel="latest"          → 按发布日期最新（不看类型）
      channel="release/beta/alpha" → 只用该类型；没有则 version=None（判缺位）
      channel="stable"（默认）   → release > beta > alpha 逐级优先，取该类型里最新的
    note 为给用户看的说明（为什么要/不要提示）。
    """
    vs = sorted(vs, key=lambda x: x["date_published"], reverse=True)
    latest = vs[0]
    if channel == "latest":
        return latest, ""
    for t in CHANNEL_ORDER:
        cand = [v for v in vs if v.get("version_type") == t]
        if cand:
            if t == latest.get("version_type"):
                return cand[0], ""  # 最新版本身就是该类型，正常
            if channel == "stable":
                return cand[0], (f"最新为 {latest.get('version_type')} "
                                 f"{latest['version_number']}（{latest['date_published'][:10]}），"
                                 f"按正式版优先回退到 {t} {cand[0]['version_number']}"
                                 f"（{cand[0]['date_published'][:10]}）")
            return cand[0], ""
    return None, ""


def cf_pick(files, channel="stable"):
    """CurseForge 文件列表 → 按 releaseType 优先正式版。返回 (file, note)。"""
    files = sorted(files, key=lambda f: f.get("fileDate", ""), reverse=True)
    latest = files[0]
    if channel == "latest":
        return latest, ""
    for t in (1, 2, 3):
        cand = [f for f in files if f.get("releaseType") == t]
        if cand:
            nm = CF_REL_NAME[t]
            if t == latest.get("releaseType"):
                return cand[0], ""
            if channel == "stable":
                return cand[0], (f"最新为 {CF_REL_NAME.get(latest.get('releaseType'), '?')} "
                                 f"{latest.get('fileName')}（{(latest.get('fileDate') or '')[:10]}），"
                                 f"按正式版优先回退到 {nm} {cand[0].get('fileName')}")
            return cand[0], ""
    return None, ""


def net_hint(e):
    """把网络类异常翻译成「用户能看懂 + 知道下一步做什么」的一句话。
    2026-09-15 加：原来只打印 `HTTP 404` / `timed out` 这类原始异常，
    普通用户看不出是自己拼错了 slug、还是网络被限速了。"""
    if isinstance(e, urllib.error.HTTPError):
        if e.code == 404:
            return "资源不存在（slug 拼错了，或该 mod 没发布在这个平台）"
        if e.code == 429:
            return "请求太频繁被限流，等几分钟再跑（本脚本是串行请求，一般不会触发）"
        if 500 <= e.code < 600:
            return f"对方服务器出错（HTTP {e.code}），稍后重试；若是 Modrinth/CF 侧故障只能等"
        return f"HTTP {e.code}"
    if isinstance(e, urllib.error.URLError):
        reason = getattr(e, "reason", e)
        txt = str(reason).lower()
        if "timed out" in txt or "timeout" in txt:
            return "连接超时（国内直连 Modrinth/CF 常见）→ 稍后重跑即可续传，或改用 MCIM 镜像的 CF 条目"
        if "refused" in txt or "unreachable" in txt or "getaddrinfo" in txt:
            return "连不上对方服务器（网络不通/被墙/DNS 问题）→ 检查网络，或改用国内镜像通道"
        if "ssl" in txt or "certificate" in txt:
            return "TLS 握手失败（证书或代理干扰）→ 关掉代理/VPN 重试，或换镜像通道"
        return f"网络错误：{reason}"
    if isinstance(e, TimeoutError):
        return "连接超时 → 稍后重跑即可续传（.part 落盘机制不会污染已下载状态）"
    if isinstance(e, json.JSONDecodeError):
        return "对方返回的不是合法 JSON（多半是被网关拦截或限流页）→ 稍后重试"
    return f"{type(e).__name__}: {e}"


def _curl(args, timeout):
    """走 curl 子进程而不是 urllib。

    实测（2026-10-03，本机）：Modrinth 的 DNS 会把 api.modrinth.com 解析到 IPv6，
    而 **IPv6 出口不通** → urllib 直连必然卡到超时（30s 一个请求，整包 80 个 mod
    就是几十分钟零输出，看着像脚本死了）。同一时刻 `curl -4` 秒回。
    curl 还有两个附带好处：自带连接复用与 HTTP/2，CDN 大文件明显更快。

    强制 `-4`：这不是可选项，是本机连通 api.modrinth.com 的唯一路径。
    """
    cmd = ["curl", "-4", "-sS", "-L", "--max-time", str(timeout),
           "--retry", "3", "--retry-delay", "2", "--retry-all-errors",
           "-A", UA["User-Agent"]] + args
    # -s 必须加：curl 的进度输出含非 UTF-8 字节，text=True 解码会崩
    return subprocess.run(cmd, capture_output=True, errors="replace")


def http_json(url, timeout=30, retries=2):
    """带重试的 JSON 请求。2026-09-15：原来单发不重试，网络抖动一次就整条失败。"""
    last = None
    for i in range(retries + 1):
        try:
            p = _curl([url], timeout)
            body = p.stdout
            if p.returncode != 0:
                raise IOError(f"curl 退出码 {p.returncode}: {p.stderr.strip()[:200]}")
            if not body.strip():
                raise IOError(f"空响应（curl rc=0）：{url}")
            return json.loads(body)
        except Exception as e:
            last = e
        if i < retries:
            print(f"    [重试{i + 1}] {net_hint(last)}")
            time.sleep(1.5 * (i + 1))
    raise last


def http_download(url, dst: Path, timeout=300, retries=8, want_size=None):
    """下载单文件，返回字节数。

    🔴 **必须断点续传，但要用「显式 `-r offset-`」而不是 `-C -`**（2026-10-03 实测）：

    本机实测 Modrinth CDN 只有 **~83 KB/s**，65MB 的 jar 要下 13 分钟。
    问题是「怎样续」—— 试过两种：

    | 写法 | 结果 |
    |---|---|
    | `-C -` | 🔴 **会把文件拼坏**。Modrinth 的 URL 会 **307 重定向**到备用 CDN，<br>重定向后 `-C -` 的 Range 语义丢失，CDN 返回 200 完整响应，<br>curl 追加写入 → 实测 68780390 字节的目标下成 **112399052 字节**，<br>而 `zipfile` 仍判定「结构合法」（假绿），只有 sha1 抓得到 |
    | `-r "<offset>-"` | ✅ 正确。实测 CDN 返回 **206 Partial Content**，<br>每次精确取回请求的区间，拼起来 sha1 匹配 |

    所以这里**自己算已下字节数、显式传 Range**：
        `offset = dst.stat().st_size` → `curl -r offset-` → 追加
    并做两道推进判据（缺了它们就是在造坏文件）：
      · 给了 Range 却**一点没长** → CDN 忽略 Range → 报错，不重复追加
      · 下得**比期望还大** → Range 被重复应用 → 报错，从零重下

    目标文件是 `.part`（调用方校验 sha1 后原子改名）。
    """
    last = None
    for i in range(retries + 1):
        try:
            have = dst.stat().st_size if dst.exists() else 0
            if want_size and have == want_size:
                return have
            args = (["-r", f"{have}-", "-o", str(dst), url] if have
                    else ["-o", str(dst), url])
            p = _curl(args, timeout)
            if p.returncode not in (0, 28):
                raise IOError(f"curl 退出码 {p.returncode}: {p.stderr.strip()[:200]}")
            got = dst.stat().st_size if dst.exists() else 0
            if got == 0:
                raise IOError("下载得到 0 字节")
            if have and got == have:
                raise IOError(
                    f"带 Range 的请求没有推进（仍是 {got} 字节）："
                    f"该 CDN 不支持断点续传，需从零重下")
            if want_size and got > want_size:
                raise IOError(
                    f"已下 {got} > 期望 {want_size}：Range 被重复应用，需从零重下")
            return got
        except Exception as e:
            last = e
            if i == retries:
                raise
            pct = ""
            if dst.exists():
                try:
                    pct = f"（已下 {dst.stat().st_size / 1048576:.1f}MB，可续传）"
                except OSError:
                    pass
            print(f"    [续传{i + 1}/{retries}] {net_hint(e)}{pct}")
            time.sleep(2 * (i + 1))


def mr_latest(slug, mc, loader=None, version=None, channel="stable"):
    """返回 (project, version_obj, primary_file, note)。
    loader=None 不过滤加载器（光影用）。
    version 给版本号则锁该版本（如 journeymap 要锁 5.10.3，因为最新 6.0.x 删了老 API）——
    **锁版本优先级最高**，不再走通道策略。"""
    project = http_json(f"{MR}/project/{slug}")
    params = {"game_versions": json.dumps([mc])}
    if loader:
        params["loaders"] = json.dumps([loader])
    url = f"{MR}/project/{slug}/version?{urllib.parse.urlencode(params)}"
    vs = http_json(url)
    if not vs:
        return project, None, None, ""
    if version:
        v = next((x for x in vs if x["version_number"] == version), None)
        if not v:
            print(f"  [警告] {slug}: 找不到锁定版本 {version}（该 MC/loader 下），改走通道策略")
            v, note = pick_version(vs, channel)
            if not v:
                return project, None, None, note
        else:
            note = f"锁定版本（{v.get('version_type', '?')}）"
    else:
        v, note = pick_version(vs, channel)
        if not v:
            return project, None, None, note
    f = next((x for x in v["files"] if x.get("primary")), v["files"][0])
    # primary 可能是 .mrpack/.zip 等非 jar，mod 类项目优先挑 jar 文件
    if not f["filename"].endswith(".jar"):
        jar = next((x for x in v["files"] if x["filename"].endswith(".jar")), None)
        if jar:
            f = jar
    return project, v, f, note


def cf_files(project, mc, loader=None, page=50):
    """拉 CF 文件列表。按 loader 映射 modLoaderType，为空则放宽只按 MC 过滤。"""
    lid = CF_LOADER_ID.get((loader or "").lower())
    if lid:
        d = http_json(f"{MCIM}/mods/{project}/files"
                      f"?gameVersion={mc}&modLoaderType={lid}&pageSize={page}")
        data = d.get("data", []) if isinstance(d, dict) else []
        if data:
            return data
    d = http_json(f"{MCIM}/mods/{project}/files?gameVersion={mc}&pageSize={page}")
    return d.get("data", []) if isinstance(d, dict) else []


def main():
    ap = argparse.ArgumentParser(
        description="整合包模组下载器（默认正式版优先：release > beta > alpha 逐级回退）",
        epilog="示例：python 下载模组.py references/基础模组清单_26.2.json --dir \"D:/.minecraft/versions/我的包\"\n"
               "      python 下载模组.py 任意清单.json --search sodium --pick 1\n"
               "退出码：0=全部成功 / 1=有下载或解析失败 / 2=参数或清单问题")
    ap.add_argument("manifest", help="清单 JSON 路径（字段：mc / loader / mods[]；可参考 references/基础模组清单_26.2.json）")
    ap.add_argument("--dir", default="instance", help="实例目录，jar 落到 <dir>/mods（默认 instance）")
    ap.add_argument("--only", help="只下载指定 slug，逗号分隔（如 --only jei,sodium）")
    ap.add_argument("--clean", action="store_true", help="清空 mods/ 后重新下载")
    ap.add_argument("--search", help="按名字搜模组（PCL 同款 /v2/search，带清单的 mc/loader 过滤），打印候选")
    ap.add_argument("--pick", type=int, help="配合 --search：直接下载第 N 个候选")
    ap.add_argument("--channel", choices=["stable", "latest", "release", "beta", "alpha"],
                    help="版本通道：stable=正式版优先(默认) latest=取最新 release=只要正式版"
                         " beta/alpha=强制预发布；清单条目可各自覆盖")
    args = ap.parse_args()

    if not Path(args.manifest).is_file():
        print(f"[错误] 清单文件不存在：{args.manifest}")
        print("  · 若还没写清单，可直接用现成的：")
        print("      python 下载模组.py references/基础模组清单_26.2.json --dir <实例目录>")
        print("  · 想按名字找 mod：python 下载模组.py <任意清单> --search <关键词>")
        sys.exit(2)
    try:
        man = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"[错误] 清单不是合法 JSON：{args.manifest} 第{e.lineno}行 {e.msg}")
        print("  · 常见原因：多了尾逗号、用了单引号、注释没删干净（标准 JSON 不认这些）")
        print("  · 拿现成清单对着改：references/基础模组清单_26.2.json")
        sys.exit(2)
    if not isinstance(man, dict) or "mc" not in man or "loader" not in man:
        print("[错误] 清单缺少必填字段。清单顶层至少要有：")
        print('  { "mc": "1.20.1", "loader": "forge", "mods": [ {"slug": "...", "cat": "..."} ] }')
        print("（完整字段说明见 references/基础模组清单_26.2.json）")
        sys.exit(2)
    mc, loader = man["mc"], man["loader"]
    man_ch = man.get("channel", "stable")
    inst = Path(args.dir)
    mods_dir, sp_dir = inst / "mods", inst / "shaderpacks"
    if args.clean and mods_dir.exists():
        for j in mods_dir.glob("*.jar"):
            j.unlink()
    mods_dir.mkdir(parents=True, exist_ok=True)
    sp_dir.mkdir(parents=True, exist_ok=True)

    def channel_for(entry):
        """优先级：命令行 --channel > 条目 channel > 清单顶层 channel > stable"""
        return args.channel or entry.get("channel") or man_ch or "stable"

    only = set(args.only.split(",")) if args.only else None
    report, failures, downgrades = [], [], []
    seen_slugs = set()

    def want(entry):
        key = entry.get("slug") or f"cf:{entry['project']}"
        return not only or key in only or entry.get("slug") in only

    # --search：PCL 同款 /v2/search（faceted），2026-09-08 实测可用
    if args.search:
        facets = json.dumps([["project_type:mod"],
                             [f"versions:{mc}"], [f"categories:{loader}"]])
        q = {"query": args.search, "limit": 10, "index": "relevance", "facets": facets}
        try:
            hits = http_json(f"{MR}/search?" + urllib.parse.urlencode(q)).get("hits", [])
        except Exception as e:
            print(f"[搜索失败] {e}")
            sys.exit(1)
        if not hits:
            print(f"[无结果] MC {mc} / {loader} 下没搜到「{args.search}」")
            sys.exit(1)
        print(f"== /v2/search 候选：{args.search}（MC {mc} / {loader}，命中 {len(hits)}）==")
        for i, h in enumerate(hits, 1):
            print(f"  {i:2d}. {h['slug']:<30} {h['title'][:28]:<30} "
                  f"↓{h.get('downloads', 0):>10,}  {h.get('description', '')[:44]}")
        if not args.pick:
            print("加 --pick N 直接下载第 N 个；或把 slug 写进清单再用 --only 下载")
            return
        if not (1 <= args.pick <= len(hits)):
            print(f"[--pick 越界] 只能选 1~{len(hits)}")
            sys.exit(1)
        slug = hits[args.pick - 1]["slug"]
        print(f"== 下载候选 #{args.pick}: {slug} → {mods_dir} ==")
        only = {slug}                      # 只下这一个，忽略清单其余项
        man["mods"].insert(0, {"slug": slug, "cat": "search"})

    def _zip_ok(p: Path):
        try:
            with zipfile.ZipFile(p) as z:
                return z.testzip() is None
        except Exception:
            return False

    def dl(url, filename, target_dir, want_sha1=None, want_size=None):
        """下载一个文件到 target_dir。

        🔴 **want_sha1 / want_size 是防「静默损坏」的唯一可靠判据**（2026-10-03 实测）：
        断点续传靠 `-C -`，但 **CDN 对某些 Range 请求会返回完整响应而不是片段**
        —— curl 于是把整个文件**追加**到已有的半截后面。
        实测 aerial-hell 下成 112399052 字节（真实大小 68780390），
        而且 **`zipfile` 照样判定为「结构合法」**（能打开、testzip 通过），
        也就是说「zip 完整性校验」这一道完全拦不住它。
        只有 sha1 / size 对得上才算数。
        """
        dst = target_dir / filename
        is_arc = filename.endswith((".jar", ".zip"))

        def _verify(p: Path) -> bool:
            """大小 + sha1 双重校验；任一不符即判定损坏。"""
            try:
                got = p.stat().st_size
            except OSError:
                return False
            if want_size and got != want_size:
                print(f"         └ 大小不符：{got} ≠ 期望 {want_size}（续传被 CDN 拼坏了）")
                return False
            if want_sha1:
                h = hashlib.sha1()
                with open(p, "rb") as fp:
                    for b in iter(lambda: fp.read(1 << 20), b""):
                        h.update(b)
                if h.hexdigest() != want_sha1:
                    print(f"         └ sha1 不符：{h.hexdigest()} ≠ 期望 {want_sha1}")
                    return False
            if is_arc and not _zip_ok(p):
                return False
            return True

        if dst.exists():
            # 🔴 已存在的文件也必须校验：上一轮进程被 kill（或断流）留下的半截 jar
            # 既不会走异常分支、也不会被删除，下次"已存在即跳过"就把它永久留在包里。
            # 实测 jei-26.2-neoforge-30.29.0.201.jar 正好卡在 1048576 字节（1 个读块）。
            if _verify(dst):
                print(f"  [跳过] {filename} 已存在")
                return True
            print(f"  [重下] {filename} 已存在但校验不通过（上次下载未完成），已重新下载")
            # 同样要抗占用：孤儿 curl 攥着文件时 unlink 报 WinError 32，
            # 不接住的话整批下载崩在这里（实测后面几十个 mod 一个没下）。
            try:
                dst.unlink()
            except PermissionError:
                print(f"  [占用] {filename}: 已存在的坏文件被占用，无法删除")
                print(f"         └ 先结束残留 curl 进程（Get-CimInstance Win32_Process "
                      f"-Filter \"Name='curl.exe'\"）再重跑")
                failures.append(filename)
                return False
        part = dst.with_name(dst.name + ".part")
        try:
            size = http_download(url, part, want_size=want_size)
            # 🔴 完整性校验：**sha1/大小优先，zip 兜底**。
            #    断点续传可能被 CDN 拼坏（见 _verify 的 docstring），
            #    而坏文件不一定过不了 zip 校验 —— 只查 zip 会漏。
            if not _verify(part):
                try:
                    part.unlink(missing_ok=True)
                except PermissionError:
                    pass
                raise ValueError("下载产物校验不通过（sha1/大小/zip）")
            try:
                part.replace(dst)  # 原子落盘：中途中断只会留下 .part，不污染"已下载"状态
            except PermissionError:
                # 🔴 Windows 专属（2026-10-03 实测踩到）：
                #   文件被**别的进程**占用时 replace/unlink 都报
                #   `WinError 32 另一个程序正在使用此文件`。
                #   最常见的元凶是**上一轮被强杀的 python 留下的孤儿 curl**
                #   ——它还在写同一个 .part。Windows 不像 Unix 那样"删了照样能写"。
                #   这时**绝不能 raise**：一个孤儿进程不该让整批下载中断
                #   （实测直接崩在 traceback 里，后面几十个 mod 一个没下）。
                print(f"  [占用] {filename}: 文件被其他进程占用（多半是上一轮遗留的 curl）")
                print(f"         └ 已下 {size / 1048576:.1f}MB 留在 .part，"
                      f"先结束残留进程再重跑即可续传")
                failures.append(filename)
                return False
            print(f"  [下载] {filename}  ({size / 1048576:.1f} MB)")
            return True
        except Exception as e:
            # 🔴 **分两种情况，别一刀切 unlink**（2026-10-03 修）：
            #   a) 超时/断流 → part 还在且是**合法前缀**，保留它，下一轮
            #      `http_download` 的 `-C -` 接着下（国内 CDN ~83KB/s，
            #      22MB 要好几分钟，超时是常态；删了就白下）。
            #   b) 校验不过 → 上面已经把 part 删了（内容可能是被 CDN 拼坏的），
            #      这种不能留，否则下一轮会拿它当合法前缀续传，越续越烂。
            if part.exists():
                try:
                    kept = part.stat().st_size
                    print(f"  [失败] {filename}: {e}")
                    print(f"         └ 保留 .part {kept / 1048576:.1f}MB 供下次续传")
                except OSError:
                    print(f"  [失败] {filename}: {e}")
            else:
                print(f"  [失败] {filename}: {e}")
            failures.append(filename)
            return False

    def add_modrinth(slug, cat, version=None, depth=0, channel="stable"):
        if slug in seen_slugs:
            return
        seen_slugs.add(slug)
        try:
            project, v, f, note = mr_latest(slug, mc, loader, version, channel)
        except urllib.error.HTTPError as e:
            print(f"  [错误] {slug}: {net_hint(e)}")
            failures.append(slug)
            return
        except Exception as e:
            print(f"  [错误] {slug}: {net_hint(e)}")
            failures.append(slug)
            return
        # 同名整合包/资源包不是 mod（实测 clockwork → modpack，下回 60MB .mrpack）
        if cat not in ("shader",) and project.get("project_type") != "mod":
            print(f"  [跳过] {slug}: project_type={project.get('project_type')}，不是 mod")
            failures.append(slug)
            return
        if not v:
            print(f"  [缺位] {slug}: 无 {mc} {loader} 的 {channel} 版本文件"
                  f"（清单/命令行要求 --channel {channel}，放宽可去掉该参数）")
            failures.append(slug)
            return
        vt = v.get("version_type", "?")
        tag = {"release": "正式版", "beta": "测试版", "alpha": "内测版"}.get(vt, vt)
        print(f"  [{tag}] {slug}: {v['version_number']}  ({v['date_published'][:10]})")
        if note:
            print(f"      ↳ {note}")
        if vt != "release":
            downgrades.append(f"{slug}={v['version_number']}({vt})")
        ok = dl(f["url"], f["filename"], mods_dir, want_sha1=(f.get("hashes") or {}).get("sha1"),
                want_size=f.get("size"))
        report.append({"slug": slug, "version": v["version_number"], "version_type": vt,
                       "file": f["filename"], "cat": cat, "note": note, "ok": ok,
                       "source": "modrinth"})
        # 递归 required 依赖（依赖也走同一通道策略，避免"正式版 mod 拖一串 beta 库"）
        for dep in v.get("dependencies", []):
            if dep.get("dependency_type") == "required" and dep.get("project_id"):
                try:
                    p = http_json(f"{MR}/project/{dep['project_id']}")
                    dslug = p["slug"]
                    if dslug not in seen_slugs:
                        # 多平台项目常把 A 端的库也标成 required（如 lambdynamiclights
                        # 在 neoforge 版本上仍声明 fabric-api）。依赖自身若根本不支持
                        # 当前 loader，说明是跨平台元数据残留，跳过而非判失败。
                        if loader and loader not in (p.get("loaders") or [loader]):
                            print(f"  [跳过跨端依赖] {slug} → {dslug}"
                                  f"（{dslug} 不支持 {loader}，仅 {','.join(p.get('loaders') or [])}）")
                            continue
                        print(f"  [依赖] {slug} → {dslug}")
                        add_modrinth(dslug, "dep", None, depth + 1, channel)
                except Exception as e:
                    print(f"  [依赖解析失败] {dep['project_id']}: {e}")

    print(f"目标: MC {mc} / {loader} → {mods_dir}")
    print(f"通道: {args.channel or man_ch or 'stable'}"
          f"（stable = release > beta > alpha 逐级回退）\n== Modrinth ==")
    for m in man["mods"]:
        if m.get("type") == "curseforge" or not want(m):
            continue
        add_modrinth(m["slug"], m.get("cat", "?"), m.get("version"), 0, channel_for(m))

    print("\n== CurseForge (MCIM 镜像) ==")
    for m in man["mods"]:
        if m.get("type") != "curseforge" or not want(m):
            continue
        pid, fid = m["project"], m.get("file")
        ch = channel_for(m)
        if fid:
            meta = http_json(f"{MCIM}/mods/{pid}/files/{fid}")
            meta = meta.get("data", meta)  # MCIM 单文件端点包一层 data
            note = "锁定文件 id"
        else:
            try:
                files = cf_files(pid, mc, loader)
            except Exception as e:
                print(f"  [错误] CF:{pid}: {e}")
                failures.append(f"cf:{pid}")
                continue
            if not files:
                print(f"  [缺位] CF:{pid} 无 {mc} 文件")
                failures.append(f"cf:{pid}")
                continue
            meta, note = cf_pick(files, ch)
            if not meta:
                print(f"  [缺位] CF:{pid} 无 {ch} 通道文件")
                failures.append(f"cf:{pid}")
                continue
        rt = CF_REL_NAME.get(meta.get("releaseType"), "?")
        tag = {"release": "正式版", "beta": "测试版", "alpha": "内测版"}.get(rt, rt)
        print(f"  [{tag}] CF:{pid} {meta.get('fileName')}  ({(meta.get('fileDate') or '')[:10]})")
        if note:
            print(f"      ↳ {note}")
        if rt != "release":
            downgrades.append(f"CF:{pid}={meta.get('fileName')}({rt})")
        url = meta.get("downloadUrl")
        if not url:
            # MCIM 不代理 /download（404），需从 download-url 端点取 forgecdn 直链
            try:
                du = http_json(f"{MCIM}/mods/{pid}/files/{meta['id']}/download-url")
                url = du.get("data")
            except Exception:
                url = None
        if not url:
            # 兜底：有些文件的 downloadUrl 是空串、download-url 端点也返回空
            # （如 Design n' Decor 2.1.0 / CF 923238-7006245）。按 forgecdn 路径规则自拼：
            #   https://mediafilez.forgecdn.net/files/{fid//1000}/{fid%1000}/{fileName}
            # 必须用元数据里的原始 fileName（不是 displayName），拼错会 403。
            fid2 = meta.get("id")
            fn = meta.get("fileName")
            if fid2 and fn:
                url = f"https://mediafilez.forgecdn.net/files/{fid2 // 1000}/{fid2 % 1000}/{fn}"
                print(f"  [兜底] downloadUrl 为空，改用 forgecdn 路径拼: {url}")
        if not url:
            print(f"  [缺位] CF:{pid} 取不到下载地址")
            failures.append(f"cf:{pid}")
            continue
        ok = dl(url, meta["fileName"], mods_dir,
                want_sha1=((meta.get("hashes") or {}).get("sha1")
                           or (meta.get("hashes") or {}).get(1)),
                want_size=meta.get("fileLength"))
        report.append({"slug": f"cf:{pid}", "version": str(meta["id"]), "version_type": rt,
                       "file": meta["fileName"], "cat": m.get("cat", "?"), "note": note,
                       "ok": ok, "source": "curseforge"})

    print("\n== 光影包 ==")
    for s in man.get("shaderpacks", []):
        if only and s["slug"] not in only:
            continue
        try:
            project, v, f, note = mr_latest(s["slug"], mc, loader=None, channel=channel_for(s))
        except urllib.error.HTTPError as e:
            print(f"  [错误] {s['slug']}: {net_hint(e)}")
            failures.append(s["slug"])
            continue
        except Exception as e:
            print(f"  [错误] {s['slug']}: {net_hint(e)}")
            failures.append(s["slug"])
            continue
        if v:
            tag = {"release": "正式版", "beta": "测试版", "alpha": "内测版"}.get(
                v.get("version_type"), v.get("version_type"))
            print(f"  [{tag}] {s['slug']}: {v['version_number']}  ({v['date_published'][:10]})")
            if note:
                print(f"      ↳ {note}")
            dl(f["url"], f["filename"], sp_dir)
            report.append({"slug": s["slug"], "version": v["version_number"],
                           "version_type": v.get("version_type"),
                           "file": f["filename"], "cat": "shader", "note": note, "ok": True,
                           "source": "modrinth"})

    out = Path(args.manifest).with_name("download_report.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    jars = len(list(mods_dir.glob("*.jar")))
    print(f"\n完成: {jars} 个 jar，报告 → {out}")

    # 版本类型汇总：一眼看清包里有多少预发布版
    from collections import Counter
    cnt = Counter(r.get("version_type", "?") for r in report)
    if report:
        print("版本类型: " + "  ".join(f"{k} {v}" for k, v in cnt.most_common()))
    if downgrades:
        print(f"⚠ 非正式版 {len(downgrades)} 项（该 MC/loader 下没有 release，或清单指定了 beta 通道）:")
        for d in downgrades:
            print(f"    - {d}")
    if failures:
        print(f"\n失败 {len(failures)} 项: {', '.join(failures)}")
        print("  常见原因与处置：")
        print("    ① slug 拼错 / 该 mod 没发布在这个平台 → 用 --search <关键词> 查正确 slug")
        print("    ② 该 MC+loader 下确实没有文件（生态还没跟上）→ 换替代 mod 或降版本")
        print("    ③ 网络超时/断流 → 直接重跑本条命令：已下好的会跳过，只补失败的")
        print("       （重跑前可加 --only <失败的slug> 只补这几项，省时间）")
        sys.exit(1)


if __name__ == "__main__":
    main()

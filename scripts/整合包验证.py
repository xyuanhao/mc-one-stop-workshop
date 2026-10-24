# -*- coding: utf-8 -*-
"""整合包静态验证：在**玩家完整的实例目录**上跑七项检查，抓出启动必崩的问题。

用法：
    python 整合包验证.py <实例目录或mods目录> [--quiet]

七项检查：
    1) modId 重复      —— 多个「顶层 jar」提供同一 modid（同 modid 双份 = 启动即崩）
    2) 依赖闭包        —— 所有 [[dependencies.X]] 里 mandatory=true / type="required" 的 modid
                          是否都有提供者（缺一条 = 启动最后一步 Loading errors encountered 退出）
    3) 版本范围  ★     —— 提供者**存在**但版本不满足 versionRange（同样启动即崩，且报错信息
                          看着像"缺依赖"，极易误导）。典型现场：
                            create_central_kitchen 要求 ends_delight [2.6.1,)，实际只有 2.4
                            createbells 要求 geckolib [4.7.3]（精确），实际是 4.8.4
    4) 加载器错配      —— 另一个加载器的构建混进来（会被跳过，它的依赖随之缺失）。
                          **只报 fabric/quilt 专属构建**（文件名对加载器毫无判别力，见下）
    5) 语言加载器      —— klf / kotlinforforge 这类第三方 modLoader 是否有提供者
    6) 内容层一致性 ★  —— config/ftbquests、kubejs 这类**文件在但 mod 没装**：
                          游戏不报错、功能静默消失。实证：7 章 63 个任务书文件全在，
                          4 个 FTB mod 却被下载脚本落到了别的目录 → 游戏里根本没任务书
    7) 同功能互斥  ★   —— 装了两套做同一件事的（小地图 Xaero vs JourneyMap、
                          配方查看器 JEI/REI/EMI、渲染优化、光影）。不崩，但界面打架
    8) minecraft 区间  ★ 2026-09-24 新增 —— 每个 mod 声明的 minecraft 版本区间是否接受
                          **启动器实际解析出的** MC 版本。实证现场：清单写 26.1，启动器展开成
                          26.1.2，而 clumps/controlling/searchables 钉成 [26.1] → 启动即崩。
                          旧版只查 mod↔mod、且 minecraft 在 SKIP_DEPS 里，整类错误全漏。
                          可用 --mc <版本> 指定实际 MC 版本（不传则自动探测，探测不到跳过）

关键细节（踩过坑）：
    · jarJar 内嵌：很多 mod 把前置库塞进自己 jar 里 —— 必须解包登记，否则「依赖缺失」
      与「modId 重复」双双假阳性。⚠️ 内嵌目录**不固定**：老 Forge 用 META-INF/jarjar/、
      新版（如 create-new-age 1.2.0）用 META-INF/jars/ —— 只认前者必漏。
    · 双平台 jar：同一 jar 里可能**同时**有 mods.toml 和 neoforge.mods.toml
      （create_things_and_misc / Terralith / yet_another_config_lib_v3 ...）。
      两个都要读，平台判定取**多数票**，不能"见到 neoforge 就算 NeoForge"。
    · 文件名不能判加载器：Forge 包的 jar 本来就带 -forge-；garnished-...-neoforged.jar
      实为合法 Forge jar（mods.toml 里 modLoader="javafml"）。
    · 裸版本号（`15.20.0.106`）Forge **不检查** —— Maven 语义下它只是 recommendedVersion、
      restrictions 为空，而 Forge 判据是 hasRestrictions()。当成精确匹配会大量误报。
    · kotlinforforge 之类的 fat jar 没有 mods.toml，属正常，不计入问题。

退出码：0 = 全部通过 / 1 = 发现问题
"""
import io
import json
import re
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SKIP_DEPS = {"neoforge", "minecraft", "forge", "fml", "javafml", "lowcodefml",
             "java", "loader"}
BUILTIN_LOADERS = {"javafml", "lowcodefml", "fml", "neoforge"}   # NeoForge 自带，无需额外 mod 提供
TOML_NAMES = ("META-INF/neoforge.mods.toml", "META-INF/mods.toml")


# ---------- 版本区间匹配（Maven 风格 —— Forge / NeoForge 用的就是这套） ----------

def vparts(v: str):
    """版本字符串 -> 可比较的数字元组。

    🔴 2026-09-18 修正：**`-` 不能无条件当分隔符截断**。
      MC 生态里 `-` 有两种含义，靠上下文区分：
        ① 构建元数据/后缀标记：`2.6.1+forge.1.20.1`、`1.20.1-2.1.0-forge` 里的 `-forge`
        ② **MC 版本前缀分隔**：`1.20.1-2.1.0`（= MC 1.20.1 + 本 mod 2.1.0）、
           `1.20.1-2`、`1.20.1-3`（Iron's 系 lib 的写法）
      旧实现按 `[+-]` 切第一段，把 `1.20.1-2.1.0` 与 `1.20.1-3` 都截成 `1.20.1`
      → 两者相等，于是 `[1.20.1-2,1.20.1-3)` 被判成「2.1.0 不满足」（误报）。
      正确做法：**`+` 之后的构建元数据才丢弃，`-` 后面保留为后续版本段。**

    解析不出（空串、含 ${...} 占位符）时返回 None —— 调用方据此**跳过判断**，
    宁可漏报也不误报。
    """
    if not v or "$" in v:
        return None
    head = v.strip().split("+")[0]          # 只丢 `+` 构建元数据，`-` 保留
    nums = re.findall(r"\d+", head)
    return tuple(int(x) for x in nums) if nums else None


def parse_range(rng: str):
    """解析版本区间 -> (lo, lo_inc, hi, hi_inc)；无约束返回 None。

    实测 Forge/NeoForge 包里的写法分布（2026-09-18 普查 442 条）：
      422 条 Maven 区间 + 16 条裸版本号 + 4 条运算符写法，**两种都要认**：

    [1.0,2.0)        1.0 <= x < 2.0      （Maven，主流）
    [1.0,)           x >= 1.0
    [1.0]            x == 1.0            （方括号无逗号 = 精确匹配，钉子户写法）
    (,2.0]           x <= 2.0
    >=1.1.20 <1.2.0  区间用空格分隔的运算符写法（rechiseledcreate 就这么写）
    >=1.2.0          单边运算符
    1.0              裸版本号 -> **无约束**（见下）
    *                通配 -> 无约束

    🔴 裸版本号必须当「无约束」，不能当精确匹配（2026-09-18 实证）：
      Maven 语义里裸版本号只设 recommendedVersion、restrictions 为空，
      而 Forge 的判据是 `versionRange.hasRestrictions()` —— 空 restrictions 直接跳过检查。
      实证：trading_floor 写 jei "15.20.0.106"（裸），包里是 15.56.0.205，差了 36 个小版本，
      **却没出现在崩溃报告的 MOD 列表里**；同一次崩溃里带括号的 3 条全崩了。
      把裸版本号当精确匹配 → 会大量误报（minecraft 依赖几乎都是裸写的）。

    含 $ 占位符 / 空 / '?' 一律当无约束（避免误报）。
    """
    if not rng:
        return None
    rng = rng.strip()
    if not rng or rng in ("?", "*") or "$" in rng:
        return None
    if rng[0] in "[(":
        lo_inc = rng[0] == "["
        hi_inc = rng[-1] == "]"
        inner = rng[1:-1]
        if "," in inner:
            a, _, b = inner.partition(",")
            return (a.strip() or None, lo_inc, b.strip() or None, hi_inc)
        v = inner.strip()
        return (v, True, v, True)
    # 运算符写法：'>=1.1.20 <1.2.0' / '>=1.2.0' / '<1.2.14'
    if re.match(r"^[<>]", rng):
        lo = hi = None
        lo_inc = hi_inc = True
        for part in rng.split():
            m = re.match(r"^(>=|>|<=|<)(\S+)$", part)
            if not m:
                continue
            op, ver = m.group(1), m.group(2)
            if op == ">=":
                lo, lo_inc = ver, True
            elif op == ">":
                lo, lo_inc = ver, False
            elif op == "<=":
                hi, hi_inc = ver, True
            else:                       # '<'
                hi, hi_inc = ver, False
        if lo is None and hi is None:
            return None
        return (lo, lo_inc, hi, hi_inc)
    return None                         # 裸版本号 -> Forge 不检查


def satisfies_raw(ver: str, rng: str) -> bool:
    """satisfies() 的多区间外壳版：认 `[a],[b],[c]` 这种**离散多段区间**。

    实证 2026-09-24：KotlinLangForge-2.14.1 写的是
      `[1.21.9],[1.21.10],[1.21.11],[26.1],[26.1.1],[26.1.2],[26.2],[26.3]`
    旧实现把整串当一个区间解析 → `]` 之后还有 `,` 就当成"上界是 26.1"，
    于是 MC 26.1.2 被判成不满足（**假阳性**，见下方第 8 项检查）。
    按 `],[` 切分后**任一**子区间命中即通过。
    """
    if not rng:
        return True
    rng = rng.strip()
    if rng.count("[") > 1 or rng.count("(") > 1:
        parts = re.split(r"\]\s*,\s*\[", rng)
        return any(satisfies_raw(ver, p if p[:1] in ("[", "(") else "[" + p + "]")
                   for p in parts)
    return satisfies(ver, parse_range(rng))


def satisfies(ver: str, spec) -> bool:
    """ver 是否落在 spec 里。任一侧解析不出 -> True（不误报）。"""
    if spec is None:
        return True
    lo, lo_inc, hi, hi_inc = spec
    v = vparts(ver)
    if v is None:
        return True
    if lo:
        p = vparts(lo)
        if p is None:
            return True
        if v < p or (v == p and not lo_inc):
            return False
    if hi:
        p = vparts(hi)
        if p is None:
            return True
        if v > p or (v == p and not hi_inc):
            return False
    return True


def detect_game_mc(inst_root: Path):
    """探测启动器**实际安装**的 MC 版本。

    只认三处，都读不到就返回 None（该项检查会跳过，不误报）：
      · 实例目录下的 <名字>.json（官方启动器/PCL/HMCL 的实例版本 json）
      · 整合包 manifest.json / modrinth.index.json 里的 minecraft 版本
      · 顶层 options.json 之类无关文件一律不认
    """
    try:
        for p in sorted(inst_root.glob("*.json")):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(d, dict):
                continue
            for key in ("clientVersion", "minecraftVersion", "version"):
                v = d.get(key)
                if isinstance(v, str) and re.fullmatch(r"\d+(\.\d+){1,3}", v):
                    return v
            mc = (d.get("minecraft") or {})
            if isinstance(mc, dict):
                v = mc.get("version")
                if isinstance(v, str) and re.fullmatch(r"\d+(\.\d+){1,3}", v):
                    return v
    except Exception:
        pass
    return None


def pick_mods_dir(arg: str) -> Path:
    p = Path(arg)
    if p.name.lower() == "mods" and p.is_dir():
        return p
    if (p / "mods").is_dir():
        return p / "mods"
    return p


def pick_dirs(arg: str):
    """返回 (实例根, mods 目录)。

    内容层检查（任务书/kubejs/光影配置有没有对应的 mod）需要实例根目录，
    所以这里同时把根目录解析出来。传 mods 目录时，用它的父目录当根，
    但**只有根下确实有 config/ 之类才作数** —— 否则内容层检查会被跳过（见下）。
    """
    p = Path(arg)
    if p.name.lower() == "mods" and p.is_dir():
        return p.parent, p
    if (p / "mods").is_dir():
        return p, p / "mods"
    return p, p


# ---- 内容层 ↔ 运行层一致性 ----
# 判据：某些目录/配置文件**只有在对应 mod 装了才有意义**。
# 少了 mod，游戏**不会报错**，只是那部分功能静默消失 —— 这是最难发现的一类问题。
# 实证（2026-09-18）：田园纪事的 7 章 63 个任务书文件全在 config/ftbquests/ 下，
# 但 4 个 FTB mod 因为下载脚本路径算错落到了别的目录，**从未进过 mods/** ——
# 游戏照常启动、照常进世界，就是没有任务书。纯 jar 内部检查完全查不出这种问题。
CONTENT_RULES = [
    # (实例根下的路径, 必须有这些 modid 之一, 说明)
    ("config/ftbquests",              ["ftbquests"],  "FTB 任务书"),
    ("defaultconfigs/ftbquests-server.snbt", ["ftbquests"], "FTB 任务书（服务端预设）"),
    ("kubejs",                        ["kubejs"],     "KubeJS 脚本"),
    ("config/openloader",             ["openloader"], "OpenLoader"),
    ("config/canary.properties",      ["canary"],     "Canary 性能优化配置"),
]

# FTB 系列的从属关系：装了主 mod 还得有它的库
CONTENT_DEPS = {
    "ftbquests": ["ftblibrary", "ftbteams"],
}

# ---- 同功能互斥家族 ----
# ⚠️ 只列**真的互斥**的。性能优化类（FerriteCore / ModernFix / Canary /
#    EntityCulling / BadOptimizations / Clumps …）各管一摊、**互补**，不能当重复报。
# 每一组内部是"解法"：同一个解法的成员可以共存（Xaero 小地图+世界地图就是一套），
# 出现**两个不同解法**才算重复。
EXCLUSIVE_SETS = [
    ("小地图/世界地图", {
        "Xaero":      {"xaerominimap", "xaeroworldmap"},
        "JourneyMap": {"journeymap"},
        "VoxelMap":   {"voxymap"},
        "原版地图扩展": {"map_atlases", "antiqueatlas"},
    }),
    ("配方查看器", {
        "JEI": {"jei"},
        "REI": {"rei", "roughlyenoughitems"},
        "EMI": {"emi"},
    }),
    ("渲染优化", {
        "Embeddium/Rubidium": {"embeddium", "rubidium"},
        "Sodium":             {"sodium"},
        "Magnesium":          {"magnesium"},
    }),
    ("光影加载", {
        "Oculus":  {"oculus"},
        "Iris":    {"iris"},
        "OptiFine": {"optifine"},
    }),
    ("物品栏整理", {
        "IPN": {"inventoryprofilesnext"},
        "Inventorio": {"inventorio"},
    }),
]


def read_toml(zf: zipfile.ZipFile):
    for n in TOML_NAMES:
        if n in zf.namelist():
            return zf.read(n).decode("utf-8", "ignore")
    return None


def toml_name(zf: zipfile.ZipFile):
    """返回实际命中的 mods.toml 路径名（判 NeoForge/Forge 平台用）。"""
    for n in TOML_NAMES:
        if n in zf.namelist():
            return n
    return None


def main():
    if len(sys.argv) < 2 or "-h" in sys.argv or "--help" in sys.argv:
        print(__doc__)
        return 2
    quiet = "--quiet" in sys.argv
    # 🔴 --mc：启动器**实际解析出**的 MC 版本（不是整合包清单里写的那个，两者可能不同）。
    #    见下方第 8 项检查的说明 —— 不传就自动探测，探测不到该项跳过（不误报）。
    mc_arg = None
    for i, a in enumerate(sys.argv):
        if a == "--mc" and i + 1 < len(sys.argv):
            mc_arg = sys.argv[i + 1]
    raw_target = Path(sys.argv[1])
    if not raw_target.exists():
        print(f"[错误] 路径不存在：{raw_target}")
        print("  · 传整合包实例目录（<你的 .minecraft>/versions/<整合包名>）或它的 mods 目录")
        return 2
    inst_root, mods = pick_dirs(sys.argv[1])
    jars = sorted(mods.glob("*.jar"))
    if not jars:
        print(f"[错误] 在 {mods} 里没找到 jar")
        if mods.name != "mods":
            print("  · 这里既没有 mods/ 子目录、本身也不叫 mods —— 确认传的是整合包实例目录")
        else:
            print("  · mods/ 是空的 —— 先把模组放进去（可能还停在解压/下载阶段）")
        return 2

    providers = defaultdict(list)   # modid -> [来源]（内嵌来源带 " > " 标记）
    provided_ver = defaultdict(list)  # modid -> [版本串]（用于版本范围校验）
    needs = defaultdict(list)       # (发起者, 依赖modid, 版本范围) -> [jar]  只含 mandatory（依赖闭包用）
    all_needs = defaultdict(list)   # 同上，但**含 optional**（版本范围校验用，见下）
    bad_loaders, no_toml = [], []
    toml_seen = []                  # 出现过的 mods.toml 路径名（判平台用，neoforge.mods.toml 是硬证据）
    self_boot = set()               # 自己声明"我就是这个语言加载器"的 modLoader 名
    lang_consumers = []             # (需要的加载器名, 自身modid, jar) —— 需要别的 mod 提供语言加载器

    def register(raw: str, jar_name: str, tag: str = ""):
        segs = raw.split("[[mods]]")
        my_id = None
        if len(segs) > 1:
            # 只取 [[mods]] 到第一个 [[dependencies 之间的内容，
            # 免得误抓到后面别的字段
            head = segs[1].split("[[dependencies")[0]
            m = re.search(r"modId\s*=\s*['\"]([^'\"]+)['\"]", head)
            if m:
                my_id = m.group(1)
                providers[my_id].append(jar_name + tag)
                vm = re.search(r"^\s*version\s*=\s*['\"]([^'\"]*)['\"]", head, re.M)
                if vm and vm.group(1):
                    provided_ver[my_id].append(vm.group(1))
        elif not my_id:
            # 🔴 **内联数组写法**（2026-10-03 实测踩到）：
            #   mods.toml 不只有 `[[mods]]` 段，还有等价的内联形式
            #       mods = [ { modId = 'fokusapi', version = '4.5', ... } ]
            #   只认 `[[mods]]` 会让这类 jar **完全不被登记为提供者**
            #   → 依赖它的 mod 报「依赖缺失」，而去翻 mods 目录会发现 jar 明明在。
            #   实测踩到两个：FokusAPI-v4.5_MOD.jar / reactify-1.4.b1_MOD.jar
            #   （CoffeeG 系 mod 普遍这么写）。
            #   判据：`[[mods]]` 不存在，但 `mods = [` 存在 → 走内联分支。
            arr = re.search(r"^\s*mods\s*=\s*\[(.*?)^\s*\]", raw, re.M | re.S)
            if arr:
                body = arr.group(1)
                m = re.search(r"modId\s*=\s*['\"]([^'\"]+)['\"]", body)
                if m:
                    my_id = m.group(1)
                    providers[my_id].append(jar_name + tag)
                    vm = re.search(r"\bversion\s*=\s*['\"]([^'\"]*)['\"]", body)
                    if vm and vm.group(1):
                        provided_ver[my_id].append(vm.group(1))
        # modLoader：非 javafml/lowcodefml 的，说明它需要某个第三方语言加载器（如 klf）
        ml = re.search(r"^\s*modLoader\s*=\s*['\"]([^'\"]+)['\"]", raw, re.M)
        if ml:
            name = ml.group(1)
            if name.lower() not in BUILTIN_LOADERS:
                if my_id and name == my_id:
                    self_boot.add(name)          # 自举：它自己就是提供器
                else:
                    lang_consumers.append((name, my_id or "?", jar_name))
        for dm in re.finditer(r'\[\[dependencies\.([^\]]+)\]\](.*?)(?=\[\[|\Z)', raw, re.S):
            owner, blk = dm.group(1), dm.group(2)
            mid = re.search(r"modId\s*=\s*['\"]([^'\"]+)['\"]", blk)
            mand = re.search(r'mandatory\s*=\s*(true|false)', blk)
            typ = re.search(r"type\s*=\s*['\"]([^'\"]+)['\"]", blk)
            if not mid:
                continue
            vr = re.search(r"versionRange\s*=\s*['\"]([^'\"]+)['\"]", blk)
            key = (owner, mid.group(1), vr.group(1) if vr else "?")
            # 🔴 版本校验要收**全部**依赖，不能只看 mandatory（2026-09-18 实测）：
            #   Forge 的 mandatory=false 只管「完全缺失时不报错」，
            #   「mod 在、但版本不在区间内」**照样是崩溃级**错误。
            #   实证：create_central_kitchen 声明 ends_delight [2.6.1,) mandatory=false，
            #   delight   声明 ends_delight [2.5,)   mandatory=false —— 两者都因包里是 2.4 崩了。
            all_needs[key].append(jar_name)
            if (mand and mand.group(1) == "true") or (typ and typ.group(1) == "required"):
                needs[key].append(jar_name)

    for j in jars:
        low = j.name.lower()
        try:
            with zipfile.ZipFile(j) as z:
                tn = toml_name(z)
                raw = read_toml(z)
                if tn:
                    toml_seen.append(tn)
                if raw:
                    register(raw, j.name)
                else:
                    no_toml.append(j.name)
                # 登记 jarJar 内嵌 jar。
                # 实测教训（2026-09-18）：内嵌 jar 放哪个目录**不固定** ——
                #   老 Forge / JarJar 早期版本用 META-INF/jarjar/*.jar
                #   新版本（如 create-new-age 1.2.0）用 META-INF/jars/*.jar
                # 只认前者会让 esl / kelvin 这类内嵌前置被误判成「依赖缺失」。
                # 判据用 metadata.json 里登记的 path 最准，退化时扫两个目录。
                inner_paths = []
                for meta_name in ("META-INF/jarjar/metadata.json", "META-INF/jars/metadata.json"):
                    if meta_name in z.namelist():
                        try:
                            meta = json.loads(z.read(meta_name).decode("utf-8", "ignore"))
                            inner_paths += [e["path"] for e in meta.get("jars", []) if e.get("path")]
                        except Exception:
                            pass
                if not inner_paths:
                    inner_paths = [n for n in z.namelist()
                                   if re.match(r"META-INF/jar(s|jar)/[^/]+\.jar$", n)]
                for n in inner_paths:
                    if n not in z.namelist():
                        continue
                    try:
                        with zipfile.ZipFile(io.BytesIO(z.read(n))) as z2:
                            r2 = read_toml(z2)
                            if r2:
                                register(r2, j.name, tag=f" > {n.split('/')[-1]}")
                    except Exception:
                        pass
        except Exception as e:
            print(f"  [读取失败] {j.name}: {type(e).__name__}")

    # ---- 加载器错配 ----
    # 🔴 核心教训（2026-09-17/18 反复踩）：**文件名不能用来判加载器**。
    #   1) Forge 实例里绝大多数 jar 的文件名本来就带 -forge-，全部标红 = 纯噪音；
    #   2) 不少 mod 是**双平台 jar** —— 同一个 jar 里同时塞了 mods.toml 和 neoforge.mods.toml
    #      （如 create_things_and_misc / Terralith / yet_another_config_lib_v3），
    #      Forge 只读 mods.toml、NeoForge 只读 neoforge.mods.toml，两边都能跑；
    #   3) 还有文件名写 neoforged 但实际是合法 Forge jar 的（garnished-...-neoforged.jar，
    #      查 mods.toml 是 modLoader="javafml" loaderVersion="[47,)"）。
    #   所以：**能可靠判定的只有「fabric/quilt 专属构建混进 Forge 包」**。
    #   判据取确定性的排除法：名字含 fabric/quilt、且既不含 forge 也不含 neoforge。
    for j in jars:
        low = j.name.lower()
        has_family = ("forge" in low) or ("neoforge" in low) or ("neoforged" in low)
        if ("fabric" in low or "quilt" in low) and not has_family:
            bad_loaders.append(j.name)

    def top(v):
        return [x for x in v if " > " not in x]

    dup = {k: v for k, v in providers.items() if len(top(v)) > 1}
    inner = {k: v for k, v in providers.items() if len(v) > 1 and k not in dup}
    missing = [(o, d, vr, js[0]) for (o, d, vr), js in needs.items()
               if d not in SKIP_DEPS and d not in providers]

    # ---- 版本范围校验 ----
    # 提供者**在**、但版本不满足区间 —— 和「依赖缺失」一样是启动即崩，
    # 但崩溃信息会写 "only supports X 2.6.1 or above / Currently, X is 2.4"，
    # 看着像缺依赖，实际是版本旧了，最容易白查半天。
    # ⚠️ 遍历 all_needs（**含 optional**）：Forge 的 mandatory=false 只豁免「完全缺失」，
    #    「存在但版本不符」仍会崩 —— 只查 mandatory 会漏掉一半以上的现场。
    # 多提供者（顶层 + jarJar 内嵌）时取**最高版本**为准（加载器也是这么取的）。
    ver_conflicts = []
    for (owner, dep, rng), jars_of in sorted(all_needs.items()):
        if dep in SKIP_DEPS or dep not in providers:
            continue
        spec = parse_range(rng)
        if spec is None:
            continue
        cand = [v for v in provided_ver.get(dep, []) if vparts(v)]
        if not cand:
            continue                      # 提供者版本解析不出 -> 不判，避免误报
        best = max(cand, key=vparts)
        if not satisfies(best, spec):
            ver_conflicts.append((owner, dep, rng, best, jars_of[0]))

    # ---- 第 8 项：mod 声明的 minecraft 区间 vs 实际游戏版本 ----
    # 🔴 2026-09-24 新增。实证现场：整合包清单写 MC "26.1"，PCL2 却把 26.1 展开成最新补丁
    #    **26.1.2**（NeoForge 26.1.2.109 绑的就是 MC 26.1.2）；而 clumps / controlling /
    #    searchables 把区间钉成 [26.1]（只认 26.1）→ 启动直接
    #      "Mod clumps requires minecraft 26.1 / Currently, minecraft is 26.1.2"
    #    被拒绝加载。旧版验证器的 minecraft 在 SKIP_DEPS 里、且只查 mod↔mod，
    #    **完全漏掉这一整类错误**，玩家只能靠崩溃日志反推。
    #    判据必须用「启动器实际解析出的 MC 版本」，不能用清单里写的那个。
    resolved_mc = mc_arg or detect_game_mc(inst_root)
    mc_conflicts = []
    if resolved_mc:
        for (owner, dep, rng), jars_of in sorted(all_needs.items()):
            if dep != "minecraft":
                continue
            spec = parse_range(rng)
            if spec is None:
                continue
            if not satisfies_raw(resolved_mc, rng):
                mc_conflicts.append((owner, rng, jars_of[0]))

    print(f"扫描 {len(jars)} 个 jar @ {mods}")
    print(f"  modId 提供者 {sum(len(v) for v in providers.values())} 个（含 jarJar 内嵌）")
    if resolved_mc:
        print(f"  （实际游戏 MC 版本 {resolved_mc} —— 据此核对每个 mod 的 minecraft 区间）")
    else:
        print(f"  （未能确定实际游戏 MC 版本 —— 第 8 项跳过；可用 --mc <版本> 指定）")
    print()

    print("### 1. modId 重复（仅顶层 jar 之间算问题）")
    if dup:
        for k, v in sorted(dup.items()):
            print(f"  ✗ {k}")
            for x in v:
                print(f"       {x}")
    else:
        print("  ✓ 无")
    if inner and not quiet:
        print(f"  （另有 {len(inner)} 个 modid 由多个 jar 以 jarJar 内嵌携带 —— 正常，NeoForge 取最高版本）")
        for k, v in sorted(inner.items())[:10]:
            print(f"       {k}: " + " + ".join(x.split(' > ')[-1] for x in v))

    print("\n### 2. 依赖闭包")
    if missing:
        for o, d, vr, jar in missing:
            print(f"  ✗ {o} 需要 {d} {vr}   （来自 {jar}）")
    else:
        n = len([k for k in needs if k[1] not in SKIP_DEPS])
        print(f"  ✓ {n} 条 mandatory 依赖都有提供者")

    print("\n### 3. 版本范围")
    if ver_conflicts:
        for o, d, rng, got, jar in ver_conflicts:
            print(f"  ✗ {o} 要求 {d} {rng}，但包里是 {got}   （来自 {jar}）")
        print("     → 症状：启动即崩，报「only supports X <版本> or above / Currently, X is <版本>」")
        print("     → 别被误导成『缺依赖』——依赖在，只是**版本不对**：升级被依赖 mod，")
        print("        或反过来降级/移除那个卡版本要求的 mod（谁的要求更窄就先看谁）")
    else:
        n = len([k for k in needs if k[1] not in SKIP_DEPS and k[2] not in ("?", "")])
        print(f"  ✓ {n} 条带版本约束的依赖都满足")

    print("\n### 8. minecraft 版本区间 vs 实际游戏版本")
    if not resolved_mc:
        print("  – 跳过（未确定实际游戏 MC 版本，可用 --mc <版本> 指定）")
    elif mc_conflicts:
        for owner, rng, jar in mc_conflicts:
            print(f"  ✗ {owner} 只要 minecraft {rng}，但实际是 {resolved_mc}   （来自 {jar}）")
        print("     → 症状：启动即崩，报「Mod X requires minecraft A / Currently, minecraft is B」")
        print("     → 多半是**启动器把清单里的 MC 版本展开成了另一个补丁号**，或某个 mod 装了旧构建。")
        print(f"     → 对策：把游戏版本钉死到 {resolved_mc}（manifest 里写 {resolved_mc}），")
        print("        并把上面这些 mod 换成支持该补丁号的构建；用 check_mc_range.py 可全量核对")
    else:
        print(f"  ✓ 所有 mod 的 minecraft 区间都接受 {resolved_mc}")

    print("\n### 4. 加载器错配")
    if bad_loaders:
        for x in bad_loaders:
            print(f"  ✗ {x}")
    else:
        print("  ✓ 无")

    # 提供者判据：语言加载器名通常就等于「提供它的那个 mod 的 modId」
    # （例：KotlinLangForge 的 modId 就是 klf，虽然它自己用 javafml 加载、通过 ServiceLoader 注册提供器；
    #  而 KotlinForForge/KFF 的 modId 是 kotlinforforge —— 名字像，但不是同一个，互不满足）
    missing_lang = [(n, jid, jar) for n, jid, jar in lang_consumers
                    if n not in providers and n not in self_boot]
    print("\n### 5. 语言加载器（klf / kotlinforforge 这类第三方 modLoader）")
    if missing_lang:
        for n, jid, jar in missing_lang:
            print(f"  ✗ {jid} 需要语言加载器 '{n}'，但没有任何 mod 提供它   （来自 {jar}）")
        print("     → 症状：启动即失败，日志 `Mod File ... needs language provider <名> to load`")
        print("     → 陷阱：提供它的 mod 名字可能与「看起来像」的库不是同一个")
        print("        （比如 klf 由 KotlinLangForge 提供，而 KotlinForForge/KFF 提供的是 kotlinforforge）")
    else:
        print(f"  ✓ {len(lang_consumers)} 个 mod 用到第三方语言加载器，提供者都在")

    # ---- 6. 内容层 ↔ 运行层一致性 ----
    # 「文件在、mod 不在」= 功能静默消失，游戏不报错 —— 纯 jar 检查查不出来。
    content_gaps = []
    have_root = (inst_root / "config").is_dir() or (inst_root / "mods").is_dir()
    if have_root:
        for rel, need_any, label in CONTENT_RULES:
            if not (inst_root / rel).exists():
                continue
            if not any(m in providers for m in need_any):
                content_gaps.append((rel, need_any, label))
        # 从属库：主 mod 在但库不在
        for main_id, libs in CONTENT_DEPS.items():
            if main_id in providers:
                for lib in libs:
                    if lib not in providers:
                        content_gaps.append((f"{main_id}（已装）", [lib], "从属前置库"))

    print("\n### 6. 内容层 ↔ 运行层一致性（文件在但 mod 没装 = 功能静默消失）")
    if not have_root:
        print("  － 跳过（传的是 mods 目录，拿不到实例根，无法核对 config/ 等）")
    elif content_gaps:
        for rel, need, label in content_gaps:
            print(f"  ✗ 检测到 {rel}（{label}），但没有 {' / '.join(need)}")
        print("     → 症状：游戏照常启动进世界，**就是那部分功能不见了、且不报任何错**")
        print("        （典型：任务书文件全在 config/ftbquests/，但 FTB mod 没进包 → 没有任务书）")
        print("     → 排查：那个 mod 是不是下到别的目录去了？下载脚本若按「相对脚本位置」"
              "算输出路径，换目录执行就会静默落错地方")
    else:
        n = len([r for r, _, _ in CONTENT_RULES if (inst_root / r).exists()])
        print(f"  ✓ {n} 处内容层文件都有对应 mod 支撑")

    # ---- 7. 同功能互斥（重复装了两套） ----
    exclusive_hits = []
    for label, solutions in EXCLUSIVE_SETS:
        present = [(name, sorted(ids & set(providers))) for name, ids in solutions.items()
                   if ids & set(providers)]
        if len(present) > 1:
            exclusive_hits.append((label, present))

    print("\n### 7. 同功能互斥（装了两套做同一件事的）")
    if exclusive_hits:
        for label, present in exclusive_hits:
            print(f"  ⚠ {label}：装了 {len(present)} 套 —— " +
                  "；".join(f"{n}({','.join(ids)})" for n, ids in present))
        print("     → 不一定会崩，但界面重叠、键位打架、白白多占资源 —— 留一套就行")
        print("     → 注意：「性能优化」类（FerriteCore/ModernFix/Canary/EntityCulling…）"
              "各管一摊、是**互补**的，不在本项判据内")
    else:
        print("  ✓ 无")

    if no_toml and not quiet:
        print(f"\n（{len(no_toml)} 个 jar 无 mods.toml，属正常：{', '.join(no_toml[:4])}）")

    ok = not (dup or missing or ver_conflicts or bad_loaders or missing_lang
              or content_gaps or exclusive_hits or mc_conflicts)
    print(f"\n结论：{'✅ 八项全通过' if ok else '⚠ 有问题需处理'}")
    if not ok:
        print("\n【怎么修】")
        if dup:
            print(f"  ① modId 重复 {len(dup)} 组：mods/ 里同一 mod 有两份 → 删掉旧的那份")
            print("     不确定哪个是旧的？看文件名里的版本号，或按修改时间取新的。")
        if missing:
            print(f"  ② 依赖缺失 {len(missing)} 条：上面 ✗ 行里『来自 <jar>』是本包里有谁需要它，")
            print("     按『需要 <modid>』这个名字去 Modrinth/CF 补下载，丢进 mods/ 再跑一次")
        if ver_conflicts:
            print(f"  ③ 版本不匹配 {len(ver_conflicts)} 条：优先**升级被依赖的那个 mod**")
            print("     （去 Modrinth/CF 版本列表找满足区间的最新版，别只看 release ——")
            print("      很多 mod 在 1.20.1 上最新的几个版本标的是 beta，漏掉它们就会装到过旧的版本）")
            print("     如果要求方写的是**精确版本**（如 [4.7.3]），通常是作者没维护好的小 mod，")
            print("     优先考虑换掉/移除它，而不是为它把整条链降级。")
        if bad_loaders:
            print(f"  ④ 加载器错配 {len(bad_loaders)} 个：找对应加载器的构建换掉")
        if missing_lang:
            print(f"  ⑤ 语言加载器缺失 {len(missing_lang)} 个：去装提供该加载器的 mod（见上方陷阱说明）")
        if content_gaps:
            print(f"  ⑥ 内容层缺 mod {len(content_gaps)} 处：把缺的 mod 补进 mods/")
            print("     常见成因：**下载脚本把 jar 落到了别的目录**（比如按相对脚本位置算路径，")
            print("     换目录执行就静默落错）。全盘搜一下那个文件名，别重新下一遍：")
            print("       find <工作目录> -iname '*<modid>*' -not -path '*/config/*'")
            print("     补进去后**必须重跑验证** —— 这类问题游戏不会报错，只能靠本项抓。")
        if exclusive_hits:
            print(f"  ⑦ 同功能重复 {len(exclusive_hits)} 组：每组留一套（删掉你不用的那个）")
            print("     留着不崩，但界面/键位会打架。选型时就该定好，别等装完再删。")
        if mc_conflicts:
            print(f"  ⑧ minecraft 区间冲突 {len(mc_conflicts)} 个：把游戏版本钉死到实际 MC，")
            print("     并把这些 mod 换成支持该补丁号的构建（见上方对策）")
        print("     改完再跑一次，直到『八项全通过』为止。")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

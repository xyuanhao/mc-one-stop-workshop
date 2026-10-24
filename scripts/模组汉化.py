#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模组汉化 —— 给整合包里的 mod **补中文，但不改任何 jar**。

产出是一个**资源包**（`assets/<命名空间>/lang/zh_cn.json`），放进
`.minecraft/resourcepacks/` 后在游戏里启用即可。mod 本体一个字节都不动。

【为什么用资源包，而不是改 jar】
  · 不动 mod → 更新 mod 时汉化不丢，也不会被校验/签名找麻烦
  · MC 的语言加载是**按键合并**、高优先级覆盖低优先级
  · 所以资源包里**只放 mod 自己没有的那些键**最安全：
    既不会盖掉 mod 自带的好译文，体积也极小
    （实测：66k 条里补了 11.3k 条，包只有 0.12 MB）

🔴 **先装 I18nUpdateMod（必装项），再跑这个脚本。**
   它会自动拉 CFPA 中文包，覆盖主流 mod。本脚本只补 **CFPA 也没覆盖**的那部分，
   所以**必须让它先跑过一次**（产生缓存 `~/.i18nupdatemod/`），本脚本会自动读它来"让位"。

   为什么要让位：MC 资源包是**同 key 高优先级覆盖低优先级**。如果本包和 CFPA 包里
   有同一个 key，本包那些**脚本合成的译文**可能盖掉 CFPA 的**人工译文** —— 那是倒退。
   实测：不让位时本包 11,349 条（124 KB），让位后只留 3,719 条（52 KB），一字不亏。

【四级翻译策略】
  ① 短语精确命中 —— 从**原版 en_us/zh_cn 对齐** + 包内已汉化 mod 的对齐条目建对照表
                     （这步质量最高；已汉化的 mod 就是最好的术语库）
  ② 词级合成     —— 逐词查词典，按中文习惯拼装（修饰语在前、中文之间不加空格）
  ③ 「A of B」    —— 整段处理成「B之A」（B 可以是多个词）
  ④ 都不行       —— **保留英文**。乱翻比不翻更糟。

【用法】
    python 模组汉化.py <mods目录> [-o 输出.zip]                 # 自动找 ~/.i18nupdatemod 让位
    python 模组汉化.py <mods目录> --cfpa <CFPA包.zip> [...]     # 手动指定让位对象
    python 模组汉化.py <mods目录> --no-cfpa                     # 不让位（只在没装 I18n 时用）

【退出码】0=成功 / 1=没产出 / 2=参数或路径问题
"""
import argparse
import collections
import json
import pathlib
import re
import sys
import zipfile

HERE = pathlib.Path(__file__).parent
WORDS_FILE = HERE / "模组汉化词库.json"

# ---------------------------------------------------------------- 人工词库
# 🔴 人工词条必须**覆盖**自动推导的结果。自动推导（拿整条物品名当单词译法）
#    会混进脏数据，实测出现过 `the`→"理论"、`when`→"时间"、`animated`→"舞刃"。
try:
    EXTRA_WORDS = json.loads(WORDS_FILE.read_text(encoding="utf-8"))
except Exception:
    EXTRA_WORDS = {}
    print("⚠ 词库文件缺失：" + str(WORDS_FILE))

CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
FMT_RE = re.compile(r"(§[0-9a-fk-orA-FK-OR])|(&[0-9a-fk-orA-FK-OR])", re.I)
PLACEHOLDER_RE = re.compile(r"%(\d+\$)?[sdifgcxSDIFGCX%]|\$\{[^}]*\}|\{[^{}]*\}")
# ⚠️ 格式码是「§ + 恰好一个字符」。写成 `§[0-9a-fk-or]+` 会贪婪吃掉后面的字母
#    （实测把 `§6Bloodwake` 切成 `§6Blood` + `wake`，翻译直接崩）
SPLIT_RE = re.compile(r"(§[0-9a-fk-orA-FK-OR]|%\d*\$?[sdifgcxSDIFGCX%]|\{[^{}]*\})")

# 🔴 出现这些词说明是**句子**不是名字。逐词硬翻句子出来的中文是垃圾
#    （实测 "Gems are returned on craft" → "宝石是returned开合成"），
#    宁可保留英文。`of` 例外，走「A of B → B之A」。
STOPWORDS = {
    "the", "a", "an", "and", "or", "to", "in", "on", "at", "by", "for",
    "with", "when", "if", "while", "is", "are", "be", "been", "am", "was",
    "were", "have", "has", "had", "do", "does", "did", "can", "could", "will",
    "would", "should", "may", "might", "must", "you", "your", "it", "its",
    "this", "that", "these", "those", "there", "here", "from", "as", "not",
    "no", "but", "so", "then", "than", "into", "over", "under", "about",
    "after", "before", "between", "during", "through", "up", "down", "out",
    "off", "per", "also", "only", "same", "other", "every", "both", "each",
    "all", "any", "more", "most", "less", "some", "which", "who", "whom",
    "what", "how", "why", "where", "because", "since", "until", "unless",
    "returns", "returned", "increase", "increases", "decrease", "decreases",
    "adds", "grants", "deals", "deal", "takes", "take", "gives", "give",
}

PHRASE: dict[str, str] = {}
WORD: dict[str, str] = {}


# ---------------------------------------------------------------- 步骤 1
def extract_langs(mods_dir: pathlib.Path):
    """从所有 mod jar 里抽 en_us / zh_cn（按命名空间归并）。"""
    data = collections.defaultdict(lambda: {"en_us": {}, "zh_cn": {}})
    for jar in sorted(mods_dir.glob("*.jar")):
        try:
            z = zipfile.ZipFile(jar)
        except Exception:
            continue
        for n in z.namelist():
            if not (n.startswith("assets/") and "/lang/" in n and n.endswith(".json")):
                continue
            p = n.split("/")
            if len(p) < 4:
                continue
            ns, lang = p[1], p[-1][:-5]
            if lang not in ("en_us", "zh_cn"):
                continue
            try:
                d = json.loads(z.read(n).decode("utf-8-sig"))
            except Exception:
                continue
            if isinstance(d, dict):
                # 同一 ns 可能散在多个 jar（附属 mod）；先到先得，别覆盖
                for k, v in d.items():
                    data[ns][lang].setdefault(k, v)
    return data


# ---------------------------------------------------------------- 步骤 2
def build_phrase_dict(data, van_en=None, van_zh=None):
    """整串英文 → 整串中文 的对照表。原版条目权重更高（×3）。"""
    votes = collections.defaultdict(collections.Counter)

    def add(en, zh):
        if not isinstance(en, str) or not isinstance(zh, str):
            return
        en, zh = en.strip(), zh.strip()
        if en and zh and en != zh:
            votes[en][zh] += 1

    if van_en and van_zh:
        for k, v in van_en.items():
            if k in van_zh:
                for _ in range(3):
                    add(v, van_zh[k])
    for ns in data:
        en, zh = data[ns]["en_us"], data[ns]["zh_cn"]
        for k, v in en.items():
            if k in zh:
                add(v, zh[k])
    return {k: c.most_common(1)[0][0] for k, c in votes.items()}


# ---------------------------------------------------------------- 步骤 2.5
def _solve_word(known, zh, votes):
    """多词短语里「只差一个词」时，把已知词的译法在 zh 里按顺序定位，
    夹缝中剩下的就是那个未知词的译法。夹缝不唯一 → 不敢猜，返回 None。"""
    seq = []
    for w in known:
        c = [t for t, _ in votes.get(w, {}).most_common(1)]
        if not c:
            return None
        seq.append(c[0])
    pos, gaps = 0, []
    for s in seq:
        i = zh.find(s, pos)
        if i < 0:
            return None
        if i > pos:
            gaps.append(zh[pos:i])
        pos = i + len(s)
    if pos < len(zh):
        gaps.append(zh[pos:])
    if len(gaps) != 1:
        return None
    g = gaps[0].strip()
    if not g or len(g) > 10:
        return None
    if not re.fullmatch(r"[\u4e00-\u9fff\u3400-\u4dbfA-Za-z0-9]*", g):
        return None
    return g


def build_word_dict(phrase):
    """从短语对照表反推出**单词**对照表（这是覆盖率的关键）。

    ① 短语本身只有一个英文词 → 直接成对
    ② 引导推导：迭代 3 轮，多词短语里只差一个词时反解出该词译法

    ⚠️ 推导结果**可信度低于**人工词库，所以主流程里人工词条后写入、覆盖它。
    """
    votes = collections.defaultdict(collections.Counter)
    for en, zh in phrase.items():
        w = en.split()
        if len(w) == 1 and re.fullmatch(r"[A-Za-z][A-Za-z\-']*", w[0]):
            if len(zh) <= 10 and re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9・·\- ]*", zh):
                votes[w[0].lower()][zh] += 1
    for _ in range(3):
        new = {}
        for en, zh in phrase.items():
            w = en.split()
            if not (2 <= len(w) <= 4) or len(zh) > 20:
                continue
            if not all(re.fullmatch(r"[A-Za-z][A-Za-z\-']*", x) for x in w):
                continue
            lower = [x.lower() for x in w]
            unk = [i for i, x in enumerate(lower) if x not in votes]
            if len(unk) != 1:
                continue
            i = unk[0]
            g = _solve_word([lower[j] for j in range(len(lower)) if j != i], zh, votes)
            if g:
                new.setdefault(lower[i], collections.Counter())[g] += 1
        if not new:
            break
        for k, c in new.items():
            if k not in votes:                 # 不覆盖 ① 的干净结果
                for v, n in c.items():
                    votes[k][v] += n
    return {k: c.most_common(1)[0][0] for k, c in votes.items()}


# ---------------------------------------------------------------- 步骤 2.7
def load_cfpa_keys(extra_zips, quiet=False):
    """收集 CFPA 汉化包里「已经有中文」的键，用来让位（不覆盖）。

    来源两处：
      · 显式传的 `--cfpa <zip>`
      · **I18nUpdateMod 的缓存** `~/.i18nupdatemod/<版本>/*.zip`
        （它首次运行后会下 1.20/1.19/1.18 并转成当前版本，这里全部读进来）
    """
    import glob
    cands = list(extra_zips)
    cache = pathlib.Path.home() / ".i18nupdatemod"
    if cache.is_dir():
        cands += glob.glob(str(cache / "*" / "*.zip"))

    keys = collections.defaultdict(set)
    used = []
    for p in cands:
        try:
            z = zipfile.ZipFile(p)
        except Exception:
            continue
        n_before = sum(len(v) for v in keys.values())
        for n in z.namelist():
            if n.startswith("assets/") and "/lang/zh_cn.json" in n:
                try:
                    d = json.loads(z.read(n).decode("utf-8-sig"))
                except Exception:
                    continue
                if isinstance(d, dict):
                    keys[n.split("/")[1]] |= set(d)
        if sum(len(v) for v in keys.values()) > n_before:
            used.append(pathlib.Path(p).name)
    if used and not quiet:
        print(f"让位对象（CFPA）: {len(used)} 个包 → {len(keys)} 个命名空间")
        for u in used[:4]:
            print(f"    · {u}")
    return keys


# ---------------------------------------------------------------- 步骤 3
def is_cjk(s):
    return bool(CJK_RE.search(s))


def lookup(w):
    w = w.strip()
    if not w:
        return None
    low = w.lower().strip(".,:;!?()[]\"'")
    return WORD.get(low) if low else None


def is_name_like(en):
    """判断是「名字」还是「句子」。"""
    if any(c in en for c in ",.!?;:()[]\"/"):
        return False
    words = re.findall(r"[A-Za-z][A-Za-z\-']*", en)
    if not words or len(words) > 5:
        return False
    for w in words:
        lw = w.lower()
        if lw == "of":
            continue
        if lw in STOPWORDS or lookup(w) is None:
            return False
    return True


def translate(en):
    en = en.strip()
    if not en:
        return en, True
    if en in PHRASE:
        return PHRASE[en], True
    if not is_name_like(en):
        return en, False

    out, ok = [], True
    for tk in SPLIT_RE.split(en):
        if not tk:
            continue
        if FMT_RE.fullmatch(tk) or PLACEHOLDER_RE.fullmatch(tk) or tk.startswith("{"):
            out.append(tk)
            continue
        if not re.search(r"[A-Za-z]", tk):
            out.append(tk)
            continue
        words = re.findall(r"[A-Za-z][A-Za-z\-']*|[^A-Za-z\s]+", tk)
        lows = [x.lower() for x in words]
        zs = []
        if "of" in lows:
            j = lows.index("of")
            A, B = words[:j], words[j + 1:]
            if A and B:
                if any(lookup(x) is None for x in A + B):
                    ok = False
                zs = ["".join(lookup(x) or x for x in B) + "之"
                      + "".join(lookup(x) or x for x in A)]
        if not zs:
            for w in words:
                t = lookup(w)
                if t is None:
                    ok = False
                    zs.append(w)               # 不认识 → 留英文
                else:
                    zs.append(t)
        buf = ""
        for z in zs:
            if not z:
                continue                       # 虚词译为空（the/a/an）
            if not buf:
                buf = z
            elif buf.endswith(z) or z in buf[-len(z):]:
                continue                       # 连续重复（Lapis Lazuli）只留一次
            elif is_cjk(buf[-1]) or is_cjk(z[0]):
                buf += z
            else:
                buf += " " + z
        out.append(buf)
    return "".join(out).strip(), ok


# ---------------------------------------------------------------- 主流程
def main() -> int:
    ap = argparse.ArgumentParser(
        description="给整合包 mod 补中文（资源包方式，不改 jar）")
    ap.add_argument("mods", help="mods 目录")
    ap.add_argument("-o", "--out", default="模组汉化补充.zip", help="输出资源包路径")
    ap.add_argument("--vanilla-en", help="原版 en_us.json（给词典用，可选）")
    ap.add_argument("--vanilla-zh", help="原版 zh_cn.json（给词典用，可选）")
    ap.add_argument("--pack-format", type=int, default=15,
                    help="资源包格式号：1.20.x=15（默认）")
    ap.add_argument("--cfpa", nargs="*", default=[],
                    help="CFPA 汉化包 zip（让位对象，可多个）。不传则自动读 ~/.i18nupdatemod/")
    ap.add_argument("--no-cfpa", action="store_true",
                    help="不让位（只在没装 I18nUpdateMod 时才该这么用）")
    a = ap.parse_args()

    mods = pathlib.Path(a.mods)
    if not mods.is_dir():
        print("✗ mods 目录不存在：" + str(mods), file=sys.stderr)
        return 2

    data = extract_langs(mods)
    if not data:
        print("✗ 没抽到任何语言文件（检查是不是给了 mods 目录）", file=sys.stderr)
        return 1

    van_en = van_zh = None
    if a.vanilla_en and a.vanilla_zh:
        try:
            van_en = json.loads(pathlib.Path(a.vanilla_en).read_text(encoding="utf-8-sig"))
            van_zh = json.loads(pathlib.Path(a.vanilla_zh).read_text(encoding="utf-8-sig"))
        except Exception as e:
            print("⚠ 原版 lang 读取失败，跳过：" + str(e))

    PHRASE.update(build_phrase_dict(data, van_en, van_zh))
    derived = build_word_dict(PHRASE)
    WORD.update(derived)                        # 先放推导结果
    WORD.update(EXTRA_WORDS)                    # 人工词条覆盖之（推导有脏数据）
    print(f"短语对照表 {len(PHRASE)}   推导单词 {len(derived)}   人工词条 {len(EXTRA_WORDS)}")

    cfpa = {} if a.no_cfpa else load_cfpa_keys(a.cfpa)
    if not a.no_cfpa and not cfpa:
        print("⚠ 没找到 CFPA 汉化包 —— 请先启动一次装了 I18nUpdateMod 的游戏，"
              "或用 --cfpa 指定；本次不做让位（可能覆盖 CFPA 译文）")

    per_ns, st = {}, {"done": 0, "kept": 0, "yield": 0}
    for ns in sorted(data):
        en, zh = data[ns]["en_us"], data[ns]["zh_cn"]
        out = {}
        for k, v in en.items():
            if k in zh or not isinstance(v, str):
                continue                       # mod 自己已有中文 → 不碰
            t, _ = translate(v)
            if t == v:
                st["kept"] += 1
                continue
            if k in cfpa.get(ns, ()):          # 🔴 CFPA 已有 → 让位，别盖掉人工译文
                st["yield"] += 1
                continue
            out[k] = t
            st["done"] += 1
        if out:
            per_ns[ns] = out

    all_en = sum(len(data[ns]["en_us"]) for ns in data)
    have_zh = sum(min(len(data[ns]["en_us"]), len(data[ns]["zh_cn"])) for ns in data)
    gap = max(1, all_en - have_zh)
    # 覆盖率要按**并集**算 —— 自带 与 CFPA 的键大量重叠，直接相加会超过 100%
    merged = sum(1 for ns in data for k in data[ns]["en_us"]
                 if k in data[ns]["zh_cn"] or k in cfpa.get(ns, ()))
    cover = merged + st["done"]
    print(f"条目 {all_en}   mod 自带中文 {have_zh} ({have_zh/all_en*100:.1f}%)   缺 {gap}")
    print(f"本包补上 {st['done']}   让位 CFPA {st['yield']}   仍留英文 {st['kept']}")
    print(f"预计总覆盖率 {cover/all_en*100:.1f}%"
          f"（自带∪CFPA {merged}  +  本包 {st['done']}）   命名空间 {len(per_ns)}")
    if not per_ns:
        print("✗ 没有可产出内容", file=sys.stderr)
        return 1

    outp = pathlib.Path(a.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    meta = {"pack": {"pack_format": a.pack_format,
                     "description": f"模组汉化补充：补齐 {st['done']} 条缺失中文"}}
    with zipfile.ZipFile(outp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr("pack.mcmeta", json.dumps(meta, ensure_ascii=False, indent=2))
        for ns in sorted(per_ns):
            z.writestr(f"assets/{ns}/lang/zh_cn.json",
                       json.dumps(per_ns[ns], ensure_ascii=False, indent=1))
    print(f"\n→ {outp}  ({outp.stat().st_size/1024/1024:.2f} MB)")
    print("   放进 .minecraft/resourcepacks/ 后在游戏「选项 → 资源包」里启用")
    return 0


if __name__ == "__main__":
    sys.exit(main())

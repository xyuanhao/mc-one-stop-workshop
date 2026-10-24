# 扫描:直接继承 BlockItem 的类,其 <init> 在调用 super BlockItem.<init> 之前推入过 aconst_null(0x01)
# 用途:找出 "BlockItem 带 null block" 的非法注册(会炸 supplementaries FireBehaviors/amendments 等遍历方)
# 用法: python scan_null_blockitem.py <mods目录> [--all]   (默认只扫直接子类; --all 同时扫整类)
import argparse
import zipfile, glob, struct, os, sys

BLOCKITEM = "net/minecraft/world/item/BlockItem"

def parse_cp(data):
    off = 8
    count = struct.unpack(">H", data[off:off+2])[0]; off += 2
    cp = [None]*(count+1)
    i = 1
    while i < count:
        tag = data[off]; off += 1
        if tag == 1:
            l = struct.unpack(">H", data[off:off+2])[0]; off += 2
            s = data[off:off+l].decode("utf-8","replace"); off += l
            cp[i] = ("utf8", s)
        elif tag in (3,4): cp[i]=(tag,); off += 4
        elif tag in (5,6): cp[i]=(tag,); off += 8
        elif tag in (7,8,16,19,20): cp[i] = (tag, struct.unpack(">H", data[off:off+2])[0]); off += 2
        elif tag in (9,10,11,12,17,18): cp[i] = (tag, struct.unpack(">H", data[off:off+2])[0], struct.unpack(">H", data[off+2:off+4])[0]); off += 4
        elif tag == 15:
            kind = data[off]; off += 1
            cp[i] = (15, kind, struct.unpack(">H", data[off:off+2])[0]); off += 2
        else:
            raise Exception("unknown cp tag %d" % tag)
        if tag in (5,6): i += 2
        else: i += 1
    return cp, off

def utf(cp, idx):
    e = cp[idx]
    return e[1] if e and e[0]=="utf8" else None

def class_name(cp, idx):
    e = cp[idx]
    return utf(cp, e[1]) if e and e[0]==7 else None

def parse_class(data):
    """returns (this_name, super_name, methods=[(mname, desc, codebytes_or_None)])"""
    if data[:4] != b"\xca\xfe\xba\xbe": return None
    cp, off = parse_cp(data)
    if off+6 > len(data): return None
    access, thisc, superc = struct.unpack(">HHH", data[off:off+6]); off += 6
    this_name = class_name(cp, thisc)
    super_name = class_name(cp, superc)
    n_if = struct.unpack(">H", data[off:off+2])[0]; off += 2
    off += n_if*2
    n_field = struct.unpack(">H", data[off:off+2])[0]; off += 2
    for _ in range(n_field):
        acc, fd, fdesc = struct.unpack(">HHH", data[off:off+6]); off += 6
        na = struct.unpack(">H", data[off:off+2])[0]; off += 2
        for _ in range(na):
            off += 2
            alen = struct.unpack(">I", data[off:off+4])[0]; off += 4
            off += alen
    n_meth = struct.unpack(">H", data[off:off+2])[0]; off += 2
    methods = []
    for _ in range(n_meth):
        acc, mn, md = struct.unpack(">HHH", data[off:off+6]); off += 6
        mname, mdesc = utf(cp, mn), utf(cp, md)
        na = struct.unpack(">H", data[off:off+2])[0]; off += 2
        code = None
        for _ in range(na):
            nidx = struct.unpack(">H", data[off:off+2])[0]; off += 2
            alen = struct.unpack(">I", data[off:off+4])[0]; off += 4
            if utf(cp, nidx) == "Code":
                code = data[off:off+alen]
            off += alen
        methods.append((mname, mdesc, code))
    return (this_name, super_name, methods, cp)

def methodref_class_name(cp, mref):
    e = cp[mref]
    if e and e[0] in (10, 11):
        nat = cp[e[2]]
        nname = utf(cp, nat[1]) if nat and nat[0] == 12 else None
        return class_name(cp, e[1]), nname
    return None, None

# getBlock() 覆写判定：方法名有可能是官方名（开发期/mojmap）或 SRG 名（1.20.x Forge 运行期）
GETBLOCK_NAMES = {"getBlock", "m_40614_"}
GETBLOCK_DESC = "()Lnet/minecraft/world/level/block/Block;"

# 修正的指令长度表
def op_len(op):
    if op in (0xaa, 0xab): return -1  # tableswitch/lookupswitch
    if op in (0x10,0x12,0x15,0x16,0x17,0x18,0x19,0x36,0x37,0x38,0x39,0x3a,0xa9): return 2
    if op in (0x11,0x13,0x14,0x84,0x99,0x9a,0x9b,0x9c,0x9d,0x9e,0x9f,0xa0,0xa1,0xa2,0xa3,0xa4,0xa5,0xa6,0xa7,0xa8,
              0xb2,0xb3,0xb4,0xb5,0xb6,0xb7,0xb8,0xbb,0xbd,0xc0,0xc1): return 3
    if op == 0xc5: return 4
    if op in (0xb9,0xba,0xc8,0xc9): return 5
    if op == 0xc4: return -2  # wide: 不确定,停止
    return 1

def init_pushes_null_before_super(cp, codebytes, mref_target=None):
    """Walk code; track aconst_null seen; return True if invokespecial BlockItem.<init> occurs after aconst_null."""
    if codebytes is None: return False
    codelen = struct.unpack(">I", codebytes[4:8])[0]
    bc = codebytes[8:8+codelen]
    i = 0
    seen_null = False
    while i < len(bc):
        op = bc[i]
        L = op_len(op)
        if L == -1:  # switch: operand size variable; super call won't follow a switch in <init> prefix
            break
        if L == -2:
            break
        if op == 0x01:
            seen_null = True
        if op == 0xb7 and i+2 < len(bc):
            mref = struct.unpack(">H", bc[i+1:i+3])[0]
            cn, n2 = methodref_class_name(cp, mref)
            if cn == BLOCKITEM and n2 == "<init>":
                return seen_null
        i += L
    return False

def scan_jar(jar, all_classes):
    out = []
    try:
        z = zipfile.ZipFile(jar)
    except Exception as e:
        print("SKIP %s (%s)" % (jar, e)); return out
    for n in z.namelist():
        if not n.endswith(".class"): continue
        try:
            this_name, super_name, methods, cp = parse_class(z.read(n))
        except Exception:
            continue
        if this_name is None: continue
        # 只扫直接继承 BlockItem 的类
        if not all_classes and super_name != BLOCKITEM:
            continue
        blockitem_methods = {(mn, md) for (mn, md, _) in methods}
        for (mname, mdesc, code) in methods:
            if mname != "<init>": continue
            try:
                ok = init_pushes_null_before_super(cp, code)
            except Exception:
                ok = False
            if ok:
                # 是否覆写了 getBlock()：覆写了就返回真实方块（如 supplier.get()），
                # 运行期 getBlock() 不会返回 null → 良性命中（citadel 即此型）
                benign = any(mn in GETBLOCK_NAMES and md == GETBLOCK_DESC
                             for (mn, md) in blockitem_methods)
                out.append((n, mdesc, super_name, benign))
    return out

def main():
    ap = argparse.ArgumentParser(
        description="null-block BlockItem 扫描：反汇编找 super(null) 构造的 BlockItem"
                    "（block==null 仍被注册 → 运行期被 supplementaries/moonlight 遍历时 NPE）",
        epilog="退出码：0=干净 / 1=有命中（可串进「体检→修→打包」流水线当闸门）")
    ap.add_argument("mods", help="mods 目录（实例/mods）")
    ap.add_argument("--all", action="store_true",
                    help="放开继承限制（默认只查直接继承 BlockItem 的类，一般够用）")
    args = ap.parse_args()
    root = args.mods
    allc = args.all
    hits, benign = [], []
    for jar in sorted(glob.glob(os.path.join(root, "*.jar"))):
        for (cls, mdesc, supern, is_benign) in scan_jar(jar, allc):
            rec = (os.path.basename(jar), cls, mdesc, supern)
            (benign if is_benign else hits).append(rec)
    print("=== 疑似 null-block 的 BlockItem 构造 (super(null) 模式) ===")
    for jar, cls, mdesc, supern in hits:
        print("HIT    %s | %s | %s | super=%s" % (jar, cls, mdesc, supern))
    for jar, cls, mdesc, supern in benign:
        print("BENIGN %s | %s | %s | super=%s  (已覆写 getBlock() → 运行期返回真实方块)"
              % (jar, cls, mdesc, supern))
    if not hits and not benign:
        print("(无命中)")
    print("疑似真凶: %d    良性(覆写 getBlock): %d" % (len(hits), len(benign)))
    if benign and not hits:
        print("→ 全部为良性，无需处理。")
    # 只有"疑似真凶"才拦流水线；良性命中不拦
    sys.exit(1 if hits else 0)

if __name__ == "__main__":
    main()

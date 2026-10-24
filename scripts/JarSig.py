"""JarSig.py —— 直接解析 .class 常量池/方法表，dump 出任意 jar 内类的方法签名。

用途：在没有对应 JDK（例如 Java 25 class 文件用 Java 21 javap 读不了）时，
精确核对某个 MC / 模组版本的真实 API 签名，避免凭记忆写错方法名或参数。

用法:
    python JarSig.py <jar路径> <类名或前缀> [更多...]
    python JarSig.py <jar> net.minecraft.client.renderer.entity.MobRenderer --brief
"""
import sys
import zipfile
import struct


class Reader:
    def __init__(self, data):
        self.d = data
        self.i = 0

    def u1(self):
        v = self.d[self.i]
        self.i += 1
        return v

    def u2(self):
        v = struct.unpack_from('>H', self.d, self.i)[0]
        self.i += 2
        return v

    def u4(self):
        v = struct.unpack_from('>I', self.d, self.i)[0]
        self.i += 4
        return v

    def skip(self, n):
        self.i += n

    def bytes(self, n):
        v = self.d[self.i:self.i + n]
        self.i += n
        return v


def parse_class(data):
    """返回 dict: name, super, interfaces, fields, methods（名字+描述符）"""
    r = Reader(data)
    if r.u4() != 0xCAFEBABE:
        raise ValueError('not a class file')
    r.u2()  # minor
    r.u2()  # major
    cp_count = r.u2()
    cp = [None] * cp_count
    i = 1
    while i < cp_count:
        tag = r.u1()
        if tag == 1:                      # Utf8
            ln = r.u2()
            cp[i] = ('utf8', r.bytes(ln).decode('utf-8', 'replace'))
        elif tag in (3, 4):               # Integer / Float
            cp[i] = ('num', r.u4())
        elif tag in (5, 6):               # Long / Double（占两个槽）
            cp[i] = ('num8', r.u4() * 2**32 + r.u4())
            i += 1
        elif tag in (7, 8, 16, 19, 20):   # Class / String / MethodType / Module / Package
            cp[i] = ('ref', r.u2())
        elif tag in (9, 10, 11, 12, 17, 18):  # ref 类（两个 u2）
            cp[i] = ('ref2', r.u2(), r.u2())
        elif tag == 15:                   # MethodHandle
            cp[i] = ('mh', r.u1(), r.u2())
        else:
            raise ValueError('unknown cp tag %d at %d' % (tag, i))
        i += 1

    def utf(idx):
        if idx is None or idx == 0 or idx >= len(cp):
            return '?'
        e = cp[idx]
        return e[1] if e and e[0] == 'utf8' else '?'

    def cls(idx):
        e = cp[idx] if idx and idx < len(cp) else None
        return utf(e[1]) if e and e[0] == 'ref' else '?'

    r.u2()                                # access_flags
    this_class = r.u2()
    super_class = r.u2()
    iface_count = r.u2()
    ifaces = [cls(r.u2()) for _ in range(iface_count)]

    def read_code_params(attr_len, arg_count):
        """读 Code 属性里的 LocalVariableTable，取 start_pc==0 的槽位当参数名。"""
        end = r.i + attr_len
        names = {}
        try:
            r.u2()                        # max_stack
            r.u2()                        # max_locals
            code_len = r.u4()
            r.skip(code_len)
            etl = r.u2()
            r.skip(etl * 8)
            ac = r.u2()
            for _ in range(ac):
                aname = utf(r.u2())
                alen = r.u4()
                sub_end = r.i + alen
                if aname == 'LocalVariableTable':
                    cnt = r.u2()
                    for _ in range(cnt):
                        spc = r.u2()
                        r.u2()                # length
                        nidx = r.u2()
                        r.u2()                # descriptor
                        idx = r.u2()
                        if spc == 0 and idx <= arg_count:
                            names[idx] = utf(nidx)
                r.i = sub_end
        except Exception:
            pass
        r.i = end
        return names

    def slot_count(desc):
        """粗略算描述符占用的槽位数（long/double 占 2）。"""
        body = desc[1:desc.index(')')]
        n = 0
        i = 0
        while i < len(body):
            c = body[i]
            if c == 'L':
                i = body.index(';', i)
                n += 1
            elif c == '[':
                while body[i] == '[':
                    i += 1
                if body[i] == 'L':
                    i = body.index(';', i)
                n += 1
            elif c in 'JD':
                n += 2
            else:
                n += 1
            i += 1
        return n

    def members():
        out = []
        n = r.u2()
        for _ in range(n):
            acc = r.u2()
            name = utf(r.u2())
            desc = utf(r.u2())
            attrs = r.u2()
            params = {}
            for _ in range(attrs):
                aname = utf(r.u2())
                ln = r.u4()
                if aname == 'Code':
                    argn = slot_count(desc) + (0 if (acc & 0x0008) else 1)
                    params = read_code_params(ln, argn)
                else:
                    r.skip(ln)
            out.append((acc, name, desc, params))
        return out

    fields = members()
    methods = members()
    return {
        'name': cls(this_class).replace('/', '.'),
        'super': cls(super_class).replace('/', '.') if super_class else None,
        'interfaces': [x.replace('/', '.') for x in ifaces],
        'fields': fields,
        'methods': methods,
    }


ACC = [(0x0001, 'public'), (0x0002, 'private'), (0x0004, 'protected'),
       (0x0008, 'static'), (0x0010, 'final'), (0x0400, 'abstract')]


def acc_str(a):
    return ' '.join(n for bit, n in ACC if a & bit)


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    jar = sys.argv[1]
    args = [a for a in sys.argv[2:] if not a.startswith('--')]
    brief = '--brief' in sys.argv
    fuzzy = '--fuzzy' in sys.argv
    show_params = '--noparams' not in sys.argv
    z = zipfile.ZipFile(jar)
    names = z.namelist()
    for query in args:
        q = query.replace('.', '/')
        # 精确匹配：类名完全相等，或查询的是它的内部类（Foo$Bar）。
        # ⚠️ 绝不能写成 n.startswith(q) —— 那样查 net/minecraft/world/item/Item
        #    会把 ItemStack / Items / ItemAttributeModifiers 全部混进来，
        #    于是你看到的 "Item 的方法" 其实是 ItemStack 的，签名对不上还找不到原因。
        hits = [n for n in names if n.endswith('.class') and
                (n[:-6] == q or n[:-6].startswith(q + '$'))]
        if fuzzy:
            hits += [n for n in names if n.endswith('.class') and q in n and n not in hits]
            seen, uniq = set(), []
            for h in hits:
                if h not in seen:
                    seen.add(h)
                    uniq.append(h)
            hits = uniq
        if not hits:
            print('!! not found:', query, '（想模糊匹配加 --fuzzy）')
            continue
        for h in hits[:8]:
            info = parse_class(z.read(h))
            print('=' * 78)
            print('CLASS %s' % info['name'])
            if info['super']:
                print('  extends %s' % info['super'])
            if info['interfaces']:
                print('  implements %s' % ', '.join(info['interfaces']))
            if not brief:
                pub_fields = [f for f in info['fields'] if f[0] & 0x0001]
                if pub_fields:
                    print('  -- public fields --')
                    for a, n, d, _p in pub_fields:
                        print('     %s %s %s' % (acc_str(a), d, n))
            print('  -- methods --')
            for a, n, d, p in info['methods']:
                if brief and not (a & 0x0001):
                    continue
                pstr = ''
                if p and show_params:
                    pstr = '   [%s]' % ', '.join('%s=%s' % (k, v) for k, v in sorted(p.items()))
                print('     %s %s%s%s' % (acc_str(a), n, d, pstr))
    return 0


if __name__ == '__main__':
    sys.exit(main())

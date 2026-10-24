# -*- coding: utf-8 -*-
"""极简 RCON 客户端（Source RCON 协议）—— 用来驱动专用服务端做自动化验证。

既可当命令行用：
    python rcon.py "list" "execute in otherworld:otherworld run locate biome otherworld:ashen_waste"
    python rcon.py --port 25575 --password devtest "save-all flush"
也可当模块用：
    import rcon
    s = rcon.connect("127.0.0.1", 25575, "devtest")
    print(rcon.send(s, "time query daytime"))

服务端需要在 server.properties 里开：
    enable-rcon=true
    rcon.port=25575
    rcon.password=devtest
"""
import socket
import struct
import sys


def pkt(rid, ptype, payload):
    body = struct.pack("<ii", rid, ptype) + payload.encode("utf-8") + b"\x00\x00"
    return struct.pack("<i", len(body)) + body


def recv_pkt(s):
    """
    收一个包，返回 `(request_id, type, payload_bytes)`。

    ⚠️ **payload 返回 bytes、不在这里解码** —— 分包的边界是按**字节**切的（见 send），
    字节长度是判定"还有没有下一包"的唯一可靠依据。先解码再取长度会踩坑：
    中文一个字占 3 字节但只算 1 个字符，一个满包（4096 字节）解出来只有 4000 出头个字符，
    按字符数判就会把满包误判成末包、把响应腰斩（实测踩过）。
    """
    raw = b""
    while len(raw) < 4:
        chunk = s.recv(4 - len(raw))
        if not chunk:
            return None, None, b""
        raw += chunk
    ln = struct.unpack("<i", raw)[0]
    data = b""
    while len(data) < ln:
        data += s.recv(ln - len(data))
    rid, ptype = struct.unpack("<ii", data[:8])
    return rid, ptype, data[8:-2]


def connect(host="127.0.0.1", port=25575, password="devtest", timeout=60):
    """连上并登录。

    timeout 默认放到 60 秒 —— 服务端在强载区块时会 `Can't keep up! Running 20s behind`，
    10 秒会导致 `TimeoutError: timed out` 或 `ConnectionResetError: WinError 10054`，
    看起来像「服务端挂了」其实只是它很忙。真挂了的话 60 秒内也会明确报错。
    """
    s = socket.create_connection((host, port), timeout=timeout)
    s.sendall(pkt(1, 3, password))
    rid, _, _ = recv_pkt(s)
    if rid == -1:
        raise RuntimeError("RCON 登录失败：密码错？")
    return s


def send(s, cmd):
    """
    发一条命令，返回**完整**响应文本。

    ⚠️ **必须循环收包**。一次响应超过 **4096 字节**就会被拆成多个包依次发。
    只收一个包的话长输出会被静默截断，断点通常落在 JSON / 列表中间，
    看着就像"服务端只答了一半"，极易误判成 mod 的 bug
    （实测踩过：dump 规则表的 JSON 被切在第 182 行）。

    终止条件是「这一包**不足 4096 字节**」：
    - 短输出 / 空输出 → 单包，直接结束。**不能**写成"一直读到空包为止" ——
      原版对不足一满包的响应**不会**再补终止包，那样写会挂到超时；
    - 长输出 → 满包继续读，最后一包是余数，必然不足 4096。

    🔴 判定必须用**字节**长度而不是解码后的字符数：实测服务端就是按字节切的
    （含中文的那一包正好 4096 字节 = 4036 字符）。用字符数判会把满包当末包。

    ⚠️ **调用方注意（实测踩过）**：**零结果命令可能压根不产生响应包** ——
    典型是 `/kill @e[...]` 没匹配到任何实体。在一条连接上连发多条这种命令，
    后续响应会整体**错位一格**，把 A 命令的回显安到 B 命令头上
    （本次就差点得出"铁矿也属于 c:tools"的假结论）。
    **判据类批量验证请一条命令开一条连接**（连上→发→读→关），
    或让每条命令都自带回显（`execute if entity … run …`）。
    """
    s.sendall(pkt(2, 2, cmd))
    parts = []
    while True:
        _, _, raw = recv_pkt(s)
        parts.append(raw)
        if len(raw) < 4096:
            break
    return b"".join(parts).decode("utf-8", errors="replace")


def main():
    args = sys.argv[1:]
    host, port, pw = "127.0.0.1", 25575, "devtest"
    opts = {"--host": "host", "--port": "port", "--password": "pw"}
    while args and args[0].startswith("--"):
        token = args.pop(0)
        key, eq, val = token.partition("=")
        if key not in opts:
            print(f"未知参数 {key}，可用：{' / '.join(opts)}")
            return
        if not eq:
            # "--key value" 形式（--help 里的示例就是这种），取值吃掉下一个参数
            if not args or args[0].startswith("--"):
                print(f"参数 {key} 缺少取值（写 {key}=值 或 {key} 值）")
                return
            val = args.pop(0)
        if not val:
            print(f"参数 {key} 取值为空")
            return
        if opts[key] == "port":
            try:
                port = int(val)
            except ValueError:
                print(f"--port 需要整数，收到 {val!r}")
                return
        elif opts[key] == "host":
            host = val
        else:
            pw = val
    if not args:
        print(__doc__)
        return
    try:
        s = connect(host, port, pw)
    except OSError as e:
        print(f"连不上 RCON {host}:{port} —— {e}\n"
              f"检查：服务端起来没？server.properties 里 enable-rcon=true / rcon.port / rcon.password？")
        return
    print("RCON 已连接")
    for cmd in args:
        print(f">>> {cmd}\n{send(s, cmd)}")
    s.close()


if __name__ == "__main__":
    main()

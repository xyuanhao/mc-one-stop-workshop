# -*- coding: utf-8 -*-
"""游戏内测试驱动器 —— 让 AI（或人）在**真实客户端**里跑场景测试。

它做的事：
  1. 构建「游戏内测试代理」jar（进程内代理，跑在客户端里）；
  2. 用 Gradle init script 把代理注入被测试工程的 runClient 运行时（**不改工程文件**）；
  3. 起客户端 → 等它到主菜单 → 建/开世界 → 按场景脚本发命令；
  4. 收产物：截图、状态 JSON、命令回显、日志异常、退出码。

为什么是「模组 + 外部驱动」而不是纯外部程序：见 references/26.x-游戏内自动化测试.md。
一句话 —— **只有进程内的模组能给出结构化状态和确定性操作**；外部程序连"点个按钮"
都要抢前台焦点、按坐标猜，而且看不到 HUD/界面画了什么。

用法
----
  # 0) 只体检，不跑（推荐第一次先看这个）
  python 游戏内测试.py doctor --project D:/WorkBuddy/01-MC模组开发/SomeMod

  # 0.5) 🔴 进游戏实测（交付门禁：证明这个 mod 真的能进游戏）
  python 游戏内测试.py smoke --project D:/WorkBuddy/01-MC模组开发/SomeMod
  #   退出码 0 = 起得来、进得去世界、渲染没黑屏、日志没异常 → 可以谈交付
  #   退出码 1 = 有判据没过（报告里逐条写清哪条挂了）
  #   退出码 2 = 参数/环境问题（JDK 不对、工程路径错、代理编不出来）

  # 1) 构建/更新代理 jar
  python 游戏内测试.py agent

  # 2) 跑一份场景（场景是 Python 脚本，能读状态、能断言）
  python 游戏内测试.py run --project <工程目录> --scenario my_scenario.py

  # 3) 不进游戏也能用的窗口取证（不抢焦点、被别的窗口盖住也能抓）
  python 游戏内测试.py shot --out win.png
  python 游戏内测试.py close            # 优雅关掉 Minecraft 窗口（等于点右上角 X）

场景脚本里可用的对象是 `agent`（中文别名 `会话`）：
  agent.进世界(mode="create", name="世界", preset="flat", seed="1")
  agent.命令("give @s minecraft:stone 64")
  agent.等(20)                      # 等 20 tick
  agent.等界面("net.minecraft.client.gui.screens.PauseScreen")
  agent.按键("key.jump", "tap")
  agent.开界面("com.example.MyScreen")
  agent.点("gui.done")              # 按翻译键点按钮（不依赖坐标/分辨率）
  agent.状态(radius=8, blocks=True)
  agent.方块("0,100,0")
  agent.截图("第一帧")
  agent.回显()                      # 命令回显/聊天
  agent.日志(pattern="ERROR")
  agent.退出()
退出码：0 = 场景跑完且断言全过；1 = 有失败/中断；2 = 参数或环境问题。
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

技能目录 = Path(__file__).resolve().parent.parent
代理模板 = 技能目录 / "templates" / "测试代理-26.1"
注入脚本 = 技能目录 / "scripts" / "游戏内注入.gradle"
代理工作根 = Path.home() / ".workbuddy" / "mc-测试代理"

# 支持的「时代」：目前只打通 26.1（NeoForge）。其它版本走文档里的替代路线。
时代表 = {
    "26.1": {"模板": "测试代理-26.1", "mc": "26.1", "java": 25},
}

C_RESET, C_RED, C_GREEN, C_YELLOW, C_GREY = "\033[0m", "\033[31m", "\033[32m", "\033[33m", "\033[90m"


def 打印(msg="", 色=None):
    if 色 and sys.stdout.isatty():
        print(f"{色}{msg}{C_RESET}", flush=True)
    else:
        print(msg, flush=True)


# ══════════════════════════════════════════════════════════════════
# Win32 窗口：抓图 / 优雅关闭（都不需要前台焦点）
# ══════════════════════════════════════════════════════════════════
user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ⚠️ HANDLE 在 64 位下是 8 字节，不声明 restype 会被 ctypes 默认截成 c_int(32 位)。
#    截断的句柄拿去 QueryFullProcessImageNameW 会静默失败（`false`）——表现是"拿不到进程名"，
#    于是下面整条「进程必须是 Java」的过滤悄悄失效。这类 Win32 封装最经典的坑。
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.QueryFullProcessImageNameW.restype = wt.BOOL
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, ctypes.c_wchar_p,
                                                ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD


def 枚举窗口(只可见=True):
    out = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        if 只可见 and not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        rect = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        out.append({"hwnd": hwnd, "title": buf.value,
                    "rect": (rect.left, rect.top, rect.right, rect.bottom),
                    "size": (rect.right - rect.left, rect.bottom - rect.top)})
        return True

    user32.EnumWindows(cb, 0)
    return out


def 窗口进程(hwnd) -> tuple[int, str]:
    """窗口所属进程的 (pid, exe 文件名小写)。拿不到就返回 (0, "") / (pid, "")。

    拿不到**不报错**——这句的调用方把"拿不到"当"跳过该层过滤"，不当"判死"。
    """
    pid = wt.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return 0, ""
    h = kernel32.OpenProcess(0x1000, False, pid.value)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return pid.value, ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = wt.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)) and n.value:
            return pid.value, buf.value.rsplit("\\", 1)[-1].lower()
    finally:
        kernel32.CloseHandle(h)
    return pid.value, ""


# 「启动客户端之前」的窗口快照。设过之后，找游戏窗口() 只认基线之外**新出现**的窗口。
_窗口基线: set[int] = set()
_基线已设 = False
_提示过候选: tuple = ()


def 快照窗口基线():
    """在拉起 runClient **之前**调：把当前桌面上的窗口全部记下来。

    之后 找游戏窗口() 只认基线之外新冒出来的窗口 —— 这能挡掉
    「启动前就已经开着的、标题同样含 minecraft 的别的程序」（另一个 MC 实例、启动器、
    甚至是别的游戏）。它同时保护了收尾逻辑：绝不朝一个不属于本次运行的窗口发 WM_CLOSE。
    """
    global _窗口基线, _基线已设
    _窗口基线 = {w["hwnd"] for w in 枚举窗口(只可见=False)}
    _基线已设 = True
    return len(_窗口基线)


def 找游戏窗口():
    """找**本次运行**的 Minecraft 客户端窗口。

    三层过滤，任何一层拿不到信息就跳过该层（宁可放过，不判死）：
      1. 标题含 "minecraft"；
      2. 窗口进程是 java.exe / javaw.exe —— 挡掉 "Minecraft Dungeons II" 这类标题撞车的
         别的游戏（它不是 Java 进程）；
      3. 已调过 快照窗口基线() 时，只认基线之外新出现的窗口 —— 挡掉启动前就开着的窗口。

    ⚠️ 2026-10-14 实测假阳性：只做第 1 层时命中了桌面上的 "Minecraft Dungeons II"，
    smoke 判据把它当成「✔ Minecraft 窗口出现」，收尾时还差点给它发 WM_CLOSE
    （= 把用户别的游戏关掉）。第 2、3 层都是为此加的 —— **别退回只匹配标题**。
    """
    global _提示过候选
    hits = []
    for w in 枚举窗口():
        if "minecraft" not in w["title"].lower():
            continue
        pid, exe = 窗口进程(w["hwnd"])
        if exe and exe not in ("java.exe", "javaw.exe"):
            continue
        if _基线已设 and w["hwnd"] in _窗口基线:
            continue
        w["pid"], w["exe"] = pid, exe
        hits.append(w)
    签名 = tuple((w["hwnd"], w["title"]) for w in hits)
    if len(hits) > 1 and 签名 != _提示过候选:
        _提示过候选 = 签名
        打印(f"  ⚠ 有 {len(hits)} 个窗口同时匹配「minecraft」，用第一个：", C_YELLOW)
        for w in hits:
            打印(f"    - {w['title']!r}｜pid {w['pid']}｜{w['exe'] or '?'}｜{w['size']}", C_GREY)
    return hits[0] if hits else None


def 抓窗口(hwnd, out_path: Path):
    """PrintWindow(PW_RENDERFULLCONTENT) 抓单个窗口：不需要前台焦点，被遮挡也能抓。"""
    from PIL import Image
    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    hdc = user32.GetWindowDC(hwnd)
    memdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(memdc, bmp)
    ok = user32.PrintWindow(hwnd, memdc, 2)

    class BMIH(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                    ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                    ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]

    bi = BMIH()
    bi.biSize = ctypes.sizeof(BMIH)
    bi.biWidth, bi.biHeight = w, -h
    bi.biPlanes, bi.biBitCount = 1, 32
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(memdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(memdc)
    user32.ReleaseDC(hwnd, hdc)

    img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    return ok, img.size


def 关窗口(hwnd) -> bool:
    """给窗口发 WM_CLOSE —— 和点右上角 X 同一条路，客户端会走正常关闭流程（存档、写日志）。"""
    return bool(user32.PostMessageW(hwnd, 0x0010, 0, 0))


# ══════════════════════════════════════════════════════════════════
# 冒烟判据用的两个取证原语（黑屏 / 日志异常）
# ══════════════════════════════════════════════════════════════════
def 判非黑屏(path: Path) -> tuple[bool, str]:
    """「抓到图」不等于「画出来了」——全黑 / 单色窗口是"起了但没渲染"最典型的样子。

    判据：缩略后算亮度均值与极差。极差 < 8 且均值 < 24 判黑屏
    （纯黑窗口是「进程活着、GL 没出图」，日志里往往一点错都没有）。
    """
    try:
        from PIL import Image
        im = Image.open(path).convert("RGB")
    except Exception as e:
        return False, f"读图失败：{type(e).__name__}: {e}"
    im.thumbnail((320, 320))
    # ⚠️ 别用 im.getdata()：Pillow 12 起它被弃用（每次跑都会打 DeprecationWarning）。
    #    而官方给的替代 get_flattened_data() 返回的是**展平成一维**的数据
    #    （[r0,g0,b0,r1,g1,b1,...]），跟 getdata() 的 [(r,g,b), ...] 不是同一种形状，
    #    不能直接替换 —— 换了会在下面 `for r, g, b in 像素` 上炸掉。
    #    tobytes() 才是跨版本稳定、又不打警告的取法：RGB 模式每 3 字节一个像素。
    数据 = im.tobytes()
    if not 数据:
        return False, "图是空的（0 像素）"
    亮度 = [0.299 * 数据[i] + 0.587 * 数据[i + 1] + 0.114 * 数据[i + 2]
            for i in range(0, len(数据), 3)]
    均 = sum(亮度) / len(亮度)
    极差 = max(亮度) - min(亮度)
    if 极差 < 8 and 均 < 24:
        return False, f"疑似黑屏/未渲染（均值 {均:.1f}，极差 {极差:.1f}）"
    return True, f"{im.size[0]}x{im.size[1]}，均值 {均:.1f}，极差 {极差:.1f}"


def 扫日志异常(日志文件: Path, 尾部行数=6000, 上限=60) -> list[str]:
    """从客户端日志里捞 ERROR / Exception —— 只看尾部（latest.log 每次运行会重写）。"""
    f = Path(日志文件)
    if not f.exists():
        return []
    lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
    hits = []
    for l in lines[-尾部行数:]:
        if re.search(r"\[.*/ERROR\]|Exception in thread|java\.lang\.\w+Exception|Caused by:", l):
            hits.append(l.strip()[:300])
    return hits[:上限]


def 探测JDK(需要版本: int):
    """优先用官方启动器自带的 JDK（本机已有、不需要联网下载）。"""
    cands = []
    runtime = Path(os.environ.get("APPDATA", "")) / ".minecraft" / "runtime"
    if runtime.is_dir():
        cands += sorted([p for p in runtime.iterdir() if p.is_dir() and (p / "bin" / "javac.exe").exists()])
    for env in ("JAVA_HOME",):
        v = os.environ.get(env)
        if v and (Path(v) / "bin" / "javac.exe").exists():
            cands.append(Path(v))
    for c in cands:
        try:
            r = subprocess.run([str(c / "bin" / "javac.exe"), "-version"],
                               capture_output=True, text=True, timeout=20)
            m = re.search(r"(\d+)", (r.stdout + r.stderr))
            if m and int(m.group(1)) >= 需要版本:
                return c, int(m.group(1))
        except Exception:
            continue
    return None, 0


def 读工程属性(project: Path) -> dict:
    props = {}
    f = project / "gradle.properties"
    if not f.exists():
        return props
    for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        # Gradle 按 ISO-8859-1 读 properties —— 中文得先按转义还原
        v = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), v)
        props[k.strip()] = v.strip()
    return props


def 判时代(props: dict):
    mc = props.get("minecraft_version", "")
    for key, info in 时代表.items():
        if mc.startswith(key):
            return key, info
    return None, None


def 探测运行目录(project: Path) -> Path:
    """runClient 的工作目录：默认 <工程>/run；build.gradle 里写死 gameDirectory 的另行处理。"""
    run = project / "run"
    return run


# ══════════════════════════════════════════════════════════════════
# 代理构建
# ══════════════════════════════════════════════════════════════════
def 代理目录(时代: str) -> Path:
    return 代理工作根 / 时代


def 构建代理(时代: str, neo_version: str | None = None, 强制=False, 静默=False):
    info = 时代表[时代]
    src = 技能目录 / "templates" / info["模板"]
    work = 代理目录(时代)
    if 强制 and work.exists():
        shutil.rmtree(work)
    if not work.exists():
        shutil.copytree(src, work)
    else:
        # 同步源码（模板更新后自动跟随），但不动 build/ 与 gradle 缓存
        for sub in ("src", "build.gradle", "gradle.properties", "settings.gradle"):
            s, d = src / sub, work / sub
            if s.is_dir():
                if d.exists():
                    shutil.rmtree(d)
                shutil.copytree(s, d)
            elif s.exists():
                shutil.copy2(s, d)

    # 写入本机 JDK 路径（Gradle 按 ISO-8859-1 读 properties，中文路径必须转义）
    jdk, ver = 探测JDK(info["java"])
    if jdk is None:
        raise SystemExit(f"找不到 Java {info['java']}+ 的 JDK（先跑 doctor 看体检结果）")
    gp = work / "gradle.properties"
    text = gp.read_text(encoding="utf-8")
    escaped = str(jdk).replace("\\", "/")
    if any(ord(ch) > 127 for ch in escaped):
        escaped = "".join(ch if ord(ch) < 128 else "\\u%04X" % ord(ch) for ch in escaped)
    text = re.sub(r"^org\.gradle\.java\.installations\.paths=.*$",
                  "org.gradle.java.installations.paths=" + escaped, text, flags=re.M)
    gp.write_text(text, encoding="utf-8")

    cmd = [str(work / ("gradlew.bat" if os.name == "nt" else "gradlew")), "build", "--console=plain"]
    if neo_version:
        cmd += [f"-Pneo_version={neo_version}"]
    if not 静默:
        打印(f" 构建代理（{时代}，JDK {ver}）…", C_GREY)
    r = subprocess.run(cmd, cwd=str(work), capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        打印(r.stdout[-4000:])
        打印(r.stderr[-2000:])
        raise SystemExit(f"代理构建失败（退出码 {r.returncode}）")
    libs = sorted((work / "build" / "libs").glob("*.jar"))
    libs = [p for p in libs if "sources" not in p.name]
    if not libs:
        raise SystemExit("代理构建成功但没找到 jar")
    return work, libs[0]


# ══════════════════════════════════════════════════════════════════
# 会话：起客户端 → 发命令 → 收结果
# ══════════════════════════════════════════════════════════════════
class 会话:
    def __init__(self, project: Path, run_dir: Path, 时代: str, jar: Path,
                 启动配置: dict | None = None, 超时秒=900, 详细=True):
        self.project = Path(project)
        self.run_dir = Path(run_dir)
        self.时代 = 时代
        self.jar = Path(jar)
        self.工作目录 = self.run_dir / "测试代理"
        self.起始配置 = 启动配置 or {}
        self.超时秒 = 超时秒
        self.详细 = 详细
        self.进程: subprocess.Popen | None = None
        self._out_pos = 0
        self._next_id = 1
        self.结果: list[dict] = []
        self.失败: list[str] = []
        self._容忍失败 = False
        self.最后一次结果: dict | None = None
        self.日志文件 = self.工作目录 / "驱动.log"
        self.客户端日志 = self.run_dir / "logs" / "latest.log"

    # ── 生命周期 ────────────────────────────────────────────────
    def 启动(self, 世界配置: dict | None = None):
        self.工作目录.mkdir(parents=True, exist_ok=True)
        for f in ("outbox.jsonl", "就绪.flag", "状态.json"):
            p = self.工作目录 / f
            if p.exists():
                p.unlink()
        # inbox 清空：每次运行从零开始
        (self.工作目录 / "inbox.jsonl").write_text("", encoding="utf-8")

        cfg = {"world": {"mode": "none"}}
        cfg.update(self.起始配置)
        if 世界配置:
            cfg["world"] = 世界配置
        (self.工作目录 / "启动.json").write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

        g = str(self.project / ("gradlew.bat" if os.name == "nt" else "gradlew"))
        cmd = [g,
               "--init-script", str(注入脚本),
               f"-Dmctestagent.jars={self.jar}",
               "runClient", "--console=plain"]
        self.日志文件.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.日志文件, "w", encoding="utf-8", errors="replace")
        打印(f" 拉起客户端：{' '.join(cmd[:2])} … runClient", C_GREY)
        快照窗口基线()          # 之后 找游戏窗口() 只认启动后新出现的窗口（含收尾关窗）
        self.进程 = subprocess.Popen(cmd, cwd=str(self.project), stdout=self._fh,
                                     stderr=subprocess.STDOUT)
        self._deadline = time.time() + self.超时秒
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.收尾()
        return False

    def 收尾(self, 等待秒=120):
        """正常路径是场景里 agent.退出() 自己关；这里是兜底：超时/异常/客户端卡住。"""
        if self.进程 is None:
            return
        t0 = time.time()
        while self.进程.poll() is None and time.time() - t0 < 等待秒:
            time.sleep(0.5)
        if self.进程.poll() is None:
            打印(" 客户端没自己退 → 发 WM_CLOSE 优雅关闭", C_YELLOW)
            w = 找游戏窗口()
            if w:
                关窗口(w["hwnd"])
            t1 = time.time()
            while self.进程.poll() is None and time.time() - t1 < 60:
                time.sleep(0.5)
        if self.进程.poll() is None:
            打印(" 还是没退 → 杀进程", C_YELLOW)
            self.进程.kill()
        try:
            self._fh.close()
        except Exception:
            pass

    def 客户端活着(self) -> bool:
        return self.进程 is not None and self.进程.poll() is None

    # ── 命令收发 ────────────────────────────────────────────────
    def _读结果(self) -> list[dict]:
        f = self.工作目录 / "outbox.jsonl"
        if not f.exists():
            return []
        out = []
        with open(f, "r", encoding="utf-8", errors="replace") as fh:
            fh.seek(self._out_pos)
            for line in fh:
                line = line.strip()
                if line.startswith("{"):
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
            self._out_pos = fh.tell()
        self.结果.extend(out)
        return out

    def _发(self, 命令: dict) -> int:
        cid = self._next_id
        self._next_id += 1
        ǒ = {"id": cid, **命令}
        with open(self.工作目录 / "inbox.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(ǒ, ensure_ascii=False) + "\n")
            fh.flush()
        return cid

    def 执行(self, op: str, 超时秒=120, **kw):
        if not self.客户端活着():
            raise RuntimeError("客户端已经退出了，命令发不出去")
        cid = self._发({"op": op, **kw})
        t0 = time.time()
        while time.time() - t0 < 超时秒:
            for r in self._读结果():
                if r.get("op") == "exit":
                    continue
                if r.get("id") == cid:
                    self.最后一次结果 = r
                    ok = r.get("ok")
                    if self.详细:
                        if ok:
                            打印(f"  ✔ {op} {json.dumps(kw, ensure_ascii=False)[:110]}", C_GREEN)
                        elif self._容忍失败:
                            打印(f"  ○ {op} 按预期失败：{r.get('error')}", C_GREY)
                        else:
                            打印(f"  ✘ {op} {json.dumps(kw, ensure_ascii=False)[:110]}  → {r.get('error')}", C_RED)
                    if not ok and not self._容忍失败:
                        self.失败.append(f"{op} 失败：{r.get('error')}")
                    return r
            if not self.客户端活着():
                raise RuntimeError(f"等 {op} 结果时客户端退出了（看 {self.客户端日志}）")
            time.sleep(0.15)
        raise TimeoutError(f"{op} 等结果超时（{超时秒}s）")

    def 预期失败(self, 方法, *args, **kwargs) -> str:
        """负样本：这一步**应当**失败，失败了才算通过。返回错误文本。

        没有负样本的验证等于没验证 —— 一个恒真的判据看起来永远是绿的。
        """
        self._容忍失败 = True
        try:
            方法(*args, **kwargs)
        finally:
            self._容忍失败 = False
        r = self.最后一次结果
        if r is None or r.get("ok"):
            raise AssertionError(f"这一步本该失败，却成功了：{r}")
        return r.get("error", "")

    # ── 场景 DSL ────────────────────────────────────────────────
    def 等就绪(self, 超时秒=300):
        """等代理报告「客户端已到主菜单」。"""
        t0 = time.time()
        while time.time() - t0 < 超时秒:
            for r in self._读结果():
                if r.get("op") == "boot":
                    if self.详细:
                        打印(f"  ✔ 客户端就绪：{json.dumps(r.get('data'), ensure_ascii=False)}", C_GREEN)
                    if not r.get("ok"):
                        self.失败.append("启动阶段失败：" + str(r.get("error")))
                    return r
            if not self.客户端活着():
                raise RuntimeError(f"客户端在就绪前退出了（看 {self.客户端日志}）")
            time.sleep(0.3)
        raise TimeoutError("等客户端就绪超时")

    def 进世界(self, mode="create", **kw):
        """启动阶段已按启动配置进世界；这里只做"确认"，需要换世界时重跑。"""
        r = self.等就绪()
        return r.get("data", {})

    def 命令(self, command: str, 超时秒=60):
        return self.执行("cmd", 超时秒=超时秒, command=command)

    def 等(self, ticks=20, 超时秒=120):
        return self.执行("wait", 超时秒=超时秒, ticks=ticks)

    def 等条件(self, what: str, value: str = "", timeout: int = 600, 超时秒=None):
        return self.执行("wait_for", 超时秒=超时秒 or (timeout / 20 + 30), what=what, value=value, timeout=timeout)

    def 等界面(self, 类名: str, timeout: int = 600):
        return self.等条件("screen", 类名, timeout)

    def 截图(self, name="shot", 超时秒=60):
        return self.执行("shot", 超时秒=超时秒, name=name)

    def 按键(self, mapping: str, state="tap"):
        return self.执行("key", mapping=mapping, state=state)

    def 开界面(self, 类名: str):
        return self.执行("screen", action="open", value=类名)

    def 关界面(self):
        return self.执行("screen", action="close")

    def 点(self, 文本或键: str, button=0):
        return self.执行("screen", action="click", value=文本或键, button=button)

    def 界面(self) -> dict:
        return self.执行("screen", action="dump").get("data", {})

    def 输入(self, 文本: str):
        return self.执行("screen", action="type", value=文本)

    def 状态(self, radius=0, blocks=False) -> dict:
        return self.执行("state", 超时秒=60, radius=radius, withBlocks=blocks).get("data", {})

    def 方块(self, pos: str) -> str:
        return self.执行("block", pos=pos).get("data", {}).get("block", "")

    def 实体(self, radius=16) -> list:
        return self.执行("entities", radius=radius).get("data", {}).get("entities", [])

    # ── 玩家输入层：真的按下去 ──────────────────────────────────
    def 移动(self, direction="forward", ticks=20, sprint=False, sneak=False):
        """走路。direction = forward/back/left/right；走完返回 {from,to,ticks}。"""
        return self.执行("move", 超时秒=ticks / 20 + 30,
                       direction=direction, ticks=ticks, sprint=sprint, sneak=sneak).get("data", {})

    def 跳(self, ticks=1):
        return self.执行("jump", ticks=ticks).get("data", {})

    def 看向(self, yaw=None, pitch=None, target=None) -> dict:
        """转向。给 target（世界坐标点 "x,y,z"）或 yaw/pitch。返回里带 crosshair（准星指着什么）。"""
        kw = {}
        if yaw is not None:
            kw["yaw"] = yaw
        if pitch is not None:
            kw["pitch"] = pitch
        if target is not None:
            kw["target"] = target
        return self.执行("look", **kw).get("data", {})

    def 看向方块(self, pos: str) -> dict:
        """瞄准一个方块的**中心**（"x,y,z" 写整数即可）。"""
        x, y, z = [float(v) for v in pos.replace(" ", "").split(",")[:3]]
        return self.看向(target=f"{x + 0.5},{y + 0.5},{z + 0.5}")

    def 右键(self, ticks=1) -> dict:
        """右键：用物品 / 放置 / 交互实体。ticks>1 按住（吃东西、拉弓）。"""
        return self.执行("use", 超时秒=ticks / 20 + 30, ticks=ticks).get("data", {})

    def 挖(self, pos: str | None = None, ticks=200) -> dict:
        """左键：挖方块（给 pos 会先瞄准）/ 打实体。返回 {block,pos,broken,ticks}。"""
        return self.执行("mine", 超时秒=ticks / 20 + 30, pos=pos or "", ticks=ticks).get("data", {})

    def 放置(self, pos: str) -> dict:
        """在 pos 处放一块（自动找贴靠面 + 瞄准 + 右键）。返回 {target,against,face,result}。"""
        return self.执行("place", 超时秒=60, pos=pos).get("data", {})

    def 快捷栏(self, slot: int) -> dict:
        """切到第 slot 格（1~9）。"""
        return self.执行("hotbar", slot=slot).get("data", {})

    def 丢出(self) -> dict:
        return self.执行("drop").get("data", {})

    def 开背包(self) -> dict:
        """真按 E 打开背包（界面和玩家手点出来的一样）。"""
        return self.执行("open_inventory").get("data", {})

    def 容器(self, action="dump", slot=-1, button=0, shift=False) -> dict:
        """容器/背包界面：dump 槽位 / click 点槽位 / close 关掉。"""
        return self.执行("container", action=action, slot=slot, button=button, shift=shift).get("data", {})

    def 点格子(self, slot: int, button=0, shift=False) -> dict:
        return self.容器(action="click", slot=slot, button=button, shift=shift)

    def 关容器(self) -> dict:
        return self.容器(action="close")

    def 说话(self, text: str) -> dict:
        return self.执行("say", text=text)

    def 回显(self, pattern="", all=False) -> list:
        return self.执行("chat", pattern=pattern, all=all).get("data", {}).get("lines", [])

    def 日志(self, lines=100, pattern="", fromLine=0) -> list:
        return self.执行("log", lines=lines, pattern=pattern, fromLine=fromLine).get("data", {}).get("lines", [])

    def 退出(self, code=0, reason="场景结束"):
        try:
            return self.执行("quit", code=code, reason=reason, 超时秒=30)
        except Exception:
            return None

    # 中文别名（同一个东西，两套名字都认）
    进世界 = 进世界
    命令_ = 命令
    等_ = 等
    截图_ = 截图
    按键_ = 按键
    状态_ = 状态
    方块_ = 方块
    点_ = 点
    退出_ = 退出

    # ── 取证 ────────────────────────────────────────────────────
    def 扫客户端异常(self) -> list[str]:
        """从客户端日志里捞 ERROR / Exception —— 只看本次运行（用 latest.log 的尾部）。"""
        return 扫日志异常(self.客户端日志)

    def 报告(self) -> dict:
        shots = sorted((self.工作目录 / "截图").glob("*.png"))
        return {
            "ok": not self.失败,
            "失败": self.失败,
            "步骤数": len([r for r in self.结果 if r.get("id", 0) > 0]),
            "失败步骤": [r for r in self.结果 if r.get("ok") is False],
            "截图": [str(p) for p in shots],
            "客户端日志": str(self.客户端日志),
            "驱动日志": str(self.日志文件),
            "工作目录": str(self.工作目录),
            "客户端异常": self.扫客户端异常(),
        }


# ══════════════════════════════════════════════════════════════════
# 子命令
# ══════════════════════════════════════════════════════════════════
def cmd_doctor(args):
    ok = True
    打印("== 游戏内测试 体检 ==")
    for 时代, info in 时代表.items():
        jdk, ver = 探测JDK(info["java"])
        if jdk:
            打印(f"  ✔ JDK {ver}：{jdk}", C_GREEN)
        else:
            打印(f"  ✘ 找不到 Java {info['java']}+ 的 JDK", C_RED)
            ok = False
    if not 注入脚本.exists():
        打印(f"  ✘ 缺少注入脚本 {注入脚本}", C_RED)
        ok = False
    else:
        打印(f"  ✔ 注入脚本就位", C_GREEN)
    if args.project:
        p = Path(args.project)
        props = 读工程属性(p)
        时代, info = 判时代(props)
        if not 时代:
            打印(f"  ✘ 工程的 minecraft_version={props.get('minecraft_version')!r} 不在支持列表 {list(时代表)}", C_RED)
            ok = False
        else:
            打印(f"  ✔ 工程 {p.name}：MC {props.get('minecraft_version')} / NeoForge {props.get('neo_version')}", C_GREEN)
            w = 代理目录(时代) / "build" / "libs"
            have = sorted(w.glob("*.jar")) if w.exists() else []
            打印(f"    代理 jar：{'已构建 ' + have[0].name if have else '未构建（先跑 python 游戏内测试.py agent）'}")
    w = 找游戏窗口()
    if w:
        打印(f"  · 当前 Minecraft 窗口：{w['title']!r}"
             f"（pid {w.get('pid', '?')}，{w.get('exe') or '?'}，{w['size']}）")
    else:
        打印("  · 当前 Minecraft 窗口：（没有）")
    打印("== 体检" + ("通过 ==" if ok else "不通过 =="), C_GREEN if ok else C_RED)
    return 0 if ok else 2


def cmd_agent(args):
    时代 = args.era
    work, jar = 构建代理(时代, neo_version=args.neo_version, 强制=args.force)
    打印(f"✔ 代理就位：{jar}", C_GREEN)
    打印(f"  工程目录：{work}")
    return 0


def cmd_inject(args):
    project = Path(args.project)
    props = 读工程属性(project)
    时代, info = 判时代(props)
    if not 时代:
        raise SystemExit(f"不支持的 MC 版本 {props.get('minecraft_version')!r}")
    _, jar = 构建代理(时代, neo_version=props.get("neo_version"), 静默=True)
    g = str(project / ("gradlew.bat" if os.name == "nt" else "gradlew"))
    打印(f'cd "{project}" && "{g}" --init-script "{注入脚本}" -Dmctestagent.jars="{jar}" runClient')
    return 0


def cmd_shot(args):
    w = 找游戏窗口()
    if not w:
        打印("没有找到 Minecraft 窗口", C_RED)
        return 2
    out = Path(args.out)
    ok, size = 抓窗口(w["hwnd"], out)
    打印(f"✔ 抓到 {w['title']!r} {size} → {out}（PrintWindow={ok}，没抢前台焦点）", C_GREEN)
    return 0


def cmd_close(args):
    w = 找游戏窗口()
    if not w:
        打印("没有找到 Minecraft 窗口", C_YELLOW)
        return 2
    关窗口(w["hwnd"])
    打印(f"✔ 已发 WM_CLOSE 给 {w['title']!r}", C_GREEN)
    return 0


def cmd_run(args):
    project = Path(args.project).resolve()
    props = 读工程属性(project)
    时代, info = 判时代(props)
    if not 时代:
        raise SystemExit(f"工程的 minecraft_version={props.get('minecraft_version')!r} 暂不支持"
                         f"（当前支持 {list(时代表)}；其它版本见 references/26.x-游戏内自动化测试.md）")
    run_dir = Path(args.rundir) if args.rundir else 探测运行目录(project)

    _, jar = 构建代理(时代, neo_version=props.get("neo_version"), 静默=not args.verbose)
    打印(f" 代理 jar：{jar.name}", C_GREY)

    # 场景世界配置
    世界 = {"mode": args.world_mode, "name": args.world, "preset": args.preset,
            "seed": args.seed, "gamemode": args.gamemode, "cheats": True,
            "structures": args.structures}
    if args.world_mode == "none":
        世界 = {"mode": "none"}
    if args.world_mode == "create" and args.clean_world:
        saves = run_dir / "saves"
        if saves.is_dir():
            for d in saves.iterdir():
                if d.is_dir() and d.name.startswith("mctestagent"):
                    shutil.rmtree(d, ignore_errors=True)
            打印(" 已清理上一轮的代理世界", C_GREY)

    选项 = {"pauseOnLostFocus": False}       # 这一条**必须**关：不关的话窗口一失焦单人世界就暂停
    if args.gui_scale > 0:
        选项["guiScale"] = args.gui_scale
    if args.render_distance > 0:
        选项["renderDistance"] = args.render_distance
    启动配置 = {
        "world": 世界,
        "options": 选项,
        "settleTicks": args.settle,
    }

    会话对象 = 会话(project, run_dir, 时代, jar, 启动配置, 超时秒=args.timeout, 详细=True)
    打印(f"== 跑场景：{args.scenario or '（无场景，只启动并取证）'} ==")
    t0 = time.time()
    try:
        with 会话对象:
            会话对象.启动()
            if args.scenario:
                src = Path(args.scenario).read_text(encoding="utf-8")
                环境 = {"agent": 会话对象, "会话": 会话对象, "__name__": "__scenario__"}
                exec(compile(src, str(args.scenario), "exec"), 环境)
            else:
                会话对象.进世界()
            会话对象.退出()
    except Exception as e:
        会话对象.失败.append(f"驱动异常：{type(e).__name__}: {e}")
        打印(f"✘ {type(e).__name__}: {e}", C_RED)

    报 = 会话对象.报告()
    报["耗时秒"] = round(time.time() - t0, 1)
    报["工程"] = str(project)
    报_json = 会话对象.工作目录 / "报告.json"
    报_json.write_text(json.dumps(报, ensure_ascii=False, indent=2), encoding="utf-8")

    打印("")
    打印("== 结果 ==")
    打印(f"  步骤 {报['步骤数']} 步，失败 {len(报['失败步骤'])} 步，耗时 {报['耗时秒']}s",
         C_GREEN if 报["ok"] else C_RED)
    for f in 报["失败"][:20]:
        打印(f"   ✘ {f}", C_RED)
    if 报["截图"]:
        打印(f"  截图 {len(报['截图'])} 张：")
        for s in 报["截图"]:
            打印(f"    {s}")
    if 报["客户端异常"]:
        打印(f"  ⚠ 客户端日志里有 {len(报['客户端异常'])} 条异常（下面前 8 条）：")
        for l in 报["客户端异常"][:8]:
            打印(f"    {l}", C_YELLOW)
    打印(f"  报告：{报_json}")
    return 0 if 报["ok"] else 1


# ══════════════════════════════════════════════════════════════════
# smoke —— 🔴 进游戏实测（交付门禁）
#   目的只有一个：证明这个 mod **能真的进游戏**，而不是「编译过了所以应该能进」。
#   build 成功 ≠ 能进游戏：缺依赖 / Mixin 崩 / 注册期 NPE / 画第一帧崩，全都是编译期查不出的。
#   退出码：0 = 全部判据过（可谈交付） ｜ 1 = 有判据没过 ｜ 2 = 参数/环境问题
# ══════════════════════════════════════════════════════════════════
def _smoke_有代理(project, run_dir, 时代, props, args, 记):
    """26.x 代理通道：到主菜单 → 自动建世界进世界 → 推进 tick → 状态可读 → 截图 → 扫异常。"""
    _, jar = 构建代理(时代, neo_version=props.get("neo_version"), 静默=not args.verbose)
    打印(f" 代理 jar：{jar.name}", C_GREY)

    世界 = {"mode": "create", "name": args.world, "preset": args.preset,
            "seed": args.seed, "gamemode": "creative", "cheats": True,
            "structures": args.structures}
    启动配置 = {"world": 世界,
                "options": {"pauseOnLostFocus": False},   # 不关这条窗口一失焦单人世界就暂停
                "settleTicks": max(args.settle, 20)}
    s = 会话(project, run_dir, 时代, jar, 启动配置, 超时秒=args.timeout, 详细=args.verbose)
    try:
        with s:
            s.启动()
            r = s.等就绪(超时秒=args.boot_timeout)
            记("客户端启动到主菜单", bool(r.get("ok")),
               str(r.get("error") or json.dumps(r.get("data"), ensure_ascii=False))[:200])
            if not r.get("ok"):
                return s
            d = r.get("data") or {}
            记("自动建世界并进入", bool(d.get("world") and d.get("dimension")),
               json.dumps(d, ensure_ascii=False)[:200])
            try:
                st0 = s.状态()
                s.等(args.settle, 超时秒=args.settle / 20 + 120)
                st1 = s.状态()
                g0 = (st0.get("world") or {}).get("gameTime")
                g1 = (st1.get("world") or {}).get("gameTime")
                记(f"世界推进 {args.settle} tick（gameTime 在涨 = 没卡死/没暂停）",
                   g0 is not None and g1 is not None and g1 > g0, f"{g0} → {g1}")
                记("玩家状态可读（人真的站在世界里）",
                   bool((st1.get("player") or {}).get("pos")),
                   str((st1.get("player") or {}).get("pos")))
            except Exception as e:
                记(f"世界推进 {args.settle} tick", False, f"{type(e).__name__}: {e}")
            try:
                rr = s.截图("冒烟-进游戏")
                p = (rr.get("data") or {}).get("path")
                if p:
                    ok, 说明 = 判非黑屏(Path(p))
                    记("渲染出了画面（非黑屏）", ok, 说明)
                else:
                    记("截图", False, "代理没返回截图路径")
            except Exception as e:
                记("截图", False, f"{type(e).__name__}: {e}")
            s.退出()
    except Exception as e:
        记("冒烟过程未跑完", False, f"{type(e).__name__}: {e}")
    return s


def 默认就绪行():
    """无代理通道下「已到主菜单」的日志判据行候选，用 | 分隔（命中任一即可）。

    实测：**这行随版本变**，写死一个必然在别的版本上假阴性 ——
      26.x（1.21+）  → ``Loaded 0 entity animations``
      1.20.1         → ``Sound engine started``
                       （1.20.1 到主菜单时还会画一堆 atlas 行，之后就不再输出；
                         日志停住是**正常**的，别当成卡住）
    """
    return "Loaded 0 entity animations|Sound engine started"


def _smoke_无代理(project, run_dir, args, 记):
    """无代理通道（1.20.1 / 1.21.x 等）：起 runClient → 等窗口 → 等主菜单标志行 → 抓图 → 扫日志。

    只能验到**主菜单**——没有进程内代理就没法自动点「创建世界」。
    进世界那一步按 references/26.x-游戏内自动化测试.md §7 的替代路线走（或人工进一次）。
    """
    g = str(project / ("gradlew.bat" if os.name == "nt" else "gradlew"))
    驱动日志 = run_dir / "测试代理" / "smoke-驱动.log"
    驱动日志.parent.mkdir(parents=True, exist_ok=True)
    fh = open(驱动日志, "w", encoding="utf-8", errors="replace")
    打印(" 拉起客户端：runClient（无代理通道，只验到主菜单）", C_GREY)
    快照窗口基线()          # 只认启动后新出现的窗口（挡掉桌面上别的 "Minecraft *"）
    # 日志快照：latest.log 可能是**上一次运行**留下的，里面早就有「主菜单标志行」。
    # 不记这两项的话，下面的等待循环第一圈就会命中旧文件 → 假阳性。
    日志 = run_dir / "logs" / "latest.log"
    if 日志.exists():
        日志旧_mtime = 日志.stat().st_mtime
        日志旧_size = 日志.stat().st_size
    else:
        日志旧_mtime, 日志旧_size = None, -1
    p = subprocess.Popen([g, "runClient", "--console=plain"], cwd=str(project),
                         stdout=fh, stderr=subprocess.STDOUT)
    try:
        窗口 = None
        t0 = time.time()
        while time.time() - t0 < args.boot_timeout:
            if p.poll() is not None:
                记("客户端进程没中途退出", False,
                   f"进程在窗口出现前就退出了（退出码 {p.poll()}），看 {驱动日志}")
                return
            窗口 = 找游戏窗口()
            if 窗口:
                break
            time.sleep(1)
        记("Minecraft 窗口出现", bool(窗口),
           f"{窗口['title']} {窗口['size']}" if 窗口 else f"{args.boot_timeout}s 内没等到窗口")
        if not 窗口:
            return

        # 等「到主菜单」的标志行（日志停在这一行之后就不再输出是正常的，别当卡住）
        标志 = args.ready_line
        候选 = [s.strip() for s in 标志.split("|") if s.strip()]
        到主菜单 = False
        命中 = None
        t1 = time.time()
        while time.time() - t1 < args.boot_timeout:
            if p.poll() is not None:
                break
            if 日志.exists():
                st = 日志.stat()
                # 🔴 先验「这份日志是本次写的」再去找标志行 —— 否则旧日志第一圈就命中。
                #    （2026-10-14 smoke4：窗口是别人的、日志是上一轮的，整场假绿）
                新鲜 = (日志旧_mtime is None
                        or st.st_mtime > 日志旧_mtime + 1e-6
                        or st.st_size < 日志旧_size)
                if 新鲜:
                    txt = 日志.read_text(encoding="utf-8", errors="replace")
                    命中 = next((c for c in 候选 if c in txt), None)
                    if 命中:
                        到主菜单 = True
                        break
            time.sleep(2)
        记(f"日志（本次新写的）出现主菜单标志行「{命中 or 标志}」", 到主菜单,
           "资源加载完成、客户端停在标题界面" if 到主菜单
           else f"没等到（候选：{标志}）；判据行随版本变，用 --ready-line 指定"
                f"（驱动日志：{驱动日志}）")

        if p.poll() is not None:
            记("客户端进程存活", False, f"退出码 {p.poll()}")

        time.sleep(max(args.settle, 20) / 20)
        图 = run_dir / "测试代理" / "冒烟-主菜单.png"
        w = 找游戏窗口()
        if w:
            抓窗口(w["hwnd"], 图)
            ok, 说明 = 判非黑屏(图)
            记("窗口抓图非黑屏", ok, 说明)
    finally:
        w = 找游戏窗口()
        if w:
            关窗口(w["hwnd"])
        t2 = time.time()
        while p.poll() is None and time.time() - t2 < 60:
            time.sleep(0.5)
        if p.poll() is None:
            p.kill()
        try:
            fh.close()
        except Exception:
            pass


def cmd_smoke(args):
    project = Path(args.project).resolve()
    if not (project / "gradle.properties").exists():
        打印(f"✘ 不像一个 MC 模组工程（没有 gradle.properties）：{project}", C_RED)
        return 2
    props = 读工程属性(project)
    时代, info = 判时代(props)
    mc = props.get("minecraft_version", "?")
    run_dir = Path(args.rundir) if args.rundir else 探测运行目录(project)
    判据 = []

    def 记(名, ok, 说明=""):
        判据.append({"判据": 名, "ok": bool(ok), "说明": str(说明)})
        打印(f"  {'✔' if ok else '✘'} {名}" + (f"：{说明}" if 说明 else ""),
             C_GREEN if ok else C_RED)

    打印(f"== 进游戏实测（smoke）：{project.name}｜MC {mc}｜NeoForge {props.get('neo_version', '?')} ==")
    打印(f" 通道：{'代理（26.x，能自动进世界）' if 时代 else '无代理（只到主菜单）'}", C_GREY)
    t0 = time.time()

    if 时代:
        s = _smoke_有代理(project, run_dir, 时代, props, args, 记)
        客户端日志 = s.客户端日志
        工作目录 = s.工作目录
        异常 = s.扫客户端异常()
    else:
        _smoke_无代理(project, run_dir, args, 记)
        客户端日志 = run_dir / "logs" / "latest.log"
        工作目录 = run_dir / "测试代理"
        异常 = 扫日志异常(客户端日志)

    记("客户端日志无 ERROR/Exception", not 异常,
       f"{len(异常)} 条" + (f"｜首条：{异常[0][:200]}" if 异常 else ""))

    ok = all(j["ok"] for j in 判据) and bool(判据)
    报 = {
        "ok": ok,
        "工程": str(project),
        "mc版本": mc,
        "通道": "agent" if 时代 else "window",
        "判据": 判据,
        "客户端日志": str(客户端日志),
        "耗时秒": round(time.time() - t0, 1),
        "客户端异常前20条": 异常[:20],
    }
    工作目录.mkdir(parents=True, exist_ok=True)
    报_json = 工作目录 / "冒烟报告.json"
    报_json.write_text(json.dumps(报, ensure_ascii=False, indent=2), encoding="utf-8")

    打印("")
    if ok:
        打印(f"✔ 进游戏实测通过（{len(判据)} 条判据全绿，{报['耗时秒']}s）——可以谈交付了", C_GREEN)
    else:
        挂 = [j["判据"] for j in 判据 if not j["ok"]]
        打印(f"✘ 进游戏实测未通过：{len(挂)}/{len(判据)} 条挂了 → {'、'.join(挂)}", C_RED)
    if 异常:
        打印(f"  ⚠ 客户端日志异常 {len(异常)} 条（前 8 条）：", C_YELLOW)
        for l in 异常[:8]:
            打印(f"    {l}", C_YELLOW)
    打印(f"  报告：{报_json}")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="游戏内测试驱动器（真实客户端 + 场景脚本）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("doctor", help="体检：JDK / 注入脚本 / 工程 / 代理 jar")
    d.add_argument("--project")
    d.set_defaults(fn=cmd_doctor)

    a = sub.add_parser("agent", help="构建/更新测试代理 jar")
    a.add_argument("--era", default="26.1", choices=list(时代表))
    a.add_argument("--neo-version", default=None, help="按指定 NeoForge 构建号编译（默认跟模板）")
    a.add_argument("--force", action="store_true", help="删掉工作目录重建")
    a.set_defaults(fn=cmd_agent)

    i = sub.add_parser("inject", help="只打印注入命令（不改工程）")
    i.add_argument("--project", required=True)
    i.set_defaults(fn=cmd_inject)

    s = sub.add_parser("shot", help="抓 Minecraft 窗口截图（不抢焦点）")
    s.add_argument("--out", default="mc窗口.png")
    s.set_defaults(fn=cmd_shot)

    c = sub.add_parser("close", help="优雅关闭 Minecraft 窗口")
    c.set_defaults(fn=cmd_close)

    k = sub.add_parser("smoke", help="🔴 进游戏实测（交付门禁：起真实客户端→进世界→不黑屏→日志无异常）")
    k.add_argument("--project", required=True)
    k.add_argument("--rundir", help="runClient 的工作目录（默认 <工程>/run）")
    k.add_argument("--world", default="mctestagent_world")
    k.add_argument("--preset", default="flat", help="flat / normal / amplified / single_biome_surface / debug")
    k.add_argument("--seed", default="1")
    k.add_argument("--structures", action="store_true")
    k.add_argument("--settle", type=int, default=100,
                   help="进世界后推进这么多 tick 再取证（默认 100，让第一帧渲染出来）")
    k.add_argument("--boot-timeout", type=int, default=420, help="等「到主菜单/窗口」的超时（秒）")
    k.add_argument("--timeout", type=int, default=900, help="整场超时（秒）")
    k.add_argument("--ready-line", default=默认就绪行(),
                   help="无代理通道下判定「到主菜单」的日志标志行；"
                        "多个候选用 | 分隔，命中任一即可")
    k.add_argument("--verbose", action="store_true")
    k.set_defaults(fn=cmd_smoke)

    r = sub.add_parser("run", help="跑一份场景")
    r.add_argument("--project", required=True)
    r.add_argument("--scenario", help="场景 Python 脚本路径")
    r.add_argument("--rundir", help="runClient 的工作目录（默认 <工程>/run）")
    r.add_argument("--world-mode", default="create", choices=["create", "open", "none"])
    r.add_argument("--world", default="mctestagent_world")
    r.add_argument("--preset", default="flat", help="flat / normal / amplified / single_biome_surface / debug")
    r.add_argument("--seed", default="1")
    r.add_argument("--gamemode", default="creative", choices=["creative", "survival", "hardcore", "debug"])
    r.add_argument("--structures", action="store_true")
    r.add_argument("--clean-world", action="store_true", default=True,
                   help="create 前清掉上一轮的 mctestagent* 世界（默认开）")
    r.add_argument("--gui-scale", type=int, default=0, help=">0 时才覆盖 guiScale（默认不动用户设置）")
    r.add_argument("--render-distance", type=int, default=0, help=">0 时才覆盖 renderDistance（默认不动用户设置）")
    r.add_argument("--settle", type=int, default=40, help="进世界后先等这么多 tick")
    r.add_argument("--timeout", type=int, default=900, help="整场超时（秒）")
    r.add_argument("--verbose", action="store_true")
    r.set_defaults(fn=cmd_run)

    args = ap.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()

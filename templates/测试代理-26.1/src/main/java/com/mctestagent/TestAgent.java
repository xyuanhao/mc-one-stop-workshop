package com.mctestagent;

import net.neoforged.fml.common.Mod;

/**
 * 游戏内测试代理 —— 模组入口。
 *
 * <p>这个 mod 不是给玩家用的：它是一个「进程内测试代理」，让外部脚本（或 AI）能在
 * 真实客户端里进世界、执行命令、按键、开界面、截图、导出状态、退出。
 *
 * <p>协议是**文件式**的（不用端口、不触发防火墙），全部路径相对于游戏运行目录
 * （也就是 {@code run/}）：
 * <pre>
 *   测试代理/启动.json    外部写入：启动时要做什么（进哪个世界、开什么选项）
 *   测试代理/inbox.jsonl  外部追加：一行一条命令
 *   测试代理/outbox.jsonl 代理追加：一行一条结果
 *   测试代理/状态.json    代理每秒覆盖：心跳 + 当前界面 + 玩家坐标
 *   测试代理/就绪.flag    代理写入：客户端已到主菜单，可以开始发命令
 *   测试代理/截图/*.png   代理写入：截图产物
 * </pre>
 */
@Mod(TestAgent.MODID)
public final class TestAgent {
    public static final String MODID = "mctestagent";

    public TestAgent() {
        Io.ensureDirs();
        Io.warn("测试代理已加载，工作目录 " + Io.DIR.toAbsolutePath());
    }
}

package com.mctestagent;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import net.minecraft.client.Minecraft;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;
import java.util.regex.Pattern;

/**
 * 代理主循环。
 *
 * <p>每 tick 做四件事：① 启动流程（只在开头）；② 收 inbox 里的新命令；
 * ③ 推进当前动作；④ 每秒写一次心跳。
 *
 * <p>命令是**串行**执行的：一次只有一个动作在跑，前一个结束才发下一个。
 * 这样脚本里的因果顺序才是确定的，不用去猜"命令生效了没有"。
 */
public final class Agent {
    public static final Agent I = new Agent();

    private static final int CHAT_MAX = 500;

    private int tickCount;
    private boolean bootStarted;
    private boolean bootDone;
    private Action current;
    private final Deque<JsonObject> pending = new ArrayDeque<>();
    private int inboxLines;
    private final List<String> chat = new ArrayList<>();
    private int inWorldTicks;
    private int autoExitTicks = -1;
    private boolean quitting;
    private int exitCode;
    private String lastError = "";

    private Agent() {
    }

    public int tickCount() {
        return tickCount;
    }

    /** 客户端 tick 回调（客户端线程）。 */
    void tick() {
        Minecraft mc = Minecraft.getInstance();
        if (mc == null) return;
        tickCount++;
        try {
            if (mc.level != null && mc.player != null) inWorldTicks++;
            if (!bootDone) {
                if (!bootStarted) {
                    bootStarted = true;
                    current = new Actions.BootAction();
                }
                step(mc);
                return;
            }
            pump();
            step(mc);
            if (autoExitTicks > 0 && inWorldTicks > autoExitTicks && !quitting) {
                requestQuit(0, "autoExitTicks 到期");
            }
            if (tickCount % 20 == 0) writeStatus(mc);
        } catch (Throwable t) {
            lastError = Actions.describe(t);
            Io.warn("tick 内异常：" + lastError);
        }
    }

    /** 推进当前动作（含超时处理）。 */
    private void step(Minecraft mc) {
        if (current == null) {
            JsonObject next = pending.poll();
            if (next == null) return;
            current = Actions.create(next);
        }
        JsonObject result;
        try {
            result = current.tick(mc);
        } catch (Throwable t) {
            result = current.fail(Actions.describe(t));
        }
        if (result == null) {
            if (current.elapsed() > current.timeoutTicks) result = current.timeout();
            else return;
        }
        finish(result);
    }

    private void finish(JsonObject result) {
        Action a = current;
        current = null;
        if (a != null && !result.has("op")) result.addProperty("op", a.op);
        boolean ok = result.has("ok") && result.get("ok").getAsBoolean();
        if (!ok) lastError = result.has("error") ? result.get("error").getAsString() : "未知失败";
        Io.appendLine(Io.OUTBOX, Io.GSON.toJson(result));
    }

    /** 读 inbox 里的新命令行，排进队列。 */
    private void pump() {
        List<String> lines = Io.readAllLines(Io.INBOX);
        if (lines.size() <= inboxLines) return;
        for (int i = inboxLines; i < lines.size(); i++) {
            String l = lines.get(i).trim();
            if (l.isEmpty() || l.startsWith("#")) continue;
            try {
                pending.add(JsonParser.parseString(l).getAsJsonObject());
            } catch (Throwable t) {
                JsonObject err = new JsonObject();
                err.addProperty("id", -1);
                err.addProperty("op", "parse");
                err.addProperty("ok", false);
                err.addProperty("error", "第 " + (i + 1) + " 行不是合法 JSON：" + t);
                Io.appendLine(Io.OUTBOX, Io.GSON.toJson(err));
            }
        }
        inboxLines = lines.size();
    }

    void onBootDone() {
        bootDone = true;
        // 注意：**不要**在这里把 inboxLines 跳过到文件末尾。
        // 外部脚本的常见用法是「启动作阶段就把整份场景写进 inbox」，
        // 跳过就等于整份场景不执行（这个坑很隐蔽：客户端一切正常，就是什么都不做）。
        JsonObject boot = Io.readJson(Io.BOOT);
        if (boot != null && boot.has("autoExitTicks")) autoExitTicks = boot.get("autoExitTicks").getAsInt();
        Io.warn("启动阶段完成，开始执行 inbox（" + Io.readAllLines(Io.INBOX).size() + " 行待处理）");
    }

    // ── 聊天缓冲区 ──────────────────────────────────────────────────────────

    void addChat(String line) {
        synchronized (chat) {
            chat.add(line);
            while (chat.size() > CHAT_MAX) chat.remove(0);
        }
    }

    int chatSize() {
        synchronized (chat) {
            return chat.size();
        }
    }

    List<String> chatLines(String pattern, boolean all) {
        Pattern p = pattern == null || pattern.isEmpty() ? null : Pattern.compile(pattern);
        List<String> out = new ArrayList<>();
        synchronized (chat) {
            int from = all ? 0 : Math.max(0, chat.size() - 100);
            for (int i = from; i < chat.size(); i++) {
                String l = chat.get(i);
                if (p == null || p.matcher(l).find()) out.add(l);
            }
        }
        return out;
    }

    String findChat(String regex, int fromIndex) {
        Pattern p = regex == null || regex.isEmpty() ? null : Pattern.compile(regex);
        synchronized (chat) {
            for (int i = Math.max(0, fromIndex); i < chat.size(); i++) {
                String l = chat.get(i);
                if (p == null || p.matcher(l).find()) return l;
            }
        }
        return null;
    }

    // ── 退出 ────────────────────────────────────────────────────────────────

    void requestQuit(int code, String reason) {
        if (quitting) return;
        quitting = true;
        exitCode = code;
        Io.warn("请求退出（code=" + code + "，原因：" + reason + "）");
        writeStatus(Minecraft.getInstance());
        // Minecraft.stop() 只是把 running 置 false，主循环下一轮就会走正常关闭流程，
        // 所以在 tick 里直接调是安全的（和点窗口关闭按钮同一条路）。
        try {
            Minecraft.getInstance().stop();
        } catch (Throwable t) {
            Io.warn("stop() 抛异常：" + t);
        }
        Io.appendLine(Io.OUTBOX, "{\"id\":-2,\"op\":\"exit\",\"ok\":true,\"code\":" + exitCode + ",\"reason\":"
                + Io.GSON.toJson(reason) + "}");
    }

    // ── 心跳 ────────────────────────────────────────────────────────────────

    private void writeStatus(Minecraft mc) {
        try {
            JsonObject o = new JsonObject();
            o.addProperty("tick", tickCount);
            o.addProperty("bootDone", bootDone);
            o.addProperty("inWorld", mc.level != null && mc.player != null);
            o.addProperty("inWorldTicks", inWorldTicks);
            o.addProperty("screen", mc.screen == null ? "" : mc.screen.getClass().getName());
            o.addProperty("pending", pending.size());
            if (current != null) {
                JsonObject c = new JsonObject();
                c.addProperty("id", current.id);
                c.addProperty("op", current.op);
                c.addProperty("elapsed", current.elapsed());
                o.add("current", c);
            }
            if (mc.player != null) {
                o.addProperty("pos", String.format("%.2f,%.2f,%.2f", mc.player.getX(), mc.player.getY(), mc.player.getZ()));
            }
            o.addProperty("chatLines", chatSize());
            o.addProperty("lastError", lastError);
            o.addProperty("quitting", quitting);
            JsonArray mods = new JsonArray();
            o.add("mods", mods);
            Io.writeText(Io.STATUS, Io.GSON.toJson(o));
        } catch (Throwable t) {
            Io.warn("写心跳失败：" + t);
        }
    }
}

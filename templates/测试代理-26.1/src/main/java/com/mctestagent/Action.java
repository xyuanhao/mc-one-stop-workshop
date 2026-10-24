package com.mctestagent;

import com.google.gson.JsonObject;

/** 一个可执行的动作。每 tick 推一次，返回非 null 表示完成。 */
abstract class Action {
    final long id;
    final String op;
    final int startTick;
    /** 超时 tick 数（20 tick = 1 秒）。超时按失败收尾，避免脚本永久卡死。 */
    int timeoutTicks = 1200;

    Action(long id, String op) {
        this.id = id;
        this.op = op;
        this.startTick = Agent.I.tickCount();
    }

    abstract JsonObject tick(net.minecraft.client.Minecraft mc) throws Exception;

    int elapsed() {
        return Agent.I.tickCount() - startTick;
    }

    JsonObject ok(JsonObject data) {
        JsonObject o = new JsonObject();
        o.addProperty("id", id);
        o.addProperty("op", op);
        o.addProperty("ok", true);
        o.addProperty("tick", Agent.I.tickCount());
        o.addProperty("elapsed", elapsed());
        if (data != null) o.add("data", data);
        return o;
    }

    JsonObject fail(String msg) {
        JsonObject o = new JsonObject();
        o.addProperty("id", id);
        o.addProperty("op", op);
        o.addProperty("ok", false);
        o.addProperty("tick", Agent.I.tickCount());
        o.addProperty("elapsed", elapsed());
        o.addProperty("error", msg);
        return o;
    }

    JsonObject timeout() {
        return fail("等超时了（已等 " + elapsed() + " tick）");
    }
}

package com.mctestagent;

import net.neoforged.api.distmarker.Dist;
import net.neoforged.bus.api.SubscribeEvent;
import net.neoforged.fml.common.EventBusSubscriber;
import net.neoforged.neoforge.client.event.ClientTickEvent;

/** 客户端 tick 挂载点。所有动作都在客户端线程上跑，避免跨线程碰游戏状态。 */
@EventBusSubscriber(modid = TestAgent.MODID, value = Dist.CLIENT)
public final class Ticker {
    private Ticker() {
    }

    @SubscribeEvent
    static void onClientTick(ClientTickEvent.Post event) {
        Agent.I.tick();
    }
}

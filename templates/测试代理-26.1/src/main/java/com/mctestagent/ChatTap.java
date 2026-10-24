package com.mctestagent;

import net.neoforged.api.distmarker.Dist;
import net.neoforged.bus.api.SubscribeEvent;
import net.neoforged.fml.common.EventBusSubscriber;
import net.neoforged.neoforge.client.event.ClientChatReceivedEvent;

/**
 * 把游戏聊天/命令回显也收进代理的缓冲区。
 * 命令有没有报错（Unknown item / Test failed / 语法错）就从这里看，
 * 比每次都去翻日志快得多。
 */
@EventBusSubscriber(modid = TestAgent.MODID, value = Dist.CLIENT)
public final class ChatTap {
    private ChatTap() {
    }

    @SubscribeEvent
    static void onChat(ClientChatReceivedEvent event) {
        try {
            Agent.I.addChat(event.getMessage().getString());
        } catch (Throwable ignored) {
        }
    }
}

package com.mctestagent;

import com.mojang.blaze3d.platform.InputConstants;
import net.minecraft.client.KeyMapping;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.screens.inventory.AbstractContainerScreen;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.inventory.ContainerInput;
import net.minecraft.world.inventory.Slot;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.EntityHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import java.util.ArrayList;
import java.util.List;

/**
 * 玩家输入层 —— **真的按下去**，而不是调命令绕过。
 *
 * <p>为什么要这层：`/setblock` 能放方块，但它走的是服务端命令，**不经过** mod 的
 * `useItemOn` / `BlockEvent` 处理链。要验「我做的方块右键有没有反应」「我的按键开了面板没有」
 * 「我的界面能不能把物品塞进去」，就必须走玩家输入这条路。
 *
 * <p>三条路径，各有讲究：
 * <ul>
 *   <li><b>移动 / 跳 / 潜行 / 疾跑 / 快捷键 / 开背包</b>：直接改 {@code KeyMapping} 的按下状态 ——
 *       原版每 tick 自己读 {@code isDown()}，等于真按键。</li>
 *   <li><b>右键 / 左键单击</b>：复刻 {@code Minecraft.startUseItem()} / {@code startAttack()} 的
 *       公开 API 调用链（那两个方法本身是包私有的，跨包调不到）。</li>
 *   <li><b>持续挖掘</b>：原版 {@code continueAttack} 需要 {@code mouseHandler.isMouseGrabbed()}
 *       ——**窗口一失焦就是 false**，所以「按住左键挖」在无人值守时根本不跑。
 *       这里直接走 {@code MultiPlayerGameMode.continueDestroyBlock()}，不依赖鼠标抓取。</li>
 * </ul>
 */
final class Input {
    private Input() {
    }

    // ── 按键映射 ────────────────────────────────────────────────────────────

    /** 按翻译键（如 {@code key.forward}）找按键映射。 */
    static KeyMapping 找映射(Minecraft mc, String 翻译键) {
        for (KeyMapping k : mc.options.keyMappings) {
            if (k.getName().equals(翻译键)) return k;
        }
        return null;
    }

    /**
     * 取「当前绑定键」。
     * 26.1 的公开 API 只有 {@code getDefaultKey()}（默认键），改过键位就不准了 ——
     * 所以先反射试 {@code getKey()}（老版本有、26.1 是包私有），拿不到再退回默认键。
     */
    static InputConstants.Key 绑定键(KeyMapping km) {
        try {
            var m = KeyMapping.class.getMethod("getKey");
            m.setAccessible(true);
            Object v = m.invoke(km);
            if (v instanceof InputConstants.Key k) return k;
        } catch (Throwable ignored) {
        }
        return km.getDefaultKey();
    }

    /** 按下 / 松开一组翻译键。返回没找到的键名。 */
    static List<String> 按键(Minecraft mc, List<String> 翻译键, boolean 按下) {
        List<String> 缺 = new ArrayList<>();
        for (String n : 翻译键) {
            KeyMapping km = n.startsWith("key.") ? 找映射(mc, n) : 找映射(mc, "key." + n);
            if (km == null) {
                缺.add(n);
                continue;
            }
            InputConstants.Key k = 绑定键(km);
            if (k == null) {
                缺.add(n);
                continue;
            }
            KeyMapping.set(k, 按下);
        }
        KeyMapping.setAll();
        return 缺;
    }

    /** 全部松开（动作结束时的兜底，避免键卡住导致"一直在走"）。 */
    static void 全松() {
        try {
            KeyMapping.releaseAll();
            KeyMapping.setAll();
        } catch (Throwable ignored) {
        }
    }

    // ── 交互 ────────────────────────────────────────────────────────────────

    /** 复刻 Minecraft.startUseItem()：右键一次。返回一句话说明走了哪条分支。 */
    static String 一次使用(Minecraft mc) {
        LocalPlayer p = mc.player;
        if (p == null) return "没进世界";
        if (p.isHandsBusy()) return "双手正忙（isHandsBusy）";
        InteractionHand hand = InteractionHand.MAIN_HAND;
        HitResult hit = mc.hitResult;
        if (hit != null && hit.getType() == HitResult.Type.ENTITY) {
            EntityHitResult eh = (EntityHitResult) hit;
            Entity e = eh.getEntity();
            InteractionResult r = mc.gameMode.interact(p, e, eh, hand);
            if (r instanceof InteractionResult.Success s) {
                if (s.swingSource() == InteractionResult.SwingSource.CLIENT) p.swing(hand);
                return "实体交互成功：" + BuiltInRegistries.ENTITY_TYPE.getKey(e.getType());
            }
            // 没消费 → 落到"用物品"
        } else if (hit != null && hit.getType() == HitResult.Type.BLOCK) {
            BlockHitResult bh = (BlockHitResult) hit;
            InteractionResult r = mc.gameMode.useItemOn(p, hand, bh);
            if (r instanceof InteractionResult.Success s) {
                if (s.swingSource() == InteractionResult.SwingSource.CLIENT) p.swing(hand);
                BlockPos bp = bh.getBlockPos();
                return "对方块使用成功：" + 方块名(mc, bp) + " 面=" + bh.getDirection().getName();
            }
        }
        InteractionResult r = mc.gameMode.useItem(p, hand);
        return "使用物品：" + r + "，手持=" + 物品名(p.getMainHandItem());
    }

    /**
     * 左键一次。复刻 startAttack()：打实体优先，其次挖方块。
     * 返回 null 表示没打中任何东西。
     */
    static String 一次攻击(Minecraft mc) {
        LocalPlayer p = mc.player;
        if (p == null) return "没进世界";
        HitResult hit = mc.hitResult;
        if (hit == null) return null;
        if (hit.getType() == HitResult.Type.ENTITY) {
            EntityHitResult eh = (EntityHitResult) hit;
            Entity e = eh.getEntity();
            double d = Math.sqrt(e.distanceToSqr(p.getEyePosition()));
            if (d > 4.0) return "目标太远（" + String.format("%.2f", d) + " 格，上限约 3~4）";
            mc.gameMode.attack(p, e);
            p.swing(InteractionHand.MAIN_HAND);
            return "攻击实体 " + BuiltInRegistries.ENTITY_TYPE.getKey(e.getType());
        }
        if (hit.getType() == HitResult.Type.BLOCK) {
            BlockHitResult bh = (BlockHitResult) hit;
            if (!mc.level.getBlockState(bh.getBlockPos()).isAir()) {
                mc.gameMode.startDestroyBlock(bh.getBlockPos(), bh.getDirection());
                p.swing(InteractionHand.MAIN_HAND);
                return "开始破坏 " + 方块名(mc, bh.getBlockPos());
            }
        }
        return null;
    }

    /** 持续挖掘一步（不依赖鼠标抓取）。返回非 null 表示可以收尾了。 */
    static String 挖掘一步(Minecraft mc, MineState st) {
        LocalPlayer p = mc.player;
        if (p == null) return "没进世界";
        HitResult hit = mc.hitResult;
        if (hit == null || hit.getType() != HitResult.Type.BLOCK) {
            if (st.起点方块 != null) {
                mc.gameMode.stopDestroyBlock();
                return "准星离开方块了";
            }
            return "准星没对着任何方块（用 look/look_at 先瞄准）";
        }
        BlockHitResult bh = (BlockHitResult) hit;
        BlockPos pos = bh.getBlockPos();
        Direction dir = bh.getDirection();
        BlockState state = mc.level.getBlockState(pos);
        if (st.起点方块 == null) {
            st.起点方块 = 方块名(mc, pos);
            st.目标 = pos.immutable();
            mc.gameMode.startDestroyBlock(pos, dir);
            p.swing(InteractionHand.MAIN_HAND);
            if (mc.level.getBlockState(pos).isAir()) return null;   // 创造模式秒破
            return null;
        }
        if (state.isAir()) {
            mc.gameMode.stopDestroyBlock();
            return null;
        }
        mc.gameMode.continueDestroyBlock(pos, dir);
        p.swing(InteractionHand.MAIN_HAND);
        return null;
    }

    static final class MineState {
        String 起点方块;
        BlockPos 目标;
        int 步数;
    }

    // ── 瞄准 ────────────────────────────────────────────────────────────────

    /** 把朝向设到能看向一个世界坐标点。 */
    static void 看向(Minecraft mc, Vec3 点) {
        LocalPlayer p = mc.player;
        Vec3 眼 = p.getEyePosition();
        double dx = 点.x - 眼.x;
        double dy = 点.y - 眼.y;
        double dz = 点.z - 眼.z;
        double 水平 = Math.sqrt(dx * dx + dz * dz);
        float yaw = (float) (Math.toDegrees(Math.atan2(dz, dx)) - 90.0);
        float pitch = (float) (-Math.toDegrees(Math.atan2(dy, 水平)));
        p.setYRot(yaw);
        p.setXRot(pitch);
    }

    /** 坐标最好落在这个方块上：返回 {邻块, 面}，面从邻块指向目标。 */
    static BlockPos 找贴靠块(Minecraft mc, BlockPos 目标) {
        LocalPlayer p = mc.player;
        Vec3 眼 = p.getEyePosition();
        BlockPos best = null;
        double bestD = Double.MAX_VALUE;
        for (Direction d : Direction.values()) {
            BlockPos n = 目标.relative(d);
            BlockState st = mc.level.getBlockState(n);
            if (st.isAir() || !st.getFluidState().isEmpty()) continue;
            double dist = Vec3.atCenterOf(n).distanceToSqr(眼);
            if (dist < bestD) {
                bestD = dist;
                best = n;
            }
        }
        return best;
    }

    static Vec3 瞄准点(BlockPos 邻块, BlockPos 目标) {
        Direction 面 = null;
        for (Direction d : Direction.values()) {
            if (邻块.relative(d).equals(目标)) {
                面 = d;
                break;
            }
        }
        Vec3 c = Vec3.atCenterOf(邻块);
        return 面 == null ? c : c.add(Vec3.atLowerCornerOf(面.getUnitVec3i()).scale(0.5));
    }

    // ── 容器 ────────────────────────────────────────────────────────────────

    /** 当前打开的容器界面快照；没开容器界面返回 null。 */
    static JsonObject 容器快照(Minecraft mc) {
        if (!(mc.screen instanceof AbstractContainerScreen<?> acs)) return null;
        JsonObject o = new JsonObject();
        o.addProperty("screen", acs.getClass().getName());
        o.addProperty("containerId", acs.getMenu().containerId);
        o.addProperty("guiLeft", acs.getGuiLeft());
        o.addProperty("guiTop", acs.getGuiTop());
        o.addProperty("xSize", acs.getXSize());
        o.addProperty("ySize", acs.getYSize());
        o.add("carried", Observe.stack(acs.getMenu().getCarried()));
        JsonArray slots = new JsonArray();
        List<Slot> list = acs.getMenu().slots;
        for (int i = 0; i < list.size(); i++) {
            Slot s = list.get(i);
            JsonObject e = new JsonObject();
            e.addProperty("index", i);
            e.addProperty("x", acs.getGuiLeft() + s.x);
            e.addProperty("y", acs.getGuiTop() + s.y);
            e.addProperty("slotX", s.x);
            e.addProperty("slotY", s.y);
            e.addProperty("container", s.container.getClass().getSimpleName());
            e.add("item", Observe.stack(s.getItem()));
            slots.add(e);
        }
        o.add("slots", slots);
        return o;
    }

    /** 点容器里的一个槽位 —— 走的就是真实点击那条路（屏幕的 slotClicked 内部也是调它）。 */
    static String 点槽位(Minecraft mc, int index, int button, boolean shift) {
        if (!(mc.screen instanceof AbstractContainerScreen<?> acs)) return "当前没有打开容器界面";
        List<Slot> list = acs.getMenu().slots;
        if (index < 0 || index >= list.size()) return "槽位序号越界（0~" + (list.size() - 1) + "）";
        ContainerInput input = shift ? ContainerInput.QUICK_MOVE : ContainerInput.PICKUP;
        mc.gameMode.handleContainerInput(acs.getMenu().containerId, index, button, input, mc.player);
        return null;
    }

    static String 方块名(Minecraft mc, BlockPos pos) {
        if (mc.level == null) return "?";
        return BuiltInRegistries.BLOCK.getKey(mc.level.getBlockState(pos).getBlock()).toString();
    }

    static String 物品名(ItemStack s) {
        return s == null || s.isEmpty() ? "空手" : BuiltInRegistries.ITEM.getKey(s.getItem()).toString();
    }
}

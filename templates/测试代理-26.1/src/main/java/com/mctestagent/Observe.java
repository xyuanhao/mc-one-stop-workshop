package com.mctestagent;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.EquipmentSlot;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.player.Inventory;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.HitResult;

/** 状态观测：把客户端能看到的东西整理成 JSON，交给外部脚本判断。 */
final class Observe {
    private static final int MAX_ENTITIES = 200;
    private static final int MAX_BLOCKS = 4000;

    private Observe() {
    }

    static JsonObject full(Minecraft mc, int radius, boolean withBlocks) {
        JsonObject o = new JsonObject();
        o.add("client", client(mc));
        o.add("screen", Ui.dump(mc.screen));
        LocalPlayer p = mc.player;
        if (p == null || mc.level == null) {
            o.addProperty("inWorld", false);
            return o;
        }
        o.addProperty("inWorld", true);
        o.add("player", player(mc, p));
        o.add("world", world(mc));
        o.add("inventory", inventory(p));
        if (radius > 0) o.add("entities", entities(mc, radius));
        if (withBlocks) o.add("blocks", blocks(mc, p.blockPosition(), radius > 0 ? radius : 4));
        return o;
    }

    static JsonObject client(Minecraft mc) {
        JsonObject o = new JsonObject();
        o.addProperty("tick", Agent.I.tickCount());
        o.addProperty("screenClass", mc.screen == null ? "" : mc.screen.getClass().getName());
        o.addProperty("running", mc.isRunning());
        o.addProperty("pauseOnLostFocus", mc.options.pauseOnLostFocus);
        o.addProperty("hideGui", mc.options.hideGui);
        try {
            o.addProperty("windowWidth", mc.getWindow().getWidth());
            o.addProperty("windowHeight", mc.getWindow().getHeight());
        } catch (Throwable ignored) {
        }
        try {
            o.addProperty("fps", mc.getFps());
        } catch (Throwable ignored) {
        }
        return o;
    }

    static JsonObject player(Minecraft mc, LocalPlayer p) {
        JsonObject o = new JsonObject();
        BlockPos bp = p.blockPosition();
        o.addProperty("pos", String.format("%.3f,%.3f,%.3f", p.getX(), p.getY(), p.getZ()));
        o.addProperty("blockPos", bp.getX() + "," + bp.getY() + "," + bp.getZ());
        o.addProperty("yaw", p.getYRot());
        o.addProperty("pitch", p.getXRot());
        o.addProperty("health", p.getHealth());
        o.addProperty("maxHealth", p.getMaxHealth());
        o.addProperty("food", p.getFoodData().getFoodLevel());
        o.addProperty("saturation", p.getFoodData().getSaturationLevel());
        o.addProperty("xpLevel", p.experienceLevel);
        o.addProperty("air", p.getAirSupply());
        o.addProperty("onGround", p.onGround());
        o.addProperty("sneaking", p.isShiftKeyDown());
        o.addProperty("sprinting", p.isSprinting());
        o.addProperty("flying", p.getAbilities().flying);
        o.addProperty("creative", p.isCreative());
        o.addProperty("spectator", p.isSpectator());
        o.addProperty("usingItem", p.isUsingItem());
        o.addProperty("gameMode", mc.gameMode == null ? "" : String.valueOf(mc.gameMode.getPlayerMode()));
        o.addProperty("dimension", p.level().dimension().identifier().toString());
        o.addProperty("alive", p.isAlive());
        o.addProperty("effectsRaw", String.valueOf(p.getActiveEffects()));
        o.add("crosshair", crosshair(mc));
        return o;
    }

    /** 准星指着什么 —— 做 place / mine / use 之前先看这个，比猜快得多。 */
    static JsonObject crosshair(Minecraft mc) {
        JsonObject o = new JsonObject();
        HitResult hit = mc.hitResult;
        if (hit == null) {
            o.addProperty("type", "none");
            return o;
        }
        o.addProperty("type", String.valueOf(hit.getType()));
        if (hit.getType() == HitResult.Type.BLOCK) {
            BlockHitResult b = (BlockHitResult) hit;
            BlockPos bp = b.getBlockPos();
            o.addProperty("pos", bp.getX() + "," + bp.getY() + "," + bp.getZ());
            o.addProperty("face", b.getDirection().getName());
            o.addProperty("block", mc.level == null ? "?" : BuiltInRegistries.BLOCK.getKey(mc.level.getBlockState(bp).getBlock()).toString());
        }
        return o;
    }

    static JsonObject world(Minecraft mc) {
        JsonObject o = new JsonObject();
        try {
            o.addProperty("gameTime", mc.level.getGameTime());
            // ⚠️ 26.1 没有 ClientLevel.getDayTime()（LevelData 里也没有 dayTime 了）。
            // 用 gameTime % 24000 作近似；要精确值就让脚本跑 /time query daytime 读回显。
            o.addProperty("timeOfDayApprox", Math.floorMod(mc.level.getGameTime(), 24000L));
            o.addProperty("raining", mc.level.isRaining());
            o.addProperty("thundering", mc.level.isThundering());
            o.addProperty("difficulty", String.valueOf(mc.level.getDifficulty()));
            o.addProperty("dimension", mc.level.dimension().identifier().toString());
        } catch (Throwable t) {
            o.addProperty("error", "读世界状态失败：" + t);
        }
        return o;
    }

    static JsonObject inventory(LocalPlayer p) {
        if (p == null) return null;
        JsonObject o = new JsonObject();
        Inventory inv = p.getInventory();
        JsonArray slots = new JsonArray();
        for (int i = 0; i < inv.getContainerSize(); i++) {
            ItemStack s = inv.getItem(i);
            if (s.isEmpty()) continue;
            JsonObject e = new JsonObject();
            e.addProperty("slot", i);
            e.addProperty("item", BuiltInRegistries.ITEM.getKey(s.getItem()).toString());
            e.addProperty("count", s.getCount());
            e.addProperty("name", s.getHoverName().getString());
            slots.add(e);
        }
        o.add("slots", slots);
        o.addProperty("selectedSlot", inv.getSelectedSlot());
        o.add("selected", stack(inv.getSelectedItem()));
        o.add("offhand", stack(p.getOffhandItem()));
        JsonObject armor = new JsonObject();
        for (EquipmentSlot slot : new EquipmentSlot[]{EquipmentSlot.HEAD, EquipmentSlot.CHEST, EquipmentSlot.LEGS, EquipmentSlot.FEET}) {
            armor.add(slot.getName(), stack(p.getItemBySlot(slot)));
        }
        o.add("armor", armor);
        return o;
    }

    static JsonObject stack(ItemStack s) {
        JsonObject o = new JsonObject();
        if (s == null || s.isEmpty()) {
            o.addProperty("empty", true);
            return o;
        }
        o.addProperty("empty", false);
        o.addProperty("item", BuiltInRegistries.ITEM.getKey(s.getItem()).toString());
        o.addProperty("count", s.getCount());
        o.addProperty("name", s.getHoverName().getString());
        return o;
    }

    static JsonArray entities(Minecraft mc, int radius) {
        JsonArray arr = new JsonArray();
        if (mc.player == null || mc.level == null) return arr;
        AABB box = mc.player.getBoundingBox().inflate(radius);
        int n = 0;
        for (Entity e : mc.level.getEntities(mc.player, box)) {
            if (n++ >= MAX_ENTITIES) break;
            JsonObject o = new JsonObject();
            o.addProperty("type", BuiltInRegistries.ENTITY_TYPE.getKey(e.getType()).toString());
            o.addProperty("uuid", e.getUUID().toString());
            o.addProperty("pos", String.format("%.2f,%.2f,%.2f", e.getX(), e.getY(), e.getZ()));
            o.addProperty("distance", Math.sqrt(e.distanceToSqr(mc.player)));
            if (e instanceof LivingEntity le) {
                o.addProperty("health", le.getHealth());
                o.addProperty("maxHealth", le.getMaxHealth());
            }
            if (e.getCustomName() != null) o.addProperty("name", e.getCustomName().getString());
            arr.add(o);
        }
        return arr;
    }

    static JsonObject blocks(Minecraft mc, BlockPos center, int radius) {
        JsonObject o = new JsonObject();
        if (mc.level == null) return o;
        int n = 0;
        int air = 0;
        for (int dx = -radius; dx <= radius; dx++) {
            for (int dy = -radius; dy <= radius; dy++) {
                for (int dz = -radius; dz <= radius; dz++) {
                    BlockPos p = center.offset(dx, dy, dz);
                    BlockState st = mc.level.getBlockState(p);
                    String id = BuiltInRegistries.BLOCK.getKey(st.getBlock()).toString();
                    if (id.equals("minecraft:air") || id.equals("minecraft:cave_air") || id.equals("minecraft:void_air")) {
                        air++;
                        continue;
                    }
                    if (n++ >= MAX_BLOCKS) continue;
                    o.addProperty(p.getX() + "," + p.getY() + "," + p.getZ(), id);
                }
            }
        }
        JsonObject meta = new JsonObject();
        meta.addProperty("center", center.getX() + "," + center.getY() + "," + center.getZ());
        meta.addProperty("radius", radius);
        meta.addProperty("nonAir", n);
        meta.addProperty("air", air);
        o.add("_meta", meta);
        return o;
    }

    /** 读单个方块。 */
    static String blockAt(Minecraft mc, BlockPos p) {
        if (mc.level == null) return null;
        return BuiltInRegistries.BLOCK.getKey(mc.level.getBlockState(p).getBlock()).toString();
    }
}

package com.mctestagent;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.mojang.blaze3d.platform.InputConstants;
import net.minecraft.client.KeyMapping;
import net.minecraft.client.Minecraft;
import net.minecraft.client.Screenshot;
import net.minecraft.client.gui.components.AbstractWidget;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.gui.screens.worldselection.CreateWorldScreen;
import net.minecraft.client.gui.screens.worldselection.WorldCreationUiState;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Holder;
import net.minecraft.core.registries.Registries;
import net.minecraft.network.chat.Component;
import net.minecraft.resources.ResourceKey;
import net.minecraft.world.level.levelgen.presets.WorldPreset;
import net.minecraft.world.level.levelgen.presets.WorldPresets;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;

import java.io.File;
import java.lang.reflect.Constructor;
import java.util.ArrayList;
import java.util.List;
import java.util.regex.Pattern;

/** 所有动作的实现，以及「一行 JSON 命令 → 动作」的工厂。 */
final class Actions {
    private Actions() {
    }

    static Action create(JsonObject c) {
        long id = c.has("id") ? c.get("id").getAsLong() : 0L;
        String op = c.has("op") ? c.get("op").getAsString() : "";
        switch (op) {
            case "cmd":
                return new CmdAction(id, str(c, "command", ""));
            case "wait":
                return new WaitAction(id, num(c, "ticks", 20));
            case "wait_for":
                return new WaitForAction(id, str(c, "what", "world"), str(c, "value", ""), num(c, "timeout", 600));
            case "shot":
                return new ShotAction(id, str(c, "name", "shot"), num(c, "downscale", 1));
            case "key":
                return new KeyAction(id, str(c, "mapping", str(c, "key", "")), str(c, "state", "tap"));
            case "screen":
                return new ScreenAction(id, str(c, "action", "dump"), str(c, "value", ""),
                        dbl(c, "x", 0), dbl(c, "y", 0), num(c, "button", 0));
            case "state":
                return new StateAction(id, num(c, "radius", 0), bool(c, "withBlocks", false));
            case "block":
                return new BlockAction(id, str(c, "pos", ""));
            case "entities":
                return new EntitiesAction(id, num(c, "radius", 16));
            case "chat":
                return new ChatAction(id, str(c, "pattern", ""), bool(c, "all", false));
            case "log":
                return new LogAction(id, num(c, "lines", 100), str(c, "pattern", ""), num(c, "fromLine", 0));
            case "quit":
                return new QuitAction(id, num(c, "code", 0), str(c, "reason", "脚本要求退出"));
            // ── 玩家输入层（真的按下去，而不是发命令绕过）──────────────
            case "move":
                return new HoldAction(id, "move", 移动键(c), num(c, "ticks", 20));
            case "jump":
                return new HoldAction(id, "jump", List.of("key.jump"), num(c, "ticks", 1));
            case "look":
                return new LookAction(id, optDbl(c, "yaw"), optDbl(c, "pitch"), str(c, "target", ""));
            case "use":
                return new UseAction(id, num(c, "ticks", 1));
            case "mine":
                return new MineAction(id, str(c, "pos", ""), num(c, "ticks", 200));
            case "place":
                return new PlaceAction(id, str(c, "pos", ""));
            case "hotbar":
                return new KeyClickAction(id, "hotbar", "key.hotbar." + Math.max(1, Math.min(9, num(c, "slot", 1))));
            case "drop":
                return new KeyClickAction(id, "drop", "key.drop");
            case "open_inventory":
                return new OpenInventoryAction(id);
            case "swap_hands":
                return new KeyClickAction(id, "swap_hands", "key.swapOffhand");
            case "container":
                return new ContainerAction(id, str(c, "action", "dump"), num(c, "slot", -1),
                        num(c, "button", 0), bool(c, "shift", false));
            case "say":
                return new SayAction(id, str(c, "text", ""));
            default:
                return new FailAction(id, op);
        }
    }

    // ── 工具 ────────────────────────────────────────────────────────────────

    static String str(JsonObject o, String k, String def) {
        return o != null && o.has(k) && !o.get(k).isJsonNull() ? o.get(k).getAsString() : def;
    }

    static int num(JsonObject o, String k, int def) {
        return o != null && o.has(k) && !o.get(k).isJsonNull() ? o.get(k).getAsInt() : def;
    }

    static double dbl(JsonObject o, String k, double def) {
        return o != null && o.has(k) && !o.get(k).isJsonNull() ? o.get(k).getAsDouble() : def;
    }

    static boolean bool(JsonObject o, String k, boolean def) {
        return o != null && o.has(k) && !o.get(k).isJsonNull() ? o.get(k).getAsBoolean() : def;
    }

    static Double optDbl(JsonObject o, String k) {
        return o != null && o.has(k) && !o.get(k).isJsonNull() ? o.get(k).getAsDouble() : null;
    }

    /** move 的按键组合：方向 + 可选的疾跑/潜行。 */
    static List<String> 移动键(JsonObject c) {
        List<String> keys = new ArrayList<>();
        keys.add(switch (str(c, "direction", "forward")) {
            case "back", "backward" -> "key.back";
            case "left" -> "key.left";
            case "right" -> "key.right";
            case "forward" -> "key.forward";
            default -> "key." + str(c, "direction", "forward");
        });
        if (bool(c, "sprint", false)) keys.add("key.sprint");
        if (bool(c, "sneak", false)) keys.add("key.sneak");
        return keys;
    }

    static String 位置(Minecraft mc) {
        if (mc.player == null) return "";
        return String.format("%.3f,%.3f,%.3f", mc.player.getX(), mc.player.getY(), mc.player.getZ());
    }

    static String 描述命中(HitResult hit) {
        if (hit == null) return "无命中（hitResult=null）";
        if (hit.getType() == HitResult.Type.BLOCK) {
            BlockHitResult b = (BlockHitResult) hit;
            return "方块 " + b.getBlockPos().getX() + "," + b.getBlockPos().getY() + "," + b.getBlockPos().getZ()
                    + " 面=" + b.getDirection().getName();
        }
        if (hit.getType() == HitResult.Type.ENTITY) return "实体";
        return "miss（对准空气）";
    }

    static BlockPos parsePos(String s) {
        String[] p = s.split("[, ]+");
        if (p.length < 3) return null;
        try {
            return new BlockPos(Integer.parseInt(p[0]), Integer.parseInt(p[1]), Integer.parseInt(p[2]));
        } catch (NumberFormatException e) {
            return null;
        }
    }

    static String describe(Throwable t) {
        StringBuilder sb = new StringBuilder(String.valueOf(t));
        StackTraceElement[] st = t.getStackTrace();
        for (int i = 0; i < Math.min(4, st.length); i++) sb.append(" | ").append(st[i]);
        return sb.toString();
    }

    // ── 动作 ────────────────────────────────────────────────────────────────

    /** 启动流程：等主菜单 → 应用选项 → 建/开世界 → 落到世界里。 */
    static final class BootAction extends Action {
        private int step;
        private JsonObject cfg = new JsonObject();
        private String worldName = "mctestagent_world";
        private String targetFolder = "";
        private int settle;

        BootAction() {
            super(0, "boot");
            timeoutTicks = 12000;   // 10 分钟：世界首次生成可能很慢
        }

        @Override
        JsonObject tick(Minecraft mc) {
            switch (step) {
                case 0: {
                    Screen s = mc.screen;
                    if (s == null) return null;
                    String cn = s.getClass().getName();
                    if (cn.contains("Loading") || cn.contains("Progress")) return null;
                    JsonObject read = Io.readJson(Io.BOOT);
                    if (read != null) cfg = read;
                    applyOptions(mc);
                    Io.writeText(Io.READY, "tick=" + Agent.I.tickCount() + "\nscreen=" + cn + "\n");
                    Io.warn("客户端已就绪，当前界面 " + cn);
                    step = 1;
                    return null;
                }
                case 1: {
                    if (clickOnboarding(mc)) return null;
                    JsonObject w = cfg.has("world") ? cfg.getAsJsonObject("world") : null;
                    String mode = str(w, "mode", "none");
                    if ("none".equals(mode)) {
                        step = 9;
                        return null;
                    }
                    worldName = str(w, "name", "mctestagent_world");
                    if ("open".equals(mode)) {
                        Io.warn("打开已有世界 " + worldName);
                        mc.createWorldOpenFlows().openWorld(worldName, () -> Io.warn("打开世界被取消"));
                        step = 5;
                        return null;
                    }
                    Io.warn("开始创建世界 " + worldName);
                    Screen old = mc.screen;
                    CreateWorldScreen.openFresh(mc, () -> mc.setScreen(old));
                    step = 2;
                    return null;
                }
                case 2: {
                    if (!(mc.screen instanceof CreateWorldScreen cws)) return null;
                    JsonObject w = cfg.getAsJsonObject("world");
                    WorldCreationUiState ui = cws.getUiState();
                    ui.setName(worldName);
                    if (w.has("seed")) ui.setSeed(w.get("seed").getAsString());
                    ui.setGenerateStructures(bool(w, "structures", false));
                    Holder<WorldPreset> holder = ui.getSettings().worldgenLoadContext()
                            .lookupOrThrow(Registries.WORLD_PRESET)
                            .getOrThrow(presetKey(str(w, "preset", "flat")));
                    ui.setWorldType(new WorldCreationUiState.WorldTypeEntry(holder));
                    ui.setGameMode(gameMode(str(w, "gamemode", "creative")));
                    ui.setAllowCommands(bool(w, "cheats", true));
                    targetFolder = ui.getTargetFolder();
                    Io.warn("世界参数已设置（预设 " + str(w, "preset", "flat") + "，目录 " + targetFolder + "）");
                    step = 3;
                    return null;
                }
                case 3: {
                    Screen s = mc.screen;
                    if (!(s instanceof CreateWorldScreen)) return null;
                    AbstractWidget btn = Ui.find(s, "selectWorld.create");
                    if (btn == null) {
                        JsonObject d = Ui.dump(s);
                        return fail("创建世界界面上找不到「创建」按钮，当前控件：" + d.get("widgets"));
                    }
                    Ui.click(btn, 0);
                    Io.warn("已点「创建世界」");
                    step = 5;
                    return null;
                }
                case 5: {
                    if (mc.level == null || mc.player == null || mc.getConnection() == null) return null;
                    if (settle++ < num(cfg, "settleTicks", 20)) return null;
                    JsonObject d = new JsonObject();
                    d.addProperty("world", worldName);
                    d.addProperty("folder", targetFolder);
                    d.addProperty("dimension", mc.level.dimension().identifier().toString());
                    d.addProperty("pos", String.format("%.2f,%.2f,%.2f", mc.player.getX(), mc.player.getY(), mc.player.getZ()));
                    Agent.I.onBootDone();
                    return ok(d);
                }
                case 9: {
                    Agent.I.onBootDone();
                    JsonObject d = new JsonObject();
                    d.addProperty("world", "");
                    return ok(d);
                }
                default:
                    return fail("启动流程状态异常：" + step);
            }
        }

        /**
         * 无障碍引导界面：只点「继续」，点不到就报出来让人看，绝不乱点。
         *
         * <p>⚠️ 必须有冷却：点完的下一 tick 界面**还没**切走，无脑重试会在日志里刷出一串
         * 「点掉引导界面按钮」（实测连点 4 次）。冷却 = 记住上次点击 tick，隔一段时间才允许再点。
         */
        private int 上次点击tick = -1000;

        private boolean clickOnboarding(Minecraft mc) {
            Screen s = mc.screen;
            if (s == null) return false;
            String cn = s.getClass().getSimpleName();
            if (!cn.contains("Onboarding") && !cn.contains("Accessibility")) return false;
            if (Agent.I.tickCount() - 上次点击tick < 40) return true;   // 还在冷却，等界面自己切走
            for (String k : new String[]{"gui.continue", "gui.ok", "gui.acknowledge", "gui.proceed"}) {
                AbstractWidget w = Ui.find(s, k);
                if (w != null) {
                    Io.warn("点掉引导界面按钮 " + k + "（界面 " + cn + "）");
                    Ui.click(w, 0);
                    上次点击tick = Agent.I.tickCount();
                    return true;
                }
            }
            Io.warn("引导界面 " + cn + " 上找不到「继续」按钮，控件：" + Ui.dump(s).get("widgets"));
            上次点击tick = Agent.I.tickCount();
            return true;
        }

        private void applyOptions(Minecraft mc) {
            JsonObject o = cfg.has("options") ? cfg.getAsJsonObject("options") : new JsonObject();
            try {
                // 关键：不抢焦点也要继续跑。默认 true 时，窗口一失去焦点单人世界就自动暂停，
                // 整个自动化会"看起来卡住"，而且日志里什么都不报。
                mc.options.pauseOnLostFocus = bool(o, "pauseOnLostFocus", false);
                mc.options.onboardAccessibility = false;
                if (o.has("guiScale")) mc.options.guiScale().set(o.get("guiScale").getAsInt());
                if (o.has("renderDistance")) mc.options.renderDistance().set(o.get("renderDistance").getAsInt());
                if (o.has("hideGui")) mc.options.hideGui = o.get("hideGui").getAsBoolean();
                mc.options.save();
            } catch (Throwable t) {
                Io.warn("应用选项失败：" + t);
            }
        }

        private static ResourceKey<WorldPreset> presetKey(String name) {
            return switch (name) {
                case "normal" -> WorldPresets.NORMAL;
                case "large_biomes" -> WorldPresets.LARGE_BIOMES;
                case "amplified" -> WorldPresets.AMPLIFIED;
                case "single_biome_surface" -> WorldPresets.SINGLE_BIOME_SURFACE;
                case "debug" -> WorldPresets.DEBUG;
                default -> WorldPresets.FLAT;
            };
        }

        private static WorldCreationUiState.SelectedGameMode gameMode(String name) {
            // 注意：SURVIVAL / CREATIVE / ... 这几个常量挂在**嵌套类** SelectedGameMode 上，
            // 不在 WorldCreationUiState 上（26.1 实测，写错会编译不过）。
            return switch (name) {
                case "survival" -> WorldCreationUiState.SelectedGameMode.SURVIVAL;
                case "hardcore" -> WorldCreationUiState.SelectedGameMode.HARDCORE;
                case "debug" -> WorldCreationUiState.SelectedGameMode.DEBUG;
                default -> WorldCreationUiState.SelectedGameMode.CREATIVE;
            };
        }
    }

    /** 以玩家身份执行一条命令。 */
    static final class CmdAction extends Action {
        private final String command;
        private boolean sent;

        CmdAction(long id, String command) {
            super(id, "cmd");
            this.command = command.startsWith("/") ? command.substring(1) : command;
        }

        @Override
        JsonObject tick(Minecraft mc) {
            if (mc.player == null || mc.getConnection() == null) return fail("还没进世界，命令没法执行");
            if (!sent) {
                if (command.isEmpty()) return fail("command 为空");
                mc.getConnection().sendCommand(command);
                sent = true;
                return null;
            }
            return ok(null);
        }
    }

    static final class WaitAction extends Action {
        private final int ticks;

        WaitAction(long id, int ticks) {
            super(id, "wait");
            this.ticks = ticks;
            timeoutTicks = ticks + 200;
        }

        @Override
        JsonObject tick(Minecraft mc) {
            return elapsed() >= ticks ? ok(null) : null;
        }
    }

    /** 等一个条件成立：世界/界面/聊天/方块/tick。 */
    static final class WaitForAction extends Action {
        private final String what;
        private final String value;
        private final int fromChat;

        WaitForAction(long id, String what, String value, int timeout) {
            super(id, "wait_for");
            this.what = what;
            this.value = value;
            this.timeoutTicks = timeout;
            this.fromChat = Agent.I.chatSize();
        }

        @Override
        JsonObject tick(Minecraft mc) {
            JsonObject d = new JsonObject();
            switch (what) {
                case "world", "ingame" -> {
                    if (mc.level != null && mc.player != null && mc.getConnection() != null) {
                        d.addProperty("what", what);
                        return ok(d);
                    }
                    return null;
                }
                case "no_screen" -> {
                    if (mc.screen == null) {
                        d.addProperty("what", what);
                        return ok(d);
                    }
                    return null;
                }
                case "screen" -> {
                    Screen s = mc.screen;
                    if (s == null) return null;
                    String cn = s.getClass().getName();
                    if (cn.equals(value) || cn.endsWith("." + value) || s.getClass().getSimpleName().equals(value)) {
                        d.addProperty("screen", cn);
                        return ok(d);
                    }
                    return null;
                }
                case "chat" -> {
                    String hit = Agent.I.findChat(value, fromChat);
                    if (hit != null) {
                        d.addProperty("line", hit);
                        return ok(d);
                    }
                    return null;
                }
                case "block" -> {
                    String[] kv = value.split("=", 2);
                    BlockPos p = parsePos(kv.length > 1 ? kv[0] : value);
                    if (p == null) return fail("block 的 value 要写成 \"x,y,z=minecraft:stone\"");
                    String cur = Observe.blockAt(mc, p);
                    if (kv.length > 1 && kv[1].equals(cur)) {
                        d.addProperty("block", cur);
                        return ok(d);
                    }
                    return null;
                }
                case "tick" -> {
                    if (Agent.I.tickCount() >= Integer.parseInt(value)) return ok(d);
                    return null;
                }
                default -> {
                    return fail("不认识的 what：" + what + "（可选 world/no_screen/screen/chat/block/tick）");
                }
            }
        }
    }

    /** 截图到 测试代理/截图/<name>.png。 */
    static final class ShotAction extends Action {
        private final String name;
        private final int downscale;
        private boolean requested;
        private volatile boolean written;
        private volatile String path = "";
        private volatile String err = "";

        ShotAction(long id, String name, int downscale) {
            super(id, "shot");
            this.name = name;
            this.downscale = Math.max(1, downscale);
        }

        @Override
        JsonObject tick(Minecraft mc) {
            if (!requested) {
                if (mc.screen != null || mc.level == null) {
                    // 主菜单也能截图，不拦
                }
                requested = true;
                try {
                    Screenshot.takeScreenshot(mc.getMainRenderTarget(), downscale, img -> {
                        try {
                            File f = Io.SHOTS.resolve(name.replaceAll("[^\\w\\u4e00-\\u9fff.-]", "_") + ".png").toFile();
                            f.getParentFile().mkdirs();
                            img.writeToFile(f);
                            path = f.getAbsolutePath();
                        } catch (Throwable t) {
                            err = describe(t);
                        } finally {
                            try {
                                img.close();
                            } catch (Throwable ignored) {
                            }
                            written = true;
                        }
                    });
                } catch (Throwable t) {
                    return fail("截图失败：" + describe(t));
                }
                return null;
            }
            if (!written) return null;
            if (!err.isEmpty()) return fail("截图写入失败：" + err);
            JsonObject d = new JsonObject();
            d.addProperty("path", path);
            d.addProperty("name", name);
            return ok(d);
        }
    }

    /** 按键：tap / down / up。mapping 传翻译键（如 key.jump）。 */
    static final class KeyAction extends Action {
        private final String mapping;
        private final String state;
        private boolean acted;

        KeyAction(long id, String mapping, String state) {
            super(id, "key");
            this.mapping = mapping;
            this.state = state;
        }

        @Override
        JsonObject tick(Minecraft mc) {
            if (!acted) {
                if (mapping.isEmpty()) return fail("mapping 不能为空");
                KeyMapping km = null;
                for (KeyMapping k : mc.options.keyMappings) {
                    if (k.getName().equals(mapping)) {
                        km = k;
                        break;
                    }
                }
                if (km == null) return fail("找不到按键映射 " + mapping);
                InputConstants.Key key = boundKey(km);
                if (key == null) return fail("按键映射 " + mapping + " 没有绑定键");
                switch (state) {
                    case "down" -> {
                        KeyMapping.set(key, true);
                        KeyMapping.setAll();
                    }
                    case "up" -> {
                        KeyMapping.set(key, false);
                        KeyMapping.setAll();
                    }
                    default -> KeyMapping.click(key);
                }
                acted = true;
                return null;
            }
            JsonObject d = new JsonObject();
            d.addProperty("mapping", mapping);
            d.addProperty("state", state);
            return ok(d);
        }

        /** 取「当前绑定键」；26.1 没有公开的 getKey()，能反射到就用，否则退回默认键。 */
        private static InputConstants.Key boundKey(KeyMapping km) {
            try {
                var m = KeyMapping.class.getMethod("getKey");
                m.setAccessible(true);
                Object v = m.invoke(km);
                if (v instanceof InputConstants.Key k) return k;
            } catch (Throwable ignored) {
            }
            return km.getDefaultKey();
        }
    }

    /** 界面操作：open / close / dump / click / click_at / type。 */
    static final class ScreenAction extends Action {
        private final String action;
        private final String value;
        private final double x;
        private final double y;
        private final int button;

        ScreenAction(long id, String action, String value, double x, double y, int button) {
            super(id, "screen");
            this.action = action;
            this.value = value;
            this.x = x;
            this.y = y;
            this.button = button;
        }

        @Override
        JsonObject tick(Minecraft mc) throws Exception {
            switch (action) {
                case "open" -> {
                    if (value.isEmpty()) return fail("open 需要给类名");
                    Class<?> c = Class.forName(value, true, mc.getClass().getClassLoader());
                    Screen s = instantiate(c, mc);
                    mc.setScreen(s);
                    JsonObject d = new JsonObject();
                    d.addProperty("opened", s.getClass().getName());
                    return ok(d);
                }
                case "close" -> {
                    mc.setScreen(null);
                    return ok(null);
                }
                case "dump" -> {
                    return ok(Ui.dump(mc.screen));
                }
                case "click" -> {
                    if (mc.screen == null) return fail("当前没有打开任何界面");
                    AbstractWidget w = Ui.find(mc.screen, value);
                    if (w == null) {
                        JsonObject d = Ui.dump(mc.screen);
                        return fail("界面上找不到「" + value + "」，现有控件：" + d.get("widgets"));
                    }
                    boolean hit = Ui.click(w, button);
                    if (!hit) return fail("点击「" + value + "」失败");
                    JsonObject d = Ui.dump(mc.screen);
                    d.addProperty("clicked", value);
                    return ok(d);
                }
                case "click_at" -> {
                    if (mc.screen == null) return fail("当前没有打开任何界面");
                    Ui.clickAt(mc.screen, x, y, button);
                    return ok(null);
                }
                case "type" -> {
                    if (mc.screen == null) return fail("当前没有打开任何界面");
                    return Ui.type(mc.screen, value) ? ok(null) : fail("界面上没有输入框");
                }
                default -> {
                    return fail("不认识的 screen action：" + action);
                }
            }
        }

        private static Screen instantiate(Class<?> c, Minecraft mc) throws Exception {
            Object[][] tries = new Object[][]{{mc.screen}, {}, {Component.empty()}};
            for (Object[] args : tries) {
                Class<?>[] sig = args.length == 1 && args[0] instanceof Screen
                        ? new Class<?>[]{Screen.class}
                        : (args.length == 1 ? new Class<?>[]{Component.class} : new Class<?>[0]);
                try {
                    Constructor<?> ctor = c.getDeclaredConstructor(sig);
                    ctor.setAccessible(true);
                    return (Screen) ctor.newInstance(args);
                } catch (NoSuchMethodException ignored) {
                }
            }
            throw new NoSuchMethodException(c.getClass().getName()
                    + " 没有 (Screen)/(Component)/() 构造器，代理开不了它 —— 改用按键触发（key 动作）");
        }
    }

    static final class StateAction extends Action {
        private final int radius;
        private final boolean withBlocks;

        StateAction(long id, int radius, boolean withBlocks) {
            super(id, "state");
            this.radius = radius;
            this.withBlocks = withBlocks;
        }

        @Override
        JsonObject tick(Minecraft mc) {
            return ok(Observe.full(mc, radius, withBlocks));
        }
    }

    static final class BlockAction extends Action {
        private final String pos;

        BlockAction(long id, String pos) {
            super(id, "block");
            this.pos = pos;
        }

        @Override
        JsonObject tick(Minecraft mc) {
            BlockPos p = parsePos(pos);
            if (p == null) return fail("pos 要写成 \"x,y,z\"");
            String id = Observe.blockAt(mc, p);
            if (id == null) return fail("还没进世界");
            JsonObject d = new JsonObject();
            d.addProperty("pos", p.getX() + "," + p.getY() + "," + p.getZ());
            d.addProperty("block", id);
            return ok(d);
        }
    }

    static final class EntitiesAction extends Action {
        private final int radius;

        EntitiesAction(long id, int radius) {
            super(id, "entities");
            this.radius = radius;
        }

        @Override
        JsonObject tick(Minecraft mc) {
            JsonObject d = new JsonObject();
            d.addProperty("radius", radius);
            d.add("entities", Observe.entities(mc, radius));
            return ok(d);
        }
    }

    static final class ChatAction extends Action {
        private final String pattern;
        private final boolean all;

        ChatAction(long id, String pattern, boolean all) {
            super(id, "chat");
            this.pattern = pattern;
            this.all = all;
        }

        @Override
        JsonObject tick(Minecraft mc) {
            JsonObject d = new JsonObject();
            List<String> lines = Agent.I.chatLines(pattern, all);
            JsonArray arr = new JsonArray();
            lines.forEach(arr::add);
            d.add("lines", arr);
            return ok(d);
        }
    }

    /** 读客户端日志尾部（logs/latest.log，相对运行目录）。 */
    static final class LogAction extends Action {
        private final int lines;
        private final String pattern;
        private final int fromLine;

        LogAction(long id, int lines, String pattern, int fromLine) {
            super(id, "log");
            this.lines = lines;
            this.pattern = pattern;
            this.fromLine = fromLine;
        }

        @Override
        JsonObject tick(Minecraft mc) {
            List<String> all = Io.readAllLines(java.nio.file.Paths.get("logs", "latest.log"));
            Pattern p = pattern.isEmpty() ? null : Pattern.compile(pattern);
            JsonArray arr = new JsonArray();
            int start = Math.max(0, Math.max(fromLine, all.size() - lines));
            for (int i = start; i < all.size(); i++) {
                String l = all.get(i);
                if (p == null || p.matcher(l).find()) arr.add(l);
            }
            JsonObject d = new JsonObject();
            d.addProperty("totalLines", all.size());
            d.add("lines", arr);
            return ok(d);
        }
    }

    static final class QuitAction extends Action {
        private final int code;
        private final String reason;

        QuitAction(long id, int code, String reason) {
            super(id, "quit");
            this.code = code;
            this.reason = reason;
        }

        @Override
        JsonObject tick(Minecraft mc) {
            JsonObject d = new JsonObject();
            d.addProperty("code", code);
            d.addProperty("reason", reason);
            Agent.I.requestQuit(code, reason);
            return ok(d);
        }
    }

    static final class FailAction extends Action {
        FailAction(long id, String op) {
            super(id, op.isEmpty() ? "?" : op);
        }

        @Override
        JsonObject tick(Minecraft mc) {
            return fail("不认识的动作 '" + op + "'。可用：cmd / wait / wait_for / shot / key / screen / state / block / "
                    + "entities / chat / log / quit / move / jump / look / use / mine / place / hotbar / drop / "
                    + "open_inventory / container / say");
        }
    }

    // ══ 玩家输入层 ═════════════════════════════════════════════════════════

    /**
     * 按住一组键 N tick 再松开。移动、跳、潜行、疾跑都走这条 —— 原版每 tick 自己读
     * {@code KeyMapping.isDown()}，所以这就是"真按键"，不是作弊式位移。
     */
    static final class HoldAction extends Action {
        private final List<String> 键;
        private final int ticks;
        private boolean started;
        private int held;
        private String 起点;

        HoldAction(long id, String op, List<String> 键, int ticks) {
            super(id, op);
            this.键 = 键;
            this.ticks = Math.max(1, ticks);
            this.timeoutTicks = this.ticks + 400;
        }

        @Override
        JsonObject tick(Minecraft mc) {
            if (mc.player == null || mc.level == null) return fail("还没进世界");
            if (mc.screen != null) return fail("有界面打开着，按键会被界面吃掉（先 screen close）");
            if (!started) {
                started = true;
                List<String> 缺 = Input.按键(mc, 键, true);
                if (!缺.isEmpty()) {
                    Input.全松();
                    return fail("找不到按键映射：" + 缺);
                }
                起点 = 位置(mc);
            }
            held++;
            if (held >= ticks) {
                Input.按键(mc, 键, false);
                JsonObject d = new JsonObject();
                d.addProperty("keys", String.join("+", 键));
                d.addProperty("from", 起点);
                d.addProperty("to", 位置(mc));
                d.addProperty("ticks", held);
                return ok(d);
            }
            return null;
        }

        @Override
        JsonObject timeout() {
            Input.全松();
            return fail("按 " + 键 + " 等超时（松开了，免得键卡住）");
        }
    }

    /** 转向：给 yaw/pitch，或给一个世界坐标点让代理自己算。 */
    static final class LookAction extends Action {
        private final Double yaw;
        private final Double pitch;
        private final String target;
        private int settle;
        private String 前;

        LookAction(long id, Double yaw, Double pitch, String target) {
            super(id, "look");
            this.yaw = yaw;
            this.pitch = pitch;
            this.target = target == null ? "" : target.trim();
        }

        @Override
        JsonObject tick(Minecraft mc) {
            if (mc.player == null) return fail("还没进世界");
            if (settle == 0) {
                前 = String.format("%.1f,%.1f", mc.player.getYRot(), mc.player.getXRot());
                if (!target.isEmpty()) {
                    String[] p = target.split("[, ]+");
                    if (p.length < 3) return fail("target 要写成 \"x,y,z\"");
                    try {
                        Input.看向(mc, new Vec3(Double.parseDouble(p[0]), Double.parseDouble(p[1]), Double.parseDouble(p[2])));
                    } catch (NumberFormatException e) {
                        return fail("target 解析失败：" + target);
                    }
                } else {
                    if (yaw != null) mc.player.setYRot(yaw.floatValue());
                    if (pitch != null) mc.player.setXRot(Math.max(-90f, Math.min(90f, pitch.floatValue())));
                }
            }
            settle++;
            // 朝向要等一 tick 才会被服务端收到（服务端对 useItemOn 会校验视线），
            // 也要等一 tick 客户端自己才会重算 hitResult。
            if (settle < 3) return null;
            JsonObject d = new JsonObject();
            d.addProperty("fromYawPitch", 前);
            d.addProperty("toYawPitch", String.format("%.1f,%.1f", mc.player.getYRot(), mc.player.getXRot()));
            JsonObject aim = new JsonObject();
            aim.addProperty("hit", 描述命中(mc.hitResult));
            if (mc.hitResult != null && mc.hitResult.getType() == HitResult.Type.BLOCK) {
                BlockHitResult b = (BlockHitResult) mc.hitResult;
                aim.addProperty("block", Input.方块名(mc, b.getBlockPos()));
                aim.addProperty("pos", b.getBlockPos().getX() + "," + b.getBlockPos().getY() + "," + b.getBlockPos().getZ());
                aim.addProperty("face", b.getDirection().getName());
            }
            d.add("crosshair", aim);
            return ok(d);
        }
    }

    /** 右键：用物品 / 放置 / 交互实体。ticks>1 时按住（吃东西、拉弓）。 */
    static final class UseAction extends Action {
        private final int ticks;
        private int held;
        private String 首次;

        UseAction(long id, int ticks) {
            super(id, "use");
            this.ticks = Math.max(1, ticks);
            this.timeoutTicks = this.ticks + 400;
        }

        @Override
        JsonObject tick(Minecraft mc) {
            if (mc.player == null) return fail("还没进世界");
            if (mc.screen != null) return fail("有界面打开着，右键会被界面吃掉（先 screen close）");
            if (held == 0) {
                首次 = Input.一次使用(mc);
            } else if (!mc.player.isUsingItem()) {
                Input.一次使用(mc);   // 长按：原版是靠 rightClickDelay 反复触发的
            }
            held++;
            if (held >= ticks) {
                boolean 仍在用 = mc.player.isUsingItem();
                if (仍在用) mc.gameMode.releaseUsingItem(mc.player);
                JsonObject d = new JsonObject();
                d.addProperty("result", 首次);
                d.addProperty("ticks", held);
                d.addProperty("usingAtEnd", 仍在用);
                d.addProperty("crosshair", 描述命中(mc.hitResult));
                d.addProperty("heldItem", Input.物品名(mc.player.getMainHandItem()));
                return ok(d);
            }
            return null;
        }
    }

    /**
     * 左键：挖方块 / 打实体。
     * <p>⚠️ 不走"按住左键"那条路 —— 原版 {@code continueAttack} 要求鼠标被抓取，
     * 窗口一失焦就停。这里直接调 {@code MultiPlayerGameMode.continueDestroyBlock()}。
     */
    static final class MineAction extends Action {
        private final String pos;
        private final int ticks;
        private final Input.MineState st = new Input.MineState();
        private int settle;

        MineAction(long id, String pos, int ticks) {
            super(id, "mine");
            this.pos = pos == null ? "" : pos.trim();
            this.ticks = Math.max(1, ticks);
            this.timeoutTicks = this.ticks + 400;
        }

        @Override
        JsonObject tick(Minecraft mc) {
            if (mc.player == null || mc.level == null) return fail("还没进世界");
            if (mc.screen != null) return fail("有界面打开着（先 screen close）");
            if (!pos.isEmpty() && st.目标 == null) {
                BlockPos t = parsePos(pos);
                if (t == null) return fail("pos 要写成 \"x,y,z\"");
                Input.看向(mc, Vec3.atCenterOf(t));
                if (++settle < 2) return null;
            }
            HitResult hit = mc.hitResult;
            if (hit != null && hit.getType() == HitResult.Type.ENTITY && st.起点方块 == null) {
                String r = Input.一次攻击(mc);
                if (r == null) return fail("没打中任何东西");
                JsonObject d = new JsonObject();
                d.addProperty("result", r);
                d.addProperty("ticks", elapsed());
                return ok(d);
            }
            String err = Input.挖掘一步(mc, st);
            st.步数++;
            if (err != null && st.起点方块 == null) return fail(err);
            if (st.起点方块 != null && st.目标 != null && mc.level.getBlockState(st.目标).isAir()) {
                JsonObject d = new JsonObject();
                d.addProperty("block", st.起点方块);
                d.addProperty("pos", st.目标.getX() + "," + st.目标.getY() + "," + st.目标.getZ());
                d.addProperty("ticks", st.步数);
                d.addProperty("broken", true);
                return ok(d);
            }
            return null;
        }

        @Override
        JsonObject timeout() {
            return fail("挖了 " + elapsed() + " tick 还没破（生存模式要选对工具；也可能准星飘了）");
        }
    }

    /**
     * 放置方块：算好贴靠块 → 瞄准 → 校验准星真的落在贴靠块的正确面上 → 右键。
     * <p>不是"直接写世界"—— 走的是玩家右键那条路，所以 mod 的 useItemOn 才会被触发。
     */
    static final class PlaceAction extends Action {
        private final BlockPos 目标;
        private BlockPos 邻块;
        private int settle;

        PlaceAction(long id, String pos) {
            super(id, "place");
            this.目标 = parsePos(pos);
        }

        @Override
        JsonObject tick(Minecraft mc) {
            if (目标 == null) return fail("pos 要写成 \"x,y,z\"");
            if (mc.player == null || mc.level == null) return fail("还没进世界");
            if (mc.screen != null) return fail("有界面打开着，右键会被界面吃掉（先 screen close）");
            if (Input.物品名(mc.player.getMainHandItem()).equals("空手")) {
                return fail("手上没拿东西（先 hotbar 切到有方块的格子，或 cmd give 再 hotbar）");
            }
            if (mc.level.getBlockState(目标).isAir() == false) {
                // 不拦，但要说清楚：右键会作用在已有的方块上
                Io.warn("目标 " + 目标 + " 已不是空气，放置可能变成交互");
            }
            if (邻块 == null) {
                邻块 = Input.找贴靠块(mc, 目标);
                if (邻块 == null) return fail("目标 " + 目标 + " 四周没有可贴靠的方块（悬空放不了）");
                Input.看向(mc, Input.瞄准点(邻块, 目标));
            }
            if (++settle < 2) return null;
            HitResult hit = mc.hitResult;
            if (hit == null || hit.getType() != HitResult.Type.BLOCK
                    || !((BlockHitResult) hit).getBlockPos().equals(邻块)) {
                return fail("准星没落在贴靠块 " + 邻块.getX() + "," + 邻块.getY() + "," + 邻块.getZ()
                        + "（实际：" + 描述命中(hit) + "）—— 视线被挡或离太远（右键上限约 4.5 格）");
            }
            BlockHitResult bh = (BlockHitResult) hit;
            double 距离 = Math.sqrt(mc.player.getEyePosition().distanceToSqr(Vec3.atCenterOf(邻块)));
            if (距离 > 4.5) {
                return fail(String.format("离贴靠块 %.2f 格，超过右键上限 4.5", 距离));
            }
            String r = Input.一次使用(mc);
            JsonObject d = new JsonObject();
            d.addProperty("target", 目标.getX() + "," + 目标.getY() + "," + 目标.getZ());
            d.addProperty("against", 邻块.getX() + "," + 邻块.getY() + "," + 邻块.getZ());
            d.addProperty("face", bh.getDirection().getName());
            d.addProperty("distance", String.format("%.2f", 距离));
            d.addProperty("result", r);
            d.addProperty("heldItem", Input.物品名(mc.player.getMainHandItem()));
            return ok(d);
        }
    }

    /** 点一下某个按键映射（交给原版的 handleKeybinds 去消费）。 */
    static final class KeyClickAction extends Action {
        private final String 键;
        private boolean clicked;
        private int settle;
        private String 界面;

        KeyClickAction(long id, String op, String 键) {
            super(id, op);
            this.键 = 键;
        }

        @Override
        JsonObject tick(Minecraft mc) {
            if (mc.player == null && !键.equals("key.inventory")) return fail("还没进世界");
            if (!clicked) {
                KeyMapping km = Input.找映射(mc, 键);
                if (km == null) return fail("找不到按键映射 " + 键);
                KeyMapping.click(Input.绑定键(km));
                clicked = true;
                return null;
            }
            if (++settle < 3) return null;    // 等 handleKeybinds 消费
            界面 = mc.screen == null ? "" : mc.screen.getClass().getName();
            JsonObject d = new JsonObject();
            d.addProperty("key", 键);
            d.addProperty("screen", 界面);
            JsonObject inv = Observe.inventory(mc.player);
            if (inv != null) d.addProperty("selectedSlot", inv.get("selectedSlot").getAsInt());
            return ok(d);
        }
    }

    /** 打开玩家背包（真按 E，界面和玩家手点出来的一模一样）。 */
    static final class OpenInventoryAction extends Action {
        private boolean clicked;
        private int settle;

        OpenInventoryAction(long id) {
            super(id, "open_inventory");
        }

        @Override
        JsonObject tick(Minecraft mc) {
            if (mc.player == null) return fail("还没进世界");
            if (!clicked) {
                KeyMapping km = Input.找映射(mc, "key.inventory");
                if (km == null) return fail("找不到 key.inventory");
                KeyMapping.click(Input.绑定键(km));
                clicked = true;
                return null;
            }
            if (++settle < 4) return null;
            JsonObject d = new JsonObject();
            d.addProperty("screen", mc.screen == null ? "" : mc.screen.getClass().getName());
            JsonObject 容器 = Input.容器快照(mc);
            if (容器 != null) d.add("container", 容器);
            else d.addProperty("container", "");
            return ok(d);
        }
    }

    /** 容器/背包界面：dump 槽位、按序号点槽位、关掉。 */
    static final class ContainerAction extends Action {
        private final String action;
        private final int slot;
        private final int button;
        private final boolean shift;

        ContainerAction(long id, String action, int slot, int button, boolean shift) {
            super(id, "container");
            this.action = action;
            this.slot = slot;
            this.button = button;
            this.shift = shift;
        }

        @Override
        JsonObject tick(Minecraft mc) {
            switch (action) {
                case "dump" -> {
                    JsonObject 快照 = Input.容器快照(mc);
                    if (快照 == null) {
                        JsonObject d = new JsonObject();
                        d.addProperty("open", false);
                        d.addProperty("screen", mc.screen == null ? "" : mc.screen.getClass().getName());
                        return ok(d);
                    }
                    快照.addProperty("open", true);
                    return ok(快照);
                }
                case "click" -> {
                    if (slot < 0) return fail("click 需要给 slot 序号（先 container dump 看序号）");
                    String err = Input.点槽位(mc, slot, button, shift);
                    if (err != null) return fail(err);
                    JsonObject d = new JsonObject();
                    d.addProperty("clicked", slot);
                    d.addProperty("button", button);
                    d.addProperty("shift", shift);
                    return ok(d);
                }
                case "close" -> {
                    if (mc.player == null) return fail("还没进世界");
                    try {
                        mc.player.closeContainer();
                    } catch (Throwable t) {
                        Io.warn("closeContainer 抛异常：" + t);
                    }
                    mc.setScreen(null);
                    return ok(null);
                }
                default -> {
                    return fail("不认识的 container action：" + action + "（dump/click/close）");
                }
            }
        }
    }

    /** 发一条聊天消息（测聊天相关的功能）。 */
    static final class SayAction extends Action {
        private final String text;
        private boolean sent;

        SayAction(long id, String text) {
            super(id, "say");
            this.text = text;
        }

        @Override
        JsonObject tick(Minecraft mc) {
            if (mc.getConnection() == null) return fail("还没进世界");
            if (!sent) {
                if (text.isEmpty()) return fail("text 为空");
                mc.getConnection().sendChat(text);
                sent = true;
                return null;
            }
            return ok(null);
        }
    }
}

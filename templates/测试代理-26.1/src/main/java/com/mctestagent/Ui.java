package com.mctestagent;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.client.gui.components.AbstractButton;
import net.minecraft.client.gui.components.AbstractWidget;
import net.minecraft.client.gui.components.EditBox;
import net.minecraft.client.gui.components.events.ContainerEventHandler;
import net.minecraft.client.gui.components.events.GuiEventListener;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.input.MouseButtonEvent;
import net.minecraft.client.input.MouseButtonInfo;
import net.minecraft.network.chat.Component;

import java.util.ArrayList;
import java.util.List;

/**
 * 界面（Screen）操作：列出控件、按「翻译键」找按钮并点它、按坐标点、往输入框填字。
 *
 * <p>按翻译键点按钮是这套工具的核心手法之一 —— 它不依赖坐标，也不依赖分辨率，
 * 更不会因为窗口没被前台激活而失效（这一点外部自动化做不到）。
 */
final class Ui {
    private Ui() {
    }

    /** 收集界面里的全部控件（含容器控件里的子控件）。 */
    static List<AbstractWidget> widgets(Screen screen) {
        List<AbstractWidget> out = new ArrayList<>();
        if (screen == null) return out;
        collect(screen.children(), out, 0);
        return out;
    }

    private static void collect(List<? extends GuiEventListener> list, List<AbstractWidget> out, int depth) {
        if (list == null || depth > 8) return;
        for (GuiEventListener l : list) {
            if (l instanceof AbstractWidget w) out.add(w);
            if (l instanceof ContainerEventHandler c) collect(c.children(), out, depth + 1);
        }
    }

    static JsonObject dump(Screen screen) {
        JsonObject o = new JsonObject();
        if (screen == null) {
            o.addProperty("screen", "");
            o.addProperty("title", "");
            o.add("widgets", new JsonArray());
            return o;
        }
        o.addProperty("screen", screen.getClass().getName());
        o.addProperty("title", text(screen.getTitle()));
        JsonArray arr = new JsonArray();
        for (AbstractWidget w : widgets(screen)) {
            JsonObject e = new JsonObject();
            e.addProperty("cls", w.getClass().getSimpleName());
            e.addProperty("text", text(w.getMessage()));
            e.addProperty("x", w.getX());
            e.addProperty("y", w.getY());
            e.addProperty("w", w.getWidth());
            e.addProperty("h", w.getHeight());
            e.addProperty("active", w.active);
            e.addProperty("visible", w.visible);
            if (w instanceof EditBox eb) e.addProperty("value", eb.getValue());
            arr.add(e);
        }
        o.add("widgets", arr);
        return o;
    }

    /**
     * 按「翻译键」或「控件上显示的文字」找控件。
     * 传 {@code gui.done} 这种键名时，会先翻译成本地化文字再比对。
     */
    static AbstractWidget find(Screen screen, String textOrKey) {
        if (screen == null) return null;
        String want = textOrKey;
        try {
            Component c = Component.translatable(textOrKey);
            String translated = c.getString();
            if (!translated.equals(textOrKey)) want = translated;
        } catch (Throwable ignored) {
        }
        List<AbstractWidget> all = widgets(screen);
        for (AbstractWidget w : all) {
            if (w.visible && want.equals(text(w.getMessage()))) return w;
        }
        for (AbstractWidget w : all) {
            if (w.getMessage() != null && text(w.getMessage()).contains(want)) return w;
        }
        return null;
    }

    /** 点一个控件：按钮走 onPress（和玩家点击同一条代码路径），其余走 onClick。 */
    static boolean click(AbstractWidget w, int button) {
        if (w == null) return false;
        try {
            int cx = w.getX() + w.getWidth() / 2;
            int cy = w.getY() + w.getHeight() / 2;
            if (w instanceof AbstractButton b) {
                b.onPress(new MouseButtonInfo(button, 0));
                return true;
            }
            w.onClick(new MouseButtonEvent(cx, cy, new MouseButtonInfo(button, 0)), false);
            return true;
        } catch (Throwable t) {
            Io.warn("点击失败：" + t);
            return false;
        }
    }

    /** 按坐标点界面（走界面自己的命中测试）。 */
    static void clickAt(Screen screen, double x, double y, int button) {
        if (screen == null) return;
        screen.mouseClicked(new MouseButtonEvent(x, y, new MouseButtonInfo(button, 0)), false);
    }

    /** 往第一个输入框填字（没有输入框则返回 false）。 */
    static boolean type(Screen screen, String value) {
        if (screen == null) return false;
        for (AbstractWidget w : widgets(screen)) {
            if (w instanceof EditBox eb) {
                eb.setValue(value);
                return true;
            }
        }
        return false;
    }

    static String text(Component c) {
        return c == null ? "" : c.getString();
    }
}

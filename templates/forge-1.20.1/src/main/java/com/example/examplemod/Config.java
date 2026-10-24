package com.example.examplemod;

import java.util.List;
import java.util.Set;
import java.util.stream.Collectors;

import net.minecraft.resources.ResourceLocation;
import net.minecraft.world.item.Item;
import net.minecraftforge.common.ForgeConfigSpec;
import net.minecraftforge.eventbus.api.SubscribeEvent;
import net.minecraftforge.fml.common.Mod;
import net.minecraftforge.fml.event.config.ModConfigEvent;
import net.minecraftforge.registries.ForgeRegistries;

/**
 * 示例配置类（Forge 版）。
 *
 * <p>官方 Forge MDK 自带这个文件，抓模板时漏了 —— 缺了它 {@code ExampleMod} 直接编译失败
 * （{@code Config.logDirtBlock} / {@code magicNumber} / {@code magicNumberIntroduction} /
 * {@code items} / {@code SPEC} 全部找不到符号）。2026-09-10 补。</p>
 *
 * <p>和 NeoForge 版的差别：这边用的是「{@code onLoad} 里缓存到静态字段」的写法，
 * 因为 Forge 1.20.1 的模板 {@code ExampleMod} 就是直接读 {@code Config.logDirtBlock}。
 * 另注意 Forge 的 {@code @Mod.EventBusSubscriber} <b>是有 bus 属性的</b>
 * （NeoForge 26.x 才把它去掉）。</p>
 *
 * <p>⚠️ 这份是按官方 Forge 1.20.1 MDK 写的，<b>没有实机编译验证过</b>
 * （本机没装 1.20.1 的依赖）。用之前先 `gradlew build` 过一遍。</p>
 */
@Mod.EventBusSubscriber(modid = ExampleMod.MODID, bus = Mod.EventBusSubscriber.Bus.MOD)
public class Config {

    private static final ForgeConfigSpec.Builder BUILDER = new ForgeConfigSpec.Builder();

    private static final ForgeConfigSpec.BooleanValue LOG_DIRT_BLOCK = BUILDER
            .comment("Whether to log the dirt block on common setup")
            .define("logDirtBlock", true);

    private static final ForgeConfigSpec.IntValue MAGIC_NUMBER = BUILDER
            .comment("A magic number")
            .defineInRange("magicNumber", 42, 0, Integer.MAX_VALUE);

    public static final ForgeConfigSpec.ConfigValue<String> MAGIC_NUMBER_INTRODUCTION = BUILDER
            .comment("What you want the introduction message to be for the magic number")
            .define("magicNumberIntroduction", "The magic number is... ");

    private static final ForgeConfigSpec.ConfigValue<List<? extends String>> ITEM_STRINGS = BUILDER
            .comment("A list of items to log on common setup.")
            // 1.20.1 的签名是 (List<String> path, Supplier<List<? extends T>>, Predicate<Object>)，
            // 不是 (String path, List, Supplier, Predicate) —— 写错会报「无法推断类型变量」，
            // 属于开箱编译就失败的那类错误（2026-10-22 实测）
            .defineListAllowEmpty(List.of("items"), () -> List.of("minecraft:iron_ingot"),
                    Config::validateItemName);

    static final ForgeConfigSpec SPEC = BUILDER.build();

    public static boolean logDirtBlock;
    public static int magicNumber;
    public static String magicNumberIntroduction;
    public static Set<Item> items;

    private static boolean validateItemName(final Object obj) {
        return obj instanceof final String itemName
                && ForgeRegistries.ITEMS.containsKey(new ResourceLocation(itemName));
    }

    @SubscribeEvent
    static void onLoad(final ModConfigEvent event) {
        logDirtBlock = LOG_DIRT_BLOCK.get();
        magicNumber = MAGIC_NUMBER.get();
        magicNumberIntroduction = MAGIC_NUMBER_INTRODUCTION.get();
        items = ITEM_STRINGS.get().stream()
                .map(itemName -> ForgeRegistries.ITEMS.getValue(new ResourceLocation(itemName)))
                .collect(Collectors.toSet());
    }
}

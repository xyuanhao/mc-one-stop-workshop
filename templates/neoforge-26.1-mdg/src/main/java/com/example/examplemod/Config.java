package com.example.examplemod;

import java.util.List;

import net.neoforged.neoforge.common.ModConfigSpec;

/**
 * 示例配置类。
 *
 * <p>官方 MDK 自带这个文件，抓模板时漏了 —— 缺了它 {@code ExampleMod} 会直接编译失败
 * （5 处 {@code Config.xxx} 找不到符号）。2026-09-10 补。</p>
 *
 * <p>这里的写法刻意保持「直接用 ConfigValue」而不是缓存到静态字段，因为模板里的
 * {@code ExampleMod} 就是那么用的（{@code Config.LOG_DIRT_BLOCK.getAsBoolean()}）。
 * 想改成缓存写法的话，注意 26.x 的 {@code @EventBusSubscriber} <b>没有 bus 属性</b>。</p>
 */
public class Config {

    private static final ModConfigSpec.Builder BUILDER = new ModConfigSpec.Builder();

    public static final ModConfigSpec.BooleanValue LOG_DIRT_BLOCK = BUILDER
            .comment("Whether to log the dirt block on common setup")
            .define("logDirtBlock", true);

    public static final ModConfigSpec.IntValue MAGIC_NUMBER = BUILDER
            .comment("A magic number")
            .defineInRange("magicNumber", 42, 0, Integer.MAX_VALUE);

    public static final ModConfigSpec.ConfigValue<String> MAGIC_NUMBER_INTRODUCTION = BUILDER
            .comment("What you want the introduction message to be for the magic number")
            .define("magicNumberIntroduction", "The magic number is... ");

    public static final ModConfigSpec.ConfigValue<List<? extends String>> ITEM_STRINGS = BUILDER
            .comment("A list of items to log on common setup.")
            .defineListAllowEmpty("items", List.of("minecraft:iron_ingot"), () -> "",
                    obj -> obj instanceof String);

    public static final ModConfigSpec SPEC = BUILDER.build();

    private Config() {
    }
}

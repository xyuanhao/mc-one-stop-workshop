# Fabric 生态 + Architectury 跨平台 资料收集

> 用途：为「AI 自动生成可编译、可运行的 Minecraft Java 版 mod」的 skill 提供精确、可执行、带版本号的参考资料。
> 收集日期：2026-09-04
> 覆盖方向：Fabric Loader + Fabric API + fabric-loom + Mixin + Architectury 跨平台
> 原则：每条关键事实标注来源 URL；凡无法从权威来源确认者标注「⚠️ 待验证」；1.20.1 与 1.21+ 写法不同处并列两种写法并标注分界版本。

---

## 0. 版本总览（先读这张表）

| 对象 | 版本（2026-09 观测） | 来源 |
|---|---|---|
| Fabric Loader | `0.19.3`（fabric-example-mod 各分支统一使用） | https://github.com/FabricMC/fabric-example-mod/blob/1.21.5/gradle.properties |
| Fabric API | `0.128.2+1.21.5`（随 MC 版本变） | 同上 |
| fabric-loom（Gradle 插件） | `1.17`（当前稳定线；example-mod 用 `1.17-SNAPSHOT`） | https://github.com/FabricMC/fabric-loom/releases |
| Architectury API | 见 Architectury 章节（按 MC 版本发布） | https://github.com/architectury/architectury-api |
| Architectury Plugin（Gradle） | `3.4.x ~ 4.x`（按 MC 版本） | https://github.com/architectury/architectury-plugin |

> ⚠️ 待验证：fabric-loom 的「当前最新稳定发布号」会随时间变化，请以 https://github.com/FabricMC/fabric-loom/releases 顶部条目为准。example-mod 模板默认用 `-SNAPSHOT` 开发版，生产构建建议锁定到不带 `-SNAPSHOT` 的 release 号。

---

## 1. 工具链与环境

### 1.1 Fabric Loader / Fabric API / Loom 坐标与用法

**fabric-loom 插件坐标**（写在 `build.gradle` 的 `plugins` 块）：

```gradle
// 1.21.4+ 的非混淆（non-obfuscated）Minecraft 版本推荐用 remap 变体；旧式写法 fabric-loom 仍向后兼容
plugins {
    id 'net.fabricmc.fabric-loom' version "${loom_version}"   // 兼容别名，保留
    id 'maven-publish'
}
```

> Loom 1.14 起新增两个插件 ID（`net.fabricmc.fabric-loom` 用于非混淆版本，`net.fabricmc.fabric-loom-remap` 用于混淆版本），`fabric-loom` 作为兼容别名保留。来源：https://github.com/FabricMC/fabric-loom/releases

**官方 example-mod 的完整 `build.gradle`（1.21.5 分支，节选要点）：**

```gradle
plugins {
    id 'net.fabricmc.fabric-loom-remap' version "${loom_version}"
    id 'maven-publish'
}

loom {
    splitEnvironmentSourceSets()           // 拆分 main / client 源码集，便于环境隔离
    mods {
        "modid" {
            sourceSet sourceSets.main
            sourceSet sourceSets.client
        }
    }
}

dependencies {
    minecraft "com.mojang:minecraft:${project.minecraft_version}"
    mappings loom.officialMojangMappings()                       // ★ 1.21.x 推荐：官方 Mojang 映射
    modImplementation "net.fabricmc:fabric-loader:${project.loader_version}"
    modImplementation "net.fabricmc.fabric-api:fabric-api:${project.fabric_api_version}"
}

// Java 工具链：1.21.x 必须 release = 21（见 1.2 节 JDK 矩阵）
tasks.withType(JavaCompile).configureEach {
    it.options.release = 21
}
java {
    withSourcesJar()
    sourceCompatibility = JavaVersion.VERSION_21
    targetCompatibility = JavaVersion.VERSION_21
}
```

> 来源：https://raw.githubusercontent.com/FabricMC/fabric-example-mod/1.21.5/build.gradle

**关于 mappings 的重要变化：**
- 当前 example-mod（含 1.20.1 分支）统一使用 `loom.officialMojangMappings()`。
- 旧式（1.21 之前主流）写法为 `yarn_mappings` 字段 + `mappings "net.fabricmc:yarn:${yarn_mappings}:v2"`（Yarn 映射）。
- 1.21.4 起 Mojang 发布的 Minecraft 部分版本为非混淆（non-obfuscated），官方映射成为首选。
- ⚠️ 待验证：若你要为 1.20.1 老项目用 Yarn，字段名为 `yarn_mappings`，格式形如 `1.20.1+build.10`。请以模板生成器实际产出为准。

### 1.2 各 MC 版本对应的 JDK 要求

来源（权威）：https://minecraft.wiki/w/Tutorial:Update_Java

| MC 版本区间 | 最低 JDK | 说明 |
|---|---|---|
| 1.18（1.18-pre2） ~ 1.20.4 | **Java 17** | 含用户指定的 1.20.1、1.20.4 |
| 1.20.5（24w14a） ~ 1.21.11（含 1.21.1 / 1.21.4 / 1.21.5） | **Java 21** | 含用户指定的全部 1.21.x |
| 26.1+ | Java 25 | 最新快照线 |

**对 mod 开发的两层含义：**
1. **运行/编译目标**：mod 代码须以对应 `release` 编译（1.20.x → 17，1.21.x → 21）。example-mod 通过 `it.options.release = 17`（1.20.1）或 `= 21`（1.21.5）强制。
2. **构建工具自身**：fabric-loom 1.12+ 要求**用 Java 21 运行 Gradle**；loom 1.17 要求 Gradle 9.4+。即「用 JDK 21 跑构建、产物可 `release=17` 兼容 1.20.x」是可行的。来源：https://github.com/FabricMC/fabric-loom/releases

> ⚠️ 待验证：Architectury 多平台项目里 Forge/NeoForge 侧对 JDK 的额外要求（ForgeGradle 通常也要求 Java 17/21，视 MC 版本），请以 Architectury 模板实际产出为准。

### 1.3 从零创建工程

**方式 A：官方模板生成器（推荐）**
- 地址：https://fabricmc.net/develop/template/
- 在网页选择 MC 版本、Mapping、是否启用数据生成，下载 zip 解压即用，已含 `gradle.properties`、`build.gradle`、`settings.gradle`、`src/` 骨架与 `runClient/runServer/runDatagen` 运行配置。

**方式 B：克隆 fabric-example-mod**
- https://github.com/FabricMC/fabric-example-mod（按 MC 版本切换分支，如 `1.21.5`）。

**方式 C：手动搭建**
- 新建目录，放置 `gradle/wrapper/`、`settings.gradle`、`build.gradle`、`gradle.properties`、`src/main/java/...`、`src/main/resources/fabric.mod.json`、`src/main/resources/pack.mcmeta`。
- 关键：`build.gradle` 必须 `plugins { id 'net.fabricmc.fabric-loom' ... }`，且 `dependencies` 至少包含 `minecraft` + `mappings` + `fabric-loader` + `fabric-api`。

### 1.4 gradle.properties 关键字段

来源：https://raw.githubusercontent.com/FabricMC/fabric-example-mod/1.21.5/gradle.properties

```properties
# 构建内存（按需调大）
org.gradle.jvmargs=-Xmx1G
org.gradle.parallel=true

# Fabric 属性
minecraft_version=1.21.5
loader_version=0.19.3
loom_version=1.17-SNAPSHOT

# Mod 属性
version=1.0.0
group=com.example

# 依赖（fabric-api 版本随 MC 版本，形如 <api版本>+<mc版本>）
fabric_api_version=0.128.2+1.21.5
```

字段说明：

| 字段 | 含义 | 备注 |
|---|---|---|
| `minecraft_version` | 目标 MC 版本 | 决定 `com.mojang:minecraft` 坐标 |
| `yarn_mappings` | Yarn 映射版本（旧式） | 现代模板改用 `loom.officialMojangMappings()`，不再需要此字段 |
| `loader_version` | Fabric Loader 版本 | 例 `0.19.3` |
| `loom_version` | fabric-loom 插件版本 | 例 `1.17` / `1.17-SNAPSHOT` |
| `mod_version` / `version` | 你的 mod 版本 | example-mod 用 `version` |
| `maven_group` / `group` | Maven 组坐标 | 例 `com.example` |
| `archives_base_name` | 产出 jar 基名 | example-mod 未显式设置，默认取项目名 |
| `fabric_api_version` | Fabric API 版本 | 形如 `0.128.2+1.21.5` |

> ⚠️ 待验证：`archives_base_name` 在部分模板里并不存在（example-mod 默认用 `project.name`）。若需自定义 jar 名，可自行添加该字段并在 `jar`/`publishing` 中使用。

### 1.5 常用 Gradle 任务

| 任务 | 作用 | 来源 |
|---|---|---|
| `runClient` | 启动带 mod 的客户端（调试） | loom 自动生成 |
| `runServer` | 启动带 mod 的服务端 | loom 自动生成 |
| `build` | 编译并产出 remap 后的 jar（`build/libs/*.jar`） | loom |
| `remapJar` | 仅对 jar 做反混淆重映射（build 已包含） | loom |
| `genSources` | 反编译 MC 与依赖源码供 IDE 阅读 | loom |
| `runDatagen` | 运行数据生成入口点（见 3.8） | loom + Fabric API |
| `sourcesJar` / `remapSourcesJar` | 源码 jar | loom |

> 注：Loom 1.17 引入「RunConfiguration」新 API（旧 API 弃用），并新增 `preferGradleTask = true` 选项让 IDE 运行配置改用 Gradle 的 `run*` 任务。来源：https://github.com/FabricMC/fabric-loom/releases

### 1.6 国内网络环境常见坑

> ⚠️ 以下为经验性建议，具体镜像地址与可用性会变化，请按需验证。

- **MavenCentral / Fabric maven 访问慢**：可在 `build.gradle` 顶部 `repositories` 之外，于 `settings.gradle` 或全局 `~/.gradle/init.gradle` 配置国内镜像（如阿里云 maven 镜像、或 HF Mirror 的 maven 代理）。Fabric 依赖主要位于 `https://maven.fabricmc.net` 与 `https://maven.minecraftforge.net`（Forge 侧）。
- **assets 下载慢/失败**：首次 `runClient` 会下载 MC 资源（assets）与版本元数据。可设置 `org.gradle.jvmargs` 增大内存，并确保 `assets` 目录可写；必要时手动放置或换源。
- **Parchment mappings**：⚠️ Parchment 主要是 **Forge / NeoForge** 侧的参数名映射方案，**Fabric 侧不需要也不建议加 Parchment**。Fabric 用 `loom.officialMojangMappings()` 或 Yarn。不要照搬 Forge 教程里的 `loom.parchmentMappings(...)`。
- **Gradle 分发下载慢**：可预先用国内镜像下载 `gradle-wrapper` 对应的 `gradle-<ver>-bin.zip`，或配置 `distributionUrl` 指向镜像。

---

## 2. 工程结构

### 2.1 标准目录树

```
my-mod/
├── build.gradle
├── settings.gradle
├── gradle.properties
├── gradle/wrapper/
│   ├── gradle-wrapper.jar
│   └── gradle-wrapper.properties
├── src/main/
│   ├── java/com/example/
│   │   ├── ExampleMod.java            # ModInitializer（main 入口）
│   │   ├── client/ExampleModClient.java   # ClientModInitializer（client 入口）
│   │   ├── item/TutorialItems.java
│   │   ├── block/TutorialBlocks.java
│   │   └── ...
│   └── resources/
│       ├── fabric.mod.json            # 必须
│       ├── pack.mcmeta                # 资源包描述（1.21.4 之前需要；之后见注）
│       ├── assets/<modid>/
│       │   ├── icon.png
│       │   ├── lang/en_us.json        # 本地化
│       │   ├── lang/zh_cn.json
│       │   ├── models/item/*.json
│       │   ├── models/block/*.json
│       │   ├── blockstates/*.json
│       │   ├── textures/item/*.png
│       │   ├── textures/block/*.png
│       │   └── items/*.json          # ★ 1.21.4+ 物品模型定义新位置
│       └── data/<modid>/
│           ├── recipes/*.json
│           ├── loot_table/blocks/*.json
│           └── tags/items/*.json
```

> `pack.mcmeta` 在 1.21.4 起被「item model definition」机制部分取代：物品模型定义挪到 `assets/<modid>/items/*.json`。资源包自身描述在较新版本仍可保留，但示例 mod 已不再强依赖。来源：https://wiki.fabricmc.net/tutorial:items

### 2.2 fabric.mod.json 完整字段说明

来源：https://raw.githubusercontent.com/FabricMC/fabric-example-mod/1.21.5/src/main/resources/fabric.mod.json

```json
{
  "schemaVersion": 1,
  "id": "modid",
  "version": "${version}",
  "name": "Example Mod",
  "description": "This is an example description! Tell everyone what your mod is about!",
  "authors": ["Me!"],
  "contact": {
    "homepage": "https://fabricmc.net/",
    "sources": "https://github.com/FabricMC/fabric-example-mod"
  },
  "license": "CC0-1.0",
  "icon": "assets/modid/icon.png",
  "environment": "*",
  "entrypoints": {
    "main": ["com.example.ExampleMod"],
    "client": ["com.example.client.ExampleModClient"]
  },
  "mixins": [
    "modid.mixins.json",
    { "config": "modid.client.mixins.json", "environment": "client" }
  ],
  "depends": {
    "fabricloader": ">=0.19.3",
    "minecraft": "~1.21.5",
    "java": ">=21",
    "fabric-api": "*"
  }
}
```

字段表：

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| `schemaVersion` | int | 是 | 当前固定为 `1` |
| `id` | string | 是 | mod 唯一 ID（小写、无空格） |
| `version` | string | 是 | 支持 `${version}` 占位符（由 processResources 展开） |
| `name` | string | 否 | 显示名 |
| `description` | string | 否 | 描述 |
| `authors` | array | 否 | 作者列表 |
| `contact` | object | 否 | homepage/sources/issues 等链接 |
| `license` | string | 否 | SPDX 许可证 ID |
| `icon` | string/array | 否 | 图标路径（可多分辨率数组） |
| `environment` | string | 否 | `*` 任意 / `client` 仅客户端 / `server` 仅服务端 |
| `entrypoints` | object | 是 | main/client/server 入口点类全限定名列表 |
| `mixins` | array | 否 | mixin 配置文件名（字符串或带 environment 的对象） |
| `depends` | object | 否 | 硬依赖（加载器/MC/Java/其他 mod），语义化版本约束 |
| `recommends` / `suggests` | object | 否 | 推荐/建议依赖 |
| `breaks` / `conflicts` | object | 否 | 不兼容约束 |
| `accessWidener` | string | 否 | `.accesswidener` 文件路径（见 3.10） |
| `custom` | object | 否 | 自定义元数据 |

版本约束语法：`~1.21.5`（1.21.5 起的小版本兼容）、`>=0.19.3`、`*`、`^1.0.0` 等。来源：Fabric Loader 文档。

### 2.3 入口点写法

```java
// src/main/java/com/example/ExampleMod.java
package com.example;

import net.fabricmc.api.ModInitializer;

public class ExampleMod implements ModInitializer {
    @Override
    public void onInitialize() {
        // 服务端+客户端都会执行（仅限环境无关逻辑）
        TutorialItems.initialize();
        TutorialBlocks.initialize();
    }
}
```

```java
// src/main/java/com/example/client/ExampleModClient.java
package com.example.client;

import net.fabricmc.api.ClientModInitializer;

public class ExampleModClient implements ClientModInitializer {
    @Override
    public void onInitializeClient() {
        // 仅客户端执行（注册渲染、按键、HUD 等）
    }
}
```

- **服务端专用入口**：`net.fabricmc.api.DedicatedServerModInitializer`（实现 `onInitializeServer()`），仅专用服务端加载。
- 三者通过 `fabric.mod.json` 的 `entrypoints` 的 `main` / `client` / `server` 注册。

### 2.4 environment 与 @Environment(EnvType.CLIENT)

- `fabric.mod.json` 顶层 `environment` 控制整个 mod 的加载环境（`*`/`client`/`server`）。
- 代码层面：客户端专属类/字段/方法用 `net.fabricmc.api.Environment` + `EnvType.CLIENT` 标注，避免被服务端类加载器加载时报 `ClassNotFoundException`：

```java
import net.fabricmc.api.Environment;
import net.fabricmc.api.EnvType;

@Environment(EnvType.CLIENT)
public class ExampleClientOnly {
    @Environment(EnvType.CLIENT)
    public static void doClientStuff() { ... }
}
```

> 标注在类上表示该类的所有成员仅在客户端可用；标注在成员上只影响该成员。未在客户端加载的代码路径不要引用被标注的成员。

---

## 3. 核心系统（最小可运行代码块）

> 命名空间统一用 `tutorial`；所有 `Identifier` 写法按版本分界（见 3.1）。

### 3.1 物品注册（含 1.21 分界）

来源：https://wiki.fabricmc.net/tutorial:items

**分界版本：1.21**。`Identifier.of(namespace, path)` 自 1.21 起；`new Identifier(namespace, path)` 为 1.21 之前写法（1.21/1.21.1 中构造函数被弃用但仍可用，建议迁移）。

**1.21 起（含 1.21.1/1.21.4/1.21.5）基础写法：**

```java
package com.example.item;

import net.minecraft.item.Item;
import net.minecraft.registry.Registry;
import net.minecraft.registry.Registries;
import net.minecraft.util.Identifier;

public final class TutorialItems {
    private TutorialItems() {}

    // 1.21 起用 Identifier.of
    public static final Item CUSTOM_ITEM =
        Registry.register(Registries.ITEM, Identifier.of("tutorial", "custom_item"),
            new Item(new Item.Settings()));

    public static void initialize() {}
}
```

**1.21.2+ 的「重写」写法（必须存 RegistryKey）：**

```java
package com.example.item;

import net.minecraft.item.Item;
import net.minecraft.item.Items;
import net.minecraft.registry.RegistryKey;
import net.minecraft.registry.RegistryKeys;
import net.minecraft.util.Identifier;
import java.util.function.Function;

public final class TutorialItems {
    private TutorialItems() {}

    // ★ 1.21.2+ 必须用 Items.register(registryKey, factory, settings)
    public static final Item CUSTOM_ITEM =
        register("custom_item", Item::new, new Item.Settings());

    public static Item register(String path, Function<Item.Settings, Item> factory, Item.Settings settings) {
        final RegistryKey<Item> registryKey = RegistryKey.of(RegistryKeys.ITEM, Identifier.of("tutorial", path));
        return Items.register(registryKey, factory, settings);
    }

    public static void initialize() {}
}
```

**1.20.1 / 1.20.4（1.21 之前）写法：**

```java
// 1.20.x 之前：
public static final Item CUSTOM_ITEM =
    Registry.register(Registries.ITEM, new Identifier("tutorial", "custom_item"),
        new Item(new Item.Settings()));
```

> 自定义物品类：`public class CustomItem extends Item { public CustomItem(Settings s){ super(s);} }`。
> 注意 `Item.use(...)` 返回值在 **1.21.2+** 由 `TypedActionResult<ItemStack>` 改为 `ActionResult`：

```java
// 1.21.2+ ：
@Override public ActionResult use(World world, PlayerEntity user, Hand hand) { return ActionResult.SUCCESS; }
// 1.21.1 及之前：
@Override public TypedActionResult<ItemStack> use(World world, PlayerEntity user, Hand hand) { return TypedActionResult.success(user.getStackInHand(hand)); }
```

### 3.2 方块注册 + FabricBlockSettings + 方块实体

来源：https://wiki.fabricmc.net/tutorial:blocks

**方块注册：**

```java
// 1.21.2+ 写法（推荐，带 RegistryKey）
package com.example.block;

import net.minecraft.block.Block;
import net.minecraft.block.Blocks;
import net.minecraft.item.Item;
import net.minecraft.item.Items;
import net.minecraft.registry.RegistryKey;
import net.minecraft.registry.RegistryKeys;
import net.minecraft.util.Identifier;
import net.minecraft.block.AbstractBlock;
import java.util.function.Function;

public final class TutorialBlocks {
    public static final Block EXAMPLE_BLOCK =
        register("example_block", Block::new, Block.Settings.create().strength(4.0f));

    private static Block register(String path, Function<AbstractBlock.Settings, Block> factory, AbstractBlock.Settings settings) {
        final Identifier identifier = Identifier.of("tutorial", path);
        final RegistryKey<Block> registryKey = RegistryKey.of(RegistryKeys.BLOCK, identifier);
        final Block block = Blocks.register(registryKey, factory, settings);
        Items.register(block); // 自动创建并注册对应 BlockItem
        return block;
    }

    public static void initialize() {}
}
```

```java
// 1.20.1 / 1.20.4（1.21 之前）写法：
public static final Block EXAMPLE_BLOCK =
    Registry.register(Registries.BLOCK, new Identifier("tutorial", "example_block"),
        new Block(Block.Settings.create().strength(4.0f)));
// 并另注册 BlockItem：
Registry.register(Registries.ITEM, new Identifier("tutorial", "example_block"),
    new BlockItem(EXAMPLE_BLOCK, new Item.Settings()));
```

> `FabricBlockSettings` 在 1.20.5 起被原版 `Block.Settings.create()` 取代（`FabricBlockSettings` 已弃用）。`Material.METAL` 在 1.20 起移除，改用 `Block.Settings.create().strength(...)`。

**方块实体（BlockEntity + BlockEntityType）：**

```java
// DemoBlockEntity.java
package com.example.block;

import net.minecraft.block.entity.BlockEntity;
import net.minecraft.util.math.BlockPos;
import net.minecraft.world.World;

public class DemoBlockEntity extends BlockEntity {
    public DemoBlockEntity(BlockPos pos, net.minecraft.block.BlockState state) {
        super(TutorialBlockEntities.DEMO_BLOCK_ENTITY, pos, state);
    }
}
```

```java
// TutorialBlockEntities.java
package com.example.block;

import net.fabricmc.fabric.api.object.builder.v1.block.entity.FabricBlockEntityTypeBuilder;
import net.minecraft.block.entity.BlockEntityType;
import net.minecraft.registry.Registry;
import net.minecraft.registry.Registries;
import net.minecraft.util.Identifier;

public final class TutorialBlockEntities {
    public static final BlockEntityType<DemoBlockEntity> DEMO_BLOCK_ENTITY =
        Registry.register(Registries.BLOCK_ENTITY_TYPE,
            Identifier.of("tutorial", "demo"),
            FabricBlockEntityTypeBuilder.create(DemoBlockEntity::new, TutorialBlocks.EXAMPLE_BLOCK)
                .build());

    public static void initialize() {}
}
```

> ⚠️ 待验证：1.21.2+ 是否需要给 `BlockEntityType` 也走 `RegistryKey` 体系。当前权威示例均采用 `Registry.register(Registries.BLOCK_ENTITY_TYPE, Identifier.of(...), ...)`，未要求 `RegistryKey`，此写法按现状保留。

### 3.3 创造模式物品栏（ItemGroupEvents）

来源：https://wiki.fabricmc.net/tutorial:itemgroup

**加入已有物品栏（1.20+ 通用，含 1.21.x）：**

```java
import net.fabricmc.fabric.api.itemgroup.v1.ItemGroupEvents;
import net.minecraft.item.ItemGroups;

// 在 onInitialize 或专门方法里：
ItemGroupEvents.modifyEntriesEvent(ItemGroups.BUILDING_BLOCKS).register(content -> {
    content.add(TutorialItems.CUSTOM_ITEM);
    // 精确定位：content.addAfter(Items.OAK_DOOR, TutorialItems.CUSTOM_ITEM);
});
```

**自建物品栏（FabricItemGroup.builder）：**

```java
import net.fabricmc.fabric.api.itemgroup.v1.FabricItemGroup;
import net.minecraft.item.ItemGroup;
import net.minecraft.item.ItemStack;
import net.minecraft.registry.Registry;
import net.minecraft.text.Text;
import net.minecraft.util.Identifier;

public final class TutorialItemGroups {
    // 1.21 起：Registry.register(Registries.ITEM_GROUP, Identifier.of(...), ...)
    public static final ItemGroup TEST_GROUP = Registry.register(
        Registries.ITEM_GROUP,
        Identifier.of("tutorial", "test_group"),
        FabricItemGroup.builder()
            .icon(() -> new ItemStack(TutorialItems.CUSTOM_ITEM))
            .displayName(Text.translatable("itemGroup.tutorial.test_group"))
            .entries((context, entries) -> entries.add(TutorialItems.CUSTOM_ITEM))
            .build());

    public static void initialize() {}
}
```

> ⚠️ `ItemGroupEvents` 自 1.20.5 起引入；1.19.2 及更早用 `FabricItemGroupBuilder`/`RegistryEntry` 旧式，本文不展开。

### 3.4 事件（Fabric API 的 Event 回调）

常用事件清单（包路径为 Fabric API 标准位置）：

| 事件 | 包 / 类 | 典型用途 |
|---|---|---|
| `UseBlockCallback` | `net.fabricmc.fabric.api.event.player` | 右键点击方块 |
| `UseItemCallback` | 同上 | 使用物品 |
| `UseEntityCallback` | 同上 | 右键实体 |
| `AttackEntityCallback` | 同上 | 攻击实体 |
| `AttackBlockCallback` | 同上 | 攻击方块 |
| `BreakBlockCallback` | 同上 | 破坏方块 |
| `PlayerBlockBreakEvents` | 同上 | 破坏前后钩子 |
| `ServerLifecycleEvents` | `net.fabricmc.fabric.api.event.lifecycle.v1` | 服务端启动/停止（SERVER_STARTED 等） |
| `ServerTickEvents` | 同上 | 服务端 tick（START/END_SERVER_TICK） |
| `ServerWorldEvents` | 同上 | 世界加载/卸载 |
| `ServerPlayerEvents` | 同上 | 玩家登录/重生/切换维度（AFTER_RESPAWN 等） |
| `ServerPlayConnectionEvents` | `net.fabricmc.fabric.api.networking.v1` | 玩家连接/断开 |
| `ResourceManagerHelper` | `net.fabricmc.fabric.api.resource` | 注册资源重载监听 |
| `CommandRegistrationCallback` | `net.fabricmc.fabric.api.command.v2` | 注册指令 |

示例：`UseBlockCallback`

```java
import net.fabricmc.fabric.api.event.player.UseBlockCallback;
import net.minecraft.util.ActionResult;

UseBlockCallback.EVENT.register((player, world, hand, hitResult, hitState) -> {
    // 返回 ActionResult.PASS 表示不拦截，SUCCESS/FALSE 表示已处理
    return ActionResult.PASS;
});
```

示例：`ServerTickEvents.START_SERVER_TICK`

```java
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents;

ServerTickEvents.START_SERVER_TICK.register(server -> {
    // 每服务端 tick 执行
});
```

> 来源：Fabric API 仓库事件类（https://github.com/FabricMC/fabric ）。事件中「回调注册」统一为 `<Event>.EVENT.register(...)` 模式。

### 3.5 网络（1.20.5+ 新 API）

来源：https://wiki.fabricmc.net/tutorial:networking

**分界版本：1.20.5**。旧 API（`ServerPlayNetworking.registerReceiver(Identifier, handler)` + `PacketByteBuf`）在 1.20.5 被 `CustomPayload` 体系取代。

**定义 Payload（服务端→客户端）：**

```java
package com.example.network;

import net.fabricmc.fabric.api.networking.v1.PayloadTypeRegistry;
import net.minecraft.network.RegistryByteBuf;
import net.minecraft.network.codec.PacketCodec;
import net.minecraft.network.packet.CustomPayload;
import net.minecraft.util.Identifier;
import net.minecraft.util.math.BlockPos;

public record HighlightPayload(BlockPos blockPos) implements CustomPayload {
    public static final CustomPayload.Id<HighlightPayload> ID =
        new CustomPayload.Id<>(Identifier.of("tutorial", "highlight"));

    public static final PacketCodec<RegistryByteBuf, HighlightPayload> CODEC =
        PacketCodec.tuple(BlockPos.PACKET_CODEC, HighlightPayload::blockPos, HighlightPayload::new);

    @Override
    public Id<? extends CustomPayload> getId() {
        return ID;
    }
}
```

**注册类型（common / main 入口）：**

```java
// playS2C：服务端→客户端；playC2S：客户端→服务端
PayloadTypeRegistry.playS2C().register(HighlightPayload.ID, HighlightPayload.CODEC);
```

**客户端接收（client 入口）：**

```java
import net.fabricmc.fabric.api.networking.v1.ClientPlayNetworking;

ClientPlayNetworking.registerGlobalReceiver(HighlightPayload.ID, (payload, context) -> {
    context.client().execute(() -> {
        // 在渲染线程安全操作
    });
});
```

**服务端发送：**

```java
import net.fabricmc.fabric.api.networking.v1.ServerPlayNetworking;
import net.minecraft.server.network.ServerPlayerEntity;

ServerPlayNetworking.send(player, new HighlightPayload(targetPos));
```

> 要点：`RegistryByteBuf` 用于 PLAY 阶段；多字段 codec 用 `PacketCodec.tuple(f1, g1, f2, g2, ctor)` 链式拼接；客户端 handler 内必须通过 `context.client().execute(...)` 切到渲染线程再碰客户端对象。

### 3.6 配置（auto-config / cloth-config / 手写 gson）

**最稳妥、零额外依赖：手写 Gson**（跨版本稳定）

```java
package com.example;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import net.fabricmc.loader.api.FabricLoader;
import java.nio.file.Files;
import java.nio.file.Path;

public class ModConfig {
    public int maxStack = 64;
    public boolean enableFeature = true;

    private static final Gson GSON = new GsonBuilder().setPrettyPrinting().create();
    private static final Path PATH =
        FabricLoader.getInstance().getConfigDir().resolve("tutorial.json");

    public static ModConfig load() {
        try {
            if (Files.exists(PATH)) return GSON.fromJson(Files.readString(PATH), ModConfig.class);
        } catch (Exception e) { /* 回退默认 */ }
        return new ModConfig();
    }

    public void save() {
        try { Files.writeString(PATH, GSON.toJson(this)); } catch (Exception ignored) {}
    }
}
```

**Cloth Config + Auto Config（带设置界面，需加依赖）：**

```gradle
// build.gradle dependencies：
modApi "me.shedaniel.cloth:cloth-config-fabric:${project.cloth_config_version}"
modApi "com.terraformersmc:modmenu:${project.modmenu_version}"
```

> ⚠️ 待验证：`cloth-config` / `modmenu` 的具体版本坐标随 MC 版本频繁变化，请从 https://modrinth.com/mod/cloth-config / https://modrinth.com/mod/modmenu 按目标 MC 版本取值；Auto Config（`me.shedaniel.autoconfig`）API 也可能变，本文不展开完整注解示例以免出错。

### 3.7 本地化（lang json）

```json
// src/main/resources/assets/tutorial/lang/en_us.json
{
  "item.tutorial.custom_item": "Custom Item",
  "itemGroup.tutorial.test_group": "Tutorial Group"
}
```

```json
// src/main/resources/assets/tutorial/lang/zh_cn.json
{
  "item.tutorial.custom_item": "自定义物品",
  "itemGroup.tutorial.test_group": "教程分组"
}
```

> ⚠️ 待验证：Minecraft 1.21.4 起语言文件命名规则有调整（`zh_cn` 区域码用法变化），请以目标版本实测为准；`en_us.json` 命名长期稳定。

### 3.8 数据生成（Fabric Data Generation）

来源：https://fabricmc.net/wiki/tutorial:datagen_setup 、https://docs.fabricmc.net/zh_cn/develop/data-generation/setup

**启用（build.gradle）：**

```gradle
// 1.21.4 之前：
fabricApi {
    configureDataGeneration()
}
// ★ 1.21.4 及之后：
fabricApi {
    configureDataGeneration() {
        client = true
    }
}
```

**入口点类：**

```java
package com.example;

import net.fabricmc.fabric.api.datagen.v1.DataGeneratorEntrypoint;
import net.fabricmc.fabric.api.datagen.v1.FabricDataGenerator;

public class ExampleModDataGenerator implements DataGeneratorEntrypoint {
    @Override
    public void onInitializeDataGenerator(FabricDataGenerator generator) {
        FabricDataGenerator.Pack pack = generator.createPack();
        pack.addProvider(MyRecipeProvider::new);
        pack.addProvider(MyLootTableProvider::new);
        pack.addProvider(MyTagProvider::new);
    }
}
```

**在 fabric.mod.json 注册入口点：**

```json
"entrypoints": {
  "fabric-datagen": ["com.example.ExampleModDataGenerator"],
  "main": ["com.example.ExampleMod"],
  "client": ["com.example.client.ExampleModClient"]
}
```

**配方 Provider（最小可运行）：**

```java
package com.example;

import net.fabricmc.fabric.api.datagen.v1.FabricDataOutput;
import net.fabricmc.fabric.api.datagen.v1.provider.FabricRecipeProvider;
import net.minecraft.data.server.recipe.RecipeExporter;
import net.minecraft.data.server.recipe.ShapedRecipeJsonBuilder;
import net.minecraft.item.Items;
import net.minecraft.recipe.book.RecipeCategory;
import net.minecraft.registry.RegistryKeys;
import net.minecraft.util.Identifier;

public class MyRecipeProvider extends FabricRecipeProvider {
    public MyRecipeProvider(FabricDataOutput output) { super(output); }

    @Override
    public void generate(RecipeExporter exporter) {
        ShapedRecipeJsonBuilder.create(RecipeCategory.MISC, TutorialItems.CUSTOM_ITEM, 1)
            .input('c', Items.COPPER_INGOT)
            .pattern("cc")
            .pattern("cc")
            .criterion("has_copper",
                net.fabricmc.fabric.api.datagen.v1.provider.FabricRecipeProvider.Conditions.fromItem(Items.COPPER_INGOT))
            .offerTo(exporter, Identifier.of("tutorial", "custom_item_from_copper"));
    }
}
```

**战利品表 Provider：**

```java
package com.example;

import net.fabricmc.fabric.api.datagen.v1.FabricDataOutput;
import net.fabricmc.fabric.api.datagen.v1.provider.FabricBlockLootTableProvider;

public class MyLootTableProvider extends FabricBlockLootTableProvider {
    public MyLootTableProvider(FabricDataOutput output) { super(output); }

    @Override
    public void generate() {
        addDrop(TutorialBlocks.EXAMPLE_BLOCK);
    }
}
```

**标签 Provider：**

```java
package com.example;

import net.fabricmc.fabric.api.datagen.v1.FabricDataOutput;
import net.fabricmc.fabric.api.datagen.v1.provider.FabricTagProvider;
import net.minecraft.item.Item;
import net.minecraft.item.Items;
import net.minecraft.registry.RegistryKeys;
import net.minecraft.registry.RegistryWrapper;
import net.minecraft.registry.tag.TagKey;
import net.minecraft.util.Identifier;

public class MyTagProvider extends FabricTagProvider<Item> {
    public MyTagProvider(FabricDataOutput output, RegistryWrapper.WrapperLookup lookup) {
        super(output, RegistryKeys.ITEM, lookup);
    }

    @Override
    protected void configure(RegistryWrapper.WrapperLookup lookup) {
        TagKey<Item> smelly = TagKey.of(RegistryKeys.ITEM, Identifier.of("tutorial", "smelly_items"));
        getOrCreateTagBuilder(smelly).add(Items.ROTTEN_FLESH);
    }
}
```

**运行：** `./gradlew runDatagen`，产出写入 `src/main/generated`（需通过 `processResources` 或 IDE 配置纳入资源；或在 `build.gradle` 把 `src/main/generated` 加入 `resources.srcDirs`）。

> ⚠️ 1.21.4 的 `FabricDataOutput` API 变化：旧式零参 `FabricDataOutput.createPack()`（返回 `Path`）已弃用，自定义 provider 应改用 `FabricDataOutput.createPack(FabricDataOutput.Pack.OutputType, boolean replace, String... namespaces)` 返回 `Pack`，再用 `Pack#path()` 取路径。标准配方/战利品/标签 Provider 由框架处理，无需手动调用。具体签名以 `maven.fabricmc.net/docs/fabric-api-*` Javadoc 为准（待验证精确方法名）。

### 3.9 客户端渲染

- **方块实体渲染**：`BlockEntityRenderer` + 注册 `BlockEntityRendererRegistry.register(...)`，包 `net.fabricmc.fabric.api.client.rendering.v1.BlockEntityRendererRegistry`。
- **实体渲染**：`EntityRenderer` + 注册 `EntityRendererRegistry.register(...)`，包 `net.fabricmc.fabric.api.client.rendering.v1.EntityRendererRegistry`。
- **模型 json**：`assets/<modid>/models/block/*.json`、`models/item/*.json`（1.21.4+ 物品模型定义额外在 `assets/<modid>/items/*.json`）。

```java
// 客户端入口中注册方块实体渲染器：
import net.fabricmc.fabric.api.client.rendering.v1.BlockEntityRendererRegistry;

BlockEntityRendererRegistry.register(TutorialBlockEntities.DEMO_BLOCK_ENTITY, DemoBlockEntityRenderer::new);
```

> 模型 json 与方块状态 json 语法遵循 Minecraft Wiki（https://minecraft.wiki/w/Model 、https://minecraft.wiki/w/Blockstates_definition ）。

### 3.10 AccessWidener

`.accesswidener` 文件用于放宽目标类/方法/字段的访问权限（public/final/protected 去除），让 mod 能访问原版私有成员，无需 Mixin。

**语法（行式）：**

```
accessWidener   v1
accessible      method  net/minecraft/class_1234  method_5678 ()V
extendable      class  net/minecraft/class_1234
mutable         field  net/minecraft/class_1234  field_9012  I
```

指令：`accessible`（方法/字段变 public）、`extendable`（类/方法可继承/重写）、`mutable`（字段非 final）。

**启用（fabric.mod.json）：**

```json
"accessWidener": "tutorial.accesswidener"
```

文件放 `src/main/resources/tutorial.accesswidener`。类名用**映射后的中间名（intermediary/yarn）或官方名**，取决于你的 mapping 配置（使用官方映射时写官方名如 `net/minecraft/server/MinecraftServer`）。

> 来源：Fabric Loader AccessWidener 文档（https://fabricmc.net/wiki/documentation:accesswidener ）。⚠️ 精确 intermediary 名需借助映射查询工具（见第 4 节 linkie）。

---

## 4. Mixin 详解（Fabric 侧）

Mixin 由 SpongePowered 提供，Fabric 通过 fabric-loom 内置的 Mixin 注解处理器（mixin-ap）自动处理 remap。来源：https://github.com/SpongePowered/Mixin/wiki

### 4.1 mixins.json 配置

```json
{
  "required": true,
  "minVersion": "0.8",
  "package": "com.example.mixin",
  "compatibilityLevel": "JAVA_21",
  "plugin": "com.example.MyMixinPlugin",
  "injectors": {
    "defaultRequire": 1
  },
  "mixins": [
    "MyBlockMixin"
  ],
  "client": [
    "MyClientMixin"
  ],
  "server": [],
  "priority": 1000
}
```

| 字段 | 含义 |
|---|---|
| `required` | true 表示缺失该 config 则加载失败 |
| `minVersion` | Mixin 最低版本 |
| `package` | mixin 类所在基包（下面写相对名） |
| `compatibilityLevel` | 目标 Java 级别（JAVA_17 / JAVA_21） |
| `plugin` | 可选，`IMixinConfigPlugin` 实现类全限定名 |
| `injectors.defaultRequire` | 注入默认要求匹配次数（0 表示允许不匹配） |
| `mixins` / `client` / `server` | 各环境加载的 mixin 类列表 |
| `priority` | 多个 config 冲突时优先级 |

在 `fabric.mod.json` 中引用：`"mixins": ["tutorial.mixins.json"]`（字符串）或带 environment 的对象（见 2.2）。

### 4.2 关键注解

| 注解 | 作用 |
|---|---|
| `@Mixin(TargetClass.class)` | 声明混入目标类（也可用 `value = ...` 字符串写映射名） |
| `@Inject(method = "...", at = @At(...))` | 在目标方法指定位置注入回调 |
| `@Overwrite` | 完整覆盖目标方法（签名须一致） |
| `@Shadow` | 映射目标类私有成员为可访问的「影子」引用 |
| `@Unique` | 标记 mixin 自身新增的成员，避免与目标类冲突 |
| `@ModifyExpressionValue` | MixinExtras 提供：修改某表达式的返回值 |

### 4.3 @Inject + @At 注入点写法

来源：https://github.com/SpongePowered/Mixin/wiki/Advanced-Mixin-Usage---Callback-Injectors

```java
package com.example.mixin;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

@Mixin(net.minecraft.server.MinecraftServer.class)
public abstract class ServerMixin {

    // 方法开头注入
    @Inject(method = "loadWorld", at = @At("HEAD"))
    private void onLoadWorld(CallbackInfo ci) {
        System.out.println("world loading");
    }

    // 方法返回前注入；ordinal 指定第几个 RETURN（从 0 计）
    @Inject(method = "tick", at = @At(value = "RETURN", ordinal = 0))
    private void onTickReturn(CallbackInfo ci) {}

    // 在调用某方法之前注入；target 用「方法名(描述符)」完整签名
    @Inject(method = "setWeather",
            at = @At(value = "INVOKE",
                     target = "Lnet/minecraft/server/world/ServerWorld;resetWeather()V"))
    private void beforeResetWeather(CallbackInfo ci) {}

    // 可取消：void 方法用 ci.cancel()；非 void 用 cir.setReturnValue(...)
    @Inject(method = "isHardcore", at = @At("HEAD"), cancellable = true)
    private void onIsHardcore(CallbackInfoReturnable<Boolean> cir) {
        // cir.setReturnValue(true); // 隐式取消并改返回值
    }
}
```

**@At 目标类型一览：** `HEAD`、`RETURN`、`INVOKE`、`INVOKE_ASSIGN`（唯一在目标「之后」注入）、`FIELD`（读/写字段）、`TAIL` 等。`INVOKE` 的 `target` 必须是 `L<类>;方法名(<描述符>)<返回>` 形式（如 `()V`、`(I)Z`）。

**@Shadow / @Unique 示例：**

```java
@Mixin(net.minecraft.entity.player.PlayerEntity.class)
public abstract class PlayerMixin {
    @Shadow public abstract boolean isSpectator();   // 影子：映射原版私有/受保护方法
    @Unique private int myCounter;                    // 新增成员，不冲突
}
```

**@Overwrite 示例：**

```java
@Mixin(net.minecraft.item.Item.class)
public abstract class ItemMixin {
    @Overwrite
    public int getMaxCount() {
        return 99; // 完全替换原版实现，签名必须一致
    }
}
```

### 4.4 MixinExtras（@ModifyExpressionValue 等）

MixinExtras 提供 `@ModifyExpressionValue`、`@ModifyArg`、`@ModifyVariable`、`@WrapOperation`、`@WrapWithCondition` 等高级注入。需在 mixin 环境启用（Fabric 的 loom 已集成）。

```java
import com.llamalad7.mixinextras.injector.ModifyExpressionValue;
import com.llamalad7.mixinextras.sugar.Local;

@Mixin(Target.class)
public class ModifyMixin {
    @ModifyExpressionValue(method = "calc", at = @At(value = "INVOKE",
            target = "Lnet/minecraft/util/math/MathHelper;clamp(DDD)D"))
    private double makeBigger(double original) {
        return original * 2.0;
    }
}
```

> ⚠️ 待验证：MixinExtras 的 `@Local` 捕获局部变量、精确 `@At` 子选项随版本微调，复杂注入请对照目标 MC 版本反编译结果校准。

### 4.5 如何反查方法名（remap / 官方映射 / linkie）

- **官方映射（Mojang mappings）**：用 `loom.officialMojangMappings()` 时，`@Mixin` 的 `value` 与 `@At` 的 `target` 直接写**官方（ obfuscated→named）名**，如 `net.minecraft.server.MinecraftServer`。
- **Yarn / Intermediary**：旧式 Yarn 映射下，`@Mixin` 用 yarn 名（如 `net.minecraft.server.MinecraftServer`），`@At` 的 `target` 描述符里类的部分常用 **intermediary 编号**（`class_1234`、`method_5678`）。
- **linkie**：在线映射查询工具 https://linkie.shedaniel.dev/ ，可查类/方法在 yarn、intermediary、官方名、Mojang 之间的对应，以及反编译签名。
- **genSources**：运行 `./gradlew genSources` 后，IDEA 里直接跳转到 MC 反编译源码，复制方法签名最可靠。
- **remap**：fabric-loom 在 `build`/`remapJar` 阶段会把 mixin 里的映射名自动重映射到运行时混淆名，开发时写 named 即可。

> ⚠️ 待验证：具体某方法在 1.21.x 官方映射下的 `target` 字符串，务必以 `genSources` 反编译或 linkie 实测为准，切勿凭记忆拼接描述符。

---

## 5. Architectury 跨平台

来源：https://github.com/architectury/architectury-api 、https://github.com/architectury/architectury-templates

### 5.1 模块划分

Architectury 采用多项目（multi-project）结构，典型模板（https://github.com/architectury/architectury-templates 的 Releases 下载）包含：

```
my-mod/
├── settings.gradle          # include ':common' ':fabric' ':forge'（或 ':neoforge'）
├── build.gradle             # 根：统一配置
├── gradle.properties         # 含 minecraft_version / loader_version / architectury_version 等
├── common/                  # 平台无关代码（入口、注册、逻辑）
│   └── src/main/java/com/example/...
├── fabric/                  # Fabric 专属实现（依赖 fabric-loom）
│   └── src/main/java/com/example/fabric/...
└── forge/                   # Forge 专属实现（依赖 ForgeGradle / Architectury Loom）
    └── src/main/java/com/example/forge/...
# 以及可选 neoforge/ 模块（NeoForge 侧）
```

> 现代模板多为 `common + fabric + forge`（或再加 `neoforge`）。Forge/NeoForge 侧用 `mods.toml`/`neoforge.mods.toml` 而非 `fabric.mod.json`，由 Architectury 插件在构建时桥接。来源：deepwiki/architectury-api 项目结构。

### 5.2 @ExpectPlatform 机制

`@ExpectPlatform`（来自 Architectury Injectables，仅作用于**静态方法**）让你在 common 侧声明方法签名，在平台侧提供实现；编译时插件把调用重定向到对应平台的实现类。

来源：https://github.com/architectury/architectury-api （README 示例）+ deepwiki/architectury-transformer `TransformExpectPlatform` 实现查找算法。

**common 侧声明：**

```java
// common/src/main/java/com/example/Platform.java
package com.example;

import dev.architectury.injectables.annotations.ExpectPlatform;

public class Platform {
    @ExpectPlatform
    public static String getConfigDirectory() {
        throw new AssertionError(); // 占位，编译期被平台实现替换
    }
}
```

**实现类名约定（重要）：**

| 平台 | 实现类全限定名 |
|---|---|
| Fabric | `com.example.fabric.PlatformImpl` |
| Forge | `com.example.forge.PlatformImpl` |
| NeoForge | `com.example.neoforge.PlatformImpl` |

即：与 common 类**同包名**，放在 `fabric` / `forge` / `neoforge` 子包，类名加 `Impl` 后缀。

**Fabric 实现：**

```java
// fabric/src/main/java/com/example/fabric/PlatformImpl.java
package com.example.fabric;

import net.fabricmc.loader.api.FabricLoader;
import java.nio.file.Path;

public class PlatformImpl {
    public static String getConfigDirectory() {
        return FabricLoader.getInstance().getConfigDir().toString();
    }
}
```

**Forge 实现：**

```java
// forge/src/main/java/com/example/forge/PlatformImpl.java
package com.example.forge;

import net.minecraftforge.fml.loading.FMLPaths;
import java.nio.file.Path;

public class PlatformImpl {
    public static String getConfigDirectory() {
        return FMLPaths.CONFIGDIR.get().toString();
    }
}
```

**common 侧调用：** 直接 `Platform.getConfigDirectory()`，编译期按平台补充分发，无需条件分支。

> ⚠️ 待验证：上述实现类名约定（`<pkg>.fabric.PlatformImpl` / `<pkg>.forge.PlatformImpl`）来自 deepwiki 对 `TransformExpectPlatform` 的算法说明与社区示例，建议首次接入时用 Architectury 模板实际产出核对包结构与类名，避免拼错子包。
> 另：`@PlatformOnly({"forge","neoforge"})` 可标注「仅在特定平台保留」的方法/字段，其他平台构建时会被剥离。

### 5.3 一套代码多平台构建的 Gradle 配置要点

- **根 `settings.gradle`**：`include 'common', 'fabric', 'forge'`（及可选 `neoforge`）。
- **根/各模块 `build.gradle`**：common 用普通 `java`/`java-library` 插件；fabric 模块用 `fabric-loom`；forge 模块用 Architectury 的 Forge Loom（fork of loom）或 ForgeGradle。
- **依赖桥接**：fabric 模块 `implementation project(':common')`；common 通过 `@ExpectPlatform` 调用，运行时由插件注入平台实现。
- **Architectury Plugin** 坐标（示例，版本随 MC 变）：`dev.architectury:architectury-plugin:<ver>`，并 `architectury { ... }` 配置。
- **构建命令**：通常模板提供 `./gradlew :fabric:build` 与 `./gradlew :forge:build`（或根 `build` 同时出多平台 jar）。具体任务名以模板 `gradle tasks` 输出为准。
- **入口点差异**：Fabric 用 `fabric.mod.json` 的 `main`/`client`；Forge/NeoForge 用 `mods.toml` + `@Mod` 注解类；Architectury 在 common 写统一初始化，由模板在平台侧桥接。

### 5.4 什么情况下该用 Architectury，什么情况下不值当

**推荐使用：**
- 需要同时发布 Fabric + Forge（+ NeoForge）三端，且核心逻辑（注册、配方、事件、网络协议）大量共享。
- 团队希望「写一次」并维护统一代码库，接受额外的多模块工程复杂度。
- 需要 Architectury 提供的 90+ 跨平台事件钩子与网络抽象。

**不值当 / 不必用：**
- **只做 Fabric**：直接用 fabric-loom + Fabric API 即可，引入 Architectury 只增加构建复杂度与依赖体积。
- mod 体量很小、平台差异巨大（如深度依赖 Forge 能力、Mixin 大量平台特化），抽象收益低。
- 对构建速度/产物体积敏感、不愿维护多模块 Gradle 的工程。
- 注意：Architectury API 本身**可选**——仅用 Architectury Plugin + `@ExpectPlatform` 也能实现跨平台，不必引入 `architectury-api` 依赖（来源：architectury-api README「Architectury API is optional for projects built on architectury」）。

---

## 6. 来源清单（便于复查）

- Fabric 模板 gradle.properties（各版本）：https://github.com/FabricMC/fabric-example-mod （分支 1.20.1 / 1.20.4 / 1.21.1 / 1.21.4 / 1.21.5）
- Fabric 模板 build.gradle：https://raw.githubusercontent.com/FabricMC/fabric-example-mod/1.21.5/build.gradle
- fabric-loom 版本：https://github.com/FabricMC/fabric-loom/releases
- JDK 要求：https://minecraft.wiki/w/Tutorial:Update_Java
- fabric.mod.json 示例：https://raw.githubusercontent.com/FabricMC/fabric-example-mod/1.21.5/src/main/resources/fabric.mod.json
- 物品：https://wiki.fabricmc.net/tutorial:items
- 方块：https://wiki.fabricmc.net/tutorial:blocks
- 物品栏：https://wiki.fabricmc.net/tutorial:itemgroup
- 网络：https://wiki.fabricmc.net/tutorial:networking
- 数据生成：https://fabricmc.net/wiki/tutorial:datagen_setup 、https://docs.fabricmc.net/zh_cn/develop/data-generation/setup
- Mixin：https://github.com/SpongePowered/Mixin/wiki 、https://github.com/SpongePowered/Mixin/wiki/Advanced-Mixin-Usage---Callback-Injectors
- Architectury API：https://github.com/architectury/architectury-api
- Architectury 模板：https://github.com/architectury/architectury-templates
- @ExpectPlatform 算法（secondary）：https://deepwiki.com/architectury/architectury-transformer/6.1-cross-platform-features

---

## 7. ⚠️ 待验证汇总（务必在落地前复核）

1. fabric-loom 当前最新稳定 release 号（会随时间变化，以 releases 页顶部为准）。
2. 1.20.1 老项目若要 Yarn 映射，`yarn_mappings` 字段的具体版本号格式。
3. Architectury 多平台 Forge/NeoForge 侧对 JDK 的额外要求。
4. `archives_base_name` 在 example-mod 中是否显式存在。
5. 国内 Maven/asset 镜像的具体可用地址（经验性，易变）。
6. Parchment 在 Fabric 侧是否完全不可用（结论：是，Fabric 用官方/Yarn 映射）。
7. 1.21.4 的 `FabricDataOutput.createPack(Pack.OutputType, boolean, String...)` 精确签名与 `Pack#path()` 方法名（以 fabric-api Javadoc 为准）。
8. 1.21.4+ 语言文件（`zh_cn.json` 等）命名规则变化细节。
9. 1.21.2+ 是否对 `BlockEntityType` 注册也要求 `RegistryKey`（现状示例未要求）。
10. `@ExpectPlatform` 实现类的子包/类名约定（`fabric.PlatformImpl` / `forge.PlatformImpl` / `neoforge.PlatformImpl`）以模板实际产出核对。
11. MixinExtras `@Local` 与复杂 `@At` 子选项随版本微调。
12. 具体 mixin `target` 描述符须以 `genSources`/linkie 实测为准。

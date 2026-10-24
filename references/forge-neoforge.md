# Forge（1.20.1）与 NeoForge（1.21+）模组开发资料收集

> 目标：为「AI 自动生成可编译、可运行的 Minecraft Java 版 mod」的 skill 提供**精确、可执行、带版本号**的权威资料。
> 收集日期：2026-09-04。
> 主线划分：**Forge 1.20.1 线**（旧 API：`ForgeRegistries`、`RegistryObject`、`SimpleChannel`、`ForgeConfigSpec`、`mods.toml`、ForgeGradle）+ **NeoForge 1.21+ 线**（新 API：`BuiltInRegistries`/`NeoForgeRegistries`、`DeferredHolder`/`DeferredItem`、`CustomPacketPayload`、`ModConfigSpec`、`neoforge.mods.toml`、ModDevGradle）。
>
> 全文每个关键事实后标注来源 URL；**凡 1.20.1 与 1.21+ 写法不同处均并列两种写法并标注分界版本**；无法从权威来源确认的内容以「⚠️ 待验证」标出。

---

## 1. 工具链与环境

### 1.1 Java / JDK 版本对照表

Minecraft 自 1.18 起由 Mojang 随游戏分发对应 JDK，但**开发 mod（编译 + 跑 Gradle）必须使用匹配的 JDK**。

| MC 版本 | 推荐加载器 | 所需 JDK | 说明来源 |
|---|---|---|---|
| 1.20.1 | Forge | **JDK 17** | Forge 1.20.1 官方文档明确要求「Java 17 Development Kit」[docs.minecraftforge.net/en/1.20.1/gettingstarted/] |
| 1.20.4 | NeoForge（或 Forge） | **JDK 17** | NeoForge 1.20.2–1.20.4 要求 Java 17 [g-portal.com/wiki/en/minecraft-neoforge/] |
| 1.21.1 | NeoForge | **JDK 21** | Mojang 从 1.20.5/1.21 起随游戏分发 Java 21；NeoForge 1.20.5–1.21.x 要求 Java 21 [g-portal.com/wiki/en/minecraft-neoforge/] |
| 1.21.4 | NeoForge | **JDK 21** | 同上 |
| 1.21.5 | NeoForge | **JDK 21** | 同上 |
| 1.21.8 | NeoForge | **JDK 21** | 同上 |
| 1.21.11（当前最新 1.21.x） | NeoForge | **JDK 21** | NeoForge 文档仓含 `version-1.21.11` 分支；Java 要求同 1.21.x [本地 nf-docs/versioned_docs/version-1.21.11] |
| 26.1（当前最新 MC，⚠️ 待验证） | NeoForge（若已支持） | **JDK 25** | 据 g-portal，MC 26.1 起需 Java 25；但 NeoForge 文档仓目前最新快照仅到 1.21.11，**NeoForge 是否已发布 26.x 构建待验证** [g-portal.com/wiki/en/minecraft-neoforge/] |

> 经验法则（社区汇总，非单点权威）：1.16 及更老 → Java 8；1.17 → Java 16/17；1.18–1.20.4 → Java 17；1.20.5+ 与 1.21.x → Java 21。来源：[modready.gg/guides/minecraft-java-version-requirements]、[lordhosting.fr/.../quelle-version-java-minecraft]。

下载建议：Eclipse Temurin（Adoptium）或 Microsoft Build of OpenJDK，务必 64 位。

### 1.2 Gradle 版本与插件坐标

#### Forge 1.20.1 — ForgeGradle（FG 5.x）

- 插件坐标：`id 'net.minecraftforge.gradle' version '5.1.+'`（FG 5.1.x 解析 `1.20.1-47.2.0`）。来源：[CSDN 生产验证 build.gradle]、[gitee virtuarealcraft build.gradle]。
- 依赖写法（build.gradle 内 `dependencies`）：
  ```gradle
  minecraft 'net.minecraft:minecraft:1.20.1'
  mappings 'net.minecraftforge:forge:1.20.1-47.2.0:mappings'
  implementation fg.deobf('net.minecraftforge:forge:1.20.1-47.2.0:universal')
  ```
  来源：[CSDN build.gradle 示例]（注意 Forge 1.20.1 构建号以 `47.2.x` 开头）。
- Gradle 本体：Forge 1.20.1 MDK 自带 Gradle Wrapper（**⚠️ 待验证：具体版本号（推测 8.5 左右）以官方 MDK 的 `gradle-wrapper.properties` 为准**）。
- 仓库：`maven.minecraftforge.net`（在 `settings.gradle` 的 `pluginManagement` 中声明）。来源：[CSDN settings.gradle 示例]。

#### NeoForge 1.21+ — ModDevGradle（MDG，插件 id `net.neoforged.moddev`）

- 插件坐标：`id 'net.neoforged.moddev' version '<见 projects.neoforged.net/neoforged/ModDevGradle>'`（官方文档示例用 `1.0.11`，插件门户显示最新 `2.0.141`；**⚠️ 实际版本请以 https://projects.neoforged.net/neoforged/ModDevGradle 为准**）。来源：[docs.neoforged.net/toolchain/docs/plugins/mdg]、[projects.neoforged.net/neoforged/moddevgradle]。
- Gradle 本体：MDG「compatible with Gradle 8.8」，且要求 Gradle 8.x；插件自身 Wrapper 已升到 8.14.5（2026-08）。**结论：使用 Gradle 8.8+**。来源：[docs.neoforged.net/toolchain/docs/plugins/mdg]、[github.com/neoforged/ModDevGradle]。
- 提供三个 plugin id：
  - `net.neoforged.moddev` —— 现代 NeoForge（21.0.x 及更新）
  - `net.neoforged.moddev.legacyforge` —— **旧版 Forge 1.17–1.20.1（legacy 模式）** 来源：[github.com/neoforged/ModDevGradle/blob/main/build.gradle]
  - `net.neoforged.moddev.repositories` —— 仅注入仓库
- NeoForge 版本写法（build.gradle 的 `neoForge { version = "..." }`）：官方示例 `21.0.103-beta`；实际应取对应 MC 版本的最新 NeoForge 构建（如 1.21.1 对应 `21.1.x`，1.21.4 对应 `21.4.x` 等）。**⚠️ 待验证：各 MC 版本对应的精确 NeoForge 构建号请以 https://projects.neoforged.net/neoforged/neoforge 为准**。来源：[docs.neoforged.net/toolchain/docs/plugins/mdg]。

> ⚠️ 注意：`net.neoforged.moddev.legacyforge` 说明 ModDevGradle 也能开发 1.20.1 Forge 模组，但本项目把「Forge 1.20.1 线」定义为使用 **ForgeGradle + 旧 API** 的传统方式。

### 1.3 从零创建工程（推荐方式）

#### Forge 1.20.1
1. 到 https://files.minecraftforge.net 下载对应版本的 **MDK**（点 `Mdk` → 等待后点 `Skip`）。
2. 解压到空目录，用 IDEA/Eclipse 打开（Gradle 自动导入）。
3. 任务：`genEclipseRuns`（Eclipse）/ `genIntellijRuns`（IDEA）/ `getVSCodeRuns`（VSCode）。来源：[docs.minecraftforge.net/en/1.20.1/gettingstarted/]。
4. 改 `build.gradle`：`base.archivesName = 'mymod'`、`group = 'com.example'`、`version = '1.20-1.0.0.0'`、`java.toolchain.languageVersion = JavaLanguageVersion.of(17)`。来源：同上。

#### NeoForge 1.21+
1. 用官方 MDK 模板仓库（GitHub 组织 `NeoForgeMDKs`，命名规则 `MDK-<mc版本>-<插件>`；例如 `MDK-1.21.1-ModDevGradle`）。来源：[github.com/neoforged/MDK]（说明每个 `1.*` 分支会镜像到 `NeoForgeMDKs` 组织）。
2. 或用 `neoforge.mdks` / 在线生成器（如 beishan 教程所述「DOWNLOAD MOD PROJECT」）。来源：[beishanair.github.io/2025/07/22/nf121/start]。
3. `settings.gradle` 插件：
   ```gradle
   plugins {
       id 'org.gradle.toolchains.foojay-resolver-convention' version '0.8.0' // 自动下载所需 JDK
   }
   ```
4. `build.gradle` 关键段：
   ```gradle
   plugins { id 'net.neoforged.moddev' version '2.0.141' } // 版本以门户为准
   neoForge {
       version = "21.1.x"            // 对应 1.21.1 的 NeoForge 构建
       validateAccessTransformers = true
       runs {
           client { client() }
           data   { data()   }
           server { server() }
       }
       mods {
           mymod { sourceSet sourceSets.main }
       }
   }
   ```
   来源：[docs.neoforged.net/toolchain/docs/plugins/mdg]。
5. `gradle.properties` 关键字段（NeoForge MDK）：
   ```
   minecraft_version=1.21.1
   minecraft_version_range=[1.21.1,1.21.2)
   neo_version=21.1.x
   mod_id=mymod
   mod_name=My Mod
   mod_license=MIT
   mod_version=1.0.0
   mod_group_id=com.example.mymod
   # Parchment（可选，社区参数名/JavaDoc）
   neoForge.parchment.minecraftVersion=1.21
   neoForge.parchment.mappingsVersion=2024.06.23
   ```
   来源：[docs.neoforged.net/docs/gettingstarted/modfiles/]。

### 1.4 常用 Gradle 任务

| 任务 | 作用 | 加载器 |
|---|---|---|
| `runClient` | 启动客户端测试 | Forge + NeoForge |
| `runServer` | 启动服务端测试（首次需改 `eula.txt`、设 `online-mode=false`） | Forge + NeoForge |
| `runData` / `runGameTestServer` | 数据生成 / 跑 GameTest | Forge + NeoForge |
| `build` / `jar` | 产出 `build/libs/<name>-<version>.jar` | Forge + NeoForge |
| `data` | NeoForge 的 data-gen run（见 1.3） | NeoForge |
| `createMinecraftArtifacts` | 生成 Minecraft 开发用 artifacts | **NeoForge (ModDevGradle)** 来源：[github.com/neoforged/ModDevGradle] |
| `genIntellijRuns` / `genEclipseRuns` | 生成 IDE 运行配置 | **Forge (ForgeGradle)** 来源：[docs.minecraftforge.net/en/1.20.1/gettingstarted/] |
| `prepareRun` / `downloadAssets` | 准备运行环境 / 下载 MC 资源 | NeoForge (MDG) 来源：[deepwiki.com/neoforged/ModDevGradle] |

### 1.5 国内网络环境常见坑

- **Maven 源**：默认 `maven.minecraftforge.net`、Mojang 官方库、NeoForge `maven.neoforged.net/releases`。国内访问慢/失败时可换镜像（如 `maven.aliyun.com` 的部分代理、或 HF Mirror），但 Forge/NeoForge 专用仓库通常无镜像，**建议保留官方源并配合代理**。**⚠️ 待验证：是否存在稳定的第三方镜像与具体地址**。
- **MC assets 下载失败**：首次 `runClient` 会下载大量资源/原版 jar，失败多为网络超时。可重试、或手动放置 assets。Gradle 缓存位于 `C:\Users\<用户>\.gradle`。
- **mapping 下载失败**：Forge 用 Mojang 官方 mapping（`mappings` 配置）；Parchment 来自 `maven.parchmentmc.org`。失败时检查 `mappings channel/version` 拼写（如 `parchment 2023.09.03-1.20.1`）。来源：[CSDN build.gradle 示例]、[docs.neoforged.net/toolchain/docs/plugins/mdg]。
- **Parchment**：社区参数名/JavaDoc。NeoForge 在 `gradle.properties` 设 `neoForge.parchment.minecraftVersion` 与 `neoForge.parchment.mappingsVersion`（注意去掉 `v` 前缀）；或在 `neoForge { parchment { minecraftVersion=... mappingsVersion=... } }`。Forge 1.20.1 用 `org.parchmentmc.librarian.forgegradle` 插件 + `mappings channel: 'parchment', version: '...'`。

---

## 2. 工程结构

### 2.1 标准目录树

```
mymod/
├── build.gradle
├── settings.gradle
├── gradle.properties
├── gradle/wrapper/gradle-wrapper.properties
├── src/main/
│   ├── java/com/example/mymod/
│   │   ├── MyMod.java              # @Mod 主类
│   │   ├── item/  block/  ...
│   └── resources/
│       ├── META-INF/
│       │   ├── mods.toml                 # Forge 1.20.1
│       │   └── neoforge.mods.toml        # NeoForge 1.21+
│       ├── pack.mcmeta                  # 资源包格式声明
│       ├── assets/<modid>/
│       │   ├── lang/en_us.json           # 本地化
│       │   ├── models/item/*.json        # 物品/方块模型
│       │   ├── items/*.json              # ⚠️ 1.21.4+ 物品模型定义（见 3.9）
│       │   └── textures/item/*.png
│       └── data/<modid>/                 # 数据包（配方/战利品/标签/进度）
└── src/generated/resources/             # 数据生成输出（需加入 resources 源集）
```
来源：[docs.minecraftforge.net/en/1.20.1/gettingstarted/modfiles/]、[docs.neoforged.net/docs/gettingstarted/modfiles/]、[docs.neoforged.net/docs/resources/client/models/items/]。

`pack.mcmeta`：**⚠️ 待验证具体的 `pack_format` 数字**（各版本不同）。安全做法——直接复制所用 MDK 自带的 `pack.mcmeta`（已对该版本正确）。结构示例：
```json
{
  "pack": {
    "pack_format": 26,
    "supported_formats": [26, 26]
  }
}
```

### 2.2 mods.toml（Forge 1.20.1）完整字段

位置：`src/main/resources/META-INF/mods.toml`。来源：[docs.minecraftforge.net/en/1.20.1/gettingstarted/modfiles/]。

```toml
modLoader="javafml"
loaderVersion="[46,)"          # javafml 主版本号；Forge 47.x 对应 [46,)

license="MIT"
issueTrackerURL="https://github.com/your/mod/issues"
showAsResourcePack=false

[[mods]]
  modId="examplemod"
  version="1.0.0.0"
  displayName="Example Mod"
  displayTest="MATCH_VERSION"
  logoFile="logo.png"
  credits="..."
  authors="Author"
  description='''
  Example description.
  '''

[[dependencies.examplemod]]
  modId="forge"
  mandatory=true
  versionRange="[46,)"
  ordering="NONE"
  side="BOTH"

[[dependencies.examplemod]]
  modId="minecraft"
  mandatory=true
  versionRange="[1.20]"
  ordering="NONE"
  side="BOTH"
```

要点：
- 非 mod 级：`modLoader`(必,`"javafml"`)、`loaderVersion`(必, Maven 版本范围)、`license`(必)、`showAsResourcePack`、`services`、`properties`、`issueTrackerURL`。
- `[[mods]]` 级（每个 mod 一个表）：`modId`(必)、`namespace`、`version`、`displayName`、`description`、`logoFile`、`logoBlur`、`updateJSONURL`、`modUrl`、`credits`、`authors`、`displayURL`、`displayTest`、`features`(可写 `{ java_version = "17" }`)。
  - `versionRange` 约束语法：`[1.21,)` 表示 ≥1.21；`[1.20.5,1.21)` 表示 1.20.5 ≤ x < 1.21；`(,2.0]` 等 Maven 范围。来源：[docs.minecraftforge.net/en/1.20.1/gettingstarted/modfiles/]（Maven Version Range 链接 maven.apache.org）。
- `[[dependencies.<modid>]]` 级：`modId`(必)、`mandatory`(必,**Forge 用布尔**)、`versionRange`、`ordering`(`NONE`/`BEFORE`/`AFTER`)、`side`(`BOTH`/`CLIENT`/`SERVER`)、`referralUrl`。

### 2.2′ neoforge.mods.toml（NeoForge 1.21+）完整字段

位置：`src/main/resources/META-INF/neoforge.mods.toml`。来源：[docs.neoforged.net/docs/gettingstarted/modfiles/]。

```toml
modLoader="javafml"
loaderVersion="[1,)"            # NeoForge 的 javafml 版本为 1
license="MIT"

[[mods]]
  modId="examplemod"
  version="${file.jarVersion}"
  displayName="Example Mod"
  description='''
  Example description.
  '''

[[dependencies.examplemod]]
  modId="neoforge"
  type="required"               # NeoForge 用 type="required|optional|incompatible|discouraged"
  versionRange="[21.1,)"        # ⚠️ 示例：约束 NeoForge 版本，实际以目标版本为准
  ordering="NONE"
  side="BOTH"

[[mixins]]                     # 声明 Mixin 配置（见第 4 节）
  config="examplemod.mixins.json"
```

关键差异（**分界版本：Forge 1.20.1 = `mods.toml` / NeoForge 1.21+ = `neoforge.mods.toml`**）：
- 依赖表字段不同：Forge 用 `mandatory=true`（布尔）；NeoForge 用 `type="required"`（字符串枚举）。
- NeoForge 额外支持 `[[accessTransformers]]`、`[[mixins]]`（含 `config`、`requiredMods`、`behaviorVersion`）、`[features.<modid>]`（如 `javaVersion="[17,)"`）、`[modproperties.<modid>]`。
- `version` 可写 `"${file.jarVersion}"`，由 manifest 注入。来源：[docs.neoforged.net/docs/gettingstarted/modfiles/]。

### 2.3 主类写法

**Forge 1.20.1**：
```java
@Mod("examplemod") // 必须与 mods.toml 中某个 modId 匹配
public class ExampleMod {
    public ExampleMod() {
        IEventBus modBus = FMLJavaModLoadingContext.get().getModEventBus();
        // 注册 DeferredRegister、监听 mod 总线事件
        ExampleItems.ITEMS.register(modBus);
    }
}
```
来源：[docs.minecraftforge.net/en/1.20.1/gettingstarted/modfiles/]。

**NeoForge 1.21+**：构造器可接收任意顺序的参数（`IEventBus` 模总线、`ModContainer`、`FMLModContainer`、`Dist`），且无参构造器只能有一个：
```java
@Mod("examplemod") // 必须与 neoforge.mods.toml 中 modId 匹配
public class ExampleMod {
    public ExampleMod(IEventBus modBus, ModContainer container) {
        ExampleItems.ITEMS.register(modBus);
        container.registerConfig(ModConfig.Type.COMMON, ExampleConfig.CONFIG_SPEC);
    }
}
```
来源：[docs.neoforged.net/docs/gettingstarted/modfiles/]。

> **⚠️ 待验证：`busGroup`**。任务描述提到「busGroup（NeoForge 1.21+）」，但 NeoForge 公开 API 中**并不存在名为 `busGroup` 的类或注解参数**。对应的真实概念是「双总线模型」：
> - **模总线（mod event bus）**：通过 `@Mod` 构造器的 `IEventBus` 参数获取，或在 `FMLJavaModLoadingContext.get().getModEventBus()` 取得；模总线事件实现 `IModBusEvent`（如 `FMLCommonSetupEvent`、`RegisterEvent`、`GatherDataEvent`、`RegisterPayloadHandlersEvent`）。
> - **游戏总线（game event bus）**：`NeoForge.EVENT_BUS`（Forge 为 `MinecraftForge.EVENT_BUS`）；游戏运行时事件（如 `LivingEvent.LivingJumpEvent`）发在此。
> 若「busGroup」指将多个 mod 分组到同一事件总线，NeoForge 暂无独立 API；请按上述双总线方式实现。

### 2.4 客户端 / 服务端分离

**Forge 1.20.1**（`DistExecutor` + `@Mod.EventBusSubscriber`）：
```java
// 在数据包处理器中把客户端逻辑丢到物理客户端执行
ctx.get().enqueueWork(() ->
    DistExecutor.unsafeRunWhenOn(Dist.CLIENT, () -> () -> ClientHandler.handle(msg, ctx)));
```
```java
@Mod.EventBusSubscriber(modid = "examplemod", value = Dist.CLIENT, bus = ModBusSubscriber.Bus.MOD)
public class ClientSetup {
    @SubscribeEvent
    public static void onClientSetup(FMLClientSetupEvent e) { /* 客户端初始化 */ }
}
```
来源：[docs.minecraftforge.net/en/1.20.1/networking/simpleimpl/]、[docs.minecraftforge.net/en/1.20.1/gettingstarted/modfiles/]。

**NeoForge 1.21+**：
```java
// 仅物理客户端的 @Mod 类
@Mod(value = "examplemod", dist = Dist.CLIENT)
public class ExampleModClient {
    public ExampleModClient(FMLModContainer container, IEventBus modBus, Dist dist) { /* ... */ }
}
```
```java
@EventBusSubscriber(value = Dist.CLIENT, modid = "examplemod")
public class ClientHandlers {
    @SubscribeEvent
    public static void onClientSetup(FMLClientSetupEvent e) { /* ... */ }
}
```
来源：[docs.neoforged.net/docs/gettingstarted/modfiles/]、[docs.neoforged.net/docs/concepts/events/]。

---

## 3. 核心系统（最小可运行代码块）

### 3.1 物品注册

**Forge 1.20.1**（返回 `RegistryObject<Item>`）：
```java
public static final DeferredRegister<Item> ITEMS =
        DeferredRegister.create(ForgeRegistries.ITEMS, ExampleMod.MOD_ID);

public static final RegistryObject<Item> EXAMPLE_ITEM = ITEMS.register(
        "example_item",
        () -> new Item(new Item.Properties().stacksTo(64)));
```
**NeoForge 1.21+**（推荐 `DeferredRegister.Items`，返回 `DeferredItem<Item>`）：
```java
public static final DeferredRegister.Items ITEMS = DeferredRegister.createItems(ExampleMod.MOD_ID);

public static final DeferredItem<Item> EXAMPLE_ITEM = ITEMS.registerItem(
        "example_item",
        props -> props.stacksTo(64));           // props 是 Item.Properties 的 unary operator

// 方块物品快捷方式
public static final DeferredItem<BlockItem> EXAMPLE_BLOCK_ITEM = ITEMS.registerSimpleBlockItem(
        "example_block", ExampleBlocks.EXAMPLE_BLOCK);
```
来源：[docs.neoforged.net/docs/items/index.md]、[docs.neoforged.net/docs/concepts/registries.md]。
**分界**：`DeferredRegister.create(ForgeRegistries.ITEMS, ...)` + `RegistryObject` 为 Forge 1.20.1；`DeferredRegister.createItems(MOD_ID)` + `DeferredItem` 为 NeoForge 1.21+（1.21.1 起即有 `DeferredRegister.Items`）。NeoForge 1.21+ 中 `Item.Properties` 必须调用 `setId(ResourceKey.create(Registries.ITEM, registryName))`，`DeferredRegister.Items` 内部已自动处理。来源：[docs.neoforged.net/docs/items/index.md]。

> 注册名（registry name）类型：**分界版本 1.21.11** NeoForge 将 `ResourceLocation` 重命名为 `Identifier`（直接替换类名即可，如 `Identifier.fromNamespaceAndPath(MOD_ID, "name")`）。1.21.1–1.21.8 仍用 `ResourceLocation.fromNamespaceAndPath(...)`。来源：对比 `nf-docs/versioned_docs/version-1.21.1` 与 `version-1.21.11` 的 registries.md。**为兼容所有 1.21.x，建议统一使用 `ResourceLocation`**。

### 3.2 方块注册 + 方块实体

**方块**（两种加载器写法一致，均用 `DeferredRegister`）：
```java
public static final DeferredRegister<Block> BLOCKS =
        DeferredRegister.create(BuiltInRegistries.BLOCK, MOD_ID); // NeoForge 1.21+
        // Forge 1.20.1 用: DeferredRegister.create(ForgeRegistries.BLOCKS, MOD_ID)

public static final DeferredBlock<Block> EXAMPLE_BLOCK = BLOCKS.register(
        "example_block",
        () -> new Block(BlockBehaviour.Properties.of().sound(SoundType.STONE)));
```
**方块实体类型（BlockEntityType）API —— 分界版本 1.21**（方块实体文档来源：[docs.neoforged.net/docs/blockentities/index.md]）：

*Forge 1.20.1*（使用 `BlockEntityType.Builder`）：
```java
public static final DeferredRegister<BlockEntityType<?>> BLOCK_ENTITY_TYPES =
        DeferredRegister.create(ForgeRegistries.BLOCK_ENTITY_TYPES, MOD_ID);

public static final RegistryObject<BlockEntityType<MyBE>> MY_BE = BLOCK_ENTITY_TYPES.register(
        "my_be",
        () -> BlockEntityType.Builder.of(MyBE::new, EXAMPLE_BLOCK.get()).build());
```
*NeoForge 1.21+*（直接 `new BlockEntityType<>(supplier, valid, blocks...)`）：
```java
public static final DeferredRegister<BlockEntityType<?>> BLOCK_ENTITY_TYPES =
        DeferredRegister.create(BuiltInRegistries.BLOCK_ENTITY_TYPE, MOD_ID);

public static final Supplier<BlockEntityType<MyBE>> MY_BE = BLOCK_ENTITY_TYPES.register(
        "my_be",
        () -> new BlockEntityType<>(
                MyBE::new,        // BlockEntityType.BlockEntitySupplier
                false,            // 仅 OP 才能加载 NBT
                EXAMPLE_BLOCK.get()));
```
方块类需实现 `EntityBlock` 并重写 `newBlockEntity`：
```java
public class MyBlock extends Block implements EntityBlock {
    public MyBlock(BlockBehaviour.Properties p) { super(p); }
    @Override public BlockEntity newBlockEntity(BlockPos pos, BlockState state) {
        return new MyBE(pos, state);
    }
}
```
方块实体读写（NeoForge 1.21+ 用 value I/O `loadAdditional`/`saveAdditional`）：
```java
@Override public void loadAdditional(ValueInput input) {
    super.loadAdditional(input);
    this.value = input.getIntOr("value", 0);
}
@Override public void saveAdditional(ValueOutput output) {
    super.saveAdditional(output);
    output.putInt("value", this.value);
}
```
来源：[docs.neoforged.net/docs/blockentities/index.md]。

### 3.3 创造模式物品栏（CreativeModeTab）

**Forge 1.20.1**：`CreativeModeTab` 注册用 `DeferredRegister.create(Registries.CREATIVE_MODE_TAB, MOD_ID)`，并覆写 `buildContents(CreativeModeTab.ItemDisplayParameters, CreativeModeTab.Output output)`（即 `output.accept(...)` 写法，1.20 起通用）。

**NeoForge 1.21+**（来源：[docs.neoforged.net/docs/items/index.md]）：
```java
public static final DeferredRegister<CreativeModeTab> CREATIVE_MODE_TABS =
        DeferredRegister.create(Registries.CREATIVE_MODE_TAB, MOD_ID);

public static final Supplier<CreativeModeTab> EXAMPLE_TAB = CREATIVE_MODE_TABS.register("example", () ->
        CreativeModeTab.builder()
            .title(Component.translatable("itemGroup." + MOD_ID + ".example"))
            .icon(() -> new ItemStack(EXAMPLE_ITEM.get()))
            .displayItems((params, output) -> {
                output.accept(EXAMPLE_ITEM.get());
                output.accept(EXAMPLE_BLOCK.get());
            })
            .build());
```
加入已有原版标签页用 `BuildCreativeModeTabContentsEvent`（仅逻辑客户端，监听模总线，`event.accept(...)`）。来源：[docs.neoforged.net/docs/items/index.md]。

### 3.4 数据生成（Datagen）

两种加载器都用 **GatherDataEvent**（在模总线 `@SubscribeEvent`）；vanilla 的 `RecipeProvider`/`LootTableProvider`/`TagsProvider`/`AdvancementProvider`/`ModelProvider` 子类基本相同。来源：[docs.neoforged.net/docs/concepts/registries.md]（其 `GatherDataEvent.Client` 示例）。

```java
@SubscribeEvent // 模总线
public static void gatherData(GatherDataEvent event) {
    DataGenerator gen = event.getGenerator();
    PackOutput out = gen.getPackOutput();
    CompletableFuture<HolderLookup.Provider> lookups = event.getLookupProvider();

    // 配方
    gen.addProvider(event.includeServer(), new MyRecipeProvider(out, lookups));
    // 战利品表
    gen.addProvider(event.includeServer(), new MyLootProvider(out, lookups));
    // 标签
    gen.addProvider(event.includeServer(), new MyBlockTagsProvider(out, lookups));
    // 进度
    gen.addProvider(event.includeServer(), new MyAdvancementProvider(out, lookups));
    // 模型（客户端）
    gen.addProvider(event.includeClient(), new MyItemModelProvider(out, event.getExistingFileHelper()));
}
```
配方示例（`RecipeProvider` 子类，使用 `RecipeOutput`）：
```java
public class MyRecipeProvider extends RecipeProvider {
    public MyRecipeProvider(PackOutput out, CompletableFuture<HolderLookup.Provider> lookups) {
        super(out, lookups);
    }
    @Override protected void buildRecipes(RecipeOutput out) {
        ShapedRecipeBuilder.shaped(RecipeCategory.MISC, EXAMPLE_ITEM.get())
            .define('x', Items.DIAMOND)
            .pattern("xx").pattern("xx")
            .unlockedBy("has_diamond", has(Items.DIAMOND))
            .save(out, new ResourceLocation(MOD_ID, "example_item_from_diamond"));
    }
}
```
> **⚠️ 待验证**：NeoForge 是否已将 `GatherDataEvent` 拆分为 `GatherDataEvent.Client` 与 `GatherDataEvent.Server` 两个事件（本地 1.21.11 文档在 registries.md 用了 `GatherDataEvent.Client`）。若拆分，则客户端模型 providers 应监听 `GatherDataEvent.Client`，服务端 providers 监听 `GatherDataEvent.Server`。写入示例按「单 GatherDataEvent + `includeServer()/includeClient()`」给出，这是 1.20.x 的稳定写法。

### 3.5 事件系统

- **双总线**：模总线（`@Mod` 构造器 `IEventBus` / `FMLJavaModLoadingContext`）vs 游戏总线（`NeoForge.EVENT_BUS` / `MinecraftForge.EVENT_BUS`）。模总线事件实现 `IModBusEvent`。来源：[docs.neoforged.net/docs/concepts/events/]。
- 注册方式：`IEventBus#addListener(...)`、`@SubscribeEvent`（实例或类）、`@EventBusSubscriber(modid=..., value=Dist.CLIENT)`（自动发现，方法须 static）。来源：同上。
- 取消/优先级：`ICancellableEvent#setCanceled`、`EventPriority`（`HIGHEST`→`LOWEST`）。
- 生命周期：`FMLConstructModEvent` → `RegisterEvent`（各 registry）→ `FMLCommonSetupEvent` → `FMLClientSetupEvent`/`FMLDedicatedServerSetupEvent` → `FMLLoadCompleteEvent`。来源：同上。

### 3.6 网络包

**Forge 1.20.1 —— SimpleChannel**（来源：[docs.minecraftforge.net/en/1.20.1/networking/simpleimpl/]）：
```java
public class PacketHandler {
    private static final String PROTOCOL = "1";
    public static final SimpleChannel INSTANCE = NetworkRegistry.newSimpleChannel(
            new ResourceLocation("mymod", "main"),
            () -> PROTOCOL,
            PROTOCOL::equals,
            PROTOCOL::equals);

    public static void register() {
        INSTANCE.registerMessage(0,
                MyMessage.class,
                (msg, buf) -> { buf.writeUtf(msg.text()); },
                buf -> new MyMessage(buf.readUtf()),
                (msg, ctx) -> {
                    ctx.get().enqueueWork(() -> { /* 主线程处理 */ });
                    ctx.get().setPacketHandled(true);
                });
    }
    // 发送：INSTANCE.sendToServer(new MyMessage("hi"));
    //      INSTANCE.send(PacketDistributor.PLAYER.with(serverPlayer), new MyMessage("hi"));
}
```
消息类示例：
```java
public class MyMessage {
    final String text;
    public MyMessage(String text) { this.text = text; }
}
```

**NeoForge 1.21+ —— CustomPacketPayload + PayloadRegistrar**（来源：[docs.neoforged.net/docs/networking/payload/]、[neoforged.net/news/20.4networking-rework]）：
```java
public record MyData(String name, int age) implements CustomPacketPayload {
    public static final CustomPacketPayload.Type<MyData> TYPE =
            new CustomPacketPayload.Type<>(ResourceLocation.fromNamespaceAndPath("mymod", "my_data"));

    public static final StreamCodec<ByteBuf, MyData> STREAM_CODEC = StreamCodec.composite(
            ByteBufCodecs.STRING_UTF8, MyData::name,
            ByteBufCodecs.VAR_INT, MyData::age,
            MyData::new);

    @Override public CustomPacketPayload.Type<? extends CustomPacketPayload> type() { return TYPE; }
}
```
```java
@SubscribeEvent // 模总线
public static void register(RegisterPayloadHandlersEvent event) {
    PayloadRegistrar reg = event.registrar("1");
    reg.playBidirectional(MyData.TYPE, MyData.STREAM_CODEC, ServerHandler::handle);
}
// 客户端处理器需在 @EventBusSubscriber(value=Dist.CLIENT) 类中用 RegisterClientPayloadHandlersEvent 注册
```
发送：`ClientPacketDistributor.sendToServer(new MyData(...))`；`PacketDistributor.sendToPlayer(player, new MyData(...))`。来源：[docs.neoforged.net/docs/networking/payload/]。

> **分界**：SimpleChannel 在 **NeoForge 20.4.x（对应 1.20.4）网络重构**后被视为废弃/移除，1.21+ 应使用 `CustomPacketPayload`。来源：[neoforged.net/news/20.4networking-rework]、[github.com/neoforged/NeoForge/pull/277]。因此：**1.20.1（Forge）= SimpleChannel；1.21+（NeoForge）= CustomPacketPayload**。

### 3.7 配置

**Forge 1.20.1 —— `ForgeConfigSpec`**（包 `net.minecraftforge.common.ForgeConfigSpec`）：
```java
public class ExampleConfig {
    public static final ForgeConfigSpec CONFIG_SPEC;
    public static final ForgeConfigSpec.ConfigValue<String> GREETING;
    static {
        ForgeConfigSpec.Builder b = new ForgeConfigSpec.Builder();
        GREETING = b.comment("问候语").define("greeting", "hello");
        CONFIG_SPEC = b.build();
    }
}
// 注册（在 mod 构造器或 FMLCommonSetupEvent）：
ModLoadingContext.get().registerConfig(ModConfig.Type.COMMON, ExampleConfig.CONFIG_SPEC);
```
**NeoForge 1.21+ —— `ModConfigSpec`**（包 `net.neoforged.neoforge.common.ModConfigSpec`，来源：[docs.neoforged.net/docs/misc/config/]）：
```java
public class ExampleConfig {
    public static final ModConfigSpec CONFIG_SPEC;
    public static final ModConfigSpec.ConfigValue<String> GREETING;
    static {
        ModConfigSpec.Builder b = new ModConfigSpec.Builder();
        GREETING = b.comment("问候语").define("greeting", "hello");
        CONFIG_SPEC = b.build();
    }
}
// 注册（在 @Mod 构造器，使用 ModContainer）：
container.registerConfig(ModConfig.Type.COMMON, ExampleConfig.CONFIG_SPEC);
```
类型：`STARTUP` / `CLIENT` / `COMMON` / `SERVER`（SERVER 会网络同步）。来源：[docs.neoforged.net/docs/misc/config/]。

### 3.8 能力 / 附件（Capability / Attachment）

**Forge 1.20.1 —— Capability**：通过 `CapabilityManager`/`ICapabilityProvider`/`LazyOptional`；物品用 `Item#initCapabilities`，方块实体用 `BlockEntity#getCapability`。**⚠️ 待验证具体 API 细节（与新附件系统差异较大）**，建议参考 Forge 官方 `capabilities` 文档页。

**NeoForge 1.21+ —— AttachmentType / DataAttachment**（来源：[docs.neoforged.net/docs/datastorage/attachments/]）：
```java
private static final DeferredRegister<AttachmentType<?>> ATTACHMENTS =
        DeferredRegister.create(NeoForgeRegistries.ATTACHMENT_TYPES, MOD_ID);

// 持久化（带序列化）
public static final Supplier<AttachmentType<Integer>> MANA = ATTACHMENTS.register(
        "mana", () -> AttachmentType.builder(() -> 0).serialize(Codec.INT.fieldOf("mana")).build());
// 不持久化
public static final Supplier<AttachmentType<SomeCache>> CACHE = ATTACHMENTS.register(
        "some_cache", () -> AttachmentType.builder(() -> new SomeCache()).build());

// 使用
int mana = player.getData(MANA);
player.setData(MANA, mana + 10);
```
> **分界**：Forge 1.20.1 用 `Capability` 体系；NeoForge 1.21+ 用 `AttachmentType`（更简洁，基于 vanilla data attachment）。来源：[docs.neoforged.net/docs/datastorage/attachments/]。

### 3.9 客户端渲染

**方块/物品模型 JSON 路径**：`assets/<modid>/models/item/<name>.json` 与对应 `assets/<modid>/textures/item/<name>.png`。方块额外有 `models/block/<name>.json`。来源：[docs.neoforged.net/docs/resources/client/models/items/]。

**物品模型定义（⚠️ 分界版本 1.21.4）**：
- *1.21.4 之前*（含 Forge 1.20.1、NeoForge 1.20.2–1.21.3）：物品模型就是 `models/item/<name>.json`：
  ```json
  {
    "parent": "item/generated",
    "textures": { "layer0": "examplemod:item/example_item" }
  }
  ```
  物品按注册名自动关联该模型，无单独定义文件。
- *1.21.4 及之后*（NeoForge，跟随 vanilla 1.21.4 的「Item Model Definition」系统）：新增 `assets/<modid>/items/<name>.json` 客户端物品定义（来源：[docs.neoforged.net/docs/resources/client/models/items/]）：
  ```json
  {
    "model": {
      "type": "minecraft:model",
      "model": "examplemod:item/example_item"
    }
  }
  ```
  同时 `models/item/example_item.json` 仍描述立方体模型本身。复合/条件/选择模型（`minecraft:composite`/`condition`/`select`/`range_dispatch`）详见上述文档页。

**BlockEntityRenderer（BER）**：实现 `BlockEntityRenderer<MyBE>`，在模总线客户端事件 `RegisterBlockEntityRenderersEvent` 中用 `event.register(EXAMPLE_BE.get(), MyBER::new)` 注册（NeoForge 1.21+；Forge 1.20.1 用 `BlockEntityRenderers.register(...)`）。来源：[docs.neoforged.net/docs/blockentities/index.md]（BER 链接）。

### 3.10 本地化（lang）

- 路径：`assets/<modid>/lang/en_us.json`（及其他语言如 `zh_cn.json`）。
- 键格式（vanilla 约定，两种加载器通用）：
  - 物品：`"item.<modid>.<registryname>": "显示名"`
  - 方块：`"block.<modid>.<registryname>": "显示名"`
  - 创造标签页：`"itemGroup.<modid>.<tab>": "显示名"`
  - 也可通过 item 的 `overrideDescription` / `Item.Properties#setId` 关联的 `DataComponents#ITEM_NAME` 控制。
- 来源：NeoForge 文档在 items/index.md 提到 `Component.translatable("itemGroup." + MOD_ID + ".example")` 作为标签页标题键。

---

## 4. Mixin（Forge / NeoForge 侧）

- **本质一致**：Forge 与 NeoForge 使用的都是 **SpongePowered Mixin**（与 Fabric 同一套），差别仅在**声明位置**，不在 mixin 语法本身。来源：[beishanair.github.io/2025/07/22/nf121/start]（「NeoForge 的 Mixin 用的其实也就是 Fabric 的，来自 Sponge 项目」）。
- **mixin 配置 `mixins.json`**（放在 `src/main/resources/`，与 Fabric 相同）：
  ```json
  {
    "plugin": "com.example.mymod.mixin.MyMixinPlugin",
    "mixins": [ "MyMixin" ],
    "client": [ "MyClientMixin" ],
    "injectors": { "defaultRequire": 1 }
  }
  ```
- **声明差异（分界：Forge 1.20.1 = `mods.toml`；NeoForge 1.21+ = `neoforge.mods.toml`）**：
  - Forge：在 `mods.toml` 追加 `[[mixins]] config = "examplemod.mixins.json"`（字段为 `config`）。来源：[docs.minecraftforge.net/en/1.20.1/gettingstarted/modfiles/]（features/services 段；mixin 表结构与 NeoForge 类似）。
  - NeoForge：在 `neoforge.mods.toml` 追加（来源：[docs.neoforged.net/docs/gettingstarted/modfiles/]）：
    ```toml
    [[mixins]]
      config = "examplemod.mixins.json"
      # requiredMods = ["sodium"]   # 可选：仅当这些 mod 存在时应用
      # behaviorVersion = "0.17.1"  # 可选：匹配 fabric mixin 行为版本
    ```
- **构建差异**：
  - *Forge 1.20.1*：通常需要 `org.spongepowered:mixingradle` 插件 + `annotationProcessor` 处理 `@Mixin` 注解，并在 `runs` 中设置 `mixin.env.refMapRemappingFile` 指向 `build/createSrgToMcp/output.srg`。来源：[CSDN build.gradle 示例]（含 `mixin.env.remapRefMap=true`、`refMapRemappingFile`）。
  - *NeoForge 1.21+（ModDevGradle）*：Mixin 支持**内建**，无需额外插件；只要 `neoforge.mods.toml` 声明了 `[[mixins]]` 且 `mixins.json` 存在即可。来源：[docs.neoforged.net/docs/gettingstarted/modfiles/]、[docs.neoforged.net/toolchain/docs/plugins/mdg]（interfaceInjection 说明「runtime 仍需 Mixin/Coremod」）。

---

## 5. 调试与发布

### 5.1 日志与混淆映射

- **Mojang 官方 mapping**：ForgeGradle 的 `minecraft {}` 中 `mappings channel: 'official', version: '1.20.1'`（或默认）。NeoForge 默认即 Mojang 官方 mapping。
- **Parchment（社区参数名/JavaDoc）**：
  - Forge 1.20.1：`plugins { id 'org.parchmentmc.librarian.forgegradle' version '1.+' }` + `mappings channel: 'parchment', version: '2023.09.03-1.20.1'`。来源：[CSDN build.gradle]、[gitee virtuarealcraft build.gradle]。
  - NeoForge 1.21+：在 `gradle.properties` 设 `neoForge.parchment.minecraftVersion=1.21` 与 `neoForge.parchment.mappingsVersion=2024.06.23`（去掉 `v` 前缀）。来源：[docs.neoforged.net/toolchain/docs/plugins/mdg]。
- **日志级别**：运行配置中设 `forge.logging.console.level=debug`（Forge）或 `systemProperty 'forge.logging.console.level', 'debug'`（NeoForge MDG `runs.configureEach`）。来源：Forge MDK 示例、[docs.neoforged.net/toolchain/docs/plugins/mdg]。

### 5.2 嵌入依赖（jarJar / Shadow）

- **NeoForge（ModDevGradle）jarJar**：在 `dependencies` 用 `jarJar` 配置，插件会自动处理 metadata：
  ```gradle
  dependencies { jarJar("com.example:lib:1.0") { jarJarRelocate "com.example.lib", "yourmod.shadow.lib" } }
  ```
  来源：[docs.neoforged.net/toolchain/docs/plugins/mdg]（"External Dependencies: Runs" 段说明 jarJar）。
- **Forge（ForgeGradle）jarJar**：ForgeGradle 5.x 也支持 `jarJar` 配置 + `jarJarRelocate`，写法类似。来源：[CSDN 等构建示例]。**⚠️ 待验证 Forge 1.20.1 的确切 jarJar DSL 名称**。
- **Shadow**：如需重定位（relocate）第三方库，可用 `com.gradleup.shadow` 插件打 fat jar；但 mod 通常优先用 jarJar 以避免与其他 mod 冲突。

### 5.3 发布到 CurseForge / Modrinth 的元数据

- **基础元数据（已在 mods.toml / neoforge.mods.toml 提供）**：`modId`、`version`、`displayName`、`description`、`authors`、`license`、`updateJSONURL`、`logoFile`。这些字段同时被 Modrinth/CurseForge 的自动检测读取。
- **Modrinth**：推荐用 `com.modrinth.minotaur` Gradle 插件，或在 Modrinth 网页填写：`name`、`version_number`、`version_type`（release/beta/alpha）、`game_versions`（如 `["1.21.1"]`）、`loaders`（如 `["neoforge"]`/`["forge"]`）、`dependencies`（含 `neoforge`/`forge` 版本约束，对应 mods.toml 的 `[[dependencies]]`）。**⚠️ 待验证：Minotaur 插件具体 task 名与字段**。
- **CurseForge**：用 `com.matthewprenger.cursegradle` 插件，需 `id`、`releaseType`、`addGameVersion("1.21.1")`、`addModLoader("NeoForge")`/`("Forge")`、`changelog`、`dependencies`。**⚠️ 待验证：CurseGradle 最新可用版本与 ModLoader 字符串**。
- **updateJSONURL**：指向一个符合 Forge/NeoForge update checker 格式的 JSON，用于游戏内更新提示。来源：[docs.minecraftforge.net/en/1.20.1/gettingstarted/modfiles/]（updateJSONURL 字段）。

---

## 6. ⚠️ 待验证清单（汇总）

1. **MC 26.1 / Java 25 的 NeoForge 支持**：文档仓最新仅到 1.21.11，NeoForge 是否已发布 26.x 构建未确认。
2. **各 MC 版本对应的精确 NeoForge 构建号**（如 1.21.1 → `21.1.x`）：以 projects.neoforged.net 为准。
3. **ModDevGradle 精确版本号**：门户显示 2.0.141，文档示例 1.0.11，请取最新稳定。
4. **`pack.mcmeta` 的 `pack_format` 数字**：跨版本不同，建议直接复制 MDK 自带文件。
5. **Forge 1.20.1 的 `GatherDataEvent` 是否拆分 Client/Server**。
6. **Forge 1.20.1 `BlockEntityType.Builder` 与 Capability 细节**、jarJar DSL 名称。
7. **`busGroup`**：NeoForge 公开 API 无此名称，疑为双总线模型之误称。
8. **CurseForge / Modrinth Gradle 插件的具体 task 与字段名**：随插件版本变化，需对照插件 README。
9. **国内 Maven 镜像的具体可用地址**：Forge/NeoForge 专用仓库多无镜像，建议官方源 + 代理。
10. **物品模型定义 `items/*.json` 的精确 NeoForge 引入版本**：随 vanilla 1.21.4 引入，NeoForge 同步；1.21.1–1.21.3 边界待逐一核对。

---

## 7. 参考来源（权威）

- NeoForge 官方文档：https://docs.neoforged.net/ （含 /docs/gettingstarted/modfiles、/docs/concepts/registries、/docs/items、/docs/blockentities、/docs/concepts/events、/docs/datastorage/attachments、/docs/misc/config、/docs/networking/payload、/docs/resources/client/models/items、/toolchain/docs/plugins/mdg）
- NeoForge 文档源码仓（含各版本快照）：https://github.com/neoforged/Documentation （本地 `nf-docs`，版本 `version-1.21.1`…`version-1.21.11`）
- Forge 官方文档（1.20.1）：https://docs.minecraftforge.net/en/1.20.1/ （gettingstarted、modfiles、networking/simpleimpl、items、config）
- Forge 文档源码仓：https://github.com/MinecraftForge/Documentation （本地 `forge-docs`，内容为 1.21.x 当前版）
- ModDevGradle 插件：https://projects.neoforged.net/neoforged/ModDevGradle 、https://github.com/neoforged/ModDevGradle
- NeoForge MDK：https://github.com/neoforged/MDK 、https://github.com/NeoForgeMDKs
- NeoForge 网络重构说明：https://neoforged.net/news/20.4networking-rework 、https://github.com/neoforged/NeoForge/pull/277
- Java 版本要求社区汇总：https://modready.gg/guides/minecraft-java-version-requirements 、https://lordhosting.fr/en/blog/minecraft-java-edition/quelle-version-java-minecraft 、https://g-portal.com/wiki/en/minecraft-neoforge/
- Mixin 说明（NeoForge 用 Fabric/Sponge Mixin）：https://beishanair.github.io/2025/07/22/nf121/start
- ForgeGradle 实战示例（CSDN / Gitee，含 `5.1.+`、`parchment`、`mixin` 配置）：https://ask.csdn.net/questions/9635969 、https://gitee.com/a-little-white-black-rice/virtuarealcraft/blob/v1.0.0-SNAPSHOT-0.1.2/build.gradle

---

## 8. 26.1 API 变更速查（都是编译期/运行期实测撞出来的）

做「枪械与工具」那一轮（2026-10-24）连着踩了 5 个改名/改签名，全在 26.1.2 上验证过：

| 旧写法（1.20~1.21.4） | 26.1 的写法 | 症状 |
|---|---|---|
| `net.minecraft.world.item.Tiers.IRON` | `ToolMaterial.IRON`（`Tiers` 类已删，`ToolMaterial` 变成带常量的 record） | 编译报 `cannot find symbol: Tiers` |
| `player.getCooldowns().isOnCooldown(item)` / `addCooldown(item, n)` | 收 **`ItemStack`**：`isOnCooldown(stack)` / `addCooldown(stack, n)` | 编译报参数不匹配 |
| `player.displayClientMessage(c, true/false)` | `player.sendOverlayMessage(c)`（动作栏）/ `sendSystemMessage(c)`（聊天） | 编译报找不到方法 |
| `ProjectileUtil.getEntityHitResult(level, entity, from, to, box, pred)` | 多了第 7 个参数 `entityMargin`：`(..., pred, 0.3f)` | 编译报 no suitable method |
| `BuiltInRegistries.ITEM.get(id)` 当 `Item` 用 | `getOptional(id).orElse(null)`（`get` 返回 `Holder.Reference`） | 类型不匹配 |

另外两条**不报错但结果错**的坑：

1. **`Item.Properties.axe/pickaxe/shovel/sword(material, dmg, speed)` 会连做三件事**：
   挂 `TOOL` 组件、**用材质耐久覆盖你之前设的 `durability()`**、**在你给的攻击力上再叠一次材质的
   `attackDamageBonus`**（26.1 的铁实测多 3.0，写 8.57 实际 11.57）。
   → 耐久要写在 `axe()` **之后**；攻击力最稳的做法是 `props.attributes(自己算的)` 整体替换。
2. **切换手持物品的当帧，`player.getAttributeValue(ATTACK_DAMAGE)` 还是旧值**（读到 1.0）。
   测量装备加成要走**物品组件** `stack.get(DataComponents.ATTRIBUTE_MODIFIERS)` 自己累加，
   或者隔一 tick 再读。

> 还有一条与 API 无关但与"截图验收"强相关：**`Options.hideGui`（F1）会把第一人称的手/手持物
> 一起藏掉** —— 自动化截图里"没有手、没有准星、没有物品栏"多半是它，不是渲染坏了。

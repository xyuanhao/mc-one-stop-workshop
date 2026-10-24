# 实战拆解：真实 mod 的可复用架构（The Fungal Infection 整合包案例）

> 来源：对 `D:\.minecraft\versions\The Fungal Infection\mods`（194 个 jar，1.20.1 Forge + Connector 混装 Fabric）直接解包的观察。
> 用途：写"内容型 mod"时参考这些被市场验证的架构决策。原始全景报告在工作区 `mc-mod-research/整合包研究-The Fungal Infection.md`。

---

## 1. 数据驱动设计模式（tacz / Spore 实证）

**为什么重要**：AI 生成的 mod 如果只写死内容，整合包作者没法扩展。数据驱动 = 把"内容"与"逻辑"分离，**注册代码只做加载器，数值/配方/转化规则全放 JSON**。

### 1.1 tacz 的 index/data/display 三件套（枪械、装备、任何"数值系统"）

每把枪 = 3 个 JSON，Java 代码零硬编码：

```
data/<ns>/index/guns/ak47.json     # 索引：name/display/data 引用 + type + sort
data/<ns>/data/guns/ak47_data.json # 数值：弹药/弹容/射速/子弹伤害/弹道衰减/后座
assets/<ns>/display/guns/ak47_display.json  # 视觉引用（模型/动画槽位）——放 assets 侧可被资源包覆写
```

index 指向 data 与 display，加载器按 id 组装。Java 侧只需要一个 `registerGun(id)` 读 JSON。

**关键技巧——自定义 JSON 解析器支持注释**：tacz 的 JSON 允许 `//` 注释（原版 JSON 不允许）。做法：读入文本 → 正则剥离 `//` 行注释和尾注释 → 再交给 Gson。数据量大时可读性收益巨大，实现只要十几行：

```java
// 示意写法：剥注释后再解析
String raw = Files.readString(path);
raw = raw.replaceAll("(?m)//[^\"\\n]*$", "");      // 去掉行注释（不在字符串内的）
// 然后正常 new Gson().fromJson(raw, XxxData.class)
// 注意：此正则不处理字符串值里含 "//" 的边角情况，够用即可；严谨版需逐字符扫描字符串状态
```

### 1.2 Spore 的转化系统（"状态转换"类规则）

感染 mod 把"谁变成谁/什么方块被感染"放**非标准 data 目录**：

```
data/spore/
├── spore_mob_conversion/    # 生物转化定义
├── spore_block_conversion/  # 方块转化（感染蔓延）
├── spore_cdu_conversion/    # 其他转化
└── weapon_attributes/       # 武器属性 JSON（cleaver/greatsword...）
```

加载方式：`GatherDataEvent`/`AddReloadListenerEvent` 注册一个 `SimpleJsonResourceReloadListener`（Forge）/ `ReloadListener`，监听 `data/<ns>/<自定义目录>`，把 JSON 反序列化成规则表。**非标准目录的好处**：数据包作者可以 `data/其他包/...` 加文件覆写，游戏内热重载（`/reload`）即生效，不碰代码。

### 1.3 什么时候值得数据驱动

| 场景 | 建议 |
|---|---|
| 武器/装备数值多、要调平衡 | ✅ tacz 式三 JSON |
| 有"转化/进化/蔓延"类状态规则 | ✅ Spore 式独立目录 |
| 一套代码想覆盖几十个同类内容（矿物、作物…） | ✅ 注册循环 + tag/JSON 驱动 |
| 只有一个方块一个物品的演示/工具 mod | ❌ 过度设计，直接注册即可 |
| 需要强类型逻辑、服务端权威计算 | 折中：核心逻辑在 Java，只把"可调参数"外置 JSON |

---

## 2. 轻量联动：不声明依赖，给第三方 mod 塞配方（Spore 实证）

Spore 的 mods.toml **没有** create/farmersdelight 依赖，但 jar 里有：

```
data/create/recipes/crushing/amethyst_dust.json   # 给 Create 加粉碎配方
data/farmersdelight/recipes/xxx.json             # 给农夫乐事加配方
```

原理：datapack 目录按 namespace 分发，目标 mod 不存在时目录被忽略、**不报错**；存在时目标 mod 自己的配方加载器会读到。副作用：若目标 mod 的配方格式变了会静默无效（datapack 本身无版本校验），发布前需用该 mod 实机验证。

**给 AI 写码的规则**：给第三方 mod 做联动，优先塞 datapack 配方/标签，而不是声明 `[[dependencies]]` 强依赖或写 @Mod.EventBusSubscriber 集成代码。只有需要调用对方 API（多方块、事件钩子）时才做代码集成。

---

## 3. 大工程包组织实证

### 3.1 Create（2687 类）——All* 聚合注册 + 大模块分层

```
com/simibubi/create/
├── AllBlocks.class / AllItems / AllBlockEntityTypes / AllCreativeModeTabs / AllEnchantments ...
│      # 根包一排 All* 静态聚合类，每类 = 一个注册维度，全部内容条目集中可见
├── content/          # 玩法内容（1774 类）—— 最大头
├── foundation/       # 基础框架（404）：渲染/网络/方块类工具
├── infrastructure/   # 基础设施（156）：存档/命令/世界生成
├── compat/           # 其他 mod 集成（138）
├── api/              # 对外 API（122）
└── impl/             # 内部实现
```

实证结论：**注册条目别散落在功能类里**，集中到 `ModBlocks/ModItems/ModBlockEntities`（或 Create 的 All*）静态类；内部再按 content/foundation 分层。参考本 skill `工程最佳实践.md` 的命名铁律。

### 3.2 Spore（1063 类）——按内容层级分包子包

```
com/Harbinger/Spore/
├── Sentities/          # 实体：按"物种层级"分子包
│   ├── BaseEntities/  BasicInfected/  EvolvedInfected/  Organoids/  Calamities/  Variants/
│   └── AI/ Utility/ MovementControls/ Projectile/
├── Sitems/ Sblocks/ SBlockEntities/  # 物品/方块/方块实体
├── Screens/ Recipes/ Senchantments/ Effect/ Particles/ sEvents/
├── Client/             # 客户端独立：Models(162)/Renderers(122)/Layers/ArmorParts/Animations
└── Core/               # 注册核心
```

实证结论：**生物系内容 mod 按"进化/层级"分包**（而非一锅粥 Entity 目录），客户端模型与逻辑严格分家。类名前缀（S = Spore）防 IDE 里和原版/库类混淆，大 mod 可借鉴。

---

## 4. 外来 mod 鉴别（反编译/研究/迁移前先认清出身）

| 类型 | 特征 | 处理建议 |
|---|---|---|
| **MCreator 产物** | 包名 `net/mcreator/<modid>/`；注册类扁平 `init/XxxModItems`、`init/XxxModTabs`（无 DeferredRegister）；GUI 在 `client/gui/XxxGuiScreen`，模型 `client/model/Modelxxx` | 代码质量普遍低、命名怪；**要迁移到手写工程建议整体重写而非逐行移植** |
| **lowcodefml 数据包** | 0 个 class；mods.toml `modLoader="lowcodefml"`；纯 data/ 结构 | 本质是数据包，无需迁移——直接当 datapack 用或复制 data 内容 |
| **Connector 转译（Fabric→Forge）** | mods 目录有 `Connector-*.jar` + `.connector/` 缓存目录（`*_mapped_srg_*.jar`） | 该 Fabric mod 实际以转译 jar 运行；研究源码应找 Fabric 原版 jar/仓库 |
| 手写 Forge mod | mods.toml `modLoader="javafml"`；注册走 DeferredRegister；包名规范 | 正常研究/参考 |

**快速鉴别命令**（解包看包名即可，无需反编译）：
```bash
unzip -l mod.jar | grep -E "net/mcreator|init/.*Items" | head      # MCreator?
unzip -p mod.jar META-INF/mods.toml | grep modLoader               # lowcodefml?
unzip -l mod.jar | head -30                                        # 结构总览
```

---

## 5. Sinytra Connector 混装原理（遇到"Fabric mod 在 Forge 包"时）

- Connector 把 Fabric mod 的映射转成 SRG/官方名，**缓存在 `mods/.connector/`**（能看到 `*_mapped_srg_1.20.1.jar` + `.input` 标记）
- 需要 `fabric-api` 打底 + `ConnectorExtras` 补桥
- 启示：整合包研究/调试时，若某个 Fabric mod 行为异常，先看它是否被 Connector 转译、版本是否在兼容表内；**给用户出方案时优先推荐原生加载器版本**

---

## 6. 整合包还揭示的 1.20.1 生态事实

- 库层极厚：Bookshelf / Kiwi / Zeta / PuzzlesLib / Moonlight / Architectury / Cloth Config 是高频前置——做 1.20.1 内容 mod 选库时用这些"事实标准"存活率最高
- 客户端与性能 mod 占近 1/3：视觉/声学/动画（Player Animator、NotEnoughAnimations、Sound Physics、EnhancedVisuals）+ 优化（Radium/Xenon/Oculus/ModernFix/FerriteCore/ServerCore）
- 主题整合包的核心内容 mod 普遍 1000+ 类、20MB+：AI 生成 mod 时若目标是"整合包级内容"，应规划模块化 + 数据驱动，否则后期扩展会卡死

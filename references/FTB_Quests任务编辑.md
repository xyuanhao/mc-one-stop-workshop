# FTB Quests 任务书编辑指南（**语法层**：字段怎么写）

> **只讲"怎么写"，不讲"写什么"**。写什么（每个模组都要有落点 / 四阶段递进 / 详细度与奖励各三档）
> → 看同目录的 `任务书设计.md`；写完对账用 `scripts/任务书覆盖度.py`。
>
> ## ⚠️ 版本警告（2026-09-13 补）
> **本文写于 FTB Quests 1.20.1 时代，SNBT 相关部分只适用于 ≤ 1.21.x。**
> **26.1.2 起 FTB Quests 全面改用 JSON5**（官方 changelog：`[26.1.2.1] All quest and progress data
> is now saved in Json5 format; SNBT is no longer used`），并且**文本从章节文件迁到了独立的 lang 翻译表**
> （章节文件里的 `title`/`subtitle`/`description` 会被忽略，只认结构字段）。
>
> 写 26.x 任务书请以 `SKILL.md` 的这两节为准：
> - 「FTB Quests 任务书：group 必须写「组 id」；26.1.2 起数据格式转 JSON5」
> - 「FTB Quests 26.1.2：文本在 lang 里，不在章节文件里」
>
> 本文的**章节 / 任务类型 / 奖励语义**部分（哪些任务类型存在、图标怎么选、奖励怎么发）仍然适用。

> 整合包「任务书」= FTB Quests mod 的数据驱动任务系统。**不用在游戏里点，改文件就能写任务**。
> 实测对象：The Fungal Infection 整合包（ftb-quests-forge-2001.4.18 / MC 1.20.1 Forge），
> 7 章节 / 53 任务 / 6 种任务类型。

## 1. 文件在哪 & 三个文件各管什么

```
config/ftbquests/
└── quests/
    ├── data.snbt              # 全局设置：书标题/图标/默认形状/进度模式/通用 flag
    ├── chapter_groups.snbt    # 章节分组（书页里的大标签页）
    └── chapters/*.snbt        # 每章一个文件（书页里的小标签），核心内容全在这
```

**给整合包做任务 = 写 chapters/ 下的 .snbt + 在 chapter_groups / data 里登记。**

## 2. SNBT 语法速览

SNBT = Stringified NBT（Minecraft 的 NBT 文本格式），要点：

- 顶层 `{}` 包整份文件；字段 `key: value`；**字段间不需要逗号**（有也行）
- 字符串必须**双引号**（单引号是转义字符串）
- 字符串数组：`["一行" "第二行"]`（行间无逗号）——description 就是这种
- 数字带后缀：`x: 0.0d`（double）、`version: 13`（int）
- 布尔：`true` / `false`
- **16 位大写十六进制 id**（如 `"767B85BB61BD91E3"`）——任务/章节/奖励全靠它互相引用

## 3. data.snbt —— 全局设置（关键字段）

```snbt
{
	default_autoclaim_rewards: "disabled"   # 奖励领取方式：disabled/manual/auto
	default_quest_shape: "circle"           # 全局默认任务形状
	icon: "spore:fungal_stem_sapling"       # 书封面图标（物品 id 或 custom_icon）
	title: "&c真菌感染"                      # 书名（& 颜色代码）
	version: 13                             # 每次大改 +1，触发旧进度兼容
	progression_mode: "linear"              # linear=按依赖解锁 / flexible=不限顺序
}
```

## 4. chapter_groups.snbt —— 分组

```snbt
{
	chapter_groups: [
		{ id: "293A24E038372106", title: "&6图鉴" }
		{ id: "2CAC297F84835B88", title: "&6藏书" }
	]
}
```
每个 chapter 文件里的 `group: "组id"` 字段把章归到某个组；空串 = 不分组。

## 5. chapter 文件结构（核心）

```snbt
{
	filename: "the_infection"          # 文件名（不用改）
	group: ""                          # 所属组 id（chapter_groups 里的）
	icon: "spore:growth_mycelium"      # 章节图标
	id: "767B85BB61BD91E3"             # 章节唯一 id
	order_index: 0                     # 章排序
	title: " &e菌丝蔓延"                # 章标题
	quests: [ ... ]                    # ★ 任务数组，正文
}
```

## 6. quest 任务 —— 字段大全

```snbt
{
	# —— 展示 ——
	title: "&e第 1 页：&7趁手的武器"       # 标题（& 颜色码）
	subtitle: "&8点击阅读"               # 副标题（小字）
	description: [                      # 正文（数组=多行，& 颜色码）
		"&7第一行说明"
		"&7第二行说明"
	]
	icon: "minecraft:paper"              # 图标：物品 id 字符串 或 NBT 对象（见 §8）
	shape: "hexagon"                     # 形状：circle/square/hexagon/gear/star/diamond...
	x: -1.5d                             # ★ 网格坐标（任务在书页上的位置）
	y: 0.0d
	size: 1.5d                           # 可选：图标放大倍数

	# —— 逻辑 ——
	id: "63DFDFE15ED90228"               # 任务唯一 id（被依赖方引用）
	dependencies: ["3BB55AFEF01456A8"]   # ★ 前置任务 id 数组（做了才能做这个）
	optional: true                       # 可选任务（不影响过关）
	hide_details_until_startable: true   # 条件未满足前隐藏细节
	require_sequential_tasks: false      # 多个任务必须按序完成

	# —— 完成条件 ——（可多个，全完成才算过）
	tasks: [ { task 对象 } ]

	# —— 奖励 ——（可选）
	rewards: [ { reward 对象 } ]
}
```

## 7. tasks 任务类型 —— 实测 6 种写法

| 类型 | 干什么 | 关键字段 | 实测例 |
|---|---|---|---|
| `"item"` | 拥有/提交物品 | `item`（物品 NBT 或 id 字符串） | 见下 |
| `"checkmark"` | 点一下就完成（阅读/剧情节点） | `title` | `{title:"继续", type:"checkmark"}` |
| `"kill"` | 击杀生物 | `entity: "minecraft:ender_dragon"` | `{entity:..., type:"kill"}` |
| `"dimension"` | 抵达某维度 | `dimension: "minecraft:the_end"` | `{dimension:..., type:"dimension"}` |
| `"structure"` | 找到结构 | `structure: "underground_bunkers:underground_bunker"` | 结构 mod 注册的结构 id |
| `"custom"` | 交给 mod 脚本判定 | `type:"custom"` + 事件 | 依赖 FTB Quests API |

**官方任务类型全集**（来源：FTB 生态 wiki 汇总 + 实测，比本包用到的 6 种更多）：
item（持有/提交物品）、checkmark（点击确认，剧情/阅读节点）、kill（击杀实体）、
advancement（完成原版/模组进度）、structure（找到结构）、observation（注视某方块/实体）、
dimension（抵达维度）、biome（进入生物群系）、xp_levels（消耗经验等级）、
location（到达坐标）、stat（统计值达标）、fluid / energy（提交流体/能量）、custom（脚本判定）。

**官方奖励类型全集**：
item（给物品，可带 `components`/NBT）、choice（玩家从列表选一）、random（按权重从 Reward Table 抽）、
loot_table（战利品箱/表）、command（执行服务端命令）、xp / xp_levels（经验）、
advancement（给进度）、toast（弹通知）、stage（解锁 Game Stages）。
Reward Tables 在书内 **Settings → Reward Tables** 管理，可一键转成 **Loot Crate**（实体奖励箱）。

**item 任务两种写法**：

```snbt
# 简写：只要物品 id（默认 Count 1）
tasks: [{
	id: "08E2195864101790"
	item: "immersiveengineering:casull"
	type: "item"
}]

# 完整 NBT：带数量/附魔/组件（Forge 的老 NBT 格式）
tasks: [{
	id: "61052CBC2430FE9D"
	item: {
		Count: 1
		id: "immersiveengineering:revolver"
	}
	type: "item"
}]
```

其他常用（未在本包出现但 FTB Quests 标准类型）：`"location"`（到达坐标）、
`"observation"`（观察方块）、`"stat"`（统计达成）、`"fluid"`（提交流体）、
`"energy"`（提交 FE）。26.x 的 NeoForge 版 item NBT 改用 Data Components（`components`），写的时候注意版本。

## 8. 图标系统（最容易抄错的点）

图标三种形态：

```snbt
icon: "minecraft:paper"                        # ① 物品 id 字符串——最常见
icon: { Count: 1, id: "minecraft:paper" }      # ② 物品 NBT 对象
icon: {                                        # ③ 自定义贴图（不存在的物品也能当图标）
	Count: 1
	id: "ftbquests:custom_icon"
	tag: {
		Icon: "minecraft:item/enchanted_book"    # ★ 纹理路径：assets/<mod>/textures/ 之后的路径
	}
}
```

`Icon` 路径规则：`<modid>:<textures/ 之后的路径>` 去掉 `.png`——
- `minecraft:item/enchanted_book` → `assets/minecraft/textures/item/enchanted_book.png`
- `spore:block/fungal_p_sap` → `assets/spore/textures/block/fungal_p_sap.png`

想用自己 mod 的贴图当图标就这么指；图标模糊就找 `textures/block|item/` 下真实存在的文件。

**shape 形状语义**（官方 UI 约定，别乱选）：
`none`=起点任务、`square`=标准、`rsquare`=中间步骤、`circle`=默认、
`hexagon`=合成/建造类、`octagon`=探索类、`diamond`=重要里程碑、`pentagon`=重大成就、`gear/star`=装饰性强调。

## 8.5 本地化与富文本（大批量做包必用）

任务文案可以**外挂语言文件**，改文案不用动任务结构（多语言包更是必须）：

```
config/ftbquests/quests/lang/en_us/chapters/<章节名>.snbt
config/ftbquests/quests/lang/zh_cn/chapters/<章节名>.snbt
```
格式：`quest.{QUEST_ID}.{field}`（field = title / subtitle / description）：

```snbt
{
	quest.27B728A9C5D03C1A.title: "&e菌丝蔓延"
	quest.27B728A9C5D03C1A.description: ["&7第一行" "&7第二行"]
}
```

**正文富文本语法**（官方支持，写法直接塞进字符串）：
- `&c &e &7…` 颜色码、`&l &o &n &m &k &r` 格式码（同 §10）
- `\\n` —— 强制换行
- `{@pagebreak}` —— 分页（长描述分多页显示）
- `{image:path width:X height:Y align:center}` —— 嵌入图片（配合书内 images 用）

## 8.6 官方命令（做包时省大量时间）

| 命令 | 用途 |
|---|---|
| `/ftbquests editing_mode [true/false]` | 开关 GUI 编辑模式（**改任务第一步**，单人无需作弊） |
| `/ftbquests reload` | 从文件重载任务数据（改完 .snbt 不用重进世界） |
| `/ftbquests change_progress` | 强制完成/重置某个任务或章节（**测试任务链必备**） |
| `/ftbquests generate_chapter from_inventory` | **从背包物品批量生成章节**（item 任务一键铺） |
| `/ftbquests item_info` | 显示手持物品的 ID / NBT / Tags（写 item 任务时查 ID 用） |
| `/ftbquests export_reward_table_to_csv` | 导出奖励表为 CSV 批量编辑 |
| `/ftbquests delete_empty_reward_tables` | 清理空奖励表 |

配置文件另有 `ftbquests-server.snbt`（团队奖励、权限、同步）与 `ftbquests-client.snbt`（UI 缩放、通知位置、fallback 语言）。

## 8.7 大批量写任务的正确姿势

别手写 50 个 16 位 hex id——写 **Python 生成器**：结构数据（dict/list）→ 两遍解析
（第一遍分配并登记 hex id，第二遍把依赖的 key 换成字面 id）→ 输出 SNBT。跨章锚点用字面 id。
生成后**必跑** `任务书校验.py`。

### 🔴 跨章依赖：必须按「目标任务所在章节」拼 id（2026-09-17 实测，最隐蔽的一个坑）

生成器里很容易写成「用当前章节的 key 去拼依赖 id」：

```python
# ❌ 错的：第 2 章的任务依赖第 1 章的任务时，会拼成 quest/02_xxx/c1_dirt —— 该 id 不存在
deps = [qid("quest/" + 当前章节key + "/" + d) for d in q["deps"]]
```

**后果**：FTB Quests 加载时**静默丢弃无效依赖**，不报错、不提示。表现是
**每一章的第一个任务都变成「无前置」**（章节之间断开），而你以为任务书没问题。
更坑的是 `任务书覆盖度.py` 的断链检查也会「全绿」——因为**无效依赖已经被丢掉了，自然查不到断链**。

```python
# ✅ 对的：先建全局 key → 章节 索引
KEY2CH = {q["key"]: ch["filename"] for ch in CH for q in ch["quests"]}
def dep_id(dep_key): return qid("quest/" + KEY2CH[dep_key] + "/" + dep_key)
```

**并且一定要加生成后自检**（把 id 收集起来，逐条核对依赖是否有效）：

```python
_MADE_IDS = set()          # 生成任务时收集
# ... 生成完毕后 ...
bad = [(q["key"], d) for ch in CH for q in ch["quests"]
       for d in q.get("deps", []) if dep_id(d) not in _MADE_IDS]
if bad: raise SystemExit(f"依赖指向不存在的任务：{bad}")
```

**判据**：整包（多章）的任务书，**「无前置任务」的数量应该等于章节数**（每章一个入口）。
如果发现「每章都有好几个无前置」，先怀疑跨章依赖被丢。

### 🔴 FTB Quests 会重写你的任务书文件 —— 别用脆弱正则去读它

只要服务端/客户端**加载过一次**，FTB 就会按自己的格式重写 `chapters/*.snbt`：

- 字段**按字母序**排列（`dependencies` → `description` → `icon` → `id` → `rewards` →
  `shape` → `subtitle` → `tasks` → `title` → `x` → `y`），和你写进去的顺序完全不同
- 顶层多出 `default_hide_dependency_lines` / `quest_links` 等字段
- **只有一行的数组写成单行**：`description: ["&7一句话"]`
- `rewards` / `tasks` 写成 `rewards: [{` … `}]`（起止符号和内容同行）

**所以写「梳理/审计任务书」的脚本时**：

| 坑 | 后果 | 修法 |
|---|---|---|
| 用 `re.search(r'\ttitle: "..."')` 找章节标题 | 命中的是**第一个任务的 title** | 按缩进层级取（章节级字段缩进 = 1 个 tab） |
| 按缩进层级取字段但没限制 | 命中的是 **reward / task 内部的同名键** | 严格卡 `ind == quest块缩进 + 2` |
| `description: [...]` 单行写法 | 进了"数组模式"出不来，**把后面所有字段全吞掉**，任务看起来"缺字段" | 特判 `s.endswith("]")` 直接解析单行 |
| 所有任务共用 `x: None/缺坐标` | 解析器 bug，不是任务书问题 | 逐块统计并打印，任何 `None` 都要查 |

**最省事的自查**：解析完打印「任务总数 / 唯一 id 数 / 无效依赖数 / 无前置任务数」四个数，
和你的数据源对一遍。数对不上就是解析器的问题，**别去改任务书**。

追求稳妥可再加一步：**启动期校验脚本**（KubeJS `validate_custom_quests.js` 思路——
启动时遍历所有 quest，检查 `dependencies` 引用的 id 都存在，断链直接报日志），
ATM 系整合包就是这么防断链的。
追求稳妥可再加一步：**启动期校验脚本**（KubeJS `validate_custom_quests.js` 思路——
启动时遍历所有 quest，检查 `dependencies` 引用的 id 都存在，断链直接报日志），
ATM 系整合包就是这么防断链的。

## 9. rewards 奖励

```snbt
rewards: [{
	icon: { Count: 1, id: "minecraft:iron_ingot" }   # 奖励图标
	id: "27ADCB33865FFAED"
	title: "&6奖励：&e铁锭 ×3"                        # 悬停显示的文字
	type: "item"                                     # item/loot/command/xp/custom/quest
	# item 型额外：item: {...} 物品
	# command 型：command: "/give @p ..."
}]
```

实测本包作者**大量用 `type: "custom"` 的奖励做"剧情解锁通知"**——图标配
`ftbquests:custom_icon` + 贴图路径，实际发奖由联动 mod/脚本完成。纯内容包做奖励
直接用 `type:"item"` 给物品最简单。

## 10. 颜色代码（& 前缀，显示名/正文通用）

```
&0黑 &1深蓝 &2深绿 &3深青 &4深红 &5紫 &6金 &7灰 &8深灰 &9蓝
&a绿 &b青 &c红 &d粉 &e黄 &f白
&l粗体 &o斜体 &n下划线 &m删除线 &k乱码 &r重置
```
整合包惯例：标题 `&e`/`&6`，正文 `&7`，强调 `&c`，副标题 `&8`。

## 11. 编辑工作流（不启动游戏也能写）

1. **规划**：先画章内任务布局——x/y 是**网格坐标**（单位网格，可带 0.5 半格），
   同一条链上的任务 x 相同、y 递增拉开间距（实测链上任务 y 差 1.5~2.0 格）；
   依赖链想清楚（谁是起点、解锁顺序）。布局微调**建议进游戏用 /ftbquests 编辑模式
   拖拽**（坐标是编辑器拖出来的，手写只求比例正确），文本编辑重点保证依赖关系正确。
2. **id**：16 位大写 hex——用 `openssl rand -hex 8 | tr a-f A-F` 之类生成，或抄现有格式
   （`0-9A-F` 16 字符）。**id 是全局引用键，改一个要同步改所有 dependencies**。
3. **写文件**：复制现有章节改，别从零写结构（缩进对齐照抄）。
4. **登记**：新章节 id 要加入 chapter_groups（如果要分组）；data.snbt 的 `version` +1。
5. **进游戏验证**：FTB Quests 打开书看布局/依赖/图标；任务文本进游戏 `L`/`Quest` 键重载
   （或重进世界）。语法错了整个文件不加载——书里看不到新章节 = 该章 .snbt 有错。

**金律**：*id 全局唯一不可重复；dependencies 引用的 id 必须存在；引号必须是英文双引号；
description 数组元素之间不能有逗号；每改完进游戏看一次书。*

**改完必跑自检**：`scripts/任务书校验.py <实例目录|ftbquests目录|单个.snbt>`——
校验引号配对/括号平衡/数组逗号，防止语法错误导致章节整章不加载（书里看不到新章节最难排查）。
实测对 9 个真实文件全通过；map 字段间逗号是**合法风格**（FTB 编辑器自己就输出逗号），不告警。

## 12. 快速复制模板（最小可运行章节）

```snbt
{
	filename: "my_chapter"
	group: ""
	icon: "minecraft:grass_block"
	id: "A000000000000001"
	order_index: 0
	title: " &e我的章节"
	quests: [
		{
			title: "&e起点"
			subtitle: "&8点我阅读"
			description: ["&7欢迎！这是第一章的第一个任务。"]
			icon: "minecraft:paper"
			id: "B000000000000001"
			shape: "circle"
			tasks: [{
				id: "C000000000000001"
				title: "继续"
				type: "checkmark"
			}]
			x: 0.0d
			y: 0.0d
		}
		{
			dependencies: ["B000000000000001"]
			title: "&e挖块石头"
			icon: "minecraft:cobblestone"
			id: "B000000000000002"
			shape: "circle"
			tasks: [{
				id: "C000000000000002"
				item: {
					Count: 1
					id: "minecraft:cobblestone"
				}
				type: "item"
			}]
			rewards: [{
				id: "D000000000000001"
				title: "&6奖励：铁锭"
				item: {
					Count: 3
					id: "minecraft:iron_ingot"
				}
				type: "item"
			}]
			x: 2.0d
			y: 0.0d
		}
	]
}
```

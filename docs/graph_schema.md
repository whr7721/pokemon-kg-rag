# 图谱 Schema 设计文档（增强版）

> 模块：数据与图谱
> 更新：2026-09-11
> 适用代码：`src/build_engine.py`、`src/build_graph.py`、`scripts/load_engine_out.py`
> 语料口径：`data/raw/data`（pokemon 1025 / move_list 953 / ability_list 322 / pokedex 24）

## 1. 设计目标

把中文图鉴 JSON 通过确定性规则建成 Neo4j 图，并把实体文本块 `Chunk` 向量化后经 `DESCRIBES` 对齐回实体，支撑两条检索路径：

- GraphRAG：向量召回 `Chunk` -> `DESCRIBES` 回到实体 -> 注入实体邻域结构化事实。
- 结构化直答：`EVOLVES_TO`、`HITS_TYPE`、`HAS_ABILITY` 等关系直接走 Cypher。

本图所有节点和关系都来自原始数据字段，不引入外部知识或大模型生成内容，可清库重跑。

## 2. 节点与唯一键

| 标签 | 唯一键 | 主要属性 | 来源 |
| --- | --- | --- | --- |
| `Pokemon` | `pokedex_id` | `name_zh/name_en/name_ja`、`main_form`、`category`、`description`、`legendary`、`text` | `pokemon/*.json` 顶层字段 |
| `Form` | `id` | `pokedex_id`、`form_name`、`is_default`、`form_index`、`types`、`category`、`height`、`weight`、`color`、`stats`、`base_stats_total`、`text` | `pokemon/*.json` 的 `forms[]` |
| `Move` | `id` | `name_zh/name_en/name_ja`、`type`、`category`、`power`、`accuracy`、`pp`、`generation`、`description`、`text` | `move_list.json` + `moves/*.json` |
| `Ability` | `id` | `name_zh/name_en/name_ja`、`generation`、`common_count`、`hidden_count`、`description`、`effect`、`text` | `ability_list.json` + `abilities/*.json` |
| `Type` | `id` | `name_zh`、`text`、`weak_to`、`resist_to`、`immune_to` | 18 属性词表 + 克制表推导 |
| `EggGroup` | `id` | `name_zh`、`form_count`、`breedable`、`text` | `forms[].egg_groups` 并集 + 规范名合并 |
| `RegionDex` | `id` | `name_zh`、`is_national`、`member_count`、`text` | `pokedex/*.json` |
| `Chunk` | `chunk_id` | `kind`（`entity` / `relation` / `hit-profile`）、`entity_type`、`entity_id`、`name_zh`、`text`、`embedding`、`embed_model` | `build_engine.py` 生成文本块，`embed_vectors.py` / `embed_relation_chunks.py` 写向量 |

ID 约定：`Pokemon/Form` 用图鉴号和形态名，`Move` 用 `move:<中文名>`，`Ability` 用 `ability:<中文名>`，`Type` 用 `type:<中文名>`，`EggGroup` 用 `egggroup:<规范名>`，`RegionDex` 用 `region:<图鉴名>`。

## 3. 关系与方向

| 关系 | 方向 | 语义 | 关键属性 |
| --- | --- | --- | --- |
| `HAS_FORM` | Pokemon -> Form | 宝可梦的形态 | `default` |
| `HAS_TYPE` | Form -> Type | 形态属性，支持双属性 | `slot` |
| `HAS_ABILITY` | Form -> Ability | 形态特性 | `slot`、`hidden` |
| `IN_EGG_GROUP` | Form -> EggGroup | 所属蛋群 | - |
| `LEARNS` | Form -> Move | 可学招式 | `method`、`label`、`move_type`、`category` |
| `EVOLVES_TO` | Pokemon -> Pokemon | 进化边 | `condition`、`method`、`stage` |
| `HITS_TYPE` | Type -> Type | 攻击属性对防御属性的伤害倍率 | `multiplier` |
| `TYPE_MOD` | Ability -> Type | 特性改变某属性招式的伤害倍率 | `base`、`variant`、`defender_type`、`note` |
| `IN_DEX` | Pokemon -> RegionDex | 地区图鉴收录关系 | `local_id`、`gen` |
| `DESCRIBES` | Chunk -> 实体 | 文本块描述哪个实体 | - |

`HITS_TYPE` 方向统一为“攻击属性 -> 防御属性”，`multiplier` 可取 `0/0.25/0.5/1/2/4`，由单属性形态样本投票生成 18×18 全表；双属性防守方的精确倍率由 `build_hit_profiles()` 直接取数据源的 `type_effectiveness` 生成受击块（`Chunk.kind='hit-profile'`），不再依赖查询端相乘。

## 4. 建图策略与关键口径

- 每个图鉴文件生成 1 个 `Pokemon` 节点，`forms[0]` 作为默认形态信息写入 Pokemon。
- 每个形态生成 1 个 `Form` 节点；属性、特性、蛋群、招式都挂在 `Form` 上，检索时经 `HAS_FORM` 归一为父 Pokemon。
- `type_effectiveness` 的 `form` 字段混杂形态名、形态简称（“草木”“洗翠”）与特性名/括号注解（“毛茸茸”“(避雷针)”），`_is_ability_tag()` 只把后者判为特性变体表，避免把带特性修正的倍率混进基础表投票。
- 每个宝可梦默认形态生成 1 条受击倍率块（`hitprofile|<pokedex_id>`），文本直接给出该属性组合的 18 项倍率；生成时与“单表相乘”结果比对，不一致记入 `build_report.json` 的 `hit_profile_warns`。
- 属性克制表由单属性形态的 `type_effectiveness` 投票生成 18x18 全表；特性变体表与基础表比较后派生 `TYPE_MOD`。
- 进化链按 `evolution_chains` 的父子顺序生成 `EVOLVES_TO`。
- `Chunk` 分两类：实体块由 `load_engine_out.py` 从 `entity_chunks.jsonl` 导入；关系块与受击块由 `scripts/embed_relation_chunks.py` 从建图产物向量化入库，措辞与 `chunk_id` 全部取自 `build_engine.py`，脚本只做主语归一与嵌入。
- `LEARNS` / `IN_DEX` 逐边成句合计约 8.8 万条，按主语聚合为概览句（每形态/每宝可梦 1 条），把关系语料控制在约 1 万条，兼顾“普通 RAG 也能召回关系事实”与索引体积。
- 叙事关系 `RIVAL_OF/PREDATES_ON/...` 不在当前确定性建库中；`multi_qa.py/rag.py` 保留查询，只有库中存在这些边时才生效。

## 5. 规模基线

以下为 `python src/build_graph.py --out build_out` 产物实测（2026-09-11）；库内节点/关系由 `scripts/load_engine_out.py` 导入，文本块规模由 `scripts/embed_relation_chunks.py` 决定，日常体检用 `scripts/check_quality.py`。

- 节点：Pokemon 1025 / Form 1320 / Move 953 / Ability 312 / Type 18 / EggGroup 16 / RegionDex 24
- 关系：LEARNS 82833 / IN_DEX 5973 / HAS_ABILITY 2866 / HAS_TYPE 2064 / IN_EGG_GROUP 1671 / HAS_FORM 1320 / EVOLVES_TO 485 / HITS_TYPE 324 / TYPE_MOD 22
- 文本块：实体块 30771（导入按 `chunk_id` 去重后 30728）；关系块 9774（其中 `LEARNS` 1317、`IN_DEX` 1025 为聚合概览，其余逐边）；受击块 1019

## 6. 索引与约束

`scripts/setup_indexes.py` 会创建：

- 向量索引：`embedding_Chunk`，1024 维，余弦相似度。
- 唯一约束：7 个实体标签的 `id`，加 `Pokemon.pokedex_id` 与 `Chunk.chunk_id`，共 9 条。

`load_engine_out.py` 的节点导入以各标签 `id` 为合并键；`scripts/setup_indexes.py` 已为全部 7 个实体标签补上 `id` 唯一约束（`Pokemon_id` / `Form_id` / `Move_id` / `Ability_id` / `Type_id` / `EggGroup_id` / `RegionDex_id`），另有 `pokemon_id`（`pokedex_id`）与 `chunk_id`。

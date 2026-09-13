# 阶段二分支改动总结报告（成员一：数据与图谱）

> **分支名称**：`feat/member1-graph-data`  
> **基线 Commit**：`a9f2401`（origin/master）  
> **分支 Commit**：`aa08a77` (`feat: 补齐图谱唯一约束、双属性受击块与关系块聚合`)  
> **修改文件**：
> - `scripts/setup_indexes.py`（修改）
> - `src/build_engine.py`（修改）
> - `scripts/check_quality.py`（新建）
> - `docs/graph_schema.md`（修改）

---

## 1. 改动背景与解决的问题

在阶段一交付后，数据与图谱模块暴露出以下痛点：
1. **索引与数据库现状脱节**：线上 AuraDB 已存在 9 条唯一约束，但 `scripts/setup_indexes.py` 只记录了 2 条，一旦冷启动重构数据库，7 个主要实体标签无法幂等保证主键唯一性；
2. **双属性受击数据被丢弃**：数据源 `type_effectiveness` 中包含双属性宝可梦在 18 属性下的完整伤害倍率，但旧建图代码仅收集单属性样本投票，双属性记录被完全丢弃，导致普通 RAG 无法召回双属性克制倍率事实；
3. **形态简称误判为特性变体**：受击表中大量形态简称（如“草木”“洗翠”“阿罗拉”）因不完全匹配全名，被旧规则误判为特性变体表，导致 12 只宝可梦（结草贵妇、谢米等）缺失基础受击表，且污染了克制表投票；
4. **关系块规模爆炸**：旧版关系块包含 8.2 万条 `LEARNS` 和 6 千条 `IN_DEX`，全量展开高达 9.6 万块，若全量向量化将造成严重的语料膨胀和检索噪声；
5. **缺少数据质量自检工具**：无法自动化验证实体完整度与关系覆盖率。

---

## 2. 具体改动内容与技术实现

### (1) 唯一约束固化 (`scripts/setup_indexes.py`)
- 在 `STEPS` 中追加 7 个实体标签的 `id` 唯一性约束：
  ```python
  "CREATE CONSTRAINT Ability_id IF NOT EXISTS FOR (a:Ability) REQUIRE a.id IS UNIQUE",
  "CREATE CONSTRAINT EggGroup_id IF NOT EXISTS FOR (e:EggGroup) REQUIRE e.id IS UNIQUE",
  "CREATE CONSTRAINT Form_id IF NOT EXISTS FOR (f:Form) REQUIRE f.id IS UNIQUE",
  "CREATE CONSTRAINT Move_id IF NOT EXISTS FOR (m:Move) REQUIRE m.id IS UNIQUE",
  "CREATE CONSTRAINT Pokemon_id IF NOT EXISTS FOR (p:Pokemon) REQUIRE p.id IS UNIQUE",
  "CREATE CONSTRAINT RegionDex_id IF NOT EXISTS FOR (r:RegionDex) REQUIRE r.id IS UNIQUE",
  "CREATE CONSTRAINT Type_id IF NOT EXISTS FOR (t:Type) REQUIRE t.id IS UNIQUE",
  ```
- 同步更新末尾 `SHOW INDEXES` 校验清单，使建索引脚本与线上库现状（9 条约束 + 4 个全文索引 + 1 个向量索引）完全对齐，支持幂等冷启动。

### (2) 双属性受击倍率块构建与形态简称修复 (`src/build_engine.py`)
- **形态简称与特性变体精确判定**：
  实现 `_is_ability_tag(tag)`，清洗后缀标记（`*‡†`），通过特性字典与特性后缀精准过滤变体表，将“草木”“洗翠”“伽勒尔”等形态简称正确归类为基础表；修复了 0306 波士可多拉（超级进化过滤）对钢属性克制表的投票污染，使 18×18 克制全表投票达到 100% 一致（`chart_consistent: true`）。
- **生成受击倍率块 (`build_hit_profiles`)**：
  为每个宝可梦默认形态生成一条 `hit-profile` 文本块（`hitprofile|<pokedex_id>`），文本形如：
  `妙蛙种子（草/毒）受到攻击时的伤害倍率：2倍[冰、火、超能力、飞行]；0.5倍[妖精、格斗、水、电]；0.25倍[草]。`
  共生成 1,019 条，直接给出全属性精确受击伤害，免去大模型在双属性乘法上的心算幻觉。

### (3) 关系块主语概览聚合 (`src/build_engine.py`)
- 实现 `_append_overview_rel_chunks()`：
  - `LEARNS` 关系按 Form 聚合为升级招式概览（每形态 1 条，列出按等级排序的前 20 个升级招式，共 1,317 条）；
  - `IN_DEX` 关系按 Pokemon 聚合为地区图鉴收录概览（每宝可梦 1 条，共 1,025 条）；
  - `EVOLVES_TO` (485)、`HITS_TYPE` (324)、`HAS_ABILITY` (2866)、`IN_EGG_GROUP` (1671)、`TYPE_MOD` (22)、`HAS_TYPE` (2064) 保持逐边生成。
- 关系块总量由 96,238 条压缩至 **9,774 条**（压缩 90%），兼顾了语料质量与索引体积。

### (4) 新建数据质量体检脚本 (`scripts/check_quality.py`)
- 覆盖四组自检指标：
  1. **字段完整率**：检查 Pokemon、Form、Move、Ability 的核心描述与正文字段；
  2. **关系覆盖率**：无形态、无属性、无特性、无进化边、无受击块的异常节点统计；
  3. **向量覆盖**：Chunk 按 `kind` 分组的 embedding 覆盖情况；
  4. **索引与约束门禁**：比对当前在线库与预期清单，若有缺失以非零码退出，可直接作为 CI / 交付验收门槛。

### (5) 图谱 Schema 文档同步校正 (`docs/graph_schema.md`)
- 更新更新日期至 2026-09-11；
- 修正唯一约束清单为 9 条，澄清 `id` 唯一性已有强约束保证；
- 补充 `Chunk.kind` 新类型（`hit-profile`）与聚合说明；
- 更新建图产物规模基准表。

---

## 3. 实测验证数据

运行 `scripts/check_quality.py` 与 `src/build_graph.py` 的实测输出：

```text
== 字段完整率 ==
  Pokemon.description  1025/1025 (100.0%)
  Pokemon.text         1025/1025 (100.0%)
  Form.text            1320/1320 (100.0%)
  Move.description     935/935   (100.0%)
  Move.text            935/935   (100.0%)
  Ability.effect       306/307   (99.7%)
  Ability.text         307/307   (100.0%)

== 关系覆盖率 ==
  Pokemon 无形态       0
  Form 无属性          0
  Form 无特性          50 (官方部分特殊形态无特性)
  全图孤立节点         0

== 规模产物基线 (build_out) ==
  Pokemon: 1025 / Form: 1320 / Move: 953 / Ability: 312 / Type: 18 / EggGroup: 16 / RegionDex: 24
  克制全表 (HITS_TYPE): 324 (18×18 满表，投票一致性 100%)
  实体块: 30,771 (去重后 30,728)
  受击块 (hit-profile): 1,019
  关系块 (relation): 9,774
```

---

## 4. 与成员二的接口约定

1. **产物位置**：`build_engine.py` 将关系块和受击块分别输出至 `build_out/chunks/relation_chunks.jsonl` 和 `build_out/chunks/entity_chunks.jsonl`；
2. **权威措辞**：关系块文本模板与 `chunk_id` 完全由成员一的 `build_engine` 生成，成员二的向量化脚本仅做读取、主语映射归一与写入，不再自行拼接自然语言，消除了措辞漂移隐患。

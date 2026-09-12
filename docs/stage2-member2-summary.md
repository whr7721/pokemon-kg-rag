# 阶段二分支改动总结报告（成员二：向量与检索）

> **分支名称**：`feat/member2-retrieval`  
> **基线 Commit**：`a9f2401`（origin/master）  
> **分支 Commit**：
> - `77a6998` (`feat: 落地混合检索、三篇图增强论文与参数/消融实验`)
> - `532f450` (`fix: 修复 GraphRAG 生成断点，直走 GraphRAG 达 16/19 (84.2%)`)
> **修改文件**：
> - `src/rag.py`（修改）
> - `src/graph_access.py`（修改）
> - `src/multi_qa.py`（修改）
> - `scripts/embed_relation_chunks.py`（重写）
> - `scripts/run_ablation.py`（新建）
> - `scripts/tune_retrieval.py`（新建）
> - `scripts/check_architecture.py`（修改）
> - `docs/retrieval.md`（重写）

---

## 1. 改动背景与解决的问题

阶段一汇报中老师指出：“MultiQA 和 GraphRAG 都做了图查询，功能重复，是两个独立模块各自维护”。组长虽然重构了统一图访问层 `GraphAccess`，但遗留了关键架构缺陷和技术瓶颈：
1. **图访问收口不彻底**：`rag.py` 内部通过 `self.driver = self.graph.driver` 借出底层驱动，4 处直接调用 `self.driver.execute_query`，绕过了 `GraphAccess`；`graph_access.py` 中遗留了 4 个复制过去但零调用的死查询常量；
2. **关系块语料割裂**：旧脚本 `embed_relation_chunks.py` 自行硬编码了 2 类查询与措辞模板，与建图引擎 `build_engine` 存在重复维护与措辞漂移风险；
3. **混合检索缺乏理论指导**：旧混合检索仅用 Python 列表拼接并硬编码截断，没有融合算法，向量余弦与 BM25 全文分数尺度不可比；且同一个高频实体的多个文本块会垄断召回列表；
4. **缺少对多跳与复杂图语义的支持**：面对进化链、克制计算、招式筛选等问题，纯向量召回经常面临“孤立块盲区”（语义不挨着但图拓扑强相关）；
5. **缺少规范的消融实验与参数网格实验**。

---

## 2. 具体改动内容与技术实现

### (1) 架构解耦与图访问收口 (`src/rag.py`, `src/graph_access.py`, `src/multi_qa.py`)
- **彻底清除底层驱动借用**：删除 `rag.py` 与 `multi_qa.py` 中借用 `self.driver`/`self.db` 的三行赋值，删除 `neo4j.RoutingControl` 导入；4 处直接 Cypher 执行点全面改为 `self.graph.run()`；
- **清理冗余死代码**：删除 `graph_access.py` 中无人使用的 `RETRIEVAL_QUERY`、`NAME_HIT_QUERY`、`MOVE_POWER_QUERY`、`ENTITY_FACT_QUERIES` 四个重复常量（它们作为 RAG 专有查询保留在 `rag.py` 内）；
- **AST 架构自检强化**：在 `scripts/check_architecture.py` 中新增 AST 语法树检查，静态断言 `multi_qa.py` 与 `rag.py` 中禁止出现 `execute_query` 与 `session` 属性调用，从根源防止“借驱动私自查库”的架构退化。

### (2) 关系块入库重写 (`scripts/embed_relation_chunks.py`)
- **单一真理来源（SSOT）**：重写脚本，彻底删除自造措辞模板，直接读取成员一 `build_engine.py` 的建图产物 `build_out/chunks/*.jsonl`；
- **主语层级归一**：将挂在 `Form` 上的关系块主语（如 `0001::妙蛙种子`）通过前缀剥离自动映射回父级 `Pokemon`（`0001`），保证挂载的 `DESCRIBES` 边能被检索召回；
- **断点续跑与规模入库**：采用 `ApiEmbedder`（BGE-M3，1024 维）+ 6 次网络重试 + 批次落盘机制，平稳完成 **9,774 条关系块 + 1,019 条受击倍率块** 的向量化写入。库内总 Chunk 数达到 41,507，缺向量数为 0。

### (3) 三篇图增强论文的工程化落地 (`src/rag.py`, `src/graph_access.py`)

#### ① PolyG（查询感知规划器）
- 实现 `PLANS` 意图映射字典与 `_plan(question)`：自适应区分 `evolution`、`counter`、`ability`、`move`、`open` 五类意图；
- **自适应控制四大维度**：
  - `labels`：在 `_name_hits` 全文通道中按意图过滤实体标签，过滤无关同名实体；
  - `need`：在 `format_fact(fact, need)` 中按意图动态剔除无关事实（如克制题只留克制表，丢弃蛋群与特性），防止 Prompt 上下文注意力稀释；
  - `paths`：进化题动态激活因果路径提取跳数（深度 2）；
  - `expand`：绑定对应的 KG²RAG 图拓扑扩展规则。

#### ② PathRAG（因果证据路径）
- `src/graph_access.py` 实现 `paths(eid, depth)`：利用 Cypher 变长路径匹配 `[:EVOLVES_TO*1..N]` 抽取宝可梦进化拓扑及触发条件（等级、石头、道具、交换）；
- `src/rag.py` 实现 `format_paths()`：将图拓扑序列化为直观的推理箭线链：
  `小火龙 --[升级、等级16以上]--> 火恐龙 --[升级、等级36以上]--> 喷火龙`
- 将证据路径作为一等公民注入 Prompt，模型直接顺着路径推理，彻底终结多跳幻觉。

#### ③ KG²RAG（图谱引导召回扩展）
- `src/graph_access.py` 实现 `related_ids(eid, rule)` 与 `chunks_of(ids)`：
  - 克制题沿 18×18 全表（`HITS_TYPE`）计算目标宝可梦的综合受击倍率，找出 $\ge 2$ 倍的攻击属性，再沿图拓扑精准抽取持有该属性的候选宝可梦（如喷火龙 $\to$ 提取岩石系的小拳石、大岩蛇）；
  - 抽取时通过 `ORDER BY CASE WHEN c.kind = 'hit-profile' THEN 0 ...` 优先提取种子实体的**双属性受击倍率块**；
- 扩展块作为第三路召回输入与语义向量、实体名全文共同融合，彻底打破纯向量检索在语义不重合时的“孤立块盲区”。

### (4) 标准 RRF 混合多路融合 (`src/rag.py`)
- 实现 `rrf_fuse(rank_lists, k=60)`：
  - **单路内部排他去重**：在单路列表遍历中记录 `seen_in_list`，同一个实体的多个分块在单路内只计最高名次一次，彻底解决单个高频实体因分块过多在向量检索中重复累加霸榜、把高精度全文实体挤出前排的重大缺陷；
  - **跨路倒数求和**：抹平 BM25 分数与向量余弦的尺度差异，无需脆弱的加权超参即可自适应平衡语义与精准匹配。

### (5) 修复生成层三大断点问题 (`src/rag.py`, `src/graph_access.py`)
- **持有者名单补全**：`format_fact` 补充 `Ability` 的 `pokemon_list` 渲染，`ENTITY_FACT_QUERIES["Ability"]` 候选放宽至 30 只，修复茂盛、储水等普特问持有者答“资料不足”的问题；
- **别名归一与近似特性注入**：入口增加 `alias_normalize`（板匙蛇 $\to$ 饭匙蛇），`_name_hits` 在未命中特性时自动调用 `guess_ability` 注入近似特性（悬浮 $\to$ 飘浮、蓄水 $\to$ 储水）；
- **对策推理第二跳**：针对战术策略问句（“适合携带什么特性”），自动沿图谱查出目标宝可梦所有特性的机制说明（`ability_rows`）注入上下文，使模型能够逻辑自洽地根据“避雷针免疫电属性”推导出对战结论。

---

## 3. 实验数据与对比评测

### (1) 检索层 Ablation 四路对照实验 (`scripts/run_ablation.py`)
在全组 19 道基准题上严格评测 Top-8 召回能力（期望子串全包含在召回证据中才算命中，无大模型心算补正）：

| 检索模式 | 算法构成 | 命中数 / 总数 | 召回命中率 | 较纯向量提升 |
|---|---|:---:|:---:|:---:|
| `vector` | 纯语义向量检索 | 9 / 19 | 47.4% | 基线 |
| `fulltext` | 纯实体名全文检索 | 1 / 19 | 5.3% | — |
| `hybrid` | 向量 + 全文 RRF 融合 | 8 / 19 | 42.1% | -5.3% |
| **`hybrid_graph`** | **向量 + 全文 + KG²RAG 图扩展 (最终方案)** | **13 / 19** | **68.4%** | **+21.0% (命中数提升 44%)** |

> **关键事实**：在阿柏怪（q04）、皮卡丘（q05）、妙蛙种子（q06）、饭匙蛇（q12）4 道克制弱点题中，纯向量与纯全文在前 8 位中完全漏召，**由 KG²RAG 沿图拓扑展开克制方与受击块后全部实现从 0 到 1 翻盘命中**。

### (2) 检索参数网格搜索实验 (`scripts/tune_retrieval.py`)
内存预取缓存加速，覆盖 `top_k ∈ {4, 6, 8, 12}` 与 `RRF_K ∈ {10, 30, 60, 100}` 共 16 组组合：
- $RRF\_K$ 在 10~100 区间高度平稳，默认 $k=60$ 表现稳健；
- 召回率随 $top\_k$ 扩大单调上升：$top\_k=4$ (52.6%) $\to$ $top\_k=8$ (63.2%) $\to$ **$top\_k=12$ 时达到最高 73.7% (14/19)**。

### (3) 端到端问答实测对比
- **直走 GraphRAG (`PokemonGraphRAG.ask()`)**：跳过 MultiQA 纯靠检索 + 图事实 + LLM 生成，通过率从修复前的 13/19 飙升至 **16/19 (84.2%)**；
- **全链路 Router (`RagRouter.answer()`)**：MultiQA 结构化直答优先、未命中兜底 GraphRAG，达到 **19/19 (100.0%)** 满分全通。

---

## 4. 架构回归验证结果

```bash
python scripts/check_architecture.py -> architecture ok (含新增的无直接 Cypher 执行断言)
python scripts/smoke_multiqa.py      -> 通过 14/14
python scripts/smoke_rag.py          -> smoke_rag done
GET /api/health                      -> {"ok": true, "status": "ok", "warnings": []}
```

# 双通道检索与实体邻域图谱事实注入规范

> 负责人：成员二（向量与检索）
> 基准版本：第一阶段（9/10 汇报里程碑）
> 对应代码：`src/embedder.py`、`src/rag.py`、`scripts/compare_rag.py`、`src/app.py`

## 1. 背景与核心改进

早期最小闭环只把 `VectorCypherRetriever` 召回到的 `Chunk.text` 交给 LLM，图谱关系主要用于前端子图展示，没有真正进入生成上下文。

当前实现的核心改进是两条召回通道：

- 语义通道：BGE-M3 向量召回 Top-K 文本块；
- 精确名通道：按问题里的宝可梦/招式/特性实体名做全文召回，并要求实体名原文出现在问题中。

全文索引按实体标签命名（`pokemonFulltext` / `abilityFulltext` / `moveFulltext` / `formFulltext`），
逐个查询后按召回分合并；命中实体节点后反查该实体的文本块（截断 700 字）。

两路结果按实体去重合并后，沿 `Chunk -[:DESCRIBES]-> 实体` 回到图谱实体，再查询实体邻域事实，把属性、特性、蛋群、进化链、属性克制等结构化信息与文本块一起拼入 Prompt。`use_graph=false` 时只使用文本块，作为普通 RAG 对照。

## 2. 向量化

统一使用云端 OpenAI 兼容 embedding API（不依赖本地 torch / sentence-transformers）：

- `EMBED_ENDPOINT`：向量服务端点（如 `https://api.siliconflow.cn/v1/embeddings`）
- `EMBED_API_KEY`（兼容 `EMBED_KEY`）：访问密钥
- `EMBED_MODEL`：`BAAI/bge-m3`，1024 维

`src/embedder.py` 只有一个 `ApiEmbedder` 类，批量返回时按 `index` 排序再取，避免服务端乱序导致向量与文本错位。库内向量（`embed_model='bge-m3'`）与查询向量同属 `BAAI/bge-m3` 空间，可直接混用。

向量索引统一为 `embedding_Chunk`，在 `scripts/setup_indexes.py` 中创建，1024 维、余弦相似度。

## 3. 检索链路

```text
用户提问
  -> BGE-M3 向量化 + 实体名全文匹配（按实体标签命名的全文索引）
  -> 向量索引 embedding_Chunk 与全文索引双路召回，按召回分合并
  -> 按实体去重，Chunk -[:DESCRIBES]-> 实体（Form 会经 HAS_FORM 归一为父 Pokemon）
  -> 对 Pokemon/Move/Ability/Type 查询邻域图谱事实
  -> 招式类条件问题额外执行 Move 图谱筛选（如“属性+威力”）
  -> 结构化事实 + 文本块拼入 Prompt
  -> tju-llm 生成回答
```

`src/rag.py` 的关键查询：

- `RETRIEVAL_QUERY`：向量召回 + `DESCRIBES` 回实体。
- `NAME_HIT_QUERY_CHUNK` / `NAME_HIT_QUERY_ENTITY`：实体名全文召回，解决“特性/招式名不在文本正文中”导致向量漏召的问题（如“茂盛”的文本块内不含“茂盛”二字）。
- `ENTITY_FACT_QUERIES`：Pokemon / Move / Ability / Type 四类实体的邻域事实。
- `MOVE_POWER_QUERY`：招式“属性 + 威力”筛选问题的图内候选，避免文本块覆盖不全。
- `SUBGRAPH_QUERIES`：前端子图数据，各分支统一输出真实方向 `fl/fn → tl/tn`。

召回参数：`app.py`、`retrieve()`、`ask()` 的 `top_k` 默认值统一为 8；向量通道取 `top_k×4`、精确名通道取 `top_k×2`，合并去重后截断到 `top_k`。

索引健康自检：`GET /api/health` 返回向量索引与全文索引在线状态；索引缺失或查询报错时 `_name_hits()` 打印警告并降级为仅向量召回（不再静默失败）。

## 4. 图谱事实注入内容

- Pokemon：属性、特性（普通/隐藏）、蛋群、前后置进化与最终进化、属性克制倍率。
- Move：属性、分类、威力、命中、PP、说明、可学宝可梦。
- Ability：说明、世代、持有宝可梦。
- Type：进攻克制倍率与防守弱点/抵抗列表（`HITS_TYPE`）。
- MoveFilter：符合“威力/属性”条件的招式候选，作为图谱筛选事实注入。

叙事关系 `RIVAL_OF/PREDATES_ON/...` 当前保留查询，但 `build_engine.py` 未生成这些边；只有额外导入叙事增强数据后才生效。

## 5. 普通 RAG vs GraphRAG 对照

- `use_graph=true`：GraphRAG，注入图谱事实。
- `use_graph=false`：普通 RAG，只注入检索文本块。

完整 5 题对比结果见 `docs/evaluation.md`。`scripts/compare_rag.py` 会按当前库重新输出两类模式的回答，最终汇报前应以组内共享库的实测结果更新评测表。

## 6. 环境变量

| 配置项 | 说明 | 默认/示例 |
| --- | --- | --- |
| `EMBED_MODEL` | 向量模型 | `BAAI/bge-m3` |
| `EMBED_DIM` | 向量维度 | `1024` |
| `EMBED_ENDPOINT` | 向量服务端点 | `https://api.siliconflow.cn/v1/embeddings` |
| `EMBED_API_KEY` / `EMBED_KEY` | 向量服务密钥 | `sk-...` |
| `VECTOR_INDEX` | Neo4j 向量索引名 | `embedding_Chunk` |
| `FULLTEXT_INDEXES` | 实体名全文索引列表（逗号分隔） | `pokemonFulltext,...` |
| `LLM_ENDPOINT` | 大模型端点 | `https://ai.tju.edu.cn/api/v3` |
| `LLM_MODEL` | 大模型名 | `tju-llm` |
| `LLM_TOKEN` | 大模型密钥 | `...` |
| `NEO4J_URL` / `NEO4J_DB` | Neo4j Aura 连接 | `neo4j+s://...` |

# 项目进度

## 当前状态
- 阶段：第一阶段（9/10 汇报）
- 分支：master
- 状态：图谱 + 文本切块 + 向量/全文索引已完成

## 已完成
1. 环境：Python 3.14 venv，neo4j / neo4j_graphrag / flask / openai / sentence_transformers 等。
2. 数据：从 42arch/pokemon-dataset-zh 下载 1708 个 JSON 到 data/raw。
3. 图构建：src/build_graph.py 批量 UNWIND 写入 Neo4j AuraDB。
4. 图校验：scripts/check_graph.py。
5. 文本切块：src/chunker.py 规则化生成 3314 个 Chunk。
6. 本地向量：src/embedder.py 使用 BAAI/bge-m3（1024 维）。
7. 索引：scripts/setup_indexes.py 创建 chunk_embedding 向量索引 + chunk_text 全文索引。

## 图统计基线
- 节点：Pokemon 1025 / Move 935 / Ability 309 / EggGroup 28 / Type 18 / Generation 9
- 关系：LEARNS_MOVE 56841 / HAS_ABILITY 2413 / HAS_TYPE 1551 / BELONGS_TO_EGG_GROUP 1304 / BELONGS_TO_GENERATION 1025 / EVOLVES_TO 487 / RESISTS 67 / STRONG_AGAINST 51 / IMMUNE_TO 16

## 文本块统计
- Chunk 3314：pokemon 1025 / pokemon_moves 1025 / move 953 / ability 311
- DESCRIBES 3314：Pokemon 2050 / Move 953 / Ability 311

## 下一步
- VectorCypherRetriever 检索（向量 + 图谱扩展）
- GraphRAG + OpenAILLM 生成
- Flask /api/ask
- 5 个代表性问题验证

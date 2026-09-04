# 项目进度

## 当前状态
- 阶段：第一阶段（9/10 汇报）
- 分支：master
- 状态：知识图谱已建成并校验通过

## 已完成
1. 环境：Python 3.14 venv，安装 neo4j / neo4j_graphrag / flask / openai / sentence_transformers 等。
2. 数据：从 42arch/pokemon-dataset-zh 下载 1708 个 JSON 到 data/raw。
3. 图构建：src/build_graph.py 用批量 UNWIND 写入 Neo4j AuraDB。
4. 图校验：scripts/check_graph.py 输出节点与关系统计。

## 图统计基线
- 节点：Pokemon 1025 / Move 935 / Ability 309 / EggGroup 28 / Type 18 / Generation 9
- 关系：LEARNS_MOVE 56841 / HAS_ABILITY 2413 / HAS_TYPE 1551 / BELONGS_TO_EGG_GROUP 1304 / BELONGS_TO_GENERATION 1025 / EVOLVES_TO 487 / RESISTS 67 / STRONG_AGAINST 51 / IMMUNE_TO 16

## 下一步
- 文本切块 + BGE-M3 本地向量
- Neo4j 向量索引 + 全文索引
- VectorRetriever / VectorCypherRetriever
- GraphRAG 生成 + Flask /api/ask

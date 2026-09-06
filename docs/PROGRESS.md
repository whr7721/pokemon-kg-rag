# 项目进度

## 当前状态
- 阶段：第一阶段（9/10 汇报）
- 分支：master
- 状态：端到端 Web 演示已完成（问答 + 证据 + 子图）

## 已完成
1. 环境：Python 3.14 venv，neo4j / neo4j_graphrag / flask / openai / sentence_transformers 等。
2. 数据：从 42arch/pokemon-dataset-zh 下载 1708 个 JSON 到 data/raw。
3. 图构建：src/build_graph.py 批量 UNWIND 写入 Neo4j AuraDB。
4. 图校验：scripts/check_graph.py。
5. 文本切块：src/chunker.py 规则化生成 3314 个 Chunk。
6. 本地向量：src/embedder.py 使用 BAAI/bge-m3（1024 维）。
7. 索引：scripts/setup_indexes.py 创建向量索引 + 全文索引。
8. 检索：src/rag.py 使用 VectorCypherRetriever，经 DESCRIBES 对齐图谱实体。
9. 生成：src/rag.py 使用 GraphRAG + OpenAILLM（学校 Qwen）。
10. Web：src/app.py Flask /api/ask，src/templates/index.html 展示回答、证据、子图。

## 图统计基线
- 节点：Pokemon 1025 / Move 935 / Ability 309 / EggGroup 28 / Type 18 / Generation 9
- 关系：LEARNS_MOVE 56841 / HAS_ABILITY 2413 / HAS_TYPE 1551 / BELONGS_TO_EGG_GROUP 1304 / BELONGS_TO_GENERATION 1025 / EVOLVES_TO 487 / RESISTS 67 / STRONG_AGAINST 51 / IMMUNE_TO 16

## 文本块统计
- Chunk 3314：pokemon 1025 / pokemon_moves 1025 / move 953 / ability 311
- DESCRIBES 3314：Pokemon 2050 / Move 953 / Ability 311

## 验证结果
- 皮卡丘属性 → 电，命中 皮丘/皮卡丘/雷丘
- 妙蛙种子最终进化 → 妙蛙花，命中 妙蛙种子/妙蛙花

## 下一步
- 图谱事实增强：让 GraphRAG 真正把实体邻域注入上下文
- 普通 RAG vs GraphRAG 对比实验
- 第一阶段 5 个代表性问题与汇报材料

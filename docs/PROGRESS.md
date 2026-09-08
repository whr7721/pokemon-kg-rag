# 项目进度

## 当前状态
- 阶段：第一阶段（9/10 汇报）
- 分支：`stage1-final`（多分支合并后代码基线）
- 状态：代码合入完成；待组内 Aura 全量重建后跑最终验证

## 阶段一已完成能力
1. 确定性图谱：Pokemon 1025 / Form 1320 / Move 953 / Ability 322 / Type 18 / EggGroup 16 / RegionDex 24
2. 关系：LEARNS 82833 / HAS_ABILITY 2866 / HAS_TYPE 2064 / IN_EGG_GROUP 1671 / EVOLVES_TO 485 / HITS_TYPE 324 / TYPE_MOD 22 / IN_DEX 5973
3. 文本块：30781 个 entity Chunk（由 `src/build_graph.py` 实际生成）
4. 本地 BGE-M3 向量化、向量索引/约束脚本
5. 实体邻域图谱事实注入（GraphRAG 模式），支持 `use_graph=false` 对照普通 RAG
6. 结构化直答路由：进化、克制、特性、策略、关系；`/test` 自测页
7. 成员三评测：5 个代表问题普通 RAG vs GraphRAG 实测报告

## 待验证/待办（见 docs/stage1-integration.md）
- 组内 Aura 使用 `python scripts/load_engine_out.py --clean` 完成一次增强 Schema 重建
- 重跑 `scripts/embed_vectors.py`、`setup_indexes.py`、`check_graph.py`
- 重跑 `smoke_rag.py`、`smoke_multiqa.py`、`compare_rag.py` 并更新实际输出
- 修复本地 `.env` 第 9 行换行问题
- review `stage1-final` 后合入 `master`，打 tag `v0.6-stage1`
# 项目进度

## 当前状态
- 阶段：第一阶段（9/10 汇报）
- 分支：`stage1-final`（多分支合并后代码基线）
- 状态：组内 Aura 已完成增强 Schema 入库并补齐 DESCRIBES；检索/直答冒烟通过

## 阶段一已完成能力
1. 确定性图谱：Pokemon 1025 / Form 1320 / Move 935 / Ability 318 / Type 18 / EggGroup 16 / RegionDex 24 / Chunk 30728
2. 关系：LEARNS 82833 / HAS_ABILITY 2866 / HAS_TYPE 2064 / IN_EGG_GROUP 1671 / EVOLVES_TO 485 / HITS_TYPE 324 / TYPE_MOD 22 / IN_DEX 5973 / DESCRIBES 30728
3. 文本块：30728 个 entity Chunk（导入按 chunk_id 去重；每个块经 DESCRIBES 指向实体）
4. 已合入 `feat/frontend`：示例问题、loading、错误提示、子图节点配色/图例
5. 本地 BGE-M3 向量化、向量索引/约束脚本
6. 实体邻域图谱事实注入（GraphRAG 模式），支持 `use_graph=false` 对照普通 RAG
7. 结构化直答路由：进化、克制、特性、策略；`/test` 自测页
8. 成员三评测：5 个代表问题普通 RAG vs GraphRAG 实测报告

## 待验证/待办（见 docs/stage1-integration.md）
- [x] 组内 Aura 使用 `python scripts/load_engine_out.py --clean` 完成一次增强 Schema 重建
- [x] 重跑 `scripts/embed_vectors.py`、`setup_indexes.py`、`check_graph.py`
- [x] 重跑 `smoke_rag.py`、`smoke_multiqa.py`（14/14）、`smoke_ask.py`
- [ ] 用最终库重跑 `compare_rag.py` 并把最后一轮答案写进 `docs/evaluation.md`
- [x] 本地 `.env` 已修正（`.env` 不入库）
- [ ] 小组 review `stage1-final` 后合入 `master`，打 tag `v0.6-stage1`

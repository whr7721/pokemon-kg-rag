# 项目进度

## 当前状态
- 阶段：第二阶段（9/15 汇报）
- 分支：`stage2-final`（成员一/二/三分支整合 + 成员四前端补齐 + 成员五架构收口）
- 状态：代码、端到端评测（22 题）、检索消融（19 题）三处口径已对齐；剩余汇报材料、演示脚本与最终 tag

## 阶段二已合入能力
1. 成员一（数据与图谱）：唯一约束补齐、双属性受击块（hit-profile）、LEARNS/IN_DEX 关系块聚合、`scripts/check_quality.py` 质量门槛
2. 成员二（向量与检索）：混合检索（向量 + 全文 + RRF）、PolyG 查询规划、PathRAG 进化证据路径、KG²RAG 图扩展、消融与调参脚本
3. 成员三（生成与评测）：22 题三路评测、few-shot 与防幻觉 Prompt、`run_eval.py` / `score_eval.py`
4. 成员四（应用与前端）：GraphRAG/对照模式切换、模式徽标、子图节点点击查看详情、`/api/compare` 与 `/api/entity`
5. 成员五（架构与集成）：统一 `GraphAccess` 图访问层、`RagRouter` 路由、三成员分支整合
6. 集成收口：前端检索路径实时切换（`vector` / `fulltext` / `hybrid` / `hybrid_graph`）、消融数字与最终代码对齐

## 待办（9/15 前）
- [x] 用最终整合代码重跑 `scripts/run_eval.py` + `scripts/score_eval.py`，让评测结果与最终混合检索一致（GraphRAG 4.05/5、77%；naive RAG 2.86/5、50%；structured 1.41/5、27%）
- [x] `docs/evaluation.md` 与最终代码口径对齐
- [x] `scripts/run_ablation.py` 重跑四路消融并对齐 `docs/retrieval.md`（vector 9/19、fulltext 3/19、hybrid 9/19、hybrid_graph 14/19）
- [ ] README、架构图、演示脚本、汇报 PPT（成员五）
- [ ] 最终核对后打 tag `v1.0-stage2`

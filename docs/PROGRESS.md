# 项目进度

## 当前状态
- 阶段：第二阶段（9/15 汇报）
- 分支：`stage2-final`（成员一/二/三分支整合 + 成员四前端补齐 + 成员五架构收口）
- 状态：代码层面二阶段最终版；评测结果与最终代码口径仍需最后核对

## 阶段二已合入能力
1. 成员一（数据与图谱）：唯一约束补齐、双属性受击块（hit-profile）、LEARNS/IN_DEX 关系块聚合、`scripts/check_quality.py` 质量门槛
2. 成员二（向量与检索）：混合检索（向量 + 全文 + RRF）、PolyG 查询规划、PathRAG 进化证据路径、KG²RAG 图扩展、消融与调参脚本
3. 成员三（生成与评测）：22 题三路评测、few-shot 与防幻觉 Prompt、`run_eval.py` / `score_eval.py`
4. 成员四（应用与前端）：GraphRAG/对照模式切换、模式徽标、子图节点点击查看详情、`/api/compare` 与 `/api/entity`
5. 成员五（架构与集成）：统一 `GraphAccess` 图访问层、`RagRouter` 路由、三成员分支整合

## 待办（9/15 前）
- [ ] 用最终整合代码重跑 `scripts/run_eval.py` + `scripts/score_eval.py`，让评测结果与最终混合检索一致
- [ ] `docs/evaluation.md` 与最终代码口径对齐
- [ ] README、架构图、演示脚本、汇报 PPT（成员五）
- [ ] 最终核对后打 tag `v1.0-stage2`

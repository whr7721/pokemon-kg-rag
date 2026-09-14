# 阶段一最终版集成说明

> 更新：2026-09-09
> 分支：`master` / tag `v0.6-stage1`（由 `stage1-final` 合入）
> 目标：把 `master`、`member1`、`member1-1`、`feat/retrieval`、`feat/生成评测` 合为一份可在 9/10 汇报使用的代码基线。

## 1. 分支贡献与合入内容

| 分支/来源 | 合入能力 | 合入文件示例 |
| --- | --- | --- |
| `master` | 基础 GraphRAG、Web 证据/子图、README | `src/templates/index.html`、基础依赖 |
| `member1` | 结构化直答路由（进化/克制/关系/特性/策略/建议）与 `/test` | `src/multi_qa.py`、`src/templates/tester.html`、`scripts/smoke_multiqa.py` |
| `member1-1` | 可复现增强 Schema 建库、HTTP Query API 客户端、批量向量化 | `src/build_engine.py`、`common/neo_http.py`、`scripts/embed_vectors.py` |
| `feat/retrieval` | 实体邻域图谱事实注入、RAG vs GraphRAG 双模式 | `src/rag.py` 检索部分、`scripts/compare_rag.py` |
| `feat/生成评测` | 5 个代表问题的普通 RAG vs GraphRAG 实测报告 | `docs/evaluation.md` |
| `feat/frontend` | 示例问题、loading、错误提示、子图节点配色/图例 | `src/templates/index.html` |

## 2. 最终口径（成员必须统一）

- Neo4j 使用**组内统一库**；不把正式项目绑定到任何成员个人云账号。
- 向量默认**本地 BAAI/bge-m3**（1024 维）；`.env` 中 `EMBED_ENGINE=bge`。
- 大模型固定使用学校平台 `tju-llm`。
- 增强 Schema 的向量索引名统一为 `embedding_Chunk`。
- 建库、向量化、建索引全部可由仓库脚本复现。

## 3. 从零重建顺序

```powershell
python src\build_graph.py
python scripts\load_engine_out.py --clean
python scripts\embed_vectors.py
python scripts\setup_indexes.py
python scripts\check_graph.py
python scripts\smoke_rag.py
python scripts\smoke_multiqa.py
python scripts\compare_rag.py
python src\app.py
```

`load_engine_out.py --clean` 会清空当前数据库，只能由约定好的一个成员执行。

## 4. 9/10 汇报前待办

- [x] 已合入 `feat/frontend` 前端增强。
- [x] 本地 `.env` 修正：已把 `EMBED_DIM=1024HF_ENDPOINT=...` 拆成两行（`.env` 不入库）。
- [x] 组内 Aura 已按增强 Schema 全量导入，并补齐 `Chunk -[:DESCRIBES]-> 实体`。
- [x] 已重跑 `scripts/smoke_rag.py` / `smoke_multiqa.py`（14/14）/ `smoke_ask.py`。
- [ ] 重跑 `scripts/compare_rag.py`，把最后一轮答案/5 题对比更新到 `docs/evaluation.md`。
- [x] 小组已 review `stage1-final`，并把合入结果打到 `master`、打 tag `v0.6-stage1`。
- [ ] 每次里程碑后做阶段性 `git commit` + `git tag` + 推 Gitee；小组成员用分支开发，不要直接改 `master`。

## 5. 当前明确边界

- `member1-1` 的确定性建库已包含：Form 形态、属性/特性/蛋群/招式、进化条件、18×18 属性克制、特性变体 `TYPE_MOD`、地区图鉴。
- 成员一曾验证的“宝可梦叙事边”（宿敌/捕食/师徒等）来自另一份数据增强，尚未纳入 `build_engine.py`；`multi_qa.py` 与 `rag.py` 保留相关查询，但只有库中存在这类边时才生效。答辩中不要把该能力描述为“当前从原始 JSON 全量复现”。
- 生成式评测（`docs/evaluation.md`）是阶段一的重要实验证据；由于每次库/向量版本不同，汇报前应把最后一轮答案与 5/25 对比表更新为组内最终库的实际输出。

## 6. 给不熟悉 Gitee 的成员

只在自己分支工作：

```powershell
git checkout -b memberX-stage1
git add .
git commit -m "feat(memberX): 说明本次改动"
git push -u origin memberX-stage1
```

不要 `git push` 到 `master`。阶段一已由 `stage1-final` 合入 `master`；后续继续按分支开发、review 后合入。

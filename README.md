# 宝可梦知识图谱 RAG 系统（Pokémon GraphRAG）

> 知识工程综合实践 · 阶段二最终版
> 数据规模：1,025 只宝可梦 / 41,507 条文本块 / 14 万条图谱关系
> 问答形式：结构化图查询直答 + 检索增强生成（GraphRAG），回答附带可追溯证据与图谱子图

本项目把宝可梦中文图鉴（[pokemon-dataset-zh](https://github.com/42arch/pokemon-dataset-zh)）用**确定性规则**构建成增强知识图谱（Neo4j AuraDB），并将实体、关系与受击倍率渲染为文本块做向量化；问答时先由 MultiQA 尝试**结构化直答**，未命中再走 GraphRAG：混合检索（向量 + 全文 + RRF）召回文本块，沿 `DESCRIBES` 回到图谱实体注入邻域事实，最后由学校 Qwen（tju-llm）生成带证据的回答。

---

## 目录

- [一、核心亮点](#一核心亮点)
- [二、系统架构](#二系统架构)
- [三、技术栈](#三技术栈)
- [四、知识图谱 Schema 与规模](#四知识图谱-schema-与规模)
- [五、从零复现：数据与建库流水线](#五从零复现数据与建库流水线)
- [六、启动 Web 演示](#六启动-web-演示)
- [七、API](#七api)
- [八、评测与实验结果](#八评测与实验结果)
- [九、目录结构](#九目录结构)
- [十、文档导航](#十文档导航)
- [十一、Git 协作与里程碑](#十一git-协作与里程碑)
- [十二、已知边界与后续工作](#十二已知边界与后续工作)
- [十三、致谢](#十三致谢)

---

## 一、核心亮点

| 亮点 | 说明 |
| --- | --- |
| **四路检索可实时切换** | 前端下拉框选择 `仅向量 / 仅全文 / 混合 / 混合+图扩展`，回答徽标与证据区同步显示当前路径，既是交互功能也是消融实验的现场演示入口 |
| **KG²RAG 图扩展** | 命中实体后沿 `HITS_TYPE` / `HAS_TYPE` / `EVOLVES_TO` 等结构扩展候选实体，把「谁克制阿柏怪」这类纯语义检索完全召不回的题从 0 翻到命中 |
| **双属性受击块** | 为 1,019 个默认形态预生成全属性受击倍率文本块，把双属性乘法从大模型心算变成可检索的图谱事实，显著降低克制类幻觉 |
| **统一问答路由** | 回应阶段一「MultiQA 与 GraphRAG 都查图、却是两个独立模块」的反馈：两者收敛为 `RagRouter` 下的两种策略，共享同一个 `GraphAccess` 图访问层，不再各自维护连接与克制表 |
| **可复现评测** | 22 题三路生成评测 + 19 题四路检索消融 + 参数网格搜索，全部有一键脚本与留档结果 |

## 二、系统架构

```text
用户问题
  │
  ▼
Flask app.py  ──►  RagRouter（统一入口，返回结构固定）
                      │
                      ├─► MultiQA 策略：进化 / 克制 / 特性 / 招式 / 策略 / 关系类问题
                      │       确定性 Cypher 直查，不经大模型，快且零幻觉
                      │
                      └─► GraphRAG 策略：开放问题
                              ① 混合检索：向量召回 + 实体名全文召回 ──RRF 融合──► Top-K 文本块
                              ② 图扩展（KG²RAG）：沿属性克制 / 进化链 / 特性 / 蛋群扩展候选实体
                              ③ 事实注入：取命中实体的邻域事实，与文本块一起拼入 Prompt
                              ④ 学校 Qwen 生成：带证据与「资料不足」兜底
                      │
                      ▼
                GraphAccess（唯一 Neo4j 连接层、共享查询与图缓存）
                      │
                      ▼
                Neo4j AuraDB（增强知识图谱 + 41,507 条带向量文本块）
```

| 层 | 职责 | 关键文件 |
| --- | --- | --- |
| 接入层 | Flask 路由、JSON 接口、模板渲染 | `src/app.py` |
| 路由层 | 判断走结构化直答还是生成式回答 | `src/router.py` |
| 策略层 | 结构化直答 / 检索增强生成 | `src/multi_qa.py`、`src/rag.py` |
| 图访问层 | 唯一 Neo4j 连接、共享 Cypher、子图组装 | `src/graph_access.py` |
| 数据层 | 建图引擎、文本块生成、向量化 | `src/build_engine.py`、`scripts/*.py` |

## 三、技术栈

| 组件 | 选型 | 说明 |
| --- | --- | --- |
| 图数据库 | Neo4j AuraDB | 图存储 + 向量索引 + CJK 全文索引 |
| 检索框架 | neo4j-graphrag-python | `VectorCypherRetriever` 向量召回后沿关系回到实体 |
| 向量模型 | BAAI/bge-m3（1024 维） | 默认注入 `ApiEmbedder` 调用云端 OpenAI 兼容接口；也可 `EMBED_ENGINE=bge` 走本地 sentence-transformers |
| 生成模型 | tju-llm（天津大学人工智能平台） | OpenAI 兼容接口 `https://ai.tju.edu.cn/api/v3` |
| Web | Flask + 原生 HTML/JS + vis-network | 问答、证据列表、图谱子图、节点详情 |
| 运行环境 | Python 3.14 + sentence-transformers + torch | 依赖版本固定见 `requirements.txt` |

## 四、知识图谱 Schema 与规模

### 4.1 节点（`scripts/check_graph.py` 实测）

| 标签 | 数量 | 说明 |
| --- | ---: | --- |
| `Pokemon` | 1,025 | 宝可梦物种，`pokedex_id` 唯一 |
| `Form` | 1,320 | 形态（普通 / 地区形态 / 超级进化等），`id = {pokedex_id}::{形态名}` |
| `Move` | 935 | 招式 |
| `Ability` | 307 | 特性 |
| `Type` | 18 | 属性 |
| `EggGroup` | 16 | 蛋群 |
| `RegionDex` | 24 | 地区图鉴 |
| `Generation` | 9 | 世代 |
| `Chunk` | 41,507 | 文本块（实体块 + 关系块 + 受击块），全部带 1024 维向量 |

### 4.2 关系（主要）

| 关系 | 数量 | 含义 |
| --- | ---: | --- |
| `LEARNS` | 82,833 | 形态 → 可学招式 |
| `DESCRIBES` | 41,507 | 文本块 → 被描述的实体 |
| `IN_DEX` | 5,973 | 宝可梦 → 地区图鉴 |
| `HAS_ABILITY` | 2,866 | 形态 → 特性 |
| `HAS_TYPE` | 2,064 | 形态 → 属性 |
| `IN_EGG_GROUP` | 1,671 | 形态 → 蛋群 |
| `HAS_FORM` | 1,320 | 宝可梦 → 形态 |
| `BELONGS_TO_GENERATION` | 1,025 | 宝可梦 → 世代 |
| `EVOLVES_TO` | 485 | 进化边（含条件） |
| `HITS_TYPE` | 324 | 18×18 属性克制全表 |
| `TYPE_MOD` | 22 | 属性对特定特性的修正 |
| 叙事边（`RIVAL_OF` 等） | 112 | 数据增强得到的可选关系，默认不参与检索 |

### 4.3 文本块（`Chunk`）

| `kind` | 数量 | 内容 |
| --- | ---: | --- |
| 实体块（无 `kind`，按 `entity_type` 区分） | 30,714 | 图鉴描述、形态属性、招式、特性、图鉴条目等 |
| `relation` | 9,774 | 关系事实的自然语言化（进化、克制、特性持有者、蛋群、可学招式概览等） |
| `hit-profile` | 1,019 | 全属性受击倍率块，如「妙蛙种子（草/毒）受到攻击时：2 倍[冰、火、超能力、飞行]…… 」 |

线上索引：主向量索引 `embedding_Chunk`（另有 7 个实体级向量索引）、4 个 CJK 全文索引（`pokemonFulltext / abilityFulltext / moveFulltext / formFulltext`）、9 条唯一约束（7 个实体 `id` + `Chunk.chunk_id` + `Pokemon.pokedex_id`）。

## 五、从零复现：数据与建库流水线

### 5.1 环境准备

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

复制 `.env.example` 为 `.env` 并填写 Neo4j、向量服务与学校大模型三项配置（`.env` 已在 `.gitignore` 中，不会入库）：

```ini
NEO4J_URL=neo4j+s://<your-instance>.databases.neo4j.io
NEO4J_USER=<username>
NEO4J_PASSWORD=<password>
NEO4J_DB=<database>
VECTOR_INDEX=embedding_Chunk

EMBED_ENDPOINT=https://api.siliconflow.cn/v1/embeddings
EMBED_API_KEY=<key>
EMBED_MODEL=BAAI/bge-m3
EMBED_DIM=1024

LLM_ENDPOINT=https://ai.tju.edu.cn/api/v3
LLM_MODEL=tju-llm
LLM_TOKEN=<token>
```

### 5.2 五步建库

```powershell
python src\build_graph.py --out build_out     # 1) 确定性建图引擎：生成实体、关系与文本块产物
python scripts\load_engine_out.py --clean     # 2) 清空当前库并导入实体与实体块（--clean 必须显式给出）
python scripts\embed_relation_chunks.py       # 3) 关系块 + 受击块向量化入库
python scripts\embed_vectors.py               # 4) 其余文本块向量化（批次断点续跑）
python scripts\setup_indexes.py               # 5) 建向量索引 / 全文索引 / 唯一约束
```

> ⚠️ 共享数据库只允许一人执行 `--clean` 全量重建，其余成员不要并发重跑建库脚本。
> 建图与向量化可重复执行：导入按实体 `id` 去重，向量化按 `embed_model` 标记跳过已完成批次。

### 5.3 自检与冒烟

```powershell
python scripts\check_graph.py          # 节点 / 关系 / 向量覆盖计数
python scripts\check_quality.py        # 字段完整率、关系覆盖率、索引门禁（缺失即非零退出）
python scripts\check_architecture.py   # 架构约束：只有 graph_access.py 可以直接建 Neo4j 连接
python scripts\smoke_rag.py            # 混合检索召回冒烟
python scripts\smoke_multiqa.py        # 结构化直答金标用例
python scripts\compare_rag.py          # 普通 RAG vs GraphRAG 对照
```

## 六、启动 Web 演示

```powershell
python src\app.py
```

- 问答主页：`http://127.0.0.1:5000`
- 多格式自测页：`http://127.0.0.1:5000/test`

界面能力：GraphRAG / 对照模式切换、检索路径下拉框（四路）、示例问题快捷入口、加载与错误提示、证据文本块列表、可点击的图谱子图与实体详情面板。

## 七、API

| 接口 | 方法 | 请求 | 返回 |
| --- | --- | --- | --- |
| `/api/ask` | POST | `{"question": "...", "top_k": 8, "use_graph": true, "retrieval_mode": "hybrid_graph"}` | `answer`、`mode`（结构化路由类型 / `graph_rag` / `naive_rag`）、`retrieval_mode`、`evidence`、`facts`、`subgraph` |
| `/api/compare` | POST | `{"question": "...", "retrieval_mode": "hybrid"}` | `naive` 与 `graph` 两条链路的答案、证据与子图，用于对照实验 |
| `/api/entity` | GET | `?label=Pokemon&name=皮卡丘` | 实体详情（属性、特性、进化、图鉴描述等） |
| `/api/health` | GET | — | 向量索引、全文索引与库状态健康检查 |

`retrieval_mode` 取值为 `vector` / `fulltext` / `hybrid` / `hybrid_graph`（默认），非法值自动回落到 `hybrid_graph`。

## 八、评测与实验结果

### 8.1 生成质量对比（22 题三路，2026-09-13 重跑）

| 策略 | 平均分 | 答对题数（≥4 分） | 答对率 |
| --- | ---: | ---: | ---: |
| 结构化直答（MultiQA） | 1.41 / 5 | 6 / 22 | 27% |
| 普通 RAG（仅文本块） | 2.86 / 5 | 11 / 22 | 50% |
| **GraphRAG（文本块 + 图谱事实）** | **4.05 / 5** | **17 / 22** | **77%** |

GraphRAG 比普通 RAG 高 1.19 分、答对率高 27 个百分点，验证了「图谱事实注入」这一核心策略的有效性。详见 `docs/evaluation.md`。

### 8.2 检索层四路消融（19 题，`scripts/run_ablation.py`）

| 检索路径 | 构成 | 命中数 | 命中率 |
| --- | --- | ---: | ---: |
| `vector` | 仅向量召回 | 9 / 19 | 47.4% |
| `fulltext` | 仅实体名全文召回 | 3 / 19 | 15.8% |
| `hybrid` | 向量 + 全文（RRF 融合） | 9 / 19 | 47.4% |
| **`hybrid_graph`** | **向量 + 全文 + KG²RAG 图扩展** | **14 / 19** | **73.7%** |

图扩展带来的提升集中在纯语义检索的死角：克制与弱点类问题（q04 阿柏怪、q05 皮卡丘、q06 妙蛙种子、q12 饭匙蛇）由全部漏召翻盘为命中。详见 `docs/retrieval.md`。

### 8.3 参数网格（`scripts/tune_retrieval.py`）

- `RRF_K` 在 10 ~ 100 区间表现平稳，生产默认取 60；
- 召回率随 `top_k` 单调上升：4 → 52.6%、6 → 57.9%、8 → 63.2%、**12 → 73.7%**。

### 8.4 现场演示建议

结构化直答可回答的问题（属性、进化、特性持有者、克制、策略）切换检索路径只改变证据区，答案不变；要展示检索路径差异请用开放问题，例如「推荐几只适合新手的水属性宝可梦」：仅向量路径回答「资料不足」，混合 + 图扩展路径可正常给出候选。

## 九、目录结构

```text
data/raw/data/       原始宝可梦中文数据集（JSON）
src/                 建图引擎、图访问层、检索、路由、Flask 应用
  build_engine.py    确定性建图引擎（实体 / 关系 / 文本块）
  graph_access.py    唯一 Neo4j 访问层
  rag.py             GraphRAG 检索与生成
  multi_qa.py        结构化直答策略
  router.py          统一问答路由
  templates/         前端页面
scripts/             建库、向量化、索引、自检、评测脚本
common/              基于 Neo4j Query API 的轻量客户端
docs/                架构、Schema、检索、评测与进度文档
build_out/           建图产物（已 gitignore）
requirements.txt
.env.example
```

## 十、文档导航

| 文档 | 内容 |
| --- | --- |
| `docs/architecture.md` | 阶段二分层架构与统一返回结构 |
| `docs/graph_schema.md` | 节点、关系、唯一键与建图策略 |
| `docs/retrieval.md` | 混合检索、KG²RAG、参数网格与四路消融 |
| `docs/evaluation.md` | 22 题三路评测与 Prompt 改进 |
| `docs/PROGRESS.md` | 阶段进度与待办 |
| `docs/stage1-integration.md` | 阶段一多分支整合记录 |
| `docs/stage2-member1-summary.md` | 成员一：图谱数据与质量 |
| `docs/stage2-member2-summary.md` | 成员二：检索与消融 |

## 十一、Git 协作与里程碑

协作方式：`master` 保持可运行基线，成员各自在 `feat/*` 分支开发，经 review 后合并；重要节点打 tag，保证任意历史版本可 `git bisect` / `git revert` 溯源恢复。

- `v0.1-graph` → `v0.2-chunks` → `v0.3-rag` → `v0.4-web` → `v0.5-docs`：阶段一最小闭环
- `v0.6-stage1`：阶段一 9/10 汇报基线
- `v0.8-stage2`：阶段二多分支整合（图谱增强 + 混合检索 + 评测）
- `v0.9-stage2-ui`：前端检索路径切换与消融数字对齐

远程仓库：Gitee（主）`https://gitee.com/chenhongjin123/zhishigongcheng.git` ｜ GitHub（镜像）`https://github.com/chj06362-create/zhishigongchengshijian.git`

## 十二、已知边界与后续工作

- **语料边界**：数据源截至第九世代，不含实时对战环境数据（如当前使用率、配招统计），策略类问题只回答机制层面的推荐；
- **叙事边**：`RIVAL_OF` / `PREDATES_ON` 等 112 条叙事关系来自数据增强，默认不参与检索，可按需开启；
- **比较类问题**：种族值未进入图谱事实，比较类问题（如「谁特攻更高」）仍依赖文本块，是当前主要失分点；
- **检索模式**：`fulltext` 仅做实体名召回，对同义替换（悬浮 → 飘浮、蓄水 → 储水）依赖别名表，可继续扩充；
- **多轮对话**：当前为单轮问答，多轮上下文与引用编号尚未实现。

## 十三、致谢

- 数据：[42arch/pokemon-dataset-zh](https://github.com/42arch/pokemon-dataset-zh)
- 检索框架：[neo4j/neo4j-graphrag-python](https://github.com/neo4j/neo4j-graphrag-python)
- 向量模型：BAAI/bge-m3；大模型服务：天津大学人工智能平台

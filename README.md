# 宝可梦知识图谱 RAG 系统

基于 Neo4j 知识图谱与检索增强生成（RAG）的宝可梦中文知识问答系统，用于“知识工程综合实践”第一阶段/最终阶段汇报。

项目把宝可梦中文图鉴 JSON 通过**确定性规则**构建为增强知识图谱（含形态 `Form`、18×18 属性克制 `HITS_TYPE`、进化条件等），规则生成文本块并写入 Neo4j 向量索引；问答时先向量召回，再沿 `DESCRIBES` 回到图谱实体、注入实体邻域事实，最后由学校 Qwen 生成带证据的回答。

## 阶段一最终版包含的能力

| 模块 | 说明 | 主要来源 |
| --- | --- | --- |
| 基础图谱 + Web 演示 | 最小 GraphRAG 闭环：建图、切块、向量化、Flask、证据与子图 | `master` |
| 可复现增强 Schema | `Form/HITS_TYPE/TYPE_MOD/LEARNS` 等确定性建库引擎 | `member1-1` |
| 结构化直答路由 | 进化/克制/特性/策略/关系等图查询直答 + `/test` 自测页 | `member1` |
| 实体邻域事实注入 | 普通 RAG vs GraphRAG 双模式对照、图谱结构化事实进 Prompt | `feat/retrieval`、`feat/生成评测` |

## 技术栈

- Neo4j AuraDB：图存储、向量索引
- neo4j-graphrag-python：`VectorCypherRetriever`
- BAAI/bge-m3：本地文本向量模型，1024 维（默认本地；`.env` 可切换 OpenAI-compatible API）
- TJU Qwen API：`tju-llm` 大模型生成
- Flask + HTML + vis-network：问答演示界面
- Python 3.14 + sentence-transformers

## 系统流程

```
JSON 数据
  -> src/build_engine.py 确定性建图产物（build_out，不入库）
  -> scripts/load_engine_out.py 写入 Neo4j（--clean 才清库）
  -> scripts/embed_vectors.py 本地 BGE-M3 写入 Chunk.embedding
  -> scripts/setup_indexes.py 向量索引 + 唯一约束
用户问题
  -> /api/ask 先走 MultiQA 结构化直答
  -> 未命中回落 PokemonGraphRAG：向量召回 -> DESCRIBES 回到实体
  -> 注入实体邻域事实 -> 学校 Qwen 生成 -> 回答 + 证据 + 子图
```

## 快速开始

```powershell
cd C:\Users\Hongjin Chen\zhishigongcheng
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

按根目录 `.env.example` 创建本地 `.env`（连接信息与密钥不入库）。从零重建：

```powershell
python src\build_graph.py                  # 生成 build_out 产物
python scripts\load_engine_out.py --clean  # 显式清空当前 Neo4j 库并导入
python scripts\embed_vectors.py            # 本地 BGE-M3 向量化
python scripts\setup_indexes.py            # 向量/约束索引
python scripts\check_graph.py              # 图计数校验
python scripts\smoke_rag.py               # 向量召回冒烟
python scripts\smoke_multiqa.py            # 结构化问答金标
python scripts\compare_rag.py              # RAG vs GraphRAG 对照实验
python src\app.py                          # Web 演示
```

浏览器打开 `http://127.0.0.1:5000`；自测页为 `http://127.0.0.1:5000/test`。

> 共享 Neo4j 库只允许约定好的一个人执行 `--clean` 全量重建；其余成员不要并发重跑建图脚本。

## 图谱 Schema（增强版）

- `Pokemon`：`pokedex_id` 唯一键，保存名称、分类、图鉴文本等。
- `Form`：`id = {pokedex_id}::{形态名}`，保存单形态属性、特性、蛋群、种族值、招式边。
- `Move` / `Ability` / `Type` / `EggGroup` / `RegionDex`：参考实体，均以 `id` 对齐。
- `Chunk`：`chunk_id` 唯一键，保存 `text/kind/entity_type/entity_id/embedding`，经 `DESCRIBES` 指向实体。
- 主要关系：`HAS_FORM`、`HAS_TYPE`、`HAS_ABILITY`、`IN_EGG_GROUP`、`LEARNS`、`EVOLVES_TO`、`HITS_TYPE`、`TYPE_MOD`、`IN_DEX`。

当前数据集的确定性产物基线（以实际运行 `check_graph.py` 为准；原始 JSONL 含少量同名重复，导入按 id 去重）：

- 节点：Pokemon 1025 / Form 1320 / Move 935 / Ability 318 / Type 18 / EggGroup 16 / RegionDex 24 / Chunk 30728
- 关系：LEARNS 82833 / IN_DEX 5973 / HAS_ABILITY 2866 / HAS_TYPE 2064 / IN_EGG_GROUP 1671 / HAS_FORM 1320 / EVOLVES_TO 485 / HITS_TYPE 324 / TYPE_MOD 22 / DESCRIBES 30728
- 文本块：30728 个 entity Chunk

## API

- `POST /api/ask`，请求体：`{"question": "皮卡丘的隐藏特性是什么？", "use_graph": true, "top_k": 8}`
- 返回：`answer`、`mode`（结构化路由类型 / `graph_rag` / `naive_rag`）、`evidence`、`facts`、`subgraph`
- `GET /test`：多格式问答自测页
- `GET /api/health`：健康检查

## Git 里程碑

- `v0.1-graph` / `v0.2-chunks` / `v0.3-rag` / `v0.4-web` / `v0.5-docs`
- `stage1-final`：阶段一多分支合入的最终版本

## 已知边界

- 进化/克制/特性/招式等能力可完全由当前数据集重建；成员一早期验证过的“叙事边（宿敌/捕食等）”属于数据增强，尚未包含在 `build_engine.py` 中，相关代码会按需保留为可选能力，答辩时应说明覆盖范围。
- `.env`、`.env.example` 不包含任何真实密钥；正式运行前请补全连接信息。

## 目录结构

```text
data/raw/data/     原始 JSON 数据集
common/            轻量 Neo4j Query API 客户端
docs/              进度、Schema、检索与评测文档（含 graph_schema.md）
scripts/           校验、建索引、向量化、冒烟与对照脚本
src/               核心代码与 Flask 应用
src/templates/     前端页面
requirements.txt
.env.example
```

## 致谢

- 数据：https://github.com/42arch/pokemon-dataset-zh
- 框架：https://github.com/neo4j/neo4j-graphrag-python
- 向量：BAAI/bge-m3；大模型：天津大学人工智能平台

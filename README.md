# 宝可梦知识图谱 RAG 系统

基于 Neo4j 知识图谱与检索增强生成（RAG）的宝可梦知识问答系统。

系统将宝可梦中文图鉴 JSON 数据集通过**确定性规则**构建为知识图谱，同时规则化生成自包含文本块；使用本地 BGE-M3 模型生成 1024 维向量，在 Neo4j 中完成向量召回后，沿 `DESCRIBES` 关系回到图谱实体，并把实体邻域事实注入上下文，最终由大模型生成带证据、可溯源的回答。

## 技术栈

- Neo4j AuraDB：图存储、向量索引、全文索引
- neo4j-graphrag-python：`VectorCypherRetriever`、`GraphRAG`
- BAAI/bge-m3：本地文本向量模型，1024 维
- TJU Qwen API：`tju-llm` 大模型生成
- Flask + HTML + vis-network：问答演示界面
- Python 3.14 + pandas + pytest

## 系统流程

```
JSON 数据
  -> 确定性图谱构建（Pokemon / Move / Ability / Type / EggGroup）
  -> 规则化文本切块（Chunk 3314）
  -> BGE-M3 向量化并写入 Chunk.embedding
  -> 创建向量索引与全文索引
用户问题
  -> 向量召回相似 Chunk
  -> 经 DESCRIBES 回到实体并扩展图谱邻域
  -> 上下文增强 + LLM 生成
  -> 回答 + 证据片段 + 子图
```

## 快速开始

```powershell
cd C:\Users\Hongjin Chen\zhishigongcheng
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

按根目录 `.env.example` 的字段创建本地 `.env`（包含 Neo4j Aura 连接、LLM 令牌、向量模型名等；`.env` 不入库）。随后依次执行：

```powershell
python src\build_graph.py
python src\build_chunks.py
python scripts\setup_indexes.py
python src\app.py
```

浏览器打开 `http://127.0.0.1:5000` 即可提问。

## 图谱 Schema

- `Pokemon`：以 `pokedex_id` 为唯一键，保存名称、属性、分类、身高、体重、描述等。
- `Move` / `Ability`：以中文名 `name_zh` 对齐，保存招式与特性文本信息。
- `Type`、`EggGroup`、`Generation`：属性、蛋组、世代节点。
- 关系：`HAS_TYPE`、`HAS_ABILITY`、`BELONGS_TO_EGG_GROUP`、`BELONGS_TO_GENERATION`、`EVOLVES_TO`、`LEARNS_MOVE`、`STRONG_AGAINST`、`RESISTS`、`IMMUNE_TO`。
- `Chunk`：以 `chunk_id` 为唯一键，存 `text`、`kind`、`entity_type`、`entity_id`、`embedding`；通过 `DESCRIBES` 指向图谱实体。

## 图统计基线

- 节点：Pokemon 1025 / Move 935 / Ability 309 / EggGroup 28 / Type 18 / Generation 9
- 文本块：Chunk 3314 = pokemon 1025 + pokemon_moves 1025 + move 953 + ability 311
- 主要关系：LEARNS_MOVE 56841、HAS_ABILITY 2413、HAS_TYPE 1551、EVOLVES_TO 487 等

## 里程碑

- `v0.1-graph`：知识图谱建库与校验
- `v0.2-chunks`：文本切块、本地向量、向量/全文索引
- `v0.3-rag`：最小 GraphRAG 问答流程
- `v0.4-web`：Flask API + 前端证据与子图展示

## API

- `POST /api/ask`，请求体：`{"question": "皮卡丘是什么属性？"}`
- 返回：`answer`、`evidence`、`subgraph`
- `GET /api/health`：健康检查

## 目录结构

```
data/raw/data/     原始 JSON 数据集
docs/PROGRESS.md   项目进度
scripts/           校验、建索引、冒烟测试脚本
src/               核心代码与 Flask 应用
src/templates/     前端页面
requirements.txt
.env.example
```

## 致谢

- 数据：https://github.com/42arch/pokemon-dataset-zh
- 框架：https://github.com/neo4j/neo4j-graphrag-python
- 向量：BAAI/bge-m3；大模型：天津大学人工智能平台

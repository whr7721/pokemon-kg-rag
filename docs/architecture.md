# 问答架构说明（阶段二）

## 1. 设计目标

针对阶段一反馈“MultiQA 和 GraphRAG 都做图查询、功能重复，但又是两个独立模块”，阶段二把系统拆成两层：

```text
Flask app.py
   -> RagRouter（统一入口）
        -> MultiQA 策略（确定性图查询直答）
        -> GraphRAG 策略（检索 + 图谱事实 + 大模型生成）
        -> GraphAccess（唯一 Neo4j 连接与共享图数据）
             -> Neo4j AuraDB
```

MultiQA 与 GraphRAG 只是两种回答策略，不再各自维护独立的 Neo4j 连接和重复的类型克制表、进化图、特性持有者等查询。

## 2. 关键文件

- `src/graph_access.py`：唯一图访问层。集中管理 Neo4j driver、共享 Cypher 查询、图级缓存、子图组装。
- `src/router.py`：统一问答入口。先尝试结构化直答，未命中再走 GraphRAG。
- `src/multi_qa.py`：结构化直答策略，调用 `GraphAccess`。
- `src/rag.py`：GraphRAG 策略，调用 `GraphAccess`、向量模型和大模型。
- `src/app.py`：只调用 `RagRouter`，不再直接持有 MultiQA 或 GraphRAG。

## 3. 统一返回结构

`RagRouter.answer()` 始终返回：

- `answer`
- `mode`：`evolution` / `counter` / `relation` / `ability` / `strategy` / `suggest` / `graph_rag` / `naive_rag`
- `evidence`
- `facts`
- `subgraph`

前端不关心具体走的是哪个模块，只需要消费统一结构。

## 4. 架构自检

运行：

```powershell
python scripts\check_architecture.py
```

该脚本会确认：

- `multi_qa.py`、`rag.py`、`app.py` 不再直接导入 `neo4j.GraphDatabase`；
- 三个模块共用同一个 `GraphAccess` 实例；
- Neo4j driver 只在 `graph_access.py` 中创建。

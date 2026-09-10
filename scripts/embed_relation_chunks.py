"""把核心关系句向量化入库，供「公平对照实验」使用。

背景：`build_engine.py` 生成的 relation_chunks（约 9.6 万条关系句）未随
`load_engine_out.py` 入库，导致库里没有任何“谁进化成谁”“什么克制什么”的句子，
普通 RAG 对这类问题只能答“资料不足”。

本脚本只导入核心子集（约 800 条），不导入全量：
    EVOLVES_TO  进化关系（含条件原文）
    HITS_TYPE   属性克制倍率

每条形如「小火龙 进化为 火恐龙（升级，等级16以上）。」，经 DESCRIBES 指向主语实体，
向量化后即可被向量通道召回——此时普通 RAG 与 GraphRAG 拥有完全相同的事实，
可以对比“多跳组装能力”本身，而不是比“谁的知识库里有没有这条事实”。

用法：
    python scripts/embed_relation_chunks.py --dry-run   # 只预览，不写库
    python scripts/embed_relation_chunks.py             # 写入并向量化
    python scripts/embed_relation_chunks.py --drop      # 回滚：删除本脚本写入的块
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402
from neo4j import GraphDatabase  # noqa: E402

from embedder import ApiEmbedder  # noqa: E402

load_dotenv(ROOT / ".env")

CHUNK_PREFIX = "relcore|"
EMBED_TAG = "bge-m3"   # 库内 embed_model 标记，与存量 Chunk 保持一致

METHOD_CN = {
    "level": "升级", "machine": "招式学习器", "egg": "蛋招式遗传",
    "trade": "连接交换", "item": "使用道具", "friendship": "亲密度",
    "beauty": "美丽度", "time": "时段条件", "weather": "天气条件",
    "location": "特定地点", "gender": "性别条件", "other": "特殊条件",
}
MULT_CN = {
    "0": "无效", "0.25": "0.25倍(极大抵抗)", "0.5": "0.5倍(抵抗)",
    "1": "1倍(普通)", "2": "2倍(克制)", "4": "4倍(极克制)",
}

EVO_QUERY = """
MATCH (a:Pokemon)-[r:EVOLVES_TO]->(b:Pokemon)
RETURN a.pokedex_id AS eid, a.name_zh AS sname, b.name_zh AS oname,
       r.method AS method, r.condition AS condition
"""

HIT_QUERY = """
MATCH (a:Type)-[h:HITS_TYPE]->(b:Type)
RETURN a.id AS eid, a.name_zh AS sname, b.name_zh AS oname, h.multiplier AS mult
"""


def build_rows(driver, db):
    """从库里现有关系生成关系句（措辞与 build_engine 的 relation_chunks 一致）。"""
    rows = []
    records, _, _ = driver.execute_query(EVO_QUERY, database_=db)
    for r in records:
        method = METHOD_CN.get(r["method"], r["method"] or "")
        cond = r["condition"] or ""
        text = f"{r['sname']} 进化为 {r['oname']}（{method}"
        if cond:
            text += f"，{cond}"
        text += "）。"
        rows.append({
            "chunk_id": f"{CHUNK_PREFIX}EVOLVES_TO|{r['eid']}|{r['oname']}",
            "eid": r["eid"], "label": "Pokemon", "name": r["sname"], "text": text,
        })

    records, _, _ = driver.execute_query(HIT_QUERY, database_=db)
    for r in records:
        text = (f"属性 {r['sname']} 攻击属性 {r['oname']} 时，"
                f"伤害倍率为{MULT_CN.get(str(r['mult']), str(r['mult']) + '倍')}。")
        rows.append({
            "chunk_id": f"{CHUNK_PREFIX}HITS_TYPE|{r['eid']}|{r['oname']}",
            "eid": r["eid"], "label": "Type", "name": r["sname"], "text": text,
        })
    return rows


def embed_rows(rows):
    embedder = ApiEmbedder()
    vectors = embedder.embed_texts([r["text"] for r in rows], batch_size=32)
    for r, v in zip(rows, vectors):
        r["embedding"] = v
    return EMBED_TAG


def write_rows(driver, db, rows, model, batch=100):
    written = 0
    for i in range(0, len(rows), batch):
        part = rows[i:i + batch]
        driver.execute_query(
            """
            UNWIND $rows AS row
            MERGE (c:Chunk {chunk_id: row.chunk_id})
            SET c.text = row.text, c.entity_type = row.label, c.entity_id = row.eid,
                c.name_zh = row.name, c.kind = 'relation', c.embedding = row.embedding,
                c.embed_model = $model
            """,
            {"rows": part, "model": model}, database_=db,
        )
        # 关系块挂到主语实体上：进化边主语是 Pokemon，克制边主语是 Type
        for label, key in (("Pokemon", "pokedex_id"), ("Type", "id")):
            sub = [r for r in part if r["label"] == label]
            if sub:
                driver.execute_query(
                    f"UNWIND $rows AS row "
                    f"MATCH (c:Chunk {{chunk_id: row.chunk_id}}), (e:`{label}` {{{key}: row.eid}}) "
                    "MERGE (c)-[:DESCRIBES]->(e)",
                    {"rows": sub}, database_=db,
                )
        written += len(part)
        print(f"  写入 {written}/{len(rows)}")
    return written


def main():
    ap = argparse.ArgumentParser(description="核心关系句向量化入库")
    ap.add_argument("--dry-run", action="store_true", help="只预览，不写库")
    ap.add_argument("--drop", action="store_true", help="回滚：删除本脚本写入的块")
    args = ap.parse_args()

    driver = GraphDatabase.driver(
        os.getenv("NEO4J_URL"),
        auth=(os.getenv("NEO4J_USER"), os.getenv("NEO4J_PASSWORD")),
    )
    db = os.getenv("NEO4J_DB")

    if args.drop:
        _, summary, _ = driver.execute_query(
            "MATCH (c:Chunk) WHERE c.chunk_id STARTS WITH $p DETACH DELETE c",
            {"p": CHUNK_PREFIX}, database_=db,
        )
        print("已删除关系块：", summary.counters.nodes_deleted)
        driver.close()
        return

    rows = build_rows(driver, db)
    print(f"待入库关系句：{len(rows)} 条（进化 "
          f"{sum(1 for r in rows if r['label'] == 'Pokemon')} / 克制 "
          f"{sum(1 for r in rows if r['label'] == 'Type')}）")
    for r in rows[:3]:
        print("  样例：", r["text"])

    if args.dry_run:
        print("--dry-run：未写入。去掉该参数即执行入库与向量化。")
        driver.close()
        return

    model = embed_rows(rows)
    written = write_rows(driver, db, rows, model)
    print(f"完成：{written} 条关系块已入库并向量化（embed_model={model}）")
    print("提示：向量索引 embedding_Chunk 会自动索引新写入的向量。")
    driver.close()


if __name__ == "__main__":
    main()

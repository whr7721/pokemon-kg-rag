# -*- coding: utf-8 -*-
"""关系块 / 受击块向量化入库。

文本与 `chunk_id` 全部取自 src/build_engine.py 的产物（build_out/chunks/*.jsonl），
本脚本只做「主语归一 → 嵌入 → 写库」，不再自行拼措辞，避免两处措辞漂移。
写入的块与实体块共享 `embedding_Chunk` 向量索引，供普通 RAG 路径召回关系事实。

用法（仓库根，先 `python src/build_graph.py --out build_out` 生成产物）：
    python scripts/embed_relation_chunks.py --dry-run
    python scripts/embed_relation_chunks.py [--batch 32] [--limit N]
    python scripts/embed_relation_chunks.py --drop      # 回滚本类块
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from common import neo_http  # noqa: E402
from embedder import ApiEmbedder  # noqa: E402

OUT = ROOT / "build_out"
EMBED_TAG = "bge-m3"          # 库内 embed_model 标记，与实体块保持一致
KINDS = ["relation", "hit-profile"]


def normalize_subject(subject: str):
    """关系块主语 -> (实体标签, 实体 id)。

    Form 主语形如 `0001::妙蛙种子`，检索层只认父 Pokemon，因此取 `::` 前缀。
    """
    if "::" in subject:
        return "Pokemon", subject.split("::", 1)[0]
    if subject.startswith("type:"):
        return "Type", subject
    if subject.startswith("ability:"):
        return "Ability", subject
    return "Pokemon", subject


def load_rows():
    """读建图产物：关系块 + 受击块。"""
    rel_path = OUT / "chunks" / "relation_chunks.jsonl"
    ent_path = OUT / "chunks" / "entity_chunks.jsonl"
    if not rel_path.exists() or not ent_path.exists():
        raise SystemExit(
            f"缺少建图产物 {rel_path} / {ent_path}；"
            "请先运行 python src/build_graph.py --out build_out")
    rows = []
    for line in rel_path.open(encoding="utf-8"):
        c = json.loads(line)
        label, eid = normalize_subject(c["subject"])
        rows.append({"chunk_id": c["id"], "text": c["text"], "kind": "relation",
                     "label": label, "eid": eid, "name": c.get("subject_name") or ""})
    for line in ent_path.open(encoding="utf-8"):
        c = json.loads(line)
        if c.get("kind") != "hit-profile":
            continue
        rows.append({"chunk_id": c["id"], "text": c["text"], "kind": "hit-profile",
                     "label": c.get("entity_label"), "eid": c.get("entity_id"),
                     "name": c.get("name_zh") or ""})
    return rows


def pending(rows):
    done = {r[0] for r in neo_http.query(
        "MATCH (c:Chunk) WHERE c.embed_model = $tag AND c.kind IN $kinds "
        "RETURN c.chunk_id", {"tag": EMBED_TAG, "kinds": KINDS})}
    return [r for r in rows if r["chunk_id"] not in done]


def write_batch(batch):
    neo_http.query(
        "UNWIND $rows AS row MERGE (c:Chunk {chunk_id: row.chunk_id}) "
        "SET c.kind = row.kind, c.entity_type = row.label, c.entity_id = row.eid, "
        "    c.name_zh = row.name, c.text = row.text, "
        "    c.embedding = row.v, c.embed_model = $tag",
        {"rows": batch, "tag": EMBED_TAG}, timeout=180)
    for label in ("Pokemon", "Type", "Ability"):
        sub = [r for r in batch if r["label"] == label]
        if sub:
            neo_http.query(
                "UNWIND $rows AS row "
                f"MATCH (c:Chunk {{chunk_id: row.chunk_id}}), (e:`{label}` {{id: row.eid}}) "
                "MERGE (c)-[:DESCRIBES]->(e)",
                {"rows": sub}, timeout=180)


def drop():
    n = neo_http.scalar(
        "MATCH (c:Chunk) WHERE c.kind IN $kinds RETURN count(c)", {"kinds": KINDS})
    neo_http.query(
        "MATCH (c:Chunk) WHERE c.kind IN $kinds DETACH DELETE c",
        {"kinds": KINDS}, timeout=180)
    print(f"已删除 {n} 个关系/受击块")


def main():
    ap = argparse.ArgumentParser(description="关系块/受击块向量化入库")
    ap.add_argument("--dry-run", action="store_true", help="只预览，不写库")
    ap.add_argument("--drop", action="store_true", help="回滚：删除本类块")
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 条（调试用）")
    args = ap.parse_args()

    if args.drop:
        drop()
        return

    rows = load_rows()
    print("产物块合计:", len(rows))
    todo = pending(rows)
    if args.limit:
        todo = todo[:args.limit]
    print("待向量化:", len(todo))
    if args.dry_run or not todo:
        for r in todo[:5]:
            print(f"   {r['kind']:12} {r['chunk_id']:34} -> {r['label']}/{r['eid']}")
            print(f"      {r['text'][:80]}")
        return

    embedder = ApiEmbedder()
    done = 0
    for i in range(0, len(todo), args.batch):
        part = todo[i:i + args.batch]
        for attempt in range(6):
            try:
                vecs = embedder.embed_texts([r["text"][:900] for r in part])
                write_batch([{**r, "v": v} for r, v in zip(part, vecs)])
                break
            except Exception as exc:
                if attempt == 5:
                    raise
                print("批次失败，重试:", str(exc)[:80])
                time.sleep(2.5 * (attempt + 1))
        done += len(part)
        if done % 320 == 0 or done == len(todo):
            print(f"  写入 {done}/{len(todo)}", flush=True)
    print("完成；本类块缺向量:", neo_http.scalar(
        "MATCH (c:Chunk) WHERE c.kind IN $kinds AND c.embedding IS NULL RETURN count(c)",
        {"kinds": KINDS}))


if __name__ == "__main__":
    main()

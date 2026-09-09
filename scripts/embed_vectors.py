# -*- coding: utf-8 -*-
"""批量 BGE-M3 向量化。

读取尚未标 `embed_model='bge-m3'` 的 Chunk 文本 → src.embedder（本地
sentence-transformers + BAAI/bge-m3，1024 维；权重默认经 HF 镜像下载）→
经 common.neo_http 写回 c.embedding，并打标记以便断点续跑。

用法（仓库根）：
    python scripts/embed_vectors.py [--batch 32]
"""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from common import neo_http  # noqa: E402
from embedder import BgeM3Embedder  # noqa: E402

PAGE = 5000


def make_embedder():
    return BgeM3Embedder()


def fetch_pending():
    rows = []
    for skip in range(0, 10_000_000, PAGE):
        vals = neo_http.query(
            "MATCH (c:Chunk) WHERE c.text IS NOT NULL AND coalesce(c.embed_model,'') <> 'bge-m3' "
            "RETURN c.chunk_id AS id, c.text AS t ORDER BY c.chunk_id SKIP $sk LIMIT $pg",
            {"sk": skip, "pg": PAGE})
        rows += [{"id": v[0], "text": v[1]} for v in vals]
        if len(vals) < PAGE:
            break
        if len(rows) >= 200_000:
            break
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=32)
    args = ap.parse_args()
    embedder = make_embedder()
    rows = fetch_pending()
    print("待向量化 Chunk:", len(rows))
    done = 0
    for i in range(0, len(rows), args.batch):
        part = rows[i:i + args.batch]
        for attempt in range(6):
            try:
                vecs = embedder.embed_texts([str(x["text"])[:900] for x in part])
                payload = [{"id": x["id"], "v": v} for x, v in zip(part, vecs)]
                neo_http.query(
                    "UNWIND $rows AS row MATCH (c:Chunk {chunk_id: row.id}) "
                    "SET c.embedding = row.v, c.embed_model = 'bge-m3'",
                    {"rows": payload}, timeout=120)
                break
            except Exception as e:
                if attempt == 5:
                    raise
                print("批次失败，重试:", str(e)[:80])
                time.sleep(2.5 * (attempt + 1))
        done += len(part)
        if done % 1200 == 0 or done == len(rows):
            print("进度:", done, "/", len(rows))
    print("完成；缺向量:", neo_http.scalar("MATCH (c:Chunk) WHERE c.embedding IS NULL RETURN count(c)"))


if __name__ == "__main__":
    main()

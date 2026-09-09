"""向量化封装：本地 BGE-M3（小组默认），也可切换任意 OpenAI-compatible 向量 API。

通过 .env 的 EMBED_ENGINE 选择：
    EMBED_ENGINE=bge         -> BgeM3Embedder（默认，本地 sentence-transformers）
    EMBED_ENGINE=dashscope   -> DashScopeEmbedder（DashScope / SiliconFlow 等
                              OpenAI-compatible embeddings；需 EMBED_KEY/EMBED_MODEL）
"""
from __future__ import annotations

import json
import os
import urllib.request
from dotenv import load_dotenv

load_dotenv()
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")


class BgeM3Embedder:
    """本地 BGE-M3 向量化封装。"""

    def __init__(self, model_name=None):
        from sentence_transformers import SentenceTransformer
        self.model_name = model_name or os.getenv("EMBED_MODEL", "BAAI/bge-m3")
        self.model = SentenceTransformer(self.model_name)

    def embed_texts(self, texts, batch_size=32):
        return self.model.encode(
            texts,
            normalize_embeddings=True,
            batch_size=batch_size,
            show_progress_bar=True,
        ).tolist()

    def embed_query(self, text):
        vec = self.model.encode([text], normalize_embeddings=True)
        return vec[0].tolist()


class DashScopeEmbedder:
    """DashScope / SiliconFlow 等 OpenAI-compatible embeddings。

    本阶段正式口径仍是本地 BGE-M3；仅当库内向量由 API 模型生成时才切换此引擎。
    """

    def __init__(self, endpoint=None, api_key=None, model=None):
        self.endpoint = (endpoint or os.getenv("EMBED_ENDPOINT")
                         or "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings")
        self.api_key = api_key or os.getenv("EMBED_KEY") or os.getenv("EMBED_API_KEY")
        self.model = model or os.getenv("EMBED_MODEL", "qwen3.7-text-embedding")
        if not self.api_key:
            raise RuntimeError("EMBED_ENGINE=dashscope 需要 EMBED_KEY/EMBED_API_KEY")

    def _call(self, texts):
        body = json.dumps({"model": self.model, "input": texts}).encode()
        req = urllib.request.Request(self.endpoint, data=body, headers={
            "Authorization": "Bearer " + self.api_key,
            "Content-Type": "application/json",
        })
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return [item["embedding"] for item in data["data"]]

    def embed_texts(self, texts, batch_size=32):
        out = []
        for i in range(0, len(texts), batch_size):
            out.extend(self._call(texts[i:i + batch_size]))
        return out

    def embed_query(self, text):
        return self._call([text])[0]


def make_embedder(engine=None):
    """按 EMBED_ENGINE 返回对应 embedder。"""
    engine = (engine or os.getenv("EMBED_ENGINE") or "bge").lower()
    if engine in ("dashscope", "api"):
        return DashScopeEmbedder()
    return BgeM3Embedder()
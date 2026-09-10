"""向量化封装：OpenAI 兼容 embeddings（SiliconFlow / DashScope 等）。

统一使用云端 API，不依赖本地 torch / sentence-transformers：
    EMBED_ENDPOINT  向量服务端点（如 https://api.siliconflow.cn/v1/embeddings）
    EMBED_MODEL     模型名（默认 BAAI/bge-m3，1024 维）
    EMBED_API_KEY   访问密钥（兼容 EMBED_KEY）
"""
from __future__ import annotations

import json
import os
import urllib.request

from dotenv import load_dotenv

load_dotenv()


class ApiEmbedder:
    """OpenAI 兼容 embeddings 客户端。"""

    def __init__(self, endpoint=None, api_key=None, model=None):
        self.endpoint = endpoint or os.getenv("EMBED_ENDPOINT")
        self.api_key = api_key or os.getenv("EMBED_KEY") or os.getenv("EMBED_API_KEY")
        self.model = model or os.getenv("EMBED_MODEL", "BAAI/bge-m3")
        if not self.endpoint:
            raise RuntimeError("缺少 EMBED_ENDPOINT（向量服务端点）")
        if not self.api_key:
            raise RuntimeError("缺少 EMBED_API_KEY / EMBED_KEY（向量服务密钥）")

    def _call(self, texts):
        body = json.dumps({"model": self.model, "input": texts}).encode("utf-8")
        req = urllib.request.Request(self.endpoint, data=body, headers={
            "Authorization": "Bearer " + self.api_key,
            "Content-Type": "application/json",
        })
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        # 按返回的 index 排序，避免服务端乱序导致向量与文本错位
        return [item["embedding"] for item in sorted(data["data"], key=lambda x: x["index"])]

    def embed_texts(self, texts, batch_size=32):
        out = []
        for i in range(0, len(texts), batch_size):
            out.extend(self._call(texts[i:i + batch_size]))
        return out

    def embed_query(self, text):
        return self._call([text])[0]

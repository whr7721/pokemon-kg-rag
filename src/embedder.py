"""本地 BGE-M3 向量化封装。"""
import os
from dotenv import load_dotenv

load_dotenv()
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

from sentence_transformers import SentenceTransformer

class BgeM3Embedder:
    def __init__(self, model_name=None):
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

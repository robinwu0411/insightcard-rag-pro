"""Embedding client — supports local (sentence-transformers), TEI service, and OpenAI.

Ingestion uses this to embed chunks before writing to vector store.
Backend uses this to embed user queries before retrieval.
"""
from typing import List
import logging

from shared.config import config

logger = logging.getLogger(__name__)


class EmbeddingClient:
    """Abstract embedding client."""

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        raise NotImplementedError

    def embed_one(self, text: str) -> List[float]:
        return self.embed_batch([text])[0]


class LocalEmbeddingClient(EmbeddingClient):
    """Uses sentence-transformers all-MiniLM-L6-v2 (ONNX/CPU) — no external API needed.

    Model downloads on first use and caches locally (~90MB).
    Produces 384-dim vectors matching the OpenSearch index dimension.
    """

    def __init__(self):
        from sentence_transformers import SentenceTransformer
        self._model = SentenceTransformer(config.embedding_model)
        logger.info(f"LocalEmbeddingClient initialized (model={config.embedding_model})")

    def embed_batch(self, texts):
        embeddings = self._model.encode(
            texts,
            batch_size=config.embedding_batch_size,
            convert_to_numpy=True,
        ).tolist()
        return embeddings


class TEIEmbeddingClient(EmbeddingClient):
    """Calls Hugging Face Text Embeddings Inference (TEI) service.

    Deploy TEI as a Docker container on a GPU instance:
      docker run --gpus all -p 8080:80 \
        ghcr.io/huggingface/text-embeddings-inference:1.5 \
        --model-id BAAI/bge-large-zh-v1.5
    """

    def __init__(self):
        import requests
        self._requests = requests
        self._endpoint = config.tei_endpoint
        self._batch_size = config.embedding_batch_size
        logger.info(f"TEIEmbeddingClient initialized (endpoint={self._endpoint})")

    def embed_batch(self, texts):
        all_embeddings = []
        for i in range(0, len(texts), self._batch_size):
            batch = texts[i:i + self._batch_size]
            resp = self._requests.post(
                f"{self._endpoint}/embed",
                json={"inputs": batch},
                timeout=30,
            )
            resp.raise_for_status()
            all_embeddings.extend(resp.json())
        return all_embeddings


class OpenAIEmbeddingClient(EmbeddingClient):
    """Calls OpenAI embedding API."""

    def __init__(self):
        from openai import OpenAI
        self._client = OpenAI(api_key=config.openai_api_key)
        self._model = config.embedding_model
        self._batch_size = config.embedding_batch_size
        logger.info(f"OpenAIEmbeddingClient initialized (model={self._model})")

    def embed_batch(self, texts):
        all_embeddings = []
        for i in range(0, len(texts), self._batch_size):
            batch = texts[i:i + self._batch_size]
            resp = self._client.embeddings.create(
                input=batch,
                model=self._model,
            )
            all_embeddings.extend([d.embedding for d in resp.data])
        return all_embeddings


def get_embedding_client() -> EmbeddingClient:
    """Factory: returns the configured embedding client."""
    provider = config.embedding_provider
    if provider == "tei":
        return TEIEmbeddingClient()
    elif provider == "openai":
        return OpenAIEmbeddingClient()
    return LocalEmbeddingClient()

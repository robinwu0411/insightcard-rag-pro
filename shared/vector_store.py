"""Vector store abstraction — supports ChromaDB (local dev) and Milvus (production).

Both backend (query) and ingestion (write) use this module.
"""
from typing import Optional
from shared.config import config


class VectorStore:
    """Abstract vector store interface."""

    def add(self, ids: list[str], texts: list[str], metadatas: list[dict], embeddings: list[list[float]]):
        raise NotImplementedError

    def query(self, query_embedding: list[float], top_k: int = 8, filter_metadata: Optional[dict] = None) -> list[dict]:
        raise NotImplementedError

    def count(self) -> int:
        raise NotImplementedError

    def delete(self, ids: list[str]):
        raise NotImplementedError


class ChromaVectorStore(VectorStore):
    """ChromaDB-based vector store for local development."""

    def __init__(self):
        import chromadb
        from chromadb.config import Settings
        config.chroma_db_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(
            path=str(config.chroma_db_dir),
            settings=Settings(anonymized_telemetry=False),
        )
        self._collection = self._client.get_or_create_collection(
            name=config.collection_name,
            metadata={"description": "Vendor growth knowledge base"},
        )

    def add(self, ids, texts, metadatas, embeddings):
        self._collection.add(
            ids=ids,
            documents=texts,
            metadatas=metadatas,
            embeddings=embeddings,
        )

    def query(self, query_embedding, top_k=8, filter_metadata=None):
        results = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            include=["documents", "metadatas", "distances"],
            where=filter_metadata,
        )
        if not results["documents"] or not results["documents"][0]:
            return []
        return [
            {
                "text": doc,
                "metadata": meta,
                "distance": dist,
                "semantic_score": max(0.0, 1.0 - dist),
            }
            for doc, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            )
        ]

    def count(self):
        return self._collection.count()

    def delete(self, ids):
        self._collection.delete(ids=ids)


class MilvusVectorStore(VectorStore):
    """Milvus-based vector store for production."""

    def __init__(self):
        from pymilvus import connections, Collection, utility
        connections.connect(host=config.milvus_host, port=config.milvus_port)
        if not utility.has_collection(config.collection_name):
            self._create_collection()
        self._collection = Collection(config.collection_name)
        self._collection.load()

    def _create_collection(self):
        from pymilvus import FieldSchema, CollectionSchema, DataType, utility
        fields = [
            FieldSchema(name="id", dtype=DataType.VARCHAR, max_length=128, is_primary=True),
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=config.embedding_dim),
            FieldSchema(name="text", dtype=DataType.VARCHAR, max_length=65535),
            FieldSchema(name="source", dtype=DataType.VARCHAR, max_length=256),
            FieldSchema(name="section", dtype=DataType.VARCHAR, max_length=512),
            FieldSchema(name="title", dtype=DataType.VARCHAR, max_length=256),
            FieldSchema(name="timestamp", dtype=DataType.VARCHAR, max_length=32),
        ]
        schema = CollectionSchema(fields, description="Vendor growth knowledge base")
        col = Collection(config.collection_name, schema)
        col.create_index(
            "embedding",
            {
                "index_type": "HNSW",
                "metric_type": "COSINE",
                "params": {"M": 16, "efConstruction": 200},
            },
        )

    def add(self, ids, texts, metadatas, embeddings):
        data = [
            ids,
            embeddings,
            [t[:65535] for t in texts],
            [m.get("source", "")[:256] for m in metadatas],
            [m.get("section", "")[:512] for m in metadatas],
            [m.get("title", "")[:256] for m in metadatas],
            [m.get("timestamp", "")[:32] for m in metadatas],
        ]
        self._collection.insert(data)

    def query(self, query_embedding, top_k=8, filter_metadata=None):
        expr = ""
        if filter_metadata:
            conditions = [f'{k} == "{v}"' for k, v in filter_metadata.items()]
            expr = " and ".join(conditions)
        results = self._collection.search(
            data=[query_embedding],
            anns_field="embedding",
            param={"metric_type": "COSINE", "params": {"ef": 64}},
            limit=top_k,
            expr=expr,
            output_fields=["text", "source", "section", "title", "timestamp"],
        )
        if not results:
            return []
        hits = results[0]
        return [
            {
                "text": hit.entity.get("text", ""),
                "metadata": {
                    "source": hit.entity.get("source", ""),
                    "section": hit.entity.get("section", ""),
                    "title": hit.entity.get("title", ""),
                    "timestamp": hit.entity.get("timestamp", ""),
                },
                "distance": 1 - hit.score,
                "semantic_score": hit.score,
            }
            for hit in hits
        ]

    def count(self):
        return self._collection.num_entities

    def delete(self, ids):
        from pymilvus import utility
        expr = f'id in {ids}'
        self._collection.delete(expr)


def get_vector_store() -> VectorStore:
    """Factory: returns the configured vector store implementation."""
    if config.vector_store_type == "milvus":
        return MilvusVectorStore()
    return ChromaVectorStore()

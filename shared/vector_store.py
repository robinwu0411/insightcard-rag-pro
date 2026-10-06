"""Vector store abstraction — supports OpenSearch (local + production) and Milvus.

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


class OpenSearchVectorStore(VectorStore):
    """OpenSearch vector store with k-NN support.

    Auth modes:
      - none:  local dev (docker-compose OpenSearch with security disabled)
      - aws:   production (AWS SigV4 via IAM task role, no passwords)
    """

    def __init__(self):
        from opensearchpy import OpenSearch, RequestsHttpConnection

        host = config.opensearch_host.replace("https://", "").replace("http://", "")

        if config.opensearch_auth == "aws":
            import boto3
            from opensearchpy import AWSV4SignerAuth
            credentials = boto3.Session().get_credentials()
            auth = AWSV4SignerAuth(credentials, config.aws_region, "es")
        else:
            auth = None  # local dev, security plugin disabled

        self._client = OpenSearch(
            hosts=[{"host": host, "port": config.opensearch_port}],
            http_auth=auth,
            use_ssl=config.opensearch_ssl,
            verify_certs=config.opensearch_ssl,
            connection_class=RequestsHttpConnection,
            timeout=30,
        )
        self._index = config.collection_name
        self._create_index_if_not_exists()

    def _create_index_if_not_exists(self):
        from opensearchpy import NotFoundError
        try:
            self._client.indices.get(self._index)
        except NotFoundError:
            self._client.indices.create(self._index, body={
                "settings": {"index.knn": True},
                "mappings": {"properties": {
                    "embedding": {
                        "type": "knn_vector",
                        "dimension": config.embedding_dim,
                        "method": {
                            "name": "hnsw",
                            "engine": "nmslib",
                            "parameters": {"m": 16, "ef_construction": 200},
                        },
                    },
                    "text": {"type": "text"},
                    "source": {"type": "keyword"},
                    "section": {"type": "text"},
                    "title": {"type": "keyword"},
                    "timestamp": {"type": "keyword"},
                }},
            })

    def add(self, ids, texts, metadatas, embeddings):
        from opensearchpy.helpers import bulk
        actions = [{
            "_index": self._index,
            "_id": ids[i],
            "_source": {
                "embedding": embeddings[i],
                "text": texts[i],
                "source": metadatas[i].get("source", ""),
                "section": metadatas[i].get("section", ""),
                "title": metadatas[i].get("title", ""),
                "timestamp": metadatas[i].get("timestamp", ""),
            },
        } for i in range(len(ids))]
        bulk(self._client, actions)
        self._client.indices.refresh(index=self._index)

    def query(self, query_embedding, top_k=8, filter_metadata=None):
        query_body = {
            "size": top_k,
            "query": {"knn": {"embedding": {"vector": query_embedding, "k": top_k}}},
        }
        if filter_metadata:
            query_body["query"]["knn"]["embedding"]["filter"] = {
                "bool": {"filter": [{"term": {k: v}} for k, v in filter_metadata.items()]}
            }
        results = self._client.search(index=self._index, body=query_body)
        hits = results["hits"]["hits"]
        return [{
            "text": hit["_source"].get("text", ""),
            "metadata": {
                "source": hit["_source"].get("source", ""),
                "section": hit["_source"].get("section", ""),
                "title": hit["_source"].get("title", ""),
                "timestamp": hit["_source"].get("timestamp", ""),
            },
            "distance": 1 - hit["_score"],
            "semantic_score": hit["_score"],
        } for hit in hits]

    def list_sources(self, limit: int = 100) -> list[dict]:
        """Return stored chunks for debugging / source listing."""
        results = self._client.search(
            index=self._index,
            body={
                "size": limit,
                "query": {"match_all": {}},
                "_source": ["text", "source", "section", "title", "timestamp"],
            },
        )
        sources = []
        for hit in results["hits"]["hits"]:
            src = hit["_source"]
            text = src.get("text", "")
            sources.append({
                "id": hit["_id"],
                "source": src.get("source", ""),
                "section": src.get("section", ""),
                "preview": text[:120] + "..." if len(text) > 120 else text,
            })
        return sources

    def count(self):
        return self._client.count(index=self._index)["count"]

    def delete(self, ids):
        for id_ in ids:
            self._client.delete(index=self._index, id=id_, ignore=[404])
        self._client.indices.refresh(index=self._index)


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
    if config.vector_store_type == "opensearch":
        return OpenSearchVectorStore()
    elif config.vector_store_type == "milvus":
        return MilvusVectorStore()
    raise ValueError(f"Unknown vector_store_type: {config.vector_store_type}. Supported: opensearch, milvus")

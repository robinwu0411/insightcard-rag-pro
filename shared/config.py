"""Shared configuration — loaded by both backend and ingestion services.

All settings come from environment variables with sensible defaults for local dev.
In production (ECS Fargate), these are injected via task definition env vars.
"""
import os
from pathlib import Path
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    # --- AWS ---
    aws_region: str = os.environ.get("AWS_REGION", "us-east-1")
    s3_bucket: str = os.environ.get("S3_BUCKET", "insightcard-rag-docs")
    sqs_queue_url: str = os.environ.get("SQS_QUEUE_URL", "")
    sqs_dlq_url: str = os.environ.get("SQS_DLQ_URL", "")

    # --- DynamoDB ---
    dynamodb_table_metadata: str = os.environ.get("DDB_TABLE_METADATA", "rag_metadata")
    dynamodb_table_memory: str = os.environ.get("DDB_TABLE_MEMORY", "rag_short_term_memory")

    # --- Vector Store (OpenSearch / Milvus) ---
    vector_store_type: str = os.environ.get("VECTOR_STORE_TYPE", "opensearch")  # opensearch | milvus
    milvus_host: str = os.environ.get("MILVUS_HOST", "localhost")
    milvus_port: int = int(os.environ.get("MILVUS_PORT", "19530"))
    collection_name: str = os.environ.get("COLLECTION_NAME", "vendor_growth_knowledge")
    # --- OpenSearch ---
    opensearch_host: str = os.environ.get("OPENSEARCH_HOST", "localhost")
    opensearch_port: int = int(os.environ.get("OPENSEARCH_PORT", "9200"))
    opensearch_ssl: bool = os.environ.get("OPENSEARCH_SSL", "false").lower() == "true"
    # local dev: no auth; production: AWS SigV4 via IAM task role
    opensearch_auth: str = os.environ.get("OPENSEARCH_AUTH", "none")  # none | aws

    # --- Embedding ---
    embedding_provider: str = os.environ.get("EMBEDDING_PROVIDER", "local")  # local | openai | tei
    embedding_model: str = os.environ.get("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
    tei_endpoint: str = os.environ.get("TEI_ENDPOINT", "http://localhost:8080")
    openai_api_key: str = os.environ.get("OPENAI_API_KEY", "")
    openai_base_url: str = os.environ.get("OPENAI_BASE_URL", "")  # OpenAI-compatible endpoint, e.g. https://api.deepseek.com
    embedding_dim: int = int(os.environ.get("EMBEDDING_DIM", "384"))
    embedding_batch_size: int = int(os.environ.get("EMBEDDING_BATCH_SIZE", "64"))

    # --- LLM ---
    llm_provider: str = os.environ.get("LLM_PROVIDER", "openai")  # openai | bedrock
    llm_model: str = os.environ.get("LLM_MODEL", "gpt-4o-mini")
    llm_max_tokens: int = int(os.environ.get("LLM_MAX_TOKENS", "600"))
    llm_temperature: float = float(os.environ.get("LLM_TEMPERATURE", "0.3"))

    # --- RAG ---
    chunk_size: int = int(os.environ.get("CHUNK_SIZE", "512"))
    chunk_overlap: int = int(os.environ.get("CHUNK_OVERLAP", "64"))
    retrieval_top_k: int = int(os.environ.get("RETRIEVAL_TOP_K", "8"))
    rerank_top_k: int = int(os.environ.get("RERANK_TOP_K", "3"))

    # --- Server ---
    host: str = os.environ.get("HOST", "0.0.0.0")
    port: int = int(os.environ.get("PORT", "8000"))

    # --- Local dev paths ---
    knowledge_docs_dir: Path = Path(__file__).parent.parent / "backend" / "knowledge_docs"

    @property
    def use_openai(self) -> bool:
        return bool(self.openai_api_key)


config = Config()

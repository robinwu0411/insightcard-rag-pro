"""Configuration for InsightCard RAG Pro — production version."""
import os
from pathlib import Path
from shared.config import config

# Re-export for convenience
HOST = config.host
PORT = config.port
KNOWLEDGE_DOCS_DIR = config.knowledge_docs_dir
CHROMA_DB_DIR = config.chroma_db_dir
COLLECTION_NAME = config.collection_name
CHUNK_SIZE = config.chunk_size
CHUNK_OVERLAP = config.chunk_overlap
RETRIEVAL_TOP_K = config.retrieval_top_k
RERANK_TOP_K = config.rerank_top_k
USE_OPENAI = config.use_openai
OPENAI_API_KEY = config.openai_api_key
OPENAI_MODEL = config.llm_model
STREAM_CHUNK_SIZE = 3
STREAM_DELAY = 0.03

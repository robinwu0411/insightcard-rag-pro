"""DynamoDB client — handles both metadata tracking and short-term memory.

Two tables:
  1. rag_metadata: tracks document ingestion status (dedup + state machine)
  2. rag_short_term_memory: stores conversation context for the query API

DynamoDB is ideal because:
  - Single-digit millisecond latency at any scale
  - No capacity planning needed (on-demand mode)
  - Native AWS integration (IAM auth, no connection pooling)
  - TTL for automatic memory expiry
"""
import time
import uuid
import logging
from typing import Optional
from botocore.exceptions import ClientError

from shared.config import config

logger = logging.getLogger(__name__)


class DynamoDBClient:
    def __init__(self):
        import boto3
        self._dynamodb = boto3.resource("dynamodb", region_name=config.aws_region)
        self._metadata_table = self._dynamodb.Table(config.dynamodb_table_metadata)
        self._memory_table = self._dynamodb.Table(config.dynamodb_table_memory)

    # --- Metadata table: document ingestion tracking ---

    def get_doc_status(self, s3_key: str, version_id: str = "") -> Optional[dict]:
        """Check if a document has already been processed."""
        pk = f"DOC#{s3_key}"
        sk = f"VER#{version_id}" if version_id else "VER#latest"
        try:
            resp = self._metadata_table.get_item(Key={"PK": pk, "SK": sk})
            return resp.get("Item")
        except ClientError as e:
            logger.error(f"DynamoDB get_item error: {e}")
            return None

    def put_doc_status(self, s3_key: str, version_id: str, status: str,
                       chunk_count: int = 0, file_hash: str = "", error_msg: str = ""):
        """Update document processing status.

        status: pending | processing | done | failed
        """
        pk = f"DOC#{s3_key}"
        sk = f"VER#{version_id}" if version_id else "VER#latest"
        item = {
            "PK": pk,
            "SK": sk,
            "s3_key": s3_key,
            "version_id": version_id,
            "status": status,
            "chunk_count": chunk_count,
            "file_hash": file_hash,
            "error_msg": error_msg,
            "updated_at": int(time.time()),
            "ttl": int(time.time()) + 90 * 24 * 3600,  # 90-day TTL
        }
        self._metadata_table.put_item(Item=item)

    def is_processed(self, s3_key: str, file_hash: str) -> bool:
        """Check if file with same hash was already processed (dedup)."""
        existing = self.get_doc_status(s3_key)
        if existing and existing.get("file_hash") == file_hash and existing.get("status") == "done":
            return True
        return False

    # --- Memory table: short-term conversation context ---

    def save_conversation(self, session_id: str, messages: list[dict],
                          ttl_seconds: int = 3600):
        """Save conversation context for a session.

        messages: [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]
        """
        item = {
            "PK": f"SESSION#{session_id}",
            "SK": f"MSG#{uuid.uuid4().hex[:8]}",
            "session_id": session_id,
            "messages": messages,
            "created_at": int(time.time()),
            "ttl": int(time.time()) + ttl_seconds,
        }
        self._memory_table.put_item(Item=item)

    def get_conversation(self, session_id: str, limit: int = 10) -> list[dict]:
        """Retrieve recent conversation context."""
        resp = self._memory_table.query(
            KeyConditionExpression="PK = :pk",
            ExpressionAttributeValues={":pk": f"SESSION#{session_id}"},
            Limit=limit,
            ScanIndexForward=False,  # most recent first
        )
        items = resp.get("Items", [])
        if not items:
            return []
        # Merge all messages from recent sessions
        all_messages = []
        for item in reversed(items):
            all_messages.extend(item.get("messages", []))
        return all_messages[-20:]  # last 20 messages max

    def clear_conversation(self, session_id: str):
        """Clear conversation history for a session."""
        resp = self._memory_table.query(
            KeyConditionExpression="PK = :pk",
            ExpressionAttributeValues={":pk": f"SESSION#{session_id}"},
        )
        for item in resp.get("Items", []):
            self._memory_table.delete_item(Key={"PK": item["PK"], "SK": item["SK"]})


class MockDynamoDBClient:
    """In-memory mock for local development without AWS."""

    def __init__(self):
        self._metadata: dict = {}
        self._memory: dict = {}

    def get_doc_status(self, s3_key, version_id=""):
        return self._metadata.get(f"{s3_key}#{version_id}")

    def put_doc_status(self, s3_key, version_id, status, chunk_count=0, file_hash="", error_msg=""):
        self._metadata[f"{s3_key}#{version_id}"] = {
            "s3_key": s3_key, "version_id": version_id, "status": status,
            "chunk_count": chunk_count, "file_hash": file_hash, "error_msg": error_msg,
        }

    def is_processed(self, s3_key, file_hash):
        existing = self.get_doc_status(s3_key)
        return existing and existing.get("file_hash") == file_hash and existing.get("status") == "done"

    def save_conversation(self, session_id, messages, ttl_seconds=3600):
        self._memory.setdefault(session_id, []).extend(messages)

    def get_conversation(self, session_id, limit=10):
        return self._memory.get(session_id, [])[-20:]

    def clear_conversation(self, session_id):
        self._memory.pop(session_id, None)


def get_dynamodb_client():
    """Factory: returns DynamoDB client or mock for local dev.

    Local dev (OPENSEARCH_AUTH=none) uses mock — no real DynamoDB needed.
    Production (OPENSEARCH_AUTH=aws) connects to real DynamoDB via IAM.
    """
    if config.opensearch_auth == "none":
        return MockDynamoDBClient()
    try:
        return DynamoDBClient()
    except Exception as e:
        logger.warning(f"DynamoDB unavailable, using mock: {e}")
        return MockDynamoDBClient()

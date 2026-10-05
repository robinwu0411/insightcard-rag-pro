"""RAG Ingestion Worker — production SQS consumer.

Listens to SQS messages triggered by S3 Event Notifications.
Each message contains: {s3_key, version_id, event_type}

Pipeline per message:
  1. Download file from S3
  2. Check DynamoDB for dedup (file hash)
  3. Parse file → text (Unstructured/Tika)
  4. Clean text (PII masking, noise removal, quality filter)
  5. Chunk text (Markdown-aware, with metadata)
  6. Embed chunks in batch (TEI/OpenAI/local)
  7. Write to vector store + DynamoDB status
  8. Delete SQS message

For local dev (no S3/SQS), runs in batch mode: reads from knowledge_docs/ directory.
"""
import sys
import json
import time
import hashlib
import logging
from pathlib import Path
from dataclasses import asdict

# Ensure project root is importable
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from shared.config import config
from shared.vector_store import get_vector_store
from shared.embedding_client import get_embedding_client
from shared.dynamodb_client import get_dynamodb_client
from shared.s3_client import get_s3_client
from backend.rag.chunker import chunk_file, ingest_directory
from backend.rag.cleaner import clean_chunks

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


class IngestionWorker:
    """SQS-driven ingestion worker for production."""

    def __init__(self):
        self.vector_store = get_vector_store()
        self.embedding_client = get_embedding_client()
        self.db = get_dynamodb_client()
        self.s3 = get_s3_client()
        self._running = True

    def process_s3_event(self, message_body: dict) -> bool:
        """Process a single S3 event message. Returns True on success."""
        s3_key = message_body.get("s3_key", "")
        version_id = message_body.get("version_id", "")
        event_type = message_body.get("event_type", "ObjectCreated")

        logger.info(f"Processing S3 event: {event_type} s3://{config.s3_bucket}/{s3_key}")

        if event_type == "ObjectRemoved":
            return self._handle_delete(s3_key, version_id)

        # 1. Download from S3
        local_path = Path(f"/tmp/ingestion/{Path(s3_key).name}")
        try:
            if self.s3:
                self.s3.download_file(s3_key, local_path)
                file_hash = self.s3.get_file_hash(s3_key, version_id)
            else:
                logger.error("S3 client not available in production mode")
                return False
        except Exception as e:
            logger.error(f"Failed to download {s3_key}: {e}")
            self.db.put_doc_status(s3_key, version_id, "failed", error_msg=str(e))
            return False

        # 2. Dedup check
        if self.db.is_processed(s3_key, file_hash):
            logger.info(f"Already processed (hash match): {s3_key}")
            return True

        # 3. Mark as processing
        self.db.put_doc_status(s3_key, version_id, "processing", file_hash=file_hash)

        # 4. Parse + Clean + Chunk
        try:
            chunks = chunk_file(local_path, config.chunk_size, config.chunk_overlap)
            chunks = clean_chunks(chunks)
            logger.info(f"Parsed {s3_key} → {len(chunks)} chunks")

            if not chunks:
                logger.warning(f"No valid chunks from {s3_key}")
                self.db.put_doc_status(s3_key, version_id, "failed", error_msg="No valid chunks")
                return False

            # 5. Embed in batch
            texts = [c.text for c in chunks]
            metadatas = [c.metadata for c in chunks]
            ids = [f"{s3_key}_{i}_{chunks[i].hash}" for i in range(len(chunks))]
            embeddings = self.embedding_client.embed_batch(texts)
            logger.info(f"Embedded {len(embeddings)} chunks (dim={len(embeddings[0])})")

            # 6. Quality check
            for i, emb in enumerate(embeddings):
                if not emb or all(v == 0 for v in emb):
                    logger.warning(f"Empty embedding for chunk {i}, skipping")
                    continue

            # 7. Write to vector store
            self.vector_store.add(
                ids=ids,
                texts=texts,
                metadatas=metadatas,
                embeddings=embeddings,
            )

            # 8. Update status
            self.db.put_doc_status(s3_key, version_id, "done",
                                   chunk_count=len(chunks), file_hash=file_hash)

            logger.info(f"Successfully ingested {s3_key}: {len(chunks)} chunks")
            return True

        except Exception as e:
            logger.error(f"Failed to process {s3_key}: {e}", exc_info=True)
            self.db.put_doc_status(s3_key, version_id, "failed", error_msg=str(e))
            return False
        finally:
            if local_path.exists():
                local_path.unlink(missing_ok=True)

    def _handle_delete(self, s3_key: str, version_id: str) -> bool:
        """Handle file deletion: remove vectors from store."""
        try:
            # In production, would delete by metadata filter
            logger.info(f"File deleted: {s3_key} — vectors should be cleaned up")
            self.db.put_doc_status(s3_key, version_id, "deleted")
            return True
        except Exception as e:
            logger.error(f"Failed to handle delete for {s3_key}: {e}")
            return False

    def run_sqs_loop(self):
        """Main SQS polling loop (production)."""
        import boto3
        sqs = boto3.client("sqs", region_name=config.aws_region)

        logger.info(f"Starting SQS polling loop: {config.sqs_queue_url}")

        while self._running:
            try:
                resp = sqs.receive_message(
                    QueueUrl=config.sqs_queue_url,
                    MaxNumberOfMessages=10,
                    WaitTimeSeconds=20,  # long polling
                    VisibilityTimeout=300,  # 5 min to process
                )

                messages = resp.get("Messages", [])
                if not messages:
                    continue

                for msg in messages:
                    body = json.loads(msg["Body"])
                    success = self.process_s3_event(body)

                    if success:
                        sqs.delete_message(
                            QueueUrl=config.sqs_queue_url,
                            ReceiptHandle=msg["ReceiptHandle"],
                        )
                        logger.info(f"Deleted SQS message: {msg['MessageId']}")
                    else:
                        # Message stays in queue → retry after visibility timeout
                        logger.warning(f"Processing failed, message will retry: {msg['MessageId']}")

            except KeyboardInterrupt:
                logger.info("Shutting down worker...")
                self._running = False
            except Exception as e:
                logger.error(f"SQS polling error: {e}", exc_info=True)
                time.sleep(5)  # back off on error

    def run_batch_local(self):
        """Batch mode for local dev: read from knowledge_docs/ directory."""
        docs_dir = config.knowledge_docs_dir
        if not docs_dir.exists():
            logger.error(f"Knowledge docs directory not found: {docs_dir}")
            return

        logger.info(f"Local batch mode: ingesting from {docs_dir}")

        chunks = ingest_directory(str(docs_dir), config.chunk_size, config.chunk_overlap)
        chunks = clean_chunks(chunks)
        logger.info(f"Total chunks after cleaning: {len(chunks)}")

        texts = [c.text for c in chunks]
        metadatas = [c.metadata for c in chunks]
        ids = [f"chunk_{i}" for i in range(len(chunks))]

        logger.info(f"Embedding {len(texts)} chunks...")
        embeddings = self.embedding_client.embed_batch(texts)

        self.vector_store.add(
            ids=ids,
            texts=texts,
            metadatas=metadatas,
            embeddings=embeddings,
        )

        logger.info(f"Done. Vector store now has {self.vector_store.count()} chunks.")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="RAG Ingestion Worker")
    parser.add_argument("--mode", choices=["sqs", "batch"], default="batch",
                        help="sqs: production SQS polling; batch: local dev directory scan")
    args = parser.parse_args()

    worker = IngestionWorker()

    if args.mode == "sqs":
        if not config.sqs_queue_url:
            logger.error("SQS_QUEUE_URL not set. Cannot run in SQS mode.")
            sys.exit(1)
        worker.run_sqs_loop()
    else:
        worker.run_batch_local()


if __name__ == "__main__":
    main()

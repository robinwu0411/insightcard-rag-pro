"""S3 client wrapper — handles file download/upload for the ingestion worker."""
import logging
from pathlib import Path
from shared.config import config

logger = logging.getLogger(__name__)


class S3Client:
    def __init__(self):
        import boto3
        self._s3 = boto3.client("s3", region_name=config.aws_region)

    def download_file(self, s3_key: str, local_path: Path) -> Path:
        """Download a file from S3 to local path."""
        local_path.parent.mkdir(parents=True, exist_ok=True)
        self._s3.download_file(config.s3_bucket, s3_key, str(local_path))
        logger.info(f"Downloaded s3://{config.s3_bucket}/{s3_key} → {local_path}")
        return local_path

    def upload_file(self, local_path: Path, s3_key: str):
        """Upload a file to S3."""
        self._s3.upload_file(str(local_path), config.s3_bucket, s3_key)
        logger.info(f"Uploaded {local_path} → s3://{config.s3_bucket}/{s3_key}")

    def get_file_hash(self, s3_key: str, version_id: str = "") -> str:
        """Get ETag (MD5 hash) of an S3 object for dedup."""
        kwargs = {"Bucket": config.s3_bucket, "Key": s3_key}
        if version_id:
            kwargs["VersionId"] = version_id
        resp = self._s3.head_object(**kwargs)
        return resp.get("ETag", "").strip('"')


def get_s3_client():
    if config.s3_bucket and config.s3_bucket != "insightcard-rag-docs":
        return S3Client()
    return None  # local dev: read from filesystem

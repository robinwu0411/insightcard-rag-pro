FROM python:3.13-slim

WORKDIR /app

# Install system dependencies for file parsing (unstructured/OCR)
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils tesseract-ocr && \
    rm -rf /var/lib/apt/lists/*

# Copy shared + ingestion code
COPY shared/ /app/shared/
COPY backend/rag/chunker.py /app/backend/rag/chunker.py
COPY backend/rag/cleaner.py /app/backend/rag/cleaner.py
COPY backend/rag/__init__.py /app/backend/rag/__init__.py
COPY backend/tools/ /app/backend/tools/
COPY ingestion/ /app/ingestion/

# Install dependencies
RUN pip install --no-cache-dir -r /app/ingestion/requirements.txt

CMD ["python", "-m", "ingestion.worker", "--mode", "sqs"]

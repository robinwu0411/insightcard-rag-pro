FROM python:3.13-slim

WORKDIR /app

# Install system dependencies for unstructured/OCR + sentence-transformers (PyTorch)
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils tesseract-ocr libgomp1 && \
    rm -rf /var/lib/apt/lists/*

# Copy shared + backend code
COPY shared/ /app/shared/
COPY backend/ /app/backend/

# Install dependencies
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]

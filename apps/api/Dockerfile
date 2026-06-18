# syntax=docker/dockerfile:1
# ---- Secure Contract Intelligence API image -------------------------------
# tesseract + a slim OpenCV-friendly base; no heavy ML OCR runtime (pytesseract
# only). The local bge-small embedding model is baked into the image at build
# time (see below) so the container never downloads from HuggingFace at runtime.
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System deps: tesseract (OCR), libmagic (MIME sniff), libGL/glib (OpenCV).
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        libmagic1 \
        libgl1 \
        libglib2.0-0 \
        poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install the local rag_core package first (better layer caching).
COPY packages/rag_core /app/packages/rag_core
RUN pip install /app/packages/rag_core

# API dependencies.
COPY apps/api/requirements.txt /app/apps/api/requirements.txt
RUN pip install -r /app/apps/api/requirements.txt

# Pre-download the embedding model into the image's HuggingFace cache so runtime
# never depends on (or is rate-limited by) the HuggingFace Hub. Keep this id in
# sync with Settings.embedding_model.
ENV HF_HOME=/opt/hf-cache
RUN mkdir -p "$HF_HOME" \
    && python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-en-v1.5')"

# Application code.
COPY apps/api /app/apps/api

WORKDIR /app/apps/api

# Pre-create writable runtime dirs (logs, chroma persistence).
RUN mkdir -p /app/logs /app/data/chroma
ENV LOG_DIR=/app/logs \
    CHROMA_PERSIST_DIR=/app/data/chroma

EXPOSE 8000

# Single worker: the in-process ingestion queue + Chroma persistence assume one
# process. Horizontal scale comes with the Celery/Chroma-server swap.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

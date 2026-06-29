# DocMindAI RAG service — container image.
# DocMindAI is the cloud-deployable component (no camera/hardware dependency).
# Build:  docker build -t docmindai .
# Run:    docker run -p 5001:5001 --env-file .env docmindai

FROM python:3.11-slim

# gcc only needed if a wheel is missing; psycopg2-binary/torch/faiss ship wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# App code
COPY . .

# Pre-download the embedding model at build time so the first request is fast
# and the container can run without internet access at runtime.
RUN python -c "from sentence_transformers import SentenceTransformer; \
    SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

EXPOSE 5001

# 1 worker (the embedding model loads once), threaded for SSE streaming.
CMD ["gunicorn", "-w", "1", "--threads", "8", "--timeout", "120", \
     "-b", "0.0.0.0:5001", "app:app"]

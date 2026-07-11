# MinerU API service image used by docker-compose.
# CPU mode is the default. GPU mode can reuse this image with NVIDIA runtime
# and a GPU backend such as vlm-auto-engine / hybrid-auto-engine.
FROM python:3.11-slim

ARG MINERU_PIP_SPEC="mineru[core]"
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MINERU_HOST=0.0.0.0 \
    MINERU_PORT=8000 \
    MINERU_OUTPUT_DIR=/data/cache \
    HF_HOME=/data/models/huggingface \
    HF_HUB_DISABLE_SYMLINKS_WARNING=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    curl \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    poppler-utils \
    tesseract-ocr \
    tesseract-ocr-chi-sim \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --upgrade pip setuptools wheel \
    && pip install "${MINERU_PIP_SPEC}"

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

WORKDIR /data/cache
EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]

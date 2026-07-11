#!/usr/bin/env sh
set -eu

mkdir -p "${MINERU_OUTPUT_DIR:-/data/cache}" /data/models/huggingface

# Docker Compose may inject HF_ENDPOINT as an empty string.
# huggingface_hub treats an empty HF_ENDPOINT as a real endpoint and builds
# invalid URLs like /api/models/..., so explicitly unset it when blank.
if [ -n "${HF_ENDPOINT:-}" ]; then
  export HF_ENDPOINT
else
  unset HF_ENDPOINT
fi

if [ -n "${MINERU_API_COMMAND:-}" ]; then
  echo "[mineru-entrypoint] starting custom command: ${MINERU_API_COMMAND}"
  exec sh -lc "${MINERU_API_COMMAND}"
fi

if command -v mineru-api >/dev/null 2>&1; then
  echo "[mineru-entrypoint] starting mineru-api on ${MINERU_HOST:-0.0.0.0}:${MINERU_PORT:-8000}"
  exec mineru-api --host "${MINERU_HOST:-0.0.0.0}" --port "${MINERU_PORT:-8000}"
fi

# Compatibility fallback for some MinerU distributions.
echo "[mineru-entrypoint] mineru-api not found; trying python -m mineru.cli.fast_api"
exec python -m mineru.cli.fast_api --host "${MINERU_HOST:-0.0.0.0}" --port "${MINERU_PORT:-8000}"

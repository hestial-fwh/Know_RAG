#!/bin/sh
set -e

export PYTHONPATH="/app:/app/ragflow_vendor:${PYTHONPATH:-}"

RAGFLOW_DEEPDOC_DIR="/app/ragflow_vendor/rag/res/deepdoc"
KNOW_RAG_DEEPDOC_DIR="${DEEPDOC_MODEL_DIR:-/data/models/deepdoc}"
mkdir -p "$(dirname "$RAGFLOW_DEEPDOC_DIR")"
if [ -d "$KNOW_RAG_DEEPDOC_DIR" ]; then
  rm -rf "$RAGFLOW_DEEPDOC_DIR"
  ln -s "$KNOW_RAG_DEEPDOC_DIR" "$RAGFLOW_DEEPDOC_DIR"
  echo "[entrypoint] Linked RAGFlow DeepDoc model dir: $RAGFLOW_DEEPDOC_DIR -> $KNOW_RAG_DEEPDOC_DIR"
fi


if [ "${DEEPDOC_AUTO_DOWNLOAD:-1}" != "0" ] && [ "${DEEPDOC_AUTO_DOWNLOAD:-1}" != "false" ]; then
  echo "[entrypoint] Preparing DeepDoc model bundle in ${DEEPDOC_MODEL_DIR:-/data/models/deepdoc} ..."
  python -m app.scripts.bootstrap_deepdoc_models || echo "[entrypoint] DeepDoc model preparation failed; continuing with builtin fallback parser."
fi

exec "$@"

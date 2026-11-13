# 单独构建依赖层，仅在升级固定依赖时重建；不修改主解释器环境。
FROM know_rag_new-backend:local
COPY backend/requirements-graphrag.lock.txt /tmp/requirements-graphrag.txt
RUN python -m venv /opt/graphrag \
    && /opt/graphrag/bin/python -m pip install --no-cache-dir -r /tmp/requirements-graphrag.txt
ENV KNOW_RAG_GRAPHRAG_PYTHON=/opt/graphrag/bin/python

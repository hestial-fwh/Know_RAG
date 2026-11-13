# 复用本机已经验证的依赖层，仅替换当前项目源码；不安装本机 Python 包。
FROM know_rag-backend:latest
WORKDIR /app
COPY app ./app
COPY ragflow_vendor ./ragflow_vendor
COPY tests ./tests
COPY scripts ./scripts
COPY alembic.ini pyproject.toml entrypoint.sh ./
RUN chmod +x /app/entrypoint.sh

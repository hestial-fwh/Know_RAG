# 应用源码与已授权安装的依赖层分开，更新应用无需重新下载官方依赖。
FROM know_rag_new-graphrag-deps:2.7.0
WORKDIR /app
COPY backend/app ./app
COPY backend/tests ./tests
COPY backend/scripts ./scripts
COPY backend/alembic.ini backend/pyproject.toml ./

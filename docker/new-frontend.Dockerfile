# 保留已有 node_modules，构建上下文通过 .dockerignore 排除宿主机依赖。
FROM know_rag-frontend:latest
WORKDIR /app
COPY . .

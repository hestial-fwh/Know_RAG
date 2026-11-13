# MinerU 引擎依赖沿用本机镜像，仅更新本项目启动适配脚本。
FROM know_rag-mineru-cpu:latest
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

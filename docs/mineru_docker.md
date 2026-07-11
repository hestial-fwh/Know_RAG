# MinerU Docker 化说明

本版本将 MinerU 纳入 `docker-compose.yml`，避免 Windows 本地 MinerU 与 backend 容器之间的路径不一致问题。

## 服务划分

- `mineru-cpu`：默认启动，CPU pipeline 模式，服务地址为 `http://mineru-cpu:8000`。
- `mineru-gpu`：可选 GPU 服务，需使用 `--profile gpu` 启动，服务地址为 `http://mineru-gpu:8000`。

## 启动 CPU 版

```bash
docker compose up -d --build
```

此时 backend 默认使用：

```env
MINERU_RUNTIME_PROFILE=cpu
MINERU_CPU_BASE_URL=http://mineru-cpu:8000
MINERU_CPU_BACKEND=pipeline
MINERU_OUTPUT_DIR=/data/cache
```

## 启动 GPU 版

GPU 版要求宿主机已经安装 NVIDIA 驱动和 NVIDIA Container Toolkit。

```bash
docker compose --profile gpu up -d --build mineru-gpu
```

然后在前端上传页选择“MinerU 运行版本：GPU VLM”。

默认 GPU 后端为：

```env
MINERU_GPU_BACKEND=vlm-auto-engine
```

如需改为 `hybrid-auto-engine`，在 `.env` 中修改：

```env
MINERU_GPU_BACKEND=hybrid-auto-engine
```

## output/cache 行为

MinerU 和 backend 共享同一个 `./cache:/data/cache` 挂载。

- backend 会把上传文件从 MinIO 下载到 `/data/cache/{kb_id}/{doc_id}/source`。
- backend 调用 MinerU 时传入 `/data/cache/{kb_id}/{doc_id}/mineru_output`。
- MinerU 解析图片被 backend 读取并上传到 MinIO。
- 解析结束后 backend 删除 `/data/cache/{kb_id}/{doc_id}`。

正常情况下，项目根目录不再需要 Windows 本地 `output/`。如果 MinerU 内部仍生成默认 `output/`，容器工作目录已经设为 `/data/cache`，并通过 `MINERU_LEGACY_OUTPUT_DIR=/data/cache/output` 做兜底清理。

## 前端选择是否真实生效

上传页会把以下字段提交到后端：

- `parser_engine`
- `mineru_profile`
- `enable_ocr`
- `enable_vlm`
- `enable_media_crop`

后端会把这些字段写入 `documents.metadata_json.parse_options`，解析时按文档级配置决定：

- CPU：调用 `http://mineru-cpu:8000`，强制 `backend=pipeline`。
- GPU：调用 `http://mineru-gpu:8000`，使用 `MINERU_GPU_BACKEND`。

因此这不是纯前端展示项。

# MinIO 存储改造说明

本版本将项目正式文件存储从 Docker 本地目录切换为 MinIO。

## 存储职责

- MinIO：保存原始上传文件、解析产生的图片、表格截图、OCR/VLM 相关媒体文件。
- `/data/cache`：仅作为解析器临时缓存目录，解析器仍然可以拿到本地文件路径。
- Postgres：继续保存文档、chunk、解析状态与 MinIO 对象引用。

## 对象 Key 规则

```text
kb/{knowledge_base_id}/documents/{document_id}/source/{filename}
kb/{knowledge_base_id}/documents/{document_id}/media/{filename}
```

`documents.storage_path` 现在保存：

```text
minio://{bucket}/{object_key}
```

`documents.metadata_json.storage` 中保存：

```json
{
  "backend": "minio",
  "bucket": "know-rag",
  "object_key": "kb/.../source/xxx.pdf",
  "uri": "minio://know-rag/kb/.../source/xxx.pdf"
}
```

## 删除行为

- 删除文档时，会删除 MinIO 前缀：

```text
kb/{knowledge_base_id}/documents/{document_id}/
```

- 删除知识库时，会删除 MinIO 前缀：

```text
kb/{knowledge_base_id}/
```

删除 MinIO 对象失败时，接口会返回错误，不继续删除数据库记录，避免产生对象残留。

## 访问行为

前端仍然访问：

```text
/files/{object_key}
```

后端通过 `/files` 接口从 MinIO 读取对象并以流式响应返回，前端不需要直接访问 MinIO。

## 启动

```bash
docker compose up -d --build
```

MinIO 控制台：

```text
http://127.0.0.1:9001
```

默认账号密码：

```text
minioadmin / minioadmin
```

## 关键配置

```env
STORAGE_BACKEND=minio
LOCAL_CACHE_DIR=/data/cache
MINIO_ENDPOINT=minio:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=know-rag
MINIO_SECURE=false
MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=minioadmin
```

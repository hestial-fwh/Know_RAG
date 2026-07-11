import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from app.core.config import settings
from app.services.storage import iter_object_bytes, stat_object, validate_object_key_for_http

router = APIRouter(prefix="/files", tags=["files"])


@router.get("/{relative_path:path}")
async def get_file(relative_path: str):
    if (settings.storage_backend or "minio").lower() == "minio":
        try:
            object_key = validate_object_key_for_http(relative_path)
        except ValueError:
            raise HTTPException(status_code=403, detail="invalid object key")
        try:
            stat = stat_object(object_key)
        except Exception as exc:
            raise HTTPException(status_code=404, detail=f"file not found: {str(exc)[:120]}")
        media_type = getattr(stat, "content_type", None) or mimetypes.guess_type(object_key)[0] or "application/octet-stream"
        return StreamingResponse(iter_object_bytes(object_key), media_type=media_type)

    root = Path(settings.local_storage_dir).resolve()
    target = (root / relative_path).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=403, detail="invalid file path")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(str(target))

"""Bootstrap RAGFlow DeepDoc-compatible model files for know_rag.

RAGFlow's Docker deployment gives users a "DeepDoc is ready by default" feeling.
For know_rag we reproduce that operational model without bundling huge binary
weights in the source zip: when the backend container starts, this script checks
`DEEPDOC_MODEL_DIR` and, if necessary, downloads the public DeepDoc model bundle
into the persistent `/data/models/deepdoc` volume.

The script is intentionally best-effort.  If the machine cannot access
HuggingFace or the mirror configured by `HF_ENDPOINT`, startup continues and the
PDF parser falls back to its deterministic RAGFlow-style heuristic parser.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

DEFAULT_MODEL_DIR = "/data/models/deepdoc"
DEEPDOC_REPO = os.getenv("DEEPDOC_HF_REPO", "InfiniFlow/deepdoc")
XGB_REPO = os.getenv("DEEPDOC_XGB_HF_REPO", "InfiniFlow/text_concat_xgb_v1.0")

LAYOUT_PATTERNS = (
    "**/*layout*.onnx",
    "**/*Layout*.onnx",
    "**/*dla*.onnx",
    "**/*DLA*.onnx",
)
TSR_PATTERNS = (
    "**/*tsr*.onnx",
    "**/*TSR*.onnx",
    "**/*table*structure*.onnx",
    "**/*table_structure*.onnx",
    "**/*Table*Structure*.onnx",
)
XGB_PATTERNS = (
    "**/*updown*concat*xgb*.model",
    "**/*text*concat*xgb*.model",
    "**/*concat*xgb*.model",
    "**/*xgb*.model",
)
OCR_PATTERNS = (
    "**/det.onnx",
    "**/rec.onnx",
    "**/ocr.res",
)


def _env_truthy(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).strip().lower() not in {"0", "false", "no", "off"}


def _find_first(root: Path, patterns: Iterable[str], *, exclude: Iterable[str] = ()) -> str:
    excludes = tuple(e.lower() for e in exclude)
    for pattern in patterns:
        for item in sorted(root.glob(pattern)):
            if not item.is_file():
                continue
            low = str(item).lower()
            if any(e in low for e in excludes):
                continue
            return str(item)
    return ""


def locate_deepdoc_assets(model_dir: str | Path | None = None) -> dict:
    root = Path(model_dir or os.getenv("DEEPDOC_MODEL_DIR", DEFAULT_MODEL_DIR))
    return {
        "model_dir": str(root),
        "layout_model": os.getenv("DEEPDOC_LAYOUT_MODEL", "") or _find_first(root, LAYOUT_PATTERNS, exclude=("tsr", "table")),
        "tsr_model": os.getenv("DEEPDOC_TSR_MODEL", "") or _find_first(root, TSR_PATTERNS),
        "xgb_model": os.getenv("DEEPDOC_XGB_MODEL", "") or _find_first(root, XGB_PATTERNS),
        "ocr_assets": [str(p) for pattern in OCR_PATTERNS for p in sorted(root.glob(pattern)) if p.is_file()],
    }


def _write_manifest(model_dir: Path, data: dict) -> None:
    try:
        model_dir.mkdir(parents=True, exist_ok=True)
        manifest = model_dir / "know_rag_deepdoc_manifest.json"
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to write DeepDoc manifest: %s", exc)


def _download_repo(repo_id: str, local_dir: Path) -> tuple[bool, str]:
    try:
        from huggingface_hub import snapshot_download  # type: ignore
    except Exception as exc:
        return False, f"huggingface_hub is not installed: {exc}"

    try:
        snapshot_download(
            repo_id=repo_id,
            local_dir=str(local_dir),
            local_dir_use_symlinks=False,
            resume_download=True,
        )
        return True, f"downloaded {repo_id}"
    except TypeError:
        # Older huggingface_hub versions do not support local_dir_use_symlinks.
        try:
            snapshot_download(repo_id=repo_id, local_dir=str(local_dir), resume_download=True)
            return True, f"downloaded {repo_id}"
        except Exception as exc:
            return False, f"failed to download {repo_id}: {exc}"
    except Exception as exc:
        return False, f"failed to download {repo_id}: {exc}"


def ensure_deepdoc_model_files(model_dir: str | Path | None = None, *, force: bool = False) -> dict:
    root = Path(model_dir or os.getenv("DEEPDOC_MODEL_DIR", DEFAULT_MODEL_DIR))
    root.mkdir(parents=True, exist_ok=True)

    status = locate_deepdoc_assets(root)
    status["auto_download"] = _env_truthy("DEEPDOC_AUTO_DOWNLOAD", "1")
    status["download_attempted"] = False
    status["download_messages"] = []

    ready = bool(status["layout_model"] and status["tsr_model"] and status["xgb_model"])
    if ready and not force:
        status["ready"] = True
        status["mode"] = "local-models"
        _write_manifest(root, status)
        return status

    if not status["auto_download"]:
        status["ready"] = ready
        status["mode"] = "local-models" if ready else "builtin-fallback"
        _write_manifest(root, status)
        return status

    # Keep first-run startup bounded.  These environment variables are honored
    # by huggingface_hub and prevent long hangs on offline intranet machines.
    os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", os.getenv("DEEPDOC_DOWNLOAD_TIMEOUT", "60"))
    os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", os.getenv("DEEPDOC_ETAG_TIMEOUT", "10"))

    status["download_attempted"] = True
    for repo in (DEEPDOC_REPO, XGB_REPO):
        ok, msg = _download_repo(repo, root)
        status["download_messages"].append(msg)
        if ok:
            logger.info("%s", msg)
        else:
            logger.warning("%s", msg)

    status.update(locate_deepdoc_assets(root))
    status["auto_download"] = True
    status["download_attempted"] = True
    status["ready"] = bool(status["layout_model"] and status["tsr_model"] and status["xgb_model"])
    status["mode"] = "local-models" if status["ready"] else "builtin-fallback"
    _write_manifest(root, status)
    return status


def main() -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    status = ensure_deepdoc_model_files(force=_env_truthy("DEEPDOC_FORCE_DOWNLOAD", "0"))
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

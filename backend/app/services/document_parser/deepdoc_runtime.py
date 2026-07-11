"""Optional DeepDoc production runtime integration.

This module wires know_rag's PDF parser to real model files when they are
available, while preserving a deterministic heuristic fallback when they are
not.  It supports three production assets compatible with the RAGFlow DeepDoc
idea:

- ONNX LayoutRecognizer: page layout detection (text/title/table/figure/...)
- ONNX TableStructureRecognizer: table row/column/header/spanning-cell detection
- XGBoost vertical concatenation model: decide whether adjacent text boxes
  should be merged across lines.

Model files are intentionally *not bundled* in the source zip, but the Docker
entrypoint and this runtime now auto-bootstrap them into `DEEPDOC_MODEL_DIR`
(default: /data/models/deepdoc) when `DEEPDOC_AUTO_DOWNLOAD=1`.  You may also
preload them manually or point the individual environment variables below to
absolute paths:

- DEEPDOC_LAYOUT_MODEL=/data/models/deepdoc/layout.onnx
- DEEPDOC_TSR_MODEL=/data/models/deepdoc/tsr.onnx
- DEEPDOC_XGB_MODEL=/data/models/deepdoc/updown_concat_xgb.model

The ONNX decoder is intentionally tolerant of common YOLO-style outputs.  If a
custom model exposes a different output contract, deploy it behind
`DEEPDOC_SERVICE_URL`; this runtime will call `/layout` and `/tsr` endpoints
using the same normalized detection schema.
"""

from __future__ import annotations

import base64
import logging
import math
import os
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable

from PIL import Image

try:
    from .deepdoc_local_ocr import LocalDeepDocOCR
except Exception:  # pragma: no cover
    LocalDeepDocOCR = None  # type: ignore

logger = logging.getLogger(__name__)

try:
    from app.scripts.bootstrap_deepdoc_models import ensure_deepdoc_model_files, locate_deepdoc_assets
except Exception:  # pragma: no cover - local dev fallback
    ensure_deepdoc_model_files = None  # type: ignore
    locate_deepdoc_assets = None  # type: ignore

LAYOUT_LABELS = [
    "background",
    "text",
    "title",
    "figure",
    "figure_caption",
    "table",
    "table_caption",
    "header",
    "footer",
    "reference",
    "equation",
]

TSR_LABELS = [
    "table",
    "table_column",
    "table_row",
    "table_column_header",
    "table_projected_row_header",
    "table_spanning_cell",
]


@dataclass
class DeepDocDetection:
    label: str
    score: float
    bbox: tuple[float, float, float, float]


@dataclass
class DeepDocStatus:
    enabled: bool
    service_url: str
    mode: str
    ocr_available: bool
    ocr_engine: str
    model_dir: str
    layout_model_path: str
    layout_loaded: bool
    tsr_model_path: str
    tsr_loaded: bool
    xgb_model_path: str
    xgb_loaded: bool
    errors: list[str]
    auto_download: bool = False
    download_attempted: bool = False
    download_messages: list[str] | None = None
    model_ready: bool = False


def _first_existing(candidates: Iterable[str | Path]) -> str:
    for item in candidates:
        if not item:
            continue
        path = Path(item)
        if path.exists():
            return str(path)
    return ""


def _normalize_label(raw: str) -> str:
    value = str(raw or "").strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "text": "text",
        "title": "title",
        "figure": "figure",
        "fig": "figure",
        "image": "figure",
        "figure_caption": "figure_caption",
        "figurecaption": "figure_caption",
        "table": "table",
        "table_caption": "table_caption",
        "tablecaption": "table_caption",
        "header": "header",
        "footer": "footer",
        "reference": "reference",
        "equation": "equation",
        "formula": "equation",
        "table_column": "table_column",
        "table_row": "table_row",
        "table_column_header": "table_column_header",
        "table_projected_row_header": "table_projected_row_header",
        "table_spanning_cell": "table_spanning_cell",
    }
    return aliases.get(value, value)


def _bbox_area(b: tuple[float, float, float, float]) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _nms(dets: list[DeepDocDetection], threshold: float = 0.45) -> list[DeepDocDetection]:
    if not dets:
        return []
    by_label: dict[str, list[DeepDocDetection]] = {}
    for det in dets:
        by_label.setdefault(det.label, []).append(det)
    kept: list[DeepDocDetection] = []
    for label, items in by_label.items():
        items = sorted(items, key=lambda d: d.score, reverse=True)
        while items:
            current = items.pop(0)
            kept.append(current)
            rest: list[DeepDocDetection] = []
            ca = _bbox_area(current.bbox)
            for other in items:
                oa = _bbox_area(other.bbox)
                x0 = max(current.bbox[0], other.bbox[0])
                y0 = max(current.bbox[1], other.bbox[1])
                x1 = min(current.bbox[2], other.bbox[2])
                y1 = min(current.bbox[3], other.bbox[3])
                inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
                union = ca + oa - inter
                if union <= 0 or inter / union <= threshold:
                    rest.append(other)
            items = rest
    return kept


class DeepDocRuntime:
    def __init__(self) -> None:
        self.model_dir = os.getenv("DEEPDOC_MODEL_DIR", "/data/models/deepdoc")
        self.service_url = os.getenv("DEEPDOC_SERVICE_URL", "").rstrip("/")
        self.score_threshold = float(os.getenv("DEEPDOC_SCORE_THRESHOLD", "0.35"))
        self.merge_threshold = float(os.getenv("DEEPDOC_XGB_MERGE_THRESHOLD", "0.52"))
        self.ocr_engine = os.getenv("DEEPDOC_OCR_ENGINE", "auto").lower()
        self.auto_download = os.getenv("DEEPDOC_AUTO_DOWNLOAD", "1") not in {"0", "false", "False"}
        self.errors: list[str] = []
        model_dir = Path(self.model_dir)
        self.bootstrap_status: dict[str, Any] = {}
        if self.auto_download and ensure_deepdoc_model_files is not None:
            try:
                self.bootstrap_status = ensure_deepdoc_model_files(model_dir)
            except Exception as exc:
                msg = f"DeepDoc model bootstrap failed: {exc}"
                logger.warning(msg)
                self.errors.append(msg)
                self.bootstrap_status = {"ready": False, "download_attempted": False, "download_messages": [msg]}

        located = {}
        if locate_deepdoc_assets is not None:
            try:
                located = locate_deepdoc_assets(model_dir)
            except Exception as exc:
                self.errors.append(f"DeepDoc model asset locate failed: {exc}")

        self.layout_model_path = _first_existing(
            [
                os.getenv("DEEPDOC_LAYOUT_MODEL", ""),
                located.get("layout_model", ""),
                model_dir / "layout.onnx",
                model_dir / "layout.paper.onnx",
                model_dir / "layout.pth.onnx",
                model_dir / "layout" / "model.onnx",
            ]
        )
        self.tsr_model_path = _first_existing(
            [
                os.getenv("DEEPDOC_TSR_MODEL", ""),
                located.get("tsr_model", ""),
                model_dir / "tsr.onnx",
                model_dir / "table_structure.onnx",
                model_dir / "table_structure_recognizer.onnx",
                model_dir / "tsr" / "model.onnx",
            ]
        )
        self.xgb_model_path = _first_existing(
            [
                os.getenv("DEEPDOC_XGB_MODEL", ""),
                located.get("xgb_model", ""),
                model_dir / "updown_concat_xgb.model",
                model_dir / "text_concat_xgb.model",
                model_dir / "xgb" / "updown_concat_xgb.model",
            ]
        )
        self.ocr_det_path = _first_existing([os.getenv("DEEPDOC_OCR_DET_MODEL", ""), model_dir / "det.onnx"])
        self.ocr_rec_path = _first_existing([os.getenv("DEEPDOC_OCR_REC_MODEL", ""), model_dir / "rec.onnx"])
        self.ocr_dict_path = _first_existing([os.getenv("DEEPDOC_OCR_DICT", ""), model_dir / "ocr.res"])
        self._layout_session = None
        self._tsr_session = None
        self._xgb = None
        self._ocr = None
        self._ort = None
        self._np = None
        self._cv2 = None
        self._init_models()

    def _init_models(self) -> None:
        if self.layout_model_path or self.tsr_model_path:
            try:
                import numpy as np  # type: ignore
                import cv2  # type: ignore
                import onnxruntime as ort  # type: ignore

                self._np = np
                self._cv2 = cv2
                self._ort = ort
            except Exception as exc:
                self.errors.append(f"ONNXRuntime/OpenCV/Numpy unavailable: {exc}")
                logger.warning("DeepDoc ONNX dependencies unavailable: %s", exc)

        if self._ort and self.layout_model_path:
            try:
                self._layout_session = self._ort.InferenceSession(self.layout_model_path, providers=["CPUExecutionProvider"])
                logger.info("Loaded DeepDoc layout ONNX: %s", self.layout_model_path)
            except Exception as exc:
                self.errors.append(f"Layout model load failed: {exc}")
                logger.warning("DeepDoc layout model load failed: %s", exc)
        if self._ort and self.tsr_model_path:
            try:
                self._tsr_session = self._ort.InferenceSession(self.tsr_model_path, providers=["CPUExecutionProvider"])
                logger.info("Loaded DeepDoc TSR ONNX: %s", self.tsr_model_path)
            except Exception as exc:
                self.errors.append(f"TSR model load failed: {exc}")
                logger.warning("DeepDoc TSR model load failed: %s", exc)
        if self.xgb_model_path:
            try:
                import xgboost as xgb  # type: ignore

                booster = xgb.Booster()
                booster.set_param({"device": "cpu"})
                booster.load_model(self.xgb_model_path)
                self._xgb = booster
                logger.info("Loaded DeepDoc XGBoost concat model: %s", self.xgb_model_path)
            except Exception as exc:
                self.errors.append(f"XGBoost model load failed: {exc}")
                logger.warning("DeepDoc XGBoost model load failed: %s", exc)
        if LocalDeepDocOCR and self.ocr_det_path and self.ocr_rec_path and self.ocr_dict_path:
            try:
                self._ocr = LocalDeepDocOCR(self.model_dir)
            except Exception as exc:
                self.errors.append(f"DeepDoc OCR load failed: {exc}")
                logger.warning("DeepDoc OCR load failed: %s", exc)

    @property
    def enabled(self) -> bool:
        """DeepDoc is a built-in parser path, not an optional API switch.

        This intentionally returns True even when production model files are not
        loaded yet.  It matches RAGFlow's user-facing behaviour: DeepDoc is the
        default document parser, while model files / services only decide whether
        the parser uses production OCR/Layout/TSR models or deterministic
        fallback logic.
        """
        return True

    @property
    def mode(self) -> str:
        if self.service_url:
            return "external-service"
        if self._layout_session or self._tsr_session or self._xgb:
            return "local-models"
        return "builtin-fallback"

    @property
    def ocr_available(self) -> bool:
        if self.service_url:
            return True
        if self._ocr is not None:
            return True
        # Backward-compatible optional OCR engines.
        try:
            import paddleocr  # type: ignore  # noqa: F401
            return True
        except Exception:
            pass
        try:
            import pytesseract  # type: ignore  # noqa: F401
            return True
        except Exception:
            return False

    def status(self) -> DeepDocStatus:
        return DeepDocStatus(
            enabled=self.enabled,
            service_url=self.service_url,
            mode=self.mode,
            ocr_available=self.ocr_available,
            ocr_engine=("service" if self.service_url else self.ocr_engine),
            model_dir=self.model_dir,
            layout_model_path=self.layout_model_path,
            layout_loaded=bool(self._layout_session),
            tsr_model_path=self.tsr_model_path,
            tsr_loaded=bool(self._tsr_session),
            xgb_model_path=self.xgb_model_path,
            xgb_loaded=bool(self._xgb),
            errors=list(self.errors),
            auto_download=self.auto_download,
            download_attempted=bool(self.bootstrap_status.get("download_attempted")),
            download_messages=list(self.bootstrap_status.get("download_messages") or []),
            model_ready=bool(self._layout_session and self._tsr_session and self._xgb),
        )

    def status_dict(self) -> dict[str, Any]:
        st = self.status()
        return {
            "enabled": st.enabled,
            "service_url": st.service_url,
            "mode": st.mode,
            "ocr_available": st.ocr_available,
            "ocr_engine": st.ocr_engine,
            "model_dir": st.model_dir,
            "layout_model_path": st.layout_model_path,
            "layout_loaded": st.layout_loaded,
            "tsr_model_path": st.tsr_model_path,
            "tsr_loaded": st.tsr_loaded,
            "xgb_model_path": st.xgb_model_path,
            "xgb_loaded": st.xgb_loaded,
            "errors": st.errors,
            "auto_download": st.auto_download,
            "download_attempted": st.download_attempted,
            "download_messages": st.download_messages or [],
            "model_ready": st.model_ready,
            "ocr_det_path": self.ocr_det_path,
            "ocr_rec_path": self.ocr_rec_path,
            "ocr_dict_path": self.ocr_dict_path,
            "ocr_loaded": bool(self._ocr),
        }

    def _image_to_data_url(self, image: Image.Image) -> str:
        buf = BytesIO()
        image.convert("RGB").save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("utf-8")


    def recognize_text(self, image_b64: str, prompt: str = "") -> str:
        """Built-in OCR path used when OCR provider is `deepdoc-builtin`.

        Priority:
        1. External DeepDoc service `/ocr` if DEEPDOC_SERVICE_URL is set.
        2. Optional local PaddleOCR if installed in the runtime image.
        3. Optional local pytesseract if installed on the host/container.

        The method is deliberately best-effort: if no local OCR engine is
        installed, it returns an empty string and the caller records a skipped
        status instead of forcing users to configure an OCR API.
        """
        raw = image_b64.split(",", 1)[-1] if image_b64.startswith("data:") else image_b64
        if not raw:
            return ""
        if self.service_url:
            try:
                import requests
                payload = {"image_base64": raw, "image": "data:image/png;base64," + raw, "prompt": prompt}
                resp = requests.post(f"{self.service_url}/ocr", json=payload, timeout=180)
                if resp.status_code == 404:
                    resp = requests.post(f"{self.service_url.rstrip('/')}/api/ocr", json=payload, timeout=180)
                resp.raise_for_status()
                data = resp.json()
                text = data.get("text") or data.get("ocr_text") or data.get("result")
                if isinstance(text, dict):
                    text = text.get("text") or text.get("content")
                return str(text or "").strip()
            except Exception as exc:
                logger.warning("DeepDoc service OCR failed: %s", exc)
        try:
            image = Image.open(BytesIO(base64.b64decode(raw))).convert("RGB")
        except Exception as exc:
            logger.warning("DeepDoc builtin OCR decode image failed: %s", exc)
            return ""
        if self._ocr is not None:
            try:
                return self._ocr.text(image)
            except Exception as exc:
                logger.warning("DeepDoc local ONNX OCR failed, trying optional engines: %s", exc)
        try:
            from paddleocr import PaddleOCR  # type: ignore

            ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                image.save(tmp.name)
                tmp_path = tmp.name
            try:
                result = ocr.ocr(tmp_path, cls=True)
            finally:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
            texts: list[str] = []
            for page in result or []:
                for line in page or []:
                    try:
                        texts.append(str(line[1][0]))
                    except Exception:
                        continue
            return "\n".join(t for t in texts if t.strip()).strip()
        except Exception as exc:
            logger.info("PaddleOCR unavailable or failed, trying pytesseract: %s", exc)
        try:
            import pytesseract  # type: ignore

            return str(pytesseract.image_to_string(image, lang=os.getenv("TESSERACT_LANG", "chi_sim+eng")) or "").strip()
        except Exception as exc:
            logger.info("No builtin OCR engine available: %s", exc)
            return ""

    def _service_predict(self, endpoint: str, image: Image.Image, *, page_width: float, page_height: float) -> list[DeepDocDetection]:
        if not self.service_url:
            return []
        import requests

        payload = {"image": self._image_to_data_url(image), "page_width": page_width, "page_height": page_height}
        resp = requests.post(f"{self.service_url}/{endpoint.lstrip('/')}", json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        raw_items = data.get("detections") or data.get("layouts") or data.get("items") or []
        return self._normalize_external_detections(raw_items, page_width=page_width, page_height=page_height)

    def _normalize_external_detections(self, items: list[Any], *, page_width: float, page_height: float) -> list[DeepDocDetection]:
        dets: list[DeepDocDetection] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            label = _normalize_label(item.get("label") or item.get("type") or item.get("class") or "")
            score = float(item.get("score") or item.get("confidence") or 1.0)
            bbox = item.get("bbox") or item.get("box")
            if not bbox or len(bbox) < 4:
                continue
            dets.extend(self._make_detection(label, score, list(bbox[:4]), page_width, page_height))
        return _nms(dets)

    def _session_input_size(self, session: Any) -> tuple[int, int]:
        try:
            shape = session.get_inputs()[0].shape
            h = int(shape[2]) if isinstance(shape[2], int) and shape[2] > 0 else 640
            w = int(shape[3]) if isinstance(shape[3], int) and shape[3] > 0 else 640
            return h, w
        except Exception:
            return 640, 640

    def _preprocess(self, image: Image.Image, session: Any):
        assert self._np is not None and self._cv2 is not None
        np = self._np
        cv2 = self._cv2
        input_h, input_w = self._session_input_size(session)
        img = np.array(image.convert("RGB"))
        orig_h, orig_w = img.shape[:2]
        scale = min(input_w / max(orig_w, 1), input_h / max(orig_h, 1))
        new_w = int(round(orig_w * scale))
        new_h = int(round(orig_h * scale))
        resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        canvas = np.full((input_h, input_w, 3), 114, dtype=np.uint8)
        pad_x = (input_w - new_w) // 2
        pad_y = (input_h - new_h) // 2
        canvas[pad_y : pad_y + new_h, pad_x : pad_x + new_w] = resized
        tensor = canvas.astype("float32") / 255.0
        tensor = tensor.transpose(2, 0, 1)[None, :, :, :]
        return tensor, scale, pad_x, pad_y, orig_w, orig_h

    def _run_onnx_detector(self, image: Image.Image, session: Any, labels: list[str], *, page_width: float, page_height: float) -> list[DeepDocDetection]:
        if not session or self._np is None:
            return []
        tensor, scale, pad_x, pad_y, orig_w, orig_h = self._preprocess(image, session)
        input_name = session.get_inputs()[0].name
        outputs = session.run(None, {input_name: tensor})
        rows = self._flatten_outputs(outputs)
        detections: list[DeepDocDetection] = []
        for row in rows:
            detections.extend(
                self._row_to_detections(
                    row,
                    labels,
                    scale=scale,
                    pad_x=pad_x,
                    pad_y=pad_y,
                    orig_w=orig_w,
                    orig_h=orig_h,
                    page_width=page_width,
                    page_height=page_height,
                )
            )
        return _nms(detections)

    def _flatten_outputs(self, outputs: list[Any]) -> list[list[float]]:
        assert self._np is not None
        np = self._np
        rows: list[list[float]] = []
        for out in outputs:
            arr = np.asarray(out)
            if arr.size == 0:
                continue
            arr = np.squeeze(arr)
            if arr.ndim == 1:
                arr = arr.reshape(1, -1)
            # Some YOLO exports are [features, boxes].  Transpose to [boxes, features].
            if arr.ndim == 2 and arr.shape[0] < arr.shape[1] and arr.shape[0] <= 128 and arr.shape[1] > 128:
                arr = arr.T
            if arr.ndim > 2:
                arr = arr.reshape(-1, arr.shape[-1])
            for row in arr:
                vals = [float(x) for x in row.tolist()]
                if len(vals) >= 6:
                    rows.append(vals)
        return rows

    def _row_to_detections(
        self,
        row: list[float],
        labels: list[str],
        *,
        scale: float,
        pad_x: float,
        pad_y: float,
        orig_w: float,
        orig_h: float,
        page_width: float,
        page_height: float,
    ) -> list[DeepDocDetection]:
        # Supported rows:
        # 1) x0,y0,x1,y1,score,class_id
        # 2) cx,cy,w,h,obj_score,cls_scores...
        # 3) x0,y0,x1,y1,cls_scores...
        candidates: list[tuple[list[float], float, int]] = []
        if len(row) == 6:
            x0, y0, x1, y1, score, cls = row
            candidates.append(([x0, y0, x1, y1], score, int(round(cls))))
        elif len(row) > 6:
            first4 = row[:4]
            rest = row[4:]
            # Common YOLO format with objectness at index 4.
            if len(rest) == len(labels) + 1:
                obj = max(0.0, min(1.0, rest[0]))
                cls_scores = rest[1:]
                cls_idx = int(max(range(len(cls_scores)), key=lambda i: cls_scores[i]))
                candidates.append((first4, obj * float(cls_scores[cls_idx]), cls_idx))
            else:
                cls_scores = rest if len(rest) == len(labels) else rest[-len(labels) :]
                if cls_scores:
                    cls_idx = int(max(range(len(cls_scores)), key=lambda i: cls_scores[i]))
                    candidates.append((first4, float(cls_scores[cls_idx]), cls_idx))
        dets: list[DeepDocDetection] = []
        for raw_box, score, cls_idx in candidates:
            if score < self.score_threshold or cls_idx < 0 or cls_idx >= len(labels):
                continue
            dets.extend(self._make_detection(labels[cls_idx], score, raw_box, page_width, page_height, scale, pad_x, pad_y, orig_w, orig_h))
        return dets

    def _make_detection(
        self,
        label: str,
        score: float,
        raw_box: list[float],
        page_width: float,
        page_height: float,
        scale: float | None = None,
        pad_x: float = 0,
        pad_y: float = 0,
        orig_w: float | None = None,
        orig_h: float | None = None,
    ) -> list[DeepDocDetection]:
        if len(raw_box) < 4:
            return []
        x0, y0, x1, y1 = [float(v) for v in raw_box[:4]]
        # If normalized, scale to page coordinates directly.
        if max(abs(x0), abs(y0), abs(x1), abs(y1)) <= 1.5:
            box = (x0 * page_width, y0 * page_height, x1 * page_width, y1 * page_height)
        else:
            # If row appears as cx,cy,w,h, convert only when xyxy is invalid
            # (x1 <= x0 or y1 <= y0).  This avoids corrupting standard
            # x0,y0,x1,y1 outputs whose right/bottom happen to be smaller than
            # 90% of the image size.
            if x1 > 0 and y1 > 0 and (x1 <= x0 or y1 <= y0):
                cx, cy, w, h = x0, y0, x1, y1
                if w > 0 and h > 0:
                    x0, y0, x1, y1 = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
            if scale and orig_w and orig_h:
                x0 = (x0 - pad_x) / scale
                x1 = (x1 - pad_x) / scale
                y0 = (y0 - pad_y) / scale
                y1 = (y1 - pad_y) / scale
                x0 = x0 / max(orig_w, 1) * page_width
                x1 = x1 / max(orig_w, 1) * page_width
                y0 = y0 / max(orig_h, 1) * page_height
                y1 = y1 / max(orig_h, 1) * page_height
            box = (x0, y0, x1, y1)
        bx0, by0, bx1, by1 = box
        bx0 = max(0.0, min(page_width, bx0))
        bx1 = max(0.0, min(page_width, bx1))
        by0 = max(0.0, min(page_height, by0))
        by1 = max(0.0, min(page_height, by1))
        if bx1 < bx0:
            bx0, bx1 = bx1, bx0
        if by1 < by0:
            by0, by1 = by1, by0
        if bx1 - bx0 < 4 or by1 - by0 < 4:
            return []
        return [DeepDocDetection(label=_normalize_label(label), score=float(score), bbox=(bx0, by0, bx1, by1))]

    def predict_layout(self, image: Image.Image, *, page_width: float, page_height: float) -> list[DeepDocDetection]:
        if self.service_url:
            try:
                return self._service_predict("layout", image, page_width=page_width, page_height=page_height)
            except Exception as exc:
                logger.warning("DeepDoc service layout failed: %s", exc)
                self.errors.append(f"DeepDoc service layout failed: {exc}")
        if not self._layout_session:
            return []
        return self._run_onnx_detector(image, self._layout_session, LAYOUT_LABELS, page_width=page_width, page_height=page_height)

    def predict_tsr(self, image: Image.Image, *, page_width: float, page_height: float) -> list[DeepDocDetection]:
        if self.service_url:
            try:
                return self._service_predict("tsr", image, page_width=page_width, page_height=page_height)
            except Exception as exc:
                logger.warning("DeepDoc service TSR failed: %s", exc)
                self.errors.append(f"DeepDoc service TSR failed: {exc}")
        if not self._tsr_session:
            return []
        return self._run_onnx_detector(image, self._tsr_session, TSR_LABELS, page_width=page_width, page_height=page_height)

    def _xgb_expected_feature_count(self) -> int | None:
        if not self._xgb:
            return None
        try:
            n = int(self._xgb.num_features())
            return n if n > 0 else None
        except Exception:
            return None

    def concat_probability(self, features: list[float]) -> float | None:
        if not self._xgb:
            return None
        try:
            import xgboost as xgb  # type: ignore

            fixed = [float(v) for v in features]
            expected = self._xgb_expected_feature_count()
            if expected:
                if len(fixed) > expected:
                    fixed = fixed[:expected]
                elif len(fixed) < expected:
                    fixed.extend([0.0] * (expected - len(fixed)))
            dmatrix = xgb.DMatrix([fixed])
            pred = self._xgb.predict(dmatrix)
            if pred is None or len(pred) == 0:
                return None
            value = pred[0]
            # Binary logistic can return scalar; softprob can return [p0,p1].
            try:
                if hasattr(value, "__len__") and len(value) > 1:
                    return float(value[1])
            except Exception:
                pass
            return float(value)
        except Exception as exc:
            msg = str(exc)[:240]
            logger.warning("DeepDoc XGBoost prediction failed: %s", msg)
            # Do not flood the UI with the full native XGBoost stack trace.
            compact = f"XGBoost prediction failed: {msg}"
            if compact not in self.errors:
                self.errors.append(compact)
            return None


@lru_cache
def get_deepdoc_runtime() -> DeepDocRuntime:
    return DeepDocRuntime()


def reload_deepdoc_runtime() -> DeepDocRuntime:
    get_deepdoc_runtime.cache_clear()
    return get_deepdoc_runtime()

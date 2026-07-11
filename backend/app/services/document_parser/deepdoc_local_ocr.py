"""Local DeepDoc OCR runtime based on det.onnx + rec.onnx + ocr.res.

This is a lightweight, self-contained PaddleOCR-style inference path for the
DeepDoc OCR assets that RAGFlow ships in InfiniFlow/deepdoc.  It intentionally
keeps dependencies limited to numpy, opencv-python-headless and onnxruntime,
which are already part of the backend image.
"""
from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2  # type: ignore
import numpy as np  # type: ignore
import onnxruntime as ort  # type: ignore
from PIL import Image

logger = logging.getLogger(__name__)


@dataclass
class OCRLine:
    text: str
    score: float
    box: list[list[float]]


def _make_session(path: str):
    options = ort.SessionOptions()
    options.enable_cpu_mem_arena = False
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.intra_op_num_threads = int(os.getenv("OCR_INTRA_OP_NUM_THREADS", "2"))
    options.inter_op_num_threads = int(os.getenv("OCR_INTER_OP_NUM_THREADS", "2"))
    return ort.InferenceSession(path, sess_options=options, providers=["CPUExecutionProvider"])


def _resize_det_image(img: np.ndarray, limit_side_len: int = 960):
    h, w = img.shape[:2]
    ratio = 1.0
    max_side = max(h, w)
    if max_side > limit_side_len:
        ratio = float(limit_side_len) / max_side
    resize_h = max(32, int(round(h * ratio / 32) * 32))
    resize_w = max(32, int(round(w * ratio / 32) * 32))
    resized = cv2.resize(img, (resize_w, resize_h), interpolation=cv2.INTER_LINEAR)
    ratio_h = resize_h / float(h)
    ratio_w = resize_w / float(w)
    return resized, ratio_h, ratio_w


def _normalize_det_image(img: np.ndarray):
    img = img.astype("float32") / 255.0
    mean = np.array([0.485, 0.456, 0.406], dtype="float32").reshape(1, 1, 3)
    std = np.array([0.229, 0.224, 0.225], dtype="float32").reshape(1, 1, 3)
    img = (img - mean) / std
    img = img.transpose(2, 0, 1)[None, :, :, :]
    return img.astype("float32")


def _order_points_clockwise(pts: np.ndarray) -> np.ndarray:
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    tmp = np.delete(pts, (np.argmin(s), np.argmax(s)), axis=0)
    diff = np.diff(np.array(tmp), axis=1)
    rect[1] = tmp[np.argmin(diff)]
    rect[3] = tmp[np.argmax(diff)]
    return rect


def _clip_box(points: np.ndarray, height: int, width: int) -> np.ndarray:
    points[:, 0] = np.clip(points[:, 0], 0, width - 1)
    points[:, 1] = np.clip(points[:, 1], 0, height - 1)
    return points


def _unclip_box(box: np.ndarray, ratio: float = 1.5) -> np.ndarray:
    # Lightweight expansion alternative to pyclipper.  It expands the rotated
    # rectangle around its center.  This is less exact than DBPostProcess but
    # avoids adding shapely/pyclipper to the backend image.
    center = box.mean(axis=0)
    return (box - center) * ratio + center


def _box_score_fast(bitmap: np.ndarray, box: np.ndarray) -> float:
    h, w = bitmap.shape[:2]
    xmin = max(0, min(w - 1, int(np.floor(box[:, 0].min()))))
    xmax = max(0, min(w - 1, int(np.ceil(box[:, 0].max()))))
    ymin = max(0, min(h - 1, int(np.floor(box[:, 1].min()))))
    ymax = max(0, min(h - 1, int(np.ceil(box[:, 1].max()))))
    if xmax <= xmin or ymax <= ymin:
        return 0.0
    mask = np.zeros((ymax - ymin + 1, xmax - xmin + 1), dtype=np.uint8)
    shifted = box.copy()
    shifted[:, 0] -= xmin
    shifted[:, 1] -= ymin
    cv2.fillPoly(mask, shifted.reshape(1, -1, 2).astype("int32"), 1)
    return float(cv2.mean(bitmap[ymin : ymax + 1, xmin : xmax + 1], mask)[0])


def _extract_boxes_from_pred(pred: np.ndarray, dest_width: int, dest_height: int, box_thresh: float = 0.5):
    if pred.ndim == 4:
        pred = pred[0, 0]
    elif pred.ndim == 3:
        pred = pred[0]
    pred = pred.astype("float32")
    bitmap = (pred > 0.3).astype("uint8")
    contours, _ = cv2.findContours((bitmap * 255).astype("uint8"), cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    boxes: list[np.ndarray] = []
    height, width = bitmap.shape
    for contour in contours[:1000]:
        if contour.shape[0] < 3:
            continue
        rect = cv2.minAreaRect(contour)
        points = cv2.boxPoints(rect)
        short_side = min(rect[1])
        if short_side < 3:
            continue
        score = _box_score_fast(pred, points)
        if score < box_thresh:
            continue
        points = _unclip_box(points, 1.5)
        rect = cv2.minAreaRect(points.astype("float32"))
        points = cv2.boxPoints(rect)
        short_side = min(rect[1])
        if short_side < 5:
            continue
        points[:, 0] = np.clip(np.round(points[:, 0] / width * dest_width), 0, dest_width - 1)
        points[:, 1] = np.clip(np.round(points[:, 1] / height * dest_height), 0, dest_height - 1)
        points = _order_points_clockwise(points.astype("float32"))
        points = _clip_box(points, dest_height, dest_width)
        rect_width = int(np.linalg.norm(points[0] - points[1]))
        rect_height = int(np.linalg.norm(points[0] - points[3]))
        if rect_width <= 3 or rect_height <= 3:
            continue
        boxes.append(points)
    return boxes


def _sort_boxes(boxes: list[np.ndarray]) -> list[np.ndarray]:
    items = sorted(boxes, key=lambda x: (x[0][1], x[0][0]))
    for i in range(len(items) - 1):
        for j in range(i, -1, -1):
            if abs(items[j + 1][0][1] - items[j][0][1]) < 10 and items[j + 1][0][0] < items[j][0][0]:
                items[j], items[j + 1] = items[j + 1], items[j]
            else:
                break
    return items


def _crop_rotate(img: np.ndarray, points: np.ndarray) -> np.ndarray:
    assert len(points) == 4
    img_crop_width = int(max(np.linalg.norm(points[0] - points[1]), np.linalg.norm(points[2] - points[3])))
    img_crop_height = int(max(np.linalg.norm(points[0] - points[3]), np.linalg.norm(points[1] - points[2])))
    img_crop_width = max(1, img_crop_width)
    img_crop_height = max(1, img_crop_height)
    pts_std = np.float32([[0, 0], [img_crop_width, 0], [img_crop_width, img_crop_height], [0, img_crop_height]])
    m = cv2.getPerspectiveTransform(points.astype("float32"), pts_std)
    dst = cv2.warpPerspective(img, m, (img_crop_width, img_crop_height), borderMode=cv2.BORDER_REPLICATE, flags=cv2.INTER_CUBIC)
    if dst.shape[0] * 1.0 / max(dst.shape[1], 1) >= 1.5:
        dst = np.rot90(dst, k=3)
    return dst


class LocalDeepDocOCR:
    def __init__(self, model_dir: str):
        self.model_dir = Path(model_dir)
        self.det_path = self.model_dir / "det.onnx"
        self.rec_path = self.model_dir / "rec.onnx"
        self.dict_path = self.model_dir / "ocr.res"
        missing = [str(p) for p in [self.det_path, self.rec_path, self.dict_path] if not p.exists()]
        if missing:
            raise FileNotFoundError("Missing DeepDoc OCR assets: " + ", ".join(missing))
        self.det_session = _make_session(str(self.det_path))
        self.rec_session = _make_session(str(self.rec_path))
        self.det_input = self.det_session.get_inputs()[0].name
        self.rec_input = self.rec_session.get_inputs()[0]
        self.rec_input_name = self.rec_input.name
        self.drop_score = float(os.getenv("DEEPDOC_OCR_DROP_SCORE", "0.5"))
        self.characters = ["blank"] + [line.rstrip("\n\r") for line in self.dict_path.read_text(encoding="utf-8").splitlines()] + [" "]
        logger.info("Loaded DeepDoc OCR ONNX: det=%s rec=%s dict=%s", self.det_path, self.rec_path, self.dict_path)

    @property
    def available(self) -> bool:
        return True

    def _resize_norm_rec_img(self, img: np.ndarray, max_wh_ratio: float):
        img_c, img_h, img_w = 3, 48, 320
        try:
            shape = self.rec_input.shape
            if len(shape) >= 4:
                if isinstance(shape[2], int) and shape[2] > 0:
                    img_h = int(shape[2])
                if isinstance(shape[3], int) and shape[3] > 0:
                    img_w = int(shape[3])
        except Exception:
            pass
        img_w = max(img_w, int(img_h * max_wh_ratio))
        h, w = img.shape[:2]
        ratio = w / float(max(h, 1))
        resized_w = img_w if math.ceil(img_h * ratio) > img_w else int(math.ceil(img_h * ratio))
        resized_image = cv2.resize(img, (max(1, resized_w), img_h))
        resized_image = resized_image.astype("float32").transpose((2, 0, 1)) / 255.0
        resized_image -= 0.5
        resized_image /= 0.5
        padding = np.zeros((img_c, img_h, img_w), dtype=np.float32)
        padding[:, :, 0:resized_w] = resized_image
        return padding

    def _decode(self, preds: np.ndarray) -> list[tuple[str, float]]:
        if preds.ndim != 3:
            preds = np.asarray(preds).reshape((preds.shape[0], -1, preds.shape[-1]))
        preds_idx = preds.argmax(axis=2)
        preds_prob = preds.max(axis=2)
        results: list[tuple[str, float]] = []
        for idxs, probs in zip(preds_idx, preds_prob):
            chars: list[str] = []
            confs: list[float] = []
            last = None
            for idx, prob in zip(idxs.tolist(), probs.tolist()):
                if idx == 0 or idx == last:
                    last = idx
                    continue
                last = idx
                if 0 <= idx < len(self.characters):
                    chars.append(self.characters[idx])
                    confs.append(float(prob))
            text = "".join(chars).strip()
            score = float(sum(confs) / len(confs)) if confs else 0.0
            results.append((text, score))
        return results

    def recognize_crops(self, crops: list[np.ndarray]) -> list[tuple[str, float]]:
        if not crops:
            return []
        max_wh_ratio = max([crop.shape[1] / float(max(crop.shape[0], 1)) for crop in crops] + [1.0])
        tensors = [self._resize_norm_rec_img(crop, max_wh_ratio) for crop in crops]
        batch = np.stack(tensors).astype("float32")
        preds = self.rec_session.run(None, {self.rec_input_name: batch})[0]
        return self._decode(np.asarray(preds))

    def __call__(self, image: Image.Image | np.ndarray) -> list[OCRLine]:
        if isinstance(image, Image.Image):
            arr = np.array(image.convert("RGB"))
        else:
            arr = image
            if arr.ndim == 2:
                arr = cv2.cvtColor(arr, cv2.COLOR_GRAY2RGB)
            elif arr.shape[2] == 4:
                arr = cv2.cvtColor(arr, cv2.COLOR_RGBA2RGB)
        if arr.shape[0] < 8 or arr.shape[1] < 8:
            return []
        resized, ratio_h, ratio_w = _resize_det_image(arr)
        det_input = _normalize_det_image(resized)
        pred = self.det_session.run(None, {self.det_input: det_input})[0]
        boxes = _extract_boxes_from_pred(np.asarray(pred), resized.shape[1], resized.shape[0])
        # Map boxes from resized image back to original image.
        mapped = []
        for box in boxes:
            b = box.copy()
            b[:, 0] = b[:, 0] / max(ratio_w, 1e-6)
            b[:, 1] = b[:, 1] / max(ratio_h, 1e-6)
            b = _clip_box(b, arr.shape[0], arr.shape[1])
            mapped.append(b)
        mapped = _sort_boxes(mapped)
        crops = [_crop_rotate(arr, box) for box in mapped]
        recs = self.recognize_crops(crops)
        lines: list[OCRLine] = []
        for box, (text, score) in zip(mapped, recs):
            if text and score >= self.drop_score:
                lines.append(OCRLine(text=text, score=score, box=box.tolist()))
        return lines

    def text(self, image: Image.Image | np.ndarray) -> str:
        return "\n".join(line.text for line in self(image) if line.text).strip()

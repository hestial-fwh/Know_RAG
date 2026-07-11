"""Remote DLA client compatibility stub.

know_rag uses local ONNX DeepDoc models by default.  If DEEPDOC_URL is set, this
stub raises a clear error instead of failing with ModuleNotFoundError.
"""
from __future__ import annotations


class DLAClient:
    def __init__(self, url: str):
        self.url = url
        raise RuntimeError(
            "Remote DeepDoc DLA service is not bundled in know_rag. "
            "Unset DEEPDOC_URL/TENSORRT_DLA_SVR or use local ONNX models."
        )

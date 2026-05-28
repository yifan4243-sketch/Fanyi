"""
OCR 服务：全屏截图 + EasyOCR 识别 + 位置信息
"""

import re
import time
from threading import Lock
from typing import Any

import numpy as np
from PIL import Image


class OcrBlock:
    """OCR 识别结果块"""
    __slots__ = ("text", "bbox", "confidence")

    def __init__(self, text: str, bbox: list, confidence: float) -> None:
        self.text = text
        self.bbox = bbox  # [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
        self.confidence = confidence

    @property
    def center(self) -> tuple[int, int]:
        """包围盒中心坐标（屏幕绝对坐标）"""
        xs = [p[0] for p in self.bbox]
        ys = [p[1] for p in self.bbox]
        return (int(sum(xs) / len(xs)), int(sum(ys) / len(ys)))

    @property
    def top_left(self) -> tuple[int, int]:
        return (int(self.bbox[0][0]), int(self.bbox[0][1]))


class OcrService:
    """OCR 引擎封装，后台线程安全"""

    def __init__(self, config: Any) -> None:
        self._config = config
        self._reader: Any = None
        self._ready = False
        self._lock = Lock()
        self._init_error: str | None = None

    def initialize(self) -> bool:
        try:
            import easyocr
            self._reader = easyocr.Reader(["en"], gpu=False, verbose=False)
            self._ready = True
            return True
        except Exception as e:
            self._init_error = str(e)
            self._ready = False
            return False

    @property
    def is_ready(self) -> bool:
        return self._ready

    @property
    def init_error(self) -> str | None:
        return self._init_error

    def recognize_blocks(self, image: Image.Image) -> list[OcrBlock]:
        """OCR 识别，返回带位置信息的文本块列表"""
        if not self._ready or self._reader is None:
            raise RuntimeError("OCR 引擎未初始化")

        with self._lock:
            processed = self._preprocess(image)
            img_array = np.array(processed)
            results = self._reader.readtext(img_array, detail=1)

            blocks: list[OcrBlock] = []
            for item in results:
                if item is None:
                    continue
                bbox, text, conf = item
                text = text.strip()
                if not text or conf < 0.4:
                    continue
                # 还原坐标（因为预处理可能缩放了图像）
                factor = float(self._config.get("ocr_scale_factor", 2.0))
                if factor != 1.0:
                    bbox = [[p[0] / factor, p[1] / factor] for p in bbox]
                blocks.append(OcrBlock(text, bbox, conf))

            # 按 Y 坐标排序（从上到下阅读顺序）
            blocks.sort(key=lambda b: b.top_left[1])
            return blocks

    def recognize_plain(self, image: Image.Image) -> str:
        """OCR 识别，只返回纯文本（用于监听去重比较）"""
        blocks = self.recognize_blocks(image)
        return "\n".join(b.text for b in blocks)

    def _preprocess(self, image: Image.Image) -> Image.Image:
        factor = float(self._config.get("ocr_scale_factor", 2.0))
        if factor != 1.0:
            w, h = image.size
            image = image.resize(
                (int(w * factor), int(h * factor)), Image.LANCZOS)
        return image.convert("L").convert("RGB")

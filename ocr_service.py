"""
OCR 服务：全屏截图 + EasyOCR 识别 + 位置信息 + 外语过滤
"""

import re
import time
from threading import Lock
from typing import Any

import numpy as np
from PIL import Image


# ---- 过滤关键词 ----

UI_STOP_WORDS = {
    "文件", "编辑", "设置", "清空", "开始", "停止", "微信", "搜索",
    "发送", "复制", "翻译", "当前状态", "框选区域", "暂停", "恢复",
    "帮助", "关于", "退出", "关闭", "保存", "取消", "确定", "返回",
    "登录", "注册", "首页", "消息", "通讯录", "发现", "我", "朋友圈",
    "视频号", "小程序", "扫一扫", "看一看", "搜一搜", "直播",
    "File", "Edit", "View", "Help", "Settings", "Options", "Tools",
    "Window", "Close", "Save", "Cancel", "OK", "Back", "Next",
    "Send", "Copy", "Paste", "Cut", "Delete", "Undo", "Redo",
}

FILIPINO_SIGNAL_WORDS = {
    "ako", "ikaw", "kita", "mahal", "kumusta", "magkano", "po", "opo",
    "hindi", "salamat", "kailangan", "gusto", "ilan", "presyo", "order",
    "padala", "bayad", "produkto", "kulay", "laki", "maliit", "malaki",
    "piraso", "ito", "yan", "mo", "na", "pa", "ba", "ng", "sa", "ang",
    "si", "ni", "may", "wala", "meron", "sige", "oo", "dito", "doon",
}

ENGLISH_SIGNAL_WORDS = {
    "price", "order", "quantity", "shipping", "payment", "product",
    "color", "size", "sample", "delivery", "quotation", "invoice",
    "address", "available", "please", "thank", "hello", "dear",
    "factory", "supplier", "manufacturer", "quality", "material",
    "package", "container", "ship", "freight", "cost", "total",
    "discount", "wholesale", "retail", "stock", "lead", "time",
}


class OcrBlock:
    """OCR 识别结果块"""
    __slots__ = ("text", "bbox", "confidence", "text_hash")

    def __init__(self, text: str, bbox: list, confidence: float) -> None:
        self.text = text
        self.bbox = bbox
        self.confidence = confidence
        self.text_hash = ""

    @property
    def top_left(self) -> tuple[int, int]:
        return (int(self.bbox[0][0]), int(self.bbox[0][1]))


def normalize_text(text: str) -> str:
    """规范化文本用于去重比较"""
    t = text.strip().lower()
    t = re.sub(r'\s+', ' ', t)
    t = re.sub(r'[^\w\s]', '', t)
    t = t.strip()
    return t


def contains_chinese(text: str) -> bool:
    """检查是否包含中文字符"""
    return bool(re.search(r'[一-鿿]', text))


def chinese_ratio(text: str) -> float:
    """中文字符占比"""
    if not text:
        return 0.0
    cleaned = re.sub(r'\s', '', text)
    if not cleaned:
        return 0.0
    chinese_chars = len(re.findall(r'[一-鿿]', cleaned))
    return chinese_chars / len(cleaned)


def is_mostly_number(text: str) -> bool:
    cleaned = re.sub(r'[\s,.\-+%$¥€£]', '', text)
    if not cleaned:
        return False
    digits = sum(1 for c in cleaned if c.isdigit())
    return digits / len(cleaned) > 0.6


def is_mostly_symbol(text: str) -> bool:
    cleaned = re.sub(r'\s', '', text)
    if not cleaned:
        return True
    letters = sum(1 for c in cleaned if c.isalpha())
    return letters / len(cleaned) < 0.3


def has_latin(text: str) -> bool:
    return bool(re.search(r'[a-zA-Z]{2,}', text))


def is_url_or_email(text: str) -> bool:
    return bool(re.search(
        r'https?://|www\.|\.com|\.cn|\.net|\.org|@\w+\.\w+', text, re.IGNORECASE))


def is_date_or_time(text: str) -> bool:
    return bool(re.search(
        r'^\d{1,4}[-/]\d{1,2}[-/]\d{1,4}$|^\d{1,2}:\d{2}', text.strip()))


def is_likely_foreign_text(text: str) -> bool:
    """判断文本是否为需要翻译的外语文本"""
    t = text.strip()
    if len(t) < 3:
        return False
    if is_mostly_number(t):
        return False
    if is_mostly_symbol(t):
        return False
    if is_url_or_email(t):
        return False
    if is_date_or_time(t):
        return False

    # 中文占比过高不翻译
    if chinese_ratio(t) > 0.3:
        return False

    # UI 常见词不翻译
    if t in UI_STOP_WORDS:
        return False

    # 必须有拉丁字母
    if not has_latin(t):
        return False

    # 纯大写短文本通常是 UI 标签
    if len(t) <= 5 and t.isupper():
        return False

    # 包含菲律宾语或英语信号词
    words = set(t.lower().split())
    if words & FILIPINO_SIGNAL_WORDS:
        return True
    if words & ENGLISH_SIGNAL_WORDS:
        return True

    # 拉丁字母占比较高且不长不短
    alpha_count = sum(1 for c in t if c.isalpha())
    total = len(re.sub(r'\s', '', t))
    if total > 0 and alpha_count / total > 0.5 and 3 <= len(t) <= 200:
        return True

    return False


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
        """OCR 识别，返回带位置信息的文本块列表（已过滤）"""
        if not self._ready or self._reader is None:
            raise RuntimeError("OCR 引擎未初始化")

        with self._lock:
            processed = self._preprocess(image)
            img_array = np.array(processed)
            results = self._reader.readtext(img_array, detail=1)

            threshold = float(self._config.get("ocr_confidence_threshold", 0.45))
            factor = float(self._config.get("ocr_scale_factor", 2.0))

            blocks: list[OcrBlock] = []
            for item in results:
                if item is None:
                    continue
                bbox, text, conf = item
                text = text.strip()
                if not text:
                    continue
                if conf < threshold:
                    continue
                if not is_likely_foreign_text(text):
                    continue

                if factor != 1.0:
                    bbox = [[p[0] / factor, p[1] / factor] for p in bbox]

                block = OcrBlock(text, bbox, conf)
                block.text_hash = normalize_text(text)
                blocks.append(block)

            blocks.sort(key=lambda b: b.top_left[1])
            return blocks

    def recognize_plain(self, image: Image.Image) -> str:
        blocks = self.recognize_blocks(image)
        return "\n".join(b.text for b in blocks)

    def _preprocess(self, image: Image.Image) -> Image.Image:
        factor = float(self._config.get("ocr_scale_factor", 2.0))
        if factor != 1.0:
            w, h = image.size
            image = image.resize(
                (int(w * factor), int(h * factor)), Image.LANCZOS)
        return image.convert("L").convert("RGB")

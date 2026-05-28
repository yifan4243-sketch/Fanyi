"""
OCR 服务 + 外语过滤
"""

import re
import time
from threading import Lock
from typing import Any

import numpy as np
from PIL import Image

# ---- UI 干扰词 ----
APP_SELF_WORDS = {
    "外贸屏幕实时翻译助手", "当前状态", "当前模式", "全屏翻译",
    "翻译回复", "复制回复", "设置", "清空", "暂停", "恢复",
    "运行日志", "中文回复", "外语回复", "客户原文", "中文翻译",
    "框选区域", "回复语言", "状态信息", "翻译当前屏幕",
}

CODE_NOISE_PATTERNS = [
    r'https?://', r'127\.0\.0\.1', r'localhost', r'/api/',
    r'\bcurl\b', r'\bPOST\b', r'\bGET\b', r'\bPUT\b', r'\bDELETE\b',
    r'application/json', r'Content-Type',
    r'\.py\b', r'\.js\b', r'\.json\b', r'\.txt\b', r'\.bat\b', r'\.exe\b',
    r'\btraceback\b', r'\berror\b', r'\bwarning\b',
    r'\bGitHub\b', r'\bcommit\b', r'\bbranch\b', r'\bmerge\b',
    r'config_service', r'def ', r'import ', r'from ',
]
_code_noise_re = re.compile('|'.join(CODE_NOISE_PATTERNS), re.IGNORECASE)

NATURAL_SIGNAL = re.compile(
    r'\b(kulambo|lamok|proteksyon|magaan|gamitin|produkto|presyo|'
    r'magkano|order|piraso|kulay|laki|salamat|kailangan|gusto|'
    r'padala|bayad|kumusta|po|opo|hindi|'
    r'price|quantity|product|shipping|payment|color|size|sample|'
    r'delivery|quotation|invoice|available|please|thank|hello|dear|'
    r'factory|supplier|manufacturer|quality|material|package|ship)\b',
    re.IGNORECASE)

FILIPINO_WORDS = {
    "ako", "ikaw", "kita", "mahal", "kumusta", "magkano", "po", "opo",
    "hindi", "salamat", "kailangan", "gusto", "ilan", "presyo", "order",
    "padala", "bayad", "produkto", "kulay", "laki", "maliit", "malaki",
    "piraso", "kulambo", "lamok", "ito", "yan", "mo", "na", "pa", "ba",
    "ng", "sa", "ang", "si", "ni", "may", "wala", "meron", "sige", "oo",
}

ENGLISH_WORDS = {
    "price", "order", "quantity", "shipping", "payment", "product",
    "color", "size", "sample", "delivery", "quotation", "invoice",
    "address", "available", "please", "thank", "hello", "dear",
    "factory", "supplier", "manufacturer", "quality", "material",
    "package", "container", "ship", "freight", "cost", "total",
    "discount", "wholesale", "retail", "stock", "lead",
}


def normalize_text(text: str) -> str:
    t = text.strip().lower()
    t = re.sub(r'\s+', ' ', t)
    t = re.sub(r'[^\w\s]', '', t)
    return t.strip()


def _chinese_ratio(text: str) -> float:
    cleaned = re.sub(r'\s', '', text)
    if not cleaned:
        return 0.0
    cn = len(re.findall(r'[一-鿿]', cleaned))
    return cn / len(cleaned)


def _is_mostly_number(text: str) -> bool:
    c = re.sub(r'[\s,.\-+%$¥€£]', '', text)
    if not c:
        return False
    return sum(1 for x in c if x.isdigit()) / len(c) > 0.6


def _is_mostly_symbol(text: str) -> bool:
    c = re.sub(r'\s', '', text)
    if not c:
        return True
    return sum(1 for x in c if x.isalpha()) / len(c) < 0.3


def _has_latin(text: str) -> bool:
    return bool(re.search(r'[a-zA-Z]{2,}', text))


def _is_url_or_email(text: str) -> bool:
    return bool(re.search(r'https?://|www\.|\.com|\.cn|\.net|\.org|@\w+\.\w+', text, re.I))


def _is_time_or_date(text: str) -> bool:
    t = text.strip()
    if re.search(r'^\d{1,2}:\d{2}', t):
        return True
    if re.search(r'^(昨天|今天|明天|上午|下午|凌晨|早上|中午|晚上)', t):
        return True
    if re.search(r'^\d{4}年\d{1,2}月\d{1,2}日', t):
        return True
    return False


def should_translate_text(text: str) -> bool:
    """严格过滤：只有自然语言聊天内容才翻译"""
    t = text.strip()

    # 太短的不翻译
    if len(t) < 5:
        return False

    # 纯数字/符号不翻译
    if _is_mostly_number(t):
        return False
    if _is_mostly_symbol(t):
        return False

    # 中文为主的不翻译
    if _chinese_ratio(t) > 0.3:
        return False

    # 时间/日期不翻译
    if _is_time_or_date(t):
        return False

    # 代码/URL/命令/接口/路径不翻译
    if _code_noise_re.search(t):
        return False

    # 软件自身 UI 文字
    if t in APP_SELF_WORDS:
        return False

    # 纯大写短文本不翻译
    if len(t) <= 6 and t.isupper():
        return False

    # 必须包含拉丁字母
    if not _has_latin(t):
        return False

    # 优先：包含自然语言信号词
    if NATURAL_SIGNAL.search(t):
        return True

    # 拉丁占比高且长度合理
    alpha = sum(1 for c in t if c.isalpha())
    total = len(re.sub(r'\s', '', t))
    if total > 0 and alpha / total > 0.5 and 10 <= len(t) <= 300:
        return True

    return False


class OcrBlock:
    __slots__ = ("text", "bbox", "confidence", "text_hash")
    def __init__(self, text: str, bbox: list, confidence: float) -> None:
        self.text = text
        self.bbox = bbox
        self.confidence = confidence
        self.text_hash = ""

    @property
    def top_left(self) -> tuple[int, int]:
        return (int(self.bbox[0][0]), int(self.bbox[0][1]))

    @property
    def center_y(self) -> float:
        ys = [p[1] for p in self.bbox]
        return sum(ys) / len(ys)


def merge_blocks(blocks: list[OcrBlock], y_gap: int = 30) -> list[OcrBlock]:
    """把同一气泡的多行文本块合并为一个"""
    if not blocks:
        return []
    merged: list[OcrBlock] = []
    current = blocks[0]
    for b in blocks[1:]:
        if abs(b.center_y - current.center_y) < y_gap:
            current.text += " " + b.text
            x0 = min(current.bbox[0][0], b.bbox[0][0])
            y0 = min(current.bbox[0][1], b.bbox[0][1])
            x1 = max(current.bbox[2][0], b.bbox[2][0])
            y1 = max(current.bbox[2][1], b.bbox[2][1])
            current.bbox = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
            current.confidence = max(current.confidence, b.confidence)
        else:
            merged.append(current)
            current = b
    merged.append(current)
    return merged


class OcrService:
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
        if not self._ready or self._reader is None:
            raise RuntimeError("OCR 未初始化")

        with self._lock:
            processed = self._preprocess(image)
            arr = np.array(processed)
            results = self._reader.readtext(arr, detail=1)

            threshold = float(self._config.get("ocr_confidence_threshold", 0.45))
            factor = float(self._config.get("ocr_scale_factor", 1.0))

            blocks: list[OcrBlock] = []
            for item in results:
                if item is None:
                    continue
                bbox, text, conf = item
                text = text.strip()
                if not text or conf < threshold:
                    continue
                if not should_translate_text(text):
                    continue
                if factor != 1.0:
                    bbox = [[p[0] / factor, p[1] / factor] for p in bbox]
                b = OcrBlock(text, bbox, conf)
                b.text_hash = normalize_text(text)
                blocks.append(b)

            blocks.sort(key=lambda b: b.center_y)
            return merge_blocks(blocks)

    def recognize_plain(self, image: Image.Image) -> str:
        return "\n".join(b.text for b in self.recognize_blocks(image))

    def _preprocess(self, image: Image.Image) -> Image.Image:
        factor = float(self._config.get("ocr_scale_factor", 1.0))
        if factor != 1.0:
            w, h = image.size
            image = image.resize((int(w * factor), int(h * factor)), Image.LANCZOS)
        return image.convert("L").convert("RGB")

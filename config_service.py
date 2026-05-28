"""
配置服务：读取/写入 config.json
首次启动时如果没有 config.json，自动从 config.example.json 复制
"""

import json
import shutil
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(__file__).parent
CONFIG_PATH = CONFIG_DIR / "config.json"
EXAMPLE_PATH = CONFIG_DIR / "config.example.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "provider": "deepseek",
    "base_url": "https://api.deepseek.com",
    "api_key": "",
    "model": "deepseek-v4-flash",
    "chat_completions_path": "/chat/completions",
    "source_language": "auto",
    "target_language": "zh-CN",
    "reply_language": "tl",
    "ocr_interval_seconds": 1.5,
    "ocr_scale_factor": 2.0,
    "ocr_confidence_threshold": 0.5,
    "max_history_days": 30,
}


class ConfigService:
    """管理 config.json 的加载和保存"""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    def load(self) -> dict[str, Any]:
        """加载配置。如果没有 config.json，首次从 example 复制一份"""
        if not CONFIG_PATH.exists():
            if EXAMPLE_PATH.exists():
                shutil.copy(EXAMPLE_PATH, CONFIG_PATH)
            else:
                self._data = dict(DEFAULT_CONFIG)
                self.save()
                return self._data

        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        except (json.JSONDecodeError, OSError):
            self._data = {}

        # 用默认值补全缺失字段
        for key, value in DEFAULT_CONFIG.items():
            if key not in self._data:
                self._data[key] = value

        return self._data

    def save(self) -> None:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=4, ensure_ascii=False)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, DEFAULT_CONFIG.get(key, default))

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    @property
    def api_key(self) -> str:
        return str(self.get("api_key", ""))

    @property
    def base_url(self) -> str:
        return str(self.get("base_url", "https://api.deepseek.com"))

    @property
    def model(self) -> str:
        return str(self.get("model", "deepseek-v4-flash"))

    @property
    def chat_completions_path(self) -> str:
        return str(self.get("chat_completions_path", "/chat/completions"))

    @property
    def is_configured(self) -> bool:
        key = self.api_key
        return bool(key) and len(key) > 10 and not key.startswith("sk-your-")

"""
翻译服务：AI 翻译 API
"""

from typing import Any
import requests


INBOUND_SYSTEM_PROMPT = """你是一个实时屏幕翻译助手。
请把用户提供的外语内容翻译成简体中文。

要求：
1. 只输出中文译文；
2. 不要输出"中文翻译："；
3. 不要分析客户意图；
4. 不要输出回复建议；
5. 不要解释；
6. 不要保留原文；
7. 如果内容是代码、命令、URL、本地接口、文件路径、系统提示、菜单按钮，请返回空字符串；
8. 如果内容不是自然语言聊天内容，请返回空字符串；
9. 保留金额、数量、型号、日期、地址等关键信息；
10. 菲律宾语、英语都要准确翻译成中文。
"""

OUTBOUND_SYSTEM_PROMPT = """你是专业外贸业务员。
请将下面中文回复翻译成客户使用的语言，并优化成自然、礼貌、专业的商务表达。

要求：
1. 只输出可以直接发送给客户的译文；
2. 不要机械直译；
3. 不要承诺无法确认的价格、库存、交期；
4. 保留价格、数量、型号、日期、地址等关键信息。
"""


class TranslatorService:

    def __init__(self, config: Any) -> None:
        self._config = config

    def translate_inbound(self, text: str) -> str:
        return self._call_api(INBOUND_SYSTEM_PROMPT, text)

    def translate_outbound(self, chinese_text: str) -> str:
        return self._call_api(OUTBOUND_SYSTEM_PROMPT, chinese_text)

    def _call_api(self, system_prompt: str, user_text: str) -> str:
        base_url = self._config.get("base_url", "https://api.deepseek.com")
        api_key = self._config.get("api_key", "")
        model = self._config.get("model", "deepseek-v4-flash")
        path = self._config.get("chat_completions_path", "/chat/completions")

        if not api_key:
            raise ValueError("API Key 未配置")

        url = base_url.rstrip("/") + path
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
            "temperature": 0.3,
            "max_tokens": 1024,
        }

        response = requests.post(url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()

        data = response.json()
        content = data["choices"][0]["message"]["content"]
        return content.strip()

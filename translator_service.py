"""
翻译服务：OpenAI-compatible API 调用
"""

from typing import Any
import requests


INBOUND_SYSTEM_PROMPT = """你是外贸客服翻译助手。
请将下面客户消息翻译成简体中文，并提取客户意图。

要求：
1. 保留金额、数量、型号、尺寸、地址、日期、联系方式；
2. 不要添加原文没有的信息；
3. 如果客户在询价、压价、催货、投诉，请明确标注；
4. 如果原文是菲律宾语/他加禄语、英语或中英混合，请准确理解后翻译；
5. 输出简洁，不要废话。

输出格式：
中文翻译：
客户意图：
需要回复的重点：
"""

OUTBOUND_SYSTEM_PROMPT_TEMPLATE = """你是专业外贸业务员。
请将下面中文回复翻译成客户使用的语言，并优化成自然、礼貌、专业的商务表达。

要求：
1. 不要机械直译；
2. 不要承诺无法确认的价格、库存、交期；
3. 保留价格、数量、型号、日期、地址等关键信息；
4. 只输出可以直接发送给客户的译文；
5. 如果目标语言是菲律宾语，请使用自然的菲律宾语/他加禄语表达，不要过度夹杂英文，除非API、Viber、model等技术词必须保留。

目标语言：
{reply_language}
"""


class TranslatorService:
    """AI 翻译 API 封装"""

    def __init__(self, config: Any) -> None:
        self._config = config

    def translate_inbound(self, text: str) -> str:
        """客户消息 -> 中文翻译"""
        return self._call_api(INBOUND_SYSTEM_PROMPT, text)

    def translate_outbound(self, chinese_text: str) -> str:
        """中文回复 -> 目标语言"""
        reply_lang = self._config.get("reply_language", "tl")
        system = OUTBOUND_SYSTEM_PROMPT_TEMPLATE.format(reply_language=reply_lang)
        return self._call_api(system, chinese_text)

    def _call_api(self, system_prompt: str, user_text: str) -> str:
        """调用 OpenAI-compatible Chat Completions API"""
        base_url = self._config.get("base_url", "https://api.deepseek.com")
        api_key = self._config.get("api_key", "")
        model = self._config.get("model", "deepseek-v4-flash")
        completions_path = self._config.get(
            "chat_completions_path", "/chat/completions")

        if not api_key:
            raise ValueError("API Key 未配置，请在设置中填写 API Key")

        url = base_url.rstrip("/") + completions_path

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"客户消息：\n{user_text}"},
            ],
            "temperature": 0.3,
            "max_tokens": 2048,
        }

        response = requests.post(url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()

        data = response.json()
        content = data["choices"][0]["message"]["content"]
        return content.strip()

    def test_connection(self) -> tuple[bool, str]:
        """测试 API 连接是否正常"""
        try:
            result = self._call_api(
                "你是一个翻译助手。请把用户消息翻译成简体中文。",
                "Hello, how much for this item?"
            )
            return True, result[:100]
        except Exception as e:
            return False, str(e)

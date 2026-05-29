"""
窗口查找：定位微信/Viber/WhatsApp 等聊天窗口
"""

import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# 目标窗口标题关键词（按优先级）
TARGET_TITLES = ["微信", "WeChat", "Viber", "WhatsApp", "Telegram", "Skype"]


def _enum_callback(hwnd: int, results: list) -> bool:
    if not user32.IsWindowVisible(hwnd):
        return True
    length = user32.GetWindowTextLengthW(hwnd)
    if length == 0:
        return True
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    title = buf.value
    for kw in TARGET_TITLES:
        if kw.lower() in title.lower():
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            results.append({
                "hwnd": hwnd,
                "title": title,
                "left": rect.left,
                "top": rect.top,
                "right": rect.right,
                "bottom": rect.bottom,
                "width": rect.right - rect.left,
                "height": rect.bottom - rect.top,
            })
            break
    return True


def find_chat_windows() -> list[dict]:
    """扫描所有可见窗口，返回匹配聊天工具的窗口列表"""
    results: list[dict] = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(WNDENUMPROC(_enum_callback), 0)
    return results


def get_primary_chat_window() -> dict | None:
    """返回优先级最高的聊天窗口"""
    windows = find_chat_windows()
    if not windows:
        return None
    # 优先微信
    for w in windows:
        if "微信" in w["title"] or "WeChat" in w["title"]:
            return w
    return windows[0]

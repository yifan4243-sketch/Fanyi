"""
主界面 — 外贸屏幕实时翻译助手 V1（全屏检测 + 浮动翻译弹窗）
"""

import time
import hashlib
from typing import Any

import mss
import pyperclip
from PIL import Image

from PySide6.QtCore import (
    Qt, Signal, Slot, QThread, QTimer, QPoint,
)
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTextEdit, QGroupBox,
    QMessageBox, QDialog, QFormLayout, QLineEdit,
    QDoubleSpinBox, QDialogButtonBox, QStatusBar, QApplication,
)

from config_service import ConfigService
from database import Database
from ocr_service import OcrService, OcrBlock
from translator_service import TranslatorService
from floating_window import TranslationPopup


# ====== API Key 设置对话框 ======

class ApiKeyDialog(QDialog):
    def __init__(self, config: ConfigService, parent=None) -> None:
        super().__init__(parent)
        self._config = config
        self.setWindowTitle("设置 API Key")
        self.setMinimumWidth(450)

        layout = QFormLayout(self)

        self._base_url = QLineEdit(config.get("base_url", ""))
        self._base_url.setPlaceholderText("https://api.deepseek.com")
        layout.addRow("Base URL:", self._base_url)

        self._api_key = QLineEdit(config.get("api_key", ""))
        self._api_key.setPlaceholderText("sk-...")
        self._api_key.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addRow("API Key:", self._api_key)

        self._model = QLineEdit(config.get("model", "deepseek-chat"))
        layout.addRow("Model:", self._model)

        self._reply_lang = QLineEdit(config.get("reply_language", "tl"))
        layout.addRow("回复语言 (tl=菲律宾语, en=英语):", self._reply_lang)

        self._interval = QDoubleSpinBox()
        self._interval.setRange(0.5, 10.0)
        self._interval.setSingleStep(0.5)
        self._interval.setValue(float(config.get("ocr_interval_seconds", 1.5)))
        layout.addRow("检测间隔(秒):", self._interval)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        layout.addRow(btns)

    def _save(self) -> None:
        self._config.set("base_url", self._base_url.text())
        self._config.set("api_key", self._api_key.text())
        self._config.set("model", self._model.text())
        self._config.set("reply_language", self._reply_lang.text())
        self._config.set("ocr_interval_seconds", self._interval.value())
        self._config.save()
        self.accept()


# ====== OCR 初始化线程 ======

class OcrInitThread(QThread):
    done = Signal(bool, str)

    def __init__(self, ocr: OcrService, parent=None) -> None:
        super().__init__(parent)
        self._ocr = ocr

    def run(self) -> None:
        ok = self._ocr.initialize()
        msg = "OCR 就绪" if ok else (self._ocr.init_error or "OCR 初始化失败")
        self.done.emit(ok, msg)


# ====== 翻译线程 ======

class TranslateWorker(QThread):
    done = Signal(str, str, str)  # source, result, direction

    def __init__(self, translator: TranslatorService, text: str, direction: str = "inbound") -> None:
        super().__init__()
        self._translator = translator
        self._text = text
        self.direction = direction

    def run(self) -> None:
        try:
            if self.direction == "inbound":
                result = self._translator.translate_inbound(self._text)
            else:
                result = self._translator.translate_outbound(self._text)
            self.done.emit(self._text, result, self.direction)
        except Exception as e:
            self.done.emit(self._text, f"[ERROR] {e}", self.direction)


# ====== 全屏监听线程 ======

class FullScreenMonitor(QThread):
    text_found = Signal(list)  # list[OcrBlock]
    status_update = Signal(str)

    def __init__(self, ocr: OcrService, config: ConfigService) -> None:
        super().__init__()
        self._ocr = ocr
        self._config = config
        self._running = False
        self._seen_hashes: set[str] = set()

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        self._running = True
        interval = float(self._config.get("ocr_interval_seconds", 1.5))

        while self._running:
            try:
                self.status_update.emit("截屏中")
                img = self._capture_fullscreen()
                self.status_update.emit("OCR识别中")
                blocks = self._ocr.recognize_blocks(img)

                # 筛选出新的/变化的文本块
                new_blocks: list[OcrBlock] = []
                for b in blocks:
                    h = self._text_hash(b.text)
                    if h not in self._seen_hashes:
                        self._seen_hashes.add(h)
                        new_blocks.append(b)

                if new_blocks:
                    self.text_found.emit(new_blocks)

                # 限制缓存大小
                if len(self._seen_hashes) > 500:
                    self._seen_hashes = set(list(self._seen_hashes)[-300:])

            except Exception as e:
                self.status_update.emit(f"错误: {e}")

            for _ in range(int(interval * 10)):
                if not self._running:
                    break
                time.sleep(0.1)

    def _capture_fullscreen(self) -> Image.Image:
        with mss.mss() as sct:
            monitor = sct.monitors[1]  # 主显示器
            screenshot = sct.grab(monitor)
            return Image.frombytes(
                "RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")

    @staticmethod
    def _text_hash(text: str) -> str:
        """生成文本短哈希，用于去重"""
        clean = text.strip().lower()
        return hashlib.md5(clean.encode()).hexdigest()[:12]


# ====== 主窗口 ======

class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("外贸屏幕实时翻译助手 V1")
        self.setMinimumSize(700, 500)

        self._config = ConfigService()
        self._config.load()
        self._db = Database()
        self._db.connect()
        self._ocr = OcrService(self._config)
        self._translator = TranslatorService(self._config)
        self._monitor: FullScreenMonitor | None = None
        self._popups: list[TranslationPopup] = []

        self._init_ocr()
        self._setup_ui()

    # ========== 初始化 ==========

    def _init_ocr(self) -> None:
        self._ocr_thread = OcrInitThread(self._ocr)
        self._ocr_thread.done.connect(self._on_ocr_ready)
        self._ocr_thread.start()

    def _on_ocr_ready(self, ok: bool, msg: str) -> None:
        if ok:
            self._log(f"[OK] {msg}")
            self._update_status("就绪")
        else:
            self._log(f"[ERROR] {msg}")
            self._update_status("OCR 初始化失败")

    # ========== UI ==========

    def _setup_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(6)

        # --- 状态 ---
        status = QHBoxLayout()
        self._status_label = QLabel("当前状态: 初始化中...")
        self._status_label.setStyleSheet("font-weight: bold; color: #2196F3; font-size: 13px;")
        status.addWidget(self._status_label)
        self._screen_label = QLabel("检测范围: 全屏")
        status.addWidget(self._screen_label)
        self._lang_label = QLabel("回复语言: 菲律宾语")
        reply_lang = self._config.get("reply_language", "tl")
        lang_names = {"tl": "菲律宾语", "en": "英语", "ru": "俄语", "auto": "自动"}
        self._lang_label.setText(f"回复语言: {lang_names.get(reply_lang, reply_lang)}")
        status.addWidget(self._lang_label)
        status.addStretch()
        root.addLayout(status)

        # --- 按钮 ---
        btn = QHBoxLayout()
        btn.setSpacing(6)

        self._btn_start = QPushButton("▶ 开始全屏翻译")
        self._btn_start.clicked.connect(self._on_start)
        self._btn_start.setStyleSheet(
            "background-color: #4CAF50; color: white; font-weight: bold; padding: 8px 16px;")
        btn.addWidget(self._btn_start)

        self._btn_stop = QPushButton("⏹ 停止")
        self._btn_stop.clicked.connect(self._on_stop)
        self._btn_stop.setEnabled(False)
        btn.addWidget(self._btn_stop)

        self._btn_single = QPushButton("📷 单次翻译")
        self._btn_single.clicked.connect(self._on_single_shot)
        self._btn_single.setStyleSheet("background-color: #FF9800; color: white;")
        btn.addWidget(self._btn_single)

        self._btn_clear = QPushButton("🗑 清空")
        self._btn_clear.clicked.connect(self._on_clear)
        btn.addWidget(self._btn_clear)

        self._btn_settings = QPushButton("⚙ 设置")
        self._btn_settings.clicked.connect(self._on_settings)
        btn.addWidget(self._btn_settings)

        btn.addStretch()
        root.addLayout(btn)

        # --- 回复区域 ---
        reply_group = QGroupBox("中文回复 → 翻译为外语")
        reply_layout = QVBoxLayout(reply_group)

        reply_row = QHBoxLayout()
        self._reply_input = QTextEdit()
        self._reply_input.setPlaceholderText("老板在这里输入中文回复...")
        self._reply_input.setFont(QFont("Microsoft YaHei", 11))
        self._reply_input.setMaximumHeight(80)
        reply_row.addWidget(self._reply_input)

        self._reply_output = QTextEdit()
        self._reply_output.setReadOnly(True)
        self._reply_output.setPlaceholderText("翻译后的外语回复...")
        self._reply_output.setFont(QFont("Microsoft YaHei", 11))
        self._reply_output.setMaximumHeight(80)
        reply_row.addWidget(self._reply_output)
        reply_layout.addLayout(reply_row)

        reply_btn = QHBoxLayout()
        self._btn_translate_reply = QPushButton("🌐 翻译回复")
        self._btn_translate_reply.clicked.connect(self._on_translate_reply)
        self._btn_translate_reply.setStyleSheet(
            "background-color: #FF9800; color: white; font-weight: bold;")
        reply_btn.addWidget(self._btn_translate_reply)

        self._btn_copy = QPushButton("📋 复制回复")
        self._btn_copy.clicked.connect(self._on_copy)
        self._btn_copy.setStyleSheet(
            "background-color: #2196F3; color: white; font-weight: bold;")
        reply_btn.addWidget(self._btn_copy)
        reply_btn.addStretch()
        reply_layout.addLayout(reply_btn)

        root.addWidget(reply_group)

        # --- 日志 ---
        log_group = QGroupBox("运行日志")
        log_layout = QVBoxLayout(log_group)
        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setMaximumHeight(100)
        self._log_text.setFont(QFont("Consolas", 9))
        self._log_text.setStyleSheet("background-color: #1E1E1E; color: #CCCCCC;")
        log_layout.addWidget(self._log_text)
        root.addWidget(log_group)

        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)
        self._statusbar.showMessage("就绪 — 点击「开始全屏翻译」即可")

    # ========== 按钮事件 ==========

    def _on_start(self) -> None:
        if not self._config.is_configured:
            QMessageBox.warning(self, "提示", "请先设置 API Key！")
            return
        if not self._ocr.is_ready:
            QMessageBox.warning(self, "提示", "OCR 引擎尚未就绪")
            return

        self._btn_start.setEnabled(False)
        self._btn_stop.setEnabled(True)
        self._update_status("正在监听全屏")

        self._monitor = FullScreenMonitor(self._ocr, self._config)
        self._monitor.text_found.connect(self._on_new_text)
        self._monitor.status_update.connect(self._update_status)
        self._monitor.start()

        interval = self._config.get("ocr_interval_seconds", 1.5)
        self._log(f"开始全屏监听，间隔 {interval} 秒")
        self._statusbar.showMessage("全屏监听中 — 翻译弹窗会自动出现在原文旁边")

    def _on_stop(self) -> None:
        if self._monitor:
            self._monitor.stop()
            self._monitor.wait(3000)
            self._monitor = None
        self._btn_start.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._update_status("已停止")
        self._log("监听已停止")
        self._statusbar.showMessage("监听已停止")

    def _on_single_shot(self) -> None:
        """单次全屏翻译"""
        if not self._config.is_configured:
            QMessageBox.warning(self, "提示", "请先设置 API Key！")
            return
        if not self._ocr.is_ready:
            QMessageBox.warning(self, "提示", "OCR 引擎尚未就绪")
            return

        self._update_status("截屏中")
        try:
            with mss.mss() as sct:
                monitor = sct.monitors[1]
                screenshot = sct.grab(monitor)
                img = Image.frombytes(
                    "RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")

            self._update_status("OCR识别中")
            blocks = self._ocr.recognize_blocks(img)
            if not blocks:
                self._log("[WARN] 全屏未识别到文字")
                self._update_status("未识别到文字")
                return

            self._log(f"全屏识别到 {len(blocks)} 个文本块")
            self._translate_blocks(blocks)

        except Exception as e:
            self._log(f"[ERROR] {e}")
            self._update_status("错误")

    def _on_new_text(self, blocks: list[OcrBlock]) -> None:
        """监听线程发现新文本"""
        self._log(f"检测到 {len(blocks)} 个新文本块")
        self._translate_blocks(blocks)

    def _on_clear(self) -> None:
        # 关闭所有浮动弹窗
        for p in self._popups:
            p.close()
        self._popups.clear()
        self._reply_input.clear()
        self._reply_output.clear()
        self._log_text.clear()
        self._log("已清空")

    def _on_settings(self) -> None:
        dlg = ApiKeyDialog(self._config, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._translator = TranslatorService(self._config)
            reply_lang = self._config.get("reply_language", "tl")
            lang_names = {"tl": "菲律宾语", "en": "英语", "ru": "俄语", "auto": "自动"}
            self._lang_label.setText(f"回复语言: {lang_names.get(reply_lang, reply_lang)}")
            self._log("配置已更新")

    def _on_translate_reply(self) -> None:
        chinese = self._reply_input.toPlainText().strip()
        if not chinese:
            QMessageBox.warning(self, "提示", "请输入中文回复")
            return
        if not self._config.is_configured:
            QMessageBox.warning(self, "提示", "请先设置 API Key")
            return

        self._update_status("翻译回复中")
        w = TranslateWorker(self._translator, chinese, "outbound")
        w.done.connect(self._on_reply_done)
        w.start()

    def _on_reply_done(self, source: str, result: str, direction: str) -> None:
        self._reply_output.setPlainText(result)
        self._log("回复翻译完成")
        self._update_status("就绪")
        try:
            self._db.insert(source, result, direction,
                          str(self._config.get("source_language", "auto")),
                          str(self._config.get("reply_language", "tl")))
        except Exception:
            pass

    def _on_copy(self) -> None:
        text = self._reply_output.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "提示", "没有可复制的内容")
            return
        pyperclip.copy(text)
        self._log("已复制到剪贴板")
        self._statusbar.showMessage("已复制！", 3000)

    # ========== 核心翻译 ==========

    def _translate_blocks(self, blocks: list[OcrBlock]) -> None:
        """对多个 OCR 文本块逐一翻译并弹出浮动窗口"""
        for block in blocks:
            self._do_translate_and_popup(block)

    def _do_translate_and_popup(self, block: OcrBlock) -> None:
        w = TranslateWorker(self._translator, block.text, "inbound")
        # 用 lambda 捕获 block 信息
        w.done.connect(
            lambda src, res, d, b=block: self._show_popup(src, res, b))
        w.start()

    def _show_popup(self, source: str, result: str, block: OcrBlock) -> None:
        """在原文旁边显示翻译浮动弹窗"""
        if result.startswith("[ERROR]"):
            self._log(f"[ERROR] 翻译失败: {result}")
            return

        # 关闭旧弹窗（最多同时显示 5 个）
        while len(self._popups) >= 5:
            old = self._popups.pop(0)
            old.close()

        screen_pos = QPoint(block.top_left[0], block.top_left[1])
        popup = TranslationPopup(
            source_text=source,
            translated_text=result,
            screen_pos=screen_pos,
        )
        popup.closed.connect(lambda p=popup: self._remove_popup(p))
        self._popups.append(popup)
        self._log(f"弹窗: {source[:30]}... → {result[:30]}...")

        # 存数据库
        try:
            self._db.insert(source, result, "inbound",
                          str(self._config.get("source_language", "auto")),
                          str(self._config.get("target_language", "zh-CN")))
        except Exception:
            pass

    def _remove_popup(self, popup: TranslationPopup) -> None:
        if popup in self._popups:
            self._popups.remove(popup)

    # ========== 工具 ==========

    def _update_status(self, status: str) -> None:
        colors = {
            "就绪": "#4CAF50", "初始化中": "#2196F3",
            "截屏中": "#2196F3", "OCR识别中": "#FF9800",
            "翻译中": "#FF9800", "翻译回复中": "#FF9800",
            "正在监听全屏": "#4CAF50", "已停止": "#9E9E9E",
            "未识别到文字": "#FF5722", "错误": "#F44336",
            "OCR 初始化失败": "#F44336",
        }
        c = colors.get(status, "#9E9E9E")
        self._status_label.setText(f"当前状态: {status}")
        self._status_label.setStyleSheet(f"font-weight: bold; color: {c}; font-size: 13px;")

    def _log(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self._log_text.append(f"[{ts}] {msg}")
        cursor = self._log_text.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self._log_text.setTextCursor(cursor)

    def closeEvent(self, event) -> None:
        if self._monitor:
            self._monitor.stop()
            self._monitor.wait(3000)
        for p in self._popups:
            p.close()
        self._popups.clear()
        self._db.close()
        event.accept()

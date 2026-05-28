"""
主界面 — 外贸屏幕实时翻译助手 V1
默认模式：框选区域 → OCR → 翻译
"""

import time
import hashlib
from typing import Any

import mss
import pyperclip
from PIL import Image

from PySide6.QtCore import Qt, Signal, Slot, QThread, QTimer
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTextEdit, QGroupBox, QSplitter,
    QMessageBox, QDialog, QFormLayout, QLineEdit,
    QDoubleSpinBox, QDialogButtonBox, QStatusBar, QApplication,
)

from config_service import ConfigService
from database import Database
from ocr_service import OcrService
from translator_service import TranslatorService
from screen_selector import ScreenSelector


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

        self._model = QLineEdit(config.get("model", "deepseek-v4-flash"))
        layout.addRow("Model:", self._model)

        self._completions_path = QLineEdit(
            config.get("chat_completions_path", "/chat/completions"))
        layout.addRow("API 路径:", self._completions_path)

        self._reply_lang = QLineEdit(config.get("reply_language", "tl"))
        layout.addRow("回复语言 (tl=菲律宾语, en=英语):", self._reply_lang)

        self._interval = QDoubleSpinBox()
        self._interval.setRange(0.5, 10.0)
        self._interval.setSingleStep(0.5)
        self._interval.setValue(float(config.get("ocr_interval_seconds", 1.5)))
        layout.addRow("监听间隔(秒):", self._interval)

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
        self._config.set("chat_completions_path", self._completions_path.text())
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

    def __init__(self, translator: TranslatorService, text: str,
                 direction: str = "inbound") -> None:
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


# ====== 区域监听线程 ======

class RegionMonitor(QThread):
    text_found = Signal(str)  # 识别到的新文本
    status_signal = Signal(str)

    def __init__(self, ocr: OcrService, config: ConfigService,
                 region: tuple[int, int, int, int]) -> None:
        super().__init__()
        self._ocr = ocr
        self._config = config
        self._region = region
        self._running = False
        self._last_text = ""
        self._seen_hashes: set[str] = set()

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        self._running = True
        interval = float(self._config.get("ocr_interval_seconds", 1.5))

        while self._running:
            try:
                self.status_signal.emit("OCR识别中")
                img = self._capture_region()
                text = self._ocr.recognize_plain(img)

                if text.strip():
                    h = hashlib.md5(text.strip().lower().encode()).hexdigest()[:12]
                    if h not in self._seen_hashes:
                        self._seen_hashes.add(h)
                        self.text_found.emit(text)
                        if len(self._seen_hashes) > 300:
                            self._seen_hashes = set(list(self._seen_hashes)[-200:])
            except Exception as e:
                self.status_signal.emit(f"监听异常: {e}")

            for _ in range(int(interval * 10)):
                if not self._running:
                    break
                time.sleep(0.1)

    def _capture_region(self) -> Image.Image:
        x, y, w, h = self._region
        with mss.mss() as sct:
            monitor = {"left": x, "top": y, "width": w, "height": h}
            screenshot = sct.grab(monitor)
            return Image.frombytes(
                "RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")


# ====== 主窗口 ======

class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("外贸屏幕实时翻译助手 V1")
        self.setMinimumSize(1000, 700)

        self._config = ConfigService()
        self._config.load()
        self._db = Database()
        self._db.connect()
        self._ocr = OcrService(self._config)
        self._translator = TranslatorService(self._config)
        self._region: tuple[int, int, int, int] | None = None
        self._monitor: RegionMonitor | None = None
        self._selector: ScreenSelector | None = None

        self._init_ocr()
        self._setup_ui()
        self._log("程序已启动，请先框选微信聊天区域")
        self._update_status("就绪")

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
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # --- 状态栏 ---
        status_group = QGroupBox("状态信息")
        status_layout = QHBoxLayout(status_group)

        self._status_label = QLabel("当前状态: 初始化中...")
        self._status_label.setStyleSheet("font-weight: bold; color: #2196F3;")
        status_layout.addWidget(self._status_label)

        self._region_label = QLabel("框选区域: 未设置")
        status_layout.addWidget(self._region_label)

        reply_lang = self._config.get("reply_language", "tl")
        lang_names = {"tl": "菲律宾语", "en": "英语", "ru": "俄语", "auto": "自动"}
        self._lang_label = QLabel(
            f"回复语言: {lang_names.get(reply_lang, reply_lang)}")
        status_layout.addWidget(self._lang_label)
        status_layout.addStretch()
        root.addWidget(status_group)

        # --- 按钮区 ---
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(6)

        self._btn_select = QPushButton("框选区域")
        self._btn_select.clicked.connect(self._on_select_region)
        self._btn_select.setStyleSheet(
            "background-color: #2196F3; color: white; font-weight: bold;")
        btn_layout.addWidget(self._btn_select)

        self._btn_translate = QPushButton("翻译当前区域")
        self._btn_translate.clicked.connect(self._on_translate_now)
        self._btn_translate.setStyleSheet(
            "background-color: #FF9800; color: white; font-weight: bold;")
        btn_layout.addWidget(self._btn_translate)

        self._btn_start = QPushButton("开始监听")
        self._btn_start.clicked.connect(self._on_start_monitor)
        self._btn_start.setStyleSheet(
            "background-color: #4CAF50; color: white; font-weight: bold;")
        btn_layout.addWidget(self._btn_start)

        self._btn_stop = QPushButton("停止监听")
        self._btn_stop.clicked.connect(self._on_stop_monitor)
        self._btn_stop.setEnabled(False)
        btn_layout.addWidget(self._btn_stop)

        self._btn_settings = QPushButton("设置")
        self._btn_settings.clicked.connect(self._on_settings)
        btn_layout.addWidget(self._btn_settings)

        self._btn_clear = QPushButton("清空")
        self._btn_clear.clicked.connect(self._on_clear)
        btn_layout.addWidget(self._btn_clear)

        btn_layout.addStretch()
        root.addLayout(btn_layout)

        # --- 翻译结果区 ---
        splitter = QSplitter(Qt.Orientation.Vertical)

        top_widget = QWidget()
        top_layout = QHBoxLayout(top_widget)
        top_layout.setContentsMargins(0, 0, 0, 0)

        src_group = QGroupBox("客户原文 (OCR 识别)")
        src_layout = QVBoxLayout(src_group)
        self._source_text = QTextEdit()
        self._source_text.setReadOnly(True)
        self._source_text.setPlaceholderText("OCR 识别的原文将显示在这里...")
        self._source_text.setFont(QFont("Microsoft YaHei", 11))
        src_layout.addWidget(self._source_text)
        top_layout.addWidget(src_group)

        trans_group = QGroupBox("中文翻译")
        trans_layout = QVBoxLayout(trans_group)
        self._translated_text = QTextEdit()
        self._translated_text.setReadOnly(True)
        self._translated_text.setPlaceholderText("AI 翻译结果...")
        self._translated_text.setFont(QFont("Microsoft YaHei", 11))
        trans_layout.addWidget(self._translated_text)
        top_layout.addWidget(trans_group)

        splitter.addWidget(top_widget)

        # --- 回复区 ---
        bottom_widget = QWidget()
        bottom_layout = QHBoxLayout(bottom_widget)
        bottom_layout.setContentsMargins(0, 0, 0, 0)

        reply_in_group = QGroupBox("中文回复输入")
        reply_in_layout = QVBoxLayout(reply_in_group)
        self._reply_input = QTextEdit()
        self._reply_input.setPlaceholderText("老板输入中文回复...")
        self._reply_input.setFont(QFont("Microsoft YaHei", 11))
        self._reply_input.setMaximumHeight(150)
        reply_in_layout.addWidget(self._reply_input)

        reply_btn_layout = QHBoxLayout()
        self._btn_translate_reply = QPushButton("翻译回复")
        self._btn_translate_reply.clicked.connect(self._on_translate_reply)
        self._btn_translate_reply.setStyleSheet(
            "background-color: #FF9800; color: white; font-weight: bold;")
        reply_btn_layout.addWidget(self._btn_translate_reply)

        self._btn_copy = QPushButton("复制回复")
        self._btn_copy.clicked.connect(self._on_copy)
        self._btn_copy.setStyleSheet(
            "background-color: #2196F3; color: white; font-weight: bold;")
        reply_btn_layout.addWidget(self._btn_copy)
        reply_btn_layout.addStretch()
        reply_in_layout.addLayout(reply_btn_layout)
        bottom_layout.addWidget(reply_in_group)

        reply_out_group = QGroupBox("外语回复输出")
        reply_out_layout = QVBoxLayout(reply_out_group)
        self._reply_output = QTextEdit()
        self._reply_output.setReadOnly(True)
        self._reply_output.setPlaceholderText("翻译后的外语回复...")
        self._reply_output.setFont(QFont("Microsoft YaHei", 11))
        reply_out_layout.addWidget(self._reply_output)
        bottom_layout.addWidget(reply_out_group)

        splitter.addWidget(bottom_widget)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter)

        # --- 日志 ---
        log_group = QGroupBox("运行日志")
        log_layout = QVBoxLayout(log_group)
        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setMaximumHeight(100)
        self._log_text.setFont(QFont("Consolas", 9))
        self._log_text.setStyleSheet(
            "background-color: #1E1E1E; color: #CCCCCC;")
        log_layout.addWidget(self._log_text)
        root.addWidget(log_group)

        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)
        self._statusbar.showMessage("就绪 — 请先框选区域")

    # ========== 按钮事件 ==========

    def _on_select_region(self) -> None:
        self._log("正在打开框选工具...")
        self._update_status("等待框选区域")
        self.hide()
        QTimer.singleShot(300, self._show_selector)

    def _show_selector(self) -> None:
        self._selector = ScreenSelector()
        self._selector.region_selected.connect(self._on_region_selected)
        self._selector.destroyed.connect(self._on_selector_closed)

    def _on_region_selected(self, x: int, y: int, w: int, h: int) -> None:
        self._region = (x, y, w, h)
        self._region_label.setText(f"框选区域: ({x}, {y}) {w}x{h}")
        self._log(f"已框选区域: ({x}, {y}) {w}x{h}")
        self._update_status("就绪")
        self.show()

    def _on_selector_closed(self) -> None:
        if self._region is None:
            self._update_status("就绪")
        self.show()

    def _on_translate_now(self) -> None:
        if not self._region:
            QMessageBox.warning(self, "提示", "请先框选屏幕区域！")
            return
        if not self._ocr.is_ready:
            QMessageBox.warning(self, "提示", "OCR 引擎尚未就绪，请稍候。")
            return
        if not self._config.is_configured:
            QMessageBox.warning(self, "提示", "请先在设置中填写 API Key！")
            return

        self._update_status("OCR识别中")
        self._log("截取区域 → OCR...")

        try:
            img = self._capture_region()
            text = self._ocr.recognize_plain(img)

            if not text.strip():
                self._source_text.setPlainText("")
                self._log("[WARN] 未识别到有效文字")
                self._update_status("未识别到文字")
                return

            self._source_text.setPlainText(text)
            self._log(f"OCR 识别 {len(text)} 字符")

            self._update_status("翻译中")
            self._do_translate(text, "inbound")
        except Exception as e:
            self._log(f"[ERROR] {e}")
            self._update_status("错误")

    def _on_start_monitor(self) -> None:
        if not self._region:
            QMessageBox.warning(self, "提示", "请先框选屏幕区域！")
            return
        if not self._ocr.is_ready:
            QMessageBox.warning(self, "提示", "OCR 引擎尚未就绪。")
            return
        if not self._config.is_configured:
            QMessageBox.warning(self, "提示", "请先设置 API Key！")
            return

        self._btn_start.setEnabled(False)
        self._btn_stop.setEnabled(True)
        self._update_status("正在监听区域")

        self._monitor = RegionMonitor(self._ocr, self._config, self._region)
        self._monitor.text_found.connect(self._on_monitor_text)
        self._monitor.status_signal.connect(self._update_status)
        self._monitor.start()

        interval = self._config.get("ocr_interval_seconds", 1.5)
        self._log(f"开始监听区域，间隔 {interval} 秒")

    def _on_stop_monitor(self) -> None:
        if self._monitor:
            self._monitor.stop()
            self._monitor.wait(3000)
            self._monitor = None

        self._btn_start.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._update_status("监听已停止")
        self._log("监听已停止")

    def _on_monitor_text(self, text: str) -> None:
        self._source_text.setPlainText(text)
        self._log(f"区域检测到变化，OCR 识别 {len(text)} 字符")
        self._update_status("翻译中")
        self._do_translate(text, "inbound")

    def _on_clear(self) -> None:
        self._source_text.clear()
        self._translated_text.clear()
        self._reply_input.clear()
        self._reply_output.clear()
        self._log_text.clear()
        self._log("已清空所有内容")

    def _on_settings(self) -> None:
        dlg = ApiKeyDialog(self._config, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._translator = TranslatorService(self._config)
            self._log("配置已更新")
            reply_lang = self._config.get("reply_language", "tl")
            lang_names = {"tl": "菲律宾语", "en": "英语", "ru": "俄语", "auto": "自动"}
            self._lang_label.setText(
                f"回复语言: {lang_names.get(reply_lang, reply_lang)}")

    def _on_translate_reply(self) -> None:
        chinese = self._reply_input.toPlainText().strip()
        if not chinese:
            QMessageBox.warning(self, "提示", "请输入中文回复！")
            return
        if not self._config.is_configured:
            QMessageBox.warning(self, "提示", "请先设置 API Key！")
            return

        self._update_status("翻译回复中")
        self._do_translate(chinese, "outbound")

    def _on_copy(self) -> None:
        text = self._reply_output.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "提示", "没有可复制的内容！请先翻译回复。")
            return
        try:
            pyperclip.copy(text)
            self._log("已复制到剪贴板")
            self._statusbar.showMessage("已复制回复到剪贴板！", 3000)
        except Exception as e:
            self._log(f"[ERROR] 复制失败: {e}")

    # ========== 核心操作 ==========

    def _capture_region(self) -> Image.Image:
        assert self._region
        x, y, w, h = self._region
        with mss.mss() as sct:
            monitor = {"left": x, "top": y, "width": w, "height": h}
            screenshot = sct.grab(monitor)
            return Image.frombytes(
                "RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")

    def _do_translate(self, text: str, direction: str) -> None:
        w = TranslateWorker(self._translator, text, direction)
        w.done.connect(self._on_translate_done)
        w.start()

    @Slot(str, str, str)
    def _on_translate_done(self, source: str, result: str, direction: str) -> None:
        if result.startswith("[ERROR]"):
            self._log(f"[ERROR] 翻译失败: {result}")
            self._update_status("错误")
            QMessageBox.critical(self, "翻译错误", f"API 请求失败:\n{result}")
            return

        if direction == "outbound":
            self._reply_output.setPlainText(result)
            self._log("回复翻译完成")
        else:
            self._translated_text.setPlainText(result)
            self._log("翻译完成")

        try:
            self._db.insert(
                source_text=source, translated_text=result,
                direction=direction,
                source_language=str(self._config.get("source_language", "auto")),
                target_language=str(
                    self._config.get("reply_language", "tl")
                    if direction == "outbound"
                    else self._config.get("target_language", "zh-CN")),
            )
        except Exception:
            pass

        self._update_status("就绪")

    # ========== 工具 ==========

    def _update_status(self, status: str) -> None:
        colors = {
            "就绪": "#4CAF50", "初始化中": "#2196F3",
            "等待框选区域": "#FF9800", "OCR识别中": "#FF9800",
            "翻译中": "#FF9800", "翻译回复中": "#FF9800",
            "正在监听区域": "#4CAF50", "监听已停止": "#9E9E9E",
            "未识别到文字": "#FF5722", "错误": "#F44336",
            "OCR 初始化失败": "#F44336",
        }
        c = colors.get(status, "#9E9E9E")
        self._status_label.setText(f"当前状态: {status}")
        self._status_label.setStyleSheet(f"font-weight: bold; color: {c};")

    def _log(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self._log_text.append(f"[{ts}] {msg}")
        cursor = self._log_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._log_text.setTextCursor(cursor)

    def closeEvent(self, event) -> None:
        if self._monitor:
            self._monitor.stop()
            self._monitor.wait(3000)
        self._db.close()
        event.accept()

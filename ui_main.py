"""
主界面 - 外贸屏幕实时翻译助手 V1
默认启动后自动全屏监听，浮动弹窗显示翻译
"""

import time
import hashlib
from typing import Any

import mss
import pyperclip
from PIL import Image

from PySide6.QtCore import Qt, Signal, Slot, QThread, QTimer, QPoint
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTextEdit, QGroupBox, QSplitter,
    QMessageBox, QDialog, QFormLayout, QLineEdit,
    QDoubleSpinBox, QSpinBox, QDialogButtonBox, QStatusBar, QApplication,
)

from config_service import ConfigService
from database import Database
from ocr_service import OcrService, OcrBlock, normalize_text
from translator_service import TranslatorService
from floating_window import TranslationPopup
from screen_selector import ScreenSelector


# ====== 设置对话框 ======

class ApiKeyDialog(QDialog):
    def __init__(self, config: ConfigService, parent=None) -> None:
        super().__init__(parent)
        self._cfg = config
        self.setWindowTitle("设置")
        self.setMinimumWidth(460)

        f = QFormLayout(self)

        self._base_url = QLineEdit(config.get("base_url", ""))
        f.addRow("Base URL:", self._base_url)

        self._api_key = QLineEdit(config.get("api_key", ""))
        self._api_key.setEchoMode(QLineEdit.EchoMode.Password)
        f.addRow("API Key:", self._api_key)

        self._model = QLineEdit(config.get("model", "deepseek-v4-flash"))
        f.addRow("Model:", self._model)

        self._path = QLineEdit(config.get("chat_completions_path", "/chat/completions"))
        f.addRow("API 路径:", self._path)

        self._reply_lang = QLineEdit(config.get("reply_language", "tl"))
        f.addRow("回复语言:", self._reply_lang)

        self._interval = QDoubleSpinBox()
        self._interval.setRange(0.5, 10.0)
        self._interval.setValue(float(config.get("ocr_interval_seconds", 1.5)))
        f.addRow("监听间隔(秒):", self._interval)

        self._max_blocks = QSpinBox()
        self._max_blocks.setRange(1, 20)
        self._max_blocks.setValue(
            int(config.get("max_translate_blocks_per_round", 5)))
        f.addRow("每轮最多翻译:", self._max_blocks)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        f.addRow(btns)

    def _save(self) -> None:
        self._cfg.set("base_url", self._base_url.text())
        self._cfg.set("api_key", self._api_key.text())
        self._cfg.set("model", self._model.text())
        self._cfg.set("chat_completions_path", self._path.text())
        self._cfg.set("reply_language", self._reply_lang.text())
        self._cfg.set("ocr_interval_seconds", self._interval.value())
        self._cfg.set("max_translate_blocks_per_round", self._max_blocks.value())
        self._cfg.save()
        self.accept()


# ====== OCR 初始化线程 ======

class OcrInitThread(QThread):
    done = Signal(bool, str)
    def __init__(self, ocr: OcrService) -> None:
        super().__init__()
        self._ocr = ocr
    def run(self) -> None:
        ok = self._ocr.initialize()
        msg = "OCR 就绪" if ok else (self._ocr.init_error or "OCR 初始化失败")
        self.done.emit(ok, msg)


# ====== 翻译线程 ======

class TranslateWorker(QThread):
    done = Signal(str, str, str)  # source, result, direction, top_left_tuple
    def __init__(self, translator: TranslatorService, text: str,
                 direction: str = "inbound", block_info: dict | None = None) -> None:
        super().__init__()
        self._t = translator
        self._text = text
        self.direction = direction
        self.block_info = block_info or {}
    def run(self) -> None:
        try:
            if self.direction == "inbound":
                r = self._t.translate_inbound(self._text)
            else:
                r = self._t.translate_outbound(self._text)
            self.done.emit(self._text, r, self.direction)
        except Exception as e:
            self.done.emit(self._text, f"[ERROR] {e}", self.direction)


# ====== 全屏监听线程 ======

class FullScreenMonitor(QThread):
    new_blocks = Signal(list)       # list[(OcrBlock, translated_text)]
    status_signal = Signal(str)
    timing_signal = Signal(str)     # 性能日志

    def __init__(self, ocr: OcrService, translator: TranslatorService,
                 config: ConfigService) -> None:
        super().__init__()
        self._ocr = ocr
        self._translator = translator
        self._cfg = config
        self._running = False
        self._cache: dict[str, tuple[str, float]] = {}  # hash -> (translated, time)

    def stop(self) -> None:
        self._running = False

    def clear_cache(self) -> None:
        self._cache.clear()

    def run(self) -> None:
        self._running = True
        interval = float(self._cfg.get("ocr_interval_seconds", 1.5))
        max_blocks = int(self._cfg.get("max_translate_blocks_per_round", 5))
        cache_minutes = int(self._cfg.get("translation_cache_minutes", 10))

        while self._running:
            round_start = time.time()
            try:
                # 1. 截图
                self.status_signal.emit("截屏中")
                t0 = time.time()
                img = self._capture_screen()
                t_capture = int((time.time() - t0) * 1000)

                # 2. OCR
                self.status_signal.emit("OCR识别中")
                t0 = time.time()
                blocks = self._ocr.recognize_blocks(img)
                t_ocr = int((time.time() - t0) * 1000)

                # 3. 过滤去重
                new_blocks: list[OcrBlock] = []
                now = time.time()
                for b in blocks:
                    h = b.text_hash or normalize_text(b.text)
                    if h in self._cache:
                        cached_time = self._cache[h][1]
                        if now - cached_time < cache_minutes * 60:
                            continue
                    self._cache[h] = ("translating...", now)
                    new_blocks.append(b)

                new_blocks = new_blocks[:max_blocks]

                # 4. 清理过期缓存
                stale = [h for h, v in self._cache.items()
                         if now - v[1] > cache_minutes * 60 * 2]
                for h in stale:
                    del self._cache[h]

                # 5. 翻译
                results: list[tuple[OcrBlock, str]] = []
                if new_blocks:
                    self.status_signal.emit("翻译中")
                    t0 = time.time()
                    for b in new_blocks:
                        try:
                            translated = self._translator.translate_inbound(b.text)
                        except Exception:
                            translated = ""
                        h = b.text_hash or normalize_text(b.text)
                        self._cache[h] = (translated, time.time())
                        if translated:
                            results.append((b, translated))
                    t_trans = int((time.time() - t0) * 1000)
                else:
                    t_trans = 0

                if results:
                    self.new_blocks.emit(results)

                total = int((time.time() - round_start) * 1000)
                self.timing_signal.emit(
                    f"截图:{t_capture}ms OCR:{t_ocr}ms "
                    f"识别:{len(blocks)}块 过滤后:{len(new_blocks)} "
                    f"翻译:{len(results)}条 {t_trans}ms 总计:{total}ms")

            except Exception as e:
                self.status_signal.emit(f"监听异常: {e}")

            # 分段 sleep
            elapsed = time.time() - round_start
            sleep_time = interval - elapsed
            if sleep_time > 0:
                for _ in range(int(sleep_time * 10)):
                    if not self._running:
                        break
                    time.sleep(0.1)

    def _capture_screen(self) -> Image.Image:
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            shot = sct.grab(monitor)
            return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


# ====== 主窗口 ======

class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("外贸屏幕实时翻译助手 V1")
        self.setMinimumSize(900, 600)

        self._cfg = ConfigService()
        self._cfg.load()
        self._db = Database()
        self._db.connect()
        self._ocr = OcrService(self._cfg)
        self._translator = TranslatorService(self._cfg)
        self._monitor: FullScreenMonitor | None = None
        self._popups: list[TranslationPopup] = []

        self._init_ocr()
        self._setup_ui()

    # ====== 初始化 ======

    def _init_ocr(self) -> None:
        self._t_ocr = OcrInitThread(self._ocr)
        self._t_ocr.done.connect(self._on_ocr_ready)
        self._t_ocr.start()

    def _on_ocr_ready(self, ok: bool, msg: str) -> None:
        if ok:
            self._log(f"[OK] {msg}")
            self._auto_start()
        else:
            self._log(f"[ERROR] {msg}")
            self._update_status("OCR 初始化失败")

    def _auto_start(self) -> None:
        if self._cfg.get("auto_start_fullscreen", True):
            if not self._cfg.is_configured:
                self._log("[提示] API Key 未配置，请点「设置」填入 Key 后点「恢复全屏翻译」")
                self._update_status("等待 API Key")
                return
            self._start_monitor()
            self._log("程序已启动，正在全屏监听屏幕外语文字")

    # ====== UI ======

    def _setup_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # --- 状态栏 ---
        status_g = QGroupBox("状态信息")
        sl = QHBoxLayout(status_g)
        self._status_label = QLabel("当前状态: 初始化中...")
        self._status_label.setStyleSheet("font-weight: bold; color: #2196F3; font-size: 13px;")
        sl.addWidget(self._status_label)
        self._mode_label = QLabel("当前模式: 全屏翻译")
        sl.addWidget(self._mode_label)
        reply_lang = self._cfg.get("reply_language", "tl")
        ln = {"tl": "菲律宾语", "en": "英语", "ru": "俄语", "auto": "自动"}
        self._lang_label = QLabel(f"回复语言: {ln.get(reply_lang, reply_lang)}")
        sl.addWidget(self._lang_label)
        sl.addStretch()
        root.addWidget(status_g)

        # --- 按钮 ---
        bl = QHBoxLayout()
        bl.setSpacing(6)

        self._btn_pause = QPushButton("⏸ 暂停全屏翻译")
        self._btn_pause.clicked.connect(self._on_pause)
        self._btn_pause.setStyleSheet("background: #FF9800; color: white; font-weight: bold;")
        bl.addWidget(self._btn_pause)

        self._btn_resume = QPushButton("▶ 恢复全屏翻译")
        self._btn_resume.clicked.connect(self._on_resume)
        self._btn_resume.setStyleSheet("background: #4CAF50; color: white; font-weight: bold;")
        self._btn_resume.setEnabled(False)
        bl.addWidget(self._btn_resume)

        self._btn_single = QPushButton("📷 翻译当前屏幕")
        self._btn_single.clicked.connect(self._on_single_shot)
        bl.addWidget(self._btn_single)

        self._btn_select = QPushButton("📐 框选区域(备用)")
        self._btn_select.clicked.connect(self._on_select_region)
        bl.addWidget(self._btn_select)

        self._btn_settings = QPushButton("⚙ 设置")
        self._btn_settings.clicked.connect(self._on_settings)
        bl.addWidget(self._btn_settings)

        self._btn_clear_popups = QPushButton("🗑 清空弹窗")
        self._btn_clear_popups.clicked.connect(self._on_clear_popups)
        bl.addWidget(self._btn_clear_popups)

        self._btn_clear_log = QPushButton("📋 清空日志")
        self._btn_clear_log.clicked.connect(lambda: self._log_text.clear())
        bl.addWidget(self._btn_clear_log)

        bl.addStretch()
        root.addLayout(bl)

        # --- 翻译结果 + 回复 ---
        splitter = QSplitter(Qt.Orientation.Vertical)

        top = QWidget()
        tl = QHBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)

        src_g = QGroupBox("客户原文")
        sl2 = QVBoxLayout(src_g)
        self._source_text = QTextEdit()
        self._source_text.setReadOnly(True)
        self._source_text.setPlaceholderText("最新识别到的外语原文...")
        self._source_text.setFont(QFont("Microsoft YaHei", 11))
        sl2.addWidget(self._source_text)
        tl.addWidget(src_g)

        trans_g = QGroupBox("中文翻译")
        tl2 = QVBoxLayout(trans_g)
        self._translated_text = QTextEdit()
        self._translated_text.setReadOnly(True)
        self._translated_text.setPlaceholderText("AI 翻译结果...")
        self._translated_text.setFont(QFont("Microsoft YaHei", 11))
        tl2.addWidget(self._translated_text)
        tl.addWidget(trans_g)

        splitter.addWidget(top)

        # 回复区
        bottom = QWidget()
        bl2 = QHBoxLayout(bottom)
        bl2.setContentsMargins(0, 0, 0, 0)

        in_g = QGroupBox("中文回复输入")
        il = QVBoxLayout(in_g)
        self._reply_input = QTextEdit()
        self._reply_input.setPlaceholderText("老板输入中文回复...")
        self._reply_input.setFont(QFont("Microsoft YaHei", 11))
        self._reply_input.setMaximumHeight(120)
        il.addWidget(self._reply_input)
        rbl = QHBoxLayout()
        self._btn_trans_reply = QPushButton("🌐 翻译回复")
        self._btn_trans_reply.clicked.connect(self._on_translate_reply)
        self._btn_trans_reply.setStyleSheet("background: #FF9800; color: white; font-weight: bold;")
        rbl.addWidget(self._btn_trans_reply)
        self._btn_copy = QPushButton("📋 复制回复")
        self._btn_copy.clicked.connect(self._on_copy)
        self._btn_copy.setStyleSheet("background: #2196F3; color: white; font-weight: bold;")
        rbl.addWidget(self._btn_copy)
        rbl.addStretch()
        il.addLayout(rbl)
        bl2.addWidget(in_g)

        out_g = QGroupBox("外语回复输出")
        ol = QVBoxLayout(out_g)
        self._reply_output = QTextEdit()
        self._reply_output.setReadOnly(True)
        self._reply_output.setPlaceholderText("AI 翻译后的外语回复...")
        self._reply_output.setFont(QFont("Microsoft YaHei", 11))
        ol.addWidget(self._reply_output)
        bl2.addWidget(out_g)

        splitter.addWidget(bottom)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter)

        # --- 日志 ---
        log_g = QGroupBox("运行日志")
        ll = QVBoxLayout(log_g)
        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setMaximumHeight(100)
        self._log_text.setFont(QFont("Consolas", 9))
        self._log_text.setStyleSheet("background: #1E1E1E; color: #CCC;")
        ll.addWidget(self._log_text)
        root.addWidget(log_g)

        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)
        self._statusbar.showMessage("就绪 — 全屏监听模式")

    # ====== 按钮事件 ======

    def _on_pause(self) -> None:
        self._stop_monitor()
        self._btn_pause.setEnabled(False)
        self._btn_resume.setEnabled(True)
        self._update_status("已暂停")
        self._log("全屏翻译已暂停")

    def _on_resume(self) -> None:
        if not self._cfg.is_configured:
            QMessageBox.warning(self, "提示", "请先设置 API Key！")
            return
        if not self._ocr.is_ready:
            QMessageBox.warning(self, "提示", "OCR 尚未就绪")
            return
        self._start_monitor()
        self._btn_pause.setEnabled(True)
        self._btn_resume.setEnabled(False)
        self._log("全屏翻译已恢复")

    def _on_single_shot(self) -> None:
        if not self._cfg.is_configured:
            QMessageBox.warning(self, "提示", "请先设置 API Key！")
            return
        if not self._ocr.is_ready:
            QMessageBox.warning(self, "提示", "OCR 尚未就绪")
            return

        self._update_status("OCR识别中")
        try:
            with mss.mss() as sct:
                shot = sct.grab(sct.monitors[1])
                img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            blocks = self._ocr.recognize_blocks(img)
            if not blocks:
                self._log("[WARN] 未识别到外语文本")
                self._update_status("全屏监听中")
                return

            max_n = int(self._cfg.get("max_translate_blocks_per_round", 5))
            for b in blocks[:max_n]:
                self._source_text.setPlainText(b.text)
                self._do_translate(b.text, "inbound", {
                    "x": b.top_left[0], "y": b.top_left[1]})
        except Exception as e:
            self._log(f"[ERROR] {e}")
        self._update_status("全屏监听中")

    def _on_select_region(self) -> None:
        self.hide()
        self._selector = ScreenSelector()
        self._selector.region_selected.connect(self._on_region_done)
        QTimer.singleShot(300, lambda: None)

    def _on_region_done(self, x: int, y: int, w: int, h: int) -> None:
        self._log(f"备用框选: ({x},{y}) {w}x{h}")
        self._region = (x, y, w, h)
        self.show()

    def _on_clear_popups(self) -> None:
        for p in self._popups:
            p.close()
        self._popups.clear()
        self._log("已清空所有翻译弹窗")

    def _on_settings(self) -> None:
        dlg = ApiKeyDialog(self._cfg, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._translator = TranslatorService(self._cfg)
            self._log("配置已更新")
            rl = self._cfg.get("reply_language", "tl")
            ln = {"tl": "菲律宾语", "en": "英语", "ru": "俄语", "auto": "自动"}
            self._lang_label.setText(f"回复语言: {ln.get(rl, rl)}")

    def _on_translate_reply(self) -> None:
        ch = self._reply_input.toPlainText().strip()
        if not ch:
            QMessageBox.warning(self, "提示", "请输入中文回复")
            return
        if not self._cfg.is_configured:
            QMessageBox.warning(self, "提示", "请先设置 API Key")
            return
        self._update_status("翻译回复中")
        self._do_translate(ch, "outbound")

    def _on_copy(self) -> None:
        t = self._reply_output.toPlainText().strip()
        if not t:
            QMessageBox.warning(self, "提示", "没有可复制的内容")
            return
        pyperclip.copy(t)
        self._log("已复制到剪贴板")
        self._statusbar.showMessage("已复制！", 3000)

    # ====== 监听控制 ======

    def _start_monitor(self) -> None:
        if self._monitor and self._monitor.isRunning():
            return
        self._monitor = FullScreenMonitor(self._ocr, self._translator, self._cfg)
        self._monitor.new_blocks.connect(self._on_blocks_translated)
        self._monitor.status_signal.connect(self._update_status)
        self._monitor.timing_signal.connect(self._log)
        self._monitor.start()
        self._update_status("全屏监听中")

    def _stop_monitor(self) -> None:
        if self._monitor:
            self._monitor.stop()
            self._monitor.wait(5000)
            self._monitor = None

    # ====== 翻译回调 ======

    @Slot(list)
    def _on_blocks_translated(self, results: list) -> None:
        """results: list[(OcrBlock, translated_text)]"""
        for block, translated in results:
            if translated.startswith("[ERROR]"):
                continue
            self._source_text.setPlainText(block.text)
            self._translated_text.setPlainText(translated)
            self._show_popup(block, translated)
            try:
                self._db.insert(block.text, translated, "inbound",
                              self._cfg.get("source_language", "auto"),
                              self._cfg.get("target_language", "zh-CN"))
            except Exception:
                pass

    def _show_popup(self, block: OcrBlock, translated: str) -> None:
        while len(self._popups) >= 8:
            old = self._popups.pop(0)
            old.close()
        pos = QPoint(block.top_left[0], block.top_left[1])
        popup = TranslationPopup(block.text, translated, pos)
        popup.closed.connect(lambda p=popup: self._rm_popup(p))
        self._popups.append(popup)

    def _rm_popup(self, p: TranslationPopup) -> None:
        if p in self._popups:
            self._popups.remove(p)

    # ====== 翻译 ======

    def _do_translate(self, text: str, direction: str, info: dict | None = None) -> None:
        w = TranslateWorker(self._translator, text, direction, info or {})
        w.done.connect(self._on_translate_done)
        w.start()

    @Slot(str, str, str)
    def _on_translate_done(self, source: str, result: str, direction: str) -> None:
        if result.startswith("[ERROR]"):
            self._log(f"[ERROR] 翻译失败: {result}")
            self._update_status("全屏监听中")
            return

        sender = self.sender()
        info = {}
        if isinstance(sender, TranslateWorker):
            info = sender.block_info

        if direction == "outbound":
            self._reply_output.setPlainText(result)
            self._log("回复翻译完成")
        else:
            self._translated_text.setPlainText(result)
            if info:
                pos = QPoint(info.get("x", 100), info.get("y", 100))
                popup = TranslationPopup(source, result, pos)
                popup.closed.connect(lambda p=popup: self._rm_popup(p))
                self._popups.append(popup)

        try:
            self._db.insert(source, result, direction,
                          self._cfg.get("source_language", "auto"),
                          self._cfg.get("reply_language", "tl")
                          if direction == "outbound"
                          else self._cfg.get("target_language","zh-CN"))
        except Exception:
            pass
        self._update_status("全屏监听中")

    # ====== 工具 ======

    def _update_status(self, s: str) -> None:
        colors = {
            "全屏监听中": "#4CAF50", "初始化中": "#2196F3",
            "截屏中": "#2196F3", "OCR识别中": "#FF9800",
            "翻译中": "#FF9800", "翻译回复中": "#FF9800",
            "已暂停": "#9E9E9E", "等待 API Key": "#F44336",
            "OCR 初始化失败": "#F44336", "错误": "#F44336",
        }
        c = colors.get(s, "#9E9E9E")
        self._status_label.setText(f"当前状态: {s}")
        self._status_label.setStyleSheet(f"font-weight: bold; color: {c}; font-size: 13px;")

    def _log(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self._log_text.append(f"[{ts}] {msg}")
        c = self._log_text.textCursor()
        c.movePosition(QTextCursor.MoveOperation.End)
        self._log_text.setTextCursor(c)

    def closeEvent(self, event) -> None:
        self._stop_monitor()
        for p in self._popups:
            p.close()
        self._popups.clear()
        self._db.close()
        event.accept()

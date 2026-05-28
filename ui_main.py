"""
外贸屏幕实时翻译助手 V1 — 全屏监听 + 浮动弹窗
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
    QDoubleSpinBox, QSpinBox, QDialogButtonBox, QStatusBar,
)

from config_service import ConfigService
from database import Database
from ocr_service import OcrService, OcrBlock, normalize_text, merge_blocks
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
        self._interval.setValue(float(config.get("ocr_interval_seconds", 2.5)))
        f.addRow("监听间隔(秒):", self._interval)
        self._max_blocks = QSpinBox()
        self._max_blocks.setRange(1, 10)
        self._max_blocks.setValue(int(config.get("max_translate_blocks_per_round", 3)))
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


# ====== OCR 初始化 ======

class OcrInitThread(QThread):
    done = Signal(bool, str)
    def __init__(self, ocr: OcrService):
        super().__init__()
        self._ocr = ocr
    def run(self) -> None:
        ok = self._ocr.initialize()
        msg = "OCR 就绪" if ok else (self._ocr.init_error or "OCR 失败")
        self.done.emit(ok, msg)


# ====== 翻译线程 ======

class TranslateWorker(QThread):
    done = Signal(str, str, str)  # source, result, direction
    def __init__(self, translator: TranslatorService, text: str,
                 direction: str = "inbound", block_info: dict | None = None):
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


# ====== 全屏监听 ======

class FullScreenMonitor(QThread):
    result_ready = Signal(str)  # translated_text only
    status_signal = Signal(str)
    timing_signal = Signal(str)
    no_text_signal = Signal()

    def __init__(self, ocr: OcrService, translator: TranslatorService,
                 config: ConfigService) -> None:
        super().__init__()
        self._ocr = ocr
        self._t = translator
        self._cfg = config
        self._running = False
        self._cache: dict[str, float] = {}
        self._busy = False

    def stop(self) -> None:
        self._running = False

    def clear_cache(self) -> None:
        self._cache.clear()

    def run(self) -> None:
        self._running = True
        interval = float(self._cfg.get("ocr_interval_seconds", 2.5))
        cache_min = int(self._cfg.get("translation_cache_minutes", 10))

        while self._running:
            if self._busy:
                time.sleep(0.2)
                continue
            self._busy = True
            r0 = time.time()
            try:
                self.status_signal.emit("截屏中")
                t0 = time.time()
                img = self._capture()
                t_cap = int((time.time() - t0) * 1000)

                self.status_signal.emit("OCR识别中")
                t0 = time.time()
                blocks = self._ocr.recognize_blocks(img)
                t_ocr = int((time.time() - t0) * 1000)
                total_blocks = len(blocks)

                # 去重 + 取最长的一条
                now = time.time()
                best: OcrBlock | None = None
                for b in blocks:
                    h = normalize_text(b.text)
                    if h in self._cache:
                        if now - self._cache[h] < cache_min * 60:
                            continue
                    self._cache[h] = now
                    if best is None or len(b.text) > len(best.text):
                        best = b

                # 清理过期
                if len(self._cache) > 500:
                    stale = [h for h, ts in self._cache.items()
                             if now - ts > cache_min * 60 * 2]
                    for h in stale:
                        del self._cache[h]

                if best:
                    self.status_signal.emit("翻译中")
                    t0 = time.time()
                    try:
                        translated = self._t.translate_inbound(best.text)
                    except Exception:
                        translated = ""
                    t_trans = int((time.time() - t0) * 1000)

                    if translated and translated.strip():
                        self.result_ready.emit(translated.strip())
                    else:
                        self.no_text_signal.emit()

                    self.timing_signal.emit(
                        f"截图:{t_cap}ms OCR:{t_ocr}ms "
                        f"识别:{total_blocks}块 翻译:1条 {t_trans}ms "
                        f"总计:{int((time.time()-r0)*1000)}ms")
                else:
                    self.timing_signal.emit(
                        f"截图:{t_cap}ms OCR:{t_ocr}ms 识别:{total_blocks}块 过滤后:0")
            except Exception as e:
                self.status_signal.emit(f"异常: {e}")
            finally:
                self._busy = False

            elapsed = time.time() - r0
            sleep_t = interval - elapsed
            if sleep_t > 0:
                for _ in range(int(sleep_t * 10)):
                    if not self._running:
                        break
                    time.sleep(0.1)

    def _capture(self) -> Image.Image:
        with mss.mss() as sct:
            shot = sct.grab(sct.monitors[1])
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
        self._workers: list[TranslateWorker] = []

        self._init_ocr()
        self._setup_ui()

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
                self._log("[提示] API Key 未配置，请点「设置」填入后点「恢复」")
                self._update_status("等待 API Key")
                return
            self._start_monitor()
            self._log("程序已启动，正在全屏监听屏幕外语文字")

    # ====== UI ======

    def _setup_ui(self) -> None:
        cw = QWidget()
        self.setCentralWidget(cw)
        root = QVBoxLayout(cw)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # 状态
        sg = QGroupBox("状态信息")
        sl = QHBoxLayout(sg)
        self._status_label = QLabel("当前状态: 初始化中...")
        self._status_label.setStyleSheet("font-weight: bold; color: #2196F3; font-size: 13px;")
        sl.addWidget(self._status_label)
        self._mode_label = QLabel("当前模式: 全屏翻译")
        sl.addWidget(self._mode_label)
        rl = self._cfg.get("reply_language", "tl")
        ln = {"tl": "菲律宾语", "en": "英语", "ru": "俄语", "auto": "自动"}
        self._lang_label = QLabel(f"回复语言: {ln.get(rl, rl)}")
        sl.addWidget(self._lang_label)
        sl.addStretch()
        root.addWidget(sg)

        # 按钮
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
        self._btn_select = QPushButton("📐 框选(备用)")
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

        # 翻译结果 + 回复
        splitter = QSplitter(Qt.Orientation.Vertical)

        top = QWidget()
        tl = QHBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        sg2 = QGroupBox("客户原文")
        sl2 = QVBoxLayout(sg2)
        self._source_text = QTextEdit()
        self._source_text.setReadOnly(True)
        self._source_text.setPlaceholderText("OCR 识别到的外语原文...")
        self._source_text.setFont(QFont("Microsoft YaHei", 11))
        sl2.addWidget(self._source_text)
        tl.addWidget(sg2)
        tg2 = QGroupBox("中文翻译")
        tl2 = QVBoxLayout(tg2)
        self._translated_text = QTextEdit()
        self._translated_text.setReadOnly(True)
        self._translated_text.setPlaceholderText("AI 翻译结果...")
        self._translated_text.setFont(QFont("Microsoft YaHei", 11))
        tl2.addWidget(self._translated_text)
        tl.addWidget(tg2)
        splitter.addWidget(top)

        bot = QWidget()
        bl2 = QHBoxLayout(bot)
        bl2.setContentsMargins(0, 0, 0, 0)
        ig = QGroupBox("中文回复输入")
        il = QVBoxLayout(ig)
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
        bl2.addWidget(ig)
        og = QGroupBox("外语回复输出")
        ol = QVBoxLayout(og)
        self._reply_output = QTextEdit()
        self._reply_output.setReadOnly(True)
        self._reply_output.setPlaceholderText("AI 翻译后的外语...")
        self._reply_output.setFont(QFont("Microsoft YaHei", 11))
        ol.addWidget(self._reply_output)
        bl2.addWidget(og)
        splitter.addWidget(bot)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter)

        lg = QGroupBox("运行日志")
        ll = QVBoxLayout(lg)
        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setMaximumHeight(100)
        self._log_text.setFont(QFont("Consolas", 9))
        self._log_text.setStyleSheet("background: #1E1E1E; color: #CCC;")
        ll.addWidget(self._log_text)
        root.addWidget(lg)

        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)
        self._statusbar.showMessage("就绪 — 全屏监听模式")

    # ====== 按钮 ======

    def _on_pause(self) -> None:
        self._stop_monitor()
        self._btn_pause.setEnabled(False)
        self._btn_resume.setEnabled(True)
        self._update_status("已暂停")
        self._log("全屏翻译已暂停")

    def _on_resume(self) -> None:
        if not self._cfg.is_configured:
            QMessageBox.warning(self, "提示", "请先设置 API Key")
            return
        self._start_monitor()
        self._btn_pause.setEnabled(True)
        self._btn_resume.setEnabled(False)

    def _on_single_shot(self) -> None:
        if not self._cfg.is_configured:
            QMessageBox.warning(self, "提示", "请先设置 API Key")
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
            max_n = int(self._cfg.get("max_translate_blocks_per_round", 3))
            blocks = blocks[:max_n]
            merged = "\n---\n".join(b.text for b in blocks)
            self._start_worker(merged, "inbound", {
                "x": blocks[0].top_left[0], "y": blocks[0].top_left[1]})
        except Exception as e:
            self._log(f"[ERROR] {e}")
        self._update_status("全屏监听中")

    def _on_select_region(self) -> None:
        self.hide()
        self._selector = ScreenSelector()
        self._selector.region_selected.connect(self._on_region_done)

    def _on_region_done(self, x: int, y: int, w: int, h: int) -> None:
        self._log(f"备用框选: ({x},{y}) {w}x{h} — 仍使用全屏模式")
        self.show()

    def _on_clear_popups(self) -> None:
        for p in self._popups:
            p.close()
        self._popups.clear()
        self._log("已清空弹窗")

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
        self._start_worker(ch, "outbound")

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
        self._monitor.result_ready.connect(self._on_monitor_result)
        self._monitor.no_text_signal.connect(lambda: self._log("已过滤无效内容"))
        self._monitor.status_signal.connect(self._update_status)
        self._monitor.timing_signal.connect(self._log)
        self._monitor.start()
        self._update_status("全屏监听中")
        self._log("全屏监听已启动")

    def _stop_monitor(self) -> None:
        if self._monitor:
            self._monitor.stop()
            self._monitor.wait(5000)
            self._monitor = None

    @Slot(str)
    def _on_monitor_result(self, translated: str) -> None:
        t = translated.strip()
        if not t:
            return

        self._translated_text.setPlainText(t)
        self._log(f"翻译完成，{len(t)} 字")

        try:
            self._db.insert("", t, "inbound",
                          self._cfg.get("source_language", "auto"),
                          self._cfg.get("target_language", "zh-CN"))
        except Exception:
            pass

        while len(self._popups) >= 6:
            old = self._popups.pop(0)
            old.close()
        pos = QPoint(200, 150)
        popup = TranslationPopup(translated_text=t, screen_pos=pos)
        popup.closed.connect(lambda p=popup: self._rm_popup(p))
        self._popups.append(popup)
        self._update_status("全屏监听中")

    def _rm_popup(self, p: TranslationPopup) -> None:
        if p in self._popups:
            self._popups.remove(p)

    # ====== 翻译 Worker（保存引用防 GC） ======

    def _start_worker(self, text: str, direction: str, info: dict | None = None) -> None:
        w = TranslateWorker(self._translator, text, direction, info or {})
        w.done.connect(self._on_worker_done)
        w.finished.connect(lambda: self._cleanup_worker(w))
        self._workers.append(w)
        w.start()

    def _cleanup_worker(self, w: TranslateWorker) -> None:
        if w in self._workers:
            self._workers.remove(w)

    @Slot(str, str, str)
    def _on_worker_done(self, source: str, result: str, direction: str) -> None:
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
                pos = QPoint(info.get("x", 150), info.get("y", 150))
                popup = TranslationPopup(translated_text=result, screen_pos=pos)
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
        cursor = self._log_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._log_text.setTextCursor(cursor)

    def closeEvent(self, event) -> None:
        self._stop_monitor()
        for p in self._popups:
            p.close()
        self._popups.clear()
        self._db.close()
        event.accept()

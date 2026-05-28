"""
浮动翻译弹窗：在原文旁边显示中文翻译
"""

from PySide6.QtCore import Qt, QPoint, Signal
from PySide6.QtGui import QFont, QColor, QPainter, QPen, QBrush
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QPushButton


class TranslationPopup(QWidget):
    """无边框浮动弹窗，显示在屏幕原文旁边"""

    closed = Signal()

    STYLE = """
        QWidget#TranslationPopup {
            background-color: #FFFFFF;
            border: 2px solid #FF6D00;
            border-radius: 8px;
        }
    """

    def __init__(
        self,
        source_text: str,
        translated_text: str,
        screen_pos: QPoint,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("TranslationPopup")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setStyleSheet(self.STYLE)

        self._screen_pos = screen_pos
        self._setup_ui(source_text, translated_text)

        self.show()

    def _setup_ui(self, source_text: str, translated_text: str) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        # 原文
        src_label = QLabel(source_text[:120])
        src_label.setFont(QFont("Microsoft YaHei", 9))
        src_label.setStyleSheet("color: #888888;")
        src_label.setWordWrap(True)
        src_label.setMaximumWidth(320)
        layout.addWidget(src_label)

        # 译文
        trans_label = QLabel(translated_text[:200])
        trans_label.setFont(QFont("Microsoft YaHei", 13))
        trans_label.setStyleSheet("color: #000000; font-weight: bold;")
        trans_label.setWordWrap(True)
        trans_label.setMaximumWidth(320)
        layout.addWidget(trans_label)

        # 关闭按钮
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(24, 24)
        close_btn.setStyleSheet(
            "QPushButton { border: none; color: #999; font-size: 14px; }"
            "QPushButton:hover { color: #F44336; }"
        )
        close_btn.clicked.connect(self.close)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)

        btn_layout = QVBoxLayout()
        btn_layout.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(btn_layout)

        self.adjustSize()

        # 定位到原文旁边（原文右下角偏移）
        self.move(self._screen_pos + QPoint(10, 10))

        # 确保不超出屏幕
        screen = self.screen()
        if screen:
            screen_geo = screen.availableGeometry()
            if self.x() + self.width() > screen_geo.right():
                self.move(screen_geo.right() - self.width(), self.y())
            if self.y() + self.height() > screen_geo.bottom():
                self.move(self.x(), screen_geo.bottom() - self.height())

    def closeEvent(self, event) -> None:
        self.closed.emit()
        super().closeEvent(event)

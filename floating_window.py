"""
浮动翻译弹窗：只显示中文译文
"""

from PySide6.QtCore import Qt, QPoint, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QPushButton


class TranslationPopup(QWidget):
    """无边框浮动弹窗，显示中文翻译在原文旁边"""

    closed = Signal()

    def __init__(self, translated_text: str, screen_pos: QPoint,
                 source_text: str = "", parent=None) -> None:
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
        self.setStyleSheet(
            "QWidget#TranslationPopup {"
            "  background-color: #FFFFFF;"
            "  border: 1.5px solid #4A90D9;"
            "  border-radius: 6px;"
            "}"
        )

        self._screen_pos = screen_pos
        self._setup_ui(translated_text)
        self.show()

    def _setup_ui(self, translated: str) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 7, 30, 7)
        layout.setSpacing(0)

        text = translated[:180]
        if len(translated) > 180:
            text += "..."

        label = QLabel(text)
        label.setFont(QFont("Microsoft YaHei", 14))
        label.setStyleSheet("color: #222222; background: transparent; border: none;")
        label.setWordWrap(True)
        label.setMinimumWidth(240)
        label.setMaximumWidth(340)
        layout.addWidget(label)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(18, 18)
        close_btn.setStyleSheet(
            "QPushButton { border: none; color: #AAA; font-size: 11px; }"
            "QPushButton:hover { color: #555; }"
        )
        close_btn.clicked.connect(self.close)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)

        # 把关闭按钮放在右上角
        close_btn.setParent(self)
        close_btn.move(self.width() - 22, 4)

        self.adjustSize()
        # 确保关闭按钮在正确位置
        close_btn.move(self.width() - 22, 4)

        self.move(self._screen_pos + QPoint(10, 10))

        screen = self.screen()
        if screen:
            geo = screen.availableGeometry()
            if self.x() + self.width() > geo.right():
                self.move(geo.right() - self.width(), self.y())
            if self.y() + self.height() > geo.bottom():
                self.move(self.x(), geo.bottom() - self.height())

    def closeEvent(self, event) -> None:
        self.closed.emit()
        super().closeEvent(event)

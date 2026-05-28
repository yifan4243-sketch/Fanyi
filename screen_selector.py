"""
屏幕区域框选器：全屏半透明遮罩 + 鼠标拖拽框选
"""

from PySide6.QtCore import Qt, QRect, QPoint, Signal
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QFont
from PySide6.QtWidgets import QWidget, QApplication


class ScreenSelector(QWidget):
    """全屏半透明遮罩，用户拖拽框选区域"""

    region_selected = Signal(int, int, int, int)  # x, y, w, h

    def __init__(self) -> None:
        super().__init__()
        self._start: QPoint | None = None
        self._end: QPoint | None = None
        self._selected_rect: QRect | None = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setCursor(Qt.CursorShape.CrossCursor)

        screen_geo = QApplication.primaryScreen().availableGeometry()
        self.setGeometry(screen_geo)
        self.setStyleSheet("background-color: rgba(0, 0, 0, 80);")
        self.show()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self._selected_rect:
            pen = QPen(QColor(0, 200, 255), 2, Qt.PenStyle.SolidLine)
            painter.setPen(pen)
            fill = QBrush(QColor(0, 200, 255, 40))
            painter.setBrush(fill)
            painter.drawRect(self._selected_rect)

            font = QFont("Microsoft YaHei", 10)
            painter.setFont(font)
            painter.setPen(QColor(255, 255, 255))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            label = f"{self._selected_rect.width()} x {self._selected_rect.height()}"
            painter.drawText(
                self._selected_rect.x() + 5,
                self._selected_rect.y() + self._selected_rect.height() - 8,
                label,
            )

        if not self._selected_rect:
            font = QFont("Microsoft YaHei", 16)
            painter.setFont(font)
            painter.setPen(QColor(255, 255, 255))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "拖拽鼠标框选翻译区域\n按 ESC 取消",
            )

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._start = event.pos()
            self._end = event.pos()
            self._selected_rect = None
            self.update()

    def mouseMoveEvent(self, event) -> None:
        if self._start:
            self._end = event.pos()
            x1, y1 = self._start.x(), self._start.y()
            x2, y2 = self._end.x(), self._end.y()
            self._selected_rect = QRect(
                min(x1, x2), min(y1, y2),
                abs(x2 - x1), abs(y2 - y1),
            )
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._end = event.pos()
            if self._start:
                x1, y1 = self._start.x(), self._start.y()
                x2, y2 = self._end.x(), self._end.y()
                sx, sy = min(x1, x2), min(y1, y2)
                sw, sh = abs(x2 - x1), abs(y2 - y1)
                if sw > 10 and sh > 10:
                    self._selected_rect = QRect(sx, sy, sw, sh)
                    self.region_selected.emit(sx, sy, sw, sh)
            self.close()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close()

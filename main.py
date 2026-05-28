"""
外贸屏幕实时翻译助手 V1 — 全屏检测 + 浮动翻译弹窗
运行: python main.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from ui_main import MainWindow


def main() -> None:
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    app = QApplication(sys.argv)
    app.setApplicationName("外贸屏幕实时翻译助手 V1")

    app.setStyleSheet("""
        QMainWindow { background-color: #F5F5F5; }
        QGroupBox {
            font-weight: bold; border: 1px solid #CCC;
            border-radius: 4px; margin-top: 8px; padding-top: 16px;
        }
        QGroupBox::title {
            subcontrol-origin: margin; left: 10px; padding: 0 4px;
        }
        QPushButton {
            padding: 6px 14px; border: 1px solid #CCC;
            border-radius: 4px; background: #FFF;
        }
        QPushButton:hover { background: #E8E8E8; }
        QPushButton:pressed { background: #D0D0D0; }
        QPushButton:disabled { color: #999; background: #F0F0F0; }
        QTextEdit { border: 1px solid #CCC; border-radius: 4px; }
        QStatusBar { background: #E8E8E8; }
    """)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

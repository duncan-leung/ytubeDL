#!/usr/bin/env python3
"""ytubeDL - Modern Desktop YouTube Downloader.

Cross-platform desktop application for macOS and Windows.
"""
import sys
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication

from core.config import Config
from core.db import Database
from ui.main_window import MainWindow


def main():
    # Enable High DPI display support for Mac Retina and Windows 4K displays
    if hasattr(Qt, "AA_EnableHighDpiScaling"):
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    if hasattr(Qt, "AA_UseHighDpiPixmaps"):
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setApplicationName("ytubeDL")
    app.setOrganizationName("ytubeDL")

    config = Config()
    db = Database()

    window = MainWindow(db, config)
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

"""Batch URL paste dialog for ytubeDL."""
import re
from typing import List
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)


class BatchDialog(QDialog):
    """Dialog allowing users to paste multiple URLs at once."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Batch Add YouTube URLs")
        self.setMinimumSize(560, 420)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        instruction = QLabel(
            "Paste one or more YouTube URLs below (one per line, or space-separated).\n"
            "Format can be raw URLs, or 'URL | filename | title':"
        )
        instruction.setStyleSheet("color: #475569; font-size: 12px;")
        layout.addWidget(instruction)

        self.text_edit = QPlainTextEdit(self)
        self.text_edit.setPlaceholderText(
            "https://www.youtube.com/watch?v=...\n"
            "https://youtu.be/...\n"
            "https://www.youtube.com/shorts/..."
        )
        self.text_edit.textChanged.connect(self._update_url_count)
        layout.addWidget(self.text_edit)

        # Quick actions
        btn_row = QHBoxLayout()
        btn_paste = QPushButton("📋 Paste from Clipboard", self)
        btn_paste.clicked.connect(self._paste_clipboard)
        btn_row.addWidget(btn_paste)

        btn_clear = QPushButton("🗑 Clear", self)
        btn_clear.clicked.connect(self.text_edit.clear)
        btn_row.addWidget(btn_clear)

        btn_row.addStretch()
        self.lbl_count = QLabel("Detected: 0 URLs", self)
        self.lbl_count.setStyleSheet("font-weight: 600; color: #2563eb;")
        btn_row.addWidget(self.lbl_count)

        layout.addLayout(btn_row)

        self.chk_enabled = QCheckBox("Mark all added URLs as Active (ready to download)", self)
        self.chk_enabled.setChecked(True)
        layout.addWidget(self.chk_enabled)

        # OK / Cancel Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _paste_clipboard(self):
        clipboard = QApplication.clipboard()
        text = clipboard.text()
        if text:
            self.text_edit.appendPlainText(text.strip())

    def _update_url_count(self):
        urls = self.get_entries()
        self.lbl_count.setText(f"Detected: {len(urls)} URLs")

    def get_entries(self) -> List[tuple]:
        """
        Parse text into list of (url, title, filename, enabled).
        """
        raw_text = self.text_edit.toPlainText().strip()
        if not raw_text:
            return []

        results = []
        is_enabled = 1 if self.chk_enabled.isChecked() else 0

        for line in raw_text.splitlines():
            line_clean = line.strip()
            if not line_clean:
                continue

            enabled = is_enabled
            if line_clean.startswith("#"):
                enabled = 0
                line_clean = line_clean.lstrip("#").strip()

            parts = [p.strip() for p in line_clean.split("|")]
            url = parts[0]
            filename = parts[1] if len(parts) > 1 else ""
            title = parts[2] if len(parts) > 2 else ""

            # Check if line looks like a valid web link
            if url.startswith("http://") or url.startswith("https://") or "youtu" in url:
                results.append((url, title, filename, enabled))

        return results

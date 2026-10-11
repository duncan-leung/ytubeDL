"""Add / Edit Channel Subscription Dialog with live content-type validation and metadata resolution."""
import os
from pathlib import Path
from typing import Dict, Optional
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
)

from core.channel_resolver import resolve_channel_metadata, validate_channel_url
from core.config import Config


class ChannelInspectWorker(QThread):
    """Background worker to fetch channel_id and channel title without blocking UI."""

    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, url: str, cookies_file: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.url = url
        self.cookies_file = cookies_file

    def run(self):
        try:
            meta = resolve_channel_metadata(self.url, self.cookies_file)
            self.finished.emit(meta)
        except Exception as e:
            self.error.emit(str(e))


class ChannelDialog(QDialog):
    """Modal dialog for adding or editing a subscribed YouTube channel / playlist."""

    def __init__(
        self,
        config: Config,
        existing_channel: Optional[Dict] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.config = config
        self.existing_channel = existing_channel
        self.inspect_worker: Optional[ChannelInspectWorker] = None

        self.setWindowTitle("Edit Channel" if existing_channel else "Add Channel / Playlist")
        self.setMinimumWidth(560)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        self._setup_ui()
        if existing_channel:
            self._load_existing_channel(existing_channel)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        # === URL Input Section ===
        url_group = QGroupBox("YouTube Content-Type URL", self)
        url_layout = QVBoxLayout(url_group)

        url_input_layout = QHBoxLayout()
        self.txt_url = QLineEdit(self)
        self.txt_url.setPlaceholderText(
            "e.g. https://www.youtube.com/@Channel/videos or .../shorts or playlist URL"
        )
        url_input_layout.addWidget(self.txt_url, stretch=1)

        self.btn_inspect = QPushButton("🔍 Auto-Detect", self)
        self.btn_inspect.setStyleSheet("font-weight: bold; padding: 5px 12px;")
        self.btn_inspect.clicked.connect(self._on_inspect_clicked)
        url_input_layout.addWidget(self.btn_inspect)
        url_layout.addLayout(url_input_layout)

        self.lbl_status = QLabel("", self)
        self.lbl_status.setStyleSheet("color: #64748b; font-size: 11px;")
        url_layout.addWidget(self.lbl_status)
        layout.addWidget(url_group)

        # === Content Type Selector ===
        type_group = QGroupBox("Content Type", self)
        type_layout = QHBoxLayout(type_group)

        self.type_button_group = QButtonGroup(self)
        self.radio_videos = QRadioButton("🎬 Videos", self)
        self.radio_shorts = QRadioButton("⚡ Shorts", self)
        self.radio_playlist = QRadioButton("📑 Playlist", self)

        self.type_button_group.addButton(self.radio_videos)
        self.type_button_group.addButton(self.radio_shorts)
        self.type_button_group.addButton(self.radio_playlist)
        self.radio_videos.setChecked(True)

        type_layout.addWidget(self.radio_videos)
        type_layout.addWidget(self.radio_shorts)
        type_layout.addWidget(self.radio_playlist)
        layout.addWidget(type_group)

        # === Channel Details Section ===
        details_group = QGroupBox("Channel & Folder Settings", self)
        form_layout = QFormLayout(details_group)
        form_layout.setLabelAlignment(Qt.AlignRight)

        self.txt_name = QLineEdit(self)
        self.txt_name.setPlaceholderText("Channel display name")
        form_layout.addRow("Channel Name:", self.txt_name)

        self.txt_channel_id = QLineEdit(self)
        self.txt_channel_id.setPlaceholderText("Auto-extracted (e.g. UC...)")
        form_layout.addRow("Channel ID:", self.txt_channel_id)

        # Output folder
        folder_layout = QHBoxLayout()
        self.txt_folder = QLineEdit(self)
        folder_layout.addWidget(self.txt_folder, stretch=1)
        btn_browse = QPushButton("Browse...", self)
        btn_browse.clicked.connect(self._browse_folder)
        folder_layout.addWidget(btn_browse)
        form_layout.addRow("Save Folder:", folder_layout)

        # Check limit with quick presets
        limit_container = QVBoxLayout()
        limit_input_row = QHBoxLayout()
        self.spin_limit = QSpinBox(self)
        self.spin_limit.setRange(1, 2000)
        self.spin_limit.setValue(20)
        self.spin_limit.setSuffix(" latest clips")
        limit_input_row.addWidget(self.spin_limit, stretch=1)
        limit_container.addLayout(limit_input_row)

        presets_row = QHBoxLayout()
        presets_row.setSpacing(4)
        for label, val in [("5 (Daily)", 5), ("20 (Normal)", 20), ("100 (Catch Up)", 100), ("300 (Deep)", 300)]:
            btn_preset = QPushButton(label, self)
            btn_preset.setStyleSheet("font-size: 11px; padding: 2px 6px;")
            btn_preset.clicked.connect(lambda _, v=val: self.spin_limit.setValue(v))
            presets_row.addWidget(btn_preset)
        limit_container.addLayout(presets_row)

        form_layout.addRow("Check Limit:", limit_container)

        # Quality
        self.combo_quality = QComboBox(self)
        self.combo_quality.addItems(["normal", "best"])
        form_layout.addRow("Quality:", self.combo_quality)

        layout.addWidget(details_group)

        # === Download Options Group ===
        options_group = QGroupBox("Download Options", self)
        options_layout = QVBoxLayout(options_group)

        self.chk_auto_download = QCheckBox(
            "Auto-download new clips when checking (Download Flag)", self
        )
        self.chk_auto_download.setChecked(True)
        options_layout.addWidget(self.chk_auto_download)

        self.chk_numbering = QCheckBox("Prefix filenames with sequence numbering (1. Title.mp4)", self)
        self.chk_numbering.setChecked(False)
        options_layout.addWidget(self.chk_numbering)

        self.chk_thumbnails = QCheckBox("Download video thumbnail images (.jpg)", self)
        self.chk_thumbnails.setChecked(False)
        options_layout.addWidget(self.chk_thumbnails)

        self.chk_use_title = QCheckBox("Use video title in filename (uncheck for video ID only)", self)
        self.chk_use_title.setChecked(True)
        options_layout.addWidget(self.chk_use_title)

        layout.addWidget(options_group)

        # === Dialog Buttons ===
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.btn_cancel = QPushButton("Cancel", self)
        self.btn_cancel.clicked.connect(self.reject)
        button_layout.addWidget(self.btn_cancel)

        self.btn_save = QPushButton("Save Channel", self)
        self.btn_save.setStyleSheet(
            "background-color: #2563eb; color: white; font-weight: bold; padding: 6px 16px; border-radius: 4px;"
        )
        self.btn_save.clicked.connect(self._on_save_clicked)
        button_layout.addWidget(self.btn_save)

        layout.addLayout(button_layout)

    def _load_existing_channel(self, ch: Dict):
        self.txt_url.setText(ch.get("channel_url", ""))
        self.txt_name.setText(ch.get("name", ""))
        self.txt_channel_id.setText(ch.get("channel_id", ""))
        self.txt_folder.setText(ch.get("folder", ""))
        self.spin_limit.setValue(int(ch.get("check_limit", 20)))

        ctype = ch.get("content_type", "videos")
        if ctype == "shorts":
            self.radio_shorts.setChecked(True)
        elif ctype == "playlist":
            self.radio_playlist.setChecked(True)
        else:
            self.radio_videos.setChecked(True)

        quality = ch.get("quality", "normal")
        idx = self.combo_quality.findText(quality)
        if idx >= 0:
            self.combo_quality.setCurrentIndex(idx)

        self.chk_auto_download.setChecked(bool(ch.get("auto_download", 1)))
        self.chk_numbering.setChecked(bool(ch.get("numbering", 0)))
        self.chk_thumbnails.setChecked(bool(ch.get("download_thumbnail", 0)))
        self.chk_use_title.setChecked(bool(ch.get("use_title", 1)))

    def _browse_folder(self):
        curr = self.txt_folder.text().strip() or self.config.output_dir
        chosen = QFileDialog.getExistingDirectory(self, "Select Save Folder", curr)
        if chosen:
            self.txt_folder.setText(chosen)

    def _on_inspect_clicked(self):
        url = self.txt_url.text().strip()
        valid, err_msg, ctype = validate_channel_url(url)
        if not valid:
            QMessageBox.warning(
                self,
                "Content Type URL Required",
                f"{err_msg}\n\n"
                "Examples:\n"
                "• Videos: https://www.youtube.com/@ChannelName/videos\n"
                "• Shorts: https://www.youtube.com/@ChannelName/shorts\n"
                "• Playlist: https://www.youtube.com/playlist?list=PL...\n",
            )
            return

        # Pre-select radio button
        if ctype == "shorts":
            self.radio_shorts.setChecked(True)
        elif ctype == "playlist":
            self.radio_playlist.setChecked(True)
        else:
            self.radio_videos.setChecked(True)

        self.btn_inspect.setEnabled(False)
        self.lbl_status.setText("⏳ Inspecting webpage source for Channel ID & Name...")

        cookie_path = self.config.cookies_file if (self.config.cookies_file and os.path.isfile(self.config.cookies_file)) else None
        self.inspect_worker = ChannelInspectWorker(url, cookie_path, self)
        self.inspect_worker.finished.connect(self._on_inspect_finished)
        self.inspect_worker.error.connect(self._on_inspect_error)
        self.inspect_worker.start()

    def _on_inspect_finished(self, meta: Dict[str, str]):
        self.btn_inspect.setEnabled(True)
        self.lbl_status.setText("✅ Auto-detection complete!")

        if meta.get("name") and not self.txt_name.text().strip():
            self.txt_name.setText(meta["name"])

        if meta.get("channel_id"):
            self.txt_channel_id.setText(meta["channel_id"])

        ctype = meta.get("content_type", "videos")
        if ctype == "shorts":
            self.radio_shorts.setChecked(True)
        elif ctype == "playlist":
            self.radio_playlist.setChecked(True)
        else:
            self.radio_videos.setChecked(True)

        # Suggest folder path if empty
        if not self.txt_folder.text().strip():
            name_part = meta.get("name") or "Channel"
            safe_name = "".join(c if c.isalnum() or c in " ._-" else "_" for c in name_part).strip()
            suggested = os.path.join(self.config.output_dir, safe_name, ctype)
            self.txt_folder.setText(suggested)

    def _on_inspect_error(self, err: str):
        self.btn_inspect.setEnabled(True)
        self.lbl_status.setText(f"⚠️ Inspection warning: {err}")
        QMessageBox.warning(
            self,
            "Inspection Incomplete",
            f"Could not auto-detect channel details:\n{err}\n\nYou can still enter the Name and Save Folder manually.",
        )

    def _get_selected_content_type(self) -> str:
        if self.radio_shorts.isChecked():
            return "shorts"
        if self.radio_playlist.isChecked():
            return "playlist"
        return "videos"

    def _on_save_clicked(self):
        url = self.txt_url.text().strip()
        valid, err_msg, ctype = validate_channel_url(url)
        if not valid:
            QMessageBox.warning(self, "Invalid URL", err_msg)
            return

        name = self.txt_name.text().strip()
        if not name:
            QMessageBox.warning(self, "Missing Name", "Please enter a Channel Name.")
            self.txt_name.setFocus()
            return

        folder = self.txt_folder.text().strip()
        if not folder:
            name_part = "".join(c if c.isalnum() or c in " ._-" else "_" for c in name).strip()
            folder = os.path.join(self.config.output_dir, name_part, self._get_selected_content_type())
            self.txt_folder.setText(folder)

        self.accept()

    def get_channel_data(self) -> Dict:
        """Return clean channel dictionary ready for database insertion/update."""
        return {
            "name": self.txt_name.text().strip(),
            "channel_id": self.txt_channel_id.text().strip() or "UNKNOWN",
            "channel_url": self.txt_url.text().strip(),
            "content_type": self._get_selected_content_type(),
            "folder": self.txt_folder.text().strip(),
            "check_limit": self.spin_limit.value(),
            "quality": self.combo_quality.currentText(),
            "auto_download": 1 if self.chk_auto_download.isChecked() else 0,
            "numbering": 1 if self.chk_numbering.isChecked() else 0,
            "download_thumbnail": 1 if self.chk_thumbnails.isChecked() else 0,
            "use_title": 1 if self.chk_use_title.isChecked() else 0,
            "enabled": 1,
        }

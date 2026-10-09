"""Settings and yt-dlp management dialog for ytubeDL."""
import sys
from pathlib import Path
from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from core.bin_helper import (
    check_latest_ytdlp_release,
    download_ytdlp_binary,
    find_ffmpeg,
    find_ytdlp,
    get_ytdlp_version,
)
from core.config import Config


class UpdateWorker(QThread):
    """Background worker to download yt-dlp without freezing UI."""

    progress = pyqtSignal(int, int)
    finished = pyqtSignal(bool, str)

    def run(self):
        def cb(cur, total):
            self.progress.emit(cur, total)

        success, msg = download_ytdlp_binary(cb)
        self.finished.emit(success, msg)


class SettingsDialog(QDialog):
    """Application settings and binary management."""

    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Settings - ytubeDL")
        self.setMinimumSize(580, 520)
        self._init_ui()
        self._load_values()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 1. Output & Download pacing
        grp_download = QGroupBox("General Download Options", self)
        l_down = QVBoxLayout(grp_download)

        # Output Dir
        l_down.addWidget(QLabel("Default Output Directory:"))
        h_out = QHBoxLayout()
        self.txt_out = QLineEdit(self)
        self.btn_browse = QPushButton("Browse...", self)
        self.btn_browse.clicked.connect(self._browse_output)
        h_out.addWidget(self.txt_out)
        h_out.addWidget(self.btn_browse)
        l_down.addLayout(h_out)

        # Delay
        h_delay = QHBoxLayout()
        h_delay.addWidget(QLabel("Delay between downloads (seconds):"))
        self.spin_delay = QSpinBox(self)
        self.spin_delay.setRange(0, 300)
        self.spin_delay.setValue(8)
        h_delay.addWidget(self.spin_delay)
        h_delay.addStretch()
        l_down.addLayout(h_delay)

        layout.addWidget(grp_download)

        # 2. Authentication & Extraction (from download_urls.py)
        grp_auth = QGroupBox("Cookies & Extraction (Reliability)", self)
        l_auth = QVBoxLayout(grp_auth)

        h_browser = QHBoxLayout()
        h_browser.addWidget(QLabel("Extract cookies from browser:"))
        self.cmb_browser = QComboBox(self)
        self.cmb_browser.addItems(["firefox", "chrome", "edge", "safari", "brave", "none"])
        h_browser.addWidget(self.cmb_browser)
        h_browser.addStretch()
        l_auth.addLayout(h_browser)

        # Cookie file
        l_auth.addWidget(QLabel("Or custom cookies.txt file (optional):"))
        h_cfile = QHBoxLayout()
        self.txt_cookie_file = QLineEdit(self)
        btn_cfile = QPushButton("Select File...", self)
        btn_cfile.clicked.connect(self._browse_cookie_file)
        h_cfile.addWidget(self.txt_cookie_file)
        h_cfile.addWidget(btn_cfile)
        l_auth.addLayout(h_cfile)

        # JS Runtime
        self.chk_js = QCheckBox("Enable Node.js signature challenge solver (--js-runtimes node)", self)
        l_auth.addWidget(self.chk_js)

        layout.addWidget(grp_auth)

        # 3. yt-dlp & FFmpeg status and updater
        grp_bin = QGroupBox("Engine & Binary Status (yt-dlp / FFmpeg)", self)
        l_bin = QVBoxLayout(grp_bin)

        self.lbl_ytdlp_ver = QLabel("yt-dlp: Checking...", self)
        self.lbl_ytdlp_ver.setStyleSheet("font-weight: 600; color: #1e293b;")
        l_bin.addWidget(self.lbl_ytdlp_ver)

        self.lbl_ffmpeg = QLabel("FFmpeg: Checking...", self)
        l_bin.addWidget(self.lbl_ffmpeg)

        # Update button & progress
        h_upd = QHBoxLayout()
        self.btn_check_upd = QPushButton("🔍 Check for Updates", self)
        self.btn_check_upd.clicked.connect(self._check_ytdlp_update)
        self.btn_upd_now = QPushButton("🔄 Update / Install yt-dlp Now", self)
        self.btn_upd_now.clicked.connect(self._start_ytdlp_update)
        h_upd.addWidget(self.btn_check_upd)
        h_upd.addWidget(self.btn_upd_now)
        h_upd.addStretch()
        l_bin.addLayout(h_upd)

        self.upd_progress = QProgressBar(self)
        self.upd_progress.setVisible(False)
        l_bin.addWidget(self.upd_progress)

        layout.addWidget(grp_bin)

        # Dialog Buttons
        btn_box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel, self)
        btn_box.accepted.connect(self._save_values)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        # Trigger binary checks
        self._refresh_bin_labels()

    def _browse_output(self):
        chosen = QFileDialog.getExistingDirectory(self, "Select Download Folder", self.txt_out.text())
        if chosen:
            self.txt_out.setText(chosen)

    def _browse_cookie_file(self):
        f, _ = QFileDialog.getOpenFileName(self, "Select Cookies File", "", "Text Files (*.txt);;All Files (*)")
        if f:
            self.txt_cookie_file.setText(f)

    def _load_values(self):
        self.txt_out.setText(self.config.output_dir)
        self.spin_delay.setValue(self.config.delay_between)

        browser = self.config.cookies_from_browser.lower()
        idx = self.cmb_browser.findText(browser)
        if idx >= 0:
            self.cmb_browser.setCurrentIndex(idx)

        self.txt_cookie_file.setText(self.config.cookies_file)
        self.chk_js.setChecked(self.config.enable_js_runtime)

    def _save_values(self):
        self.config.output_dir = self.txt_out.text().strip()
        self.config.delay_between = self.spin_delay.value()
        self.config.cookies_from_browser = self.cmb_browser.currentText()
        self.config.cookies_file = self.txt_cookie_file.text().strip()
        self.config.enable_js_runtime = self.chk_js.isChecked()
        self.accept()

    def _refresh_bin_labels(self):
        ytdlp_bin = find_ytdlp(self.config.get("custom_ytdlp_path"))
        if ytdlp_bin:
            ver = get_ytdlp_version(ytdlp_bin) or "Unknown version"
            self.lbl_ytdlp_ver.setText(f"yt-dlp: ✅ Active ({ver})\nPath: {ytdlp_bin}")
        else:
            self.lbl_ytdlp_ver.setText("yt-dlp: ❌ Not Found! Click 'Update / Install yt-dlp Now' below.")

        ffmpeg_bin = find_ffmpeg(self.config.get("custom_ffmpeg_path"))
        if ffmpeg_bin:
            self.lbl_ffmpeg.setText(f"FFmpeg: ✅ Found at {ffmpeg_bin}")
        else:
            self.lbl_ffmpeg.setText("FFmpeg: ⚠️ Not detected (needed for merging 1080p+ streams).")

    def _check_ytdlp_update(self):
        self.btn_check_upd.setEnabled(False)
        self.btn_check_upd.setText("Checking...")
        try:
            rel = check_latest_ytdlp_release()
            cur_ver = get_ytdlp_version()
            if rel:
                latest = rel.get("version", "")
                if cur_ver and cur_ver >= latest:
                    QMessageBox.information(self, "yt-dlp Up to Date", f"You have the latest version: {cur_ver}")
                else:
                    QMessageBox.information(
                        self,
                        "Update Available",
                        f"Current version: {cur_ver or 'None'}\nLatest version: {latest}\n\nClick 'Update / Install yt-dlp Now' to update automatically.",
                    )
            else:
                QMessageBox.warning(self, "Check Failed", "Could not query GitHub for latest release.")
        finally:
            self.btn_check_upd.setEnabled(True)
            self.btn_check_upd.setText("🔍 Check for Updates")

    def _start_ytdlp_update(self):
        self.btn_upd_now.setEnabled(False)
        self.upd_progress.setVisible(True)
        self.upd_progress.setRange(0, 100)
        self.upd_progress.setValue(0)

        self._upd_worker = UpdateWorker(self)
        self._upd_worker.progress.connect(self._on_update_progress)
        self._upd_worker.finished.connect(self._on_update_finished)
        self._upd_worker.start()

    def _on_update_progress(self, current: int, total: int):
        if total > 0:
            percent = int((current / total) * 100)
            self.upd_progress.setValue(percent)

    def _on_update_finished(self, success: bool, msg: str):
        self.btn_upd_now.setEnabled(True)
        self.upd_progress.setVisible(False)
        if success:
            QMessageBox.information(self, "Update Complete", msg)
            self._refresh_bin_labels()
        else:
            QMessageBox.critical(self, "Update Failed", msg)

"""Main application window for ytubeDL."""
import os
import subprocess
import sys
from pathlib import Path
from PyQt5.QtCore import QModelIndex, Qt, QUrl
from PyQt5.QtGui import QDesktopServices, QIcon, QKeySequence
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QShortcut,
    QSplitter,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.bin_helper import (
    find_ffmpeg,
    find_ytdlp,
    get_ffmpeg_version,
    get_ytdlp_version,
)
from core.config import Config
from core.converter import ConvertWorker, FFmpegDownloadWorker
from core.db import Database
from core.downloader import DownloadWorker
from ui.batch_dialog import BatchDialog
from ui.channel_view import ChannelView
from ui.settings_dialog import SettingsDialog
from ui.styles import MODERN_STYLE
from ui.table_model import DownloadTableModel


class MainWindow(QMainWindow):
    """Primary desktop interface for ytubeDL."""

    def __init__(self, db: Database, config: Config):
        super().__init__()
        self.db = db
        self.config = config
        self.worker: Optional[DownloadWorker] = None
        self.convert_worker: Optional[ConvertWorker] = None
        self.ffmpeg_download_worker: Optional[FFmpegDownloadWorker] = None

        self.current_filter = "all"
        self.search_query = ""

        self.setWindowTitle("ytubeDL - YouTube Video Downloader")
        self.resize(1100, 750)
        self.setStyleSheet(MODERN_STYLE)

        self._init_menu()
        self._init_ui()
        self._setup_shortcuts()
        self.refresh_table()

    def _init_menu(self):
        menubar = self.menuBar()

        # File Menu
        menu_file = menubar.addMenu("&File")

        act_import = QAction("Import from &url.txt...", self)
        act_import.triggered.connect(self._import_url_txt)
        menu_file.addAction(act_import)

        act_export = QAction("Export to url.&txt...", self)
        act_export.triggered.connect(self._export_url_txt)
        menu_file.addAction(act_export)

        menu_file.addSeparator()

        act_settings = QAction("&Settings...", self)
        act_settings.setShortcut("Ctrl+,")
        act_settings.triggered.connect(self._open_settings)
        menu_file.addAction(act_settings)

        menu_file.addSeparator()
        act_exit = QAction("E&xit", self)
        act_exit.setShortcut("Ctrl+Q")
        act_exit.triggered.connect(self.close)
        menu_file.addAction(act_exit)

        # Queue Menu
        menu_queue = menubar.addMenu("&Queue")

        act_enable_all = QAction("Enable All Items", self)
        act_enable_all.triggered.connect(self._enable_all)
        menu_queue.addAction(act_enable_all)

        act_disable_all = QAction("Disable All Items", self)
        act_disable_all.triggered.connect(self._disable_all)
        menu_queue.addAction(act_disable_all)

        menu_queue.addSeparator()
        act_clear_comp = QAction("Clear Completed Items", self)
        act_clear_comp.triggered.connect(self._clear_completed)
        menu_queue.addAction(act_clear_comp)

        # Channels Menu
        menu_channels = menubar.addMenu("&Channels")
        act_add_ch = QAction("Add &New Channel...", self)
        act_add_ch.triggered.connect(self._open_add_channel_action)
        menu_channels.addAction(act_add_ch)

        act_sync_all = QAction("&Check All Channels", self)
        act_sync_all.triggered.connect(self._sync_all_channels_action)
        menu_channels.addAction(act_sync_all)

        menu_channels.addSeparator()
        act_unfold = QAction("&Unfold All Channels", self)
        act_unfold.triggered.connect(lambda: self.channel_view._unfold_all())
        menu_channels.addAction(act_unfold)

        act_fold = QAction("&Fold All Channels", self)
        act_fold.triggered.connect(lambda: self.channel_view._fold_all())
        menu_channels.addAction(act_fold)

        # Help Menu
        menu_help = menubar.addMenu("&Help")
        act_check_upd = QAction("Check for yt-dlp &Updates...", self)
        act_check_upd.triggered.connect(self._open_settings)
        menu_help.addAction(act_check_upd)

        act_about = QAction("&About ytubeDL", self)
        act_about.triggered.connect(self._show_about)
        menu_help.addAction(act_about)

    def _init_ui(self):
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(12, 10, 12, 10)
        main_layout.setSpacing(8)

        # === TABBED LAYOUT ===
        self.tab_widget = QTabWidget(self)
        self.tab_widget.setStyleSheet(
            """
            QTabWidget::pane {
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                background-color: #ffffff;
            }
            QTabBar::tab {
                background: #f1f5f9;
                color: #475569;
                font-weight: 600;
                font-size: 13px;
                padding: 7px 18px;
                margin-right: 4px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
            }
            QTabBar::tab:selected {
                background: #ffffff;
                color: #1e293b;
                border-bottom: 2px solid #2563eb;
            }
            """
        )

        # === TAB 1: DIRECT DOWNLOADS ===
        tab_direct = QWidget()
        direct_layout = QVBoxLayout(tab_direct)
        direct_layout.setContentsMargins(10, 10, 10, 10)
        direct_layout.setSpacing(10)

        # 1. Top URL Entry Bar
        top_bar = QHBoxLayout()

        self.txt_url_input = QLineEdit(self)
        self.txt_url_input.setPlaceholderText(
            "Paste or type YouTube URL here... (e.g., https://www.youtube.com/watch?v=...)"
        )
        self.txt_url_input.returnPressed.connect(self._add_single_url)
        top_bar.addWidget(self.txt_url_input, stretch=1)

        self.btn_add = QPushButton("➕ Add URL", self)
        self.btn_add.setObjectName("btn_add")
        self.btn_add.clicked.connect(self._add_single_url)
        top_bar.addWidget(self.btn_add)

        self.btn_batch = QPushButton("📋 Batch Paste...", self)
        self.btn_batch.clicked.connect(self._open_batch_dialog)
        top_bar.addWidget(self.btn_batch)

        direct_layout.addLayout(top_bar)

        # 2. Output Folder & Controls Bar
        control_bar = QHBoxLayout()

        lbl_out = QLabel("Output Folder:", self)
        lbl_out.setStyleSheet("font-weight: 500; color: #475569;")
        control_bar.addWidget(lbl_out)

        self.txt_output_path = QLineEdit(self)
        self.txt_output_path.setText(self.config.output_dir)
        self.txt_output_path.setReadOnly(True)
        control_bar.addWidget(self.txt_output_path, stretch=1)

        btn_browse = QPushButton("📁 Browse...", self)
        btn_browse.clicked.connect(self._browse_output_dir)
        control_bar.addWidget(btn_browse)

        btn_open_folder = QPushButton("📂 Open", self)
        btn_open_folder.setToolTip("Open output directory in file browser")
        btn_open_folder.clicked.connect(self._open_output_folder)
        control_bar.addWidget(btn_open_folder)

        control_bar.addSpacing(16)

        # Run / Stop / Convert Buttons
        self.btn_run = QPushButton("▶ Run Downloads", self)
        self.btn_run.setObjectName("btn_run")
        self.btn_run.setMinimumWidth(130)
        self.btn_run.clicked.connect(self._start_download_queue)
        control_bar.addWidget(self.btn_run)

        self.btn_stop = QPushButton("⏹ Stop", self)
        self.btn_stop.setObjectName("btn_stop")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop_current_task)
        control_bar.addWidget(self.btn_stop)

        control_bar.addSpacing(8)

        self.btn_convert_mp3 = QPushButton("🎵 Convert to MP3", self)
        self.btn_convert_mp3.setObjectName("btn_convert_mp3")
        self.btn_convert_mp3.setToolTip("Convert selected completed download row(s) to MP3 with same filename")
        self.btn_convert_mp3.clicked.connect(self._convert_selected_to_mp3)
        control_bar.addWidget(self.btn_convert_mp3)

        direct_layout.addLayout(control_bar)

        # 3. Filter & Search Bar
        filter_bar = QHBoxLayout()

        self.btn_filter_all = QPushButton("All", self)
        self.btn_filter_pending = QPushButton("Pending", self)
        self.btn_filter_completed = QPushButton("Completed", self)
        self.btn_filter_disabled = QPushButton("Disabled", self)

        self.filter_buttons = {
            "all": self.btn_filter_all,
            "pending": self.btn_filter_pending,
            "completed": self.btn_filter_completed,
            "disabled": self.btn_filter_disabled,
        }

        for key, btn in self.filter_buttons.items():
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, k=key: self._set_filter(k))
            filter_bar.addWidget(btn)

        self.btn_filter_all.setChecked(True)
        filter_bar.addStretch()

        self.txt_search = QLineEdit(self)
        self.txt_search.setPlaceholderText("🔍 Search title, filename or URL...")
        self.txt_search.setMaximumWidth(280)
        self.txt_search.textChanged.connect(self._on_search_changed)
        filter_bar.addWidget(self.txt_search)

        btn_refresh = QPushButton("🔄 Refresh", self)
        btn_refresh.clicked.connect(self.refresh_table)
        filter_bar.addWidget(btn_refresh)

        direct_layout.addLayout(filter_bar)

        # 4. Central Table View
        self.model = DownloadTableModel(self.db, self)
        self.table_view = QTableView(self)
        self.table_view.setModel(self.model)
        self.table_view.setAlternatingRowColors(True)
        self.table_view.setSelectionBehavior(QTableView.SelectRows)
        self.table_view.setSelectionMode(QTableView.ExtendedSelection)
        self.table_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table_view.customContextMenuRequested.connect(self._show_context_menu)

        header = self.table_view.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        self.table_view.setColumnWidth(0, 60)
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        self.table_view.setColumnWidth(1, 120)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.Interactive)
        self.table_view.setColumnWidth(3, 220)
        header.setSectionResizeMode(4, QHeaderView.Interactive)
        self.table_view.setColumnWidth(4, 260)

        direct_layout.addWidget(self.table_view, stretch=1)

        self.tab_widget.addTab(tab_direct, "📥 Direct Downloads")

        # === TAB 2: CHANNELS & FEEDS ===
        self.channel_view = ChannelView(self.db, self.config, self)
        self.channel_view.log_message.connect(self._append_log)
        self.channel_view.status_changed.connect(lambda msg: self.statusBar().showMessage(msg, 5000))
        self.tab_widget.addTab(self.channel_view, "📺 Channels & Feeds")

        main_layout.addWidget(self.tab_widget, stretch=1)

        # === 5. PROGRESS & LIVE LOG DRAWER ===
        prog_bar_layout = QHBoxLayout()
        self.lbl_batch_status = QLabel("Ready", self)
        self.lbl_batch_status.setStyleSheet("font-weight: 500; color: #475569;")
        prog_bar_layout.addWidget(self.lbl_batch_status)

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setValue(0)
        prog_bar_layout.addWidget(self.progress_bar, stretch=1)

        self.btn_toggle_logs = QPushButton("▼ Show Logs", self)
        self.btn_toggle_logs.clicked.connect(self._toggle_logs)
        prog_bar_layout.addWidget(self.btn_toggle_logs)

        main_layout.addLayout(prog_bar_layout)

        # Collapsible log drawer
        self.log_viewer = QPlainTextEdit(self)
        self.log_viewer.setObjectName("log_viewer")
        self.log_viewer.setReadOnly(True)
        self.log_viewer.setMaximumHeight(140)
        self.log_viewer.setVisible(False)
        main_layout.addWidget(self.log_viewer)

        # Status Bar
        self.statusBar().showMessage("Ready")

    def _setup_shortcuts(self):
        # Delete selected
        shortcut_del = QShortcut(QKeySequence.Delete, self.table_view)
        shortcut_del.activated.connect(self._delete_selected)

        # Space toggles enable/disable
        shortcut_space = QShortcut(QKeySequence(Qt.Key_Space), self.table_view)
        shortcut_space.activated.connect(self._toggle_selected_enabled)

    # === ACTIONS & SLOTS ===

    def refresh_table(self):
        """Fetch rows from SQLite and update virtual model without lag."""
        rows = self.db.get_all(filter_status=self.current_filter, search=self.search_query)
        self.model.set_items(rows)
        self._update_filter_button_counts()

    def _update_filter_button_counts(self):
        stats = self.db.get_stats()
        self.btn_filter_all.setText(f"All ({stats['total']})")
        self.btn_filter_pending.setText(f"Pending ({stats['pending']})")
        self.btn_filter_completed.setText(f"Completed ({stats['completed']})")
        self.btn_filter_disabled.setText(f"Disabled ({stats['disabled']})")

    def _set_filter(self, filter_key: str):
        self.current_filter = filter_key
        for k, btn in self.filter_buttons.items():
            btn.setChecked(k == filter_key)
        self.refresh_table()

    def _on_search_changed(self, text: str):
        self.search_query = text.strip()
        self.refresh_table()

    def _open_add_channel_action(self):
        self.tab_widget.setCurrentWidget(self.channel_view)
        self.channel_view._open_add_channel_dialog()

    def _sync_all_channels_action(self):
        self.tab_widget.setCurrentWidget(self.channel_view)
        self.channel_view._start_channel_sync()

    def _add_single_url(self):
        url = self.txt_url_input.text().strip()
        if not url:
            return

        added_id = self.db.add_url(url)
        if added_id:
            self.txt_url_input.clear()
            self.refresh_table()
            self.statusBar().showMessage(f"Added URL to queue: {url}", 4000)
        else:
            QMessageBox.information(self, "Duplicate URL", "This URL already exists in your library.")

    def _open_batch_dialog(self):
        dlg = BatchDialog(self)
        if dlg.exec_() == BatchDialog.Accepted:
            entries = dlg.get_entries()
            if entries:
                added = self.db.add_urls_batch(entries)
                self.refresh_table()
                self.statusBar().showMessage(
                    f"Batch complete: Added {added} new URLs ({len(entries) - added} skipped)", 5000
                )

    def _browse_output_dir(self):
        chosen = QFileDialog.getExistingDirectory(
            self, "Select Output Directory", self.config.output_dir
        )
        if chosen:
            self.config.output_dir = chosen
            self.txt_output_path.setText(chosen)

    def _open_output_folder(self):
        path = self.config.output_dir
        if sys.platform == "darwin":
            subprocess.run(["open", path])
        elif sys.platform == "win32":
            subprocess.run(["explorer", os.path.normpath(path)])
        else:
            subprocess.run(["xdg-open", path])

    def _open_settings(self):
        dlg = SettingsDialog(self.config, self)
        if dlg.exec_() == SettingsDialog.Accepted:
            self.txt_output_path.setText(self.config.output_dir)

    def _toggle_logs(self):
        vis = not self.log_viewer.isVisible()
        self.log_viewer.setVisible(vis)
        self.btn_toggle_logs.setText("▲ Hide Logs" if vis else "▼ Show Logs")

    # === DOWNLOAD QUEUE CONTROL ===

    def _start_download_queue(self, target_ids=None):
        if self.worker and self.worker.isRunning():
            return

        # Ensure yt-dlp is available
        ytdlp_bin = find_ytdlp(self.config.get("custom_ytdlp_path"))
        if not ytdlp_bin:
            reply = QMessageBox.question(
                self,
                "yt-dlp Missing",
                "yt-dlp was not found on your system.\n\n"
                "Would you like ytubeDL to download and install the official standalone binary now?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                self._open_settings()
            return

        self.btn_run.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.progress_bar.setValue(0)
        self.lbl_batch_status.setText("Starting downloads...")

        self.worker = DownloadWorker(self.db, self.config, target_item_ids=target_ids, parent=self)
        self.worker.log_message.connect(self._append_log)
        self.worker.item_started.connect(self._on_item_started)
        self.worker.item_progress.connect(self._on_item_progress)
        self.worker.item_finished.connect(self._on_item_finished)
        self.worker.item_error.connect(self._on_item_error)
        self.worker.overall_progress.connect(self._on_overall_progress)
        self.worker.queue_finished.connect(self._on_queue_finished)
        self.worker.start()

    def _stop_current_task(self):
        """Stop whatever task is currently running (download or conversion)."""
        if self.worker and self.worker.isRunning():
            self.lbl_batch_status.setText("Stopping download queue...")
            self.worker.stop()
            self.btn_stop.setEnabled(False)
        if self.convert_worker and self.convert_worker.isRunning():
            self.lbl_batch_status.setText("Stopping MP3 conversion...")
            self.convert_worker.stop()
            self.btn_stop.setEnabled(False)

    def _stop_download_queue(self):
        self._stop_current_task()

    def _append_log(self, text: str):
        self.log_viewer.appendPlainText(text)
        sb = self.log_viewer.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_item_started(self, item_id: int, url: str):
        self.model.update_item_status(item_id, "downloading")
        self.lbl_batch_status.setText(f"Downloading: {url}")
        self.statusBar().showMessage(f"Downloading: {url}")

    def _on_item_progress(self, item_id: int, progress_text: str):
        self.lbl_batch_status.setText(f"Progress: {progress_text}")

    def _on_item_finished(self, item_id: int, filename: str, title: str):
        self.model.update_item_result(item_id, filename, title, "completed")
        self._update_filter_button_counts()

    def _on_item_error(self, item_id: int, error_text: str):
        self.model.update_item_status(item_id, "error", error_text)
        self._update_filter_button_counts()

    def _on_overall_progress(self, current: int, total: int):
        if total > 0:
            percent = int((current / total) * 100)
            self.progress_bar.setValue(percent)
            self.progress_bar.setFormat(f"{current} / {total} ({percent}%)")

    def _on_queue_finished(self, downloaded_count: int):
        self.btn_run.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.lbl_batch_status.setText(f"Done. {downloaded_count} video(s) downloaded.")
        self.statusBar().showMessage(f"Queue complete. Downloaded: {downloaded_count}", 6000)
        self.refresh_table()

    # === CONTEXT MENU & TABLE ACTIONS ===

    def _get_selected_items(self):
        selected_indexes = self.table_view.selectionModel().selectedRows()
        items = []
        for idx in selected_indexes:
            item = self.model.get_item(idx.row())
            if item:
                items.append(item)
        return items

    def _show_context_menu(self, pos):
        items = self._get_selected_items()
        if not items:
            return

        menu = QMenu(self)

        act_run_single = menu.addAction("▶ Download Selected Now")
        act_run_single.triggered.connect(lambda: self._download_specific(items))

        act_convert = menu.addAction("🎵 Convert Selected to MP3")
        act_convert.triggered.connect(self._convert_selected_to_mp3)

        menu.addSeparator()

        # If single item selected with a filename, provide file open
        if len(items) == 1 and items[0].get("filename"):
            act_reveal = menu.addAction("🎬 Reveal Video File")
            act_reveal.triggered.connect(lambda: self._reveal_file(items[0]))

            mp3_path = Path(self.config.output_dir) / f"{Path(items[0]['filename']).stem}.mp3"
            if mp3_path.is_file():
                act_reveal_mp3 = menu.addAction("🎵 Reveal MP3 File")
                act_reveal_mp3.triggered.connect(lambda: self._reveal_file_path(mp3_path))

        act_browser = menu.addAction("🌐 Open in Web Browser")
        act_browser.triggered.connect(lambda: self._open_in_browser(items[0]))

        act_copy = menu.addAction("📋 Copy URL")
        act_copy.triggered.connect(lambda: self._copy_url(items[0]))

        menu.addSeparator()

        act_toggle = menu.addAction("⏸ Toggle Active / Disabled")
        act_toggle.triggered.connect(self._toggle_selected_enabled)

        act_reset = menu.addAction("🔄 Reset Status to Pending")
        act_reset.triggered.connect(self._reset_selected_status)

        act_del = menu.addAction("🗑 Delete Selected")
        act_del.triggered.connect(self._delete_selected)

        menu.exec_(self.table_view.viewport().mapToGlobal(pos))

    def _download_specific(self, items):
        ids = [i["id"] for i in items]
        self._start_download_queue(target_ids=ids)

    def _reveal_file(self, item):
        filename = item.get("filename")
        if not filename:
            return
        filepath = Path(self.config.output_dir) / filename
        self._reveal_file_path(filepath)

    def _reveal_file_path(self, filepath: Path):
        if not filepath.exists():
            QMessageBox.warning(self, "File Not Found", f"File does not exist:\n{filepath}")
            return

        if sys.platform == "darwin":
            subprocess.run(["open", "-R", str(filepath)])
        elif sys.platform == "win32":
            subprocess.run(["explorer", f"/select,{os.path.normpath(str(filepath))}"])
        else:
            subprocess.run(["xdg-open", str(filepath.parent)])

    # === MP3 AUDIO CONVERSION ===

    def _convert_selected_to_mp3(self):
        """Convert selected completed download row(s) to MP3 with the same filename."""
        if (self.worker and self.worker.isRunning()) or (
            self.convert_worker and self.convert_worker.isRunning()
        ):
            QMessageBox.information(
                self, "Task In Progress", "Please wait for current operation to finish or stop it first."
            )
            return

        selected_items = self._get_selected_items()
        if not selected_items:
            QMessageBox.information(
                self,
                "No Rows Selected",
                "Please select one or more completed download row(s) in the table to convert to MP3.",
            )
            return

        out_dir = Path(self.config.output_dir)
        valid_items = []
        missing_files = []

        for item in selected_items:
            fn = (item.get("filename") or "").strip()
            if not fn:
                missing_files.append(f"{item.get('title') or item.get('url')} (not downloaded)")
                continue
            src_path = out_dir / fn
            if not src_path.is_file():
                missing_files.append(f"{fn} (file not found on disk)")
                continue
            valid_items.append(item)

        if not valid_items:
            QMessageBox.warning(
                self,
                "Cannot Convert",
                "None of the selected items have completed video files on disk.\n\n"
                "Please make sure the videos are downloaded before converting.",
            )
            return

        if missing_files and len(missing_files) < len(selected_items):
            msg = (
                f"{len(missing_files)} of {len(selected_items)} selected item(s) are missing or incomplete:\n"
                + "\n".join(f" • {m}" for m in missing_files[:5])
            )
            if len(missing_files) > 5:
                msg += f"\n • ...and {len(missing_files) - 5} more"
            msg += f"\n\nDo you want to proceed converting the remaining {len(valid_items)} item(s)?"

            reply = QMessageBox.question(
                self, "Some Files Incomplete", msg, QMessageBox.Yes | QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                return

        # Check for FFmpeg executable
        ffmpeg_bin = find_ffmpeg(self.config.get("custom_ffmpeg_path"))
        if not ffmpeg_bin:
            reply = QMessageBox.question(
                self,
                "FFmpeg Required",
                "FFmpeg is required to convert MP4 to MP3, but it was not detected on this machine.\n\n"
                "Would you like ytubeDL to download and configure standalone FFmpeg automatically now?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                self._install_ffmpeg_and_convert(valid_items)
            else:
                QMessageBox.information(
                    self,
                    "FFmpeg Setup",
                    "To enable MP3 conversion:\n\n"
                    "1. Open Settings (Ctrl+,)\n"
                    "2. Click 'Install / Download FFmpeg Now' or browse to your ffmpeg.exe.\n\n"
                    "Alternatively, place ffmpeg.exe in the same folder as ytubeDL.",
                )
            return

        self._start_conversion(valid_items)

    def _install_ffmpeg_and_convert(self, pending_items):
        """Download and install FFmpeg with progress display, then proceed with conversion."""
        self.btn_run.setEnabled(False)
        self.btn_convert_mp3.setEnabled(False)
        self.btn_stop.setEnabled(False)
        self.progress_bar.setValue(0)
        self.lbl_batch_status.setText("Downloading standalone FFmpeg engine...")
        self.statusBar().showMessage("Downloading FFmpeg binary...")

        self._append_log("🔄 Downloading official standalone FFmpeg binary...")

        self.ffmpeg_download_worker = FFmpegDownloadWorker(self)

        def on_prog(cur, total):
            if total > 0:
                pct = int((cur / total) * 100)
                self.progress_bar.setValue(pct)
                self.progress_bar.setFormat(f"Downloading FFmpeg: {pct}%")

        def on_finish(success, msg):
            self.btn_run.setEnabled(True)
            self.btn_convert_mp3.setEnabled(True)
            self.progress_bar.setValue(0)
            self.progress_bar.setFormat("%p%")

            if success:
                self._append_log(f"✅ {msg}")
                self.statusBar().showMessage(msg, 5000)
                QMessageBox.information(
                    self, "FFmpeg Installed", f"{msg}\n\nProceeding with MP3 conversion..."
                )
                self._start_conversion(pending_items)
            else:
                self._append_log(f"❌ {msg}")
                self.lbl_batch_status.setText("FFmpeg installation failed.")
                QMessageBox.critical(
                    self,
                    "FFmpeg Download Failed",
                    f"{msg}\n\nPlease install FFmpeg or set its path in Settings (Ctrl+,).",
                )

        self.ffmpeg_download_worker.progress.connect(on_prog)
        self.ffmpeg_download_worker.finished.connect(on_finish)
        self.ffmpeg_download_worker.start()

    def _start_conversion(self, valid_items):
        """Launch ConvertWorker in the background."""
        self.btn_run.setEnabled(False)
        self.btn_convert_mp3.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.progress_bar.setValue(0)
        self.lbl_batch_status.setText(f"Converting {len(valid_items)} item(s) to MP3...")

        self.convert_worker = ConvertWorker(
            valid_items, Path(self.config.output_dir), self.config, self
        )
        self.convert_worker.log_message.connect(self._append_log)
        self.convert_worker.item_started.connect(self._on_convert_item_started)
        self.convert_worker.item_progress.connect(self._on_convert_item_progress)
        self.convert_worker.overall_progress.connect(self._on_overall_progress)
        self.convert_worker.all_finished.connect(self._on_convert_all_finished)
        self.convert_worker.start()

    def _on_convert_item_started(self, item_id: int, filename: str):
        self.lbl_batch_status.setText(f"Converting: {filename}")
        self.statusBar().showMessage(f"Converting to MP3: {filename}")

    def _on_convert_item_progress(self, item_id: int, progress_text: str):
        self.lbl_batch_status.setText(f"Converting: {progress_text}")

    def _on_convert_all_finished(
        self, success_count: int, error_count: int, converted_files: list
    ):
        self.btn_run.setEnabled(True)
        self.btn_convert_mp3.setEnabled(True)
        self.btn_stop.setEnabled(False)

        status_text = f"Finished conversion: {success_count} succeeded, {error_count} failed."
        self.lbl_batch_status.setText(status_text)
        self.statusBar().showMessage(status_text, 6000)

        if success_count > 0:
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("Conversion Complete")
            msg_box.setIcon(QMessageBox.Information)
            msg_box.setText(f"Successfully converted {success_count} file(s) to MP3!")
            msg_box.setInformativeText(f"Saved with the same filename in:\n{self.config.output_dir}")
            btn_open = msg_box.addButton("📂 Open Output Folder", QMessageBox.ActionRole)
            msg_box.addButton(QMessageBox.Ok)
            msg_box.exec_()

            if msg_box.clickedButton() == btn_open:
                self._open_output_folder()
        elif error_count > 0:
            QMessageBox.warning(
                self,
                "Conversion Incomplete",
                f"Conversion failed for {error_count} item(s).\nPlease check the log viewer for details.",
            )

    def _open_in_browser(self, item):
        url = item.get("url")
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def _copy_url(self, item):
        url = item.get("url")
        if url:
            QApplication.clipboard().setText(url)
            self.statusBar().showMessage(f"Copied to clipboard: {url}", 3000)

    def _toggle_selected_enabled(self):
        items = self._get_selected_items()
        for item in items:
            self.db.toggle_enabled(item["id"])
        self.refresh_table()

    def _reset_selected_status(self):
        items = self._get_selected_items()
        for item in items:
            self.db.update_status(item["id"], "pending")
        self.refresh_table()

    def _delete_selected(self):
        items = self._get_selected_items()
        if not items:
            return
        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Are you sure you want to delete {len(items)} selected item(s) from the library?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            ids = [i["id"] for i in items]
            self.db.delete_items(ids)
            self.refresh_table()

    def _enable_all(self):
        for item in self.db.get_all():
            self.db.update_row(item["id"], enabled=1)
        self.refresh_table()

    def _disable_all(self):
        for item in self.db.get_all():
            self.db.update_row(item["id"], enabled=0)
        self.refresh_table()

    def _clear_completed(self):
        reply = QMessageBox.question(
            self,
            "Clear Completed",
            "Remove all completed downloads from the table?\n(Your downloaded files will NOT be deleted).",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            cleared = self.db.clear_completed()
            self.refresh_table()
            self.statusBar().showMessage(f"Cleared {cleared} completed item(s).", 4000)

    # === IMPORT / EXPORT ===

    def _import_url_txt(self):
        f, _ = QFileDialog.getOpenFileName(
            self, "Import url.txt", "", "Text Files (*.txt);;All Files (*)"
        )
        if f:
            try:
                added, skipped = self.db.import_from_url_txt(f)
                self.refresh_table()
                QMessageBox.information(
                    self,
                    "Import Complete",
                    f"Successfully imported {added} new URLs.\n({skipped} duplicates/skipped).",
                )
            except Exception as e:
                QMessageBox.critical(self, "Import Failed", str(e))

    def _export_url_txt(self):
        f, _ = QFileDialog.getSaveFileName(
            self, "Export url.txt", "url.txt", "Text Files (*.txt);;All Files (*)"
        )
        if f:
            try:
                count = self.db.export_to_url_txt(f)
                QMessageBox.information(
                    self,
                    "Export Complete",
                    f"Successfully exported {count} URLs to:\n{f}",
                )
            except Exception as e:
                QMessageBox.critical(self, "Export Failed", str(e))

    def _show_about(self):
        ytdlp_ver = get_ytdlp_version() or "Not detected"
        QMessageBox.about(
            self,
            "About ytubeDL",
            f"<h3>ytubeDL v2.0.0</h3>"
            f"<p>Fast, reliable desktop YouTube video and channel downloader for macOS and Windows.</p>"
            f"<p><b>Active yt-dlp Engine:</b> {ytdlp_ver}</p>"
            f"<p>Designed with high-speed virtualized tables, reliable extraction arguments, and 1-click self-updating.</p>",
        )

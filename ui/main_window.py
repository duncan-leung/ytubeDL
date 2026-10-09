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
    QVBoxLayout,
    QWidget,
)

from core.bin_helper import find_ytdlp, get_ytdlp_version
from core.config import Config
from core.db import Database
from core.downloader import DownloadWorker
from ui.batch_dialog import BatchDialog
from ui.settings_dialog import SettingsDialog
from ui.styles import MODERN_STYLE
from ui.table_model import DownloadTableModel


class MainWindow(QMainWindow):
    """Primary desktop interface for ytubeDL."""

    def __init__(self, db: Database, config: Config):
        super().__init__()
        self.db = db
        self.config = config
        self.worker: DownloadWorker = None

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
        main_layout.setContentsMargins(16, 12, 16, 12)
        main_layout.setSpacing(10)

        # === 1. TOP URL ENTRY BAR ===
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

        main_layout.addLayout(top_bar)

        # === 2. OUTPUT FOLDER & CONTROLS BAR ===
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

        # Run / Stop Buttons
        self.btn_run = QPushButton("▶ Run Downloads", self)
        self.btn_run.setObjectName("btn_run")
        self.btn_run.setMinimumWidth(130)
        self.btn_run.clicked.connect(self._start_download_queue)
        control_bar.addWidget(self.btn_run)

        self.btn_stop = QPushButton("⏹ Stop", self)
        self.btn_stop.setObjectName("btn_stop")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop_download_queue)
        control_bar.addWidget(self.btn_stop)

        main_layout.addLayout(control_bar)

        # === 3. FILTER & SEARCH BAR ===
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

        main_layout.addLayout(filter_bar)

        # === 4. CENTRAL TABLE VIEW (VIRTUALIZED) ===
        self.model = DownloadTableModel(self.db, self)
        self.table_view = QTableView(self)
        self.table_view.setModel(self.model)
        self.table_view.setAlternatingRowColors(True)
        self.table_view.setSelectionBehavior(QTableView.SelectRows)
        self.table_view.setSelectionMode(QTableView.ExtendedSelection)
        self.table_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table_view.customContextMenuRequested.connect(self._show_context_menu)

        # Column sizing
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

        main_layout.addWidget(self.table_view, stretch=1)

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

    def _stop_download_queue(self):
        if self.worker and self.worker.isRunning():
            self.lbl_batch_status.setText("Stopping queue...")
            self.worker.stop()
            self.btn_stop.setEnabled(False)

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

        menu.addSeparator()

        # If single item selected with a filename, provide file open
        if len(items) == 1 and items[0].get("filename"):
            act_reveal = menu.addAction("📁 Reveal Downloaded File")
            act_reveal.triggered.connect(lambda: self._reveal_file(items[0]))

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
        if not filepath.exists():
            QMessageBox.warning(self, "File Not Found", f"File does not exist:\n{filepath}")
            return

        if sys.platform == "darwin":
            subprocess.run(["open", "-R", str(filepath)])
        elif sys.platform == "win32":
            subprocess.run(["explorer", f"/select,{os.path.normpath(str(filepath))}"])
        else:
            subprocess.run(["xdg-open", str(filepath.parent)])

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
            f"<h3>ytubeDL</h3>"
            f"<p>Fast, reliable desktop YouTube video downloader for macOS and Windows.</p>"
            f"<p><b>Active yt-dlp Engine:</b> {ytdlp_ver}</p>"
            f"<p>Designed with high-speed virtualized tables, reliable extraction arguments, and 1-click self-updating.</p>",
        )

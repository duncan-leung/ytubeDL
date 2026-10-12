"""Foldable Channel Management View with Tree View, Clip Deletion, and Sync Controls."""
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from PyQt5.QtCore import QItemSelectionModel, QModelIndex, Qt, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QColor, QDesktopServices, QDrag, QFont, QIcon, QKeySequence, QStandardItem, QStandardItemModel
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QDialog,
    QFileDialog,
    QHeaderView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QShortcut,
    QSplitter,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from core.bin_helper import find_ytdlp
from core.channel_worker import ChannelWorker
from core.config import Config
from core.db import Database
from ui.channel_dialog import ChannelDialog

ROLE_NODE_TYPE = Qt.UserRole + 1      # "channel" or "clip"
ROLE_DATA_ID = Qt.UserRole + 2        # channel_id (int) or clip_id (int)
ROLE_RAW_DATA = Qt.UserRole + 3       # entire channel dict or clip dict


def format_local_timestamp(utc_str: Optional[str], fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Convert a UTC timestamp string (e.g. from SQLite CURRENT_TIMESTAMP) to the device's local timezone."""
    if not utc_str or not isinstance(utc_str, str):
        return ""
    clean = utc_str.strip()
    if clean.lower() in ("never checked", "none", ""):
        return clean

    for pattern in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M",
    ):
        try:
            dt = datetime.strptime(clean, pattern)
            dt_utc = dt.replace(tzinfo=timezone.utc)
            dt_local = dt_utc.astimezone()  # Device local timezone
            return dt_local.strftime(fmt)
        except (ValueError, OverflowError):
            continue
    return clean[:16] if len(clean) >= 16 else clean


class ChannelTreeView(QTreeView):
    """Custom QTreeView with drag-and-drop channel re-ordering."""

    channel_dropped = pyqtSignal(int, object, str)  # (source_id, target_id_or_None, indicator_str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDefaultDropAction(Qt.CopyAction)

    def startDrag(self, supportedActions):
        indexes = self.selectedIndexes()
        if not indexes or indexes[0].parent().isValid():
            return
        item = self.model().itemFromIndex(indexes[0])
        if not item or item.data(ROLE_NODE_TYPE) != "channel":
            return
        drag = QDrag(self)
        mime_data = self.model().mimeData(indexes)
        drag.setMimeData(mime_data)
        # Use CopyAction so Qt's default drag handler never calls clearOrRemove() on our model
        drag.exec_(Qt.CopyAction)

    def dragEnterEvent(self, event):
        indexes = self.selectedIndexes()
        if indexes and not indexes[0].parent().isValid():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        indexes = self.selectedIndexes()
        if indexes and not indexes[0].parent().isValid():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        indexes = self.selectedIndexes()
        if not indexes or indexes[0].parent().isValid():
            event.ignore()
            return

        source_item = self.model().itemFromIndex(indexes[0])
        if not source_item or source_item.data(ROLE_NODE_TYPE) != "channel":
            event.ignore()
            return

        source_id = source_item.data(ROLE_DATA_ID)
        pos = event.pos()
        target_idx = self.indexAt(pos)

        target_id = None
        indicator_str = "below"

        if target_idx.isValid():
            if target_idx.parent().isValid():
                target_idx = target_idx.parent()
            target_item = self.model().itemFromIndex(target_idx)
            if target_item and target_item.data(ROLE_NODE_TYPE) == "channel":
                target_id = target_item.data(ROLE_DATA_ID)
                rect = self.visualRect(target_idx)
                indicator_str = "below" if pos.y() > rect.center().y() else "above"

        event.setDropAction(Qt.CopyAction)
        event.accept()

        # Defer execution so startDrag terminates cleanly before refresh_tree modifies the model
        QTimer.singleShot(0, lambda: self.channel_dropped.emit(source_id, target_id, indicator_str))


class ChannelView(QWidget):
    """Widget displaying foldable channels and their latest retrieved clips in a tree hierarchy."""

    log_message = pyqtSignal(str)
    status_changed = pyqtSignal(str)

    def __init__(self, db: Database, config: Config, parent=None):
        super().__init__(parent)
        self.db = db
        self.config = config
        self.worker: Optional[ChannelWorker] = None
        self.search_filter = ""

        self._setup_ui()
        self._setup_shortcuts()
        self.refresh_tree()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # === 1. TOP TOOLBAR ===
        toolbar_layout = QHBoxLayout()
        toolbar_layout.setSpacing(6)

        self.btn_add_channel = QPushButton("➕ Add Channel...", self)
        self.btn_add_channel.setStyleSheet(
            "background-color: #2563eb; color: white; font-weight: bold; padding: 6px 14px; border-radius: 4px;"
        )
        self.btn_add_channel.clicked.connect(self._open_add_channel_dialog)
        toolbar_layout.addWidget(self.btn_add_channel)

        self.btn_edit_channel = QPushButton("⚙ Edit Channel...", self)
        self.btn_edit_channel.setStyleSheet("padding: 6px 12px; font-weight: 500;")
        self.btn_edit_channel.clicked.connect(self._on_edit_channel_clicked)
        toolbar_layout.addWidget(self.btn_edit_channel)

        self.btn_check_all = QPushButton("🔄 Check All", self)
        self.btn_check_all.setStyleSheet(
            "background-color: #10b981; color: white; font-weight: bold; padding: 6px 14px; border-radius: 4px;"
        )
        self.btn_check_all.clicked.connect(lambda: self._start_channel_sync())
        toolbar_layout.addWidget(self.btn_check_all)

        self.btn_download_pending = QPushButton("📥 Download Pending", self)
        self.btn_download_pending.setStyleSheet("padding: 6px 12px; font-weight: 500;")
        self.btn_download_pending.clicked.connect(lambda: self._start_channel_sync(force_download=True))
        toolbar_layout.addWidget(self.btn_download_pending)

        self.btn_stop = QPushButton("⏹ Stop", self)
        self.btn_stop.setEnabled(False)
        self.btn_stop.setStyleSheet(
            "background-color: #ef4444; color: white; font-weight: bold; padding: 6px 12px; border-radius: 4px;"
        )
        self.btn_stop.clicked.connect(self._stop_sync)
        toolbar_layout.addWidget(self.btn_stop)

        toolbar_layout.addSpacing(6)

        # Move channel up / down buttons
        self.btn_move_up = QPushButton("▲", self)
        self.btn_move_up.setToolTip("Move selected channel up (Alt+Up)")
        self.btn_move_up.setStyleSheet("padding: 5px 10px; font-weight: bold;")
        self.btn_move_up.clicked.connect(self._move_channel_up)
        toolbar_layout.addWidget(self.btn_move_up)

        self.btn_move_down = QPushButton("▼", self)
        self.btn_move_down.setToolTip("Move selected channel down (Alt+Down)")
        self.btn_move_down.setStyleSheet("padding: 5px 10px; font-weight: bold;")
        self.btn_move_down.clicked.connect(self._move_channel_down)
        toolbar_layout.addWidget(self.btn_move_down)

        toolbar_layout.addSpacing(6)

        self.btn_unfold_all = QPushButton("▼ Unfold All", self)
        self.btn_unfold_all.setStyleSheet("padding: 5px 10px;")
        self.btn_unfold_all.clicked.connect(self._unfold_all)
        toolbar_layout.addWidget(self.btn_unfold_all)

        self.btn_fold_all = QPushButton("▲ Fold All", self)
        self.btn_fold_all.setStyleSheet("padding: 5px 10px;")
        self.btn_fold_all.clicked.connect(self._fold_all)
        toolbar_layout.addWidget(self.btn_fold_all)

        toolbar_layout.addStretch()

        # Search box
        self.txt_search = QLineEdit(self)
        self.txt_search.setPlaceholderText("🔍 Filter channels or titles...")
        self.txt_search.setClearButtonEnabled(True)
        self.txt_search.setMaximumWidth(240)
        self.txt_search.textChanged.connect(self._on_search_changed)
        toolbar_layout.addWidget(self.txt_search)

        layout.addLayout(toolbar_layout)

        # === 2. FOLDABLE TREE VIEW ===
        self.tree_view = ChannelTreeView(self)
        self.tree_view.setAlternatingRowColors(True)
        self.tree_view.setSelectionMode(QTreeView.ExtendedSelection)
        self.tree_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree_view.customContextMenuRequested.connect(self._show_context_menu)
        self.tree_view.channel_dropped.connect(self._on_channel_dropped)
        self.tree_view.doubleClicked.connect(self._on_tree_double_clicked)
        self.tree_view.setStyleSheet(
            """
            QTreeView {
                background-color: #ffffff;
                alternate-background-color: #f8fafc;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                font-size: 13px;
            }
            QTreeView::item {
                padding: 4px 6px;
            }
            QTreeView::item:selected {
                background-color: #e0e7ff;
                color: #1e1b4b;
            }
            QHeaderView::section {
                background-color: #f1f5f9;
                padding: 6px;
                font-weight: 600;
                color: #475569;
                border: none;
                border-bottom: 1px solid #cbd5e1;
                border-right: 1px solid #e2e8f0;
            }
            """
        )

        self.model = QStandardItemModel(self)
        self.model.setHorizontalHeaderLabels([
            "Channel / Clip Title",
            "Content Type",
            "Status / Progress",
            "Date",
            "Folder / Filename",
        ])
        self.tree_view.setModel(self.model)

        header = self.tree_view.header()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.Interactive)
        self.tree_view.setColumnWidth(4, 280)

        # Keyboard shortcuts for tree view
        shortcut_up = QShortcut(QKeySequence("Alt+Up"), self.tree_view)
        shortcut_up.activated.connect(self._move_channel_up)
        shortcut_down = QShortcut(QKeySequence("Alt+Down"), self.tree_view)
        shortcut_down.activated.connect(self._move_channel_down)
        shortcut_rename = QShortcut(QKeySequence(Qt.Key_F2), self.tree_view)
        shortcut_rename.activated.connect(self._on_rename_shortcut)

        layout.addWidget(self.tree_view, stretch=1)

        # === 3. BOTTOM PROGRESS BAR ===
        prog_layout = QHBoxLayout()
        self.lbl_sync_status = QLabel("Ready", self)
        self.lbl_sync_status.setStyleSheet("font-weight: 500; color: #475569;")
        prog_layout.addWidget(self.lbl_sync_status)

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setValue(0)
        prog_layout.addWidget(self.progress_bar, stretch=1)

        layout.addLayout(prog_layout)

    def _setup_shortcuts(self):
        shortcut_del = QShortcut(QKeySequence.Delete, self.tree_view)
        shortcut_del.activated.connect(self._delete_selected)
        shortcut_up = QShortcut(QKeySequence("Alt+Up"), self.tree_view)
        shortcut_up.activated.connect(self._move_channel_up)
        shortcut_down = QShortcut(QKeySequence("Alt+Down"), self.tree_view)
        shortcut_down.activated.connect(self._move_channel_down)

    # === TREE POPULATION & FOLD / UNFOLD ===

    def refresh_tree(self, selected_channel_id: Optional[int] = None):
        """Rebuild hierarchical tree structure: Level 1 = Channels, Level 2 = Clips."""
        # Save expanded state of channel IDs
        expanded_ids = set()
        for row in range(self.model.rowCount()):
            parent_idx = self.model.index(row, 0)
            if self.tree_view.isExpanded(parent_idx):
                item = self.model.item(row, 0)
                if item:
                    ch_id = item.data(ROLE_DATA_ID)
                    if ch_id:
                        expanded_ids.add(ch_id)

        self.model.removeRows(0, self.model.rowCount())

        channels = self.db.get_channels()
        query = self.search_filter.lower()

        total_channels = len(channels)
        total_clips = 0

        bold_font = QFont()
        bold_font.setBold(True)

        for ch in channels:
            ch_id = ch["id"]
            ch_name = ch["name"]
            c_type = ch.get("content_type", "videos")
            ch_folder = ch.get("folder", "")
            last_checked = ch.get("last_checked") or ""
            local_checked = format_local_timestamp(last_checked) if last_checked else "Never checked"
            limit = ch.get("check_limit", 20)
            auto_dl = "Auto-DL: ON" if ch.get("auto_download", 1) else "Auto-DL: OFF"

            clips = self.db.get_channel_videos(ch_id)

            # Filter logic
            matching_clips = clips
            if query:
                channel_matches = query in ch_name.lower() or query in c_type.lower()
                if not channel_matches:
                    matching_clips = [
                        c for c in clips if query in (c.get("title") or "").lower()
                    ]
                    if not matching_clips:
                        continue

            total_clips += len(clips)

            # Create Channel Row (Parent)
            type_icon = "🎬" if c_type == "videos" else ("⚡" if c_type == "shorts" else "📑")
            ch_title_text = f"{type_icon} {ch_name}"
            item_name = QStandardItem(ch_title_text)
            item_name.setFont(bold_font)
            item_name.setData("channel", ROLE_NODE_TYPE)
            item_name.setData(ch_id, ROLE_DATA_ID)
            item_name.setData(ch, ROLE_RAW_DATA)
            item_name.setToolTip(
                f"Channel: {ch_name}\n"
                f"Type: {c_type.capitalize()}\n"
                f"Check Limit: {limit} latest clips\n"
                f"Clips in database: {len(clips)}\n"
                f"Last Checked: {local_checked}"
            )

            item_type = QStandardItem(f"{c_type.capitalize()} (Limit: {limit})")
            item_type.setData("channel", ROLE_NODE_TYPE)
            item_type.setData(ch_id, ROLE_DATA_ID)

            item_status = QStandardItem(f"{auto_dl} • {len(clips)} saved")
            item_status.setData("channel", ROLE_NODE_TYPE)
            item_status.setData(ch_id, ROLE_DATA_ID)

            item_date = QStandardItem(f"Checked: {local_checked}")
            item_date.setData("channel", ROLE_NODE_TYPE)
            item_date.setData(ch_id, ROLE_DATA_ID)
            if last_checked:
                item_date.setToolTip(f"Last checked: {local_checked} (Local Time)\nUTC: {last_checked}")

            item_folder = QStandardItem(ch_folder)
            item_folder.setData("channel", ROLE_NODE_TYPE)
            item_folder.setData(ch_id, ROLE_DATA_ID)

            channel_row_items = [item_name, item_type, item_status, item_date, item_folder]

            # Populate Clips (Children)
            for clip in matching_clips:
                clip_id = clip["id"]
                clip_title = clip.get("title") or clip.get("video_id") or "Untitled Clip"
                clip_status = clip.get("status", "pending")
                clip_date = clip.get("download_date") or clip.get("discovered_at") or ""
                local_clip_date = format_local_timestamp(clip_date)
                clip_fn = clip.get("filename") or ""

                if clip_status == "completed":
                    status_text = "✅ Completed"
                elif clip_status == "downloading":
                    status_text = "⬇ Downloading..."
                elif clip_status == "error":
                    status_text = f"❌ {clip.get('error_msg') or 'Error'}"
                else:
                    status_text = "⏳ Pending"

                child_name = QStandardItem(f"  • {clip_title}")
                child_name.setData("clip", ROLE_NODE_TYPE)
                child_name.setData(clip_id, ROLE_DATA_ID)
                child_name.setData(clip, ROLE_RAW_DATA)

                child_type = QStandardItem("-")
                child_type.setData("clip", ROLE_NODE_TYPE)
                child_type.setData(clip_id, ROLE_DATA_ID)

                child_status = QStandardItem(status_text)
                child_status.setData("clip", ROLE_NODE_TYPE)
                child_status.setData(clip_id, ROLE_DATA_ID)

                child_date = QStandardItem(local_clip_date)
                child_date.setData("clip", ROLE_NODE_TYPE)
                child_date.setData(clip_id, ROLE_DATA_ID)
                if clip_date:
                    child_date.setToolTip(f"Recorded: {local_clip_date} (Local Time)\nUTC: {clip_date}")

                child_fn = QStandardItem(clip_fn)
                child_fn.setData("clip", ROLE_NODE_TYPE)
                child_fn.setData(clip_id, ROLE_DATA_ID)

                item_name.appendRow([child_name, child_type, child_status, child_date, child_fn])

            self.model.appendRow(channel_row_items)

            # Restore expanded state (default to unfolded if newly added or matching query)
            parent_idx = item_name.index()
            if ch_id in expanded_ids or query:
                self.tree_view.expand(parent_idx)

            if selected_channel_id is not None and ch_id == selected_channel_id:
                self.tree_view.setCurrentIndex(parent_idx)
                self.tree_view.selectionModel().select(
                    parent_idx,
                    QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows,
                )

        self.lbl_sync_status.setText(f"{total_channels} channels, {total_clips} clips tracked.")

    def _unfold_all(self):
        """Unfold / expand all channel rows."""
        self.tree_view.expandAll()

    def _fold_all(self):
        """Fold / collapse all channel rows."""
        self.tree_view.collapseAll()

    def _on_search_changed(self, text: str):
        self.search_filter = text.strip()
        self.refresh_tree()

    # === REORDERING & DRAG-AND-DROP ===

    def _on_channel_dropped(self, source_id: int, target_id: Optional[int], indicator: str):
        channels = self.db.get_channels()
        ch_ids = [c["id"] for c in channels]
        if source_id not in ch_ids:
            return
        ch_ids.remove(source_id)
        if target_id is None or target_id not in ch_ids:
            ch_ids.append(source_id)
        else:
            idx = ch_ids.index(target_id)
            if indicator == "below":
                ch_ids.insert(idx + 1, source_id)
            else:
                ch_ids.insert(idx, source_id)
        self.db.reorder_channels(ch_ids)
        self.refresh_tree(selected_channel_id=source_id)
        self.status_changed.emit("Reordered channels.")

    def _move_channel_up(self):
        items = self._get_selected_items()
        if not items:
            return
        it = items[0]
        cid = it.data(ROLE_DATA_ID)
        if it.data(ROLE_NODE_TYPE) == "clip":
            cid = it.data(ROLE_RAW_DATA).get("channel_id")
        if cid:
            self._move_channel_by_id(cid, -1)

    def _move_channel_down(self):
        items = self._get_selected_items()
        if not items:
            return
        it = items[0]
        cid = it.data(ROLE_DATA_ID)
        if it.data(ROLE_NODE_TYPE) == "clip":
            cid = it.data(ROLE_RAW_DATA).get("channel_id")
        if cid:
            self._move_channel_by_id(cid, 1)

    def _move_channel_by_id(self, channel_id: int, direction: int):
        moved = self.db.move_channel(channel_id, direction)
        if moved:
            self.refresh_tree(selected_channel_id=channel_id)
            self.status_changed.emit("Channel order changed.")

    # === ACTIONS & DIALOGS ===

    def _on_tree_double_clicked(self, index: QModelIndex):
        item = self.model.itemFromIndex(index)
        if not item:
            return
        node_type = item.data(ROLE_NODE_TYPE)
        if node_type == "channel":
            ch = item.data(ROLE_RAW_DATA)
            if ch:
                self._open_edit_channel_dialog(ch)
        elif node_type == "clip":
            clip = item.data(ROLE_RAW_DATA)
            if clip:
                if clip.get("status") == "completed" and clip.get("filename"):
                    self._reveal_clip_file(clip)
                elif clip.get("video_url"):
                    self._open_in_browser(clip["video_url"])

    def _on_edit_channel_clicked(self):
        items = self._get_selected_items()
        if not items:
            QMessageBox.information(self, "Edit Channel", "Please select a channel to edit.")
            return
        it = items[0]
        node_type = it.data(ROLE_NODE_TYPE)
        if node_type == "channel":
            ch = it.data(ROLE_RAW_DATA)
            self._open_edit_channel_dialog(ch)
        elif node_type == "clip":
            cid = it.data(ROLE_RAW_DATA).get("channel_id")
            ch = self.db.get_channel(cid)
            if ch:
                self._open_edit_channel_dialog(ch)

    def _on_rename_shortcut(self):
        items = self._get_selected_items()
        if not items:
            return
        it = items[0]
        if it.data(ROLE_NODE_TYPE) == "channel":
            cid = it.data(ROLE_DATA_ID)
            ch_data = it.data(ROLE_RAW_DATA)
            if cid and ch_data:
                self._quick_rename_channel(cid, ch_data.get("name", ""))

    def _quick_rename_channel(self, ch_id: int, current_name: str):
        new_name, ok = QInputDialog.getText(
            self,
            "Rename Channel",
            "Enter new channel display name:",
            text=current_name,
        )
        if ok and new_name.strip():
            self.db.update_channel(channel_id=ch_id, name=new_name.strip())
            self.refresh_tree(selected_channel_id=ch_id)
            self.status_changed.emit(f"Renamed channel to '{new_name.strip()}'.")

    def _open_add_channel_dialog(self):
        dlg = ChannelDialog(self.config, parent=self)
        if dlg.exec_() == QDialog.Accepted:
            data = dlg.get_channel_data()
            ch_id = self.db.add_channel(
                name=data["name"],
                channel_id=data["channel_id"],
                channel_url=data["channel_url"],
                content_type=data["content_type"],
                folder=data["folder"],
                check_limit=data["check_limit"],
                auto_download=data["auto_download"],
                quality=data["quality"],
                numbering=data["numbering"],
                download_thumbnail=data["download_thumbnail"],
                use_title=data["use_title"],
            )
            self.refresh_tree(selected_channel_id=ch_id)
            self.status_changed.emit(f"Added channel: {data['name']}")

            # Ask if user wants to check and fetch latest clips now
            reply = QMessageBox.question(
                self,
                "Check Channel Now",
                f"Channel '{data['name']}' was added.\n\n"
                f"Would you like to check it now to retrieve the latest {data['check_limit']} clips?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.Yes and ch_id:
                self._start_channel_sync(target_ids=[ch_id])

    def _open_edit_channel_dialog(self, ch: Dict):
        dlg = ChannelDialog(self.config, existing_channel=ch, parent=self)
        if dlg.exec_() == QDialog.Accepted:
            data = dlg.get_channel_data()
            self.db.update_channel(
                channel_id=ch["id"],
                name=data["name"],
                channel_id_code=data.get("channel_id"),
                channel_url=data["channel_url"],
                content_type=data["content_type"],
                folder=data["folder"],
                check_limit=data["check_limit"],
                auto_download=data["auto_download"],
                quality=data["quality"],
                numbering=data["numbering"],
                download_thumbnail=data["download_thumbnail"],
                use_title=data["use_title"],
            )
            self.refresh_tree(selected_channel_id=ch["id"])
            self.status_changed.emit(f"Updated channel: {data['name']}")

            # Ask if user wants to check channel with new settings now
            reply = QMessageBox.question(
                self,
                "Check Channel Now",
                f"Channel '{data['name']}' settings updated.\n\n"
                f"Check limit is now {data['check_limit']}.\n"
                f"Would you like to check this channel now?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                self._start_channel_sync(target_ids=[ch["id"]])

    # === WORKER SYNC & DOWNLOAD EXECUTION ===

    def _start_channel_sync(
        self, target_ids: Optional[List[int]] = None, force_download: Optional[bool] = None
    ):
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "Sync In Progress", "A channel check is already running.")
            return

        ytdlp_bin = find_ytdlp(self.config.get("custom_ytdlp_path"))
        if not ytdlp_bin:
            QMessageBox.warning(
                self,
                "yt-dlp Required",
                "yt-dlp is required to download channel clips. Please ensure it is installed.",
            )
            return

        self.btn_check_all.setEnabled(False)
        self.btn_download_pending.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.progress_bar.setValue(0)
        self.lbl_sync_status.setText("Checking channels...")

        self.worker = ChannelWorker(
            self.db,
            self.config,
            target_channel_ids=target_ids,
            force_download=force_download,
            parent=self,
        )
        self.worker.log_message.connect(self._on_worker_log)
        self.worker.channel_started.connect(self._on_channel_started)
        self.worker.channel_finished.connect(self._on_channel_finished)
        self.worker.video_finished.connect(self._on_video_finished)
        self.worker.overall_progress.connect(self._on_overall_progress)
        self.worker.all_channels_finished.connect(self._on_all_finished)
        self.worker.start()

    def _stop_sync(self):
        if self.worker and self.worker.isRunning():
            self.lbl_sync_status.setText("Stopping channel check...")
            self.worker.stop()
            self.btn_stop.setEnabled(False)

    def _on_worker_log(self, text: str):
        self.log_message.emit(text)

    def _on_channel_started(self, channel_id: int, channel_name: str):
        self.lbl_sync_status.setText(f"Checking: {channel_name}...")

    def _on_channel_finished(self, channel_id: int, new_found: int, downloaded: int):
        self.refresh_tree()

    def _on_video_finished(self, video_row_id: int, filename: str, title: str):
        self.refresh_tree()

    def _on_overall_progress(self, current: int, total: int):
        if total > 0:
            pct = int((current / total) * 100)
            self.progress_bar.setValue(pct)
            self.progress_bar.setFormat(f"{current} / {total} channels ({pct}%)")

    def _on_all_finished(self, new_found: int, total_downloaded: int):
        self.btn_check_all.setEnabled(True)
        self.btn_download_pending.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.progress_bar.setValue(100)
        status_msg = f"Check complete! {new_found} new clip(s) found, {total_downloaded} downloaded."
        self.lbl_sync_status.setText(status_msg)
        self.status_changed.emit(status_msg)
        self.refresh_tree()

    # === CONTEXT MENU & ROW DELETION ===

    def _get_selected_items(self) -> List[QStandardItem]:
        indexes = self.tree_view.selectionModel().selectedRows(0)
        items = []
        for idx in indexes:
            it = self.model.itemFromIndex(idx)
            if it:
                items.append(it)
        return items

    def _show_context_menu(self, pos):
        items = self._get_selected_items()
        if not items:
            return

        menu = QMenu(self)

        # Distinguish channel rows vs clip rows
        first_item = items[0]
        node_type = first_item.data(ROLE_NODE_TYPE)

        if node_type == "channel":
            ch_data = first_item.data(ROLE_RAW_DATA)
            ch_id = first_item.data(ROLE_DATA_ID)

            act_sync_ch = menu.addAction(f"🔄 Check '{ch_data['name']}' Now")
            act_sync_ch.triggered.connect(lambda: self._start_channel_sync(target_ids=[ch_id]))

            act_dl_pending = menu.addAction("📥 Download Pending Clips for Channel")
            act_dl_pending.triggered.connect(
                lambda: self._start_channel_sync(target_ids=[ch_id], force_download=True)
            )

            menu.addSeparator()

            # Fold / Unfold this channel
            parent_idx = first_item.index()
            is_exp = self.tree_view.isExpanded(parent_idx)
            act_toggle_fold = menu.addAction("▲ Fold Channel" if is_exp else "▼ Unfold Channel")
            act_toggle_fold.triggered.connect(
                lambda: self.tree_view.setExpanded(parent_idx, not is_exp)
            )

            act_open_folder = menu.addAction("📁 Open Save Folder")
            act_open_folder.triggered.connect(lambda: self._open_channel_folder(ch_data))

            act_rename = menu.addAction("✏ Rename Channel... (F2)")
            act_rename.triggered.connect(lambda: self._quick_rename_channel(ch_id, ch_data["name"]))

            act_edit = menu.addAction("⚙ Edit Channel Settings...")
            act_edit.triggered.connect(lambda: self._open_edit_channel_dialog(ch_data))

            menu.addSeparator()

            act_move_up = menu.addAction("▲ Move Channel Up (Alt+Up)")
            act_move_up.triggered.connect(lambda: self._move_channel_by_id(ch_id, -1))

            act_move_down = menu.addAction("▼ Move Channel Down (Alt+Down)")
            act_move_down.triggered.connect(lambda: self._move_channel_by_id(ch_id, 1))

            menu.addSeparator()

            act_prune = menu.addAction("🧹 Prune Old Clips (Keep Latest N)...")
            act_prune.triggered.connect(lambda: self._prune_channel_dialog(ch_id, ch_data["name"]))

            act_delete_ch = menu.addAction("🗑 Delete Channel & History")
            act_delete_ch.triggered.connect(lambda: self._delete_channel(ch_id, ch_data["name"]))

        elif node_type == "clip":
            clip_data = first_item.data(ROLE_RAW_DATA)

            act_dl_clip = menu.addAction("▶ Download This Clip Now")
            act_dl_clip.triggered.connect(lambda: self._download_specific_clip(clip_data))

            if clip_data.get("filename"):
                act_reveal = menu.addAction("🎬 Reveal Video File")
                act_reveal.triggered.connect(lambda: self._reveal_clip_file(clip_data))

            act_browser = menu.addAction("🌐 Open in Web Browser")
            act_browser.triggered.connect(lambda: self._open_in_browser(clip_data.get("video_url", "")))

            act_copy = menu.addAction("📋 Copy URL")
            act_copy.triggered.connect(lambda: self._copy_url(clip_data.get("video_url", "")))

            menu.addSeparator()

            act_reset = menu.addAction("🔄 Reset Status to Pending")
            act_reset.triggered.connect(lambda: self._reset_selected_clips_to_pending(items))

            act_delete_rows = menu.addAction(f"🗑 Delete Selected Clip Row(s) ({len(items)})")
            act_delete_rows.triggered.connect(self._delete_selected)

        menu.exec_(self.tree_view.viewport().mapToGlobal(pos))

    def _open_channel_folder(self, ch_data: Dict):
        folder = ch_data.get("folder") or self.config.output_dir
        if not os.path.exists(folder):
            os.makedirs(folder, exist_ok=True)
        if sys.platform == "darwin":
            subprocess.run(["open", folder])
        elif sys.platform == "win32":
            subprocess.run(["explorer", os.path.normpath(folder)])
        else:
            subprocess.run(["xdg-open", folder])

    def _reveal_clip_file(self, clip_data: Dict):
        fn = clip_data.get("filename")
        ch_id = clip_data.get("channel_id")
        ch = self.db.get_channel(ch_id)
        folder = ch.get("folder") if ch else self.config.output_dir
        if not fn:
            return
        filepath = Path(folder) / fn
        if not filepath.exists():
            QMessageBox.warning(self, "File Not Found", f"File does not exist:\n{filepath}")
            return
        if sys.platform == "darwin":
            subprocess.run(["open", "-R", str(filepath)])
        elif sys.platform == "win32":
            subprocess.run(["explorer", f"/select,{os.path.normpath(str(filepath))}"])
        else:
            subprocess.run(["xdg-open", str(filepath.parent)])

    def _open_in_browser(self, url: str):
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def _copy_url(self, url: str):
        if url:
            QApplication.clipboard().setText(url)
            self.status_changed.emit(f"Copied to clipboard: {url}")

    def _reset_selected_clips_to_pending(self, items: List[QStandardItem]):
        for it in items:
            if it.data(ROLE_NODE_TYPE) == "clip":
                cid = it.data(ROLE_DATA_ID)
                self.db.update_channel_video_status(cid, "pending")
        self.refresh_tree()

    def _download_specific_clip(self, clip_data: Dict):
        ch_id = clip_data.get("channel_id")
        self.db.update_channel_video_status(clip_data["id"], "pending")
        self._start_channel_sync(target_ids=[ch_id], force_download=True)

    def _delete_channel(self, channel_id: int, channel_name: str):
        reply = QMessageBox.question(
            self,
            "Confirm Delete Channel",
            f"Are you sure you want to delete channel '{channel_name}'?\n\n"
            f"All tracked clip records for this channel will be removed from the database.\n"
            f"(Downloaded video files on disk will NOT be touched).",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.db.delete_channel(channel_id)
            self.refresh_tree()
            self.status_changed.emit(f"Deleted channel: {channel_name}")

    def _prune_channel_dialog(self, channel_id: int, channel_name: str):
        val, ok = QInputDialog.getInt(
            self,
            "Prune Old Clips",
            f"Keep how many newest clips for '{channel_name}'?\n(Older rows will be deleted from history):",
            20,
            1,
            500,
        )
        if ok:
            pruned = self.db.prune_channel_videos(channel_id, keep_latest=val)
            self.refresh_tree()
            QMessageBox.information(
                self, "Prune Complete", f"Pruned {pruned} older clip row(s) from history."
            )

    def _delete_selected(self):
        """Delete selected rows (channel or clip rows). Allows user to delete any old rows."""
        items = self._get_selected_items()
        if not items:
            return

        clip_ids_to_del = []
        channel_items_to_del = []

        for it in items:
            ntype = it.data(ROLE_NODE_TYPE)
            if ntype == "clip":
                clip_ids_to_del.append(it.data(ROLE_DATA_ID))
            elif ntype == "channel":
                channel_items_to_del.append(it)

        if channel_items_to_del:
            for it in channel_items_to_del:
                ch_id = it.data(ROLE_DATA_ID)
                ch_raw = it.data(ROLE_RAW_DATA)
                self._delete_channel(ch_id, ch_raw["name"])
            return

        if clip_ids_to_del:
            reply = QMessageBox.question(
                self,
                "Delete Clip Rows",
                f"Delete {len(clip_ids_to_del)} selected clip record(s) from channel history?\n\n"
                f"(Downloaded video files on disk will NOT be deleted).",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                deleted = self.db.delete_channel_video_rows(clip_ids_to_del)
                self.refresh_tree()
                self.status_changed.emit(f"Deleted {deleted} clip record(s).")

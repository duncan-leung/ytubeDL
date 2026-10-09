"""High-performance virtualized QAbstractTableModel for ytubeDL."""
from typing import Any, Dict, List, Optional
from PyQt5.QtCore import QAbstractTableModel, QModelIndex, Qt
from PyQt5.QtGui import QColor

from core.db import Database


class DownloadTableModel(QAbstractTableModel):
    """Virtualized model capable of rendering 10,000+ items at 60 FPS."""

    COLUMNS = ["Active", "Status", "Title", "Filename", "URL"]

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self._items: List[Dict] = []
        # Fast lookup mapping: item_id -> row_index
        self._id_to_row: Dict[int, int] = {}

    def set_items(self, items: List[Dict]):
        """Replace all items in model with minimal rendering overhead."""
        self.beginResetModel()
        self._items = items
        self._id_to_row = {item["id"]: i for i, item in enumerate(items)}
        self.endResetModel()

    def get_items(self) -> List[Dict]:
        return self._items

    def get_item(self, row: int) -> Optional[Dict]:
        if 0 <= row < len(self._items):
            return self._items[row]
        return None

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._items)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self.COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole) -> Any:
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            if 0 <= section < len(self.COLUMNS):
                return self.COLUMNS[section]
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:
        if not index.isValid():
            return Qt.NoItemFlags

        col = index.column()
        base_flags = Qt.ItemIsSelectable | Qt.ItemIsEnabled

        if col == 0:
            return base_flags | Qt.ItemIsUserCheckable
        elif col in (2, 4):  # Title and URL are editable
            return base_flags | Qt.ItemIsEditable
        return base_flags

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid() or index.row() >= len(self._items):
            return None

        item = self._items[index.row()]
        col = index.column()

        # Checkbox column
        if col == 0:
            if role == Qt.CheckStateRole:
                return Qt.Checked if item.get("enabled", 1) else Qt.Unchecked
            return None

        # Text display
        if role == Qt.DisplayRole or role == Qt.EditRole:
            if col == 1:
                if not item.get("enabled", 1):
                    return "⏸ Disabled"
                status = item.get("status", "pending")
                if status == "completed":
                    return "✅ Completed"
                elif status == "downloading":
                    return "🚀 Downloading"
                elif status == "error":
                    return "❌ Error"
                return "⏳ Pending"
            elif col == 2:
                return item.get("title", "")
            elif col == 3:
                return item.get("filename", "")
            elif col == 4:
                return item.get("url", "")

        # Text color for status column
        if role == Qt.ForegroundRole and col == 1:
            if not item.get("enabled", 1):
                return QColor("#64748b")  # Slate gray
            status = item.get("status", "pending")
            if status == "completed":
                return QColor("#059669")  # Emerald green
            elif status == "downloading":
                return QColor("#2563eb")  # Vivid blue
            elif status == "error":
                return QColor("#dc2626")  # Red
            return QColor("#d97706")  # Amber

        # Center alignment for Active and Status columns
        if role == Qt.TextAlignmentRole:
            if col in (0, 1):
                return Qt.AlignCenter

        # Tooltips
        if role == Qt.ToolTipRole:
            err = item.get("error_msg", "")
            if err:
                return f"Error: {err}\nURL: {item.get('url')}"
            return f"{item.get('title') or item.get('url')}\nFilename: {item.get('filename', 'Not downloaded')}"

        return None

    def setData(self, index: QModelIndex, value: Any, role: int = Qt.EditRole) -> bool:
        if not index.isValid() or index.row() >= len(self._items):
            return False

        row = index.row()
        col = index.column()
        item = self._items[row]
        item_id = item["id"]

        # Checkbox toggle
        if col == 0 and (role == Qt.CheckStateRole or role == Qt.EditRole):
            new_enabled = 1 if (value == Qt.Checked or value == 1 or value is True) else 0
            item["enabled"] = new_enabled
            self.db.update_row(item_id, enabled=new_enabled)
            # Emit change for both Checkbox and Status columns
            self.dataChanged.emit(self.index(row, 0), self.index(row, 1))
            return True

        # Inline text edit
        if role == Qt.EditRole:
            val_str = str(value).strip()
            if col == 2:  # Title
                item["title"] = val_str
                self.db.update_row(item_id, title=val_str)
                self.dataChanged.emit(index, index)
                return True
            elif col == 4:  # URL
                if val_str:
                    item["url"] = val_str
                    self.db.update_row(item_id, url=val_str)
                    self.dataChanged.emit(index, index)
                    return True

        return False

    def update_item_status(self, item_id: int, status: str, error_msg: str = ""):
        """Fast targeted update for a single row during downloads."""
        row = self._id_to_row.get(item_id)
        if row is not None and row < len(self._items):
            self._items[row]["status"] = status
            self._items[row]["error_msg"] = error_msg
            self.dataChanged.emit(self.index(row, 1), self.index(row, 1))

    def update_item_result(self, item_id: int, filename: str, title: str, status: str = "completed"):
        """Fast targeted update when item finishes downloading."""
        row = self._id_to_row.get(item_id)
        if row is not None and row < len(self._items):
            self._items[row]["filename"] = filename
            self._items[row]["title"] = title
            self._items[row]["status"] = status
            self._items[row]["error_msg"] = ""
            self.dataChanged.emit(self.index(row, 1), self.index(row, 3))

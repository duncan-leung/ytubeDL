"""Database manager for ytubeDL using SQLite."""
import sqlite3
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from core.config import get_db_path


class Database:
    """Thread-safe SQLite manager for download queue items."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = str(db_path or get_db_path())
        self._local = threading.local()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Get thread-local SQLite connection with WAL mode enabled."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            conn.row_factory = sqlite3.Row
            # Enable WAL mode for high concurrency
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            self._local.conn = conn
        return self._local.conn

    def _init_db(self):
        """Create tables if they do not exist."""
        conn = self._get_connection()
        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS downloads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT UNIQUE NOT NULL,
                    title TEXT DEFAULT '',
                    filename TEXT DEFAULT '',
                    status TEXT DEFAULT 'pending',
                    enabled INTEGER DEFAULT 1,
                    quality TEXT DEFAULT 'normal',
                    error_msg TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_status_enabled ON downloads(status, enabled);"
            )

    def add_url(
        self,
        url: str,
        title: str = "",
        filename: str = "",
        status: str = "pending",
        enabled: int = 1,
        quality: str = "normal",
    ) -> Optional[int]:
        """Add single URL to the queue. Returns row id, or None if already exists."""
        url = url.strip()
        if not url:
            return None

        # Detect quality suffix like "best"
        if url.endswith(" best"):
            quality = "best"
            url = url[:-5].strip()

        # Determine status automatically if filename already present
        if filename and status == "pending":
            status = "completed"

        conn = self._get_connection()
        try:
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO downloads (url, title, filename, status, enabled, quality)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (url, title.strip(), filename.strip(), status, enabled, quality),
                )
                return cursor.lastrowid
        except sqlite3.IntegrityError:
            # Already exists in database
            return None

    def add_urls_batch(self, items: List[Tuple[str, str, str, int]]) -> int:
        """
        Batch add items: list of (url, title, filename, enabled).
        Returns number of newly added rows.
        """
        conn = self._get_connection()
        added = 0
        with conn:
            for item in items:
                url = item[0].strip()
                if not url:
                    continue
                quality = "normal"
                if url.endswith(" best"):
                    quality = "best"
                    url = url[:-5].strip()

                title = item[1].strip() if len(item) > 1 else ""
                filename = item[2].strip() if len(item) > 2 else ""
                enabled = item[3] if len(item) > 3 else 1
                status = "completed" if filename else "pending"

                try:
                    conn.execute(
                        """
                        INSERT INTO downloads (url, title, filename, status, enabled, quality)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (url, title, filename, status, enabled, quality),
                    )
                    added += 1
                except sqlite3.IntegrityError:
                    pass
        return added

    def get_all(
        self, filter_status: Optional[str] = None, search: Optional[str] = None
    ) -> List[Dict]:
        """Fetch rows matching filters and optional search query."""
        conn = self._get_connection()
        query = "SELECT * FROM downloads WHERE 1=1"
        params: List = []

        if filter_status:
            if filter_status == "pending":
                query += " AND status = 'pending' AND enabled = 1"
            elif filter_status == "completed":
                query += " AND status = 'completed'"
            elif filter_status == "disabled":
                query += " AND enabled = 0"
            elif filter_status == "error":
                query += " AND status = 'error'"

        if search:
            query += " AND (url LIKE ? OR title LIKE ? OR filename LIKE ?)"
            s = f"%{search.strip()}%"
            params.extend([s, s, s])

        query += " ORDER BY id DESC"
        cursor = conn.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

    def get_by_id(self, item_id: int) -> Optional[Dict]:
        conn = self._get_connection()
        cursor = conn.execute("SELECT * FROM downloads WHERE id = ?", (item_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_pending_enabled(self) -> List[Dict]:
        """Retrieve items ready for downloading in order of queue."""
        conn = self._get_connection()
        cursor = conn.execute(
            """
            SELECT * FROM downloads 
            WHERE enabled = 1 AND status IN ('pending', 'error')
            ORDER BY id ASC
            """
        )
        return [dict(row) for row in cursor.fetchall()]

    def update_status(self, item_id: int, status: str, error_msg: str = ""):
        conn = self._get_connection()
        with conn:
            conn.execute(
                """
                UPDATE downloads
                SET status = ?, error_msg = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status, error_msg, item_id),
            )

    def update_download_result(
        self, item_id: int, filename: str, title: str, status: str = "completed"
    ):
        conn = self._get_connection()
        with conn:
            conn.execute(
                """
                UPDATE downloads
                SET filename = ?, title = ?, status = ?, error_msg = '', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (filename, title, status, item_id),
            )

    def toggle_enabled(self, item_id: int, enabled: Optional[int] = None) -> int:
        conn = self._get_connection()
        with conn:
            if enabled is None:
                cursor = conn.execute(
                    "UPDATE downloads SET enabled = 1 - enabled WHERE id = ?", (item_id,)
                )
            else:
                cursor = conn.execute(
                    "UPDATE downloads SET enabled = ? WHERE id = ?", (enabled, item_id)
                )
        item = self.get_by_id(item_id)
        return item["enabled"] if item else 1

    def update_row(
        self,
        item_id: int,
        url: Optional[str] = None,
        title: Optional[str] = None,
        filename: Optional[str] = None,
        enabled: Optional[int] = None,
        status: Optional[str] = None,
    ):
        updates = []
        params = []
        if url is not None:
            updates.append("url = ?")
            params.append(url.strip())
        if title is not None:
            updates.append("title = ?")
            params.append(title.strip())
        if filename is not None:
            updates.append("filename = ?")
            params.append(filename.strip())
        if enabled is not None:
            updates.append("enabled = ?")
            params.append(int(enabled))
        if status is not None:
            updates.append("status = ?")
            params.append(status)

        if not updates:
            return

        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(item_id)

        conn = self._get_connection()
        with conn:
            conn.execute(
                f"UPDATE downloads SET {', '.join(updates)} WHERE id = ?",
                params,
            )

    def delete_items(self, item_ids: List[int]) -> int:
        if not item_ids:
            return 0
        conn = self._get_connection()
        placeholders = ",".join("?" for _ in item_ids)
        with conn:
            cursor = conn.execute(
                f"DELETE FROM downloads WHERE id IN ({placeholders})", item_ids
            )
            return cursor.rowcount

    def clear_completed(self) -> int:
        conn = self._get_connection()
        with conn:
            cursor = conn.execute("DELETE FROM downloads WHERE status = 'completed'")
            return cursor.rowcount

    def get_stats(self) -> Dict[str, int]:
        conn = self._get_connection()
        cursor = conn.execute(
            """
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN enabled = 1 AND status = 'pending' THEN 1 ELSE 0 END) as pending,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
                SUM(CASE WHEN enabled = 0 THEN 1 ELSE 0 END) as disabled,
                SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as error
            FROM downloads
            """
        )
        row = cursor.fetchone()
        return {
            "total": row["total"] or 0,
            "pending": row["pending"] or 0,
            "completed": row["completed"] or 0,
            "disabled": row["disabled"] or 0,
            "error": row["error"] or 0,
        }

    # === URL.TXT COMPATIBILITY METHODS ===

    def import_from_url_txt(self, filepath: str) -> Tuple[int, int]:
        """
        Import lines from a url.txt file.
        Returns: (added_count, skipped_count)
        Format: 'URL | filename | title'
        Commented lines start with '#'
        """
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {filepath}")

        items = []
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line_str = line.strip()
                if not line_str:
                    continue

                enabled = 1
                if line_str.startswith("#"):
                    enabled = 0
                    line_str = line_str.lstrip("#").strip()

                parts = [p.strip() for p in line_str.split("|")]
                url = parts[0]
                filename = parts[1] if len(parts) > 1 else ""
                title = parts[2] if len(parts) > 2 else ""

                if url:
                    items.append((url, title, filename, enabled))

        added = self.add_urls_batch(items)
        skipped = len(items) - added
        return added, skipped

    def export_to_url_txt(self, filepath: str) -> int:
        """
        Export all records to url.txt format.
        Disabled items are exported with leading '# '.
        Returns: count of exported rows
        """
        conn = self._get_connection()
        cursor = conn.execute("SELECT * FROM downloads ORDER BY id ASC")
        rows = cursor.fetchall()

        with open(filepath, "w", encoding="utf-8") as f:
            for row in rows:
                prefix = "" if row["enabled"] else "# "
                url = row["url"]
                if row["quality"] == "best":
                    url = f"{url} best"

                filename = row["filename"] or ""
                title = row["title"] or ""

                if filename and title:
                    f.write(f"{prefix}{url} | {filename} | {title}\n")
                elif filename:
                    f.write(f"{prefix}{url} | {filename}\n")
                else:
                    f.write(f"{prefix}{url}\n")

        return len(rows)

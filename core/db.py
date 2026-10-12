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

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS channels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    channel_url TEXT NOT NULL,
                    content_type TEXT NOT NULL DEFAULT 'videos',
                    folder TEXT NOT NULL,
                    check_limit INTEGER DEFAULT 20,
                    auto_download INTEGER DEFAULT 1,
                    quality TEXT DEFAULT 'normal',
                    numbering INTEGER DEFAULT 0,
                    download_thumbnail INTEGER DEFAULT 0,
                    use_title INTEGER DEFAULT 1,
                    enabled INTEGER DEFAULT 1,
                    last_checked TIMESTAMP,
                    sort_order INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            # Ensure sort_order column exists on existing databases
            try:
                conn.execute("ALTER TABLE channels ADD COLUMN sort_order INTEGER DEFAULT 0;")
            except sqlite3.OperationalError:
                pass
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS channel_videos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
                    video_id TEXT NOT NULL,
                    video_url TEXT NOT NULL,
                    title TEXT DEFAULT '',
                    filename TEXT DEFAULT '',
                    thumbnail_url TEXT DEFAULT '',
                    status TEXT DEFAULT 'pending',
                    download_date TIMESTAMP,
                    error_msg TEXT DEFAULT '',
                    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(channel_id, video_id)
                );
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_channel_videos_lookup ON channel_videos(channel_id, video_id);"
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

    # === CHANNELS & FEEDS METHODS ===

    def add_channel(
        self,
        name: str,
        channel_id: str,
        channel_url: str,
        content_type: str = "videos",
        folder: str = "",
        check_limit: int = 20,
        auto_download: int = 1,
        quality: str = "normal",
        numbering: int = 0,
        download_thumbnail: int = 0,
        use_title: int = 1,
        enabled: int = 1,
    ) -> Optional[int]:
        """Add a new channel subscription. Returns channel ID, or None if error."""
        conn = self._get_connection()
        with conn:
            cursor_order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) FROM channels;")
            next_order = (cursor_order.fetchone()[0] or 0) + 1

            cursor = conn.execute(
                """
                INSERT INTO channels (
                    name, channel_id, channel_url, content_type, folder,
                    check_limit, auto_download, quality, numbering,
                    download_thumbnail, use_title, enabled, sort_order
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name.strip(),
                    channel_id.strip(),
                    channel_url.strip(),
                    content_type.strip(),
                    folder.strip(),
                    int(check_limit),
                    int(auto_download),
                    quality.strip(),
                    int(numbering),
                    int(download_thumbnail),
                    int(use_title),
                    int(enabled),
                    int(next_order),
                ),
            )
            return cursor.lastrowid

    def update_channel(
        self,
        channel_id: int,
        name: Optional[str] = None,
        channel_url: Optional[str] = None,
        content_type: Optional[str] = None,
        folder: Optional[str] = None,
        check_limit: Optional[int] = None,
        auto_download: Optional[int] = None,
        quality: Optional[str] = None,
        numbering: Optional[int] = None,
        download_thumbnail: Optional[int] = None,
        use_title: Optional[int] = None,
        enabled: Optional[int] = None,
        channel_id_code: Optional[str] = None,
        sort_order: Optional[int] = None,
    ):
        """Update existing channel configuration."""
        updates = []
        params = []
        if name is not None:
            updates.append("name = ?")
            params.append(name.strip())
        if channel_id_code is not None:
            updates.append("channel_id = ?")
            params.append(channel_id_code.strip())
        if channel_url is not None:
            updates.append("channel_url = ?")
            params.append(channel_url.strip())
        if content_type is not None:
            updates.append("content_type = ?")
            params.append(content_type.strip())
        if folder is not None:
            updates.append("folder = ?")
            params.append(folder.strip())
        if check_limit is not None:
            updates.append("check_limit = ?")
            params.append(int(check_limit))
        if auto_download is not None:
            updates.append("auto_download = ?")
            params.append(int(auto_download))
        if quality is not None:
            updates.append("quality = ?")
            params.append(quality.strip())
        if numbering is not None:
            updates.append("numbering = ?")
            params.append(int(numbering))
        if download_thumbnail is not None:
            updates.append("download_thumbnail = ?")
            params.append(int(download_thumbnail))
        if use_title is not None:
            updates.append("use_title = ?")
            params.append(int(use_title))
        if enabled is not None:
            updates.append("enabled = ?")
            params.append(int(enabled))
        if sort_order is not None:
            updates.append("sort_order = ?")
            params.append(int(sort_order))

        if not updates:
            return

        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(channel_id)

        conn = self._get_connection()
        with conn:
            conn.execute(
                f"UPDATE channels SET {', '.join(updates)} WHERE id = ?",
                params,
            )

    def delete_channel(self, channel_id: int) -> bool:
        """Delete channel and cascade delete all its video history."""
        conn = self._get_connection()
        with conn:
            conn.execute("DELETE FROM channel_videos WHERE channel_id = ?", (channel_id,))
            cursor = conn.execute("DELETE FROM channels WHERE id = ?", (channel_id,))
            return cursor.rowcount > 0

    def get_channels(self, enabled_only: bool = False) -> List[Dict]:
        """Fetch all subscribed channels ordered by sort_order."""
        conn = self._get_connection()
        query = "SELECT * FROM channels"
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY sort_order ASC, id ASC"
        cursor = conn.execute(query)
        return [dict(row) for row in cursor.fetchall()]

    def reorder_channels(self, ordered_channel_ids: List[int]):
        """Persist explicit sequence of channel IDs as sort_order."""
        conn = self._get_connection()
        with conn:
            for idx, cid in enumerate(ordered_channel_ids):
                conn.execute("UPDATE channels SET sort_order = ? WHERE id = ?", (idx, cid))

    def move_channel(self, channel_id: int, direction: int) -> bool:
        """
        Move a channel up (direction=-1) or down (direction=1).
        Returns True if moved, False if already at edge.
        """
        channels = self.get_channels()
        ids = [c["id"] for c in channels]
        if channel_id not in ids:
            return False
        curr_idx = ids.index(channel_id)
        new_idx = curr_idx + direction
        if new_idx < 0 or new_idx >= len(ids):
            return False
        ids[curr_idx], ids[new_idx] = ids[new_idx], ids[curr_idx]
        self.reorder_channels(ids)
        return True

    def get_channel(self, channel_id: int) -> Optional[Dict]:
        """Fetch single channel by primary key ID."""
        conn = self._get_connection()
        cursor = conn.execute("SELECT * FROM channels WHERE id = ?", (channel_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def update_channel_last_checked(self, channel_id: int):
        conn = self._get_connection()
        with conn:
            conn.execute(
                "UPDATE channels SET last_checked = CURRENT_TIMESTAMP WHERE id = ?",
                (channel_id,),
            )

    # === CHANNEL VIDEOS (CLIPS & HISTORY) ===

    def add_channel_videos(
        self, channel_id: int, videos: List[Dict]
    ) -> Tuple[int, List[Dict]]:
        """
        Record newly retrieved videos for a channel.
        Skips already existing videos.
        Returns (new_count, list_of_new_video_records).
        """
        conn = self._get_connection()
        added_records = []
        with conn:
            for v in videos:
                vid = str(v.get("videoId") or "").strip()
                if not vid:
                    continue
                url = v.get("url") or f"https://www.youtube.com/watch?v={vid}"
                title = (v.get("title") or "").strip()
                thumbnail_url = (v.get("thumbnail_url") or "").strip()

                try:
                    cursor = conn.execute(
                        """
                        INSERT INTO channel_videos (
                            channel_id, video_id, video_url, title, thumbnail_url, status
                        )
                        VALUES (?, ?, ?, ?, ?, 'pending')
                        """,
                        (channel_id, vid, url, title, thumbnail_url),
                    )
                    row_id = cursor.lastrowid
                    added_records.append({
                        "id": row_id,
                        "channel_id": channel_id,
                        "video_id": vid,
                        "video_url": url,
                        "title": title,
                        "thumbnail_url": thumbnail_url,
                        "status": "pending",
                    })
                except sqlite3.IntegrityError:
                    # Video already retrieved previously
                    pass

        return len(added_records), added_records

    def get_channel_videos(
        self, channel_id: int, status: Optional[str] = None
    ) -> List[Dict]:
        """Fetch tracked clips for a specific channel."""
        conn = self._get_connection()
        query = "SELECT * FROM channel_videos WHERE channel_id = ?"
        params: List = [channel_id]
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY id ASC"
        cursor = conn.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

    def get_all_channel_videos(self) -> List[Dict]:
        """Fetch all video records across all channels."""
        conn = self._get_connection()
        cursor = conn.execute("SELECT * FROM channel_videos ORDER BY id ASC")
        return [dict(row) for row in cursor.fetchall()]

    def update_channel_video_status(
        self, video_row_id: int, status: str, error_msg: str = ""
    ):
        conn = self._get_connection()
        with conn:
            conn.execute(
                """
                UPDATE channel_videos
                SET status = ?, error_msg = ?
                WHERE id = ?
                """,
                (status, error_msg, video_row_id),
            )

    def update_channel_video_downloaded(
        self,
        video_row_id: int,
        filename: str,
        title: str = "",
        status: str = "completed",
    ):
        conn = self._get_connection()
        with conn:
            if title:
                conn.execute(
                    """
                    UPDATE channel_videos
                    SET filename = ?, title = ?, status = ?, download_date = CURRENT_TIMESTAMP, error_msg = ''
                    WHERE id = ?
                    """,
                    (filename, title, status, video_row_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE channel_videos
                    SET filename = ?, status = ?, download_date = CURRENT_TIMESTAMP, error_msg = ''
                    WHERE id = ?
                    """,
                    (filename, status, video_row_id),
                )

    def delete_channel_video_rows(self, row_ids: List[int]) -> int:
        """Delete specific video rows (allows user to purge old/unneeded rows)."""
        if not row_ids:
            return 0
        conn = self._get_connection()
        placeholders = ",".join("?" for _ in row_ids)
        with conn:
            cursor = conn.execute(
                f"DELETE FROM channel_videos WHERE id IN ({placeholders})", row_ids
            )
            return cursor.rowcount

    def prune_channel_videos(self, channel_id: int, keep_latest: int = 50) -> int:
        """Delete older rows exceeding keep_latest count for a channel."""
        if keep_latest <= 0:
            return 0
        conn = self._get_connection()
        with conn:
            cursor = conn.execute(
                """
                DELETE FROM channel_videos
                WHERE channel_id = ?
                AND id NOT IN (
                    SELECT id FROM channel_videos
                    WHERE channel_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                )
                """,
                (channel_id, channel_id, keep_latest),
            )
            return cursor.rowcount

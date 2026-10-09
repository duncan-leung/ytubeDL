"""Background QThread download worker running yt-dlp matching download_urls.py."""
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional
from PyQt5.QtCore import QThread, pyqtSignal

from core.bin_helper import find_ffmpeg, find_ytdlp
from core.config import Config
from core.db import Database


class DownloadWorker(QThread):
    """Worker thread that executes yt-dlp sequentially for queued items."""

    # Qt Signals for UI updates
    log_message = pyqtSignal(str)  # Line of stdout/stderr
    item_started = pyqtSignal(int, str)  # (item_id, url)
    item_progress = pyqtSignal(int, str)  # (item_id, progress_text)
    item_finished = pyqtSignal(int, str, str)  # (item_id, filename, title)
    item_error = pyqtSignal(int, str)  # (item_id, error_text)
    overall_progress = pyqtSignal(int, int)  # (completed_in_batch, total_in_batch)
    queue_finished = pyqtSignal(int)  # (total_downloaded)

    def __init__(
        self,
        db: Database,
        config: Config,
        target_item_ids: Optional[List[int]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.db = db
        self.config = config
        self.target_item_ids = target_item_ids
        self._is_stopped = False
        self._current_process: Optional[subprocess.Popen] = None

    def stop(self):
        """Request graceful stop of the download queue."""
        self._is_stopped = True
        if self._current_process and self._current_process.poll() is None:
            try:
                self._current_process.terminate()
            except Exception:
                pass

    def _using_cookies(self) -> bool:
        browser = self.config.cookies_from_browser
        cookie_file = self.config.cookies_file
        return bool(browser and browser != "none") or bool(
            cookie_file and os.path.isfile(cookie_file)
        )

    def _build_cookies_args(self) -> List[str]:
        browser = self.config.cookies_from_browser
        cookie_file = self.config.cookies_file

        if browser and browser != "none":
            return ["--cookies-from-browser", browser]
        if cookie_file and os.path.isfile(cookie_file):
            return ["--cookies", cookie_file]
        return []

    def _build_js_args(self) -> List[str]:
        if self.config.enable_js_runtime:
            return ["--js-runtimes", "node", "--remote-components", "ejs:github"]
        return []

    def _build_player_client_args(self) -> List[str]:
        if self._using_cookies():
            return [
                "--extractor-args",
                "youtube:player_client=web_safari,web_embedded,-tv_downgraded",
            ]
        return ["--extractor-args", "youtube:player_client=android"]

    def _build_ffmpeg_args(self, ffmpeg_bin: Optional[str]) -> List[str]:
        if ffmpeg_bin:
            ffmpeg_dir = str(Path(ffmpeg_bin).parent)
            return ["--ffmpeg-location", ffmpeg_dir]
        return []

    def _sanitize_title(self, title: str) -> str:
        safe = "".join(c if c.isalnum() or c in "._- " else "_" for c in title)
        safe = safe.strip().rstrip(".")
        return safe or "video"

    def run(self):
        """Main thread loop."""
        ytdlp_bin = find_ytdlp(self.config.get("custom_ytdlp_path"))
        if not ytdlp_bin:
            self.log_message.emit("❌ Error: yt-dlp executable could not be found.")
            self.log_message.emit(
                "💡 Please check Settings or click 'Update/Download yt-dlp' to install it."
            )
            self.queue_finished.emit(0)
            return

        ffmpeg_bin = find_ffmpeg(self.config.get("custom_ffmpeg_path"))
        output_dir = Path(self.config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Retrieve items to process
        if self.target_item_ids:
            items = [self.db.get_by_id(i) for i in self.target_item_ids]
            items = [i for i in items if i is not None]
        else:
            items = self.db.get_pending_enabled()

        total = len(items)
        if total == 0:
            self.log_message.emit("ℹ️ Queue is empty or all items are already downloaded/disabled.")
            self.queue_finished.emit(0)
            return

        self.log_message.emit(f"🚀 Starting download batch: {total} item(s)...")
        downloaded_count = 0

        for idx, item in enumerate(items):
            if self._is_stopped:
                self.log_message.emit("⏹ Download queue stopped by user.")
                break

            item_id = item["id"]
            url = item["url"]
            quality = item.get("quality", "normal")

            self.db.update_status(item_id, "downloading")
            self.item_started.emit(item_id, url)
            self.overall_progress.emit(idx, total)
            self.log_message.emit(f"\n[{idx + 1}/{total}] 🔄 Processing: {url}")

            # 1. Fetch title first (matching download_urls.py)
            fetched_title = ""
            new_filename = ""
            try:
                title_cmd = [ytdlp_bin, "--no-update"]
                title_cmd += self._build_cookies_args()
                title_cmd += self._build_js_args()
                title_cmd += self._build_player_client_args()
                title_cmd += ["--print", "title", "--quiet", url]

                self.log_message.emit(f"🔎 Fetching title for {url}...")
                title_proc = subprocess.run(
                    title_cmd,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=25,
                )
                fetched_title = title_proc.stdout.strip()
                safe_title = self._sanitize_title(fetched_title)
                new_filename = f"{safe_title}.mp4"
                self.log_message.emit(f"📝 Title: {fetched_title}")
            except Exception as e:
                self.log_message.emit(f"⚠️ Title fetch warning: {e}")
                if "watch?v=" in url:
                    vid_id = url.split("watch?v=")[1].split("&")[0]
                    new_filename = f"{vid_id}.mp4"
                elif "youtu.be/" in url:
                    vid_id = url.split("youtu.be/")[1].split("?")[0]
                    new_filename = f"{vid_id}.mp4"
                else:
                    new_filename = f"video_{item_id}.mp4"
                fetched_title = new_filename

            if self._is_stopped:
                self.db.update_status(item_id, "pending")
                break

            # 2. Download video
            output_path = str(output_dir / new_filename)
            self.log_message.emit(f"📥 Downloading to: {output_path}")

            dl_cmd = [ytdlp_bin, "--no-update"]
            dl_cmd += self._build_cookies_args()
            dl_cmd += self._build_js_args()
            dl_cmd += self._build_player_client_args()
            dl_cmd += self._build_ffmpeg_args(ffmpeg_bin)

            # Format selection
            if quality == "best":
                dl_cmd += ["--format", "bestvideo+bestaudio/best"]
            else:
                dl_cmd += [
                    "--format",
                    "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                ]

            dl_cmd += ["--merge-output-format", "mp4"]
            dl_cmd += ["-o", output_path]
            dl_cmd += [url]

            try:
                self._current_process = subprocess.Popen(
                    dl_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    universal_newlines=True,
                )

                # Stream stdout line by line
                if self._current_process.stdout:
                    for line in self._current_process.stdout:
                        if self._is_stopped:
                            self._current_process.terminate()
                            break
                        line_clean = line.strip()
                        if line_clean:
                            self.log_message.emit(line_clean)
                            # Extract download progress percentage if present
                            if "[download]" in line_clean and "%" in line_clean:
                                match = re.search(r"(\d+(\.\d+)?%)", line_clean)
                                if match:
                                    self.item_progress.emit(item_id, match.group(1))

                self._current_process.wait()

                if self._is_stopped:
                    self.db.update_status(item_id, "pending")
                    break

                if self._current_process.returncode == 0:
                    self.db.update_download_result(
                        item_id, new_filename, fetched_title, "completed"
                    )
                    self.item_finished.emit(item_id, new_filename, fetched_title)
                    self.log_message.emit(f"✅ Download completed: {new_filename}")
                    downloaded_count += 1
                else:
                    err_msg = f"yt-dlp exited with code {self._current_process.returncode}"
                    self.db.update_status(item_id, "error", err_msg)
                    self.item_error.emit(item_id, err_msg)
                    self.log_message.emit(f"❌ Failed: {err_msg}")

            except Exception as e:
                err_msg = str(e)
                self.db.update_status(item_id, "error", err_msg)
                self.item_error.emit(item_id, err_msg)
                self.log_message.emit(f"❌ Exception during download: {err_msg}")

            finally:
                self._current_process = None

            # Rate-limiting sleep between downloads
            delay = self.config.delay_between
            if idx < total - 1 and not self._is_stopped and delay > 0:
                self.log_message.emit(f"⏳ Sleeping {delay}s to avoid rate limits...")
                for _ in range(delay * 2):
                    if self._is_stopped:
                        break
                    time.sleep(0.5)

        self.overall_progress.emit(total, total)
        self.log_message.emit(
            f"\n🏁 Finished! {downloaded_count} video(s) downloaded successfully."
        )
        self.queue_finished.emit(downloaded_count)

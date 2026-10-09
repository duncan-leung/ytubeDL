"""Background QThread download worker running yt-dlp matching download_urls.py."""
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from PyQt5.QtCore import QThread, pyqtSignal

from core.bin_helper import find_ffmpeg, find_ytdlp
from core.config import Config
from core.db import Database


def get_installed_browsers() -> List[str]:
    """Detect installed web browsers that yt-dlp can extract cookies from."""
    found = []
    if sys.platform == "win32":
        local_app = os.environ.get("LOCALAPPDATA", "")
        app_data = os.environ.get("APPDATA", "")
        prog_files = os.environ.get("ProgramFiles", "C:\\Program Files")
        prog_files_x86 = os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")

        checks = {
            "edge": [
                Path(local_app) / "Microsoft" / "Edge" / "User Data",
                Path(prog_files_x86) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                Path(prog_files) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            ],
            "chrome": [
                Path(local_app) / "Google" / "Chrome" / "User Data",
                Path(prog_files) / "Google" / "Chrome" / "Application" / "chrome.exe",
                Path(prog_files_x86) / "Google" / "Chrome" / "Application" / "chrome.exe",
            ],
            "firefox": [
                Path(app_data) / "Mozilla" / "Firefox" / "Profiles",
                Path(prog_files) / "Mozilla Firefox" / "firefox.exe",
                Path(prog_files_x86) / "Mozilla Firefox" / "firefox.exe",
            ],
            "brave": [
                Path(local_app) / "BraveSoftware" / "Brave-Browser" / "User Data",
            ],
        }
        for b_name, paths in checks.items():
            if any(p.exists() for p in paths):
                found.append(b_name)
    elif sys.platform == "darwin":
        mac_checks = {
            "chrome": [
                Path("/Applications/Google Chrome.app"),
                Path.home() / "Library/Application Support/Google/Chrome",
            ],
            "safari": [
                Path("/Applications/Safari.app"),
                Path.home() / "Library/Safari",
            ],
            "firefox": [
                Path("/Applications/Firefox.app"),
                Path.home() / "Library/Application Support/Firefox",
            ],
            "edge": [Path("/Applications/Microsoft Edge.app")],
            "brave": [Path("/Applications/Brave Browser.app")],
        }
        for b_name, paths in mac_checks.items():
            if any(p.exists() for p in paths):
                found.append(b_name)
    return found


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

    def _resolve_browser(self) -> Optional[str]:
        """Resolve browser choice, auto-detecting available browser if set to auto or missing."""
        pref = self.config.cookies_from_browser
        if not pref or pref == "none":
            return None

        avail = get_installed_browsers()
        if pref == "auto":
            if avail:
                for p in ["chrome", "edge", "safari", "firefox", "brave"]:
                    if p in avail:
                        return p
                return avail[0]
            return None

        # If a specific browser was set but is not installed, fallback to an available one
        if avail:
            if pref in avail:
                return pref
            for p in ["chrome", "edge", "safari", "firefox", "brave"]:
                if p in avail:
                    return p
            return avail[0]

        return None

    def _build_cookies_args(
        self, browser_override: Optional[str] = None, allow_cookies: bool = True
    ) -> List[str]:
        if not allow_cookies:
            return []
        browser = browser_override if browser_override is not None else self._resolve_browser()
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

    def _build_player_client_args(self, using_cookies: bool) -> List[str]:
        if using_cookies:
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

    def _fetch_title(self, ytdlp_bin: str, url: str, browser: Optional[str]) -> str:
        """Attempt to fetch video title, falling back to no cookies if cookies fail."""
        trials = [True, False] if browser else [False]
        for use_cookies in trials:
            try:
                cmd = [ytdlp_bin, "--no-update"]
                cmd += self._build_cookies_args(browser, allow_cookies=use_cookies)
                cmd += self._build_js_args()
                cmd += self._build_player_client_args(using_cookies=use_cookies)
                cmd += ["--print", "title", "--quiet", url]

                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=25,
                )
                title = proc.stdout.strip()
                if title:
                    return title
            except Exception:
                if use_cookies and browser:
                    continue
        return ""

    def _execute_download(
        self,
        ytdlp_bin: str,
        ffmpeg_bin: Optional[str],
        output_path: str,
        url: str,
        quality: str,
        item_id: int,
        browser: Optional[str],
        use_cookies: bool,
    ) -> Tuple[int, str]:
        """Run yt-dlp download process, streaming stdout and returning exit code and full log."""
        dl_cmd = [ytdlp_bin, "--no-update"]
        dl_cmd += self._build_cookies_args(browser, allow_cookies=use_cookies)
        dl_cmd += self._build_js_args()
        dl_cmd += self._build_player_client_args(using_cookies=use_cookies)
        dl_cmd += self._build_ffmpeg_args(ffmpeg_bin)

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

        output_lines = []
        try:
            self._current_process = subprocess.Popen(
                dl_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True,
            )

            if self._current_process.stdout:
                for line in self._current_process.stdout:
                    if self._is_stopped:
                        self._current_process.terminate()
                        break
                    line_clean = line.strip()
                    if line_clean:
                        output_lines.append(line_clean)
                        self.log_message.emit(line_clean)
                        if "[download]" in line_clean and "%" in line_clean:
                            match = re.search(r"(\d+(\.\d+)?%)", line_clean)
                            if match:
                                self.item_progress.emit(item_id, match.group(1))

            self._current_process.wait()
            ret = self._current_process.returncode
        except Exception as e:
            ret = -1
            output_lines.append(str(e))
        finally:
            self._current_process = None

        return ret, "\n".join(output_lines)

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

            # Resolve active browser
            active_browser = self._resolve_browser()
            if active_browser:
                self.log_message.emit(f"🍪 Cookie source: {active_browser}")
            else:
                self.log_message.emit("🍪 Cookie source: None (direct download)")

            # 1. Fetch title first
            self.log_message.emit(f"🔎 Fetching title for {url}...")
            fetched_title = self._fetch_title(ytdlp_bin, url, active_browser)
            if fetched_title:
                safe_title = self._sanitize_title(fetched_title)
                new_filename = f"{safe_title}.mp4"
                self.log_message.emit(f"📝 Title: {fetched_title}")
            else:
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

            use_cookies = bool(
                active_browser
                or (self.config.cookies_file and os.path.isfile(self.config.cookies_file))
            )
            ret_code, log_out = self._execute_download(
                ytdlp_bin,
                ffmpeg_bin,
                output_path,
                url,
                quality,
                item_id,
                active_browser,
                use_cookies,
            )

            # Auto-fallback: if download failed while using cookies, retry without cookies!
            cookie_error_keywords = [
                "cookie",
                "browser",
                "could not find",
                "sqlite",
                "permission",
                "locked",
            ]
            failed_due_to_cookies = use_cookies and (
                ret_code != 0
                and any(kw in log_out.lower() for kw in cookie_error_keywords)
            )

            if not self._is_stopped and ret_code != 0 and (failed_due_to_cookies or use_cookies):
                self.log_message.emit(
                    f"⚠️ Notice: Cookie extraction from '{active_browser or 'browser'}' failed or unavailable."
                )
                self.log_message.emit(
                    "🔄 Automatically retrying download directly without cookies..."
                )
                ret_code, log_out = self._execute_download(
                    ytdlp_bin,
                    ffmpeg_bin,
                    output_path,
                    url,
                    quality,
                    item_id,
                    active_browser,
                    use_cookies=False,
                )

            if self._is_stopped:
                self.db.update_status(item_id, "pending")
                break

            if ret_code == 0:
                self.db.update_download_result(
                    item_id, new_filename, fetched_title, "completed"
                )
                self.item_finished.emit(item_id, new_filename, fetched_title)
                self.log_message.emit(f"✅ Download completed: {new_filename}")
                downloaded_count += 1
            else:
                err_msg = f"yt-dlp exited with code {ret_code}"
                self.db.update_status(item_id, "error", err_msg)
                self.item_error.emit(item_id, err_msg)
                self.log_message.emit(f"❌ Failed: {err_msg}")

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

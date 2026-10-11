"""Background QThread worker for checking channels and downloading new clips."""
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse
from PyQt5.QtCore import QThread, pyqtSignal
import requests

from core.bin_helper import find_ffmpeg, find_ytdlp
from core.config import Config
from core.db import Database
from core.downloader import get_installed_browsers
from core.youtube_extract import fetch_first_page_videos


def sanitize_filename(text: str) -> str:
    """Strip or replace characters that are invalid in filenames."""
    safe = "".join(c if c.isalnum() or c in "._- " else "_" for c in text)
    safe = safe.strip().rstrip(".")
    return safe or "clip"


class ChannelWorker(QThread):
    """Worker thread that checks channels for latest clips and downloads them if enabled."""

    log_message = pyqtSignal(str)
    channel_started = pyqtSignal(int, str)  # (channel_id, channel_name)
    channel_progress = pyqtSignal(int, str)  # (channel_id, status_text)
    channel_finished = pyqtSignal(int, int, int)  # (channel_id, newly_found, downloaded)
    video_started = pyqtSignal(int, str)  # (video_row_id, title)
    video_progress = pyqtSignal(int, str)  # (video_row_id, progress_text)
    video_finished = pyqtSignal(int, str, str)  # (video_row_id, filename, title)
    video_error = pyqtSignal(int, str)  # (video_row_id, error_text)
    overall_progress = pyqtSignal(int, int)  # (current_channel_idx, total_channels)
    all_channels_finished = pyqtSignal(int, int)  # (total_new_found, total_downloaded)

    def __init__(
        self,
        db: Database,
        config: Config,
        target_channel_ids: Optional[List[int]] = None,
        force_download: Optional[bool] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.db = db
        self.config = config
        self.target_channel_ids = target_channel_ids
        self.force_download = force_download
        self._is_stopped = False
        self._current_process: Optional[subprocess.Popen] = None

    def stop(self):
        """Request stop of the worker."""
        self._is_stopped = True
        if self._current_process and self._current_process.poll() is None:
            try:
                self._current_process.terminate()
            except Exception:
                pass

    def _resolve_browser(self) -> Optional[str]:
        pref = self.config.cookies_from_browser
        if not pref or pref == "none":
            return None
        avail = get_installed_browsers()
        if pref == "auto":
            for p in ["chrome", "edge", "safari", "firefox", "brave"]:
                if p in avail:
                    return p
            return avail[0] if avail else None
        if pref in avail:
            return pref
        return avail[0] if avail else None

    def _build_cookies_args(self, browser: Optional[str], allow_cookies: bool = True) -> List[str]:
        if not allow_cookies:
            return []
        if browser and browser != "none":
            return ["--cookies-from-browser", browser]
        cookie_file = self.config.cookies_file
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

    def _download_thumbnail(self, thumbnail_url: str, save_dir: str, title: str):
        if not thumbnail_url:
            return
        try:
            resp = requests.get(thumbnail_url, timeout=10)
            if resp.status_code == 200:
                os.makedirs(save_dir, exist_ok=True)
                fn = sanitize_filename(title)
                target = os.path.join(save_dir, f"{fn}.jpg")
                with open(target, "wb") as f:
                    f.write(resp.content)
        except Exception:
            pass

    def _execute_download(
        self,
        ytdlp_bin: str,
        ffmpeg_bin: Optional[str],
        output_path: str,
        url: str,
        quality: str,
        browser: Optional[str],
        use_cookies: bool,
        video_row_id: int,
    ) -> int:
        cmd = [ytdlp_bin, "--no-update"]
        cmd += self._build_cookies_args(browser, allow_cookies=use_cookies)
        cmd += self._build_js_args()
        cmd += self._build_player_client_args(using_cookies=use_cookies)
        if ffmpeg_bin:
            cmd += ["--ffmpeg-location", str(Path(ffmpeg_bin).parent)]

        if quality == "best":
            cmd += ["--format", "bestvideo+bestaudio/best"]
        else:
            cmd += [
                "--format",
                "bestvideo[ext=mp4][height<=480]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            ]

        cmd += ["--merge-output-format", "mp4"]
        cmd += ["-o", output_path]
        cmd += [url]

        try:
            self._current_process = subprocess.Popen(
                cmd,
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
                        self.log_message.emit(line_clean)
                        if "[download]" in line_clean and "%" in line_clean:
                            match = re.search(r"(\d+(\.\d+)?%)", line_clean)
                            if match:
                                self.video_progress.emit(video_row_id, match.group(1))

            self._current_process.wait()
            return self._current_process.returncode
        except Exception as e:
            self.log_message.emit(f"Download exception: {e}")
            return -1
        finally:
            self._current_process = None

    def run(self):
        ytdlp_bin = find_ytdlp(self.config.get("custom_ytdlp_path"))
        ffmpeg_bin = find_ffmpeg(self.config.get("custom_ffmpeg_path"))

        # Select channels to check
        if self.target_channel_ids:
            channels = [self.db.get_channel(cid) for cid in self.target_channel_ids]
            channels = [c for c in channels if c is not None]
        else:
            channels = self.db.get_channels(enabled_only=True)

        total_channels = len(channels)
        if total_channels == 0:
            self.log_message.emit("ℹ️ No channels available to check.")
            self.all_channels_finished.emit(0, 0)
            return

        self.log_message.emit(f"🚀 Checking {total_channels} channel(s)...")
        active_browser = self._resolve_browser()

        total_new_videos = 0
        total_downloaded = 0

        for ch_idx, channel in enumerate(channels):
            if self._is_stopped:
                self.log_message.emit("⏹ Operation cancelled by user.")
                break

            ch_id = channel["id"]
            ch_name = channel["name"]
            ch_url = channel["channel_url"]
            c_type = channel.get("content_type", "videos")
            limit = int(channel.get("check_limit", 20))
            folder = channel.get("folder") or self.config.output_dir

            self.overall_progress.emit(ch_idx, total_channels)
            self.channel_started.emit(ch_id, ch_name)
            self.log_message.emit(f"\n[{ch_idx + 1}/{total_channels}] 🔎 Checking: {ch_name} ({c_type})...")

            # 1. Fetch latest clips
            cookie_path = self.config.cookies_file if (self.config.cookies_file and os.path.isfile(self.config.cookies_file)) else None
            clips = fetch_first_page_videos(ch_url, content_type=c_type, cookie_path=cookie_path, limit=limit)

            # If HTML parser found nothing or if user requested a larger limit to catch up (e.g. 50, 100, 200 clips)
            if (not clips or (limit > 30 and len(clips) < limit)) and ytdlp_bin:
                if not clips:
                    self.log_message.emit("ℹ️ HTML extraction returned no items, trying yt-dlp...")
                else:
                    self.log_message.emit(f"ℹ️ Fetching up to {limit} clips using yt-dlp to catch up on history...")
                try:
                    cmd = [
                        ytdlp_bin,
                        "--flat-playlist",
                        "--dump-single-json",
                        "--playlist-end",
                        str(limit),
                        "--quiet",
                        ch_url,
                    ]
                    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=True)
                    import json
                    info = json.loads(proc.stdout)
                    entries = info.get("entries") or []
                    ytdlp_clips = []
                    for e in entries:
                        if e and e.get("id"):
                            ytdlp_clips.append({
                                "videoId": e.get("id"),
                                "title": e.get("title") or e.get("id"),
                                "url": e.get("url") or f"https://www.youtube.com/watch?v={e.get('id')}",
                                "thumbnail_url": (e.get("thumbnails") or [{}])[-1].get("url", ""),
                            })
                    if ytdlp_clips:
                        clips = ytdlp_clips
                except Exception as e:
                    self.log_message.emit(f"⚠️ yt-dlp playlist fetch notice for {ch_name}: {e}")

            if clips:
                # Process in reverse order so older unretrieved clips are processed before newest
                clips.reverse()

            new_count, new_items = self.db.add_channel_videos(ch_id, clips)
            self.db.update_channel_last_checked(ch_id)
            total_new_videos += new_count
            self.log_message.emit(f"✅ Found {len(clips)} clip(s) on feed ({new_count} new).")

            # 2. Determine download eligibility
            should_download = bool(channel.get("auto_download", 1))
            if self.force_download is not None:
                should_download = self.force_download

            ch_downloaded = 0
            if should_download and ytdlp_bin:
                pending_clips = self.db.get_channel_videos(ch_id, status="pending")
                if pending_clips:
                    self.log_message.emit(f"📥 Starting downloads for {len(pending_clips)} pending clip(s)...")
                    os.makedirs(folder, exist_ok=True)

                    for clip_idx, clip in enumerate(pending_clips, start=1):
                        if self._is_stopped:
                            break

                        v_id = clip["id"]
                        v_url = clip["video_url"]
                        v_title = clip["title"] or clip["video_id"]

                        self.video_started.emit(v_id, v_title)
                        self.log_message.emit(f"  • Downloading [{clip_idx}/{len(pending_clips)}]: {v_title}")

                        # Build filename
                        safe_title = sanitize_filename(v_title)
                        if channel.get("use_title", 1):
                            base_fn = f"{safe_title}.mp4"
                        else:
                            base_fn = f"{clip['video_id']}.mp4"

                        if channel.get("numbering", 0):
                            base_fn = f"{clip_idx}. {base_fn}"

                        output_path = os.path.join(folder, base_fn)
                        quality = channel.get("quality", "normal")
                        use_cookies = bool(active_browser or cookie_path)

                        ret = self._execute_download(
                            ytdlp_bin,
                            ffmpeg_bin,
                            output_path,
                            v_url,
                            quality,
                            active_browser,
                            use_cookies,
                            v_id,
                        )

                        # Retry without cookies if cookies failed
                        if ret != 0 and use_cookies and not self._is_stopped:
                            self.log_message.emit("  ⚠️ Retrying without cookies...")
                            ret = self._execute_download(
                                ytdlp_bin,
                                ffmpeg_bin,
                                output_path,
                                v_url,
                                quality,
                                active_browser,
                                use_cookies=False,
                                video_row_id=v_id,
                            )

                        if ret == 0:
                            self.db.update_channel_video_downloaded(v_id, base_fn, v_title, "completed")
                            self.video_finished.emit(v_id, base_fn, v_title)
                            ch_downloaded += 1
                            total_downloaded += 1
                            self.log_message.emit(f"  ✅ Completed: {base_fn}")

                            if channel.get("download_thumbnail", 0):
                                thumb_dir = os.path.join(folder, "thumbnails")
                                self._download_thumbnail(clip.get("thumbnail_url", ""), thumb_dir, safe_title)
                        else:
                            err_msg = f"yt-dlp exited with code {ret}"
                            self.db.update_channel_video_status(v_id, "error", err_msg)
                            self.video_error.emit(v_id, err_msg)
                            self.log_message.emit(f"  ❌ Error: {err_msg}")

                        # Sleep between downloads
                        delay = self.config.delay_between
                        if delay > 0 and clip_idx < len(pending_clips) and not self._is_stopped:
                            time.sleep(delay)

            self.channel_finished.emit(ch_id, new_count, ch_downloaded)

        self.overall_progress.emit(total_channels, total_channels)
        self.log_message.emit(
            f"\n🏁 Finished checking channels! {total_new_videos} new clip(s) found, "
            f"{total_downloaded} downloaded."
        )
        self.all_channels_finished.emit(total_new_videos, total_downloaded)

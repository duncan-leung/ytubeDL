"""Audio converter and worker threads using FFmpeg for ytubeDL."""
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional
from PyQt5.QtCore import QThread, pyqtSignal

from core.bin_helper import download_ffmpeg_binary, find_ffmpeg
from core.config import Config


class ConvertWorker(QThread):
    """Background worker thread that converts downloaded videos to MP3 using FFmpeg."""

    log_message = pyqtSignal(str)
    item_started = pyqtSignal(int, str)  # (item_id, filename)
    item_progress = pyqtSignal(int, str)  # (item_id, progress_text)
    item_finished = pyqtSignal(int, str, str)  # (item_id, src_filename, mp3_filename)
    item_error = pyqtSignal(int, str, str)  # (item_id, src_filename, error_text)
    overall_progress = pyqtSignal(int, int)  # (current, total)
    all_finished = pyqtSignal(int, int, list)  # (success_count, error_count, converted_files)

    def __init__(
        self,
        items: List[Dict],
        output_dir: Path,
        config: Config,
        parent=None,
    ):
        super().__init__(parent)
        self.items = items
        self.output_dir = Path(output_dir)
        self.config = config
        self._is_stopped = False
        self._current_process: Optional[subprocess.Popen] = None

    def stop(self):
        """Request graceful stop of the conversion."""
        self._is_stopped = True
        if self._current_process and self._current_process.poll() is None:
            try:
                self._current_process.terminate()
            except Exception:
                pass

    def run(self):
        """Execute sequential FFmpeg MP3 conversions for selected items."""
        ffmpeg_bin = find_ffmpeg(self.config.get("custom_ffmpeg_path"))
        if not ffmpeg_bin:
            self.log_message.emit("❌ Error: FFmpeg executable could not be found.")
            self.log_message.emit("💡 Please install FFmpeg or click 'Install / Download FFmpeg' in Settings.")
            self.all_finished.emit(0, len(self.items), [])
            return

        total = len(self.items)
        success_count = 0
        error_count = 0
        converted_files: List[str] = []

        self.log_message.emit(f"🎵 Starting MP3 conversion batch: {total} item(s)...")

        for idx, item in enumerate(self.items):
            if self._is_stopped:
                self.log_message.emit("⏹ Conversion queue stopped by user.")
                break

            item_id = item.get("id", -1)
            filename = item.get("filename", "")
            src_path = self.output_dir / filename

            self.overall_progress.emit(idx, total)
            self.item_started.emit(item_id, filename)

            if not filename:
                err = "No filename associated with this item."
                self.log_message.emit(f"❌ [{idx + 1}/{total}] Error: {err}")
                self.item_error.emit(item_id, filename, err)
                error_count += 1
                continue

            if not src_path.is_file():
                err = f"File not found on disk: {src_path.name}"
                self.log_message.emit(f"❌ [{idx + 1}/{total}] Error: {err}")
                self.item_error.emit(item_id, filename, err)
                error_count += 1
                continue

            if src_path.suffix.lower() == ".mp3":
                self.log_message.emit(f"ℹ️ [{idx + 1}/{total}] Already an MP3: {src_path.name}")
                success_count += 1
                converted_files.append(str(src_path))
                self.item_finished.emit(item_id, filename, filename)
                continue

            dest_path = src_path.with_suffix(".mp3")
            self.log_message.emit(f"🔄 [{idx + 1}/{total}] Converting: {src_path.name} -> {dest_path.name}")

            cmd = [
                ffmpeg_bin,
                "-y",  # Overwrite output without prompting
                "-i", str(src_path),
                "-vn",  # Disable video stream
                "-c:a", "libmp3lame",
                "-q:a", "2",  # High quality LAME V2 (~190 kbps)
                "-map_metadata", "0",  # Preserve metadata tags
                "-id3v2_version", "3",  # Universally compatible ID3v2.3 tags
                str(dest_path),
            ]

            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            err_lines: List[str] = []

            try:
                self._current_process = subprocess.Popen(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    universal_newlines=True,
                    creationflags=creationflags,
                )

                if self._current_process.stdout:
                    for line in self._current_process.stdout:
                        if self._is_stopped:
                            self._current_process.terminate()
                            break
                        clean_line = line.strip()
                        if clean_line:
                            err_lines.append(clean_line)
                            # Log progress markers
                            if "size=" in clean_line and "time=" in clean_line:
                                self.item_progress.emit(item_id, clean_line)

                self._current_process.wait()
                ret = self._current_process.returncode
            except Exception as e:
                ret = -1
                err_lines.append(str(e))
            finally:
                self._current_process = None

            if self._is_stopped:
                # Remove partially converted file if stopped
                if dest_path.exists():
                    try:
                        dest_path.unlink()
                    except Exception:
                        pass
                break

            # Fallback retry: if libmp3lame or encoder failed, try default mp3 encoder
            if ret != 0 and not self._is_stopped:
                err_text_lower = " ".join(err_lines).lower()
                if "libmp3lame" in err_text_lower or "encoder" in err_text_lower or "unknown encoder" in err_text_lower:
                    self.log_message.emit(
                        "⚠️ Notice: 'libmp3lame' encoder missing in current FFmpeg. Retrying with standard MP3 encoder..."
                    )
                    cmd_fallback = [
                        ffmpeg_bin,
                        "-y",
                        "-i", str(src_path),
                        "-vn",
                        "-c:a", "mp3",
                        "-b:a", "192k",
                        str(dest_path),
                    ]
                    try:
                        self._current_process = subprocess.Popen(
                            cmd_fallback,
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            bufsize=1,
                            universal_newlines=True,
                            creationflags=creationflags,
                        )
                        if self._current_process.stdout:
                            for line in self._current_process.stdout:
                                clean_line = line.strip()
                                if clean_line:
                                    err_lines.append(clean_line)
                        self._current_process.wait()
                        ret = self._current_process.returncode
                    except Exception as e:
                        ret = -1
                        err_lines.append(str(e))
                    finally:
                        self._current_process = None

            if ret == 0 and dest_path.is_file() and dest_path.stat().st_size > 0:
                self.log_message.emit(f"✅ Successfully converted: {dest_path.name}")
                self.item_finished.emit(item_id, filename, dest_path.name)
                success_count += 1
                converted_files.append(str(dest_path))
            else:
                summary_err = "\n".join(err_lines[-5:]) if err_lines else f"Exit code {ret}"
                self.log_message.emit(f"❌ [{idx + 1}/{total}] Conversion failed: {summary_err}")
                if "does not contain any stream" in summary_err.lower():
                    self.log_message.emit(
                        "💡 Tip: The input video does not contain an audio stream."
                    )
                elif "encoder" in summary_err.lower() or "codec" in summary_err.lower():
                    self.log_message.emit(
                        "💡 Tip: In ytubeDL Settings (Ctrl+,), click 'Install / Download FFmpeg Now' to get the official standalone build with full audio encoders."
                    )
                self.item_error.emit(item_id, filename, f"FFmpeg failed ({ret})")
                error_count += 1

        self.overall_progress.emit(total, total)
        self.log_message.emit(
            f"🏁 MP3 Conversion Complete: {success_count} converted, {error_count} failed."
        )
        self.all_finished.emit(success_count, error_count, converted_files)


class FFmpegDownloadWorker(QThread):
    """Background worker to download and install FFmpeg without freezing UI."""

    progress = pyqtSignal(int, int)  # (current, total)
    finished = pyqtSignal(bool, str)  # (success, message)

    def run(self):
        def cb(cur, total):
            self.progress.emit(cur, total)

        success, msg = download_ffmpeg_binary(cb)
        self.finished.emit(success, msg)

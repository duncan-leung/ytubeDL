"""Configuration manager for ytubeDL."""
import json
import os
import sys
from pathlib import Path


def get_app_data_dir() -> Path:
    """Return standard user data directory for ytubeDL across platforms."""
    app_name = "ytubeDL"
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    else:
        xdg_data = os.environ.get("XDG_DATA_HOME")
        base = Path(xdg_data) if xdg_data else Path.home() / ".config"

    data_dir = base / app_name
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir
    except (PermissionError, OSError):
        # Fallback to local directory for portable mode or sandboxed environments
        local_dir = Path(__file__).resolve().parent.parent / ".data"
        local_dir.mkdir(parents=True, exist_ok=True)
        return local_dir


def get_bin_dir() -> Path:
    """Return internal directory for managed binaries (yt-dlp, ffmpeg)."""
    bin_dir = get_app_data_dir() / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    return bin_dir


def get_db_path() -> Path:
    """Return path to SQLite database."""
    return get_app_data_dir() / "library.db"


class Config:
    """Application configuration container with JSON persistence."""

    DEFAULT_SETTINGS = {
        "output_dir": str(Path.home() / "Downloads"),
        "delay_between": 8,
        "initial_sleep": 0,
        "cookies_from_browser": "auto",
        "cookies_file": "",
        "enable_js_runtime": True,
        "quality_format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "custom_ytdlp_path": "",
        "custom_ffmpeg_path": "",
        "auto_check_update": True,
        "window_width": 1050,
        "window_height": 720,
    }

    def __init__(self):
        self.config_file = get_app_data_dir() / "config.json"
        self._data = dict(self.DEFAULT_SETTINGS)
        self.load()

    def load(self):
        """Load configuration from disk if available."""
        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    self._data.update(loaded)
            except Exception as e:
                print(f"[Config] Error loading config: {e}")

        # Ensure output directory exists or falls back to user home
        out_path = Path(self._data.get("output_dir", ""))
        if not out_path.exists():
            try:
                out_path.mkdir(parents=True, exist_ok=True)
            except Exception:
                self._data["output_dir"] = str(Path.home() / "Downloads")

    def save(self):
        """Save configuration to disk."""
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[Config] Error saving config: {e}")

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value
        self.save()

    @property
    def output_dir(self) -> str:
        return self._data.get("output_dir", str(Path.home() / "Downloads"))

    @output_dir.setter
    def output_dir(self, val: str):
        self._data["output_dir"] = val
        self.save()

    @property
    def delay_between(self) -> int:
        return int(self._data.get("delay_between", 8))

    @delay_between.setter
    def delay_between(self, val: int):
        self._data["delay_between"] = int(val)
        self.save()

    @property
    def cookies_from_browser(self) -> str:
        return self._data.get("cookies_from_browser", "firefox")

    @cookies_from_browser.setter
    def cookies_from_browser(self, val: str):
        self._data["cookies_from_browser"] = val
        self.save()

    @property
    def cookies_file(self) -> str:
        return self._data.get("cookies_file", "")

    @cookies_file.setter
    def cookies_file(self, val: str):
        self._data["cookies_file"] = val
        self.save()

    @property
    def enable_js_runtime(self) -> bool:
        return bool(self._data.get("enable_js_runtime", True))

    @enable_js_runtime.setter
    def enable_js_runtime(self, val: bool):
        self._data["enable_js_runtime"] = bool(val)
        self.save()

    @property
    def auto_check_update(self) -> bool:
        return bool(self._data.get("auto_check_update", True))

    @auto_check_update.setter
    def auto_check_update(self, val: bool):
        self._data["auto_check_update"] = bool(val)
        self.save()

"""Helper for detecting, finding, and updating yt-dlp and ffmpeg binaries."""
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple
import requests
from core.config import get_bin_dir

GITHUB_API_LATEST = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"


def find_ytdlp(custom_path: Optional[str] = None) -> Optional[str]:
    """
    Locate yt-dlp executable with prioritization:
    1. Custom user path
    2. App internal bin directory (managed standalone binary)
    3. System PATH (via shutil.which)
    4. Well-known platform paths
    5. Python module execution fallback
    """
    if custom_path and os.path.isfile(custom_path) and os.access(custom_path, os.X_OK):
        return custom_path

    # Internal managed bin folder
    bin_name = "yt-dlp.exe" if sys.platform == "win32" else "yt-dlp"
    internal_path = get_bin_dir() / bin_name
    if internal_path.is_file() and os.access(str(internal_path), os.X_OK):
        return str(internal_path)

    # System PATH
    found = shutil.which("yt-dlp")
    if found:
        return found

    # Well-known locations
    candidates = []
    if sys.platform == "darwin":
        candidates = [
            "/opt/homebrew/bin/yt-dlp",
            "/usr/local/bin/yt-dlp",
            str(Path.home() / ".local/bin/yt-dlp"),
            "/Library/Frameworks/Python.framework/Versions/3.13/bin/yt-dlp",
            "/Library/Frameworks/Python.framework/Versions/3.12/bin/yt-dlp",
            "/Library/Frameworks/Python.framework/Versions/3.11/bin/yt-dlp",
        ]
    elif sys.platform == "win32":
        candidates = [
            str(Path.home() / "AppData/Local/Programs/yt-dlp/yt-dlp.exe"),
            str(Path.home() / "AppData/Roaming/yt-dlp/yt-dlp.exe"),
            "C:\\Program Files\\yt-dlp\\yt-dlp.exe",
        ]
    else:
        candidates = [
            "/usr/bin/yt-dlp",
            "/usr/local/bin/yt-dlp",
            str(Path.home() / ".local/bin/yt-dlp"),
        ]

    for p in candidates:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p

    return None


def find_ffmpeg(custom_path: Optional[str] = None) -> Optional[str]:
    """Locate ffmpeg executable."""
    if custom_path and os.path.isfile(custom_path) and os.access(custom_path, os.X_OK):
        return custom_path

    bin_name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    internal_path = get_bin_dir() / bin_name
    if internal_path.is_file() and os.access(str(internal_path), os.X_OK):
        return str(internal_path)

    found = shutil.which("ffmpeg")
    if found:
        return found

    candidates = []
    if sys.platform == "darwin":
        candidates = [
            "/opt/homebrew/bin/ffmpeg",
            "/usr/local/bin/ffmpeg",
            str(Path.home() / ".local/bin/ffmpeg"),
        ]
    elif sys.platform == "win32":
        candidates = [
            str(Path.home() / "AppData/Local/ffmpeg/bin/ffmpeg.exe"),
            "C:\\ffmpeg\\bin\\ffmpeg.exe",
            "C:\\Program Files\\ffmpeg\\bin\\ffmpeg.exe",
        ]

    for p in candidates:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p

    return None


def get_ytdlp_version(ytdlp_bin: Optional[str] = None) -> Optional[str]:
    """Get the version string of the active yt-dlp."""
    binary = ytdlp_bin or find_ytdlp()
    if not binary:
        return None
    try:
        proc = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, check=True, timeout=5
        )
        return proc.stdout.strip()
    except Exception:
        return None


def check_latest_ytdlp_release() -> Optional[Dict[str, str]]:
    """
    Check the latest GitHub release of yt-dlp.
    Returns: dict with 'version', 'tag_name', 'download_url', or None on network error.
    """
    try:
        resp = requests.get(
            GITHUB_API_LATEST,
            headers={"User-Agent": "ytubeDL-Desktop-App"},
            timeout=8,
        )
        if resp.status_code != 200:
            return None

        data = resp.json()
        version = data.get("tag_name", "").lstrip("v")
        assets = data.get("assets", [])

        # Choose the right asset for current platform
        target_name = "yt-dlp.exe" if sys.platform == "win32" else "yt-dlp_macos" if sys.platform == "darwin" else "yt-dlp"

        download_url = None
        for asset in assets:
            if asset.get("name") == target_name:
                download_url = asset.get("browser_download_url")
                break

        # Fallback for mac/linux if platform-specific asset isn't found
        if not download_url and sys.platform != "win32":
            for asset in assets:
                if asset.get("name") == "yt-dlp":
                    download_url = asset.get("browser_download_url")
                    break

        return {
            "version": version,
            "published_at": data.get("published_at", ""),
            "download_url": download_url,
        }
    except Exception as e:
        print(f"[bin_helper] Error checking release: {e}")
        return None


def download_ytdlp_binary(progress_callback=None) -> Tuple[bool, str]:
    """
    Download the latest official standalone yt-dlp binary into app's managed bin directory.
    Returns (success: bool, message: str).
    """
    release_info = check_latest_ytdlp_release()
    if not release_info or not release_info.get("download_url"):
        return False, "Could not fetch latest release info from GitHub."

    download_url = release_info["download_url"]
    bin_name = "yt-dlp.exe" if sys.platform == "win32" else "yt-dlp"
    dest_path = get_bin_dir() / bin_name
    temp_path = get_bin_dir() / f"{bin_name}.tmp"

    try:
        resp = requests.get(
            download_url,
            headers={"User-Agent": "ytubeDL-Desktop-App"},
            stream=True,
            timeout=30,
        )
        resp.raise_for_status()

        total_size = int(resp.headers.get("content-length", 0))
        downloaded = 0

        with open(temp_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback and total_size > 0:
                        progress_callback(downloaded, total_size)

        # Make executable on Unix
        if sys.platform != "win32":
            st = os.stat(temp_path)
            os.chmod(temp_path, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

        # Atomic replace
        if dest_path.exists():
            dest_path.unlink()
        temp_path.rename(dest_path)

        new_version = get_ytdlp_version(str(dest_path)) or release_info["version"]
        return True, f"Successfully updated yt-dlp to version {new_version}!"

    except Exception as e:
        if temp_path.exists():
            temp_path.unlink()
        return False, f"Failed to download yt-dlp: {e}"

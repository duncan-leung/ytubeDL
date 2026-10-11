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

    # Application directory (where ytubeDL is located)
    try:
        app_dir = Path(sys.executable).parent
        app_ytdlp = app_dir / bin_name
        if app_ytdlp.is_file() and os.access(str(app_ytdlp), os.X_OK):
            return str(app_ytdlp)
    except Exception:
        pass

    # PyInstaller bundle directory
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        try:
            meipass_p = Path(sys._MEIPASS) / bin_name
            if meipass_p.is_file():
                return str(meipass_p)
        except Exception:
            pass

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
    """
    Locate ffmpeg executable with prioritization:
    1. Custom user path
    2. Internal managed bin folder
    3. Application directory (where ytubeDL.exe is located)
    4. PyInstaller _MEIPASS folder (if bundled)
    5. Beside yt-dlp binary (if yt-dlp is found)
    6. System PATH (via shutil.which)
    7. Well-known platform paths
    """
    if custom_path and os.path.isfile(custom_path) and os.access(custom_path, os.X_OK):
        return custom_path

    bin_name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    internal_path = get_bin_dir() / bin_name
    if internal_path.is_file() and os.access(str(internal_path), os.X_OK):
        return str(internal_path)

    # Application directory (portable exe folder)
    try:
        app_dir = Path(sys.executable).parent
        app_ffmpeg = app_dir / bin_name
        if app_ffmpeg.is_file() and os.access(str(app_ffmpeg), os.X_OK):
            return str(app_ffmpeg)
    except Exception:
        pass

    # PyInstaller bundle
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        try:
            meipass_p = Path(sys._MEIPASS) / bin_name
            if meipass_p.is_file():
                return str(meipass_p)
        except Exception:
            pass

    # Beside yt-dlp binary
    try:
        ytdlp_path = find_ytdlp(None)
        if ytdlp_path:
            sibling = Path(ytdlp_path).parent / bin_name
            if sibling.is_file() and os.access(str(sibling), os.X_OK):
                return str(sibling)
    except Exception:
        pass

    found = shutil.which("ffmpeg")
    if found:
        return found

    candidates = []
    if sys.platform == "darwin":
        candidates = [
            "/opt/homebrew/bin/ffmpeg",
            "/usr/local/bin/ffmpeg",
            str(Path.home() / ".local/bin/ffmpeg"),
            "/opt/homebrew/opt/ffmpeg/bin/ffmpeg",
        ]
    elif sys.platform == "win32":
        candidates = [
            str(Path.home() / "AppData/Local/ffmpeg/bin/ffmpeg.exe"),
            str(Path.home() / "AppData/Local/Programs/ffmpeg/bin/ffmpeg.exe"),
            "C:\\ffmpeg\\bin\\ffmpeg.exe",
            "C:\\Program Files\\ffmpeg\\bin\\ffmpeg.exe",
            "C:\\Program Files (x86)\\ffmpeg\\bin\\ffmpeg.exe",
            "C:\\tools\\ffmpeg\\bin\\ffmpeg.exe",
            str(Path.home() / "scoop/shims/ffmpeg.exe"),
            "C:\\ProgramData\\chocolatey\\bin\\ffmpeg.exe",
        ]
    else:
        candidates = [
            "/usr/bin/ffmpeg",
            "/usr/local/bin/ffmpeg",
            str(Path.home() / ".local/bin/ffmpeg"),
        ]

    for p in candidates:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p

    return None


def get_ffmpeg_version(ffmpeg_bin: Optional[str] = None) -> Optional[str]:
    """Get the version string of the active ffmpeg."""
    binary = ffmpeg_bin or find_ffmpeg()
    if not binary:
        return None
    try:
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        proc = subprocess.run(
            [binary, "-version"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
            creationflags=creationflags,
        )
        lines = proc.stdout.splitlines()
        if lines:
            first_line = lines[0].strip()
            parts = first_line.split()
            if len(parts) >= 3 and parts[0].lower() == "ffmpeg" and parts[1].lower() == "version":
                return f"v{parts[2]}"
            return first_line
        return None
    except Exception:
        return None


def download_ffmpeg_binary(progress_callback=None) -> Tuple[bool, str]:
    """
    Download standalone FFmpeg binary into app's managed bin directory.
    On Windows: downloads static build zip from yt-dlp/FFmpeg-Builds and extracts ffmpeg.exe & ffprobe.exe.
    On macOS: uses brew install or evermeet static build.
    On Linux: downloads static build tar.xz from yt-dlp/FFmpeg-Builds.
    Returns (success: bool, message: str).
    """
    import zipfile
    import tarfile

    bin_dir = get_bin_dir()
    bin_dir.mkdir(parents=True, exist_ok=True)

    if sys.platform == "win32":
        download_url = "https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
        temp_zip = bin_dir / "ffmpeg_download.zip.tmp"
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

            with open(temp_zip, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback and total_size > 0:
                            progress_callback(downloaded, total_size)

            with zipfile.ZipFile(temp_zip, "r") as zf:
                for member in zf.namelist():
                    basename = os.path.basename(member)
                    if basename.lower() in ("ffmpeg.exe", "ffprobe.exe"):
                        dest_path = bin_dir / basename
                        with zf.open(member) as src, open(dest_path, "wb") as dst:
                            shutil.copyfileobj(src, dst)

            if temp_zip.exists():
                temp_zip.unlink()

            ffmpeg_path = bin_dir / "ffmpeg.exe"
            if ffmpeg_path.is_file():
                ver = get_ffmpeg_version(str(ffmpeg_path)) or "Latest"
                return True, f"Successfully installed FFmpeg ({ver})!"
            return False, "Failed to find ffmpeg.exe in downloaded archive."

        except Exception as e:
            if temp_zip.exists():
                temp_zip.unlink()
            return False, f"Failed to download FFmpeg: {e}"

    elif sys.platform == "darwin":
        brew_bin = shutil.which("brew")
        if brew_bin:
            try:
                proc = subprocess.run(
                    [brew_bin, "install", "ffmpeg"],
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                if proc.returncode == 0:
                    ver = get_ffmpeg_version() or "Installed"
                    return True, f"Successfully installed FFmpeg via Homebrew ({ver})!"
            except Exception:
                pass

        download_url = "https://evermeet.cx/ffmpeg/getrelease/zip"
        temp_zip = bin_dir / "ffmpeg_mac.zip.tmp"
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

            with open(temp_zip, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback and total_size > 0:
                            progress_callback(downloaded, total_size)

            with zipfile.ZipFile(temp_zip, "r") as zf:
                for member in zf.namelist():
                    basename = os.path.basename(member)
                    if basename == "ffmpeg":
                        dest_path = bin_dir / "ffmpeg"
                        with zf.open(member) as src, open(dest_path, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                        st = os.stat(dest_path)
                        os.chmod(dest_path, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

            if temp_zip.exists():
                temp_zip.unlink()

            ffmpeg_path = bin_dir / "ffmpeg"
            if ffmpeg_path.is_file():
                ver = get_ffmpeg_version(str(ffmpeg_path)) or "Latest"
                return True, f"Successfully installed FFmpeg ({ver})!"
            return False, "Failed to extract ffmpeg for macOS. Please install using: brew install ffmpeg"
        except Exception as e:
            if temp_zip.exists():
                temp_zip.unlink()
            return False, f"Failed to download FFmpeg: {e}. You can install it with: brew install ffmpeg"

    else:
        download_url = "https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz"
        temp_tar = bin_dir / "ffmpeg_download.tar.xz.tmp"
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

            with open(temp_tar, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback and total_size > 0:
                            progress_callback(downloaded, total_size)

            with tarfile.open(temp_tar, "r:xz") as tf:
                for member in tf.getmembers():
                    basename = os.path.basename(member.name)
                    if basename in ("ffmpeg", "ffprobe"):
                        dest_path = bin_dir / basename
                        fobj = tf.extractfile(member)
                        if fobj:
                            with open(dest_path, "wb") as dst:
                                shutil.copyfileobj(fobj, dst)
                            st = os.stat(dest_path)
                            os.chmod(dest_path, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

            if temp_tar.exists():
                temp_tar.unlink()

            ffmpeg_path = bin_dir / "ffmpeg"
            if ffmpeg_path.is_file():
                ver = get_ffmpeg_version(str(ffmpeg_path)) or "Latest"
                return True, f"Successfully installed FFmpeg ({ver})!"
            return False, "Failed to extract ffmpeg from tar archive."
        except Exception as e:
            if temp_tar.exists():
                temp_tar.unlink()
            return False, f"Failed to download FFmpeg: {e}"


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

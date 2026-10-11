# ytubeDL

Modern, high-performance desktop YouTube video downloader for **macOS** and **Windows**.

Designed to be responsive, robust, and turnkey for both technical users and non-technical friends.

---

## Key Highlights

- **Instant, Zero-Lag UI**: Built with PyQt5 using virtualized Model-View architecture (`QTableView` + `QAbstractTableModel`). Can display and scroll through **10,000+ items at a fluid 60 FPS** without the stuttering or freezing common in Tkinter.
- **Dedicated Channels & Subscriptions Tab**:
  - Automatically monitors channels and playlists for **Videos**, **Shorts**, or **Playlists**.
  - **Smart URL Detection & Validation**: Automatically detects content type (`/videos`, `/shorts`, or playlist `list=`). Warns with a guidance dialog if a bare home page is entered.
  - **Webpage Source Scraper**: Inspects YouTube HTML in under 1s to resolve `channel_id`, channel title, and avatar.
  - **Foldable Channel Tree**: Hierarchical view allowing you to fold or unfold any channel to inspect or hide its retrieved clips.
  - **Auto-Sync & Conditional Download**: Retrieves the set limit of latest clips, deduplicates against library records, and automatically downloads new clips if the channel's download flag is enabled.
  - **Flexible Row Management**: Select and delete any old clip rows directly from the tree, or use 1-click prune to keep only the newest $N$ clips.
- **Integrated Library (No `url.txt` File Needed)**: All URLs, video titles, and download statuses are stored seamlessly in an internal SQLite database (`library.db`).
- **1-Click MP4 to MP3 Audio Conversion**:
  - Select any one or multiple completed rows in the table and click **🎵 Convert to MP3** (or right-click -> *Convert Selected to MP3*).
  - Automatically extracts high-quality audio using the exact same filename (e.g. `Song.mp4` -> `Song.mp3`) in your output folder.
  - Transfers track metadata and generates standard ID3v2.3 tags readable by Windows Media Player, iTunes, car audio systems, and mobile devices.
  - Context menu provides direct **🎬 Reveal Video File** and **🎵 Reveal MP3 File** shortcuts.
- **Turnkey for Non-Technical Friends**:
  - Automatically checks if `yt-dlp` or `ffmpeg` is available.
  - Offers a **1-click auto-download and setup** of official standalone binaries (`yt-dlp` and `ffmpeg`) directly inside the app—no terminal, PATH tweaking, or command-line required.
  - Checks companion folders automatically: simply placing `ffmpeg.exe` in the same directory as `ytubeDL.exe` works immediately.
  - Built-in `--no-update` flag suppresses noisy 90-day deprecation warnings during active downloads.
- **Proven Reliable Extraction Engine**:
  - Preserves battle-tested parameters: browser cookies extraction (`firefox`, `chrome`, `edge`, `safari`, `brave`, or `none`).
  - Extractor arguments: `youtube:player_client=web_safari,web_embedded,-tv_downgraded` with cookies, and `android` client without.
  - Node.js challenge solving (`--js-runtimes node --remote-components ejs:github`).
  - Dual-stream best format selector (`bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best`) merged into clean `.mp4`.
  - Rate-limit sleep pacing between downloads.
- **Full Row Operations**:
  - **Type or Paste Single URL**: Quick paste bar with `+ Add URL`.
  - **Batch Paste Dialog**: Paste dozens or hundreds of URLs at once with instant duplicate prevention.
  - **Enable / Disable**: Toggle checkboxes to include or skip rows during downloads.
  - **Inline Editing**: Double-click any title or URL to edit.
  - **Delete**: Select one or multiple rows and press `Delete` or right-click.
  - **Context Menu**: Download selected now, reveal downloaded file in Finder / Explorer, open in browser, copy URL, reset status to pending.
- **Selectable Output Directory**: Browse and select any folder; remembered automatically between launches.
- **Cross-Platform Executables**: Pre-configured **GitHub Actions CI/CD** to build `.exe` for Windows and `.app` / `.zip` for macOS automatically.

---

## Quick Start (Running from Source)

### 1. Requirements
- Python 3.9+ (Python 3.11 - 3.13 tested)
- PyQt5 & requests

```bash
cd ytubeDL
pip install -r requirements.txt
```

### 2. Launch the Application
```bash
python3 app.py
```

---

## Building Executables for Friends (No Source Code Needed)

### Option A: Using GitHub Actions (Recommended)
You can compile native standalone binaries for both Windows and macOS without needing both machines locally:
1. Push this repository to GitHub.
2. In your repository, go to the **Actions** tab.
3. Select **"Build Desktop Executables"** and click **"Run workflow"** (or push a tag like `v1.0.0`).
4. Once completed (approx. 2-3 minutes), download the built artifacts:
   - **`ytubeDL-Windows`**: `ytubeDL.exe`
   - **`ytubeDL-macOS`**: `ytubeDL-macOS.zip` (containing `ytubeDL.app`)
5. Send the zip or `.exe` directly to your friend!

### Option B: Build Locally on Your Current Machine
To compile on your current operating system:
```bash
pip install pyinstaller
pyinstaller ytubeDL.spec
```
The compiled bundle will be in the `dist/` directory:
- **macOS**: `dist/ytubeDL.app`
- **Windows**: `dist/ytubeDL.exe`

---

## Migrating from Existing `url.txt` (Optional)

If you have an existing `url.txt` list from previous scripts:
1. Launch **ytubeDL**.
2. Click **File -> Import from url.txt...** in the top menu bar.
3. Select your file. All URLs, titles, and already downloaded records will be imported into the app's internal database in seconds.
4. You can also export your library back to a `url.txt` at any time via **File -> Export to url.txt...**.

---

## Configuration & Data Storage Locations

Settings and library data are saved in your system's standard application data directory:
- **macOS**: `~/Library/Application Support/ytubeDL/`
- **Windows**: `%APPDATA%\ytubeDL\`
- **Linux**: `~/.config/ytubeDL/`

Binaries (`yt-dlp`, `ffmpeg`) downloaded via the built-in 1-click updater are safely stored inside the `bin/` subfolder of this directory.

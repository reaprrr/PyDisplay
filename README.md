# PyDisplay
A lightweight, customisable Windows system stats overlay built with Python and tkinter. Displays real-time GPU, CPU, RAM, network, disk I/O, and storage usage in a compact always-on-top window — no browser, no Electron, no bloat.

---

## Features
- **Real-time stats** — GPU usage, VRAM, temp & wattage · CPU usage, clock speed, processes & threads · RAM · network up/down · disk I/O · per-drive storage
- **Click-through mode** — overlay stays visible but passes all mouse clicks to whatever's behind it
- **Fully themeable** — built-in dark/light/terminal/ice/sunset/midnight themes plus a full colour picker for every element
- **Font size scaling** — resize all text from a single slider
- **Section management** — show, hide, collapse, and reorder sections via drag-and-drop in Settings
- **Horizontal & vertical layouts** — switch between compact side-by-side and stacked views
- **Memory Cleaner** — built-in RAM cleaner (Safe & Aggressive modes) accessible via the Memory Tools dropdown; covers all operations from Memory Reduct v3.5.2
- **Speed test** — built-in download/upload/ping test (no extra dependencies)
- **IP lookup** — one-click public IP + geolocation
- **Logging** — periodic snapshots of all stats to a local text file
- **Minimize to tray** — optional system tray icon with live GPU % (requires `pystray`)
- **Portable mode** — run entirely from a self-contained folder; nothing written to `%APPDATA%`
- **Config versioning** — settings survive app updates without breaking
- **Multi-GPU support** — select your active GPU in Settings
- **NVIDIA · AMD · Intel Arc** — automatic vendor detection with multiple fallback backends

---

## Download

### Standalone Executable (Recommended)
A pre-compiled `.exe` is available for users who don't have Python installed.

- **No Python required** — just download and run
- Built with [PyInstaller](https://pyinstaller.org/) (`--onefile --noconsole`)
- Features the custom black cat icon
- May trigger a Windows SmartScreen warning on first launch — this is expected for unsigned executables; click **More info → Run anyway**

> The `.exe` bundles all internals but still relies on the built-in dependency manager to install external packages (psutil, pynvml, etc.) on first launch, same as the Python version.

### From Source (Python)
```bash
git clone https://github.com/reaprrr/PyDisplay.git
cd PyDisplay
python PyDisplay.pyw
```
On first launch the dependency manager will open. Install any missing packages and hit **LAUNCH**.

---

## Building the Executable Yourself

1. Install PyInstaller:
   ```
   pip install pyinstaller
   ```

2. Run from the project folder:
   ```
   pyinstaller --clean --onefile --noconsole --name PyDisplay --icon=app.ico PyDisplay.pyw
   ```

3. Your `.exe` will appear in the `dist/` folder.

> **Icon not showing?** Delete `build/`, `dist/`, `__pycache__`, and `PyDisplay.spec`, then rebuild. Windows may also cache the old icon — moving the `.exe` to a new folder forces a refresh.

---

## Requirements
**Python 3.9+** on Windows (source version only).

| Package | Required | Purpose |
|---------|----------|---------|
| `psutil` | ✅ Yes | CPU, RAM, disk & network stats |
| `pynvml` | ✅ If NVIDIA GPU | NVIDIA GPU usage, VRAM, temp & wattage |
| `pywin32` | Optional | CPU clock speed · AMD/Intel GPU stats via WMI |
| `pystray` | Optional | Minimize to system tray |
| `GPUtil` | Optional | Fallback GPU reader if pynvml & pywin32 both fail |

> PyDisplay includes a built-in dependency manager that handles installation automatically on first launch.

---

## Usage
- **Drag** the top bar to move the overlay
- **Resize** from any edge or corner
- **Ctrl + hover** to show tooltips while click-through is active
- **Ctrl + click** a section header to collapse / expand it
- **Settings (⚙)** — toggle features, change theme, reorder sections, adjust poll rate
- **Theme (◈)** — open the colour picker to customise every section accent, background, and text colour; one **Dropdown Tools** swatch controls all tool buttons across every dropdown at once
- **HELP? (?)** — opens the Quick Reference panel listing every button, section, and shortcut
- **Minimize** — closes to tray if `pystray` is installed, otherwise hides

### Network Tools
Click **TOOLS** in the Network section to expand:
- **▶ SPEED TEST** — native ping/download/upload test, no browser needed
- **⌖ IP LOOKUP** — fetches your public IP and geolocation info

### Memory Tools
Click **TOOLS** in the Memory section to expand:
- **🧹 MEMORY CLEAN** — opens the Memory Cleaner popup
  - **Safe Clean** — trims process working sets, flushes modified pages & file system/registry caches; safe for games and browsers
  - **Aggressive Clean** — all Safe steps plus standby list purge and memory page combination (may cause a brief stutter)
  - Live step-by-step output log with before/after RAM usage and GB freed

---

## Portable Mode
PyDisplay can run in a fully self-contained portable configuration — nothing is written to `%APPDATA%` or anywhere outside the folder.

To enable portable mode, place `PyDisplay.pyw` (or `PyDisplay.exe`) inside a folder named **`PyDisplay`**. PyDisplay detects this automatically on launch and stores all config, themes, and logs alongside the executable in that folder.

---

## Dependency Manager
On first launch PyDisplay opens a dependency setup page where you can install, update, and manage all required packages without leaving the app.

- **⌕ Update** — checks GitHub for a new PyDisplay release
- **↻ Check Dep Updates** — checks PyPI for newer versions of installed packages
- **✕ Uninstall All** — removes all installed dependencies with a confirmation prompt and post-removal verification
- **Check for Duplicates** — scans for packages installed in multiple locations and lets you clean them up
- Package status labels show `? Failed` when an operation fails — click the label to see the exact error

---

## Data & Config
All config and logs are stored in `%APPDATA%\PyDisplay\` (or the app folder in portable mode):

| File | Contents |
|------|----------|
| `PyDisplay_pos.json` | Window position, settings, active theme |
| `PyDisplay_theme_Default.json` | Default theme (auto-created) |
| `PyDisplay_theme_*.json` | Any saved custom themes |
| `PyDisplay_log.txt` | Periodic stats snapshots (if logging enabled) |
| `PyDisplay_install.log` | Dependency install/remove history |
| `PyDisplay_error.log` | Non-fatal error log |

---

## Changelog
See [CHANGELOG.md](CHANGELOG.md) for the full version history.

---

## License
MIT

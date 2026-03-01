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
- **Config versioning** — settings survive app updates without breaking
- **Multi-GPU support** — select your active GPU in Settings
- **NVIDIA · AMD · Intel Arc** — automatic vendor detection with multiple fallback backends

---

## Requirements
**Python 3.9+** on Windows.

| Package | Required | Purpose |
|---------|----------|---------|
| `psutil` | ✅ Yes | CPU, RAM, disk & network stats |
| `pynvml` | ✅ If NVIDIA GPU | NVIDIA GPU usage, VRAM, temp & wattage |
| `pywin32` | Optional | CPU clock speed · AMD/Intel GPU stats via WMI |
| `pystray` | Optional | Minimize to system tray |
| `GPUtil` | Optional | Fallback GPU reader if pynvml & pywin32 both fail |

> PyDisplay includes a built-in dependency manager that handles installation automatically on first launch.

---

## Installation
```bash
git clone https://github.com/reaprrr/PyDisplay.git
cd PyDisplay
python PyDisplay.pyw
```
On first launch the dependency manager will open. Install any missing packages and hit **LAUNCH**.

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
Click **▶ TOOLS** in the Network section to expand:
- **▶ SPEED TEST** — native ping/download/upload test, no browser needed
- **⌖ IP LOOKUP** — fetches your public IP and geolocation info

### Memory Tools
Click **▶ TOOLS** in the Memory section to expand:
- **🧹 MEMORY CLEAN** — opens the Memory Cleaner popup
  - **Safe Clean** — trims process working sets, flushes modified pages & file system/registry caches; safe for games and browsers
  - **Aggressive Clean** — all Safe steps plus standby list purge and memory page combination (may cause a brief stutter)
  - Live step-by-step output log with before/after RAM usage and GB freed

---

## Data & Config
All config and logs are stored in `%APPDATA%\PyDisplay\`:

| File | Contents |
|------|----------|
| `PyDisplay_pos.json` | Window position, settings, active theme |
| `PyDisplay_theme_Default.json` | Default theme (auto-created) |
| `PyDisplay_theme_*.json` | Any saved custom themes |
| `PyDisplay_log.txt` | Periodic stats snapshots (if logging enabled) |
| `PyDisplay_error.log` | Non-fatal error log |

---

## Changelog
See [CHANGELOG.md](CHANGELOG.md) for the full version history.

---

## License
MIT

# PyDisplay
<div align="center">

![PyDisplay](https://img.shields.io/badge/PyDisplay-v1.0.6-00ffe5?style=for-the-badge&labelColor=0a0a0f)
![Python](https://img.shields.io/badge/Python-3.9%2B-3776ab?style=flat-square)
![Windows](https://img.shields.io/badge/Windows-10%2B-0078d4?style=flat-square)
![License](https://img.shields.io/badge/License-MIT-39ff7f?style=flat-square)

A lightweight, infinitely customisable Windows system stats overlay. Real-time GPU, CPU, RAM, network, disk I/O & storage in a sleek always-on-top window — no browser, no Electron, no bloat.

[**Features**](#features) • [**Installation**](#installation) • [**Usage**](#usage) • [**Theming**](#theming) • [**Changelog**](CHANGELOG.md)

</div>

---

## Features

### 📊 Real-Time Monitoring
| Feature | Details |
|---------|---------|
| **GPU** | Usage · VRAM · Temperature · Power Draw |
| **CPU** | Usage · Clock Speed · Processes · Threads |
| **Memory** | Total · Used · Free · Memory Cleaner (Safe & Aggressive) |
| **Network** | Upload · Download · Built-in Speed Test · IP Lookup |
| **Disk I/O** | Read · Write Speeds |
| **Storage** | Per-drive capacity & usage |

### 🎨 Customisation
- **6 built-in themes** — Dark, Light, Terminal, Ice, Sunset, Midnight
- **Full colour picker** — Customise every element: section accents, backgrounds, text, buttons
- **Font scaling** — Single slider resizes all text
- **Section management** — Show, hide, collapse, reorder via drag-and-drop
- **Layout modes** — Horizontal (side-by-side) or Vertical (stacked)
- **Multi-GPU support** — Select your active GPU in Settings

### 🛠️ Tools & Utilities
- **Memory Cleaner**
  - 🧹 **Safe Clean** — trims working sets, flushes caches (gaming-safe)
  - ⚡ **Aggressive Clean** — adds standby purge & memory combining (brief stutter)
  - Live progress logs · before/after RAM display · GB freed counter
- **Network Tools**
  - 🚀 **Speed Test** — native ping/download/upload (no external dependencies)
  - 🌐 **IP Lookup** — public IP + geolocation info
- **Dependency Manager** — auto-install, update, remove packages without leaving the app

### ⚙️ Advanced Features
- **Click-through mode** — overlay visible but passes mouse clicks to apps behind it
- **Always-on-top** — configurable, enabled by default
- **Minimize to tray** — optional system tray icon with live GPU% display
- **CSV logging** — periodic snapshots of all stats to file
- **Config versioning** — settings survive app updates
- **Atomic file writes** — prevents config corruption on crash
- **Portable mode** — fully self-contained, nothing written to system (optional)
- **Auto-update checker** — GitHub release tracking

### 🎯 GPU Support
**Automatic vendor detection with fallback chain:**
- NVIDIA → `pynvml` (primary) → `GPUtil` (fallback)
- AMD/Intel → `pywin32` (WMI) → `psutil` (basic)

---

## Installation

### Requirements
**Python 3.9+** on Windows 10+

### Quick Start
```bash
git clone https://github.com/reaprrr/PyDisplay.git
cd PyDisplay
python PyDisplay.pyw
```

On first launch the **Dependency Manager** opens automatically. Select which packages to install and click **LAUNCH**.

### Optional Dependencies
| Package | Purpose | Install if... |
|---------|---------|---------------|
| `psutil` | **REQUIRED** · CPU, RAM, disk, network | Always installed |
| `pynvml` | NVIDIA GPU stats | You have an NVIDIA GPU |
| `pywin32` | CPU clock speed, AMD/Intel GPU via WMI | You want advanced CPU/GPU data |
| `pystray` | System tray integration | You want minimize-to-tray |
| `GPUtil` | GPU fallback reader | Primary GPU reader fails |

---

## Usage

### Controls
| Action | Result |
|--------|--------|
| **Drag** top bar | Move overlay |
| **Resize** edges/corners | Resize window |
| **Ctrl + hover** | Show tooltips (click-through mode) |
| **Ctrl + click** section header | Collapse/expand section |
| **Wheel scroll** | Scroll section list |

### Buttons & Menus

#### Main Bar (Top)
| Button | Purpose |
|--------|---------|
| **⚙ SETTINGS** | Toggle features, change theme, reorder sections, adjust poll rate |
| **◈ THEME** | Open colour picker · customise every element · one **Dropdown Tools** swatch controls all tool buttons |
| **? HELP** | Quick Reference panel (buttons, shortcuts, tooltips) |
| **−** | Minimize to tray (if `pystray` installed) |
| **×** | Close |

#### Dropdowns
**▶ TOOLS** buttons expand to reveal utility menus:

**Memory Tools**
```
🧹 MEMORY CLEAN  → Safe Clean | Aggressive Clean | Re-run
```

**Network Tools**
```
🚀 SPEED TEST    → Measures ping, download, upload
⌖ IP LOOKUP     → Fetches public IP + location
```

### Settings Panel
- **Always-on-Top** — keep overlay above all windows
- **Click-Through** — passes clicks to apps behind overlay
- **Lock Position** — prevent accidental movement
- **Tray Mode** — minimize to system tray (requires `pystray`)
- **Poll Rate** — update frequency (50ms–5s)
- **Log Interval** — CSV logging period (off, 5s, 10s, 30s, 1m, 5m)
- **Temp Unit** — Celsius or Fahrenheit
- **Section Visibility** — show/hide GPU, CPU, Memory, Network, Disk, Storage
- **Section Order** — drag-and-drop reorder
- **Export/Import** — backup and share settings

---

## Theming

### Quick Themes
Open **◈ THEME** and select from:
- 🌑 **Dark** (default) — sleek dark background
- ☀️ **Light** — light background, readable in bright rooms
- 💻 **Terminal** — monochrome, hacker aesthetic
- ❄️ **Ice** — cool blues and cyans
- 🌅 **Sunset** — warm oranges and purples
- 🌙 **Midnight** — deep blues with bright accents

### Custom Themes
Click the **colour picker button (◈)** to:
- Adjust **Background**, **Panel**, **Border**, **Text** base colours
- Customise **Section Accents** (GPU, CPU, Memory, Network, Disk, Storage)
- Control **Dropdown Tools** button colour (applies to all tool buttons)
- Save/load themes by name
- Import/export as JSON

### Colour Swatches
One click opens a full-screen colour picker with hex input, live preview, and save.

---

## Dependency Manager

Accessible on app startup or via **⚙ Settings → Dependencies**.

| Action | Behaviour |
|--------|-----------|
| **⌕ Update** | Check GitHub for new PyDisplay release |
| **↻ Check Dep Updates** | Check PyPI for newer versions of installed packages |
| **✕ Uninstall All** | Remove all deps with confirmation · post-removal verification |
| **Check for Duplicates** | Scan for packages installed in multiple locations |
| **? Failed** | Click a failed status label to see exact error message |

---

## Data & Config

All files stored in:
- **Portable mode**: `<PyDisplay folder>/` (if enabled)
- **Standard mode**: `%APPDATA%\PyDisplay\`

| File | Contents |
|------|----------|
| `PyDisplay_pos.json` | Window position, size, settings, active theme |
| `PyDisplay_theme_Default.json` | Default theme (auto-created) |
| `PyDisplay_theme_*.json` | Custom saved themes |
| `PyDisplay_log.txt` | Periodic stats snapshots (if logging enabled) |
| `PyDisplay_install.log` | Dependency install/remove history |
| `PyDisplay_error.log` | Non-fatal error log |

---

## Changelog
See [CHANGELOG.md](CHANGELOG.md) for full release history and detailed improvements.

### Latest (v1.0.6)
- ✨ **Portable Mode** — fully self-contained configuration option
- 🐛 **Fixed** dialog positioning, atomic config writes, race conditions
- 📋 **Always-on-Top** now enabled by default
- 🎨 **Improved** button layout on dependency page

---

## Troubleshooting

### App won't start
- Ensure Python 3.9+ is installed
- Run `python PyDisplay.pyw` from command line to see error messages

### GPU not detected
- NVIDIA: Install `pynvml` via Dependency Manager
- AMD/Intel: Install `pywin32` for WMI support; falls back to `psutil` (basic) if unavailable

### Memory Cleaner not working
- Right-click `PyDisplay.pyw` → Run as administrator
- Memory Cleaner requires elevated privileges

### Tray mode not available
- Install `pystray`: Dependency Manager will prompt or run `pip install pystray`

### Config/logs not saving
- Check `%APPDATA%\PyDisplay\` has write permissions
- Or enable Portable Mode to store everything in the script folder

---

## Performance

Designed to be lightweight:
- **CPU**: <1% idle, <3% with speed test
- **RAM**: ~80–120 MB
- **GPU**: Minimal impact (vendor libs do the heavy lifting)

Adjust **Poll Rate** in Settings to reduce CPU usage on slower systems.

---

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl + Hover` | Show tooltips (click-through mode only) |
| `Ctrl + Click` (section header) | Collapse/expand section |
| `Drag` (top bar) | Move overlay |
| `Drag` (edges/corners) | Resize |

---

## Development

### Project Structure
```
PyDisplay/
├── PyDisplay.pyw          # Main app (single-file)
├── README.md              # This file
├── CHANGELOG.md           # Version history
└── .gitignore             # Git ignore rules
```

### Contributing
Found a bug? Have an idea? [Open an issue](https://github.com/reaprrr/PyDisplay/issues) or submit a pull request.

---

## License
MIT License — see LICENSE file for details.

---

<div align="center">

**[⬆ back to top](#pydisplay)**

Made with ❤️ for Windows power users.

</div>

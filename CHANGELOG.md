# Changelog

All notable changes to PyDisplay will be documented here.

---

## [1.0.0] - 2026-03-01

### Initial Release

#### Core Features
- Real-time GPU, CPU, RAM, network, and disk monitoring via always-on-top overlay
- Click-through mode with adjustable opacity
- Drag-to-reposition with persistent position saving across sessions
- Minimize to system tray with live GPU percentage rendered into the tray icon
- Full theme support — built-in themes plus custom theme creation and validation
- Config versioning to handle settings migrations cleanly

#### Code & Architecture
- Single-file `.pyw` — no installer, no bloat
- `_BASE_FONT_SIZE` constant introduced for consistent font scaling across the app
- `_open_settings` refactored from ~772 lines into 11 focused helper methods
- `_build` refactored from ~510 lines into 7 focused helper methods
- Theme validation with safe fallback defaults on load
- Startup opacity bug fixed — `wm_attributes("-alpha")` now guarded to prevent invisible window on launch
- All error paths routed through `_log_error()` helper for consistent logging
- Dead imports removed (`ctypes.wintypes`)
- Popup z-order system implemented to prevent popups rendering behind the main window
- `_ALL_POPUP_ATTRS` promoted to class-level constant (previously rebuilt every 50ms)
- Tooltip attributes moved fully to instance scope

#### Dependency Checker
- Built-in dependency checker window on launch with one-click installs
- "Check for Updates" button to check for outdated Python packages
- "Check for App Update" button — hits the GitHub Releases API and reports whether a newer version of PyDisplay is available
- Both update buttons right-aligned and styled consistently in the dep window
- Update status messages display inline in the dep window header row
- Inline `tkinter.messagebox` import in crash handler kept as intentional lazy load

#### Project
- `README.md` with feature overview, requirements, and usage instructions
- `requirements.txt` with core and optional dependencies annotated
- `.gitignore` excluding runtime data files and cache
- `_APP_VERSION` and `_GITHUB_REPO` constants at the top of the file for easy version bumping on future releases

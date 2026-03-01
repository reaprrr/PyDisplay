# PyDisplay — Changelog

---

## v1.0.3 — 2026-03-01

### Added
- **Version number in main title bar** — "PyDisplay - v1.0.3" now appears in the top-right corner of the main overlay; auto-updates with `_APP_VERSION`
- **Version number on Dependency Setup page** — `v1.0.3` now appears beside the **PyDisplay** label in the top-right of the dependency setup header; auto-updates with `_APP_VERSION`
- **⌕ Update button** — compact app-update button placed directly to the right of the version label on the dependency setup page; replaces the old full-size button at the bottom
- **Check Dep Updates button moved** — relocated to the legend row beside `* required package` for a cleaner layout; renamed from "Check for Updates" to "Check Dep Updates"
- **Uninstall All button** — new red button in the bottom action row; uninstalls all installed dependencies with a confirmation popup, live progress feedback, and post-removal verification
  - Handles package aliases (`pynvml` / `nvidia-ml-py`, `GPUtil` / `gputil`) to ensure complete removal
  - Confirmation popup styled to match the dep page with custom title bar and `overrideredirect` (no OS chrome)
  - Reports packages that could not be removed
- **Clickable `? Failed` error status** — all package status labels now show `? Failed` (yellow, clickable) instead of `✘ failed` (red, static) when an install or removal fails; clicking opens a styled error detail popup showing the exact error message
- **Unified status message location** — all install, remove, update, and duplicate-check status messages now appear on the legend row beside **Check Dep Updates** instead of in a separate floating log row
- **HELP? Quick Reference updated** — documents Memory Tools, Network Tools, and the unified Dropdown Tools colour swatch

### Changed
- Version font on Dependency Setup page set to `BASE_FONT_SIZE - 1` (1px larger than initial)
- Separate log label row removed — `log_var` now aliased to `_upd_status_var` so all messages route to one place
- App version bumped to `1.0.3`

### Fixed
- `pywin32` no longer shows "failed" after a successful install — import check now skipped for pywin32 (DLLs require a fresh process); verified via `importlib.metadata` instead
- pywin32 post-install script no longer called — it was triggering a Windows system dialog (`Error installing pythoncom314.dll`) when another process had the DLL locked; pywin32 works correctly on next launch without it
- `wmi` install failure during pywin32 setup is now non-fatal — wmi loads correctly after restart
- `_appdata_dir` undefined error in batch install/remove worker — replaced with the global `_APP_DIR` constant throughout
- `exc` variable capture in `root.after` lambdas fixed — all error strings now captured as `_exc_str = str(exc)` before scheduling

---

## v1.0.2 — 2026-03-01

### Added
- **Memory Cleaner** — fully integrated Windows memory cleaning tool, accessible via a new `▶ TOOLS` dropdown under the `▸ MEMORY` section, matching the look and behaviour of the Network Tools dropdown
  - **Safe Clean** — trims process working sets, flushes the modified page list, clears file system & registry caches; safe for gaming and browsers
  - **Aggressive Clean** — all Safe steps plus standby list purge and memory page combination (may cause a brief stutter)
  - Popout window with mode selector (Safe / Aggressive), live step-by-step output log, progress feedback, and before/after RAM usage display
  - Output auto-formats large values: e.g. `1.2 GB freed` instead of raw MB
  - Run / Re-run button with animated spinner while cleaning is in progress
  - Covers all operations from **Memory Reduct v3.5.2** by henrypp (working set trim, modified page flush, file system cache, registry cache, standby list, memory combining, low-memory notification, heap compaction)
  - Privileges (SeDebugPrivilege, SeIncreaseQuotaPrivilege, etc.) requested automatically; failures reported gracefully without crashing
- **Unified Dropdown Tool Button Colour** — all tool buttons across every dropdown (Network and Memory) now share a single colour controlled by one **Dropdown Tools** swatch in the Theme Picker; changing it applies to all tabs at once
- **Memory Tools dropdown** — styled identically to the Network Tools dropdown: same popout behaviour, same hover/active colours, same layout

### Changed
- Memory section description updated internally to reflect new tooling
- App version bumped to `1.0.2`

### Fixed
- `HeapCompact` overflow error on 64-bit heap handles — handle array now uses `ctypes.c_void_p` to prevent `int too long to convert` crash
- Memory Tools dropdown geometry manager conflict resolved (`pack` vs `grid` mismatch that caused startup crash)
- Re-run on Memory Cleaner now correctly re-executes the full clean cycle rather than returning instantly

---

## v1.0.1 — Initial tracked release

- Core overlay: GPU, CPU, Memory, Network, Disk, Storage sections
- Network Tools: Speed Test, IP Lookup
- Theme Picker with named theme save/load
- Settings: always-on-top, click-through, lock position, tray mode, poll rate, log interval, temp unit, section visibility & order, export/import
- CSV session logging
- Auto-update check against GitHub latest tag
- Custom title bar, drag/resize, tooltip system

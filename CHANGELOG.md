# Changelog
All notable changes to PyDisplay will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [1.0.5] - 2026-03-01

### Removed
- **Dead function `_run_pywin32_postinstall`** — 81-line function was defined inside `_run_dependency_check` but never called anywhere in the codebase. The batch installer explicitly skips the post-install step for pywin32 (works after restart without it in modern pip). Removed the function and its stale reference comment.
- **`_last_disk_io` intermediate variable** — was assigned only to immediately initialize `_last_disk` on the next line. Collapsed into a single expression.
- **Unreachable `"remove"` branch in `_apply()`** — `action_var` was checked for the value `"remove"` but was never set to it anywhere. Removed the dead branch, the `to_uninstall` list, and the entire 60-line batch-uninstall loop in `_worker` that could never be reached as a result.
- **Duplicate `dlg.update_idletasks()` call** — back-to-back identical calls in the IP lookup popup positioning block; one removed.
- **`_initial_action = "keep"` single-use variable** — used only to pass its value to `tk.StringVar()` on the very next line. Inlined directly.
- **Duplicate local colour constants inside `_run_dependency_check`** — `BG`, `PANEL`, `BORDER`, `TEXT`, `SUBTEXT`, `GREEN`, and `RED` were re-declared as locals with the same hex values as the module-level globals. Removed the local copies.
- **Inline pip uninstall logic (duplicated)** — the 35-line block covering pip candidate resolution, `sys.modules` purging, and manual file removal from site-packages was copy-pasted in two places (`_worker_single` and the batch `_worker`). Replaced both with a single call to the new shared helper.
- **Inline GPUtil setuptools preamble (duplicated)** — the `subprocess.run` block that installs `setuptools` before GPUtil (required on Python 3.12+ due to removed `distutils`) appeared identically in both install paths. Replaced both with a call to the new shared helper.
- **`if _wmi_proc.returncode != 0: pass` no-op** — the conditional did nothing and was removed; the existing comment explaining the non-fatal nature of wmi install failures is retained.

### Added
- **`_pip_uninstall_pkg(pip_name, imp_name)`** — shared helper that handles the full uninstall sequence: pip candidate aliases, `sys.modules` cache purge, manual file removal fallback, and a final importability check. Used by both the single-row action button and the batch install/apply worker.
- **`_pip_install_setuptools_for_gputil()`** — shared helper that installs `setuptools` before GPUtil to restore `distutils` on Python 3.12+. Used by both install paths.
- **`YELLOW` added to module-level colour palette** — was previously only defined as a local inside `_run_dependency_check`, making it inconsistent with every other colour constant and unavailable outside that function.

### Changed
- **Colour constants moved earlier in module** — `BG`, `PANEL`, `BORDER`, and the full palette are now defined before `_run_dependency_check` (and before `import psutil`) so the dependency-checker UI can reference the module-level values directly. `_load_position()` still overwrites them at startup with the saved theme.

---

## [1.0.4] - 2026-03-01

### Changed
- **Extracted shared `_append_to_log()` base function** — `_write_crash_log` and `_log_error` no longer duplicate file-open boilerplate.
- **Eliminated duplicate `y = ay - dh` assignment** in speedtest popup positioning.
- **Collapsed identical `_mem_safe_clean` and `_mem_aggressive_clean` methods** into one method + alias.
- **Merged four parallel colour dicts in the Theme Picker** (`_default_colors`, `_recolor_keys`, `_recolor_cmds`, `_current_colors`) into a single `_recolor_data` dict; the four dicts are now one-liner derivations of it, eliminating sync issues.
- **Replaced `_save_reopen_picker`'s manual read-merge-write logic** with a single `_write_config(...)` call.
- **Promoted `_has_saved_position()`** from inside the `__main__` block to module level, alongside `_dep_skip_flagged` and `_reopen_picker_flagged`.
- **Extracted duplicated `_centre_on_parent` / `_track_parent` pattern** from `_save_theme` and `_load_theme` into a shared `_track_popup_to_parent()` method on `App`.
- **Replaced 5 inline `import importlib.metadata` calls** inside worker threads with a single top-level import (`_importlib_metadata`).
- **Removed dead no-op conditional**: `accent = self._color_tools if aggressive else self._color_tools`.

---

## [1.0.3] - 2026-03-01

### Added
- **Version number in main title bar** — "PyDisplay - v1.0.3" now appears in the top-right corner of the main overlay; auto-updates with `_APP_VERSION`.
- **Version number on Dependency Setup page** — `v1.0.3` now appears beside the **PyDisplay** label in the top-right of the dependency setup header; auto-updates with `_APP_VERSION`.
- **⌕ Update button** — compact app-update button placed directly to the right of the version label on the dependency setup page; replaces the old full-size button at the bottom.
- **Check Dep Updates button moved** — relocated to the legend row beside `* required package` for a cleaner layout; renamed from "Check for Updates" to "Check Dep Updates".
- **Uninstall All button** — new red button in the bottom action row; uninstalls all installed dependencies with a confirmation popup, live progress feedback, and post-removal verification.
  - Handles package aliases (`pynvml` / `nvidia-ml-py`, `GPUtil` / `gputil`) to ensure complete removal.
  - Confirmation popup styled to match the dep page with custom title bar and `overrideredirect` (no OS chrome).
  - Reports packages that could not be removed.
- **Clickable `? Failed` error status** — all package status labels now show `? Failed` (yellow, clickable) instead of `✘ failed` (red, static) when an install or removal fails; clicking opens a styled error detail popup showing the exact error message.
- **Unified status message location** — all install, remove, update, and duplicate-check status messages now appear on the legend row beside **Check Dep Updates** instead of in a separate floating log row.
- **HELP? Quick Reference updated** — documents Memory Tools, Network Tools, and the unified Dropdown Tools colour swatch.

### Changed
- Version font on Dependency Setup page set to `BASE_FONT_SIZE - 1` (1px larger than initial).
- Separate log label row removed — `log_var` now aliased to `_upd_status_var` so all messages route to one place.
- App version bumped to `1.0.3`.

### Fixed
- `pywin32` no longer shows "failed" after a successful install — import check now skipped for pywin32 (DLLs require a fresh process); verified via `importlib.metadata` instead.
- pywin32 post-install script no longer called — it was triggering a Windows system dialog (`Error installing pythoncom314.dll`) when another process had the DLL locked; pywin32 works correctly on next launch without it.
- `wmi` install failure during pywin32 setup is now non-fatal — wmi loads correctly after restart.
- `_appdata_dir` undefined error in batch install/remove worker — replaced with the global `_APP_DIR` constant throughout.
- `exc` variable capture in `root.after` lambdas fixed — all error strings now captured as `_exc_str = str(exc)` before scheduling.

---

## [1.0.2] - 2026-03-01

### Added
- **Memory Cleaner** — fully integrated Windows memory cleaning tool, accessible via a new `▶ TOOLS` dropdown under the `▸ MEMORY` section, matching the look and behaviour of the Network Tools dropdown.
  - **Safe Clean** — trims process working sets, flushes the modified page list, clears file system & registry caches; safe for gaming and browsers.
  - **Aggressive Clean** — all Safe steps plus standby list purge and memory page combination (may cause a brief stutter).
  - Popout window with mode selector (Safe / Aggressive), live step-by-step output log, progress feedback, and before/after RAM usage display.
  - Output auto-formats large values: e.g. `1.2 GB freed` instead of raw MB.
  - Run / Re-run button with animated spinner while cleaning is in progress.
  - Covers all operations from **Memory Reduct v3.5.2** by henrypp (working set trim, modified page flush, file system cache, registry cache, standby list, memory combining, low-memory notification, heap compaction).
  - Privileges (SeDebugPrivilege, SeIncreaseQuotaPrivilege, etc.) requested automatically; failures reported gracefully without crashing.
- **Unified Dropdown Tool Button Colour** — all tool buttons across every dropdown (Network and Memory) now share a single colour controlled by one **Dropdown Tools** swatch in the Theme Picker; changing it applies to all tabs at once.
- **Memory Tools dropdown** — styled identically to the Network Tools dropdown: same popout behaviour, same hover/active colours, same layout.

### Changed
- Memory section description updated internally to reflect new tooling.
- App version bumped to `1.0.2`.

### Fixed
- `HeapCompact` overflow error on 64-bit heap handles — handle array now uses `ctypes.c_void_p` to prevent `int too long to convert` crash.
- Memory Tools dropdown geometry manager aconflict resolved (`pack` vs `grid` mismatch that caused startup crash).
- Re-run on Memory Cleaner now correctly re-executes the full clean cycle rather thana returning instantly.

---

## [1.0.1] - Initial tracked release

- Core overlay: GPU, CPU, Memory, Network, Disk, Storage sections.
- Network Tools: Speed Test, IP Lookup.
- Theme Picker with named theme save/load.
- Settings: always-on-top, click-through, lock position, tray mode, poll rate, log interval, temp unit, section visibility & order, export/import.
- CSV session logging.
- Auto-update check against GitHub latest tag.
- Custom title bar, drag/resize, tooltip system.

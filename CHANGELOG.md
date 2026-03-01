# PyDisplay Changelog

---

## v1.0.4 — 2026-03-01

### Code Quality & Refactoring
- Extracted shared `_append_to_log()` base function — `_write_crash_log` and `_log_error` no longer duplicate file-open boilerplate
- Eliminated duplicate `y = ay - dh` assignment in speedtest popup positioning
- Collapsed identical `_mem_safe_clean` and `_mem_aggressive_clean` methods into one method + alias
- Merged four parallel colour dicts in the Theme Picker (`_default_colors`, `_recolor_keys`, `_recolor_cmds`, `_current_colors`) into a single `_recolor_data` dict; the four dicts are now one-liner derivations of it, eliminating sync issues
- Replaced `_save_reopen_picker`'s manual read-merge-write logic with a single `_write_config(...)` call
- Promoted `_has_saved_position()` from inside the `__main__` block to module level, alongside `_dep_skip_flagged` and `_reopen_picker_flagged`
- Extracted duplicated `_centre_on_parent` / `_track_parent` pattern from `_save_theme` and `_load_theme` into a shared `_track_popup_to_parent()` method on `App`
- Replaced 5 inline `import importlib.metadata` calls inside worker threads with a single top-level import (`_importlib_metadata`)
- Removed dead no-op conditional: `accent = self._color_tools if aggressive else self._color_tools`

---

## v1.0.3

### Bug Fixes
- Fixed "Reopen Choose Position" checkbox not persisting correctly across sessions
  - Root cause: `_save_reopen_picker(False)` was called after the picker was shown, silently clearing the user's saved preference
  - Fix: removed the erroneous post-show call; `_save_reopen_picker` now correctly writes `false` rather than deleting the key

---

## v1.0.2 and earlier

Initial release series. Core features:
- Live GPU, CPU, memory, network, disk, and storage monitoring via Win32 and optional dependencies (psutil, pynvml, pywin32, GPUtil, pystray)
- Draggable overlay with configurable position, opacity, and font size
- Per-section colour theming with save/load support
- Memory cleaner (safe and aggressive modes)
- Speedtest integration
- IP lookup
- System tray support (via pystray)
- Dependency manager with install/uninstall and auto-update checking

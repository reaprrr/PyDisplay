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

## [1.0.4] - Previous release

- See repository history for earlier changes.

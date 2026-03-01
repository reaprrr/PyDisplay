# PyDisplay — Changelog

---

## v1.0.3 — 2026-03-01

### Added
- **Version number in main title bar** — "PyDisplay - v1.0.3" now appears in the top-right corner of the main overlay; auto-updates with `_APP_VERSION`
- **Version number on Dependency Setup page** — `v1.0.3` now appears beside the **PyDisplay** label in the top-right of the dependency setup header; auto-updates with `_APP_VERSION`
- **HELP? Quick Reference updated** — the Help popup now documents all tools added in v1.0.2 and v1.0.3:
  - Memory section updated to mention the Memory Cleaner
  - New **Memory Tools** section listing `▶ TOOLS`, `🧹 MEMORY CLEAN`, `Safe Clean`, and `Aggressive Clean` with descriptions
  - Theme Picker section now explains that the **Dropdown Tools** colour swatch controls all tool buttons across both Network and Memory dropdowns simultaneously

### Changed
- Version font on Dependency Setup page set to `BASE_FONT_SIZE - 1` (1px larger than initial implementation)
- App version bumped to `1.0.3`

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

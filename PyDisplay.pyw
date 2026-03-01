import tkinter as tk
import tkinter.filedialog as _fd
import threading
import time
import json
import os
import datetime
import importlib
import importlib.metadata as _importlib_metadata
import subprocess
import sys
import traceback
import urllib.request
import socket
import ctypes
import glob


# ── App-wide constants ────────────────────────────────────────────────────────

# Suppress console windows spawned by subprocess calls on Windows
_NO_WIN = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0x08000000

# Config / data paths — single source of truth used everywhere
_APP_DIR    = os.path.join(os.environ.get("APPDATA", ""), "PyDisplay")
_CFG_PATH   = os.path.join(_APP_DIR, "PyDisplay_pos.json")
_LOG_PATH   = os.path.join(_APP_DIR, "PyDisplay_error.log")
_DATA_LOG_PATH      = os.path.join(_APP_DIR, "PyDisplay_log.txt")
_DEFAULT_THEME_PATH = os.path.join(_APP_DIR, "PyDisplay_theme_Default.json")
_THEME_DIR  = _APP_DIR   # themes live alongside the config
_FONT           = "Courier New" # single source of truth for the app font
_BASE_FONT_SIZE = 9             # default font size; all remap logic is relative to this
_CONFIG_VERSION  = 1             # increment when config schema changes; triggers migration
_APP_VERSION     = "1.0.4"       # increment on each release; checked against GitHub latest tag
_GITHUB_REPO     = "reaprrr/PyDisplay"

# Default display section order — defined once, referenced everywhere
_DEFAULT_SECTION_ORDER = ["gpu", "cpu", "mem", "net", "disk", "storage"]

# Win32 constants used by click-through and popup z-order pinning
_HWND_TOPMOST   = -1
_SWP_NOSIZE     = 0x0001
_SWP_NOMOVE     = 0x0002
_SWP_NOACTIVATE = 0x0010
_GWL_EXSTYLE    = -20
_WS_EX_LAYERED  = 0x80000
_WS_EX_TRANSPARENT = 0x20
_WS_EX_TOOLWINDOW  = 0x80
_WS_EX_APPWINDOW   = 0x40000
_LWA_ALPHA      = 0x2
_user32         = ctypes.windll.user32


# ── Memory Cleaner — Win32 constants & helpers ────────────────────────────────

_MC_TOKEN_ADJUST_PRIVILEGES  = 0x0020
_MC_TOKEN_QUERY              = 0x0008
_MC_SE_PRIVILEGE_ENABLED     = 0x00000002
_MC_PROCESS_QUERY_INFO       = 0x0400
_MC_PROCESS_SET_QUOTA        = 0x0100
_MC_SystemFileCacheInfo      = 0x15
_MC_SystemMemListInfo        = 0x50
_MC_SystemCombinePhysMem     = 0x82
_MC_SystemRegistryRecon      = 0x9E
_MC_MemEmptyWorkingSet       = 2
_MC_MemFlushModified         = 3
_MC_MemPurgeStandby          = 4
_MC_MemPurgeLowStandby       = 5

class _MC_LUID(ctypes.Structure):
    _fields_ = [("LowPart", ctypes.c_ulong), ("HighPart", ctypes.c_long)]

class _MC_LUID_ATTR(ctypes.Structure):
    _fields_ = [("Luid", _MC_LUID), ("Attributes", ctypes.c_ulong)]

class _MC_TOKEN_PRIVS(ctypes.Structure):
    _fields_ = [("PrivilegeCount", ctypes.c_ulong), ("Privileges", _MC_LUID_ATTR * 1)]

class _MC_FILECACHE_INFO(ctypes.Structure):
    _fields_ = [("CurrentSize", ctypes.c_size_t), ("PeakSize", ctypes.c_size_t),
                ("PageFaultCount", ctypes.c_ulong), ("MinimumWorkingSet", ctypes.c_size_t),
                ("MaximumWorkingSet", ctypes.c_size_t), ("Flags", ctypes.c_ulong)]

class _MC_COMBINE_INFO(ctypes.Structure):
    _fields_ = [("Handle", ctypes.c_void_p), ("PagesCombined", ctypes.c_ulonglong),
                ("Flags", ctypes.c_ulong)]

def _mc_enable_privilege(name):
    advapi32 = ctypes.windll.advapi32
    kernel32  = ctypes.windll.kernel32
    advapi32.OpenProcessToken.argtypes      = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_void_p)]
    advapi32.OpenProcessToken.restype       = ctypes.c_int
    advapi32.LookupPrivilegeValueW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.POINTER(_MC_LUID)]
    advapi32.LookupPrivilegeValueW.restype  = ctypes.c_int
    advapi32.AdjustTokenPrivileges.restype  = ctypes.c_int
    h = ctypes.c_void_p()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(),
                                     _MC_TOKEN_ADJUST_PRIVILEGES | _MC_TOKEN_QUERY,
                                     ctypes.byref(h)):
        return False
    luid = _MC_LUID()
    if not advapi32.LookupPrivilegeValueW(None, name, ctypes.byref(luid)):
        kernel32.CloseHandle(h); return False
    tp = _MC_TOKEN_PRIVS()
    tp.PrivilegeCount = 1; tp.Privileges[0].Luid = luid
    tp.Privileges[0].Attributes = _MC_SE_PRIVILEGE_ENABLED
    advapi32.AdjustTokenPrivileges(h, False, ctypes.byref(tp), ctypes.sizeof(tp), None, None)
    ok = kernel32.GetLastError() == 0
    kernel32.CloseHandle(h)
    return ok

def _mc_set_mem_list(cmd_val):
    cmd = ctypes.c_int(cmd_val)
    return ctypes.windll.ntdll.NtSetSystemInformation(
        _MC_SystemMemListInfo, ctypes.byref(cmd), ctypes.sizeof(cmd)) == 0

def _mc_get_ram_mb():
    """Return (total_mb, used_mb, free_mb)."""
    class _MEMSTATUS(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtVirtual", ctypes.c_ulonglong)]
    ms = _MEMSTATUS(); ms.dwLength = ctypes.sizeof(ms)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
    total = ms.ullTotalPhys / (1024**2)
    free  = ms.ullAvailPhys / (1024**2)
    return total, total - free, free

def _mc_run(aggressive=False, log_cb=None):
    """
    Run the full memory cleaning sequence in a background thread.
    log_cb(msg) is called with status lines for the UI to display.
    Returns MB freed (float).
    """
    def _log(msg):
        if log_cb: log_cb(msg)

    ntdll    = ctypes.windll.ntdll
    kernel32 = ctypes.windll.kernel32
    psapi    = ctypes.windll.psapi

    _, used_before, _ = _mc_get_ram_mb()

    # 1. Privileges
    for priv in ("SeDebugPrivilege", "SeIncreaseQuotaPrivilege",
                 "SeMaintainVolumePrivilege", "SeProfileSingleProcessPrivilege"):
        _mc_enable_privilege(priv)

    # 2. Flush process working sets
    _log("Flushing process working sets…")
    pid_arr  = (ctypes.c_ulong * 8192)()
    bytes_ret = ctypes.c_ulong()
    psapi.EnumProcesses(ctypes.byref(pid_arr), ctypes.sizeof(pid_arr), ctypes.byref(bytes_ret))
    n_pids = bytes_ret.value // ctypes.sizeof(ctypes.c_ulong)
    ok = 0
    for i in range(n_pids):
        pid = pid_arr[i]
        if not pid: continue
        h = kernel32.OpenProcess(_MC_PROCESS_QUERY_INFO | _MC_PROCESS_SET_QUOTA, False, pid)
        if h:
            psapi.EmptyWorkingSet(h)
            kernel32.SetProcessWorkingSetSizeEx(h, ctypes.c_size_t(-1), ctypes.c_size_t(-1), 0)
            kernel32.CloseHandle(h); ok += 1
    _log(f"  Trimmed {ok} processes")

    # 3. System working set
    _log("Flushing system working set…")
    _mc_set_mem_list(_MC_MemEmptyWorkingSet)

    # 4. Modified page list
    _log("Flushing modified page list…")
    _mc_set_mem_list(_MC_MemFlushModified)

    # 5. File cache
    _log("Clearing file system cache…")
    info    = _MC_FILECACHE_INFO()
    ret_len = ctypes.c_ulong(0)
    st = ntdll.NtQuerySystemInformation(_MC_SystemFileCacheInfo, ctypes.byref(info),
                                         ctypes.sizeof(info), ctypes.byref(ret_len))
    if st == 0:
        info.MinimumWorkingSet = ctypes.c_size_t(-1).value
        info.MaximumWorkingSet = ctypes.c_size_t(-1).value
        ntdll.NtSetSystemInformation(_MC_SystemFileCacheInfo, ctypes.byref(info), ctypes.sizeof(info))

    # 6. Registry cache
    _log("Flushing registry cache…")
    ntdll.NtSetSystemInformation(_MC_SystemRegistryRecon, None, 0)

    # 7. Combine duplicate pages
    _log("Combining duplicate memory pages…")
    ci = _MC_COMBINE_INFO()
    st = ntdll.NtSetSystemInformation(_MC_SystemCombinePhysMem, ctypes.byref(ci), ctypes.sizeof(ci))
    if st == 0 and ci.PagesCombined:
        _log(f"  Combined {ci.PagesCombined:,} pages ({ci.PagesCombined*4//1024} MB)")

    # 8. Low-memory notification
    _log("Signalling low-memory event…")
    cmd = ctypes.c_int(1)
    ntdll.NtSetSystemInformation(0x4A, ctypes.byref(cmd), ctypes.sizeof(cmd))

    # 9. Heap compact
    _log("Compacting heaps…")
    kernel32.HeapCompact.restype  = ctypes.c_size_t
    kernel32.HeapCompact.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    heap_arr = (ctypes.c_void_p * 64)()
    n = kernel32.GetProcessHeaps(64, ctypes.byref(heap_arr))
    for i in range(min(n, 64)):
        if heap_arr[i]: kernel32.HeapCompact(heap_arr[i], 0)

    # 10. DNS cache
    _log("Flushing DNS cache…")
    try: ctypes.windll.dnsapi.DnsFlushResolverCache()
    except Exception: pass

    # 11. Clipboard
    _log("Clearing clipboard…")
    u32 = ctypes.windll.user32
    if u32.OpenClipboard(None): u32.EmptyClipboard(); u32.CloseClipboard()

    # 12+13. Aggressive: standby lists
    if aggressive:
        _log("Purging low-priority standby list…")
        _mc_set_mem_list(_MC_MemPurgeLowStandby)
        _log("Purging full standby list…")
        _mc_set_mem_list(_MC_MemPurgeStandby)

    _, used_after, _ = _mc_get_ram_mb()
    freed = used_before - used_after
    return freed


# ── Config helpers ────────────────────────────────────────────────────────────

def _read_config():
    """Read and return the config dict from disk. Returns {} on any failure."""
    try:
        with open(_CFG_PATH) as _f:
            return json.load(_f)
    except Exception:
        return {}

def _write_config(data):
    """Merge data into the existing config and write it back to disk."""
    try:
        os.makedirs(_APP_DIR, exist_ok=True)
        existing = _read_config()
        existing.update(data)
        with open(_CFG_PATH, "w") as _f:
            json.dump(existing, _f, indent=2)
    except Exception as e:
        _log_error("_write_config", e)


def _append_to_log(text):
    """Append raw text to the error log file. Creates the file/dir if needed."""
    try:
        os.makedirs(_APP_DIR, exist_ok=True)
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        pass


def _write_crash_log(exc_text):
    """Write an unhandled exception to PyDisplay_error.log."""
    ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    _append_to_log(f"\n{'─'*72}\n  CRASH — {ts}\n{'─'*72}\n{exc_text}\n")


def _log_error(context, exc):
    """Append a non-fatal error to the error log with context label."""
    ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    _append_to_log(f"\n  [{ts}]  {context}: {exc}\n")


def _can_import(imp_name):
    """Check if a package is importable. imp_name may be 'A|B' to try alternatives.
    Purges sys.modules before checking so post-uninstall state is always accurate."""
    for name in imp_name.split("|"):
        name = name.strip()
        # Remove any cached entry so we always hit the filesystem
        for key in list(sys.modules.keys()):
            if key == name or key.startswith(name + "."):
                sys.modules.pop(key, None)
        importlib.invalidate_caches()
        # GPUtil uses distutils.version which was removed in Python 3.12+.
        # Inject the setuptools shim so the import check succeeds when setuptools is installed.
        if name == "GPUtil":
            for key in list(sys.modules.keys()):
                if key == "distutils" or key.startswith("distutils."):
                    sys.modules.pop(key, None)
            importlib.invalidate_caches()
            try:
                import setuptools  # noqa — activates distutils shim
                import distutils.version
            except Exception:
                pass
        try:
            importlib.import_module(name)
            return True
        except ImportError:
            continue
    return False


# ── Dependency checker — runs before anything else ───────────────────────────

def _make_titlebar(window, bg, border, subtext, red, on_close=None,
                   title_text=None, title_fg=None, title_bg=None,
                   separator_color=None):
    """
    Build a standard draggable title bar on *window* and return the bar frame.
    Adds a close button (✕) on the left that calls on_close (defaults to
    window.destroy).  If title_text is given, a centred label is placed as the
    drag handle; otherwise an invisible spacer fills that role.
    A 1-px separator is packed below the bar automatically.
    """
    drag = {"x": 0, "y": 0}
    def _drag_start(e): drag["x"] = e.x_root; drag["y"] = e.y_root
    def _drag_move(e):
        dx = e.x_root - drag["x"]; dy = e.y_root - drag["y"]
        window.geometry(f"+{window.winfo_x()+dx}+{window.winfo_y()+dy}")
        drag["x"] = e.x_root; drag["y"] = e.y_root

    tb = tk.Frame(window, bg=bg, height=28)
    tb.pack(fill="x")
    tb.pack_propagate(False)

    x_btn = tk.Label(tb, text=" ✕ ", bg=border, fg=subtext,
                     font=(_FONT, _BASE_FONT_SIZE, "bold"), cursor="hand2", padx=2, pady=2)
    x_btn.pack(side="left", padx=(4, 0))
    _close = on_close if on_close else window.destroy
    x_btn.bind("<Button-1>", lambda e: _close())
    x_btn.bind("<Enter>",    lambda e: x_btn.config(fg=red))
    x_btn.bind("<Leave>",    lambda e: x_btn.config(fg=subtext))

    if title_text:
        handle = tk.Label(tb, text=title_text, bg=title_bg or bg,
                          fg=title_fg or subtext,
                          font=(_FONT, _BASE_FONT_SIZE, "bold"), cursor="fleur")
        handle.pack(side="left", fill="both", expand=True)
    else:
        handle = tk.Label(tb, text="", bg=bg, cursor="fleur")
        handle.pack(side="left", fill="both", expand=True)

    for w in (tb, handle):
        w.bind("<ButtonPress-1>", _drag_start)
        w.bind("<B1-Motion>",     _drag_move)

    sep_color = separator_color or border
    tk.Frame(window, bg=sep_color, height=1).pack(fill="x")
    return tb


def _has_nvidia_gpu():
    """Return True if an NVIDIA GPU is detected on this system."""
    # Method 1: check for nvidia-smi or NVIDIA driver DLL in system dirs
    _system32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    if os.path.exists(os.path.join(_system32, "nvapi64.dll")) or        os.path.exists(os.path.join(_system32, "nvcuda.dll")):
        return True
    # Method 2: check Windows registry for NVIDIA display adapter
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\OpenGLDrivers") as _k:
            i = 0
            while True:
                try:
                    name, _, _ = winreg.EnumValue(_k, i)
                    if "nvidia" in name.lower() or "nvoglv" in name.lower():
                        return True
                    i += 1
                except OSError:
                    break
    except Exception:
        pass
    # Method 3: scan device manager via registry
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Enum\PCI") as _k:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(_k, i)
                    # NVIDIA PCI vendor ID is 10DE
                    if "VEN_10DE" in sub.upper():
                        return True
                    i += 1
                except OSError:
                    break
    except Exception:
        pass
    return False


def _run_dependency_check():
    """Pre-launch dependency manager. Returns dict with launch/skip_dep_check keys."""

    _nvidia = _has_nvidia_gpu()

    PACKAGES = [
        # (display_name, pip_name, import_name, required, description)
        # ── Required ──────────────────────────────────────────────────────────
        ("psutil",  "psutil",  "psutil",    True,    "Core system stats — CPU, RAM, disk & network usage"),
        ("pynvml",  "pynvml",  "pynvml",    _nvidia, "Reads live data from your NVIDIA GPU (temp, usage, VRAM)"),
        # ── Optional ──────────────────────────────────────────────────────────
        ("pywin32", "pywin32", "pythoncom", False,   "Enables CPU clock speed & AMD/Intel GPU stats on Windows"),
        ("pystray", "pystray", "pystray",   False,   "Adds a system tray icon so you can minimize to the taskbar"),
        ("GPUtil",  "GPUtil",  "GPUtil",    False,   "Backup GPU reader if pynvml & pywin32 both can't detect your GPU"),
    ]

    BG      = "#0a0a0f"
    PANEL   = "#111118"
    BORDER  = "#1e1e2e"
    TEXT    = "#e0e0f0"
    SUBTEXT = "#6868a0"
    GREEN   = "#39ff7f"
    RED     = "#ff3860"
    YELLOW  = "#ffcc00"
    ACCENT  = "#00ffe5"

    result = {"launch": False, "skip_dep_check": False, "reopen_placement": False}

    root = tk.Tk()
    root.title("PyDisplay — Dependencies")
    root.configure(bg=BG)
    root.resizable(False, False)
    root.wm_attributes("-topmost", True)
    root.overrideredirect(True)
    root.minsize(460, 0)

    # ── Custom title bar with X ───────────────────────────────────────────────
    _make_titlebar(root, PANEL, BORDER, SUBTEXT, RED, on_close=root.destroy)

    # ── Header ────────────────────────────────────────────────────────────────
    hdr_frame = tk.Frame(root, bg=BG)
    hdr_frame.pack(fill="x", padx=16, pady=(12, 0))
    _ver_frame = tk.Frame(hdr_frame, bg=BG)
    _ver_frame.pack(side="right")
    tk.Label(_ver_frame, text="PyDisplay", bg=BG, fg=ACCENT,
             font=(_FONT, _BASE_FONT_SIZE, "bold")).pack(side="left")
    tk.Label(_ver_frame, text=f"  v{_APP_VERSION}", bg=BG, fg=SUBTEXT,
             font=(_FONT, _BASE_FONT_SIZE - 1)).pack(side="left")
    _app_upd_btn = tk.Label(_ver_frame, text="⌕ Update",
                            bg=BORDER, fg=ACCENT,
                            font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), cursor="hand2",
                            padx=6, pady=2)
    _app_upd_btn.pack(side="left", padx=(10, 0))
    tk.Label(hdr_frame, text="  ·  dependency setup", bg=BG, fg=TEXT,
             font=(_FONT, _BASE_FONT_SIZE, "bold")).pack(side="left")
    tk.Frame(root, bg=BORDER, height=1).pack(fill="x", padx=12, pady=(8, 0))

    # ── Helper ────────────────────────────────────────────────────────────────
    def _get_version(pip_name):
        try:
            return _importlib_metadata.version(pip_name)
        except Exception:
            return ""

    _install_log = os.path.join(_APP_DIR, "PyDisplay_install.log")

    def _run_pywin32_postinstall(appdata_dir, install_log):
        """Run pywin32's post-install step. Must be called after pip installs pywin32."""
        _post_log = []
        _post_ok  = False
        # CREATE_NO_WINDOW prevents a cmd console flashing/freezing on screen
        # (uses the module-level _NO_WIN constant)

        _scripts_dirs = [
            os.path.join(os.path.dirname(sys.executable), "Scripts"),
            os.path.join(sys.prefix, "Scripts"),
        ]
        try:
            import site as _site
            for _sp in _site.getsitepackages():
                _scripts_dirs.append(os.path.join(os.path.dirname(_sp), "Scripts"))
        except Exception:
            pass

        for _sdir in _scripts_dirs:
            for _exe in ("pywin32_postinstall.exe", "pywin32_postinstall"):
                _ep = os.path.join(_sdir, _exe)
                if os.path.exists(_ep):
                    _post_log.append(f"trying entry point: {_ep}")
                    try:
                        _pr = subprocess.run([_ep, "-install"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            creationflags=_NO_WIN, timeout=30)
                        _post_log.append(f"exit={_pr.returncode} stdout={_pr.stdout.strip()!r} stderr={_pr.stderr.strip()!r}")
                        if _pr.returncode == 0:
                            _post_ok = True
                            break
                    except subprocess.TimeoutExpired:
                        _post_log.append("timed out — skipping")
            _py = os.path.join(_sdir, "pywin32_postinstall.py")
            if not _post_ok and os.path.exists(_py):
                _post_log.append(f"trying script: {_py}")
                try:
                    _pr = subprocess.run([sys.executable, _py, "-install"],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                        creationflags=_NO_WIN, timeout=30)
                    _post_log.append(f"exit={_pr.returncode} stdout={_pr.stdout.strip()!r} stderr={_pr.stderr.strip()!r}")
                    if _pr.returncode == 0:
                        _post_ok = True
                except subprocess.TimeoutExpired:
                    _post_log.append("timed out — skipping")
            if _post_ok:
                break

        if not _post_ok:
            _post_log.append("trying: python -m pywin32_postinstall -install")
            try:
                _pr = subprocess.run(
                    [sys.executable, "-m", "pywin32_postinstall", "-install"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    creationflags=_NO_WIN, timeout=30)
                _post_log.append(f"exit={_pr.returncode} stdout={_pr.stdout.strip()!r} stderr={_pr.stderr.strip()!r}")
                if _pr.returncode == 0:
                    _post_ok = True
            except subprocess.TimeoutExpired:
                _post_log.append("timed out — skipping")

        if not _post_ok:
            _post_log.append("trying in-process post_install()")
            try:
                import pywin32_postinstall as _pw32pi
                _pw32pi.install()
                _post_ok = True
                _post_log.append("in-process post_install() succeeded")
            except Exception as _pie:
                _post_log.append(f"in-process failed: {_pie}")

        try:
            os.makedirs(appdata_dir, exist_ok=True)
            with open(install_log, "a", encoding="utf-8") as _lf:
                _lf.write("\n" + "─"*72 + "\n")
                _lf.write(f"  pywin32 post-install log (ok={_post_ok}):\n")
                for _ll in _post_log:
                    _lf.write(f"    {_ll}\n")
                _lf.write("─"*72 + "\n")
        except Exception:
            pass

    # row_data: list of dicts with keys: disp, pip_name, imp_name, required,
    #           installed (live), action_var ("install"|"remove"|"keep"), widgets
    row_data = []
    _row_errors = {}  # pip_name → error string, for clickable "? Failed" status

    def _set_failed(status_lbl, pip_name, err_str):
        """Mark a status label as failed and make it clickable to show the error."""
        _row_errors[pip_name] = err_str
        status_lbl.config(text="?  Failed", fg=YELLOW, cursor="hand2")
        def _show_err(e, pn=pip_name, sl=status_lbl):
            err = _row_errors.get(pn, "No error details available.")
            dlg = tk.Toplevel(root)
            dlg.configure(bg=PANEL)
            dlg.overrideredirect(True)
            dlg.attributes("-topmost", True)
            dlg.lift()
            dlg.focus_force()
            dlg.grab_set()
            _make_titlebar(dlg, PANEL, BORDER, SUBTEXT, RED,
                           on_close=dlg.destroy,
                           title_text=f"PyDisplay  ·  {pn} error", title_fg=TEXT, title_bg=PANEL)
            df = tk.Frame(dlg, bg=PANEL, padx=16, pady=12)
            df.pack(fill="both")
            tk.Label(df, text=f"Install error — {pn}", bg=PANEL, fg=RED,
                     font=(_FONT, _BASE_FONT_SIZE, "bold")).pack(anchor="w")
            tk.Frame(df, bg=BORDER, height=1).pack(fill="x", pady=(4, 8))
            tk.Label(df, text=err, bg=BORDER, fg=TEXT,
                     font=(_FONT, _BASE_FONT_SIZE - 2), anchor="w", justify="left",
                     padx=8, pady=8, wraplength=380).pack(fill="x")
            tk.Frame(df, bg=PANEL, height=6).pack()
            close_btn = tk.Label(df, text="Close", bg=BORDER, fg=SUBTEXT,
                                 font=(_FONT, _BASE_FONT_SIZE, "bold"), cursor="hand2",
                                 padx=12, pady=4)
            close_btn.pack(anchor="e")
            close_btn.bind("<Button-1>", lambda e: dlg.destroy())
            close_btn.bind("<Enter>", lambda e: close_btn.config(fg=TEXT))
            close_btn.bind("<Leave>", lambda e: close_btn.config(fg=SUBTEXT))
            dlg.update_idletasks()
            cx = root.winfo_x() + (root.winfo_width()  - dlg.winfo_reqwidth())  // 2
            cy = root.winfo_y() + (root.winfo_height() - dlg.winfo_reqheight()) // 2
            dlg.geometry(f"+{cx}+{cy}")
        status_lbl.bind("<Button-1>", _show_err)
        status_lbl.bind("<Enter>", lambda e: status_lbl.config(fg=GREEN))
        status_lbl.bind("<Leave>", lambda e: status_lbl.config(fg=YELLOW))

    # ── Package list ──────────────────────────────────────────────────────────
    # Single-button design: shows "Install" when missing, "Delete" when installed.
    # Clicking toggles the queued action; button text/colour always reflects
    # what WILL HAPPEN when you hit Launch — no separate Remove column needed.

    list_frame = tk.Frame(root, bg=BG)
    list_frame.pack(fill="x", padx=12, pady=(10, 4))
    list_frame.columnconfigure(0, minsize=76)   # name
    list_frame.columnconfigure(1, minsize=130)  # status
    list_frame.columnconfigure(2, weight=1)     # description
    list_frame.columnconfigure(3, minsize=100)  # action btn

    # Column headers
    for col, hdr in enumerate(["Package", "Status", "Description", "Action"]):
        tk.Label(list_frame, text=hdr, bg=BG, fg=SUBTEXT,
                 font=(_FONT, _BASE_FONT_SIZE, "bold"), anchor="w",
                 padx=6).grid(row=0, column=col, sticky="ew", pady=(0, 2))
    tk.Frame(root, bg=BORDER, height=1).pack(fill="x", padx=12, pady=(0, 2))

    data_frame = tk.Frame(root, bg=BG)
    data_frame.pack(fill="x", padx=12)
    data_frame.columnconfigure(0, minsize=76)
    data_frame.columnconfigure(1, minsize=130)
    data_frame.columnconfigure(2, weight=1)
    data_frame.columnconfigure(3, minsize=100)

    for i, (disp, pip_name, imp_name, required, desc) in enumerate(PACKAGES):
        installed = _can_import(imp_name)
        version   = _get_version(pip_name) if installed else ""
        row_bg    = PANEL if i % 2 == 0 else BG

        # Default queued action: keep — user must explicitly click Install
        _initial_action = "keep"

        action_var = tk.StringVar(value=_initial_action)

        # Col 0 — package name + required marker
        req_tag = " *" if required else ""
        tk.Label(data_frame, text=disp + req_tag, bg=row_bg, fg=TEXT,
                 font=(_FONT, _BASE_FONT_SIZE, "bold"), anchor="w",
                 padx=6, pady=8).grid(row=i, column=0, sticky="ew")

        # Col 1 — live status label
        if installed:
            st_text  = f"✔  v{version}" if version else "✔  installed"
            st_color = GREEN
        else:
            st_text  = "✘  missing"
            st_color = RED
        status_lbl = tk.Label(data_frame, text=st_text, bg=row_bg, fg=st_color,
                               font=(_FONT, _BASE_FONT_SIZE), anchor="w", padx=6, pady=8)
        status_lbl.grid(row=i, column=1, sticky="ew")

        # Col 2 — description
        tk.Label(data_frame, text=desc, bg=row_bg, fg=SUBTEXT,
                 font=(_FONT, _BASE_FONT_SIZE - 2), anchor="w",
                 padx=6, pady=8, justify="left").grid(row=i, column=2, sticky="ew")

        # Col 3 — single smart action button
        #   not installed  →  "＋ Install"  (click → immediately pip install)
        #   installed      →  "✕ Delete"   (click → immediately pip uninstall)
        #   while running  →  "  …"        (disabled until done)
        action_btn = tk.Label(data_frame, bg=row_bg,
                              font=(_FONT, _BASE_FONT_SIZE, "bold"), cursor="hand2",
                              padx=8, pady=5, anchor="center")
        action_btn.grid(row=i, column=3, sticky="ew", padx=(4, 6), pady=3)

        _inst_ref    = [installed]   # mutable — updated after each op
        _busy_ref    = [False]       # True while a pip op is running for this row

        def _refresh_action_btn(btn=action_btn, inst_ref=_inst_ref, busy_ref=_busy_ref, rb=row_bg):
            if busy_ref[0]:
                btn.config(text="  …  ", fg=SUBTEXT, bg=rb, cursor="arrow")
                return
            if inst_ref[0]:
                btn.config(text="✕  Delete ", fg=RED,   bg=BORDER, cursor="hand2")
            else:
                btn.config(text="＋ Install", fg=GREEN, bg=BORDER, cursor="hand2")

        _rd_ref = [None]

        def _click_action(e, rd_ref=_rd_ref,
                          inst_ref=_inst_ref, busy_ref=_busy_ref,
                          disp_s=disp, pip_name_s=pip_name, imp_name_s=imp_name):
            if busy_ref[0]:
                return  # already running
            # Lock the button immediately
            busy_ref[0] = True
            root.after(0, rd_ref[0]["refresh_btn"])

            def _worker_single():
                if inst_ref[0]:
                    # ── UNINSTALL ──────────────────────────────────────────────
                    root.after(0, lambda: log_var.set(f"Removing {disp_s}…"))
                    root.after(0, lambda: rd_ref[0]["status_lbl"].config(text="⏳  Removing…", fg=YELLOW))
                    root.after(0, lambda: _set_progress(0.1))
                    try:
                        _pip_candidates = (["pynvml", "nvidia-ml-py"] if pip_name_s == "pynvml"
                                           else ["GPUtil", "gputil"] if pip_name_s == "GPUtil"
                                           else [pip_name_s])
                        for _cand in _pip_candidates:
                            subprocess.run(
                                [sys.executable, "-m", "pip", "uninstall", _cand, "-y",
                                 "--disable-pip-version-check"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, creationflags=_NO_WIN)
                        importlib.invalidate_caches()
                        for _mod in list(sys.modules.keys()):
                            if _mod == imp_name_s or _mod.startswith(imp_name_s + "."):
                                sys.modules.pop(_mod, None)
                        # manual file removal if pip didn't register it
                        if _can_import(imp_name_s):
                            import importlib.util as _ilu, shutil as _sh
                            _spec = _ilu.find_spec(imp_name_s)
                            if _spec and _spec.origin:
                                _pkg_dir = os.path.dirname(_spec.origin)
                                if os.path.basename(_pkg_dir).lower() == imp_name_s.lower():
                                    _sh.rmtree(_pkg_dir, ignore_errors=True)
                                else:
                                    os.remove(_spec.origin)
                                _sp = os.path.dirname(_pkg_dir)
                                for _e in os.listdir(_sp):
                                    if _e.lower().startswith(imp_name_s.lower()) and (
                                            _e.endswith(".dist-info") or _e.endswith(".egg-info")):
                                        _sh.rmtree(os.path.join(_sp, _e), ignore_errors=True)
                            for _mod in list(sys.modules.keys()):
                                if _mod == imp_name_s or _mod.startswith(imp_name_s + "."):
                                    sys.modules.pop(_mod, None)
                            importlib.invalidate_caches()
                        if _can_import(imp_name_s):
                            raise RuntimeError(
                                f"{imp_name_s!r} still importable — may need manual removal "
                                f"from site-packages.")
                        inst_ref[0] = False
                        root.after(0, lambda: rd_ref[0]["status_lbl"].config(
                            text="✘  removed", fg=SUBTEXT))
                        root.after(0, lambda: log_var.set(f"✔  {disp_s} removed."))
                        root.after(0, lambda: _set_progress(1.0))
                        root.after(200, lambda: _set_progress(None))
                    except Exception as exc:
                        _exc_str = str(exc)
                        root.after(0, lambda s=_exc_str: _set_failed(rd_ref[0]["status_lbl"], pip_name_s, s))
                        root.after(0, lambda s=_exc_str: log_var.set(f"✘  {disp_s}: {s}"))
                        root.after(0, lambda: _set_progress(None))
                else:
                    # ── INSTALL ────────────────────────────────────────────────
                    _msg = f"Installing {disp_s}…" if pip_name_s != "pywin32" else "Installing pywin32 + wmi…"
                    root.after(0, lambda: log_var.set(_msg))
                    root.after(0, lambda: rd_ref[0]["status_lbl"].config(text="⏳  Installing…", fg=YELLOW))
                    root.after(0, lambda: _set_progress(0.1))
                    try:
                        # GPUtil requires distutils which was removed in Python 3.12+;
                        # install setuptools first to provide it.
                        if pip_name_s == "GPUtil":
                            root.after(0, lambda: log_var.set("Installing setuptools for GPUtil…"))
                            subprocess.run(
                                [sys.executable, "-m", "pip", "install", "setuptools",
                                 "--disable-pip-version-check", "--no-cache-dir"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, creationflags=_NO_WIN)
                            root.after(0, lambda: log_var.set("Installing GPUtil…"))
                        proc = subprocess.run(
                            [sys.executable, "-m", "pip", "install", pip_name_s,
                             "--disable-pip-version-check", "--no-cache-dir"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, creationflags=_NO_WIN)
                        if proc.returncode != 0:
                            _err = (proc.stderr or "").strip().splitlines()
                            raise RuntimeError(_err[-1] if _err else f"exit {proc.returncode}")
                        if pip_name_s == "pywin32":
                            # Skip post-install — it triggers a Windows DLL dialog if
                            # pythoncom is locked by another process. pywin32 works fully
                            # after a restart without needing the post-install in modern pip.
                            root.after(0, lambda: _set_progress(0.75))
                            # Still install wmi so it's ready after restart
                            root.after(0, lambda: log_var.set("Installing wmi…"))
                            subprocess.run(
                                [sys.executable, "-m", "pip", "install", "wmi",
                                 "--disable-pip-version-check", "--no-cache-dir"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, creationflags=_NO_WIN)
                            # wmi may fail in current process — non-fatal, works after restart
                        importlib.invalidate_caches()
                        for _mod in list(sys.modules.keys()):
                            if _mod == imp_name_s or _mod.startswith(imp_name_s + "."):
                                sys.modules.pop(_mod, None)
                        if pip_name_s in ("pywin32", "wmi"):
                            try:
                                os.add_dll_directory(os.path.dirname(sys.executable))
                            except (AttributeError, OSError):
                                pass
                            try:
                                import site as _site
                                for _sp in _site.getsitepackages():
                                    for _sub in ("", "win32",
                                                 os.path.join("win32", "lib"),
                                                 "win32com", "win32comext"):
                                        _d = os.path.join(_sp, _sub)
                                        if os.path.isdir(_d) and _d not in sys.path:
                                            sys.path.insert(0, _d)
                                    try:
                                        os.add_dll_directory(_sp)
                                    except (AttributeError, OSError):
                                        pass
                            except Exception:
                                pass
                            importlib.invalidate_caches()
                        # GPUtil uses distutils.version which was removed in Python 3.12+.
                        # setuptools re-provides it but the current process won't see it
                        # until we purge the stale cache and inject it into sys.modules.
                        if pip_name_s == "GPUtil":
                            for _mod in list(sys.modules.keys()):
                                if _mod == "distutils" or _mod.startswith("distutils."):
                                    sys.modules.pop(_mod, None)
                            importlib.invalidate_caches()
                            try:
                                import setuptools  # noqa — triggers distutils shim
                                import distutils.version  # should now resolve via setuptools
                            except Exception:
                                pass
                        # pywin32 DLLs can't be loaded in the current process after
                        # install — verify via metadata only, skip live import check.
                        if pip_name_s == "pywin32":
                            try:
                                ver_str = f"✔  v{_importlib_metadata.version(pip_name_s)}"
                            except Exception:
                                ver_str = "✔  installed"
                            inst_ref[0] = True
                            root.after(0, lambda vs=ver_str: rd_ref[0]["status_lbl"].config(
                                text=vs, fg=GREEN))
                            root.after(0, lambda: log_var.set("✔  pywin32 installed. Restart PyDisplay to activate."))
                            root.after(0, lambda: _set_progress(1.0))
                            root.after(200, lambda: _set_progress(None))
                        else:
                            try:
                                importlib.import_module(imp_name_s)
                            except Exception as _ie:
                                raise RuntimeError(f"installed but import failed: {_ie}")
                            try:
                                ver_str = f"✔  v{_importlib_metadata.version(pip_name_s)}"
                            except Exception:
                                ver_str = "✔  installed"
                            inst_ref[0] = True
                            root.after(0, lambda vs=ver_str: rd_ref[0]["status_lbl"].config(
                                text=vs, fg=GREEN))
                            root.after(0, lambda: log_var.set(f"✔  {disp_s} installed."))
                            root.after(0, lambda: _set_progress(1.0))
                            root.after(200, lambda: _set_progress(None))
                    except Exception as exc:
                        _exc_str = str(exc)
                        root.after(0, lambda s=_exc_str: _set_failed(rd_ref[0]["status_lbl"], pip_name_s, s))
                        root.after(0, lambda s=_exc_str: log_var.set(f"✘  {disp_s}: {s}"))
                        root.after(0, lambda: _set_progress(None))

                # Always unlock button and refresh its appearance when done
                busy_ref[0] = False
                root.after(0, rd_ref[0]["refresh_btn"])

            threading.Thread(target=_worker_single, daemon=True).start()

        action_btn.bind("<Button-1>", _click_action)
        action_btn.bind("<Enter>", lambda e, btn=action_btn, ir=_inst_ref, br=_busy_ref:
            btn.config(fg=RED if ir[0] else GREEN) if not br[0] else None)
        action_btn.bind("<Leave>", lambda e, r=_refresh_action_btn: r())
        _refresh_action_btn()   # seed initial appearance

        _rd = {
            "disp": disp, "pip_name": pip_name, "imp_name": imp_name,
            "required": required,
            "action_var": action_var,
            "status_lbl": status_lbl,
            "action_btn": action_btn,
            "refresh_btn": _refresh_action_btn,
            "inst_ref": _inst_ref,
            "busy_ref": _busy_ref,
        }
        _rd_ref[0] = _rd
        row_data.append(_rd)

    # ── Legend + Check for Updates ───────────────────────────────────────────
    legend_row = tk.Frame(root, bg=BG)
    legend_row.pack(fill="x", padx=14, pady=(4, 0))
    tk.Label(legend_row, text="  * required package",
             bg=BG, fg=TEXT, font=(_FONT, _BASE_FONT_SIZE + 1),
             anchor="w").pack(side="left")

    _upd_btn = tk.Label(legend_row, text="↻  Check Dep Updates",
                        bg=BORDER, fg=ACCENT,
                        font=(_FONT, _BASE_FONT_SIZE, "bold"), cursor="hand2",
                        width=20, padx=12, pady=6)
    _upd_btn.pack(side="right", padx=(0, 2))

    _upd_status_var = tk.StringVar(value="")
    tk.Label(legend_row, textvariable=_upd_status_var, bg=BG, fg=ACCENT,
             font=(_FONT, _BASE_FONT_SIZE), anchor="e").pack(side="right", fill="x", expand=True)

    def _reset_app_upd_btn():
        _app_upd_btn.config(text="⌕ Update", fg=ACCENT, cursor="hand2")
        _app_upd_btn.bind("<Button-1>", lambda e: _check_app_update())
        _app_upd_btn.bind("<Enter>",    lambda e: _app_upd_btn.config(fg=GREEN))
        _app_upd_btn.bind("<Leave>",    lambda e: _app_upd_btn.config(fg=ACCENT))

    def _check_app_update():
        """Query GitHub releases API to check if a newer version of PyDisplay is available."""
        _app_upd_btn.config(text="⌕ Checking…", fg=SUBTEXT, cursor="arrow")
        _app_upd_btn.unbind("<Button-1>")
        _app_upd_btn.unbind("<Enter>")
        _app_upd_btn.unbind("<Leave>")

        def _worker():
            try:
                url = f"https://api.github.com/repos/{_GITHUB_REPO}/releases/latest"
                req = urllib.request.Request(url, headers={"User-Agent": "PyDisplay"})
                try:
                    with urllib.request.urlopen(req, timeout=8) as _r:
                        data = json.loads(_r.read())
                except urllib.error.HTTPError as _he:
                    if _he.code == 404:
                        def _no_release():
                            _reset_app_upd_btn()
                            _upd_status_var.set("ℹ  No releases published yet on GitHub.")
                        root.after(0, _no_release)
                        return
                    raise
                latest_tag = data.get("tag_name", "").lstrip("v")
                html_url   = data.get("html_url", f"https://github.com/{_GITHUB_REPO}/releases")

                def _show():
                    _reset_app_upd_btn()
                    if not latest_tag:
                        _upd_status_var.set("✘  Could not read latest release tag.")
                        return
                    if latest_tag == _APP_VERSION:
                        _upd_status_var.set(f"✔  PyDisplay is up to date (v{_APP_VERSION}).")
                    else:
                        _upd_status_var.set(f"↑  Update available: v{latest_tag}  (you have v{_APP_VERSION})")
                        _show_app_update_dialog(latest_tag, html_url)
                root.after(0, _show)
            except Exception as exc:
                def _err():
                    _reset_app_upd_btn()
                    _upd_status_var.set(f"✘  App update check failed: {exc}")
                root.after(0, _err)

        threading.Thread(target=_worker, daemon=True).start()

    def _show_app_update_dialog(latest_tag, html_url):
        """Confirm update, then download, replace, and restart automatically."""
        dlg = tk.Toplevel(root)
        dlg.title("PyDisplay — Update Available")
        dlg.configure(bg=BG)
        dlg.resizable(False, False)
        dlg.wm_attributes("-topmost", True)
        dlg.overrideredirect(True)
        dlg.withdraw()

        _make_titlebar(dlg, PANEL, BORDER, SUBTEXT, RED, on_close=dlg.destroy)

        tk.Label(dlg, text="↑  PyDisplay Update Available",
                 bg=BG, fg="#ff6b35", font=(_FONT, _BASE_FONT_SIZE, "bold"),
                 padx=16, pady=12).pack(fill="x")
        tk.Frame(dlg, bg=BORDER, height=1).pack(fill="x", padx=10)

        _status_var = tk.StringVar(value=(
            f"A new version of PyDisplay is available.\n\n"
            f"  Current:  v{_APP_VERSION}\n"
            f"  Latest:    v{latest_tag}\n\n"
            f"Click Update to download and restart automatically."
        ))
        _status_lbl = tk.Label(dlg, textvariable=_status_var,
                               bg=BG, fg=TEXT, font=(_FONT, _BASE_FONT_SIZE),
                               justify="left", padx=20, pady=12)
        _status_lbl.pack(fill="x")
        tk.Frame(dlg, bg=BORDER, height=1).pack(fill="x", padx=10)

        act = tk.Frame(dlg, bg=BG)
        act.pack(fill="x", padx=16, pady=10)

        def _do_update():
            """Download the .pyw from the release assets, replace this file, restart."""
            update_btn.config(text="Downloading…", fg=SUBTEXT, cursor="arrow")
            update_btn.unbind("<Button-1>")
            update_btn.unbind("<Enter>")
            update_btn.unbind("<Leave>")
            skip_btn.config(cursor="arrow")
            skip_btn.unbind("<Button-1>")

            def _worker():
                try:
                    # Fetch release assets to find the .pyw file
                    api_url = f"https://api.github.com/repos/{_GITHUB_REPO}/releases/latest"
                    req = urllib.request.Request(api_url, headers={"User-Agent": "PyDisplay"})
                    with urllib.request.urlopen(req, timeout=10) as _r:
                        release_data = json.loads(_r.read())

                    asset_url = None
                    for asset in release_data.get("assets", []):
                        if asset["name"].endswith(".pyw"):
                            asset_url = asset["browser_download_url"]
                            break

                    if not asset_url:
                        # Fall back to raw file from the repo tag
                        asset_url = (
                            f"https://raw.githubusercontent.com/{_GITHUB_REPO}"
                            f"/v{latest_tag}/PyDisplay.pyw"
                        )

                    def _show_downloading():
                        _status_var.set(
                            f"Downloading v{latest_tag}…\nPlease wait."
                        )
                    root.after(0, _show_downloading)

                    req2 = urllib.request.Request(asset_url, headers={"User-Agent": "PyDisplay"})
                    with urllib.request.urlopen(req2, timeout=30) as _r2:
                        new_code = _r2.read()

                    # Write to a temp file first, then replace
                    this_file  = os.path.abspath(sys.argv[0])
                    backup     = this_file + ".bak"
                    tmp        = this_file + ".tmp"

                    with open(tmp, "wb") as _f:
                        _f.write(new_code)

                    # Backup current file then replace
                    if os.path.exists(backup):
                        os.remove(backup)
                    os.rename(this_file, backup)
                    os.rename(tmp, this_file)

                    def _restart():
                        _status_var.set(
                            f"✔  Updated to v{latest_tag}!\nRestarting PyDisplay…"
                        )
                        dlg.update_idletasks()
                        dlg.after(1200, lambda: (
                            subprocess.Popen([sys.executable, this_file]),
                            root.destroy()
                        ))
                    root.after(0, _restart)

                except Exception as exc:
                    def _fail():
                        _status_var.set(f"✘  Update failed:\n{exc}\n\nYou can update manually from GitHub.")
                        update_btn.config(text="↑  Update", fg="#ff6b35", cursor="hand2")
                        update_btn.bind("<Button-1>", lambda e: _do_update())
                        update_btn.bind("<Enter>",    lambda e: update_btn.config(fg=GREEN))
                        update_btn.bind("<Leave>",    lambda e: update_btn.config(fg="#ff6b35"))
                    root.after(0, _fail)

            threading.Thread(target=_worker, daemon=True).start()

        update_btn = tk.Label(act, text="↑  Update & Restart",
                              bg=BORDER, fg="#ff6b35",
                              font=(_FONT, _BASE_FONT_SIZE, "bold"), cursor="hand2",
                              padx=12, pady=6)
        update_btn.pack(side="right", padx=(8, 0))
        update_btn.bind("<Button-1>", lambda e: _do_update())
        update_btn.bind("<Enter>",    lambda e: update_btn.config(fg=GREEN))
        update_btn.bind("<Leave>",    lambda e: update_btn.config(fg="#ff6b35"))

        skip_btn = tk.Label(act, text="Skip",
                            bg=BORDER, fg=SUBTEXT,
                            font=(_FONT, _BASE_FONT_SIZE, "bold"), cursor="hand2",
                            padx=12, pady=6)
        skip_btn.pack(side="right")
        skip_btn.bind("<Button-1>", lambda e: dlg.destroy())
        skip_btn.bind("<Enter>",    lambda e: skip_btn.config(fg=TEXT))
        skip_btn.bind("<Leave>",    lambda e: skip_btn.config(fg=SUBTEXT))

        dlg.update_idletasks()
        _rx = root.winfo_rootx() + root.winfo_width()  // 2 - dlg.winfo_reqwidth()  // 2
        _ry = root.winfo_rooty() + root.winfo_height() // 2 - dlg.winfo_reqheight() // 2
        dlg.geometry(f"+{_rx}+{_ry}")
        dlg.deiconify()
        dlg.grab_set()

    _reset_app_upd_btn()  # re-bind now that widget exists

    def _check_updates():
        _installed_pkgs = [(rd["disp"], rd["pip_name"]) for rd in row_data if rd["inst_ref"][0]]
        if not _installed_pkgs:
            _upd_status_var.set("No installed packages to check.")
            return

        _upd_btn.config(text="↻  Checking Deps…", fg=SUBTEXT, cursor="arrow")
        _upd_btn.unbind("<Button-1>")
        _upd_btn.unbind("<Enter>")
        _upd_btn.unbind("<Leave>")

        def _worker_check():
            updates = []  # list of (disp, pip_name, current_ver, latest_ver)
            for disp, pip_name in _installed_pkgs:
                try:
                    current = _importlib_metadata.version(pip_name)
                except Exception:
                    continue
                try:
                    url = f"https://pypi.org/pypi/{pip_name}/json"
                    with urllib.request.urlopen(url, timeout=6) as _r:
                        data = json.loads(_r.read())
                    latest = data["info"]["version"]
                    if latest != current:
                        updates.append((disp, pip_name, current, latest))
                except Exception:
                    pass

            def _show_result():
                _reset_upd_btn()
                if not updates:
                    _upd_status_var.set("✔  All packages are up to date.")
                    return
                _show_update_dialog(updates)

            root.after(0, _show_result)

        threading.Thread(target=_worker_check, daemon=True).start()

    def _reset_upd_btn():
        _upd_btn.config(text="↻  Check Dep Updates", fg=ACCENT, cursor="hand2")
        _upd_btn.bind("<Button-1>", lambda e: _check_updates())
        _upd_btn.bind("<Enter>",    lambda e: _upd_btn.config(fg=GREEN))
        _upd_btn.bind("<Leave>",    lambda e: _upd_btn.config(fg=ACCENT))

    def _show_update_dialog(updates):
        """Show a dialog listing available updates with upgrade / skip-1d / skip-7d options."""
        # Load existing snooze data
        _snooze_path = os.path.join(_APP_DIR, "PyDisplay_snooze.json")
        try:
            with open(_snooze_path) as _sf:
                _snooze = json.load(_sf)
        except Exception:
            _snooze = {}

        def _save_snooze():
            try:
                os.makedirs(os.path.dirname(_snooze_path), exist_ok=True)
                with open(_snooze_path, "w") as _sf:
                    json.dump(_snooze, _sf)
            except Exception:
                pass

        now_ts = time.time()

        # Filter out snoozed packages
        pending = [(d, p, c, l) for d, p, c, l in updates
                   if now_ts > _snooze.get(p, 0)]

        if not pending:
            log_var.set("✔  All updates snoozed — nothing to show.")
            return

        dlg = tk.Toplevel(root)
        dlg.title("PyDisplay — Updates Available")
        dlg.configure(bg=BG)
        dlg.resizable(False, False)
        dlg.wm_attributes("-topmost", True)
        dlg.withdraw()

        tk.Label(dlg, text="↻  Updates Available",
                 bg=BG, fg=ACCENT, font=(_FONT, _BASE_FONT_SIZE, "bold"),
                 padx=16, pady=12).pack(fill="x")

        tk.Frame(dlg, bg=BORDER, height=1).pack(fill="x", padx=10)

        pkg_frame = tk.Frame(dlg, bg=BG)
        pkg_frame.pack(fill="x", padx=16, pady=(8, 4))

        # Header row
        for col, hdr in enumerate(["Package", "Current", "Latest", "Action"]):
            tk.Label(pkg_frame, text=hdr, bg=BG, fg=SUBTEXT,
                     font=(_FONT, _BASE_FONT_SIZE, "bold"), anchor="w",
                     padx=4).grid(row=0, column=col, sticky="ew", pady=(0, 2))
        pkg_frame.columnconfigure(3, weight=1)

        _upgrade_vars = []  # (pip_name, disp, latest, selected_var)

        for i, (disp, pip_name, current, latest) in enumerate(pending, start=1):
            row_bg = PANEL if i % 2 == 0 else BG
            tk.Label(pkg_frame, text=disp, bg=row_bg, fg=TEXT,
                     font=(_FONT, _BASE_FONT_SIZE, "bold"), anchor="w",
                     padx=4, pady=6).grid(row=i, column=0, sticky="ew")
            tk.Label(pkg_frame, text=current, bg=row_bg, fg=SUBTEXT,
                     font=(_FONT, _BASE_FONT_SIZE), anchor="w",
                     padx=4).grid(row=i, column=1, sticky="ew")
            tk.Label(pkg_frame, text=latest, bg=row_bg, fg=GREEN,
                     font=(_FONT, _BASE_FONT_SIZE, "bold"), anchor="w",
                     padx=4).grid(row=i, column=2, sticky="ew")

            btn_cell = tk.Frame(pkg_frame, bg=row_bg)
            btn_cell.grid(row=i, column=3, sticky="ew", padx=(6, 0))

            _sel = tk.BooleanVar(value=True)
            _upgrade_vars.append((pip_name, disp, latest, _sel))

            chk = tk.Label(btn_cell, text="☑  Upgrade", bg=row_bg, fg=GREEN,
                           font=(_FONT, _BASE_FONT_SIZE, "bold"), cursor="hand2", padx=6)
            chk.pack(side="left")

            def _toggle(e, v=_sel, lbl=chk):
                v.set(not v.get())
                lbl.config(text=("☑  Upgrade" if v.get() else "☐  Skip"),
                           fg=(GREEN if v.get() else SUBTEXT))
            chk.bind("<Button-1>", _toggle)

            def _snooze_1d(e, p=pip_name, d=disp):
                _snooze[p] = now_ts + 86400
                _save_snooze()
                log_var.set(f"Snoozed {d} for 1 day.")
                dlg.destroy()
            def _snooze_7d(e, p=pip_name, d=disp):
                _snooze[p] = now_ts + 604800
                _save_snooze()
                log_var.set(f"Snoozed {d} for 7 days.")
                dlg.destroy()

            s1 = tk.Label(btn_cell, text="1d", bg=row_bg, fg=SUBTEXT,
                          font=(_FONT, _BASE_FONT_SIZE - 1), cursor="hand2", padx=4)
            s1.pack(side="left")
            s1.bind("<Button-1>", _snooze_1d)
            s1.bind("<Enter>", lambda e, w=s1: w.config(fg=YELLOW))
            s1.bind("<Leave>", lambda e, w=s1: w.config(fg=SUBTEXT))

            s7 = tk.Label(btn_cell, text="7d", bg=row_bg, fg=SUBTEXT,
                          font=(_FONT, _BASE_FONT_SIZE - 1), cursor="hand2", padx=4)
            s7.pack(side="left")
            s7.bind("<Button-1>", _snooze_7d)
            s7.bind("<Enter>", lambda e, w=s7: w.config(fg=YELLOW))
            s7.bind("<Leave>", lambda e, w=s7: w.config(fg=SUBTEXT))

        tk.Frame(dlg, bg=BORDER, height=1).pack(fill="x", padx=10, pady=(6, 0))

        act_row = tk.Frame(dlg, bg=BG)
        act_row.pack(fill="x", padx=16, pady=10)

        tk.Label(act_row, text="1d / 7d = skip for that many days",
                 bg=BG, fg=SUBTEXT, font=(_FONT, _BASE_FONT_SIZE - 1)).pack(side="left")

        def _do_upgrade():
            to_up = [(p, d, l) for p, d, l, v in _upgrade_vars if v.get()]
            dlg.destroy()
            if not to_up:
                log_var.set("No packages selected for upgrade.")
                return

            def _up_worker():
                for pip_name, disp, latest in to_up:
                    root.after(0, lambda d=disp: log_var.set(f"Upgrading {d}…"))
                    try:
                        proc = subprocess.run(
                            [sys.executable, "-m", "pip", "install",
                             f"{pip_name}=={latest}",
                             "--disable-pip-version-check", "--no-cache-dir"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, creationflags=_NO_WIN)
                        if proc.returncode == 0:
                            root.after(0, lambda d=disp: log_var.set(f"✔  {d} upgraded."))
                        else:
                            err = (proc.stderr or "").strip().splitlines()
                            root.after(0, lambda d=disp, e=err[-1] if err else "failed":
                                       log_var.set(f"✘  {d}: {e}"))
                    except Exception as exc:
                        root.after(0, lambda d=disp, e=str(exc): log_var.set(f"✘  {d}: {e}"))
                root.after(0, _refresh_all)

            threading.Thread(target=_up_worker, daemon=True).start()

        up_btn = tk.Label(act_row, text="Upgrade Selected",
                          bg=BORDER, fg=ACCENT,
                          font=(_FONT, _BASE_FONT_SIZE, "bold"), cursor="hand2",
                          padx=12, pady=6)
        up_btn.pack(side="right", padx=(8, 0))
        up_btn.bind("<Button-1>", lambda e: _do_upgrade())
        up_btn.bind("<Enter>",    lambda e: up_btn.config(fg=GREEN))
        up_btn.bind("<Leave>",    lambda e: up_btn.config(fg=ACCENT))

        cl_btn = tk.Label(act_row, text="Close",
                          bg=BORDER, fg=SUBTEXT,
                          font=(_FONT, _BASE_FONT_SIZE, "bold"), cursor="hand2",
                          padx=12, pady=6)
        cl_btn.pack(side="right")
        cl_btn.bind("<Button-1>", lambda e: dlg.destroy())
        cl_btn.bind("<Enter>",    lambda e: cl_btn.config(fg=TEXT))
        cl_btn.bind("<Leave>",    lambda e: cl_btn.config(fg=SUBTEXT))

        dlg.update_idletasks()
        _rx = root.winfo_rootx() + root.winfo_width()  // 2 - dlg.winfo_reqwidth()  // 2
        _ry = root.winfo_rooty() + root.winfo_height() // 2 - dlg.winfo_reqheight() // 2
        dlg.geometry(f"+{_rx}+{_ry}")
        dlg.deiconify()
        dlg.grab_set()

    _reset_upd_btn()  # bind initial click handler

    tk.Frame(root, bg=BORDER, height=1).pack(fill="x", padx=8, pady=(6, 0))

    # ── Progress / log line — aliased to legend row status var ───────────────
    log_var = _upd_status_var  # all status messages go beside "Check Dep Updates"

    log_row = tk.Frame(root, bg=BG)
    log_row.pack(fill="x", padx=16, pady=(4, 0))

    _install_all_btn = tk.Label(log_row, text="\u2b07  Install All",
                                bg=BORDER, fg=ACCENT,
                                font=(_FONT, _BASE_FONT_SIZE, "bold"), cursor="hand2",
                                width=20, padx=12, pady=6)
    _install_all_btn.pack(side="right")

    _dup_btn = tk.Label(log_row, text="Check for Duplicates",
                        bg=BORDER, fg=TEXT,
                        font=(_FONT, _BASE_FONT_SIZE, "bold"), cursor="hand2",
                        width=20, padx=12, pady=6)
    _dup_btn.pack(side="right", padx=(0, 6))
    _dup_btn.bind("<Button-1>", lambda e: _check_duplicates())
    _dup_btn.bind("<Enter>",    lambda e: _dup_btn.config(fg=YELLOW))
    _dup_btn.bind("<Leave>",    lambda e: _dup_btn.config(fg=TEXT))

    _uninstall_all_btn = tk.Label(log_row, text="✕  Uninstall All",
                                  bg=BORDER, fg=RED,
                                  font=(_FONT, _BASE_FONT_SIZE, "bold"), cursor="hand2",
                                  width=16, padx=12, pady=6)
    _uninstall_all_btn.pack(side="right", padx=(0, 6))
    _uninstall_all_btn.bind("<Enter>", lambda e: _uninstall_all_btn.config(bg=RED, fg=BG))
    _uninstall_all_btn.bind("<Leave>", lambda e: _uninstall_all_btn.config(bg=BORDER, fg=RED))

    def _uninstall_all():
        """Uninstall all currently installed dependencies, then verify they are gone."""
        installed = [rd for rd in row_data if rd["inst_ref"][0]]
        if not installed:
            _upd_status_var.set("No installed packages to remove.")
            return

        # Confirm before proceeding
        confirm = tk.Toplevel(root)
        confirm.configure(bg=PANEL)
        confirm.resizable(False, False)
        confirm.title("")
        confirm.overrideredirect(True)
        confirm.attributes("-topmost", True)
        confirm.lift()
        confirm.focus_force()
        confirm.grab_set()
        _make_titlebar(confirm, PANEL, BORDER, SUBTEXT, RED,
                       on_close=confirm.destroy,
                       title_text="PyDisplay  ·  Uninstall All", title_fg=TEXT, title_bg=PANEL)
        cf = tk.Frame(confirm, bg=PANEL, padx=16, pady=12)
        cf.pack(fill="both")
        tk.Label(cf, text="Uninstall All Dependencies?", bg=PANEL, fg=RED,
                 font=(_FONT, _BASE_FONT_SIZE, "bold")).pack(anchor="w")
        tk.Label(cf, text="This will remove all installed packages.\nPyDisplay will not function until they are reinstalled.",
                 bg=PANEL, fg=SUBTEXT, font=(_FONT, _BASE_FONT_SIZE - 1), justify="left").pack(anchor="w", pady=(4, 10))
        btn_row = tk.Frame(cf, bg=PANEL)
        btn_row.pack(fill="x")

        def _do_uninstall():
            confirm.destroy()
            _uninstall_all_btn.config(text="✕  Removing…", fg=SUBTEXT, cursor="arrow")
            _uninstall_all_btn.unbind("<Button-1>")
            _uninstall_all_btn.unbind("<Enter>")
            _uninstall_all_btn.unbind("<Leave>")
            _upd_status_var.set("Uninstalling all dependencies…")

            def _worker():
                failed = []
                for rd in installed:
                    pip_n = rd["pip_name"]
                    # Handle packages with multiple pip names
                    if pip_n == "pynvml":
                        candidates = ["pynvml", "nvidia-ml-py"]
                    elif pip_n == "GPUtil":
                        candidates = ["GPUtil", "gputil"]
                    else:
                        candidates = [pip_n]
                    root.after(0, lambda n=pip_n: _upd_status_var.set(f"Removing {n}…"))
                    root.after(0, lambda rd=rd: rd["status_lbl"].config(text="⏳  Removing…", fg=YELLOW))
                    for cand in candidates:
                        try:
                            subprocess.run(
                                [sys.executable, "-m", "pip", "uninstall", "-y", cand,
                                 "--disable-pip-version-check"],
                                capture_output=True, text=True)
                        except Exception as exc:
                            failed.append(f"{cand}: {exc}")

                # ── Verify all are gone ──────────────────────────────────────
                root.after(0, lambda: _upd_status_var.set("Verifying removal…"))
                still_present = []
                for rd in installed:
                    try:
                        _importlib_metadata.version(rd["pip_name"])
                        still_present.append(rd["pip_name"])
                    except Exception:
                        pass  # gone — good

                def _finish():
                    # Refresh all row status labels
                    for rd in row_data:
                        ver = _get_version(rd["pip_name"])
                        installed_now = ver != ""
                        rd["inst_ref"][0] = installed_now
                        st_text  = f"✔  v{ver}" if installed_now else "✘  missing"
                        st_color = GREEN if installed_now else RED
                        rd["status_lbl"].config(text=st_text, fg=st_color)
                        rd["refresh_btn"]()
                    _update_install_all_btn()

                    if still_present:
                        _upd_status_var.set(f"⚠  Could not remove: {', '.join(still_present)}")
                    elif failed:
                        _upd_status_var.set(f"⚠  Errors during removal: {'; '.join(failed)}")
                    else:
                        _upd_status_var.set(f"✔  All {len(installed)} package(s) removed successfully.")

                    # Re-enable button
                    _uninstall_all_btn.config(text="✕  Uninstall All", fg=RED, cursor="hand2")
                    _uninstall_all_btn.bind("<Button-1>", lambda e: _uninstall_all())
                    _uninstall_all_btn.bind("<Enter>", lambda e: _uninstall_all_btn.config(bg=RED, fg=BG))
                    _uninstall_all_btn.bind("<Leave>", lambda e: _uninstall_all_btn.config(bg=BORDER, fg=RED))

                root.after(0, _finish)

            threading.Thread(target=_worker, daemon=True).start()

        cancel_btn = tk.Label(btn_row, text="Cancel", bg=BORDER, fg=SUBTEXT,
                 font=(_FONT, _BASE_FONT_SIZE, "bold"), cursor="hand2",
                 padx=12, pady=4)
        cancel_btn.pack(side="left", padx=(0, 6))
        cancel_btn.bind("<Button-1>", lambda e: confirm.destroy())
        cancel_btn.bind("<Enter>", lambda e: cancel_btn.config(fg=TEXT))
        cancel_btn.bind("<Leave>", lambda e: cancel_btn.config(fg=SUBTEXT))
        ok_btn = tk.Label(btn_row, text="✕  Yes, Uninstall All", bg=RED, fg=BG,
                          font=(_FONT, _BASE_FONT_SIZE, "bold"), cursor="hand2",
                          padx=12, pady=4)
        ok_btn.pack(side="left")
        ok_btn.bind("<Button-1>", lambda e: _do_uninstall())

        confirm.update_idletasks()
        cx = root.winfo_x() + (root.winfo_width() - confirm.winfo_reqwidth()) // 2
        cy = root.winfo_y() + (root.winfo_height() - confirm.winfo_reqheight()) // 2
        confirm.geometry(f"+{cx}+{cy}")

    _uninstall_all_btn.bind("<Button-1>", lambda e: _uninstall_all())

    def _update_install_all_btn():
        """Update button text/state in real time based on how many packages are missing."""
        missing_count = sum(1 for rd in row_data if not rd["inst_ref"][0])
        if missing_count == 0:
            _install_all_btn.config(text="\u2714  All Installs Done",
                                    fg=GREEN, cursor="arrow", bg=BORDER)
            _install_all_btn.unbind("<Button-1>")
            _install_all_btn.unbind("<Enter>")
            _install_all_btn.unbind("<Leave>")
        else:
            dep_word = "dependency" if missing_count == 1 else "dependencies"
            _install_all_btn.config(
                text=f"\u2b07  Install {missing_count} {dep_word}",
                fg=ACCENT, cursor="hand2", bg=BORDER)
            _install_all_btn.bind("<Button-1>", lambda e: _install_all())
            _install_all_btn.bind("<Enter>",    lambda e: _install_all_btn.config(fg=GREEN))
            _install_all_btn.bind("<Leave>",    lambda e: _install_all_btn.config(fg=ACCENT))

    def _install_all():
        """Click the Install button on every row that isn't already installed."""
        _to_get = [rd for rd in row_data if not rd["inst_ref"][0]]
        if not _to_get:
            _update_install_all_btn()
            return
        for rd in _to_get:
            if not rd["busy_ref"][0]:
                rd["action_btn"].event_generate("<Button-1>")

    def _poll_install_state():
        """Poll every 300 ms and keep the button label in sync with install state.
        Stops automatically when the dep-check window is destroyed."""
        if not root.winfo_exists():
            return
        _update_install_all_btn()
        root.after(300, _poll_install_state)

    _poll_install_state()  # start real-time polling

    # Simple canvas progress bar (hidden until an operation runs)
    prog_canvas = tk.Canvas(root, bg=BG, highlightthickness=0, height=3)
    prog_canvas.pack(fill="x", padx=16, pady=(2, 0))

    def _set_progress(frac):
        """Draw/update a thin progress bar. frac 0.0–1.0; None = hide."""
        prog_canvas.delete("all")
        if frac is None:
            return
        w = prog_canvas.winfo_width() or 420
        prog_canvas.create_rectangle(0, 0, int(w * frac), 3, fill=ACCENT, outline="")

    # ── Bottom row ────────────────────────────────────────────────────────────
    bot = tk.Frame(root, bg=BG, pady=10)
    bot.pack(fill="x", padx=16)

    # Don't show again checkbox on left
    _dsa_var = tk.BooleanVar(value=False)
    _chk_lbl = tk.Label(bot, text="☐  Don't show again",
                        bg=BG, fg=SUBTEXT, font=(_FONT, _BASE_FONT_SIZE - 1), cursor="hand2")
    _chk_lbl.pack(side="left")
    def _toggle_dsa(e=None):
        v = not _dsa_var.get()
        _dsa_var.set(v)
        _chk_lbl.config(text=("☑" if v else "☐") + "  Don't show again",
                        fg=(GREEN if v else SUBTEXT))
    _chk_lbl.bind("<Button-1>", _toggle_dsa)

    # Reopen Choose Position checkbox — same style as Don't show again
    # Pre-load persisted state so the checkbox reflects saved preference
    _init_reopen = _reopen_picker_flagged()
    result["reopen_placement"] = _init_reopen
    _place_lbl = tk.Label(bot, text=("☑" if _init_reopen else "☐") + "  Reopen Choose Position",
                          bg=BG, fg=(GREEN if _init_reopen else SUBTEXT),
                          font=(_FONT, _BASE_FONT_SIZE - 1), cursor="hand2")
    _place_lbl.pack(side="left", padx=(12, 0))
    def _toggle_placement(e=None):
        v = not result.get("reopen_placement", False)
        result["reopen_placement"] = v
        _save_reopen_picker(v)
        _place_lbl.config(text=("☑" if v else "☐") + "  Reopen Choose Position",
                          fg=(GREEN if v else SUBTEXT))
    _place_lbl.bind("<Button-1>", _toggle_placement)
    _place_lbl.bind("<Enter>", lambda e: _place_lbl.config(fg=ACCENT))
    _place_lbl.bind("<Leave>", lambda e: _place_lbl.config(
        fg=(GREEN if result.get("reopen_placement") else SUBTEXT)))

    # ── Refresh all rows to reflect current install state ─────────────────────
    def _refresh_all():
        for rd in row_data:
            now_installed = _can_import(rd["imp_name"])
            version       = _get_version(rd["pip_name"]) if now_installed else ""
            rd["inst_ref"][0] = now_installed
            st_text  = (f"✔  v{version}" if version else "✔  installed") if now_installed else "✘  missing"
            st_color = GREEN if now_installed else RED
            rd["status_lbl"].config(text=st_text, fg=st_color)
            # Reset queued action to a sensible default
            av = rd["action_var"]
            av.set("keep" if (now_installed or not rd["required"]) else "install")
            rd["refresh_btn"]()
        _update_install_all_btn()

    # Buttons on right
    btn_area = tk.Frame(bot, bg=BG)
    btn_area.pack(side="right")

    def _make_btn(parent, text, color, cmd):
        b = tk.Label(parent, text=text, bg=BORDER, fg=color,
                     font=(_FONT, _BASE_FONT_SIZE, "bold"), cursor="hand2", padx=12, pady=6)
        b.pack(side="left", padx=(8, 0))
        b.bind("<Button-1>", lambda e: cmd())
        b.bind("<Enter>",    lambda e, w=b: w.config(fg=GREEN))
        b.bind("<Leave>",    lambda e, w=b, c=color: w.config(fg=c))
        return b

    def _show_missing_warning(missing, on_continue):
        """Show a modal warning when required packages are missing. Calls on_continue if user proceeds."""
        _warn_win = tk.Toplevel(root)
        _warn_win.title("PyDisplay — Warning")
        _warn_win.configure(bg=BG)
        _warn_win.resizable(False, False)
        _warn_win.wm_attributes("-topmost", True)
        _warn_win.overrideredirect(True)
        _warn_win.withdraw()

        # Custom title bar with X
        _make_titlebar(_warn_win, PANEL, BORDER, SUBTEXT, RED, on_close=_warn_win.destroy)

        tk.Label(_warn_win, text="⚠  Missing Required Packages",
                 bg=BG, fg=YELLOW, font=(_FONT, _BASE_FONT_SIZE, "bold"),
                 padx=16, pady=14).pack(fill="x")

        _pkg_list = "\n".join(f"  • {p}" for p in missing)
        tk.Label(_warn_win,
                 text=f"The following required package(s) are not installed:\n\n{_pkg_list}\n\n"
                      "PyDisplay may crash or not display correctly.\n"
                      "Install the missing packages first, or continue at your own risk.",
                 bg=BG, fg=TEXT, font=(_FONT, _BASE_FONT_SIZE),
                 justify="left", padx=16, pady=8, wraplength=340).pack(fill="x")

        tk.Frame(_warn_win, bg=BORDER, height=1).pack(fill="x", padx=10, pady=4)

        _warn_btn_row = tk.Frame(_warn_win, bg=BG)
        _warn_btn_row.pack(fill="x", padx=16, pady=10)

        def _do_continue():
            _warn_win.destroy()
            on_continue()

        _wb1 = tk.Label(_warn_btn_row, text="Continue Anyway",
                        bg=BORDER, fg=YELLOW,
                        font=(_FONT, _BASE_FONT_SIZE, "bold"), cursor="hand2",
                        padx=12, pady=6)
        _wb1.pack(side="right", padx=(8, 0))
        _wb1.bind("<Button-1>", lambda e: _do_continue())
        _wb1.bind("<Enter>",    lambda e: _wb1.config(fg=GREEN))
        _wb1.bind("<Leave>",    lambda e: _wb1.config(fg=YELLOW))

        _wb2 = tk.Label(_warn_btn_row, text="Go Back",
                        bg=BORDER, fg=SUBTEXT,
                        font=(_FONT, _BASE_FONT_SIZE, "bold"), cursor="hand2",
                        padx=12, pady=6)
        _wb2.pack(side="right")
        _wb2.bind("<Button-1>", lambda e: _warn_win.destroy())
        _wb2.bind("<Enter>",    lambda e: _wb2.config(fg=TEXT))
        _wb2.bind("<Leave>",    lambda e: _wb2.config(fg=SUBTEXT))

        _warn_win.update_idletasks()
        _rx = (root.winfo_rootx() + root.winfo_width()  // 2
               - _warn_win.winfo_reqwidth()  // 2)
        _ry = (root.winfo_rooty() + root.winfo_height() // 2
               - _warn_win.winfo_reqheight() // 2)
        _warn_win.geometry(f"+{_rx}+{_ry}")
        _warn_win.deiconify()
        _warn_win.grab_set()

    def _apply(launch_after=True):
        # Before doing anything: if launching and required packages are still missing, warn first
        if launch_after:
            _missing_required = [
                rd["disp"] for rd in row_data
                if rd["required"] and not rd["inst_ref"][0]
            ]
            if _missing_required:
                def _on_continue():
                    result["launch"] = True
                    result["skip_dep_check"] = _dsa_var.get()
                    root.destroy()
                _show_missing_warning(_missing_required, _on_continue)
                return

        to_install   = []
        to_uninstall = []
        seen = set()
        for rd in row_data:
            disp, pip_name, imp_name = rd["disp"], rd["pip_name"], rd["imp_name"]
            status_lbl = rd["status_lbl"]
            s = rd["action_var"].get()
            if s == "install" and pip_name not in seen:
                to_install.append((disp, pip_name, imp_name, status_lbl, rd))
                seen.add(pip_name)
            elif s == "remove" and pip_name not in seen:
                to_uninstall.append((disp, pip_name, imp_name, status_lbl, rd))
                seen.add(pip_name)

        if not to_install and not to_uninstall:
            if launch_after:
                result["launch"] = True
                result["skip_dep_check"] = _dsa_var.get()
                root.destroy()
            else:
                _refresh_all()
            return

        # Disable the launch button while running
        apply_btn.config(fg=SUBTEXT, cursor="arrow")
        apply_btn.unbind("<Button-1>")

        # Show progress bar
        total_ops = len(to_uninstall) + len(to_install)
        _done_ops = [0]
        def _advance():
            _done_ops[0] += 1
            root.after(0, lambda: _set_progress(_done_ops[0] / total_ops))

        def _write_log(pkg, pip_n, stdout_txt, stderr_txt, exc_msg):
            try:
                os.makedirs(_APP_DIR, exist_ok=True)
                with open(_install_log, "a", encoding="utf-8") as f:
                    f.write(f"\n{'─'*72}\n")
                    f.write(f"  {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  {pkg} ({pip_n})\n")
                    f.write(f"  Error: {exc_msg}\n")
                    for lbl, txt in (("stdout", stdout_txt), ("stderr", stderr_txt)):
                        if txt.strip():
                            f.write(f"\n  {lbl}:\n")
                            for line in txt.strip().splitlines():
                                f.write(f"    {line}\n")
                    f.write(f"{'─'*72}\n")
            except Exception:
                pass

        # _run_pywin32_postinstall defined at closure scope above

        def _worker():
            _had_error = False
            for disp, pip_name, imp_name, status_lbl, _rd in to_uninstall:
                root.after(0, lambda msg=f"Removing {disp}…": log_var.set(msg))
                root.after(0, lambda sl=status_lbl: sl.config(text="⏳  Removing…", fg=YELLOW))
                try:
                    # Try pip uninstall first using all known dist names
                    _pip_candidates = ["pynvml", "nvidia-ml-py"] if pip_name == "pynvml" else [pip_name]
                    _last_stdout = _last_err = ""
                    for _cand in _pip_candidates:
                        proc = subprocess.run(
                            [sys.executable, "-m", "pip", "uninstall", _cand, "-y",
                             "--disable-pip-version-check"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            creationflags=_NO_WIN)
                        _last_stdout = (proc.stdout or "").strip()
                        _last_err    = (proc.stderr or "").strip()
                    importlib.invalidate_caches()
                    # Purge sys.modules cache so _can_import hits the filesystem
                    for _mod in list(sys.modules.keys()):
                        if _mod == imp_name or _mod.startswith(imp_name + "."):
                            sys.modules.pop(_mod, None)
                    # If pip didn't register it, the files may exist loose in site-packages
                    # (no .dist-info folder) — find and delete them manually
                    if _can_import(imp_name):
                        import importlib.util as _ilu
                        _spec = _ilu.find_spec(imp_name)
                        if _spec and _spec.origin:
                            import shutil
                            _pkg_dir = os.path.dirname(_spec.origin)
                            if os.path.basename(_pkg_dir).lower() == imp_name.lower():
                                shutil.rmtree(_pkg_dir, ignore_errors=True)
                            else:
                                os.remove(_spec.origin)
                            _sp = os.path.dirname(_pkg_dir)
                            for _entry in os.listdir(_sp):
                                if _entry.lower().startswith(imp_name.lower()) and (
                                        _entry.endswith(".dist-info") or
                                        _entry.endswith(".egg-info")):
                                    shutil.rmtree(os.path.join(_sp, _entry), ignore_errors=True)
                        for _mod in list(sys.modules.keys()):
                            if _mod == imp_name or _mod.startswith(imp_name + "."):
                                sys.modules.pop(_mod, None)
                        importlib.invalidate_caches()
                    if _can_import(imp_name):
                        raise RuntimeError(
                            f"Could not remove {imp_name!r} — it may be installed outside "
                            f"the current Python environment. Try deleting it manually from "
                            f"site-packages.")
                    try:
                        os.makedirs(_APP_DIR, exist_ok=True)
                        with open(_install_log, "a", encoding="utf-8") as _lf:
                            _lf.write("\n" + "─"*72 + "\n")
                            _lf.write(f"  {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  REMOVED {disp} ({pip_name})\n")
                            if _last_stdout:
                                _lf.write(f"  pip stdout: {_last_stdout}\n")
                            _lf.write("─"*72 + "\n")
                    except Exception:
                        pass
                    root.after(0, lambda sl=status_lbl: sl.config(text="✘  removed", fg=SUBTEXT))
                    root.after(0, lambda msg=f"✔  {disp} removed.": log_var.set(msg))
                    # Update inst_ref so button flips to "Install" immediately
                    def _post_remove(rd=_rd):
                        rd["inst_ref"][0] = False
                        rd["action_var"].set("keep")
                        rd["refresh_btn"]()
                    root.after(0, _post_remove)
                except Exception as exc:
                    try:
                        os.makedirs(_APP_DIR, exist_ok=True)
                        with open(_install_log, "a", encoding="utf-8") as _lf:
                            _lf.write("\n" + "─"*72 + "\n")
                            _lf.write(f"  {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  REMOVE FAILED {disp} ({pip_name})\n")
                            _lf.write(f"  {exc}\n")
                            _lf.write("─"*72 + "\n")
                    except Exception:
                        pass
                    _had_error = True
                    root.after(0, lambda sl=status_lbl, pn=pip_name, s=str(exc): _set_failed(sl, pn, s))
                    root.after(0, lambda msg=f"✘  {disp} remove failed: {exc}": log_var.set(msg))
                _advance()
                time.sleep(0.3)

            for disp, pip_name, imp_name, status_lbl, _rd in to_install:
                root.after(0, lambda msg=f"Installing {disp}…": log_var.set(msg))
                root.after(0, lambda sl=status_lbl: sl.config(text="⏳  Installing…", fg=YELLOW))
                stdout_txt = stderr_txt = ""
                try:
                    # GPUtil requires distutils (removed in Python 3.12+); install setuptools first.
                    if pip_name == "GPUtil":
                        root.after(0, lambda: log_var.set("Installing setuptools for GPUtil…"))
                        subprocess.run(
                            [sys.executable, "-m", "pip", "install", "setuptools",
                             "--disable-pip-version-check", "--no-cache-dir"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            creationflags=_NO_WIN)
                        root.after(0, lambda: log_var.set(f"Installing {disp}…"))
                    proc = subprocess.run(
                        [sys.executable, "-m", "pip", "install", pip_name,
                         "--disable-pip-version-check", "--no-cache-dir"],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                        creationflags=_NO_WIN)
                    stdout_txt = proc.stdout or ""
                    stderr_txt = proc.stderr or ""
                    if proc.returncode != 0:
                        raise RuntimeError((stderr_txt.strip().splitlines() or [f"code {proc.returncode}"])[-1])
                    if pip_name == "pywin32":
                        # Skip post-install — triggers Windows DLL dialog if locked.
                        # pywin32 works after restart without it in modern pip.
                        root.after(0, lambda: log_var.set("Installing wmi…"))
                        _wmi_proc = subprocess.run(
                            [sys.executable, "-m", "pip", "install", "wmi",
                             "--disable-pip-version-check", "--no-cache-dir"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            creationflags=_NO_WIN)
                        if _wmi_proc.returncode != 0:
                            pass  # wmi may fail in current process — non-fatal, works after restart
                    importlib.invalidate_caches()
                    for _mod in list(sys.modules.keys()):
                        if _mod == imp_name or _mod.startswith(imp_name + "."):
                            sys.modules.pop(_mod, None)
                    if pip_name in ("pywin32", "wmi"):
                        try:
                            os.add_dll_directory(os.path.dirname(sys.executable))
                        except (AttributeError, OSError):
                            pass
                        try:
                            import site as _site
                            for _sp in _site.getsitepackages():
                                for _sub in ("", "win32", os.path.join("win32", "lib"), "win32com", "win32comext"):
                                    _d = os.path.join(_sp, _sub)
                                    if os.path.isdir(_d) and _d not in sys.path:
                                        sys.path.insert(0, _d)
                                try:
                                    os.add_dll_directory(_sp)
                                except (AttributeError, OSError):
                                    pass
                        except Exception:
                            pass
                        importlib.invalidate_caches()
                    # GPUtil needs distutils (removed in 3.12+); purge stale cache and
                    # inject the setuptools shim before verifying the import.
                    if pip_name == "GPUtil":
                        for _mod in list(sys.modules.keys()):
                            if _mod == "distutils" or _mod.startswith("distutils."):
                                sys.modules.pop(_mod, None)
                        importlib.invalidate_caches()
                        try:
                            import setuptools  # noqa — triggers distutils shim
                            import distutils.version
                        except Exception:
                            pass
                    _import_err = None
                    if pip_name == "pywin32":
                        # pywin32 DLLs can't load in current process — skip import check
                        pass
                    else:
                        try:
                            importlib.import_module(imp_name)
                        except Exception as _ie:
                            _import_err = str(_ie)
                        if _import_err is not None:
                            raise RuntimeError(f"installed but import failed: {_import_err}")
                    try:
                        ver_str = f"✔  v{_importlib_metadata.version(pip_name)}"
                    except Exception:
                        ver_str = "✔  installed"
                    root.after(0, lambda sl=status_lbl, vs=ver_str: sl.config(text=vs, fg=GREEN))
                    _log_msg = "✔  pywin32 installed. Restart PyDisplay to activate." if pip_name == "pywin32" else f"✔  {disp} installed."
                    root.after(0, lambda msg=_log_msg: log_var.set(msg))
                    # Update inst_ref so button flips to "Delete" immediately
                    def _post_install(rd=_rd):
                        rd["inst_ref"][0] = True
                        rd["action_var"].set("keep")
                        rd["refresh_btn"]()
                    root.after(0, _post_install)
                except Exception as exc:
                    _had_error = True
                    _exc_str = str(exc)
                    _write_log(disp, pip_name, stdout_txt, stderr_txt, _exc_str)
                    root.after(0, lambda sl=status_lbl, pn=pip_name, s=_exc_str: _set_failed(sl, pn, s))
                    root.after(0, lambda msg=f"✘  {disp} failed: {_exc_str}": log_var.set(msg))
                _advance()
                time.sleep(0.3)

            if _had_error:
                root.after(0, lambda: _set_progress(None))
                root.after(0, lambda: log_var.set(
                    "Some operations failed — see %APPDATA%\\PyDisplay\\PyDisplay_install.log"))
                root.after(0, _refresh_all)
                # Re-enable launch button
                root.after(0, lambda: apply_btn.config(fg=ACCENT, cursor="hand2"))
                root.after(0, lambda: apply_btn.bind("<Button-1>", lambda e: _apply()))
            elif launch_after:
                root.after(0, lambda: _set_progress(1.0))
                root.after(0, lambda: log_var.set("All done — launching PyDisplay…"))
                result["launch"] = True
                result["skip_dep_check"] = _dsa_var.get()
                root.after(1200, root.destroy)
            else:
                root.after(0, lambda: _set_progress(None))
                root.after(0, lambda: log_var.set("Done."))
                root.after(0, _refresh_all)
                root.after(0, lambda: apply_btn.config(fg=ACCENT, cursor="hand2"))
                root.after(0, lambda: apply_btn.bind("<Button-1>", lambda e: _apply()))

        threading.Thread(target=_worker, daemon=True).start()

    def _skip():
        result["launch"] = True
        result["skip_dep_check"] = _dsa_var.get()
        root.destroy()

    def _exit():
        result["launch"] = False
        root.destroy()

    def _check_duplicates():
        """Scan all site-packages dirs for packages installed in more than one location."""
        log_var.set("Scanning for duplicate installations…")

        def _scan():
            import site
            _dirs = []
            try:
                _dirs += site.getsitepackages()
            except Exception:
                pass
            try:
                _dirs.append(site.getusersitepackages())
            except Exception:
                pass
            _dirs = list(dict.fromkeys(d for d in _dirs if os.path.isdir(d)))

            # Build map: pip_name → list of (version, location)
            _pkg_locs = {}
            for _d in _dirs:
                for _di in os.listdir(_d):
                    if not _di.endswith(".dist-info"):
                        continue
                    _parts = _di[:-len(".dist-info")].rsplit("-", 1)
                    if len(_parts) != 2:
                        continue
                    _pname, _ver = _parts
                    _pname_norm = _pname.lower().replace("-", "_")
                    _pkg_locs.setdefault(_pname_norm, []).append((_ver, _d, _pname))

            # Only packages we care about that have >1 location
            _tracked = {rd["pip_name"].lower().replace("-", "_") for rd in row_data}
            _dups = {k: v for k, v in _pkg_locs.items()
                     if k in _tracked and len(v) > 1}

            root.after(0, lambda: _show_dup_dialog(_dups))

        def _show_dup_dialog(dups):
            log_var.set("")
            dlg = tk.Toplevel(root)
            dlg.title("PyDisplay — Duplicate Check")
            dlg.configure(bg=BG)
            dlg.resizable(False, False)
            dlg.wm_attributes("-topmost", True)
            dlg.overrideredirect(True)
            dlg.withdraw()

            # Title bar
            _make_titlebar(dlg, PANEL, BORDER, SUBTEXT, RED, on_close=dlg.destroy)

            if not dups:
                tk.Label(dlg, text="✔  No duplicate installations found",
                         bg=BG, fg=GREEN, font=(_FONT, _BASE_FONT_SIZE, "bold"),
                         padx=16, pady=16).pack(fill="x")
                tk.Frame(dlg, bg=BORDER, height=1).pack(fill="x", padx=10)
                cl = tk.Label(dlg, text="Close", bg=BORDER, fg=SUBTEXT,
                              font=(_FONT, _BASE_FONT_SIZE, "bold"), cursor="hand2",
                              padx=12, pady=6)
                cl.pack(pady=10)
                cl.bind("<Button-1>", lambda e: dlg.destroy())
                cl.bind("<Enter>",    lambda e: cl.config(fg=TEXT))
                cl.bind("<Leave>",    lambda e: cl.config(fg=SUBTEXT))
            else:
                tk.Label(dlg, text="⚠  Duplicate Installations Found",
                         bg=BG, fg=YELLOW, font=(_FONT, _BASE_FONT_SIZE, "bold"),
                         padx=16, pady=12).pack(fill="x")
                tk.Label(dlg,
                         text="These packages are installed in multiple locations.\n"
                              "Keep one and remove the rest to avoid conflicts.",
                         bg=BG, fg=SUBTEXT, font=(_FONT, _BASE_FONT_SIZE - 1),
                         padx=16, pady=(0, 6), justify="left").pack(fill="x")
                tk.Frame(dlg, bg=BORDER, height=1).pack(fill="x", padx=10, pady=(0, 6))

                body = tk.Frame(dlg, bg=BG)
                body.pack(fill="x", padx=16, pady=(0, 4))

                # For each duplicate, show the locations with checkboxes to remove
                _removals = []  # list of (pip_name, location, var)
                for pkg_norm, locs in dups.items():
                    # Use the display pip name from the first entry
                    pip_disp = locs[0][2]
                    tk.Label(body, text=pip_disp, bg=BG, fg=ACCENT,
                             font=(_FONT, _BASE_FONT_SIZE, "bold"), anchor="w").pack(fill="x", pady=(6, 2))
                    for i, (ver, loc, _) in enumerate(locs):
                        row_f = tk.Frame(body, bg=BG)
                        row_f.pack(fill="x", pady=1)
                        keep_label = "  (keep)" if i == 0 else ""
                        _var = tk.BooleanVar(value=(i != 0))  # tick all but first by default
                        chk = tk.Label(row_f,
                                       text=("☑" if _var.get() else "☐"),
                                       bg=BG, fg=RED if _var.get() else SUBTEXT,
                                       font=(_FONT, _BASE_FONT_SIZE - 1), cursor="hand2", padx=2)
                        if i == 0:
                            chk.config(text="☐", fg=SUBTEXT, cursor="arrow")
                        chk.pack(side="left")
                        short_loc = loc if len(loc) < 42 else "…" + loc[-40:]
                        tk.Label(row_f, text=f"v{ver}  {short_loc}{keep_label}",
                                 bg=BG, fg=SUBTEXT if i == 0 else TEXT,
                                 font=(_FONT, _BASE_FONT_SIZE - 1), anchor="w").pack(side="left", padx=(4, 0))
                        if i != 0:
                            def _toggle_chk(e, v=_var, lbl=chk):
                                v.set(not v.get())
                                lbl.config(text="☑" if v.get() else "☐",
                                           fg=RED if v.get() else SUBTEXT)
                            chk.bind("<Button-1>", _toggle_chk)
                            _removals.append((pip_disp, loc, _var))

                tk.Frame(dlg, bg=BORDER, height=1).pack(fill="x", padx=10, pady=(8, 0))
                act = tk.Frame(dlg, bg=BG)
                act.pack(fill="x", padx=16, pady=10)

                def _do_remove_dups():
                    to_del = [(p, l) for p, l, v in _removals if v.get()]
                    if not to_del:
                        dlg.destroy()
                        return
                    dlg.destroy()
                    def _rm_worker():
                        for pip_n, loc in to_del:
                            root.after(0, lambda n=pip_n: log_var.set(f"Removing duplicate {n}…"))
                            try:
                                # Find and delete the dist-info and package files in that location
                                import shutil as _sh
                                _norm = pip_n.lower().replace("-", "_")
                                for _entry in os.listdir(loc):
                                    _en = _entry.lower().replace("-", "_")
                                    if (_en.startswith(_norm) and
                                            (_entry.endswith(".dist-info") or
                                             _entry.endswith(".data") or
                                             _en == _norm or
                                             _en == _norm + ".py")):
                                        _fp = os.path.join(loc, _entry)
                                        try:
                                            if os.path.isdir(_fp):
                                                _sh.rmtree(_fp)
                                            else:
                                                os.remove(_fp)
                                        except Exception:
                                            pass
                            except Exception as _ex:
                                root.after(0, lambda n=pip_n, x=str(_ex):
                                           log_var.set(f"✘  {n}: {x}"))
                        root.after(0, lambda: log_var.set("✔  Duplicate removal complete."))
                        root.after(0, _refresh_all)
                    threading.Thread(target=_rm_worker, daemon=True).start()

                rm_btn = tk.Label(act, text="Remove Selected",
                                  bg=BORDER, fg=RED,
                                  font=(_FONT, _BASE_FONT_SIZE, "bold"), cursor="hand2",
                                  padx=12, pady=6)
                rm_btn.pack(side="right", padx=(8, 0))
                rm_btn.bind("<Button-1>", lambda e: _do_remove_dups())
                rm_btn.bind("<Enter>",    lambda e: rm_btn.config(fg=GREEN))
                rm_btn.bind("<Leave>",    lambda e: rm_btn.config(fg=RED))

                cl_btn2 = tk.Label(act, text="Cancel",
                                   bg=BORDER, fg=SUBTEXT,
                                   font=(_FONT, _BASE_FONT_SIZE, "bold"), cursor="hand2",
                                   padx=12, pady=6)
                cl_btn2.pack(side="right")
                cl_btn2.bind("<Button-1>", lambda e: dlg.destroy())
                cl_btn2.bind("<Enter>",    lambda e: cl_btn2.config(fg=TEXT))
                cl_btn2.bind("<Leave>",    lambda e: cl_btn2.config(fg=SUBTEXT))

            dlg.update_idletasks()
            _rx = root.winfo_rootx() + root.winfo_width()  // 2 - dlg.winfo_reqwidth()  // 2
            _ry = root.winfo_rooty() + root.winfo_height() // 2 - dlg.winfo_reqheight() // 2
            dlg.geometry(f"+{_rx}+{_ry}")
            dlg.deiconify()
            dlg.grab_set()

        threading.Thread(target=_scan, daemon=True).start()

    # Single LAUNCH button: applies queued installs/removals then starts PyDisplay
    apply_btn = _make_btn(btn_area, "▶  LAUNCH", ACCENT,  _apply)
    _make_btn(btn_area,             "SKIP",       SUBTEXT, _skip)
    _make_btn(btn_area,             "EXIT",       RED,     _exit)

    # ── Restore remembered dep-dialog position (or centre on first run) ─────
    root.update_idletasks()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    pw, ph = root.winfo_reqwidth(), root.winfo_reqheight()
    try:
        _pj = _read_config()
        _dx, _dy = int(_pj["dep_x"]), int(_pj["dep_y"])
        # Clamp to screen so it can never be off-screen after a resolution change
        _dx = max(0, min(_dx, sw - pw))
        _dy = max(0, min(_dy, sh - ph))
        root.geometry(f"+{_dx}+{_dy}")
    except Exception:
        root.geometry(f"+{sw//2 - pw//2}+{sh//2 - ph//2}")

    def _save_dep_pos():
        _write_config({"dep_x": root.winfo_x(), "dep_y": root.winfo_y()})

    root.bind("<Configure>", lambda e: _save_dep_pos() if e.widget is root else None)

    root.mainloop()
    return result


# ── Quick pre-check: if every package already imports, skip the window ────────

def _dep_skip_flagged():
    """Return True if user has previously chosen Don't show again."""
    return bool(_read_config().get("skip_dep_check", False))

def _save_dep_skip():
    """Write skip_dep_check=True into pos.json without overwriting other keys."""
    _write_config({"skip_dep_check": True})

def _has_saved_position():
    """Return True if a saved x/y position exists in the config."""
    _d = _read_config()
    return "x" in _d and "y" in _d

def _reopen_picker_flagged():
    """Return True if user has enabled always-reopen Choose Position."""
    return bool(_read_config().get("reopen_picker", False))

def _save_reopen_picker(enabled):
    """Write reopen_picker flag into pos.json without overwriting other keys."""
    _write_config({"reopen_picker": bool(enabled)})


# ── Singleton check — prevent multiple instances ──────────────────────────────

def _check_singleton():
    """Return a mutex handle if this is the first instance, or None if another is running.
    Shows a notice with countdown, OK to dismiss, or Kill & Continue to replace the old instance."""
    _MUTEX_NAME = "PyDisplay_SingletonMutex_8f3a2c1d"
    try:
        _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _handle = _kernel32.CreateMutexW(None, True, _MUTEX_NAME)
        _err    = ctypes.get_last_error()
        if _err == 183:  # ERROR_ALREADY_EXISTS
            _handle = None
    except Exception:
        _handle = None
        _err    = 0

    if _handle is None or _err == 183:
        # Another instance is running — show notice with countdown
        _result = {"action": "exit"}  # "exit" or "kill_and_continue"

        _nr = tk.Tk()
        _nr.overrideredirect(True)
        _nr.wm_attributes("-topmost", True)
        _nr.configure(bg="#141414")
        _nr.resizable(False, False)

        _BG     = "#141414"
        _PANEL  = "#1a1a1a"
        _BORDER = "#2a2a2a"
        _ACCENT = "#00bfff"
        _GREEN  = "#39ff14"
        _RED    = "#ff4444"
        _TEXT   = "#e0e0e0"
        _SUB    = "#606060"
        _CYAN   = "#00ffe5"

        def _do_exit():
            _result["action"] = "exit"
            _nr.destroy()

        def _do_kill():
            _result["action"] = "kill_and_continue"
            _nr.destroy()

        # Title bar
        _make_titlebar(_nr, _PANEL, _BORDER, _SUB, _RED,
                       on_close=_do_exit,
                       title_text="PyDisplay", title_fg=_ACCENT, title_bg=_PANEL)

        _fr = tk.Frame(_nr, bg=_BG, padx=20, pady=12)
        _fr.pack()

        tk.Label(_fr, text="Already Running", bg=_BG, fg=_TEXT,
                 font=(_FONT, _BASE_FONT_SIZE, "bold")).pack()
        tk.Label(_fr, text="Another instance of PyDisplay is active.",
                 bg=_BG, fg=_SUB, font=(_FONT, _BASE_FONT_SIZE - 2)).pack(pady=(4, 0))
        tk.Label(_fr, text="This new launch will exit unless you close the existing one.",
                 bg=_BG, fg=_SUB, font=(_FONT, _BASE_FONT_SIZE - 2)).pack(pady=(2, 10))

        tk.Frame(_fr, bg=_BORDER, height=1).pack(fill="x", pady=(0, 10))

        # Timer label (clickable to cancel countdown)
        _secs    = [10]
        _cancelled = [False]
        _timer_lbl = tk.Label(_fr, text=f"Closing in {_secs[0]}s  ·  click to cancel",
                              bg=_BG, fg=_SUB, font=(_FONT, _BASE_FONT_SIZE - 3),
                              cursor="hand2")
        _timer_lbl.pack(pady=(0, 8))

        def _cancel_timer(e=None):
            _cancelled[0] = True
            _timer_lbl.config(text="Auto-close cancelled", fg=_SUB, cursor="")
            _timer_lbl.unbind("<Button-1>")
        _timer_lbl.bind("<Button-1>", _cancel_timer)
        _timer_lbl.bind("<Enter>", lambda e: _timer_lbl.config(fg=_CYAN))
        _timer_lbl.bind("<Leave>", lambda e: _timer_lbl.config(
            fg=_SUB if not _cancelled[0] else _SUB))

        def _tick():
            if _cancelled[0] or not _nr.winfo_exists():
                return
            _secs[0] -= 1
            if _secs[0] <= 0:
                _nr.destroy()
                return
            _timer_lbl.config(text=f"Closing in {_secs[0]}s  ·  click to cancel")
            _nr.after(1000, _tick)
        _nr.after(1000, _tick)

        # Buttons row
        _btn_row = tk.Frame(_fr, bg=_BG)
        _btn_row.pack(fill="x")

        _kill_btn = tk.Label(_btn_row, text="Close Existing & Launch", bg=_BORDER, fg=_RED,
                             font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), cursor="hand2",
                             padx=8, pady=4)
        _kill_btn.pack(side="left", padx=(0, 6))
        _kill_btn.bind("<Button-1>", lambda e: _do_kill())
        _kill_btn.bind("<Enter>",    lambda e: _kill_btn.config(bg=_RED, fg=_BG))
        _kill_btn.bind("<Leave>",    lambda e: _kill_btn.config(bg=_BORDER, fg=_RED))

        _ok_btn = tk.Label(_btn_row, text="OK", bg=_BORDER, fg=_GREEN,
                           font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), cursor="hand2",
                           padx=12, pady=4)
        _ok_btn.pack(side="right")
        _ok_btn.bind("<Button-1>", lambda e: _do_exit())
        _ok_btn.bind("<Enter>",    lambda e: _ok_btn.config(fg=_CYAN))
        _ok_btn.bind("<Leave>",    lambda e: _ok_btn.config(fg=_GREEN))

        _nr.update_idletasks()
        _sw = _nr.winfo_screenwidth(); _sh = _nr.winfo_screenheight()
        _nw = _nr.winfo_reqwidth();    _nh = _nr.winfo_reqheight()
        _nr.geometry(f"+{_sw//2 - _nw//2}+{_sh//2 - _nh//2}")
        _nr.mainloop()

        if _result["action"] == "kill_and_continue":
            # Kill other PyDisplay instances using only stdlib — psutil may not be installed yet
            _this_pid = os.getpid()
            _killed   = []

            # Try psutil first if available
            try:
                import psutil as _psutil
                for _proc in _psutil.process_iter(["pid", "name", "cmdline"]):
                    try:
                        if _proc.pid == _this_pid:
                            continue
                        if (_proc.info["name"] or "").lower() not in ("python.exe", "pythonw.exe"):
                            continue
                        if "pydisplay" in " ".join(_proc.info["cmdline"] or []).lower():
                            _proc.kill()
                            _killed.append(_proc.pid)
                    except Exception:
                        pass
            except ImportError:
                # psutil not installed yet — fall back to tasklist + taskkill (pure Windows builtins)
                try:
                    _out = subprocess.check_output(
                        ["tasklist", "/FI", "IMAGENAME eq python.exe",
                         "/FI", "IMAGENAME eq pythonw.exe", "/FO", "CSV", "/NH"],
                        creationflags=_NO_WIN, text=True, stderr=subprocess.DEVNULL)
                except Exception:
                    _out = ""
                # tasklist CSV gives: "name","pid","session","#","mem"
                # but /FI with two IMAGENAME filters is OR on some versions — parse carefully
                for _row in _out.splitlines():
                    _row = _row.strip().strip('"')
                    if not _row:
                        continue
                    _cols = [c.strip('"') for c in _row.split('","')]
                    if len(_cols) < 2:
                        continue
                    try:
                        _pid = int(_cols[1])
                    except ValueError:
                        continue
                    if _pid == _this_pid:
                        continue
                    # We can't read cmdline without psutil, so kill any python.exe
                    # that isn't us — safe because the mutex confirmed one exists
                    subprocess.call(
                        ["taskkill", "/F", "/PID", str(_pid)],
                        creationflags=_NO_WIN,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    _killed.append(_pid)

            # Wait for killed process to release the mutex
            time.sleep(1.2 if _killed else 0.4)

            # Re-acquire mutex as new sole owner
            try:
                _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                _handle = _kernel32.CreateMutexW(None, True, _MUTEX_NAME)
                _err2   = ctypes.get_last_error()
                if _err2 == 183:
                    _handle = None
            except Exception:
                _handle = None
            return _handle

        raise SystemExit(0)

    return _handle  # keep alive for the lifetime of the process


_singleton_mutex = _check_singleton()

_dep_result = None
try:
    if not _dep_skip_flagged():
        _dep_result = _run_dependency_check()
        if not _dep_result["launch"]:
            raise SystemExit(0)
        if _dep_result.get("skip_dep_check"):
            _save_dep_skip()
except SystemExit:
    raise
except Exception:
    _write_crash_log(traceback.format_exc())
    raise SystemExit(1)

# ── Now safe to import third-party packages ───────────────────────────────────
import psutil


try:
    import pynvml
    pynvml.nvmlInit()
    NVIDIA_AVAILABLE = True
except Exception:
    NVIDIA_AVAILABLE = False

try:
    import GPUtil
    GPUTIL_AVAILABLE = True
except Exception:
    GPUTIL_AVAILABLE = False

try:
    import wmi
    WMI_AVAILABLE = True
except Exception:
    WMI_AVAILABLE = False

_cpu_base_mhz = None

def get_cpu_freq_ghz():
    """
    Real-time CPU frequency via WMI PercentProcessorPerformance * MaxClockSpeed.
    Matches Task Manager. Initialises COM per-thread as required by WMI.
    """
    global _cpu_base_mhz
    try:
        import pythoncom
        pythoncom.CoInitialize()
        try:
            w = wmi.WMI()
            if _cpu_base_mhz is None:
                _cpu_base_mhz = float(w.Win32_Processor()[0].MaxClockSpeed)
            perf = w.Win32_PerfFormattedData_Counters_ProcessorInformation(Name="_Total")
            if perf:
                pct = float(perf[0].PercentProcessorPerformance)
                return (_cpu_base_mhz * pct / 100.0) / 1000.0
        finally:
            pythoncom.CoUninitialize()
    except Exception:
        pass
    return None


def get_disk_io():
    """
    Returns (read_mb_per_s, write_mb_per_s) since last call.
    """
    global _last_disk, _last_disk_time
    cur = psutil.disk_io_counters()
    now = time.time()
    if cur is None:
        return 0.0, 0.0
    dt = now - _last_disk_time
    if dt <= 0:
        return 0.0, 0.0
    read  = (cur.read_bytes  - _last_disk.read_bytes)  / dt / (1024 * 1024)
    write = (cur.write_bytes - _last_disk.write_bytes) / dt / (1024 * 1024)
    _last_disk      = cur
    _last_disk_time = now
    return max(read, 0.0), max(write, 0.0)


def get_disk_usage():
    """
    Returns list of (mountpoint, used_gb, total_gb, percent) for physical drives.
    Skips CD-ROMs and very small (<1 GB) volumes.
    """
    results = []
    seen = set()
    for part in psutil.disk_partitions(all=False):
        if "cdrom" in part.opts.lower() or part.fstype == "":
            continue
        mp = part.mountpoint
        if mp in seen:
            continue
        seen.add(mp)
        try:
            u = psutil.disk_usage(mp)
            if u.total < 1024**3:
                continue
            results.append((mp, u.used / 1024**3, u.total / 1024**3, u.percent))
        except Exception:
            pass
    return results


BG      = "#0a0a0f"
PANEL   = "#111118"
BORDER  = "#1e1e2e"
ACCENT1 = "#00ffe5"
ACCENT2 = "#ff6b35"
ACCENT3 = "#7b30d1"
DIM     = "#3a3a5c"
TEXT    = "#e0e0f0"
SUBTEXT = "#6868a0"
RED     = "#ff3860"
GREEN   = "#39ff7f"

_last_net = psutil.net_io_counters()
_last_net_time = time.time()

_last_disk_io   = psutil.disk_io_counters()
_last_disk      = _last_disk_io if _last_disk_io else type("_D", (), {"read_bytes": 0, "write_bytes": 0})()
_last_disk_time = time.time()

# Windows click-through helpers
try:
    def _set_click_through(hwnd, enable, alpha_fraction=None):
        style = _user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        if enable:
            style |= (_WS_EX_LAYERED | _WS_EX_TRANSPARENT)
        else:
            style &= ~_WS_EX_TRANSPARENT
            style |= _WS_EX_LAYERED
        _user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, style)
        # Re-assert alpha so SetWindowLongW never resets it
        if alpha_fraction is not None:
            alpha_byte = max(0, min(255, int(alpha_fraction * 255)))
            _user32.SetLayeredWindowAttributes(hwnd, 0, alpha_byte, _LWA_ALPHA)

except Exception:
    def _set_click_through(hwnd, enable, alpha_fraction=None): pass


def get_network_speed():
    global _last_net, _last_net_time
    cur = psutil.net_io_counters()
    now = time.time()
    dt = now - _last_net_time
    if dt <= 0:
        return 0, 0
    recv = (cur.bytes_recv - _last_net.bytes_recv) / dt / (1024 * 1024)
    sent = (cur.bytes_sent - _last_net.bytes_sent) / dt / (1024 * 1024)
    _last_net = cur
    _last_net_time = now
    return max(recv, 0), max(sent, 0)


def get_gpu_power_temp_wmi():
    """Try OpenHardwareMonitor / LibreHardwareMonitor for power+temp."""
    for ns in (r"root\OpenHardwareMonitor", r"root\LibreHardwareMonitor"):
        try:
            w = wmi.WMI(namespace=ns)
            power, temp = None, None
            for s in w.Sensor():
                if s.SensorType == "Power" and "GPU Power" in s.Name:
                    power = float(s.Value)
                if s.SensorType == "Temperature" and "GPU Core" in s.Name:
                    temp = float(s.Value)
            if power is not None or temp is not None:
                return power, temp
        except Exception:
            pass
    return None, None


_VENDOR_MATCH = {
    "amd":   lambda n: "AMD" in n or "RADEON" in n,
    "intel": lambda n: "INTEL" in n and any(k in n for k in ("ARC", "XE", "IRIS", "UHD")),
}

def _get_wmi_gpu_stats(stats, vendor):
    """AMD or Intel GPU via WMI Win32_VideoController + OHM/LHM."""
    match = _VENDOR_MATCH.get(vendor, lambda n: False)
    try:
        import pythoncom
        pythoncom.CoInitialize()
        try:
            w = wmi.WMI()
            for vc in w.Win32_VideoController():
                name = (vc.Name or "").strip()
                if match(name.upper()):
                    stats["name"] = name
                    ram = getattr(vc, "AdapterRAM", None)
                    if ram:
                        stats["vram_total"] = ram / (1024**3)
                    break
            try:
                perf = w.Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine()
                total = sum(float(getattr(p, "UtilizationPercentage", 0) or 0) for p in perf)
                if total:
                    stats["usage"] = min(total, 100)
            except Exception:
                pass
        finally:
            pythoncom.CoUninitialize()
    except Exception:
        pass
    wmi_power, wmi_temp = get_gpu_power_temp_wmi()
    if stats["wattage"] is None:
        stats["wattage"] = wmi_power
    if stats["temp"] is None:
        stats["temp"] = wmi_temp
    return stats


def _detect_gpu_vendor():
    """Return 'nvidia', 'amd', 'intel', or 'unknown'."""
    try:
        import pythoncom
        pythoncom.CoInitialize()
        try:
            w = wmi.WMI()
            for vc in w.Win32_VideoController():
                name = (vc.Name or "").upper()
                if "NVIDIA" in name:
                    return "nvidia"
                if "AMD" in name or "RADEON" in name:
                    return "amd"
                if "INTEL" in name and any(k in name for k in ("ARC", "XE", "IRIS", "UHD")):
                    return "intel"
        finally:
            pythoncom.CoUninitialize()
    except Exception:
        pass
    return "unknown"


_gpu_vendor = None

def get_gpu_stats(app_inst=None):
    global _gpu_vendor
    _dev_idx = getattr(app_inst, "_gpu_device_index", 0) if app_inst else 0
    stats = {"name": "GPU", "usage": 0, "vram_used": 0,
             "vram_total": 0, "wattage": None, "temp": None}

    # ── NVIDIA (pynvml) ───────────────────────────────────────────────────────
    if NVIDIA_AVAILABLE:
        try:
            h = pynvml.nvmlDeviceGetHandleByIndex(_dev_idx)
            try:
                name = pynvml.nvmlDeviceGetName(h)
                stats["name"] = name.decode() if isinstance(name, bytes) else str(name)
            except Exception:
                stats["name"] = "NVIDIA GPU"
            stats["usage"] = pynvml.nvmlDeviceGetUtilizationRates(h).gpu
            mem = pynvml.nvmlDeviceGetMemoryInfo(h)
            stats["vram_used"] = mem.used / (1024**3)
            stats["vram_total"] = mem.total / (1024**3)
            try:
                stats["temp"] = pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU)
            except Exception:
                pass
            try:
                stats["wattage"] = pynvml.nvmlDeviceGetPowerUsage(h) / 1000.0
            except Exception:
                pass
        except Exception:
            pass
        # Fill any missing temp/power via OHM/LHM
        if stats["wattage"] is None or stats["temp"] is None:
            wmi_power, wmi_temp = get_gpu_power_temp_wmi()
            if stats["wattage"] is None: stats["wattage"] = wmi_power
            if stats["temp"] is None:    stats["temp"]    = wmi_temp
        return stats

    # ── Auto-detect vendor for non-NVIDIA ────────────────────────────────────
    if _gpu_vendor is None:
        _gpu_vendor = _detect_gpu_vendor()

    if _gpu_vendor == "amd":
        return _get_wmi_gpu_stats(stats, "amd")

    if _gpu_vendor == "intel":
        return _get_wmi_gpu_stats(stats, "intel")

    # ── GPUtil generic fallback ───────────────────────────────────────────────
    if GPUTIL_AVAILABLE:
        try:
            gpus = GPUtil.getGPUs()
            if gpus:
                g = gpus[0]
                stats["name"]       = g.name
                stats["usage"]      = g.load * 100
                stats["vram_used"]  = g.memoryUsed / 1024
                stats["vram_total"] = g.memoryTotal / 1024
                stats["temp"]       = g.temperature
        except Exception:
            pass

    if stats["wattage"] is None or stats["temp"] is None:
        wmi_power, wmi_temp = get_gpu_power_temp_wmi()
        if stats["wattage"] is None: stats["wattage"] = wmi_power
        if stats["temp"] is None:    stats["temp"]    = wmi_temp
    return stats







class MiniBar(tk.Frame):
    def __init__(self, parent, label, accent, unit="%", max_val=100, **kw):
        super().__init__(parent, bg=PANEL, **kw)
        self.accent  = accent
        self.unit    = unit
        self.max_val = max_val
        # Storage bars get a two-line layout with a free-space sub-label
        self._is_storage = (unit == "GB_STORAGE")
        row = tk.Frame(self, bg=PANEL)
        row.pack(fill="x")
        lbl_width = 4 if self._is_storage else 8
        self._drive_lbl = tk.Label(row, text=label, bg=PANEL, fg=SUBTEXT,
                 font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), width=lbl_width, anchor="w")
        self._drive_lbl.pack(side="left")
        val_width = 20 if self._is_storage else 11
        self._val_lbl = tk.Label(row, text="0", bg=PANEL, fg=TEXT,
                                  font=(_FONT, _BASE_FONT_SIZE, "bold"), width=val_width, anchor="e")
        self._val_lbl.pack(side="right")
        self._track = tk.Frame(row, bg=BORDER, height=8)
        self._track.pack(side="left", fill="x", expand=True, padx=4)
        self._fill = tk.Frame(self._track, bg=accent, height=8)
        self._fill.place(x=0, y=0, relheight=1, relwidth=0)
        # Sub-row for free space (storage only)
        if self._is_storage:
            sub = tk.Frame(self, bg=PANEL)
            sub.pack(fill="x")
            tk.Label(sub, text="", bg=PANEL, fg=SUBTEXT,
                     font=(_FONT, 1), width=lbl_width).pack(side="left")  # spacer
            self._free_lbl = tk.Label(sub, text="", bg=PANEL, fg=SUBTEXT,
                                      font=(_FONT, _BASE_FONT_SIZE - 2), anchor="w")
            self._free_lbl.pack(side="left", fill="x", expand=True, padx=4)

    def set(self, val):
        pct = min(val / self.max_val, 1.0) if self.max_val else 0
        color = RED if pct > 0.85 else ("#ffaa00" if pct > 0.60 else self.accent)
        self._fill.config(bg=color)
        self._fill.place(relwidth=pct)
        if self.unit == "GB":
            txt = f"{val:.1f}/{self.max_val:.0f} GB"
        elif self.unit == "GB_STORAGE":
            free = self.max_val - val
            txt = f"{val:.1f}/{self.max_val:.0f} GB  {pct*100:.0f}%"
            self._free_lbl.config(text=f"  {free:.1f} GB free")
        elif self.unit == "W":
            txt = f"{val:.0f} W"
        elif self.unit == "MB/s":
            txt = f"{val:.2f} MB/s"
        else:
            txt = f"{val:.0f}%"
        self._val_lbl.config(text=txt)



# ── Speedtest engine (pure stdlib — no extra dependencies) ───────────────────

def _st_http_get(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": "Python-speedtest/1.0",
                                               "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def _st_get_nearest_server():
    best = None
    best_lat = float("inf")
    try:
        data = _st_http_get(
            "https://www.speedtest.net/api/js/servers?engine=js&limit=10&https_functional=true",
            timeout=10)
        servers = json.loads(data)
    except Exception:
        servers = []
    for s in servers:
        host = s.get("host", "")
        if not host:
            continue
        host_clean = host.split(":")[0]
        port = int(host.split(":")[1]) if ":" in host else 8080
        try:
            t0 = time.perf_counter()
            sock = socket.create_connection((host_clean, port), timeout=3)
            sock.close()
            lat = (time.perf_counter() - t0) * 1000
            if lat < best_lat:
                best_lat = lat
                best = dict(s)
                best["_latency_ms"] = lat
        except Exception:
            continue
    return best

def _st_measure_ping(host, port, samples=5):
    times = []
    for _ in range(samples):
        try:
            t0 = time.perf_counter()
            s = socket.create_connection((host, port), timeout=3)
            s.close()
            times.append((time.perf_counter() - t0) * 1000)
            time.sleep(0.05)
        except Exception:
            pass
    return round(sum(times) / len(times), 1) if times else None

_ST_PARALLEL_STREAMS = 8  # Parallel connections — matches Ookla client

def _st_download_worker(base_url, t_end, counter, lock, cancel_flag):
    import itertools
    sizes = [2000, 2500, 3000, 3500, 4000]
    for size in itertools.cycle(sizes):
        if time.perf_counter() >= t_end or (cancel_flag and cancel_flag()):
            break
        url = f"{base_url}/random{size}x{size}.jpg"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Python-speedtest/1.0",
                                                       "Cache-Control": "no-cache"})
            with urllib.request.urlopen(req, timeout=10) as r:
                while time.perf_counter() < t_end:
                    if cancel_flag and cancel_flag():
                        return
                    chunk = r.read(65536)
                    if not chunk:
                        break
                    with lock:
                        counter[0] += len(chunk)
        except Exception:
            pass

def _st_upload_worker(upload_url, payload, t_end, counter, lock, cancel_flag):
    while time.perf_counter() < t_end:
        if cancel_flag and cancel_flag():
            break
        try:
            req = urllib.request.Request(
                upload_url, data=payload, method="POST",
                headers={"User-Agent": "Python-speedtest/1.0",
                         "Content-Type": "application/octet-stream",
                         "Cache-Control": "no-cache"})
            with urllib.request.urlopen(req, timeout=15) as r:
                r.read()
            with lock:
                counter[0] += len(payload)
        except Exception:
            pass

def _st_measure_download(base_url, duration=10, progress_cb=None, cancel_flag=None):
    from concurrent.futures import ThreadPoolExecutor
    import threading as _th
    total   = [0]
    lock    = _th.Lock()
    t_start = time.perf_counter()
    t_end   = t_start + duration
    with ThreadPoolExecutor(max_workers=_ST_PARALLEL_STREAMS) as ex:
        for _ in range(_ST_PARALLEL_STREAMS):
            ex.submit(_st_download_worker, base_url, t_end, total, lock, cancel_flag)
        while time.perf_counter() < t_end:
            if cancel_flag and cancel_flag():
                break
            time.sleep(0.25)
            if progress_cb:
                elapsed = time.perf_counter() - t_start
                with lock:
                    b = total[0]
                progress_cb((b * 8) / (elapsed * 1_000_000) if elapsed > 0 else 0)
    elapsed = time.perf_counter() - t_start
    with lock:
        b = total[0]
    return round((b * 8) / (elapsed * 1_000_000), 2) if elapsed > 0 and b > 0 else None

def _st_measure_upload(base_url, duration=10, progress_cb=None, cancel_flag=None):
    from concurrent.futures import ThreadPoolExecutor
    import threading as _th
    total      = [0]
    lock       = _th.Lock()
    payload    = os.urandom(512 * 1024)
    upload_url = f"{base_url}/upload.php"
    t_start    = time.perf_counter()
    t_end      = t_start + duration
    with ThreadPoolExecutor(max_workers=_ST_PARALLEL_STREAMS) as ex:
        for _ in range(_ST_PARALLEL_STREAMS):
            ex.submit(_st_upload_worker, upload_url, payload, t_end, total, lock, cancel_flag)
        while time.perf_counter() < t_end:
            if cancel_flag and cancel_flag():
                break
            time.sleep(0.25)
            if progress_cb:
                elapsed = time.perf_counter() - t_start
                with lock:
                    b = total[0]
                progress_cb((b * 8) / (elapsed * 1_000_000) if elapsed > 0 else 0)
    elapsed = time.perf_counter() - t_start
    with lock:
        b = total[0]
    return round((b * 8) / (elapsed * 1_000_000), 2) if elapsed > 0 and b > 0 else None

def _st_run(status_cb, result_cb, cancel_flag):
    try:
        status_cb("Finding nearest server…")
        server = _st_get_nearest_server()
        if cancel_flag():
            return
        if not server:
            result_cb({"error": "Could not find a test server.\nCheck your connection."})
            return
        host     = server["host"].split(":")[0]
        port     = int(server["host"].split(":")[1]) if ":" in server["host"] else 8080
        sponsor  = server.get("sponsor", "") or server.get("name", host)
        base_url = server.get("url", "").rsplit("/", 1)[0] or f"http://{server['host']}"

        status_cb(f"Measuring ping…\nServer: {sponsor}")
        ping = _st_measure_ping(host, port)
        if cancel_flag():
            return
        ping_str = f"{ping:.0f} ms" if ping is not None else "N/A"

        status_cb(f"Testing download…\nServer: {sponsor}\nPing: {ping_str}")
        def dl_prog(mbps):
            if not cancel_flag():
                status_cb(f"Testing DL Speed…\nServer: {sponsor}\nPing: {ping_str}\n↓ {mbps:.1f} Mbps…")
        download = _st_measure_download(base_url, duration=10, progress_cb=dl_prog, cancel_flag=cancel_flag)
        if cancel_flag():
            return
        dl_str = f"{download:.1f} Mbps" if download is not None else "N/A"

        status_cb(f"Testing upload…\nServer: {sponsor}\nPing: {ping_str}\n↓ {dl_str}")
        def ul_prog(mbps):
            if not cancel_flag():
                status_cb(f"Uploading…\nServer: {sponsor}\nPing: {ping_str}\n↓ {dl_str}\n↑ {mbps:.1f} Mbps…")
        upload = _st_measure_upload(base_url, duration=10, progress_cb=ul_prog, cancel_flag=cancel_flag)
        if cancel_flag():
            return

        result_cb({
            "server":   sponsor,
            "ping":     ping_str,
            "download": dl_str,
            "upload":   f"{upload:.1f} Mbps" if upload is not None else "N/A",
        })
    except Exception as ex:
        result_cb({"error": str(ex)})


class App(tk.Tk):
    # Popup attribute names — used by z-order and focus management
    _ALL_POPUP_ATTRS = (
        "_iplookup_popup", "_speedtest_popup", "_ttguide_popup",
        "_color_popup", "_settings_popup",
        "_mem_safe_popup", "_mem_aggr_popup",
    )

    def __init__(self):
        super().__init__()
        os.makedirs(_APP_DIR, exist_ok=True)
        self.title("PyDisplay")
        self.configure(bg=BG)
        self.resizable(True, True)
        self.minsize(300, 200)
        self.overrideredirect(True)
        self.lower()
        self.wm_attributes('-topmost', False)
        self._drag_x = 0
        self._drag_y = 0
        self._drag_wx = 0
        self._drag_wy = 0
        self._drag_ww = 0
        self._drag_wh = 0
        self._resize_edge = None
        self._popup_focus_order = []   # tracks which popup was clicked most recently
        # Popup references — declared here so getattr calls are never needed
        self._settings_popup     = None
        self._color_popup        = None
        self._speedtest_popup    = None
        self._iplookup_popup     = None
        self._ttguide_popup      = None
        self._mem_safe_popup     = None
        self._mem_aggr_popup     = None
        self._hwnd               = None
        self._outer              = None
        self._resize_top_btns    = None
        self._closing            = False
        self._tooltip_window     = None
        self._tooltip_after      = None
        self._settings_pending   = False
        self._color_pending      = False
        self._collapsed_sections = []
        self._last_disk_usage_applied = None
        self._speedtest_running = False
        self._speedtest_cancelled = False
        self._fetch_running = False       # guard: prevents thread pile-up on slow polls
        self._freq_min_seen = float('inf')
        self._freq_max_seen = 0.0
        self._gpu_device_index = 0
        self._gpu_temp_min  = float('inf')
        self._gpu_temp_max  = float('-inf')
        self._disk_usage_cache = None
        self._disk_usage_ts    = 0.0
        self._proc_cache       = (0, 0, 0)   # (procs, threads, handles)
        self._proc_ts          = 0.0
        self._last_snapshot    = None
        self._logging_active   = False
        self._log_after_id     = None
        self._color_gpu       = "#008000"
        self._color_cpu       = "#800000"
        self._color_mem       = "#008080"
        self._color_net       = "#7b30d1"
        self._color_disk      = "#8c4600"
        self._color_storage   = "#c0c0c0"
        self._color_tools = "#8080ff"
        self._font_size        = _BASE_FONT_SIZE
        self._datetime_format  = ""     # "" = off, "time", "date", "both"
        self._datetime_position = "bottom"  # "bottom" or "top"
        self._temp_unit        = "C"    # "C" or "F"
        self._refresh_ms       = 1000   # main poll interval: 500 / 1000 / 2000 / 5000
        self._log_interval_ms  = 15000  # log write interval: 5000 / 15000 / 30000 / 60000
        self._position_locked  = False
        self._click_through_on = True   # click-through active by default
        self._minimize_to_tray = False  # minimize to tray instead of close
        self._tray_show_gpu    = False  # show GPU % in tray tooltip when minimized
        self._tooltips_enabled = True   # show hover tooltips (gated by ctrl/click-through)
        self._ram_peak_pct    = 0.0
        self._net_peak_down   = 0.0
        self._net_peak_up     = 0.0
        self._disk_peak_read  = 0.0
        self._disk_peak_write = 0.0
        self._section_collapsed = {}
        self._hidden_sections   = set()   # sections hidden via Settings
        self._section_order     = _DEFAULT_SECTION_ORDER
        self._layout_mode       = "vertical"  # "vertical" or "horizontal"
        self._font_originals_prune_counter = 0  # prune dead widget refs every N polls
        self._active_theme_path = _DEFAULT_THEME_PATH
        # Only create Default theme file if it doesn't already exist
        if not os.path.exists(self._active_theme_path):
            try:
                with open(self._active_theme_path, "w") as _f:
                    json.dump({
                        "name": "Default",
                        "BG": BG, "PANEL": PANEL, "BORDER": BORDER,
                        "TEXT": TEXT, "SUBTEXT": SUBTEXT, "DIM": DIM,
                        "gpu":       "#008000",
                        "cpu":       "#800000",
                        "mem":       "#008080",
                        "net":       "#7b30d1",
                        "disk":      "#8c4600",
                        "storage":   "#c0c0c0",
                        "tools": "#8080ff",
                        "font_size": _BASE_FONT_SIZE,
                        "datetime_format":   "",
                        "datetime_position": "bottom",
                        "layout_mode":       "vertical",
                    }, _f, indent=4)
            except Exception:
                pass
        self._load_position()  # overwrites _color_* and globals with saved values
        try:
            self.iconbitmap("57347baf24597738002c6178-512.ico")
        except Exception:
            pass
        self._build()
        def _do_restore():
            self._apply_font_size(self._font_size)
            self._restore_collapsed()
            self._restore_hidden_sections()
            self._apply_section_order()
            if self._layout_mode == "horizontal":
                self.after(20, self._apply_layout_mode)
            # Re-apply position from saved config rather than winfo_x/y, which can
            # return 0,0 when a fullscreen game has taken over the display at launch.
            def _reapply_pos():
                self.update_idletasks()
                try:
                    _pd = _read_config()
                    _sx, _sy = int(_pd["x"]), int(_pd["y"])
                    self.geometry(f"{self.winfo_width()}x{self.winfo_reqheight()}+{_sx}+{_sy}")
                except Exception:
                    self.geometry(f"{self.winfo_width()}x{self.winfo_reqheight()}+{self.winfo_x()}+{self.winfo_y()}")
            self.after(100, _reapply_pos)
        self.after(50, _do_restore)
        self._poll()
        self.bind("<ButtonPress-1>", self._drag_start)
        self.bind("<B1-Motion>", self._drag_move)
        self.after(100, self._init_click_through)
        self._poll_ctrl()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Destroy>", self._on_destroy)

    def _on_close(self):
        self._closing = True
        self._save_position()
        self.destroy()

    def _on_destroy(self, e):
        if e.widget is self and not self._closing:
            self._save_position()

    def _hide_to_tray(self):
        """Hide the overlay and show a system-tray icon to restore it."""
        try:
            import pystray
            from PIL import Image, ImageDraw, ImageFont

            self.withdraw()
            self._save_position()

            _show_gpu = self._tray_show_gpu

            def _make_icon_image(gpu_pct=None):
                """Build a 256×256 tray icon. If gpu_pct is given, render it as text."""
                SIZE = 256
                img  = Image.new("RGBA", (SIZE, SIZE), (17, 17, 24, 255))
                draw = ImageDraw.Draw(img)
                if gpu_pct is not None:
                    # Green → yellow → red based on load
                    r = int(min(255, gpu_pct * 2.55))
                    g = int(max(0, 255 - gpu_pct * 2.55))
                    colour = (r, g, 20, 255)
                    label = f"{int(gpu_pct)}%"
                    # Try progressively smaller bold fonts until text fits
                    font = None
                    for size in (130, 110, 90, 70):
                        try:
                            font = ImageFont.truetype("arialbd.ttf", size)
                        except Exception:
                            try:
                                font = ImageFont.truetype("arial.ttf", size)
                            except Exception:
                                font = ImageFont.load_default()
                        try:
                            bb = draw.textbbox((0, 0), label, font=font)
                            tw, th = bb[2] - bb[0], bb[3] - bb[1]
                        except Exception:
                            tw, th = len(label) * (size // 2), size
                        if tw <= SIZE - 8:
                            break
                    # Draw a subtle border rect for contrast
                    pad = 6
                    draw.rounded_rectangle([pad, pad, SIZE - pad, SIZE - pad],
                                           radius=20, outline=colour, width=6)
                    # Centre text
                    draw.text(((SIZE - tw) / 2 - (bb[0] if font else 0),
                               (SIZE - th) / 2 - (bb[1] if font else 0)),
                              label, font=font, fill=colour)
                else:
                    r = SIZE // 2
                    draw.ellipse([r - 40, r - 40, r + 40, r + 40],
                                 fill=(57, 255, 127, 255))
                return img

            def _restore(icon, item):
                icon.stop()
                self.after(0, self.deiconify)

            def _quit(icon, item):
                icon.stop()
                self.after(0, lambda: (self._save_position(), self.destroy()))

            menu = pystray.Menu(
                pystray.MenuItem("Restore PyDisplay", _restore, default=True),
                pystray.MenuItem("Quit",              _quit),
            )

            initial_img = _make_icon_image(0 if _show_gpu else None)
            icon = pystray.Icon("PyDisplay", initial_img, "PyDisplay", menu)

            # Background thread: update icon image with live GPU % every second
            if _show_gpu:
                _tray_active = {"alive": True}

                def _gpu_icon_updater():
                    # Wait until the icon is fully running before sending updates
                    time.sleep(0.5)
                    while _tray_active["alive"]:
                        try:
                            snap = self._last_snapshot
                            gpu_pct = 0.0
                            if snap:
                                raw = snap["gpu"].get("usage", 0)
                                gpu_pct = float(raw) if raw is not None else 0.0
                            new_img = _make_icon_image(gpu_pct)
                            icon.icon  = new_img
                            icon.title = f"PyDisplay  |  GPU {gpu_pct:.0f}%"
                        except Exception:
                            pass
                        time.sleep(1)

                def _patched_restore(icon, item):
                    _tray_active["alive"] = False
                    icon.stop()
                    self.after(0, self.deiconify)

                def _patched_quit(icon, item):
                    _tray_active["alive"] = False
                    icon.stop()
                    self.after(0, lambda: (self._save_position(), self.destroy()))

                # Rebuild menu with patched callbacks that stop the updater thread
                icon.menu = pystray.Menu(
                    pystray.MenuItem("Restore PyDisplay", _patched_restore, default=True),
                    pystray.MenuItem("Quit",              _patched_quit),
                )

                threading.Thread(target=_gpu_icon_updater, daemon=True).start()

            threading.Thread(target=icon.run, daemon=True).start()

        except ImportError:
            # pystray / Pillow not installed — just hide and show a restore toast
            self.withdraw()
            self._save_position()
            # Fallback: re-show after a brief moment so the user isn't stuck
            self.after(3000, self.deiconify)
            try:
                self._show_toast("pystray not installed — restoring in 3s")
            except Exception:
                pass

    def _init_click_through(self):
        try:
            hwnd = _user32.GetParent(self.winfo_id())
            if hwnd == 0:
                hwnd = self.winfo_id()
            self._hwnd = hwnd
            _set_click_through(hwnd, True)  # start as click-through (non-ctrl)
            # Hide from taskbar: set TOOLWINDOW, clear APPWINDOW
            style = _user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
            style = (style | _WS_EX_TOOLWINDOW) & ~_WS_EX_APPWINDOW
            _user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, style)
        except Exception:
            self._hwnd = None

    def _popup_pos(self, pw, ph):
        """Return (x, y) to position a popup centred on the app window.
        Reads live window position first so the popup always follows the current
        location, even if the window was moved since the config was last saved.
        Falls back to the saved config (reliable under fullscreen games where
        winfo_* calls return unreliable values)."""
        # --- app window position: prefer live geometry (always up-to-date) ---
        try:
            ax, ay = self.winfo_rootx(), self.winfo_rooty()
            aw, ah = self.winfo_width(), self.winfo_height()
            if aw <= 1 or ah <= 1:
                raise ValueError("winfo returned degenerate size")
        except Exception:
            # fallback: parse geometry string
            try:
                geo = self.geometry()
                size_pos = geo.split("+")
                ax = int(size_pos[1])
                ay = int(size_pos[2])
                wh = size_pos[0].split("x")
                aw = int(wh[0])
                ah = int(wh[1])
            except Exception:
                # last resort: saved config (reliable under fullscreen games)
                try:
                    pos = _read_config()
                    ax = int(pos["x"])
                    ay = int(pos["y"])
                    aw = int(pos["w"])
                    ah = int(pos["h"])
                except Exception:
                    ax, ay = 0, 0
                    aw, ah = self.winfo_screenwidth(), self.winfo_screenheight()

        # --- screen bounds: use the monitor the app lives on via ctypes ---
        # This is immune to fullscreen games hijacking winfo_screenwidth/height
        sw, sh = 0, 0
        try:
            # MONITOR_DEFAULTTONEAREST = 2
            hwnd = self._hwnd if self._hwnd else 0
            hmon = _user32.MonitorFromWindow(hwnd, 2)
            class _RECT(ctypes.Structure):
                _fields_ = [("left",ctypes.c_long),("top",ctypes.c_long),
                             ("right",ctypes.c_long),("bottom",ctypes.c_long)]
            class _MONINFO(ctypes.Structure):
                _fields_ = [("cbSize",ctypes.c_ulong),("rcMonitor",_RECT),
                             ("rcWork",_RECT),("dwFlags",ctypes.c_ulong)]
            mi = _MONINFO()
            mi.cbSize = ctypes.sizeof(_MONINFO)
            if _user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                sw = mi.rcWork.right  - mi.rcWork.left
                sh = mi.rcWork.bottom - mi.rcWork.top
                # offset ax/ay relative to monitor work area for clamping
                ox = mi.rcWork.left
                oy = mi.rcWork.top
            else:
                raise RuntimeError("GetMonitorInfoW failed")
        except Exception:
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
            ox, oy = 0, 0

        x = ax + (aw - pw) // 2
        y = ay + (ah - ph) // 2
        x = max(ox + 10, min(x, ox + sw - pw - 10))
        y = max(oy + 10, min(y, oy + sh - ph - 10))
        return x, y

    def _make_popup(self, alpha=1.0):
        """Create a frameless, always-on-top Toplevel with standard popup settings."""
        w = tk.Toplevel(self)
        w.overrideredirect(True)
        w.wm_attributes("-topmost", True)
        w.wm_attributes("-alpha", alpha)
        w.resizable(False, False)
        return w

    def _get_popup_hwnd(self, popup):
        """Return the real top-level HWND for a tkinter popup."""
        try:
            hwnd = popup.winfo_id()
            parent = _user32.GetAncestor(hwnd, 2)  # GA_ROOT = 2
            return parent if parent else hwnd
        except Exception:
            return None

    def _pin_popup_topmost(self, popup):
        """Pin a popup above all other windows using Win32. Called once at open."""
        hwnd = self._get_popup_hwnd(popup)
        if hwnd:
            try:
                _user32.SetWindowPos(
                    hwnd, _HWND_TOPMOST, 0, 0, 0, 0,
                    _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE)
            except Exception:
                pass

    def _raise_popup(self, attr):
        """Move attr to end of focus order so it renders on top of other popups."""
        if attr in self._popup_focus_order:
            self._popup_focus_order.remove(attr)
        self._popup_focus_order.append(attr)
        self._repin_open_popups()

    def _repin_open_popups(self):
        """Re-establish the full popup z-stack every call.
        Stack (bottom→top): main window → popups in focus order (last = topmost).
        Uses SetWindowPos insert-after chain so the order is exact and stable."""
        # Build ordered list of open HWNDs (focus order last = top)
        ordered_attrs = [a for a in self._popup_focus_order if a in self._ALL_POPUP_ATTRS]
        # Append any open popups not yet in the focus order (fallback)
        for a in self._ALL_POPUP_ATTRS:
            if a not in ordered_attrs:
                ordered_attrs.append(a)

        open_hwnds = []
        for attr in ordered_attrs:
            w = getattr(self, attr, None)
            if w and w.winfo_exists():
                hwnd = self._get_popup_hwnd(w)
                if hwnd:
                    open_hwnds.append(hwnd)

        if not open_hwnds:
            return

        try:
            # Assert all popups as HWND_TOPMOST first
            for hwnd in open_hwnds:
                _user32.SetWindowPos(hwnd, _HWND_TOPMOST, 0, 0, 0, 0,
                                     _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE)
            # Stack them: top popup is last in open_hwnds; work downward
            # SetWindowPos(A, B) places A immediately BELOW B
            for i in range(len(open_hwnds) - 1, 0, -1):
                _user32.SetWindowPos(open_hwnds[i - 1], open_hwnds[i], 0, 0, 0, 0,
                                     _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE)
            # Place main window below the bottom-most popup
            if self._hwnd:
                _user32.SetWindowPos(self._hwnd, open_hwnds[0], 0, 0, 0, 0,
                                     _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE)
        except Exception:
            pass

    def _poll_ctrl(self):
        """Poll Ctrl key state every 50ms via GetAsyncKeyState - no focus needed."""
        try:
            VK_CONTROL = 0x11
            ctrl_held = bool(_user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)
            if self._hwnd:
                alpha = float(self.wm_attributes("-alpha"))
                if self._click_through_on:
                    # Normal mode: click-through unless CTRL is held
                    _set_click_through(self._hwnd, not ctrl_held, alpha)
                else:
                    # Click-through disabled: always interactive
                    _set_click_through(self._hwnd, False, alpha)
        except Exception:
            pass
        self._repin_open_popups()
        self.after(50, self._poll_ctrl)

    def _ctrl_ok(self, state=None):
        """Return True if CTRL is held OR click-through is disabled (always interactive)."""
        if not self._click_through_on:
            return True
        if state is not None:
            return bool(state & 0x4)
        try:
            return bool(_user32.GetAsyncKeyState(0x11) & 0x8000)
        except Exception:
            return False

    def _get_edge(self, x, y):
        w = self.winfo_width()
        h = self.winfo_height()
        m = 20  # corner margin px
        top    = y < m
        bottom = y > h - m
        left   = x < m
        right  = x > w - m
        if top    and left:  return "nw"
        if top    and right: return "ne"
        if bottom and left:  return "sw"
        if bottom and right: return "se"
        return None

    def _drag_start(self, event):
        self._repin_open_popups()
        if not self._ctrl_ok(event.state):
            return
        if self._position_locked:
            return
        self._resize_edge = self._get_edge(event.x, event.y)
        # Store screen-absolute start position
        self._drag_x = self.winfo_pointerx()
        self._drag_y = self.winfo_pointery()
        self._drag_wx = self.winfo_x()
        self._drag_wy = self.winfo_y()
        self._drag_ww = self.winfo_width()
        self._drag_wh = self.winfo_height()

    def _drag_move(self, event):
        if not self._ctrl_ok(event.state):
            return
        if self._position_locked:
            return
        px = self.winfo_pointerx()
        py = self.winfo_pointery()
        dx = px - self._drag_x
        dy = py - self._drag_y
        edge = self._resize_edge
        if edge is None:
            self.geometry(f"+{self._drag_wx + dx}+{self._drag_wy + dy}")
        else:
            x, y = self._drag_wx, self._drag_wy
            w, h = self._drag_ww, self._drag_wh
            if "e" in edge: w = max(150, w + dx)
            if "s" in edge: h = max(100, h + dy)
            if "w" in edge:
                w = max(150, w - dx)
                x = self._drag_wx + self._drag_ww - w
            if "n" in edge:
                h = max(100, h - dy)
                y = self._drag_wy + self._drag_wh - h
            self.geometry(f"{w}x{h}+{x}+{y}")

    def _save_position(self):
        try:
            collapsed = [k for k, v in self._section_collapsed.items() if v]
            _write_config({
                "config_version": _CONFIG_VERSION,
                "x": self.winfo_x(), "y": self.winfo_y(),
                "w": self.winfo_width(), "h": self.winfo_height(),
                "active_theme": self._active_theme_path,
                "collapsed": collapsed,
                "hidden_sections": list(self._hidden_sections),
                "section_order":   list(self._section_order),
                "refresh_ms":      self._refresh_ms,
                "log_interval_ms": self._log_interval_ms,
                "always_on_top":   bool(self.wm_attributes("-topmost")),
                "temp_unit":       self._temp_unit,
                "click_through":   self._click_through_on,
                "position_locked": self._position_locked,
                "minimize_to_tray": self._minimize_to_tray,
                "tray_show_gpu":    self._tray_show_gpu,
                "gpu_device_index": self._gpu_device_index,
                "tooltips_enabled": self._tooltips_enabled,
                "layout_mode":      self._layout_mode,
            })
        except Exception as e:
            _log_error("_save_position", e)

    def _migrate_config(self, pos):
        """Migrate old config dicts to the current schema. Returns the updated dict.
        Increment _CONFIG_VERSION and add a new elif block here whenever the schema changes."""
        v = pos.get("config_version", 0)
        if v >= _CONFIG_VERSION:
            return pos
        if v < 1:
            pos["config_version"] = 1
        # Future migrations go here:
        # if v < 2:
        #     pos["new_key"] = pos.pop("old_key", default)
        #     pos["config_version"] = 2
        _write_config(pos)
        return pos

    def _load_position(self):
        try:
            pos = _read_config()
            if not pos:
                raise ValueError("empty config")
            pos = self._migrate_config(pos)
            self.geometry(f"{pos['w']}x{pos['h']}+{pos['x']}+{pos['y']}")
            saved_theme = pos.get("active_theme", "")
            if saved_theme and os.path.exists(saved_theme):
                self._active_theme_path = saved_theme
            self._collapsed_sections = pos.get("collapsed", [])
            self._hidden_sections    = set(pos.get("hidden_sections", []))
            _saved_order = pos.get("section_order", [])
            if _saved_order:
                # Merge: keep saved order, append any new keys not yet in it
                _default = _DEFAULT_SECTION_ORDER
                _merged  = [k for k in _saved_order if k in _default]
                _merged += [k for k in _default if k not in _merged]
                self._section_order = _merged
            self._refresh_ms      = int(pos.get("refresh_ms",      1000))
            self._log_interval_ms = int(pos.get("log_interval_ms", 15000))
            if "always_on_top" in pos:
                self.wm_attributes("-topmost", bool(pos["always_on_top"]))
            self._click_through_on  = bool(pos.get("click_through",    True))
            self._position_locked   = bool(pos.get("position_locked",   False))
            self._minimize_to_tray  = bool(pos.get("minimize_to_tray",  False))
            self._tray_show_gpu     = bool(pos.get("tray_show_gpu",     False))
            self._gpu_device_index  = int(pos.get("gpu_device_index",  0))
            self._temp_unit         = pos.get("temp_unit", "C")
            self._tooltips_enabled  = bool(pos.get("tooltips_enabled", True))
            self._layout_mode       = pos.get("layout_mode", "vertical")
        except Exception:
            pass
        # Apply the active theme (Default or last used)
        try:
            with open(self._active_theme_path) as f:
                t = json.load(f)
            t = self._validate_theme(t)
            global BG, PANEL, BORDER, TEXT, SUBTEXT, DIM
            BG=t["BG"]; PANEL=t["PANEL"]; BORDER=t["BORDER"]
            TEXT=t["TEXT"]; SUBTEXT=t["SUBTEXT"]; DIM=t["DIM"]
            self._color_gpu=t["gpu"]; self._color_cpu=t["cpu"]; self._color_mem=t["mem"]
            self._color_net=t["net"]; self._color_disk=t["disk"]; self._color_storage=t["storage"]
            self._color_tools=t.get("tools", "#8080ff")
            self._font_size=t["font_size"]; self._datetime_format=t["datetime_format"]
            self._datetime_position=t["datetime_position"]
            if "opacity" in t:
                self.wm_attributes("-alpha", float(t["opacity"]))
            self._layout_mode=t["layout_mode"]
        except Exception:
            pass

    # ── Settings import / export ──────────────────────────────────────────────

    def _export_settings(self):
        """Save all current settings + active theme into one portable JSON."""
        path = _fd.asksaveasfilename(
            title="Export PyDisplay Settings",
            defaultextension=".json",
            filetypes=[("JSON file", "*.json"), ("All files", "*.*")],
            initialfile="PyDisplay_settings.json",
            parent=self,
        )
        if not path:
            return
        try:
            # Gather current position/behaviour data
            collapsed = [k for k, v in self._section_collapsed.items() if v]
            data = {
                "__pydisplay_export__": True,
                "config_version": _CONFIG_VERSION,
                # Theme colours
                "BG": BG, "PANEL": PANEL, "BORDER": BORDER,
                "TEXT": TEXT, "SUBTEXT": SUBTEXT, "DIM": DIM,
                "gpu":       self._color_gpu,
                "cpu":       self._color_cpu,
                "mem":       self._color_mem,
                "net":       self._color_net,
                "disk":      self._color_disk,
                "storage":   self._color_storage,
                "tools": self._color_tools,
                # Display options
                "font_size":       self._font_size,
                "datetime_format": self._datetime_format,
                "datetime_position": self._datetime_position,
                "temp_unit":       self._temp_unit,
                # Behaviour
                "refresh_ms":      self._refresh_ms,
                "log_interval_ms": self._log_interval_ms,
                "always_on_top":   bool(self.wm_attributes("-topmost")),
                "opacity":         round(float(self.wm_attributes("-alpha")), 2),
                "click_through":   self._click_through_on,
                "position_locked": self._position_locked,
                "minimize_to_tray": self._minimize_to_tray,
                "tray_show_gpu":    self._tray_show_gpu,
                # Layout
                "collapsed":       collapsed,
                "hidden_sections": list(self._hidden_sections),
                "section_order":   list(self._section_order),
                # GPU device index
                "gpu_device_index": self._gpu_device_index,
                # Tooltips
                "tooltips_enabled": self._tooltips_enabled,
                "layout_mode":      self._layout_mode,
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
            self._show_toast("✔  Settings exported")
        except Exception as exc:
            self._show_toast(f"✘  Export failed: {exc}")

    def _import_settings(self):
        """Load a previously exported settings JSON and apply everything."""
        path = _fd.askopenfilename(
            title="Import PyDisplay Settings",
            filetypes=[("JSON file", "*.json"), ("All files", "*.*")],
            parent=self,
        )
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if not data.get("__pydisplay_export__"):
                self._show_toast("✘  Not a PyDisplay settings file")
                return

            # Apply theme colours
            self._apply_theme(
                data.get("BG",      BG),
                data.get("PANEL",   PANEL),
                data.get("BORDER",  BORDER),
                data.get("gpu",     self._color_gpu),
                data.get("cpu",     self._color_cpu),
                data.get("mem",     self._color_mem),
                data.get("net",     self._color_net),
                data.get("disk",    self._color_disk),
                data.get("storage", self._color_storage),
                data.get("TEXT",    TEXT),
                data.get("SUBTEXT", SUBTEXT),
                data.get("DIM",     DIM),
                data.get("tools", self._color_tools),
            )
            # Apply display options
            if "font_size" in data:
                self._apply_font_size(data["font_size"])
            self._datetime_format = data.get("datetime_format", self._datetime_format)
            self._datetime_position = data.get("datetime_position", self._datetime_position)
            self._temp_unit       = data.get("temp_unit",       self._temp_unit)
            # Apply behaviour
            self._refresh_ms      = int(data.get("refresh_ms",      self._refresh_ms))
            self._log_interval_ms = int(data.get("log_interval_ms", self._log_interval_ms))
            self.wm_attributes("-topmost", bool(data.get("always_on_top", False)))
            self.wm_attributes("-alpha",   float(data.get("opacity", 1.0)))
            self._click_through_on  = bool(data.get("click_through",    True))
            self._position_locked   = bool(data.get("position_locked",   False))
            self._minimize_to_tray  = bool(data.get("minimize_to_tray",  False))
            self._tray_show_gpu     = bool(data.get("tray_show_gpu",     False))
            self._tooltips_enabled  = bool(data.get("tooltips_enabled",  True))
            if "layout_mode" in data:
                self._layout_mode = data["layout_mode"]
                self.after(80, self._apply_layout_mode)
            # Apply layout
            collapsed = data.get("collapsed", [])
            self._section_collapsed = {k: True for k in collapsed}
            self._hidden_sections = set(data.get("hidden_sections", []))
            saved_order = data.get("section_order", [])
            if saved_order:
                _default = _DEFAULT_SECTION_ORDER
                merged = [k for k in saved_order if k in _default]
                merged += [k for k in _default if k not in merged]
                self._section_order = merged
            # Apply GPU device index
            _new_idx = int(data.get("gpu_device_index", 0))
            if _new_idx != self._gpu_device_index:
                self._gpu_device_index = _new_idx
                self._reinit_nvidia_handle()
            # Restore collapsed + hidden + order
            self.after(50, self._restore_collapsed)
            self.after(60, self._restore_hidden_sections)
            self.after(70, self._apply_section_order)
            # Save so the imported state persists
            self._save_position()
            self._show_toast("✔  Settings imported")
            # Close settings popup if open so user sees fresh state
            try:
                if self._settings_popup.winfo_exists():
                    self._settings_popup.destroy()
            except Exception:
                pass
        except Exception as exc:
            self._show_toast(f"✘  Import failed: {exc}")

    def _reinit_nvidia_handle(self):
        """Invalidate cached GPU vendor so get_gpu_stats re-detects after index change."""
        global _gpu_vendor
        if not NVIDIA_AVAILABLE:
            return
        _gpu_vendor = None

    def _storage_bust(self, lbl, e):
        if not self._ctrl_ok(e.state):
            return
        self._disk_usage_ts = 0.0
        lbl.config(fg=GREEN)
        self.after(1000, lambda: lbl.config(fg=SUBTEXT))

    def _restore_collapsed(self):
        """Collapse any sections that were collapsed at last save."""
        collapsed = self._collapsed_sections
        if not collapsed:
            return
        section_map = {
            "gpu":     "_gpu_hdr",
            "cpu":     "_cpu_hdr",
            "mem":     "_mem_hdr",
            "net":     "_net_hdr",
            "disk":    "_disk_hdr",
            "storage": "_storage_hdr",
        }
        for key in collapsed:
            hdr = getattr(self, section_map.get(key, ""), None)
            if hdr is None:
                continue
            body = getattr(hdr, "_body", None)
            if body:
                body.pack_forget()
                hdr._arrow.config(text="▶")
                self._section_collapsed[key] = True
        self.update_idletasks()
        self.geometry(f"{self.winfo_width()}x{self.winfo_reqheight()}")

    def _restore_hidden_sections(self):
        """Hide any sections that were hidden at last save."""
        for key in self._hidden_sections:
            w = getattr(self, f"_section_wrapper_{key}", None)
            if w:
                w.pack_forget()
                w.grid_forget()
        self.update_idletasks()
        self.geometry(f"{self.winfo_width()}x{self.winfo_reqheight()}")

    def _apply_section_order(self):
        """Repack all section wrappers in outer according to _section_order."""
        order   = self._section_order
        hidden  = self._hidden_sections

        # Unpack all wrappers first
        for key in order:
            w = getattr(self, f"_section_wrapper_{key}", None)
            if w:
                w.pack_forget()
                w.grid_forget()

        # If horizontal mode, delegate to _apply_layout_mode
        if self._layout_mode == "horizontal":
            self._apply_layout_mode()
            return

        # Vertical: pack wrappers in order
        for key in order:
            if key in hidden:
                continue
            w = getattr(self, f"_section_wrapper_{key}", None)
            if w:
                w.pack(fill="x")

        self.update_idletasks()
        self.geometry(f"{self.winfo_width()}x{self.winfo_reqheight()}")

    def _apply_layout_mode(self):
        """Switch between vertical (stacked) and horizontal (two-column grid) layout."""
        mode    = self._layout_mode
        outer   = self._outer
        if outer is None:
            return

        order   = self._section_order
        hidden  = self._hidden_sections
        visible = [k for k in order if k not in hidden]

        # Unpack / ungrid all wrappers
        for key in order:
            w = getattr(self, f"_section_wrapper_{key}", None)
            if w:
                w.pack_forget()
                w.grid_forget()

        if mode == "horizontal":
            # ── Horizontal: one column per visible section, all in one row ───
            outer.pack_configure(fill="both")
            outer.config(padx=4)                 # tighter side padding in horizontal mode
            n = max(1, len(visible))

            # Clear all previous column/row configs
            for c in range(10):
                try: outer.columnconfigure(c, weight=0, uniform="", minsize=0)
                except Exception: pass
            outer.rowconfigure(0, weight=1)       # allow rows to stretch vertically

            # Equal-width columns
            for c in range(n):
                outer.columnconfigure(c, weight=1, uniform="col")

            for i, key in enumerate(visible):
                w = getattr(self, f"_section_wrapper_{key}", None)
                if w is None:
                    continue
                # Divider between sections via padx
                px_l = 0 if i == 0 else 2
                px_r = 0 if i == n - 1 else 2
                w.grid(row=0, column=i, sticky="nsew", padx=(px_l, px_r), pady=0)

            self.update_idletasks()
            # Each section is naturally ~280px wide; add small gutter per gap
            section_w = 280
            gutter = (n - 1) * 4 + 8   # gaps + outer padding
            target_w = section_w * n + gutter
            target_h = self.winfo_reqheight()
            self.geometry(f"{target_w}x{target_h}")
        else:
            # ── Vertical: pack wrappers stacked ──────────────────────────────
            outer.pack_configure(fill="x")
            outer.config(padx=12)               # restore original side padding
            # Clear grid config
            for c in range(10):
                try: outer.columnconfigure(c, weight=0, uniform="", minsize=0)
                except Exception: pass
            outer.rowconfigure(0, weight=0)
            for key in visible:
                w = getattr(self, f"_section_wrapper_{key}", None)
                if w:
                    w.pack(fill="x")

            self.update_idletasks()
            self.geometry(f"320x{self.winfo_reqheight()}")

    def _show_toast(self, message):
        """Show an auto-dismissing popup centred on the app window."""
        toast = self._make_popup()
        toast.configure(bg=PANEL)
        toast.wm_attributes("-topmost", bool(self.wm_attributes("-topmost")))

        tk.Frame(toast, bg=GREEN, height=2).pack(fill="x")
        tk.Label(toast, text=message, bg=PANEL, fg=GREEN,
                 font=(_FONT, _BASE_FONT_SIZE + 1, "bold"), padx=20, pady=12).pack()
        tk.Frame(toast, bg=GREEN, height=2).pack(fill="x")

        self._apply_font_size(self._font_size, root=toast)

        toast.update_idletasks()
        tw = toast.winfo_reqwidth()
        th = toast.winfo_reqheight()
        tx = self.winfo_x() + self.winfo_width() // 2 - tw // 2
        ty = self.winfo_y() + self.winfo_height() // 2 - th // 2
        toast.geometry(f"{tw}x{th}+{tx}+{ty}")
        toast.after(2000, lambda: toast.destroy() if toast.winfo_exists() else None)

    def _ip_lookup(self):
        """Show local IPv4, IPv6 and public IP in a speedtest-style popup."""
        if self._iplookup_popup and self._iplookup_popup.winfo_exists():
            self._iplookup_popup.lift()
            return

        dlg = self._make_popup()
        dlg.configure(bg=PANEL)
        self._iplookup_popup = dlg
        self._raise_popup("_iplookup_popup")
        dlg.bind("<ButtonPress>", lambda e: self._raise_popup("_iplookup_popup"))
        dlg.after(30, lambda: self._pin_popup_topmost(dlg) if dlg.winfo_exists() else None)

        # ── Title bar ─────────────────────────────────────────────────────────
        _make_titlebar(dlg, PANEL, BORDER, SUBTEXT, RED,
                       on_close=lambda: dlg.destroy() if dlg.winfo_exists() else None,
                       title_text="⧖ IP LOOKUP", title_fg=self._color_tools, title_bg=PANEL,
                       separator_color=self._color_tools)

        # ── Inner content ─────────────────────────────────────────────────────
        inner = tk.Frame(dlg, bg=PANEL, padx=16, pady=12)
        inner.pack(fill="x")

        _spin_chars = ["◐", "◓", "◑", "◒"]
        _spin_state = {"idx": 0}
        _spin_job   = [None]

        spin_lbl = tk.Label(inner, text="◐  Fetching IPs…", bg=PANEL, fg=ACCENT1,
                            font=(_FONT, _BASE_FONT_SIZE - 1, "bold"))
        spin_lbl.pack(pady=(0, 6))

        _fields = {}
        _public_revealed = {"v": False}
        for label, key, color in [
            ("Public",  "public", RED),
            ("IPv4",    "ipv4",   TEXT),
        ]:
            row = tk.Frame(inner, bg=PANEL)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=f"{label}:", bg=PANEL, fg=SUBTEXT,
                     font=(_FONT, _BASE_FONT_SIZE - 2), anchor="e", width=7).pack(side="left")
            val = tk.Label(row, text="—", bg=PANEL, fg=color,
                           font=(_FONT, _BASE_FONT_SIZE - 1, "bold"), anchor="w")
            val.pack(side="left", padx=(4, 0))
            _fields[key] = (val, color)

        # ── Bottom bar ────────────────────────────────────────────────────────
        tk.Frame(dlg, bg=BORDER, height=1).pack(fill="x", padx=8, pady=(8, 0))
        btn_row = tk.Frame(dlg, bg=PANEL, pady=6)
        btn_row.pack(fill="x", padx=12)
        close_btn = tk.Label(btn_row, text="CLOSE", bg=BORDER, fg=SUBTEXT,
                             font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), cursor="hand2", padx=10, pady=3)
        close_btn.pack(side="right")
        def _close_ip():
            if dlg.winfo_exists():
                dlg.destroy()
            return "break"
        close_btn.bind("<Button-1>", lambda e: _close_ip())
        close_btn.bind("<Enter>",    lambda e: close_btn.config(fg=RED))
        close_btn.bind("<Leave>",    lambda e: close_btn.config(fg=SUBTEXT))

        # ── Spinner ───────────────────────────────────────────────────────────
        def _tick():
            if not dlg.winfo_exists():
                return
            _spin_state["idx"] = (_spin_state["idx"] + 1) % len(_spin_chars)
            ch  = _spin_chars[_spin_state["idx"]]
            cur = spin_lbl.cget("text")
            tail = cur.split("  ", 1)[1] if "  " in cur else cur
            spin_lbl.config(text=f"{ch}  {tail}")
            _spin_job[0] = self.after(120, _tick)
        _spin_job[0] = self.after(120, _tick)

        # ── Copy on click helper ──────────────────────────────────────────────
        def _copy(lbl, fg):
            val = lbl.cget("text")
            if val in ("—", "N/A", ""):
                return
            dlg.clipboard_clear()
            dlg.clipboard_append(val)
            lbl.config(text="✓ copied", fg=GREEN)
            dlg.after(1000, lambda: lbl.config(text=val, fg=fg) if dlg.winfo_exists() else None)

        # ── Position popup: bottom = top of main app; stack below speedtest if open ──
        self._apply_font_size(self._font_size, root=dlg)
        dlg.update_idletasks()
        dlg.update_idletasks()
        dw = max(dlg.winfo_reqwidth(), 280)
        dh = dlg.winfo_reqheight() + 4
        ax = self.winfo_rootx()
        ay = self.winfo_rooty()
        aw = self.winfo_width()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = ax + (aw - dw) // 2
        # Stack popups upward: bottom of this popup = top of main app (or top of speedtest)
        y = ay - dh
        st = self._speedtest_popup
        if st and st.winfo_exists():
            try:
                st_geo = st.geometry()  # "WxH+X+Y"
                st_y = int(st_geo.split("+")[2])
                y = st_y - dh
            except Exception:
                y = ay - dh - 4
        x = max(10, min(x, sw - dw - 10))
        y = max(10, min(y, sh - dh - 10))
        dlg.geometry(f"{dw}x{dh}+{x}+{y}")

        # ── Fetch in background ───────────────────────────────────────────────
        def _fetch():
            results = {}

            # Local IPv4
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                results["ipv4"] = s.getsockname()[0]
                s.close()
            except Exception:
                results["ipv4"] = "N/A"

            # Public IP
            try:
                with urllib.request.urlopen(
                        urllib.request.Request("https://api.ipify.org",
                            headers={"User-Agent": "PyDisplay/1.0"}), timeout=6) as r:
                    results["public"] = r.read().decode().strip()
            except Exception:
                try:
                    with urllib.request.urlopen("https://ifconfig.me/ip", timeout=6) as r:
                        results["public"] = r.read().decode().strip()
                except Exception:
                    results["public"] = "N/A"

            def _apply():
                if not dlg.winfo_exists():
                    return
                try:
                    if _spin_job[0]:
                        self.after_cancel(_spin_job[0])
                except Exception:
                    pass
                spin_lbl.config(text="✓  Done", fg=GREEN)
                for key, (lbl, color) in _fields.items():
                    val = results.get(key, "N/A")
                    if key == "public":
                        # Show masked until clicked
                        lbl.config(text="* click to unhide *", fg=RED, cursor="hand2")
                        def _reveal(e, l=lbl, v=val):
                            if not _public_revealed["v"]:
                                _public_revealed["v"] = True
                                l.config(text=v, fg=self._color_tools)
                                l.bind("<Button-1>", lambda e2, l2=l: _copy(l2, self._color_tools))
                                l.bind("<Enter>",    lambda e2, l2=l: l2.config(fg=GREEN))
                                l.bind("<Leave>",    lambda e2, l2=l: l2.config(fg=self._color_tools) if l2.cget("text") != "✓ copied" else None)
                            else:
                                _copy(l, self._color_tools)
                        lbl.bind("<Button-1>", _reveal)
                        lbl.bind("<Enter>",    lambda e, l=lbl: l.config(fg=GREEN))
                        lbl.bind("<Leave>",    lambda e, l=lbl: l.config(fg=RED))
                    else:
                        lbl.config(text=val, fg=color, cursor="hand2")
                        lbl.bind("<Button-1>", lambda e, l=lbl, f=color: _copy(l, f))
                        lbl.bind("<Enter>",    lambda e, l=lbl, f=color: l.config(fg=GREEN))
                        lbl.bind("<Leave>",    lambda e, l=lbl, f=color: l.config(fg=f) if l.cget("text") != "✓ copied" else None)
            self.after(0, _apply)

        threading.Thread(target=_fetch, daemon=True).start()

    def _speedtest(self):
        """Run a native speedtest using Ookla servers — no extra dependencies."""
        if self._speedtest_running:
            if self._speedtest_popup and self._speedtest_popup.winfo_exists():
                self._speedtest_popup.lift()
            return

        self._speedtest_cancelled = False

        try:
            self._st_btn.config(text="● OPEN", fg=SUBTEXT, cursor="arrow")
        except Exception:
            pass

        dlg = self._make_popup()
        dlg.configure(bg=PANEL)
        self._speedtest_popup = dlg
        self._raise_popup("_speedtest_popup")
        dlg.bind("<ButtonPress>", lambda e: self._raise_popup("_speedtest_popup"))
        dlg.after(30, lambda: self._pin_popup_topmost(dlg) if dlg.winfo_exists() else None)

        _spin_job = [None]

        def _close_popup():
            self._speedtest_cancelled = True
            self._speedtest_running   = False
            try:
                if _spin_job[0]:
                    self.after_cancel(_spin_job[0])
            except Exception:
                pass
            try:
                self._st_btn.config(text="▶ SPEED TEST", fg=self._color_tools, cursor="arrow")
            except Exception:
                pass
            if dlg.winfo_exists():
                dlg.destroy()

        # ── Title bar ─────────────────────────────────────────────────────────
        _make_titlebar(dlg, PANEL, BORDER, SUBTEXT, RED,
                       on_close=_close_popup,
                       title_text="▶ SPEED TEST", title_fg=self._color_tools, title_bg=PANEL,
                       separator_color=self._color_tools)

        # ── Inner content ─────────────────────────────────────────────────────
        inner = tk.Frame(dlg, bg=PANEL, padx=16, pady=12)
        inner.pack(fill="x")

        _spin_chars = ["◐", "◓", "◑", "◒"]
        _spin_state = {"idx": 0}

        # Spinner / phase label
        spin_lbl = tk.Label(inner, text="–  Ready", bg=PANEL, fg=SUBTEXT,
                            font=(_FONT, _BASE_FONT_SIZE - 1, "bold"))
        spin_lbl.pack(pady=(0, 10))

        # ── Live result rows — visible from the start, update as each phase completes ──
        _fields = {}
        for label, key, color in [
            ("Server",  "server",   TEXT),
            ("Ping",    "ping",     GREEN),
            ("↓ Down",  "download", ACCENT1),
            ("↑ Up",    "upload",   self._color_tools),
        ]:
            row = tk.Frame(inner, bg=PANEL)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=f"{label}:", bg=PANEL, fg=SUBTEXT,
                     font=(_FONT, _BASE_FONT_SIZE - 2), anchor="e", width=8).pack(side="left")
            val = tk.Label(row, text="—", bg=PANEL, fg=color,
                           font=(_FONT, _BASE_FONT_SIZE, "bold"), anchor="w")
            val.pack(side="left", padx=(4, 0))
            _fields[key] = val

        # ── Bottom bar ────────────────────────────────────────────────────────
        tk.Frame(dlg, bg=BORDER, height=1).pack(fill="x", padx=8, pady=(8, 0))
        btn_row = tk.Frame(dlg, bg=PANEL, pady=6)
        btn_row.pack(fill="x", padx=12)

        start_btn = tk.Label(btn_row, text="START", bg=BORDER, fg=GREEN,
                             font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), cursor="hand2",
                             padx=10, pady=3)
        start_btn.pack(side="left")
        start_btn.bind("<Enter>", lambda e: start_btn.config(fg=ACCENT1) if start_btn.cget("text") == "START" else None)
        start_btn.bind("<Leave>", lambda e: start_btn.config(fg=GREEN) if start_btn.cget("text") == "START" else None)

        cancel_btn = tk.Label(btn_row, text="CLOSE", bg=BORDER, fg=SUBTEXT,
                              font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), cursor="hand2",
                              padx=10, pady=3)
        cancel_btn.pack(side="right")
        cancel_btn.bind("<Button-1>", lambda e: (_close_popup(), "break"))
        cancel_btn.bind("<Enter>",    lambda e: cancel_btn.config(fg=RED))
        cancel_btn.bind("<Leave>",    lambda e: cancel_btn.config(
            fg=RED if cancel_btn.cget("text") == "CANCEL" else SUBTEXT))

        # ── Spinner tick ──────────────────────────────────────────────────────
        def _tick():
            if not dlg.winfo_exists() or not self._speedtest_running:
                return
            _spin_state["idx"] = (_spin_state["idx"] + 1) % len(_spin_chars)
            ch  = _spin_chars[_spin_state["idx"]]
            cur = spin_lbl.cget("text")
            tail = cur.split("  ", 1)[1] if "  " in cur else cur
            spin_lbl.config(text=f"{ch}  {tail}")
            _spin_job[0] = self.after(120, _tick)
        # ── Status callback — updates spinner label + live field values ───────
        def _on_status(msg):
            if not dlg.winfo_exists():
                return
            lines = [l for l in msg.strip().split("\n") if l.strip()]
            if not lines:
                return
            # First line → phase label in spinner
            spin_lbl.config(text=f"{_spin_chars[_spin_state['idx']]}  {lines[0]}")
            # Remaining lines → parse "Label: value" and update live fields
            for line in lines[1:]:
                if line.startswith("Server:"):
                    _fields["server"].config(text=line[7:].strip())
                elif line.startswith("Ping:"):
                    _fields["ping"].config(text=line[5:].strip())
                elif line.startswith("\u2193"):   # ↓
                    val = line.split(" ", 1)[1].rstrip("…") if " " in line else line
                    _fields["download"].config(text=val)
                elif line.startswith("\u2191"):   # ↑
                    val = line.split(" ", 1)[1].rstrip("…") if " " in line else line
                    _fields["upload"].config(text=val)

        # ── Result callback — finalise display ────────────────────────────────
        def _on_result(res):
            self._speedtest_running = False
            try:
                if _spin_job[0]:
                    self.after_cancel(_spin_job[0])
            except Exception:
                pass
            try:
                self._st_btn.config(text="▶ SPEED TEST", fg=self._color_tools, cursor="arrow")
            except Exception:
                pass
            if not dlg.winfo_exists():
                return
            def _reset_start_btn():
                start_btn.config(text="RE-RUN", fg=GREEN, cursor="hand2")
                start_btn.bind("<Button-1>", lambda e: _start_test())
                start_btn.bind("<Enter>", lambda e: start_btn.config(fg=ACCENT1))
                start_btn.bind("<Leave>", lambda e: start_btn.config(fg=GREEN))
            if "error" in res:
                spin_lbl.config(text="✕  Error", fg=RED)
                _fields["server"].config(text=res["error"], fg=RED)
                cancel_btn.config(text="CLOSE", fg=SUBTEXT)
                cancel_btn.bind("<Leave>", lambda e: cancel_btn.config(fg=SUBTEXT))
                _reset_start_btn()
                return
            spin_lbl.config(text="✓  Complete", fg=GREEN)
            for key, val in res.items():
                if key in _fields:
                    _fields[key].config(text=val)
            cancel_btn.config(text="CLOSE", fg=SUBTEXT)
            cancel_btn.bind("<Leave>", lambda e: cancel_btn.config(fg=SUBTEXT))
            _reset_start_btn()

        # ── Position popup: bottom = top of main app; stack below ip lookup if open ──
        self._apply_font_size(self._font_size, root=dlg)
        dlg.update_idletasks()
        dw = max(dlg.winfo_reqwidth(), 280)
        dh = dlg.winfo_reqheight()
        ax = self.winfo_rootx()
        ay = self.winfo_rooty()
        aw = self.winfo_width()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = ax + (aw - dw) // 2
        # Stack popups upward: bottom of this popup = top of main app (or top of IP lookup)
        y = ay - dh
        ip = self._iplookup_popup
        if ip and ip.winfo_exists():
            try:
                ip_geo = ip.geometry()  # "WxH+X+Y"
                ip_y = int(ip_geo.split("+")[2])
                y = ip_y - dh
            except Exception:
                y = ay - dh - 4
        x = max(10, min(x, sw - dw - 10))
        y = max(10, min(y, sh - dh - 10))
        dlg.geometry(f"{dw}x{dh}+{x}+{y}")

        # ── Start test on demand ─────────────────────────────────────────────
        def _start_test():
            if self._speedtest_running:
                return
            self._speedtest_running   = True
            self._speedtest_cancelled = False
            start_btn.config(text="RUNNING", fg=SUBTEXT, cursor="arrow")
            start_btn.unbind("<Button-1>")
            cancel_btn.config(text="CANCEL", fg=RED)
            cancel_btn.bind("<Leave>", lambda e: cancel_btn.config(
                fg=RED if cancel_btn.cget("text") == "CANCEL" else SUBTEXT))
            spin_lbl.config(text="◐  Finding server…", fg=ACCENT1)
            _spin_job[0] = self.after(120, _tick)
            threading.Thread(
                target=_st_run,
                args=(
                    lambda msg: self.after(0, lambda m=msg: _on_status(m)),
                    lambda res: self.after(0, lambda r=res: _on_result(r)),
                    lambda: self._speedtest_cancelled,
                ),
                daemon=True
            ).start()

        start_btn.bind("<Button-1>", lambda e: _start_test())

    def _section(self, parent, title, accent, section_key=None):
        # Wrapper holds hdr+body together so it can be gridded in horizontal mode
        wrapper = tk.Frame(parent, bg=BG)
        wrapper.pack(fill="x")
        if section_key:
            setattr(self, f"_section_wrapper_{section_key}", wrapper)

        hdr = tk.Frame(wrapper, bg=BG)
        hdr.pack(fill="x", pady=(8, 3))

        body = tk.Frame(wrapper, bg=PANEL, padx=8, pady=5)
        body.pack(fill="both", expand=True)

        arrow_lbl = tk.Label(hdr, text="▸", bg=BG, fg=accent,
                             font=(_FONT, _BASE_FONT_SIZE, "bold"), cursor="arrow")
        arrow_lbl.pack(side="left")
        title_lbl = tk.Label(hdr, text=" " + title.lstrip("▸").strip(), bg=BG, fg=accent,
                              font=(_FONT, _BASE_FONT_SIZE, "bold"))
        title_lbl.pack(side="left")
        divider = tk.Frame(hdr, bg=accent, height=1)
        divider.pack(side="left", fill="x", expand=True, padx=(6, 0))

        def toggle(e):
            if not self._ctrl_ok(e.state):
                return
            if body.winfo_ismapped():
                body.pack_forget()
                arrow_lbl.config(text="▶")
                if section_key:
                    self._section_collapsed[section_key] = True
            else:
                body.pack(fill="both", expand=True, after=hdr)
                arrow_lbl.config(text="▸")
                if section_key:
                    self._section_collapsed[section_key] = False
            self.update_idletasks()
            self.geometry(f"{self.winfo_width()}x{self.winfo_reqheight()}")

        def on_enter(e, a=arrow_lbl, c=accent):
            if self._ctrl_ok(e.state):
                a.config(fg=GREEN)

        def on_leave(e, a=arrow_lbl, c=accent):
            a.config(fg=c)

        arrow_lbl.bind("<Enter>", on_enter)
        arrow_lbl.bind("<Leave>", on_leave)
        arrow_lbl.bind("<Button-1>", toggle)

        hdr._arrow   = arrow_lbl
        hdr._title   = title_lbl
        hdr._divider = divider
        hdr._body    = body

        return body, title_lbl, hdr

    def _track_popup_to_parent(self, dlg, parent):
        """Keep dlg centred on parent while both exist."""
        def _centre():
            if parent and parent.winfo_exists():
                dlg.update_idletasks()
                dw, dh = dlg.winfo_width(), dlg.winfo_height()
                px, py = parent.winfo_rootx(), parent.winfo_rooty()
                pw, ph = parent.winfo_width(), parent.winfo_height()
                dlg.geometry(f"+{px + (pw - dw) // 2}+{py + (ph - dh) // 2}")
                dlg.lift()
        def _track():
            if parent and parent.winfo_exists() and dlg.winfo_exists():
                _centre()
                dlg.after(50, _track)
        dlg.after(10, lambda: (_centre(), dlg.after(50, _track)))

    def _save_theme(self, parent=None, on_save=None):
        """Prompt for a name then save to PyDisplay_theme_<name>.json."""
        dlg = self._make_popup()
        dlg.configure(bg=PANEL)

        self._track_popup_to_parent(dlg, parent)

        tk.Label(dlg, text="SAVE THEME", bg=PANEL, fg=SUBTEXT,
                 font=(_FONT, _BASE_FONT_SIZE - 3, "bold")).pack(pady=(8, 4), padx=12)

        entry = tk.Entry(dlg, bg=BORDER, fg=TEXT, insertbackground=TEXT,
                         font=(_FONT, _BASE_FONT_SIZE), relief="flat", width=20)
        entry.pack(padx=12, pady=(0, 6), ipady=3)
        # Default to last saved name, or derive from active theme filename
        if not hasattr(self, "_last_theme_name"):
            base = os.path.basename(self._active_theme_path)
            if base.startswith("PyDisplay_theme_") and base.endswith(".json"):
                self._last_theme_name = base[len("PyDisplay_theme_"):-len(".json")].replace("_", " ")
            else:
                self._last_theme_name = "my theme"
        entry.insert(0, self._last_theme_name)
        entry.select_range(0, "end")
        entry.focus_set()

        status_lbl = tk.Label(dlg, text="", bg=PANEL, fg=RED,
                              font=(_FONT, _BASE_FONT_SIZE - 3), pady=0)
        status_lbl.pack()

        btn_row = tk.Frame(dlg, bg=PANEL)
        btn_row.pack(fill="x", padx=12, pady=(2, 8))

        def do_save():
            raw  = entry.get().strip()
            safe = "".join(c for c in raw if c.isalnum() or c in " _-").strip()
            if not safe:
                status_lbl.config(text="invalid name")
                return
            fname = "PyDisplay_theme_" + safe.replace(" ", "_") + ".json"
            path  = os.path.join(_THEME_DIR, fname)
            theme = {
                "name": safe,
                "BG": BG, "PANEL": PANEL, "BORDER": BORDER,
                "TEXT": TEXT, "SUBTEXT": SUBTEXT, "DIM": DIM,
                "gpu":       self._color_gpu,
                "cpu":       self._color_cpu,
                "mem":       self._color_mem,
                "net":       self._color_net,
                "disk":      self._color_disk,
                "storage":   self._color_storage,
                "tools": self._color_tools,
                "datetime_format": self._datetime_format,
                "datetime_position": self._datetime_position,
                "opacity":         round(float(self.wm_attributes("-alpha")), 2),
                "layout_mode":     self._layout_mode,
            }
            try:
                with open(path, "w") as f:
                    json.dump(theme, f, indent=4)
                self._last_theme_name = safe
                self._active_theme_path = path
                self._save_position()
                # Clear entry/buttons and show success state
                entry.pack_forget()
                status_lbl.config(text=f"✔  {fname} saved", fg=GREEN)
                btn_row.pack_forget()
                def _open_location(e, p=path):
                    subprocess.Popen(f'explorer /select,"{p}"')
                def _confirm():
                    if on_save:
                        on_save()
                    dlg.destroy()
                ok_btn = tk.Label(dlg, text="OK", bg=BORDER, fg=GREEN,
                                  font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), cursor="hand2", padx=24, pady=2)
                ok_btn.pack(pady=(4, 2))
                ok_btn.bind("<Button-1>", lambda e: _confirm())
                ok_btn.bind("<Enter>", lambda e: ok_btn.config(fg=TEXT))
                ok_btn.bind("<Leave>", lambda e: ok_btn.config(fg=GREEN))
                loc_btn = tk.Label(dlg, text="📂  Save Location", bg=BORDER, fg="#c0c0c0",
                                   font=(_FONT, _BASE_FONT_SIZE - 2), cursor="hand2", padx=10, pady=4)
                loc_btn.pack(pady=(0, 8))
                loc_btn.bind("<Button-1>", _open_location)
                loc_btn.bind("<Enter>", lambda e: loc_btn.config(fg=TEXT))
                loc_btn.bind("<Leave>", lambda e: loc_btn.config(fg="#c0c0c0"))
                dlg.bind("<Return>", lambda e: _confirm())
            except Exception as e:
                status_lbl.config(text=str(e), fg=RED)

        save_btn = tk.Label(btn_row, text="SAVE", bg=BORDER, fg=GREEN,
                            font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), cursor="hand2",
                            padx=10, pady=3)
        save_btn.pack(side="left")
        save_btn.bind("<Enter>", lambda e: save_btn.config(fg=ACCENT1))
        save_btn.bind("<Leave>", lambda e: save_btn.config(fg=GREEN))
        save_btn.bind("<Button-1>", lambda e: do_save())
        entry.bind("<Return>", lambda e: do_save())

        cancel_btn = tk.Label(btn_row, text="CANCEL", bg=BORDER, fg=SUBTEXT,
                              font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), cursor="hand2",
                              padx=10, pady=3)
        cancel_btn.pack(side="right")
        cancel_btn.bind("<Enter>", lambda e: cancel_btn.config(fg=RED))
        cancel_btn.bind("<Leave>", lambda e: cancel_btn.config(fg=SUBTEXT))
        cancel_btn.bind("<Button-1>", lambda e: dlg.destroy())

        self._apply_font_size(self._font_size, root=dlg)

        dlg.update_idletasks()
        dx, dy = self._popup_pos(dlg.winfo_reqwidth(), dlg.winfo_reqheight())
        dlg.geometry(f"+{dx}+{dy}")

    def _load_theme(self, parent=None):
        """Scan for saved theme files and show a picker."""
        theme_dir = _THEME_DIR
        files = sorted(glob.glob(os.path.join(theme_dir, "PyDisplay_theme_*.json")))
        if not files:
            self._show_toast("No saved themes found")
            return

        dlg = self._make_popup()
        dlg.configure(bg=PANEL)

        def _centre_on_parent():
            if parent and parent.winfo_exists():
                dlg.update_idletasks()
                dw, dh = dlg.winfo_width(), dlg.winfo_height()
                px, py = parent.winfo_rootx(), parent.winfo_rooty()
                pw, ph = parent.winfo_width(), parent.winfo_height()
                dlg.geometry(f"+{px + (pw - dw) // 2}+{py + (ph - dh) // 2}")
                dlg.lift()
        self._track_popup_to_parent(dlg, parent)

        # ── Header ────────────────────────────────────────────────────────────
        hdr = tk.Frame(dlg, bg=BORDER, padx=12, pady=6)
        hdr.pack(fill="x")
        tk.Label(hdr, text="LOAD THEME", bg=BORDER, fg=TEXT,
                 font=(_FONT, _BASE_FONT_SIZE - 2, "bold")).pack(side="left")
        close_btn = tk.Label(hdr, text="✕", bg=BORDER, fg=SUBTEXT,
                             font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), cursor="hand2")
        close_btn.pack(side="right")
        close_btn.bind("<Enter>",    lambda e: close_btn.config(fg=RED))
        close_btn.bind("<Leave>",    lambda e: close_btn.config(fg=SUBTEXT))
        close_btn.bind("<Button-1>", lambda e: dlg.destroy())

        tk.Frame(dlg, bg=DIM, height=1).pack(fill="x")

        # ── Theme list ────────────────────────────────────────────────────────
        list_frame = tk.Frame(dlg, bg=PANEL)
        list_frame.pack(fill="both", padx=0, pady=4)

        def apply_file(path):
            try:
                with open(path) as f:
                    t = json.load(f)
                t = self._validate_theme(t)
                self._apply_theme(
                    t["BG"], t["PANEL"], t["BORDER"],
                    t["gpu"], t["cpu"], t["mem"], t["net"], t["disk"], t["storage"],
                    t["TEXT"], t["SUBTEXT"], t["DIM"], t["tools"],
                )
                self._apply_font_size(t["font_size"])
                self._datetime_format   = t["datetime_format"]
                self._datetime_position = t["datetime_position"]
                self._layout_mode = t["layout_mode"]
                self.after(80, self._apply_layout_mode)
                self._active_theme_path = path
                self._save_position()
                theme_name = t.get("name", os.path.basename(path))
                dlg.destroy()
                self._show_toast(f"*{theme_name} loaded*")
            except Exception:
                pass

        def delete_file(path, row_frame, rebuild, theme_name):
            # Confirmation dialog
            conf = self._make_popup()
            conf.configure(bg=PANEL)

            tk.Frame(conf, bg=BORDER, height=1).pack(fill="x")
            tk.Label(conf, text="DELETE THEME", bg=PANEL, fg=RED,
                     font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), pady=8).pack()
            tk.Label(conf, text=f"\"{theme_name}\"", bg=PANEL, fg=TEXT,
                     font=(_FONT, _BASE_FONT_SIZE - 2), pady=0).pack()
            tk.Label(conf, text="This cannot be undone.", bg=PANEL, fg=SUBTEXT,
                     font=(_FONT, _BASE_FONT_SIZE - 3), pady=4).pack()
            tk.Frame(conf, bg=BORDER, height=1).pack(fill="x", padx=8)

            btn_row = tk.Frame(conf, bg=PANEL, pady=8)
            btn_row.pack()

            def confirm_delete():
                try:
                    os.remove(path)
                except Exception:
                    pass
                conf.destroy()
                rebuild()

            del_btn = tk.Label(btn_row, text="DELETE", bg=RED, fg=BG,
                               font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), cursor="hand2",
                               padx=10, pady=3)
            del_btn.pack(side="left", padx=(0, 6))
            del_btn.bind("<Enter>", lambda e: del_btn.config(bg=TEXT))
            del_btn.bind("<Leave>", lambda e: del_btn.config(bg=RED))
            del_btn.bind("<Button-1>", lambda e: confirm_delete())

            can_btn = tk.Label(btn_row, text="CANCEL", bg=BORDER, fg=SUBTEXT,
                               font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), cursor="hand2",
                               padx=10, pady=3)
            can_btn.pack(side="left")
            can_btn.bind("<Enter>", lambda e: can_btn.config(fg=TEXT))
            can_btn.bind("<Leave>", lambda e: can_btn.config(fg=SUBTEXT))
            can_btn.bind("<Button-1>", lambda e: conf.destroy())

            self._apply_font_size(self._font_size, root=conf)
            conf.update_idletasks()
            # Centre over the load dialog (dlg)
            if dlg.winfo_exists():
                cw, ch = conf.winfo_width(), conf.winfo_height()
                dx, dy = dlg.winfo_rootx(), dlg.winfo_rooty()
                dw, dh = dlg.winfo_width(), dlg.winfo_height()
                conf.geometry(f"+{dx + (dw - cw) // 2}+{dy + (dh - ch) // 2}")
            self._pin_popup_topmost(conf)

        def build_list():
            for w in list_frame.winfo_children():
                w.destroy()
            current_files = sorted(glob.glob(os.path.join(theme_dir, "PyDisplay_theme_*.json")))
            if not current_files:
                tk.Label(list_frame, text="No themes saved", bg=PANEL, fg=SUBTEXT,
                         font=(_FONT, _BASE_FONT_SIZE - 2), padx=12, pady=8).pack()
                return
            for path in current_files:
                try:
                    with open(path) as f:
                        t = json.load(f)
                    label = t.get("name", os.path.basename(path))
                    swatch_colors = [t.get("BG","#111"), t.get("PANEL","#222"),
                                     t.get("gpu","#0f0"), t.get("cpu","#f00"),
                                     t.get("mem","#0ff")]
                except Exception:
                    label = os.path.basename(path)
                    swatch_colors = ["#111","#222","#0f0","#f00","#0ff"]

                is_active = (path == self._active_theme_path)

                row = tk.Frame(list_frame, bg=BORDER if is_active else PANEL, cursor="hand2")
                row.pack(fill="x", padx=6, pady=2)

                # Colour swatches
                swatch_frame = tk.Frame(row, bg=row["bg"])
                swatch_frame.pack(side="left", padx=(8, 4), pady=6)
                for col in swatch_colors:
                    tk.Frame(swatch_frame, bg=col, width=6, height=14).pack(side="left", padx=1)

                # Theme name
                name_lbl = tk.Label(row, text=label.upper(), bg=row["bg"],
                                    fg=GREEN if is_active else TEXT,
                                    font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), anchor="w")
                name_lbl.pack(side="left", fill="x", expand=True, pady=6)

                # Active indicator
                if is_active:
                    tk.Label(row, text="✔", bg=row["bg"], fg=GREEN,
                             font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), padx=4).pack(side="left")

                # Delete button
                del_btn = tk.Label(row, text="✕", bg=row["bg"], fg=SUBTEXT,
                                   font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), cursor="hand2",
                                   padx=8, pady=6)
                del_btn.pack(side="right")

                def _on_del_enter(e, b=del_btn, r=row): b.config(fg=RED)
                def _on_del_leave(e, b=del_btn, r=row): b.config(fg=SUBTEXT)
                del_btn.bind("<Enter>", _on_del_enter)
                del_btn.bind("<Leave>", _on_del_leave)
                del_btn.bind("<Button-1>", lambda e, p=path, r=row, n=label: delete_file(p, r, build_list, n))

                # Row hover — only on non-delete areas
                def _on_row_enter(e, r=row, n=name_lbl, a=is_active):
                    r.config(bg=DIM)
                    n.config(bg=DIM)
                    for c in r.winfo_children():
                        try: c.config(bg=DIM)
                        except Exception: pass
                def _on_row_leave(e, r=row, n=name_lbl, a=is_active):
                    bg = BORDER if a else PANEL
                    r.config(bg=bg)
                    n.config(bg=bg)
                    for c in r.winfo_children():
                        try: c.config(bg=bg)
                        except Exception: pass

                row.bind("<Enter>", _on_row_enter)
                row.bind("<Leave>", _on_row_leave)
                name_lbl.bind("<Enter>", _on_row_enter)
                name_lbl.bind("<Leave>", _on_row_leave)
                name_lbl.bind("<Button-1>", lambda e, p=path: apply_file(p))
                row.bind("<Button-1>", lambda e, p=path: apply_file(p))

        build_list()

        tk.Frame(dlg, bg=DIM, height=1).pack(fill="x", pady=(2, 0))

        self._apply_font_size(self._font_size, root=dlg)
        dlg.update_idletasks()
        _centre_on_parent()

    def _open_color_picker(self):
        if self._color_popup and self._color_popup.winfo_exists():
            self._color_popup.destroy()
            self._color_popup = None
            return
        if self._color_pending:
            return
        self._color_pending = True
        popup = self._make_popup()
        popup.configure(bg=PANEL)
        # registered in _color_popup AFTER geometry is locked (see _finalize_color below)

        # Track unsaved changes
        _unsaved = {"changed": False}
        def _mark_unsaved():
            _unsaved["changed"] = True
        def _mark_saved():
            _unsaved["changed"] = False

        def _try_close():
            if _unsaved["changed"]:
                conf = self._make_popup()
                conf.configure(bg=BORDER)
                inner = tk.Frame(conf, bg=PANEL, padx=16, pady=12)
                inner.pack(padx=1, pady=1)
                tk.Label(inner, text="Unsaved Changes *", bg=PANEL, fg=RED,
                         font=(_FONT, _BASE_FONT_SIZE, "bold")).pack(pady=(0, 6))
                btn_row = tk.Frame(inner, bg=PANEL)
                btn_row.pack()
                save_btn = tk.Label(btn_row, text="Save", bg=GREEN, fg=BG,
                                    font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), cursor="hand2",
                                    padx=10, pady=3)
                save_btn.pack(side="left", padx=(0, 6))
                save_btn.bind("<Enter>", lambda e: save_btn.config(bg=TEXT))
                save_btn.bind("<Leave>", lambda e: save_btn.config(bg=GREEN))
                save_btn.bind("<Button-1>", lambda e: (conf.destroy(), self._save_theme(parent=popup, on_save=lambda: (_mark_saved(), popup.destroy()))))
                close_btn2 = tk.Label(btn_row, text="Close anyway", bg=BORDER, fg=SUBTEXT,
                                      font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), cursor="hand2",
                                      padx=10, pady=3)
                close_btn2.pack(side="left")
                close_btn2.bind("<Enter>", lambda e: close_btn2.config(fg=RED))
                close_btn2.bind("<Leave>", lambda e: close_btn2.config(fg=SUBTEXT))
                close_btn2.bind("<Button-1>", lambda e: (conf.destroy(), popup.destroy()))
                self._apply_font_size(self._font_size, root=conf)
                conf.update_idletasks()
                cw, ch = conf.winfo_width(), conf.winfo_height()
                px, py = popup.winfo_rootx(), popup.winfo_rooty()
                pw, ph = popup.winfo_width(), popup.winfo_height()
                conf.geometry(f"+{px + (pw - cw) // 2}+{py + (ph - ch) // 2}")
                self._pin_popup_topmost(conf)
            else:
                popup.destroy()

        # ── Title bar ─────────────────────────────────────────────────────────
        _make_titlebar(popup, PANEL, BORDER, SUBTEXT, RED,
                       on_close=_try_close,
                       title_text="◈ THEME", title_fg=ACCENT3, title_bg=PANEL,
                       separator_color=ACCENT3)
        tk.Frame(popup, bg=PANEL, height=6).pack(fill="x")  # spacing below title bar


        # ── Presets ───────────────────────────────────────────────────────────
        preset_row = tk.Frame(popup, bg=PANEL, padx=12, pady=6)
        preset_row.pack(fill="x")
        tk.Label(preset_row, text="PRESETS", bg=PANEL, fg=TEXT,
                 font=(_FONT, _BASE_FONT_SIZE - 2, "bold")).pack(side="left")
        # Row 1: Dark / Light
        for label, cmd in [("Dark", self._theme_dark), ("Light", self._theme_light)]:
            b = tk.Label(preset_row, text=label, bg=BORDER, fg=SUBTEXT,
                         font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), cursor="hand2", padx=8, pady=2,
                         width=5, anchor="center")
            b.pack(side="right", padx=(4, 0))
            b.bind("<Enter>",    lambda e, w=b: w.config(bg=ACCENT3, fg=BG))
            b.bind("<Leave>",    lambda e, w=b: w.config(bg=BORDER, fg=SUBTEXT))
            b.bind("<Button-1>", lambda e, c=cmd: (_mark_unsaved(), c(), _refresh_swatches()))

        # Row 2: extra presets
        preset_row2 = tk.Frame(popup, bg=PANEL, padx=12, pady=2)
        preset_row2.pack(fill="x")
        tk.Label(preset_row2, text="", bg=PANEL,
                 font=(_FONT, _BASE_FONT_SIZE - 2, "bold")).pack(side="left")
        for label, cmd in [
            ("Terminal", self._theme_terminal),
            ("Ice",      self._theme_ice),
            ("Sunset",   self._theme_sunset),
            ("Midnight", self._theme_midnight),
        ]:
            b = tk.Label(preset_row2, text=label, bg=BORDER, fg=SUBTEXT,
                         font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), cursor="hand2", padx=6, pady=2,
                         width=7, anchor="center")
            b.pack(side="right", padx=(4, 0))
            b.bind("<Enter>",    lambda e, w=b: w.config(bg=ACCENT3, fg=BG))
            b.bind("<Leave>",    lambda e, w=b: w.config(bg=BORDER, fg=SUBTEXT))
            b.bind("<Button-1>", lambda e, c=cmd: (_mark_unsaved(), c(), _refresh_swatches()))

        tk.Frame(popup, bg=BORDER, height=1).pack(fill="x", padx=12, pady=(0, 2))

        # ── Layout mode ───────────────────────────────────────────────────────
        layout_row = tk.Frame(popup, bg=PANEL, padx=12, pady=6)
        layout_row.pack(fill="x")
        tk.Label(layout_row, text="LAYOUT", bg=PANEL, fg=TEXT,
                 font=(_FONT, _BASE_FONT_SIZE - 2, "bold")).pack(side="left")
        _layout_state = {"mode": self._layout_mode}
        layout_lbl = tk.Label(layout_row,
                              text="— HORIZONTAL" if _layout_state["mode"] == "horizontal" else "| VERTICAL",
                              bg=BORDER,
                              fg=SUBTEXT,
                              font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), cursor="hand2", padx=8, pady=2)
        layout_lbl.pack(side="right")
        def _toggle_layout(e=None):
            _layout_state["mode"] = "horizontal" if _layout_state["mode"] == "vertical" else "vertical"
            self._layout_mode = _layout_state["mode"]
            layout_lbl.config(
                text="— HORIZONTAL" if _layout_state["mode"] == "horizontal" else "| VERTICAL",
                fg=SUBTEXT,
            )
            _mark_unsaved()
            self._apply_layout_mode()
            self._save_position()
        layout_lbl.bind("<Button-1>", _toggle_layout)
        layout_lbl.bind("<Enter>", lambda e: layout_lbl.config(fg=GREEN))
        layout_lbl.bind("<Leave>", lambda e: layout_lbl.config(
            fg=SUBTEXT))

        tk.Frame(popup, bg=BORDER, height=1).pack(fill="x", padx=12, pady=(0, 2))

        # ── Save / Load ───────────────────────────────────────────────────────
        save_row = tk.Frame(popup, bg=PANEL, padx=12, pady=6)
        save_row.pack(fill="x")
        tk.Label(save_row, text="THEMES", bg=PANEL, fg=TEXT,
                 font=(_FONT, _BASE_FONT_SIZE - 2, "bold")).pack(side="left")
        for label, cmd in [("Load", self._load_theme), ("Save", self._save_theme)]:
            b = tk.Label(save_row, text=label, bg=BORDER, fg=GREEN,
                         font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), cursor="hand2", padx=8, pady=2,
                         width=5, anchor="center")
            b.pack(side="right", padx=(4, 0))
            b.bind("<Enter>",    lambda e, w=b: w.config(bg=GREEN, fg=BG))
            b.bind("<Leave>",    lambda e, w=b: w.config(bg=BORDER, fg=GREEN))
            b.bind("<Button-1>", lambda e, c=cmd, p=popup, lbl=label: (_mark_saved() if lbl=="Save" else None, c(parent=p)))

        tk.Frame(popup, bg=BORDER, height=1).pack(fill="x", padx=12, pady=(0, 2))

        # ── Recolor buttons ───────────────────────────────────────────────────
        tk.Label(popup, text="RECOLOR", bg=PANEL, fg=SUBTEXT,
                 font=(_FONT, _BASE_FONT_SIZE - 3, "bold"), padx=12).pack(anchor="w", pady=(6, 2))

        _swatch_refs = {}  # label -> swatch widget for live updates

        def _refresh_swatches():
            live = {
                "GPU": self._color_gpu, "CPU": self._color_cpu,
                "MEMORY": self._color_mem, "NETWORK": self._color_net,
                "DISK": self._color_disk, "STORAGE": self._color_storage,
                "TOOLS": self._color_tools,
                "BACKGROUND": BG,
            }
            for lbl, sw in _swatch_refs.items():
                c = live.get(lbl, "")
                if c and sw.winfo_exists():
                    sw.config(fg=c)
                    sw.bind("<Enter>", lambda e, w=sw, col=c: w.config(bg=col, fg=BG))
                    sw.bind("<Leave>", lambda e, w=sw, col=c: w.config(bg=BORDER, fg=col))

        # Single source of truth for each recolor row: (default_color, attr_key, recolor_cmd, current_color)
        _recolor_data = {
            "GPU":        ("#008000", "_color_gpu",     self._recolor_gpu,     self._color_gpu),
            "CPU":        ("#800000", "_color_cpu",     self._recolor_cpu,     self._color_cpu),
            "MEMORY":     ("#008080", "_color_mem",     self._recolor_mem,     self._color_mem),
            "NETWORK":    ("#7b30d1", "_color_net",     self._recolor_net,     self._color_net),
            "DISK":       ("#8c4600", "_color_disk",    self._recolor_disk,    self._color_disk),
            "STORAGE":    ("#c0c0c0", "_color_storage", self._recolor_storage, self._color_storage),
            "TOOLS":      ("#8080ff", "_color_tools",   self._recolor_tools,   self._color_tools),
            "BACKGROUND": ("#0a0a0f", None,             self._recolor_bg,      BG),
        }
        _default_colors  = {k: v[0] for k, v in _recolor_data.items()}
        _recolor_keys    = {k: v[1] for k, v in _recolor_data.items() if v[1]}
        _recolor_cmds    = {k: v[2] for k, v in _recolor_data.items()}
        _current_colors  = {k: v[3] for k, v in _recolor_data.items()}

        def _make_recolor_row(label):
            cmd   = _recolor_cmds[label]
            color = _current_colors[label]
            row   = tk.Frame(popup, bg=PANEL, padx=12, pady=2)
            row.pack(fill="x")
            tk.Label(row, text=label, bg=PANEL, fg=TEXT,
                     font=(_FONT, _BASE_FONT_SIZE - 2, "bold")).pack(side="left")

            # Reset to default button
            def _do_reset(lbl=label):
                def_col = _default_colors[lbl]
                key     = _recolor_keys.get(lbl)
                if key:
                    # apply via _recolor_section logic
                    if key == "_color_storage":
                        self._color_storage = def_col
                        try:
                            self._storage_hdr._arrow.config(fg=def_col)
                            self._storage_hdr._title.config(fg=def_col)
                            self._storage_hdr._divider.config(bg=def_col)
                        except Exception: pass
                        for b in self._storage_bars:
                            try: b.accent = def_col
                            except Exception: pass
                    elif key == "_color_tools":
                        self._color_tools = def_col
                        try: self._st_btn.config(fg=def_col)
                        except Exception: pass
                        try: self._ip_btn.config(fg=def_col)
                        except Exception: pass
                        try: self._mem_clean_btn.config(fg=def_col)
                        except Exception: pass
                    else:
                        setattr(self, key, def_col)
                        section_map = {
                            "_color_gpu":  (self._gpu_hdr,  [self.gpu_usage, self.vram_bar],
                                            [self.gpu_watt_lbl, self.gpu_temp_lbl,
                                             self.gpu_temp_max_lbl, self.gpu_temp_min_lbl]),
                            "_color_cpu":  (self._cpu_hdr,  [self.cpu_usage],
                                            [self.cpu_freq_max_lbl, self.cpu_freq_min_lbl,
                                             self.cpu_proc_lbl, self.cpu_thread_lbl, self.cpu_handle_lbl]),
                            "_color_mem":  (self._mem_hdr,  [self.ram_bar],
                                            [self.ram_used_lbl, self.ram_total_lbl,
                                             self.ram_pct_lbl, self.ram_peak_lbl]),
                            "_color_net":  (self._net_hdr,  [self.net_down, self.net_up],
                                            [self.net_peak_down_lbl, self.net_peak_up_lbl]),
                            "_color_disk": (self._disk_hdr, [self.disk_read, self.disk_write],
                                            [self.disk_peak_read_lbl, self.disk_peak_write_lbl]),
                        }
                        if key in section_map:
                            hdr, bars, pills = section_map[key]
                            try:
                                hdr._arrow.config(fg=def_col)
                                hdr._title.config(fg=def_col)
                                hdr._divider.config(bg=def_col)
                            except Exception: pass
                            for b in bars:
                                try: b.accent = def_col
                                except Exception: pass
                            for p in pills:
                                try: p.config(fg=def_col)
                                except Exception: pass
                else:
                    # Background reset
                    global BG
                    old_bg = BG
                    BG = def_col
                    self.configure(bg=def_col)
                    def _r(w):
                        try:
                            if w.cget("bg") == old_bg: w.config(bg=def_col)
                        except Exception: pass
                        for c in w.winfo_children(): _r(c)
                    _r(self)
                self._save_position()
                _refresh_swatches()
                rst_btn.config(fg=GREEN)
                self.after(800, lambda b=rst_btn: b.config(fg=SUBTEXT) if b.winfo_exists() else None)

            rst_btn = tk.Label(row, text="↺", bg=PANEL, fg=SUBTEXT,
                               font=(_FONT, _BASE_FONT_SIZE, "bold"), cursor="hand2", padx=2)
            rst_btn.pack(side="right", padx=(0, 2))
            rst_btn.bind("<Enter>",    lambda e, b=rst_btn: b.config(fg=ACCENT1))
            rst_btn.bind("<Leave>",    lambda e, b=rst_btn: b.config(fg=SUBTEXT))
            rst_btn.bind("<Button-1>", lambda e, fn=_do_reset: fn())
            self._bind_tooltip(rst_btn,
                f"## ↺  RESET {label}\n"
                "Restore this colour slot to its built-in default.\n"
                "Does not affect other colour slots.")

            swatch = tk.Label(row, text="  ■  ", bg=BORDER, fg=color,
                              font=(_FONT, _BASE_FONT_SIZE + 5, "bold"), cursor="hand2",
                              padx=4, pady=1)
            swatch.pack(side="right")
            swatch.bind("<Enter>",    lambda e, w=swatch, c=color: w.config(bg=c, fg=BG))
            swatch.bind("<Leave>",    lambda e, w=swatch, c=color: w.config(bg=BORDER, fg=c))
            swatch.bind("<Button-1>", lambda e, c=cmd: (_mark_unsaved(), c(), _refresh_swatches()))
            self._bind_tooltip(swatch,
                f"## {label} COLOUR\n"
                "Click to open a colour picker and change\n"
                f"the {label.lower()} section accent colour.\n"
                "Live preview updates as you drag the picker.")
            _swatch_refs[label] = swatch

        for label in ["GPU", "CPU", "MEMORY", "NETWORK", "DISK",
                      "STORAGE", "TOOLS", "BACKGROUND"]:
            _make_recolor_row(label)

        tk.Frame(popup, bg=BORDER, height=1).pack(fill="x", padx=12, pady=(4, 2))

        # ── Font size ─────────────────────────────────────────────────────────
        font_row = tk.Frame(popup, bg=PANEL, padx=12, pady=6)
        font_row.pack(fill="x")
        tk.Label(font_row, text="FONT SIZE", bg=PANEL, fg=TEXT,
                 font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), width=11, anchor="w").pack(side="left")
        font_entry = tk.Entry(font_row, bg=BORDER, fg=TEXT, insertbackground=TEXT,
                              font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), relief="flat",
                              width=3, justify="center")
        font_entry.insert(0, str(self._font_size))
        def _apply_fs(e=None):
            try:
                size = int(font_entry.get())
                self._apply_font_size(size)
                # Remap popup fonts from their originals
                _apply_font_to_popup(size)
                # Dynamically resize the popup to fit the new font
                popup.update_idletasks()
                new_h = popup.winfo_reqheight()
                _popup_locked_size[1] = new_h
                popup.geometry(f"{_popup_locked_size[0]}x{new_h}")
                # Only mark unsaved if font differs from the default (9)
                if size != _BASE_FONT_SIZE:
                    _mark_unsaved()
                else:
                    self._save_position()
            except ValueError:
                pass
        def _fs_up(e=None):
            try:
                size = min(20, int(font_entry.get()) + 1)
                font_entry.delete(0, "end")
                font_entry.insert(0, str(size))
            except ValueError:
                pass
        def _fs_down(e=None):
            try:
                size = max(6, int(font_entry.get()) - 1)
                font_entry.delete(0, "end")
                font_entry.insert(0, str(size))
            except ValueError:
                pass
        # Up/down arrows for font size (left of entry)
        _fs_arrow_frame = tk.Frame(font_row, bg=PANEL)
        _fs_arrow_frame.pack(side="left", padx=(4, 0))
        _fs_up_btn = tk.Label(_fs_arrow_frame, text="▲", bg=PANEL, fg=SUBTEXT,
                              font=(_FONT, _BASE_FONT_SIZE - 3), cursor="hand2", padx=2)
        _fs_up_btn.pack(side="top")
        _fs_up_btn.bind("<Button-1>", _fs_up)
        _fs_up_btn.bind("<Enter>",    lambda e: _fs_up_btn.config(fg=ACCENT1))
        _fs_up_btn.bind("<Leave>",    lambda e: _fs_up_btn.config(fg=SUBTEXT))
        _fs_dn_btn = tk.Label(_fs_arrow_frame, text="▼", bg=PANEL, fg=SUBTEXT,
                              font=(_FONT, _BASE_FONT_SIZE - 3), cursor="hand2", padx=2)
        _fs_dn_btn.pack(side="top")
        _fs_dn_btn.bind("<Button-1>", _fs_down)
        _fs_dn_btn.bind("<Enter>",    lambda e: _fs_dn_btn.config(fg=ACCENT1))
        _fs_dn_btn.bind("<Leave>",    lambda e: _fs_dn_btn.config(fg=SUBTEXT))
        font_entry.pack(side="left", padx=(4, 0), ipady=2)
        font_apply = tk.Label(font_row, text="APPLY", bg=BORDER, fg=GREEN,
                              font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), cursor="hand2",
                              padx=6, pady=2)
        font_apply.pack(side="left", padx=(4, 0))
        font_apply.bind("<Button-1>", _apply_fs)
        font_apply.bind("<Enter>", lambda e: font_apply.config(bg=GREEN, fg=BG))
        font_apply.bind("<Leave>", lambda e: font_apply.config(bg=BORDER, fg=GREEN))
        font_entry.bind("<Return>", _apply_fs)
        self._bind_tooltip(font_apply,
            "## FONT SIZE  APPLY\n"
            "Apply the entered font size to all overlay text.\n"
            "Valid range: 6 – 20. Press Enter or click APPLY.\n"
            "Use ▲ ▼ arrows to step up/down by 1.")

        tk.Frame(popup, bg=BORDER, height=1).pack(fill="x", padx=12, pady=(0, 2))

        # ── Opacity ───────────────────────────────────────────────────────────
        _op_row = tk.Frame(popup, bg=PANEL, padx=12, pady=6)
        _op_row.pack(fill="x")
        tk.Label(_op_row, text="OPACITY", bg=PANEL, fg=TEXT,
                 font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), width=11, anchor="w").pack(side="left")
        _cur_op_pct = int(round(float(self.wm_attributes("-alpha")) * 100))
        op_entry = tk.Entry(_op_row, bg=BORDER, fg=TEXT, insertbackground=TEXT,
                            font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), relief="flat",
                            width=4, justify="center")
        op_entry.insert(0, str(_cur_op_pct))
        def _apply_op(e=None):
            try:
                pct = int(op_entry.get().strip().rstrip("%"))
                pct = max(10, min(100, pct))
                op_entry.delete(0, "end")
                op_entry.insert(0, str(pct))
                self.wm_attributes("-alpha", pct / 100.0)
                _mark_unsaved()
            except ValueError:
                pass
        def _op_up(e=None):
            try:
                pct = int(op_entry.get().strip().rstrip("%"))
                pct = min(100, pct + 5)
                op_entry.delete(0, "end")
                op_entry.insert(0, str(pct))
            except ValueError:
                pass
        def _op_down(e=None):
            try:
                pct = int(op_entry.get().strip().rstrip("%"))
                pct = max(10, pct - 5)
                op_entry.delete(0, "end")
                op_entry.insert(0, str(pct))
            except ValueError:
                pass
        # Up/down arrows for opacity (left of entry, 5% increments)
        _op_arrow_frame = tk.Frame(_op_row, bg=PANEL)
        _op_arrow_frame.pack(side="left", padx=(4, 0))
        _op_up_btn = tk.Label(_op_arrow_frame, text="▲", bg=PANEL, fg=SUBTEXT,
                              font=(_FONT, _BASE_FONT_SIZE - 3), cursor="hand2", padx=2)
        _op_up_btn.pack(side="top")
        _op_up_btn.bind("<Button-1>", _op_up)
        _op_up_btn.bind("<Enter>",    lambda e: _op_up_btn.config(fg=ACCENT1))
        _op_up_btn.bind("<Leave>",    lambda e: _op_up_btn.config(fg=SUBTEXT))
        _op_dn_btn = tk.Label(_op_arrow_frame, text="▼", bg=PANEL, fg=SUBTEXT,
                              font=(_FONT, _BASE_FONT_SIZE - 3), cursor="hand2", padx=2)
        _op_dn_btn.pack(side="top")
        _op_dn_btn.bind("<Button-1>", _op_down)
        _op_dn_btn.bind("<Enter>",    lambda e: _op_dn_btn.config(fg=ACCENT1))
        _op_dn_btn.bind("<Leave>",    lambda e: _op_dn_btn.config(fg=SUBTEXT))
        op_entry.pack(side="left", padx=(4, 0), ipady=2)
        tk.Label(_op_row, text="%", bg=PANEL, fg=SUBTEXT,
                 font=(_FONT, _BASE_FONT_SIZE - 2, "bold")).pack(side="left", padx=(2, 0))
        op_apply = tk.Label(_op_row, text="APPLY", bg=BORDER, fg=GREEN,
                            font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), cursor="hand2",
                            padx=6, pady=2)
        op_apply.pack(side="left", padx=(4, 0))
        op_apply.bind("<Button-1>", _apply_op)
        op_apply.bind("<Enter>", lambda e: op_apply.config(bg=GREEN, fg=BG))
        op_apply.bind("<Leave>", lambda e: op_apply.config(bg=BORDER, fg=GREEN))
        op_entry.bind("<Return>", _apply_op)
        self._bind_tooltip(op_apply,
            "## OPACITY  APPLY\n"
            "Set the overlay window transparency (10–100%).\n"
            "100% = fully opaque. 50% = semi-transparent.\n"
            "Use ▲ ▼ arrows to step in 5% increments.")

        tk.Frame(popup, bg=BORDER, height=1).pack(fill="x", padx=12, pady=(0, 2))

        # ── Info display (collapsible) ────────────────────────────────────────
        _info_pos_state  = {"val": self._datetime_position}
        _info_fmt_state  = {"val": self._datetime_format}
        _info_expanded   = {"open": False}

        info_row = tk.Frame(popup, bg=PANEL, padx=12, pady=6)
        info_row.pack(fill="x")

        info_arrow = tk.Label(info_row, text="\u25bc" if _info_expanded["open"] else "\u25b6",
                              bg=PANEL, fg=SUBTEXT,
                              font=(_FONT, _BASE_FONT_SIZE - 2), cursor="hand2", padx=2)
        info_arrow.pack(side="left")

        tk.Label(info_row, text="INFO DISPLAY", bg=PANEL, fg=TEXT,
                 font=(_FONT, _BASE_FONT_SIZE - 2, "bold")).pack(side="left", padx=(4, 0))

        # Current value summary shown on the right
        def _info_summary():
            pos = _info_pos_state["val"]
            fmt = _info_fmt_state["val"]
            if not fmt:
                return f"{pos.upper()}  ·  OFF"
            return f"{pos.upper()}  ·  {fmt.upper()}"

        info_val_lbl = tk.Label(info_row, text=_info_summary(),
                                bg=PANEL, fg=GREEN if _info_fmt_state["val"] else SUBTEXT,
                                font=(_FONT, _BASE_FONT_SIZE - 2, "bold"))
        info_val_lbl.pack(side="right", padx=(0, 4))

        # Sub-panel
        info_sub = tk.Frame(popup, bg=BORDER)

        # Position row inside sub
        pos_sub_row = tk.Frame(info_sub, bg=BORDER, padx=24, pady=3)
        pos_sub_row.pack(fill="x")
        tk.Label(pos_sub_row, text="POSITION", bg=BORDER, fg=SUBTEXT,
                 font=(_FONT, _BASE_FONT_SIZE - 2)).pack(side="left", padx=(0, 6))

        def _make_pos_btn(label, val):
            active = (_info_pos_state["val"] == val)
            b = tk.Label(pos_sub_row, text=label, bg=BG,
                         fg=GREEN if active else TEXT,
                         font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), cursor="hand2", padx=6, pady=2)
            b.pack(side="left", padx=(0, 4))
            def _click(e, v=val, btn=b):
                self._datetime_position = v
                _info_pos_state["val"] = v
                for child in pos_sub_row.winfo_children():
                    if getattr(child, "_pos_btn", False):
                        child.config(fg=TEXT)
                btn.config(fg=GREEN)
                info_val_lbl.config(text=_info_summary(),
                                    fg=GREEN if _info_fmt_state["val"] else SUBTEXT)
                self._save_position()
                _mark_unsaved()
                self._update_datetime_lbl()
            b.bind("<Button-1>", _click)
            b.bind("<Enter>", lambda e, w=b: w.config(fg=ACCENT1))
            b.bind("<Leave>", lambda e, w=b, v=val: w.config(
                fg=GREEN if _info_pos_state["val"] == v else TEXT))
            b._pos_btn = True

        for label, val in [("BOTTOM", "bottom"), ("TOP", "top")]:
            _make_pos_btn(label, val)

        # Format row inside sub
        fmt_sub_row = tk.Frame(info_sub, bg=BORDER, padx=24, pady=3)
        fmt_sub_row.pack(fill="x")
        tk.Label(fmt_sub_row, text="SHOW", bg=BORDER, fg=SUBTEXT,
                 font=(_FONT, _BASE_FONT_SIZE - 2)).pack(side="left", padx=(0, 6))

        def _make_fmt_btn(label, val):
            active = (_info_fmt_state["val"] == val)
            b = tk.Label(fmt_sub_row, text=label, bg=BG,
                         fg=GREEN if active else SUBTEXT,
                         font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), cursor="hand2", padx=6, pady=2)
            b.pack(side="left", padx=(0, 4))
            def _click(e, v=val, btn=b):
                self._datetime_format = v
                _info_fmt_state["val"] = v
                for child in fmt_sub_row.winfo_children():
                    if getattr(child, "_fmt_btn", False):
                        child.config(fg=SUBTEXT)
                btn.config(fg=GREEN)
                info_val_lbl.config(text=_info_summary(),
                                    fg=GREEN if v else SUBTEXT)
                self._update_datetime_lbl()
                _mark_unsaved()
            b.bind("<Button-1>", _click)
            b.bind("<Enter>", lambda e, w=b: w.config(fg=ACCENT1))
            b.bind("<Leave>", lambda e, w=b, v=val: w.config(
                fg=GREEN if _info_fmt_state["val"] == v else SUBTEXT))
            b._fmt_btn = True

        for label, val in [("OFF", ""), ("TIME", "time"), ("DATE", "date"), ("BOTH", "both")]:
            _make_fmt_btn(label, val)

        def _set_info_expanded(want):
            _info_expanded["open"] = want
            info_arrow.config(text="\u25bc" if want else "\u25b6")
            if want:
                info_sub.pack(fill="x", after=info_row)
            else:
                info_sub.pack_forget()
            # Resize popup to fit actual content
            popup.update_idletasks()
            popup.geometry(f"{_popup_locked_size[0]}x{popup.winfo_reqheight()}")

        def _toggle_info_expand(e):
            _set_info_expanded(not _info_expanded["open"])

        info_arrow.bind("<Button-1>", _toggle_info_expand)
        info_arrow.bind("<Enter>", lambda e: info_arrow.config(fg=ACCENT1))
        info_arrow.bind("<Leave>", lambda e: info_arrow.config(fg=SUBTEXT))
        info_row.bind("<Button-1>", _toggle_info_expand)
        self._bind_tooltip(info_arrow,
            "## INFO DISPLAY\n"
            "Show a clock or date inside the overlay.\n"
            "Choose position (top/bottom) and what to show:\n"
            "OFF · TIME · DATE · BOTH")

        if _info_expanded["open"]:
            info_sub.pack(fill="x", after=info_row)

        tk.Frame(popup, bg=BORDER, height=1).pack(fill="x", padx=12, pady=(0, 2))
        rst_row = tk.Frame(popup, bg=PANEL, padx=12, pady=6)
        rst_row.pack(fill="x")
        tk.Label(rst_row, text="RESET THEME", bg=PANEL, fg=TEXT,
                 font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), width=11, anchor="w").pack(side="left")
        rst = tk.Label(rst_row, text="RESET", bg=BORDER, fg=RED,
                       font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), cursor="hand2", padx=8, pady=2)
        rst.pack(side="right")
        rst.bind("<Enter>",    lambda e: rst.config(bg=RED, fg=BG))
        rst.bind("<Leave>",    lambda e: rst.config(bg=BORDER, fg=RED))
        self._bind_tooltip(rst,
            "## RESET THEME\n"
            "Revert all colours back to the Default theme.\n"
            "A confirmation dialog will appear before resetting.\n"
            "Saved themes are not affected — only the active colours.")

        def _confirm_reset():
            popup.destroy()
            confirm = self._make_popup()
            confirm.configure(bg=PANEL)

            tk.Label(confirm, text="RESET THEME", bg=PANEL, fg=RED,
                     font=(_FONT, _BASE_FONT_SIZE, "bold")).pack(pady=(12, 4), padx=16)
            tk.Frame(confirm, bg=RED, height=1).pack(fill="x", padx=12, pady=(0, 10))
            tk.Label(confirm, text="Reset to default theme?", bg=PANEL, fg=TEXT,
                     font=(_FONT, _BASE_FONT_SIZE - 2)).pack(padx=16, pady=(0, 10))

            btn_row = tk.Frame(confirm, bg=PANEL)
            btn_row.pack(fill="x", padx=12, pady=(0, 10))

            yes_btn = tk.Label(btn_row, text="YES", bg=BORDER, fg=RED,
                               font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), cursor="hand2",
                               padx=12, pady=3)
            yes_btn.pack(side="left", padx=(0, 6))
            yes_btn.bind("<Enter>",    lambda e: yes_btn.config(fg=GREEN))
            yes_btn.bind("<Leave>",    lambda e: yes_btn.config(fg=RED))
            yes_btn.bind("<Button-1>", lambda e: (confirm.destroy(), self._reset_to_default()))

            no_btn = tk.Label(btn_row, text="NO", bg=BORDER, fg=SUBTEXT,
                              font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), cursor="hand2",
                              padx=12, pady=3)
            no_btn.pack(side="left")
            no_btn.bind("<Enter>",    lambda e: no_btn.config(fg=GREEN))
            no_btn.bind("<Leave>",    lambda e: no_btn.config(fg=SUBTEXT))
            no_btn.bind("<Button-1>", lambda e: confirm.destroy())

            self._apply_font_size(self._font_size, root=confirm)

            confirm.update_idletasks()
            cw = confirm.winfo_reqwidth()
            ch = confirm.winfo_reqheight()
            cx, cy = self._popup_pos(cw, ch)
            confirm.geometry(f"{cw}x{ch}+{cx}+{cy}")
            confirm.focus_set()
            confirm.bind("<FocusOut>", lambda e: confirm.destroy() if confirm.winfo_exists() else None)

        rst.bind("<Button-1>", lambda e: _confirm_reset())



        # Snapshot every Courier New font in the popup at its as-built (base) size
        # so we can always remap from the original, not from a previously-remapped value.
        _popup_font_originals = {}
        def _snap_popup(w):
            try:
                f = w.cget("font")
                if isinstance(f, str):
                    f = list(self.tk.splitlist(f))
                else:
                    f = list(f)
                if len(f) >= 2 and f[0] == _FONT:
                    _popup_font_originals[id(w)] = (w, list(f))
            except Exception:
                pass
            for c in w.winfo_children():
                _snap_popup(c)
        _snap_popup(popup)

        def _apply_font_to_popup(size):
            """Remap popup fonts from their original base sizes."""
            size = max(6, min(20, int(size)))
            def remap(orig):
                if orig <= 1: return 1
                return max(5, size + (orig - _BASE_FONT_SIZE))
            for wid, (w, orig_f) in list(_popup_font_originals.items()):
                try:
                    nf = list(orig_f)
                    nf[1] = remap(int(orig_f[1]))
                    w.config(font=tuple(nf))
                except Exception:
                    pass

        _apply_font_to_popup(self._font_size)

        # Measure actual size based on current expanded state
        if not _info_expanded["open"]:
            info_sub.pack_forget()
        popup.update_idletasks()
        pw, ph = popup.winfo_reqwidth(), popup.winfo_reqheight()
        _popup_locked_size = [pw, ph]

        bx, by = self._popup_pos(pw, ph)
        popup.geometry(f"{pw}x{ph}+{bx}+{by}")
        def _finalize_color():
            self._color_pending = False
            if not popup.winfo_exists():
                return
            popup.geometry(f"{pw}x{ph}+{bx}+{by}")
            self._color_popup = popup
            self._raise_popup("_color_popup")
            popup.bind("<ButtonPress>", lambda e: self._raise_popup("_color_popup"))
            self._pin_popup_topmost(popup)
        popup.after(30, _finalize_color)
        popup.focus_set()

    # Default colours for per-item reset
    _DEFAULT_COLORS = {
        "_color_gpu":       "#008000",
        "_color_cpu":       "#800000",
        "_color_mem":       "#008080",
        "_color_net":       "#7b30d1",
        "_color_disk":      "#8c4600",
        "_color_storage":   "#c0c0c0",
        "_color_tools":     "#8080ff",
        "BG":               "#0a0a0f",
    }

    def _pick_color(self, current, key=None, live_cb=None):
        """Open colour chooser with live preview. Returns chosen hex or None."""
        from tkinter.colorchooser import askcolor
        result = askcolor(color=current, title="Pick colour", parent=self)
        chosen = result[1] if result and result[1] else None

        # If cancelled, restore original via live_cb
        if not chosen and live_cb:
            try: live_cb(current)
            except Exception: pass

        return chosen

    def _recolor_section(self, hdr, bars, pills, key):
        current = getattr(self, key)
        def _live(c):
            try: hdr._arrow.config(fg=c); hdr._title.config(fg=c); hdr._divider.config(bg=c)
            except Exception: pass
            for bar in bars:
                try:
                    bar.accent = c
                    bar._fill.config(bg=c)
                except Exception: pass
            for pill in pills:
                try: pill.config(fg=c)
                except Exception: pass
        _live(current)
        color = self._pick_color(current, key=key, live_cb=_live)
        if not color:
            _live(current)
            return
        setattr(self, key, color)
        _live(color)
        self._save_position()

    def _recolor_gpu(self):
        self._recolor_section(self._gpu_hdr, [self.gpu_usage, self.vram_bar],
            [self.gpu_watt_lbl, self.gpu_temp_lbl, self.gpu_temp_max_lbl, self.gpu_temp_min_lbl], "_color_gpu")

    def _recolor_cpu(self):
        self._recolor_section(self._cpu_hdr, [self.cpu_usage],
            [self.cpu_freq_max_lbl, self.cpu_freq_min_lbl,
             self.cpu_proc_lbl, self.cpu_thread_lbl, self.cpu_handle_lbl], "_color_cpu")

    def _recolor_mem(self):
        self._recolor_section(self._mem_hdr, [self.ram_bar],
            [self.ram_used_lbl, self.ram_total_lbl, self.ram_pct_lbl, self.ram_peak_lbl], "_color_mem")

    def _recolor_net(self):
        self._recolor_section(self._net_hdr, [self.net_down, self.net_up],
            [self.net_peak_down_lbl, self.net_peak_up_lbl], "_color_net")

    def _recolor_disk(self):
        self._recolor_section(self._disk_hdr, [self.disk_read, self.disk_write],
            [self.disk_peak_read_lbl, self.disk_peak_write_lbl], "_color_disk")

    def _recolor_storage(self):
        self._recolor_section(self._storage_hdr, self._storage_bars, [], "_color_storage")

    def _recolor_bg(self):
        global BG
        current = BG
        def _live(c):
            global BG
            old = BG
            BG = c
            self.configure(bg=c)
            def _r(w):
                try:
                    if w.cget("bg") == old: w.config(bg=c)
                except Exception: pass
                for ch in w.winfo_children(): _r(ch)
            _r(self)
            for attr in ("_color_popup", "_settings_popup"):
                try:
                    w = getattr(self, attr, None)
                    if w and w.winfo_exists(): _r(w)
                except Exception: pass
        color = self._pick_color(current, key="BG", live_cb=_live)
        if not color:
            _live(current)
            return
        _live(color)
        self._save_position()

    def _recolor_btn(self, color_attr, btn_attr):
        """Shared picker for single-button recolor (speedtest, iplookup)."""
        current = getattr(self, color_attr)
        def _live(c):
            try: getattr(self, btn_attr).config(fg=c)
            except Exception: pass
        color = self._pick_color(current, key=color_attr, live_cb=_live)
        if not color:
            _live(current)
            return
        setattr(self, color_attr, color)
        _live(color)
        self._save_position()

    def _recolor_tools(self):
        current = self._color_tools
        def _live(c):
            try: self._st_btn.config(fg=c)
            except Exception: pass
            try: self._ip_btn.config(fg=c)
            except Exception: pass
            try: self._mem_clean_btn.config(fg=c)
            except Exception: pass
        color = self._pick_color(current, key="_color_tools", live_cb=_live)
        if not color:
            _live(current)
            return
        self._color_tools = color
        _live(color)
        self._save_position()

    def _fmt_temp(self, val):
        """Format a temperature value according to the current unit setting."""
        if val is None:
            return "N/A"
        if self._temp_unit == "F":
            return f"{val * 9/5 + 32:.0f}°F"
        return f"{val:.0f}°C"

    def _apply_font_size(self, size, root=None):
        """Walk every widget and remap Courier New font sizes relative to new base.

        If *root* is given (e.g. a Toplevel popup), only that subtree is scanned
        and remapped — the main-window cache is left untouched.  This lets callers
        apply the current font-size setting to freshly-built popup widgets.
        """
        size = max(6, min(20, int(size)))

        # Remap helper: offset from original base 9
        def remap(orig_size):
            if orig_size <= 1: return 1
            offset = orig_size - _BASE_FONT_SIZE  # e.g. 7→-2, 8→-1, 9→0, 10→+1, 11→+2
            return max(5, size + offset)

        # ── Popup-only path ───────────────────────────────────────────────────
        if root is not None:
            # Use snapshotted originals if available (settings popup), else walk current
            originals = getattr(root, "_font_originals", None)
            if originals:
                for wid, (w, orig_f) in list(originals.items()):
                    try:
                        new_f = list(orig_f)
                        new_f[1] = remap(int(orig_f[1]))
                        w.config(font=tuple(new_f))
                    except Exception:
                        originals.pop(wid, None)
            else:
                def _remap_subtree(w):
                    try:
                        f = w.cget("font")
                        if isinstance(f, str):
                            f = list(self.tk.splitlist(f))
                        else:
                            f = list(f)
                        if len(f) >= 2 and f[0] == _FONT:
                            new_f = list(f)
                            new_f[1] = remap(int(f[1]))
                            w.config(font=tuple(new_f))
                    except Exception:
                        pass
                    for c in w.winfo_children():
                        _remap_subtree(c)
                _remap_subtree(root)
            return

        # ── Main-window path ─────────────────────────────────────────────────
        self._font_size = size
        # On first call, snapshot every widget's original font size.
        # Subsequent calls always remap from the original, not the current value.
        if not hasattr(self, "_font_originals"):
            self._font_originals = {}

        def _collect_originals(w, is_root=False):
            # Skip Toplevel popups — they have their own font scope
            if not is_root and isinstance(w, tk.Toplevel):
                return
            wid = id(w)
            if wid not in self._font_originals:
                try:
                    f = w.cget("font")
                    if isinstance(f, str):
                        f = list(self.tk.splitlist(f))
                    else:
                        f = list(f)
                    if len(f) >= 2 and f[0] == _FONT:
                        self._font_originals[wid] = (w, list(f))
                except Exception:
                    pass
            for c in w.winfo_children():
                _collect_originals(c)

        _collect_originals(self, is_root=True)

        for wid, (w, orig_f) in list(self._font_originals.items()):
            try:
                new_f = list(orig_f)
                new_f[1] = remap(int(orig_f[1]))
                w.config(font=tuple(new_f))
            except Exception:
                # Widget was destroyed — drop it from cache
                del self._font_originals[wid]

        # ── Ticker canvas (canvas items aren't widgets, handle separately) ────
        try:
            ticker_font = (_FONT, remap(7))
            new_h = max(10, remap(7) * 2)
            cy = new_h // 2
            self._ticker_canvas.itemconfig(self._ticker_text_id,  font=ticker_font)
            self._ticker_canvas.itemconfig(self._ticker_text_id2, font=ticker_font)
            self._ticker_canvas.config(height=new_h)
            self._ticker_canvas.coords(self._ticker_text_id,  self._ticker_x, cy)
            self._ticker_canvas.coords(self._ticker_text_id2, self._ticker_x, cy)
            # Keep the status frame height and time label in sync
            sf = self._ticker_canvas.master
            sf.config(height=new_h)
            self.time_lbl.config(font=ticker_font)
        except Exception:
            pass

        self.update_idletasks()
        self.geometry(f"{self.winfo_width()}x{self.winfo_reqheight()}")

        # ── Also remap & resize any open settings popup ───────────────────────
        try:
            sp = self._settings_popup
            if sp and sp.winfo_exists():
                # Always remap from snapshotted originals so size 9 restores correctly
                originals = getattr(sp, "_font_originals", {})
                for wid, (w, orig_f) in list(originals.items()):
                    try:
                        new_f = list(orig_f)
                        orig_size = int(orig_f[1])
                        new_f[1] = max(5, size + (orig_size - _BASE_FONT_SIZE))
                        w.config(font=tuple(new_f))
                    except Exception:
                        originals.pop(wid, None)
                sp.update_idletasks()
                sp.geometry(f"{sp.winfo_reqwidth()}x{sp.winfo_reqheight()}")
        except Exception:
            pass

        # ── Re-check top button bar label after font change ───────────────────
        try:
            fn = self._resize_top_btns
            if fn:
                self.after(10, fn)
        except Exception:
            pass

    def _validate_theme(self, data):
        """Fill any missing keys in a theme dict with current values as defaults."""
        defaults = {
            "BG": BG, "PANEL": PANEL, "BORDER": BORDER,
            "TEXT": TEXT, "SUBTEXT": SUBTEXT, "DIM": DIM,
            "gpu": self._color_gpu, "cpu": self._color_cpu,
            "mem": self._color_mem, "net": self._color_net,
            "disk": self._color_disk, "storage": self._color_storage,
            "tools": self._color_tools,
            "font_size": self._font_size,
            "datetime_format": self._datetime_format,
            "datetime_position": self._datetime_position,
            "opacity": 1.0,  # safe default; overridden if theme file contains the key
            "layout_mode": self._layout_mode,
        }
        for key, val in defaults.items():
            data.setdefault(key, val)
        return data

    def _apply_theme(self, bg, panel, border, c_gpu, c_cpu, c_mem, c_net, c_disk, c_storage, text, subtext, dim, c_tools=None, *_ignored):
        global BG, PANEL, BORDER, TEXT, SUBTEXT, DIM
        old_bg, old_panel, old_border = BG, PANEL, BORDER
        old_text, old_subtext, old_dim = TEXT, SUBTEXT, DIM
        BG = bg; PANEL = panel; BORDER = border
        TEXT = text; SUBTEXT = subtext; DIM = dim
        self._color_gpu = c_gpu
        self._color_cpu = c_cpu; self._color_mem = c_mem
        self._color_net = c_net; self._color_disk = c_disk
        self._color_storage = c_storage
        if c_tools is not None:
            self._color_tools = c_tools
        try: self._st_btn.config(fg=self._color_tools)
        except Exception: pass
        try: self._ip_btn.config(fg=self._color_tools)
        except Exception: pass
        try: self._mem_clean_btn.config(fg=self._color_tools)
        except Exception: pass
        for hdr, color in [
            (self._gpu_hdr, c_gpu),
            (self._cpu_hdr, c_cpu), (self._mem_hdr, c_mem),
            (self._net_hdr, c_net), (self._disk_hdr, c_disk),
            (self._storage_hdr, c_storage),
        ]:
            try: hdr._arrow.config(fg=color); hdr._title.config(fg=color); hdr._divider.config(bg=color)
            except Exception: pass
        for bar, color in [
            (self.gpu_usage, c_gpu), (self.vram_bar, c_gpu),
            (self.cpu_usage, c_cpu), (self.ram_bar, c_mem),
            (self.net_down, c_net), (self.net_up, c_net),
            (self.disk_read, c_disk), (self.disk_write, c_disk),
        ]:
            try: bar.accent = color
            except Exception: pass
        for bar in self._storage_bars:
            try: bar.accent = c_storage
            except Exception: pass
        for pill, color in [
            (self.gpu_watt_lbl, c_gpu), (self.gpu_temp_lbl, c_gpu),
            (self.gpu_temp_max_lbl, c_gpu), (self.gpu_temp_min_lbl, c_gpu),
            (self.cpu_freq_max_lbl, c_cpu), (self.cpu_freq_min_lbl, c_cpu),
            (self.cpu_proc_lbl, c_cpu), (self.cpu_thread_lbl, c_cpu), (self.cpu_handle_lbl, c_cpu),
            (self.ram_used_lbl, c_mem), (self.ram_total_lbl, c_mem),
            (self.ram_pct_lbl,  c_mem), (self.ram_peak_lbl,  c_mem),
            (self.net_peak_down_lbl,  c_net),  (self.net_peak_up_lbl,    c_net),
            (self.disk_peak_read_lbl, c_disk), (self.disk_peak_write_lbl, c_disk),
        ]:
            try: pill.config(fg=color)
            except Exception: pass
        def _r(w):
            try:
                cur_bg = w.cget("bg")
                if   cur_bg == old_bg:     w.config(bg=bg)
                elif cur_bg == old_panel:  w.config(bg=panel)
                elif cur_bg == old_border: w.config(bg=border)
                elif cur_bg == old_dim:    w.config(bg=dim)
            except Exception: pass
            try:
                cur_fg = w.cget("fg")
                if   cur_fg == old_text:    w.config(fg=text)
                elif cur_fg == old_subtext: w.config(fg=subtext)
                elif cur_fg == old_dim:     w.config(fg=dim)
            except Exception: pass
            for c in w.winfo_children(): _r(c)
        _r(self)
        # Also update any open popups
        for attr in ("_color_popup", "_settings_popup"):
            try:
                w = getattr(self, attr, None)
                if w and w.winfo_exists():
                    _r(w)
            except Exception: pass

    def _reset_to_default(self):
        """Reset to the Default theme JSON file; fall back to built-in dark if missing."""
        try:
            with open(_DEFAULT_THEME_PATH) as f:
                t = json.load(f)
            t = self._validate_theme(t)
            self._apply_theme(
                t["BG"], t["PANEL"], t["BORDER"],
                t["gpu"], t["cpu"], t["mem"], t["net"], t["disk"], t["storage"],
                t["TEXT"], t["SUBTEXT"], t["DIM"], t["tools"],
            )
            self._apply_font_size(t["font_size"])
            self._datetime_format   = t["datetime_format"]
            self._datetime_position = t["datetime_position"]
            self._active_theme_path = _DEFAULT_THEME_PATH
            self._save_position()
        except Exception:
            self._theme_dark()

    def _theme_dark(self):
        self._apply_theme(
            BG, PANEL, BORDER,
            "#008000", "#800000", "#008080", "#400080", "#8c4600", "#c0c0c0",
            TEXT, SUBTEXT, DIM,
            "#8080ff", "#8080ff",
        )
        # Read font size from the Default theme file so it respects the user's
        # chosen default rather than hardcoding a value here.
        try:
            with open(_DEFAULT_THEME_PATH) as _f:
                _t = json.load(_f)
            self._apply_font_size(_t.get("font_size", _BASE_FONT_SIZE))
        except Exception:
            self._apply_font_size(_BASE_FONT_SIZE)


    def _theme_terminal(self):
        """Green phosphor terminal theme."""
        self._apply_theme(
            "#020c02", "#061206", "#0d2b0d",
            "#00ff41", "#39ff14", "#00cc33", "#00aa22", "#008833", "#00dd44",
            "#00ff41", "#007722", "#003311",
            "#00cc33", "#00cc33",
        )


    def _theme_ice(self):
        """Cool ice blue theme."""
        self._apply_theme(
            "#020810", "#060f1e", "#0d1f3c",
            "#00cfff", "#0077ff", "#00aaff", "#3399ff", "#0055cc", "#66ccff",
            "#c8e8ff", "#3366aa", "#0d2040",
            "#00aaff", "#00aaff",
        )


    def _theme_sunset(self):
        """Warm sunset orange/pink theme."""
        self._apply_theme(
            "#0f0505", "#1a0a08", "#2e1210",
            "#ff6b35", "#ff3860", "#ff9500", "#cc2244", "#ff5500", "#ffaa55",
            "#ffe0d0", "#884433", "#3d1a10",
            "#ff6b35", "#ff6b35",
        )


    def _theme_midnight(self):
        """Deep purple midnight theme."""
        self._apply_theme(
            "#07040f", "#0e0820", "#1a1030",
            "#b060ff", "#ff40aa", "#8040ff", "#6020dd", "#a050ee", "#c080ff",
            "#e0d0ff", "#5030aa", "#1a1040",
            "#8040ff", "#8040ff",
        )


    def _theme_light(self):
        """Clean light theme — soft white background, distinct readable accents."""
        self._apply_theme(
            "#f4f4f8",   # BG       — very light grey-white
            "#ffffff",   # PANEL    — pure white panels
            "#dcdce8",   # BORDER   — soft grey dividers
            "#0066cc",   # GPU      — strong blue
            "#cc2200",   # CPU      — deep red
            "#007755",   # MEM      — forest green
            "#7700cc",   # NET      — purple
            "#bb5500",   # DISK     — burnt orange
            "#888888",   # STORAGE  — neutral grey
            "#1a1a2e",   # TEXT     — near-black for readability
            "#666688",   # SUBTEXT  — muted blue-grey
            "#c0c0d8",   # DIM      — light lavender for separators
            "#0066cc",   # SPEEDTEST
            "#0066cc",   # IPLOOKUP
        )


    def _pill(self, parent, label, accent):
        f = tk.Frame(parent, bg=PANEL, padx=6)
        f.pack(side="left")
        tk.Label(f, text=label, bg=PANEL, fg=SUBTEXT,
                 font=(_FONT, _BASE_FONT_SIZE - 3, "bold")).pack()
        lbl = tk.Label(f, text="—", bg=PANEL, fg=accent,
                       font=(_FONT, _BASE_FONT_SIZE, "bold"))
        lbl.pack()
        return lbl

    def _log_enter(self, e):
        if self._logging_active:
            self._log_lbl.config(text="◉ STOP", bg=RED, fg=BG)
        else:
            self._log_lbl.config(bg=GREEN, fg=BG)

    def _log_leave(self, e):
        if self._logging_active:
            self._log_lbl.config(text="● REC", bg=RED, fg=BG)
        else:
            self._log_lbl.config(text="◉ LOG", bg=BORDER, fg=SUBTEXT)

    def _toggle_log(self):
        if self._logging_active:
            self._logging_active = False
            if self._log_after_id is not None:
                self.after_cancel(self._log_after_id)
                self._log_after_id = None
            self._log_lbl.config(text="◉ LOG", bg=BORDER, fg=SUBTEXT)
            # Write session-end marker
            self._append_log_marker("END")
        else:
            # Cancel any stale callback before starting a fresh loop
            if self._log_after_id is not None:
                self.after_cancel(self._log_after_id)
                self._log_after_id = None
            self._logging_active = True
            self._log_lbl.config(text="● REC", bg=RED, fg=BG)
            # Write session-start marker then first snapshot immediately
            self._append_log_marker("START")
            self._log_snapshot()

    def _append_log_marker(self, kind):
        now = datetime.datetime.now()
        sep = "═" * 48
        ts  = now.strftime("%Y-%m-%d %H:%M:%S")
        if kind == "START":
            line = f"\n{sep}\n  ▶ Logging started  —  {ts}\n{sep}\n"
        else:
            line = f"\n{sep}\n  ■ Logging stopped  —  {ts}\n{sep}\n"
        try:
            with open(_DATA_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass

    def _log_snapshot(self):
        """Write one compact snapshot line, then reschedule if still logging."""
        self._log_after_id = None
        if not self._logging_active:
            return
        s = self._last_snapshot
        if s is None:
            self._log_after_id = self.after(self._log_interval_ms, self._log_snapshot)
            return

        now = datetime.datetime.now()
        ts  = now.strftime("%Y-%m-%d %H:%M:%S")

        def fmt(val, fmt_str, fallback="N/A"):
            return fmt_str.format(val) if val is not None else fallback

        # Build one line per component, all horizontal
        gpu  = s["gpu"]
        cpu_line = (
            f"CPU {s['cpu_pct']:.0f}%"
            + f" | {fmt(s['freq_ghz'], '{:.2f}GHz')}"
            + f" | {s['procs']}P {s['threads']}T {s['handles']}H"
        )
        gpu_line = (
            f"GPU {gpu['usage']:.0f}%"
            + f" | {gpu['vram_used']:.1f}/{gpu['vram_total']:.0f}GB VRAM"
            + f" | {self._fmt_temp(gpu['temp'])}"
            + f" | {fmt(gpu['wattage'], '{:.0f}W')}"
        )
        mem_line  = f"MEM {s['ram_used']:.1f}/{s['ram_total']:.0f}GB ({s['ram_pct']:.0f}%)"
        net_line  = f"NET ↓{s['net_down']:.2f} ↑{s['net_up']:.2f} MB/s"
        disk_line = f"DISK R:{s['disk_read']:.2f} W:{s['disk_write']:.2f} MB/s"

        parts = [f"[{ts}]", gpu_line, cpu_line, mem_line, net_line, disk_line]

        if s.get("disk_usage"):
            stor = "  ".join(
                f"{mp.rstrip(chr(92)).rstrip('/')} {used:.0f}/{total:.0f}GB ({pct:.0f}%)"
                for mp, used, total, pct in s["disk_usage"]
            )
            parts.append(f"STR {stor}")

        line = "  |  ".join(parts)

        try:
            with open(_DATA_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

        self._log_after_id = self.after(self._log_interval_ms, self._log_snapshot)

    # ── Startup helpers ───────────────────────────────────────────────────────
    _REG_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
    _REG_KEY = "PyDisplay"

    def _startup_enabled(self):
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._REG_RUN) as k:
                winreg.QueryValueEx(k, self._REG_KEY)
            return True
        except Exception:
            return False

    def _set_startup(self, enable):
        try:
            import winreg, sys
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._REG_RUN, 0,
                                winreg.KEY_SET_VALUE) as k:
                if enable:
                    exe = sys.executable
                    script = os.path.abspath(__file__)
                    winreg.SetValueEx(k, self._REG_KEY, 0, winreg.REG_SZ,
                                      f'"{exe}" "{script}"')
                else:
                    try:
                        winreg.DeleteValue(k, self._REG_KEY)
                    except FileNotFoundError:
                        pass
            return True
        except Exception:
            return False


    def _settings_startup(self, tog_grid, popup):
        # ── Start with Windows ────────────────────────────────────────────────
        tk.Label(tog_grid, text="START WITH WINDOWS", bg=PANEL, fg=TEXT,
                 font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), anchor="w").grid(
                 row=0, column=1, sticky="w", pady=4, padx=(4,0))

        _startup_on = self._startup_enabled()
        _state = {"on": _startup_on}

        toggle_lbl = tk.Label(tog_grid,
                              text="ON" if _startup_on else "OFF",
                              bg=BORDER,
                              fg=GREEN if _startup_on else SUBTEXT,
                              font=(_FONT, _BASE_FONT_SIZE - 2, "bold"),
                              cursor="hand2", padx=8, pady=2, width=4, anchor="center")
        toggle_lbl.grid(row=0, column=2, sticky="e", pady=4, padx=(0,4))

        status_lbl = tk.Label(popup, text="", bg=PANEL, fg=SUBTEXT,
                              font=(_FONT, _BASE_FONT_SIZE - 3), pady=0)

        def _toggle_startup(e):
            want = not _state["on"]
            if not want:
                def _do_remove():
                    ok = self._set_startup(False)
                    if ok:
                        _state["on"] = False
                        toggle_lbl.config(text="OFF", fg=SUBTEXT)
                        status_lbl.config(text="✔ Removed from startup", fg=GREEN)
                        status_lbl.pack()
                    else:
                        status_lbl.config(text="✘ Failed — check permissions", fg=RED)
                        status_lbl.pack()

                confirm = self._make_popup()
                confirm.configure(bg=PANEL)
                tk.Label(confirm, text="REMOVE STARTUP ENTRY?", bg=PANEL, fg=RED,
                         font=(_FONT, _BASE_FONT_SIZE, "bold")).pack(pady=(10, 4), padx=16)
                tk.Frame(confirm, bg=RED, height=1).pack(fill="x", padx=12, pady=(0, 8))
                tk.Label(confirm,
                         text="Remove PyDisplay from Windows startup?\n"
                              "You can re-enable this at any time.",
                         bg=PANEL, fg=TEXT, font=(_FONT, _BASE_FONT_SIZE - 2),
                         justify="center").pack(padx=16, pady=(0, 10))
                _cfg_row = tk.Frame(confirm, bg=PANEL)
                _cfg_row.pack(fill="x", padx=14, pady=(0, 10))
                yes_btn2 = tk.Label(_cfg_row, text="YES, REMOVE", bg=BORDER, fg=RED,
                                    font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), cursor="hand2",
                                    padx=10, pady=3)
                yes_btn2.pack(side="left", padx=(0, 6))
                yes_btn2.bind("<Enter>", lambda e: yes_btn2.config(fg=GREEN))
                yes_btn2.bind("<Leave>", lambda e: yes_btn2.config(fg=RED))
                yes_btn2.bind("<Button-1>", lambda e: (confirm.destroy(), _do_remove()))
                no_btn2 = tk.Label(_cfg_row, text="KEEP IT", bg=BORDER, fg=SUBTEXT,
                                   font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), cursor="hand2",
                                   padx=10, pady=3)
                no_btn2.pack(side="left")
                no_btn2.bind("<Enter>", lambda e: no_btn2.config(fg=GREEN))
                no_btn2.bind("<Leave>", lambda e: no_btn2.config(fg=SUBTEXT))
                no_btn2.bind("<Button-1>", lambda e: confirm.destroy())
                self._apply_font_size(self._font_size, root=confirm)
                confirm.update_idletasks()
                cw = confirm.winfo_reqwidth()
                ch = confirm.winfo_reqheight()
                px2 = popup.winfo_x() + (popup.winfo_width() - cw) // 2
                py2 = popup.winfo_y() + (popup.winfo_height() - ch) // 2
                confirm.geometry(f"{cw}x{ch}+{px2}+{py2}")
                confirm.focus_set()
            else:
                ok = self._set_startup(True)
                if ok:
                    _state["on"] = True
                    toggle_lbl.config(text="ON", fg=GREEN)
                    status_lbl.config(text="✔ Added to startup", fg=GREEN)
                    status_lbl.pack()
                else:
                    status_lbl.config(text="✘ Failed — check permissions", fg=RED)
                    status_lbl.pack()

        toggle_lbl.bind("<Button-1>", _toggle_startup)
        toggle_lbl.bind("<Enter>", lambda e: toggle_lbl.config(fg=ACCENT1))
        toggle_lbl.bind("<Leave>", lambda e: toggle_lbl.config(
            fg=GREEN if _state["on"] else SUBTEXT))
        self._bind_tooltip(toggle_lbl,
            "## START WITH WINDOWS\n"
            "Add PyDisplay to the Windows registry startup key\n"
            "so it launches automatically when you log in.\n"
            "Writes to HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run.")

        # separator
        tk.Frame(tog_grid, bg=BORDER, height=1).grid(
            row=1, column=0, columnspan=3, sticky="ew")


    def _settings_aot(self, tog_grid, popup):
        # ── Always on top ─────────────────────────────────────────────────────
        tk.Label(tog_grid, text="ALWAYS ON TOP", bg=PANEL, fg=TEXT,
                 font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), anchor="w").grid(row=2, column=1, sticky="w", pady=4, padx=(4,0))

        _aot_on = bool(self.wm_attributes("-topmost"))
        _aot_state = {"on": _aot_on}

        aot_lbl = tk.Label(tog_grid,
                           text="ON" if _aot_on else "OFF",
                           bg=BORDER,
                           fg=GREEN if _aot_on else SUBTEXT,
                           font=(_FONT, _BASE_FONT_SIZE - 2, "bold"),
                           cursor="hand2", padx=8, pady=2, width=4, anchor="center")
        aot_lbl.grid(row=2, column=2, sticky="e", pady=4, padx=(0,4))

        def _toggle_aot(e):
            want = not _aot_state["on"]
            self.wm_attributes("-topmost", want)
            _aot_state["on"] = want
            aot_lbl.config(text="ON" if want else "OFF",
                           fg=GREEN if want else SUBTEXT)
            def _reassert_alpha():
                try:
                    alpha = float(self.wm_attributes("-alpha"))
                    if self._hwnd:
                        _set_click_through(self._hwnd, True, alpha)
                    for w in self.winfo_children():
                        try:
                            if isinstance(w, tk.Toplevel) and w is not popup:
                                w.wm_attributes("-alpha", 1.0)
                        except Exception:
                            pass
                except Exception:
                    pass
                if want:
                    self._repin_open_popups()
            self.after(50, _reassert_alpha)
            self._save_position()

        aot_lbl.bind("<Button-1>", _toggle_aot)
        aot_lbl.bind("<Enter>", lambda e: aot_lbl.config(fg=ACCENT1))
        aot_lbl.bind("<Leave>", lambda e: aot_lbl.config(
            fg=GREEN if _aot_state["on"] else SUBTEXT))
        self._bind_tooltip(aot_lbl,
            "## ALWAYS ON TOP\n"
            "Keep the PyDisplay overlay above all other windows.\n"
            "Disable if it overlaps pop-ups or full-screen games.")

        # separator
        tk.Frame(tog_grid, bg=BORDER, height=1).grid(
            row=3, column=0, columnspan=3, sticky="ew")


    def _settings_lock(self, tog_grid, popup):
        # ── Lock position ─────────────────────────────────────────────────────
        _ = tk.Label(tog_grid, text="LOCK POSITION", bg=PANEL, fg=TEXT,
                 font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), anchor="w")
        _.grid(row=4, column=1, sticky="w", pady=4, padx=(4,0))

        _lock_state = {"on": self._position_locked}

        lock_lbl = tk.Label(tog_grid,
                            text="ON" if self._position_locked else "OFF",
                            bg=BORDER,
                            fg=GREEN if self._position_locked else SUBTEXT,
                            font=(_FONT, _BASE_FONT_SIZE - 2, "bold"),
                            cursor="hand2", padx=8, pady=2, width=4, anchor="center")
        lock_lbl.grid(row=4, column=2, sticky="e", pady=4, padx=(0,4))

        def _toggle_lock(e):
            want = not _lock_state["on"]
            self._position_locked = want
            _lock_state["on"] = want
            lock_lbl.config(text="ON" if want else "OFF",
                            fg=GREEN if want else SUBTEXT)
            self._save_position()

        lock_lbl.bind("<Button-1>", _toggle_lock)
        lock_lbl.bind("<Enter>", lambda e: lock_lbl.config(fg=ACCENT1))
        lock_lbl.bind("<Leave>", lambda e: lock_lbl.config(
            fg=GREEN if _lock_state["on"] else SUBTEXT))
        self._bind_tooltip(lock_lbl,
            "## LOCK POSITION\n"
            "Prevent the overlay from being moved or resized.\n"
            "Useful once you\'ve got it positioned just right.")

        # separator
        tk.Frame(tog_grid, bg=BORDER, height=1).grid(
            row=5, column=0, columnspan=3, sticky="ew")


    def _settings_clickthrough(self, tog_grid, popup):
        # ── Click-through ─────────────────────────────────────────────────────
        _ = tk.Label(tog_grid, text="CLICK-THROUGH", bg=PANEL, fg=TEXT,
                 font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), anchor="w")
        _.grid(row=6, column=1, sticky="w", pady=4, padx=(4,0))

        _ct_state = {"on": self._click_through_on}

        ct_lbl = tk.Label(tog_grid,
                          text="ON" if self._click_through_on else "OFF",
                          bg=BORDER,
                          fg=GREEN if self._click_through_on else SUBTEXT,
                          font=(_FONT, _BASE_FONT_SIZE - 2, "bold"),
                          cursor="hand2", padx=8, pady=2, width=4, anchor="center")
        ct_lbl.grid(row=6, column=2, sticky="e", pady=4, padx=(0,4))

        def _toggle_ct(e):
            want = not _ct_state["on"]
            self._click_through_on = want
            _ct_state["on"] = want
            ct_lbl.config(text="ON" if want else "OFF",
                          fg=GREEN if want else SUBTEXT)
            self._save_position()
            return "break"

        ct_lbl.bind("<Button-1>", _toggle_ct)
        ct_lbl.bind("<Enter>", lambda e: ct_lbl.config(fg=ACCENT1))
        ct_lbl.bind("<Leave>", lambda e: ct_lbl.config(
            fg=GREEN if _ct_state["on"] else SUBTEXT))
        self._bind_tooltip(ct_lbl,
            "## CLICK-THROUGH\n"
            "ON: mouse clicks pass through to windows behind the overlay.\n"
            "    Hold Ctrl to temporarily make it interactive.\n"
            "OFF: overlay is always interactive — no Ctrl key needed.\n"
            "    Tooltips also show without Ctrl when this is OFF.")

        # separator
        tk.Frame(tog_grid, bg=BORDER, height=1).grid(
            row=7, column=0, columnspan=3, sticky="ew")


    def _settings_tooltips(self, tog_grid, popup):
        # ── Show Tooltips ─────────────────────────────────────────────────────
        tk.Label(tog_grid, text="", bg=PANEL).grid(
            row=8, column=0, sticky="e", pady=4, padx=(0, 2))

        tk.Label(tog_grid, text="SHOW TOOLTIPS", bg=PANEL, fg=TEXT,
                 font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), anchor="w").grid(
                 row=8, column=1, sticky="w", pady=4, padx=(4, 0))

        _tt_state = {"on": self._tooltips_enabled}

        tt_lbl = tk.Label(tog_grid,
                          text="ON" if _tt_state["on"] else "OFF",
                          bg=BORDER,
                          fg=GREEN if _tt_state["on"] else SUBTEXT,
                          font=(_FONT, _BASE_FONT_SIZE - 2, "bold"),
                          cursor="hand2", padx=8, pady=2, width=4, anchor="center")
        tt_lbl.grid(row=8, column=2, sticky="e", pady=4, padx=(0,4))

        def _toggle_tt(e):
            want = not _tt_state["on"]
            self._tooltips_enabled = want
            _tt_state["on"] = want
            tt_lbl.config(text="ON" if want else "OFF",
                          fg=GREEN if want else SUBTEXT)
            self._save_position()

        tt_lbl.bind("<Button-1>", _toggle_tt)
        tt_lbl.bind("<Enter>", lambda e: tt_lbl.config(fg=ACCENT1))
        tt_lbl.bind("<Leave>", lambda e: tt_lbl.config(
            fg=GREEN if _tt_state["on"] else SUBTEXT))
        self._bind_tooltip(tt_lbl,
            "## SHOW TOOLTIPS\n"
            "Enable hover tooltips on every button and stat.\n"
            "Tooltips still require Ctrl to be held, or\n"
            "Click-Through to be OFF, before they appear.")

        # separator
        tk.Frame(tog_grid, bg=BORDER, height=1).grid(
            row=9, column=0, columnspan=3, sticky="ew")


    def _settings_mtt(self, tog_grid, popup):
        # ── Minimize to tray (collapsible) ────────────────────────────────────
        _mtt_state     = {"on": self._minimize_to_tray}
        _mtt_expanded  = {"open": _mtt_state["on"]}

        mtt_arrow = tk.Label(tog_grid,
                             text="\u25bc" if _mtt_expanded["open"] else "\u25b6",
                             bg=PANEL, fg=SUBTEXT,
                             font=(_FONT, _BASE_FONT_SIZE - 2), cursor="hand2")
        mtt_arrow.grid(row=10, column=0, sticky="e", pady=4, padx=(0,2))

        mtt_text = tk.Label(tog_grid, text="MINIMIZE TO TRAY", bg=PANEL, fg=TEXT,
                 font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), anchor="w")
        mtt_text.grid(row=10, column=1, sticky="w", pady=4, padx=(4,0))

        mtt_lbl = tk.Label(tog_grid,
                           text="ON" if _mtt_state["on"] else "OFF",
                           bg=BORDER,
                           fg=GREEN if _mtt_state["on"] else SUBTEXT,
                           font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), width=4, anchor="center",
                           cursor="hand2", padx=8, pady=2)
        mtt_lbl.grid(row=10, column=2, sticky="e", pady=4, padx=(0,4))

        # ── Sub-options panel (shown when expanded) ───────────────────────────
        mtt_sub = tk.Frame(tog_grid, bg=BORDER)

        tgu_sub_row = tk.Frame(mtt_sub, bg=BORDER, padx=24, pady=3)
        tgu_sub_row.pack(fill="x")
        tk.Label(tgu_sub_row, text="\u2514  SHOW GPU %", bg=BORDER, fg=SUBTEXT,
                 font=(_FONT, _BASE_FONT_SIZE - 2)).pack(side="left")

        _tgu_on = self._tray_show_gpu
        _tgu_state = {"on": _tgu_on}
        tgu_lbl = tk.Label(tgu_sub_row,
                           text="ON" if _tgu_on else "OFF",
                           bg=BG,
                           fg=GREEN if _tgu_on else SUBTEXT,
                           font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), width=4, anchor="center",
                           cursor="hand2", padx=8, pady=2)
        tgu_lbl.pack(side="right")

        def _toggle_tgu(e):
            want = not _tgu_state["on"]
            self._tray_show_gpu = want
            _tgu_state["on"] = want
            tgu_lbl.config(text="ON" if want else "OFF",
                           fg=GREEN if want else SUBTEXT)
            self._save_position()
        tgu_lbl.bind("<Button-1>", _toggle_tgu)
        tgu_lbl.bind("<Enter>", lambda e: tgu_lbl.config(fg=ACCENT1))
        tgu_lbl.bind("<Leave>", lambda e: tgu_lbl.config(
            fg=GREEN if _tgu_state["on"] else SUBTEXT))
        self._bind_tooltip(tgu_lbl,
            "## SHOW GPU % IN TRAY\n"
            "When active, the tray icon displays live GPU load %\n"
            "as a colour-coded number (green \u2192 yellow \u2192 red).")

        def _set_mtt_expanded(want):
            _mtt_expanded["open"] = want
            mtt_arrow.config(text="\u25bc" if want else "\u25b6")
            if want:
                mtt_sub.grid(row=11, column=0, columnspan=3, sticky="ew", padx=4)
            else:
                mtt_sub.grid_remove()
            popup.update_idletasks()
            popup.geometry(f"{popup.winfo_reqwidth()}x{popup.winfo_reqheight()}")

        def _toggle_mtt(e):
            want = not _mtt_state["on"]
            self._minimize_to_tray = want
            _mtt_state["on"] = want
            mtt_lbl.config(text="ON" if want else "OFF",
                           fg=GREEN if want else SUBTEXT)
            self._save_position()
            try:
                self._close_btn.config(
                    text=" HIDE" if want else " \u2715 ",
                    font=(_FONT, _BASE_FONT_SIZE - 2 if want else _BASE_FONT_SIZE, "bold"))
            except Exception:
                pass
            _set_mtt_expanded(want)

        def _toggle_mtt_expand(e):
            _set_mtt_expanded(not _mtt_expanded["open"])

        mtt_lbl.bind("<Button-1>", _toggle_mtt)
        mtt_lbl.bind("<Enter>", lambda e: mtt_lbl.config(fg=ACCENT1))
        mtt_lbl.bind("<Leave>", lambda e: mtt_lbl.config(
            fg=GREEN if _mtt_state["on"] else SUBTEXT))
        self._bind_tooltip(mtt_lbl,
            "## MINIMIZE TO TRAY\n"
            "When ON, the \u2715 close button hides PyDisplay to\n"
            "the system tray instead of quitting.\n"
            "Right-click the tray icon to Restore or Quit.")

        mtt_arrow.bind("<Button-1>", _toggle_mtt_expand)
        mtt_arrow.bind("<Enter>", lambda e: mtt_arrow.config(fg=ACCENT1))
        mtt_arrow.bind("<Leave>", lambda e: mtt_arrow.config(fg=SUBTEXT))

        if _mtt_expanded["open"]:
            mtt_sub.grid(row=11, column=0, columnspan=3, sticky="ew", padx=4)


    def _settings_rates(self, tog_grid, popup):
        # ── Opacity / Poll Rate / Log Every        # ── Opacity / Poll Rate / Log Every — shared grid for column alignment ─
        sel_grid = tk.Frame(popup, bg=PANEL, padx=12, pady=2)
        sel_grid.pack(fill="x")

        _poll_options    = [("0.5s", 500), ("1s", 1000), ("2s", 2000), ("5s", 5000)]
        _log_options     = [("5s", 5000), ("15s", 15000), ("30s", 30000), ("60s", 60000)]

        # -- POLL RATE row (grid row 0) --
        tk.Label(sel_grid, text="POLL RATE", bg=PANEL, fg=TEXT,
                 font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), width=9, anchor="w").grid(
                 row=0, column=0, sticky="w", padx=(0, 4), pady=4)

        def _make_poll_btn(label, ms, col):
            b = tk.Label(sel_grid, text=label, bg=BORDER,
                         fg=GREEN if ms == self._refresh_ms else SUBTEXT,
                         font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), width=4,
                         anchor="center", cursor="hand2", pady=2)
            b.grid(row=0, column=col, padx=(0, 4), pady=4, sticky="ew")
            def _click(e, m=ms, btn=b):
                self._refresh_ms = m
                self._save_position()
                for child in sel_grid.winfo_children():
                    if getattr(child, "_poll_btn", False):
                        child.config(fg=SUBTEXT)
                btn.config(fg=GREEN)
            b.bind("<Button-1>", _click)
            b.bind("<Enter>", lambda e, btn=b, m=ms: btn.config(fg=ACCENT1))
            b.bind("<Leave>", lambda e, btn=b, m=ms: btn.config(
                fg=GREEN if m == self._refresh_ms else SUBTEXT))
            b._poll_btn = True
            self._bind_tooltip(b,
                f"## POLL RATE  {label}\n"
                f"Refresh all stats every {label}.\n"
                "Faster = smoother but more CPU. Slower = lower overhead.")
        for _ci, (_lbl, _ms) in enumerate(_poll_options):
            _make_poll_btn(_lbl, _ms, _ci + 1)

        # separator
        tk.Frame(sel_grid, bg=BORDER, height=1).grid(
            row=1, column=0, columnspan=5, sticky="ew", pady=0)

        # -- LOG EVERY row (grid row 2) --
        _ = tk.Label(sel_grid, text="LOG EVERY", bg=PANEL, fg=TEXT,
                 font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), width=9, anchor="w")
        _.grid(row=2, column=0, sticky="w", padx=(0, 4), pady=4)

        def _make_log_btn(label, ms, col):
            b = tk.Label(sel_grid, text=label, bg=BORDER,
                         fg=GREEN if ms == self._log_interval_ms else SUBTEXT,
                         font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), width=4,
                         anchor="center", cursor="hand2", pady=2)
            b.grid(row=2, column=col, padx=(0, 4), pady=4, sticky="ew")
            def _click(e, m=ms, btn=b):
                self._log_interval_ms = m
                self._save_position()
                for child in sel_grid.winfo_children():
                    if getattr(child, "_log_btn", False):
                        child.config(fg=SUBTEXT)
                btn.config(fg=GREEN)
            b.bind("<Button-1>", _click)
            b.bind("<Enter>", lambda e, btn=b, m=ms: btn.config(fg=ACCENT1))
            b.bind("<Leave>", lambda e, btn=b, m=ms: btn.config(
                fg=GREEN if m == self._log_interval_ms else SUBTEXT))
            b._log_btn = True
            self._bind_tooltip(b,
                f"## LOG EVERY  {label}\n"
                f"Write one stats snapshot line to the log file every {label}.\n"
                "Shorter = more data. Longer = smaller log file size.")
        for _ci, (_lbl, _ms) in enumerate(_log_options):
            _make_log_btn(_lbl, _ms, _ci + 1)


    def _settings_tempunit(self, tog_grid, popup):
        # ── Temperature unit ─────────────────────────────────────────────────
        tk.Frame(popup, bg=BORDER, height=1).pack(fill="x", padx=8, pady=(4, 0))
        _tu_settings_row = tk.Frame(popup, bg=PANEL, padx=12, pady=6)
        _tu_settings_row.pack(fill="x")
        tk.Label(_tu_settings_row, text="TEMP UNIT", bg=PANEL, fg=TEXT,
                 font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), width=9, anchor="w").pack(side="left")
        _tu_state = {"val": self._temp_unit}

        def _make_tu_btn(label, val):
            active = (_tu_state["val"] == val)
            b = tk.Label(_tu_settings_row, text=label, bg=BORDER,
                         fg=GREEN if active else SUBTEXT,
                         font=(_FONT, _BASE_FONT_SIZE - 2, "bold"),
                         cursor="hand2", padx=5, pady=2)
            b.pack(side="left", padx=(4, 0))
            def _click(e, v=val, btn=b):
                self._temp_unit = v
                _tu_state["val"] = v
                for child in _tu_settings_row.winfo_children():
                    if isinstance(child, tk.Label) and child != _tu_settings_row.winfo_children()[0]:
                        child.config(fg=SUBTEXT)
                btn.config(fg=GREEN)
                self._save_position()
            b.bind("<Button-1>", _click)
            b.bind("<Enter>",    lambda e, w=b: w.config(fg=ACCENT1))
            b.bind("<Leave>",    lambda e, w=b, v=val: w.config(
                fg=GREEN if _tu_state["val"] == v else SUBTEXT))
            _desc = ("Celsius — standard in most hardware monitoring tools."
                     if val == "C" else
                     "Fahrenheit — preferred in the US.")
            self._bind_tooltip(b,
                f"## TEMP UNIT  {label}\n"
                f"{_desc}\n"
                "Affects GPU active/high/low temperature displays.")

        for _tu_lbl, _tu_val in [("°C", "C"), ("°F", "F")]:
            _make_tu_btn(_tu_lbl, _tu_val)


    def _settings_gpu_sel(self, tog_grid, popup):
        # ── GPU Device selector (NVIDIA only, shown when >1 GPU detected) ────
        _nvidia_gpu_count = 0
        if NVIDIA_AVAILABLE:
            try:
                _nvidia_gpu_count = pynvml.nvmlDeviceGetCount()
            except Exception:
                pass

        if _nvidia_gpu_count > 1:
            gpu_sel_frame = tk.Frame(popup, bg=PANEL, padx=12, pady=4)
            gpu_sel_frame.pack(fill="x")
            tk.Label(gpu_sel_frame, text="GPU DEVICE", bg=PANEL, fg=TEXT,
                     font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), anchor="w").pack(side="left", padx=(0, 8))
            _gpu_idx_state = {"val": self._gpu_device_index}
            _gpu_btns2 = []

            def _make_gpu_btn(idx):
                try:
                    h2 = pynvml.nvmlDeviceGetHandleByIndex(idx)
                    n2 = pynvml.nvmlDeviceGetName(h2)
                    n2 = n2.decode() if isinstance(n2, bytes) else str(n2)
                    short = n2.replace("NVIDIA ", "").replace("GeForce ", "")[:16]
                except Exception:
                    short = f"GPU {idx}"
                active = (idx == _gpu_idx_state["val"])
                b = tk.Label(gpu_sel_frame, text=f"{idx}: {short}", bg=BORDER,
                             fg=GREEN if active else SUBTEXT,
                             font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), cursor="hand2",
                             padx=6, pady=2)
                b.pack(side="left", padx=(0, 4))
                _gpu_btns2.append(b)
                def _click_gpu(e, i=idx, btn=b):
                    self._gpu_device_index = i
                    _gpu_idx_state["val"] = i
                    self._reinit_nvidia_handle()
                    for bb in _gpu_btns2:
                        bb.config(fg=SUBTEXT)
                    btn.config(fg=GREEN)
                    self._save_position()
                b.bind("<Button-1>", _click_gpu)
                b.bind("<Enter>",    lambda e, btn=b: btn.config(fg=ACCENT1))
                b.bind("<Leave>",    lambda e, btn=b, i=idx: btn.config(
                    fg=GREEN if i == _gpu_idx_state["val"] else SUBTEXT))

            for _gi in range(_nvidia_gpu_count):
                _make_gpu_btn(_gi)

            tk.Frame(popup, bg=BORDER, height=1).pack(fill="x", padx=8, pady=(4, 6))


    def _settings_sections(self, tog_grid, popup):
        # ── Section visibility + drag-to-reorder ──────────────────────────────
        # Separator before sections label
        tk.Frame(tog_grid, bg=BORDER, height=1).grid(
            row=12, column=0, columnspan=3, sticky="ew", pady=(4,0))
        tk.Label(tog_grid, text="SECTIONS", bg=PANEL, fg=TEXT,
                 font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), anchor="w").grid(
                 row=13, column=0, columnspan=3, sticky="w", pady=(4,2))
        _SECTION_ROW_OFFSET = 14

        _hdr_map = {
            "gpu":     "_gpu_hdr",
            "cpu":     "_cpu_hdr",
            "mem":     "_mem_hdr",
            "net":     "_net_hdr",
            "disk":    "_disk_hdr",
            "storage": "_storage_hdr",
        }
        _label_map = {
            "gpu": "GPU", "cpu": "CPU",
            "mem": "MEMORY", "net": "NETWORK", "disk": "DISK", "storage": "STORAGE",
        }

        # Container for all draggable rows — same shared frame as toggles above
        rows_frame = tog_grid

        # Build one row per section in current order
        _rows = []   # list of {"key": key, "frame": f, "tog": tog, "state": state}

        def _build_rows():
            for r in _rows:
                for w in (r["handle"], r["name_lbl"], r["tog"]):
                    try: w.destroy()
                    except Exception: pass
            _rows.clear()
            for key in self._section_order:
                _add_row(key)

        def _add_row(key):
            visible = key not in self._hidden_sections
            state   = {"visible": visible, "key": key}

            row_idx = _SECTION_ROW_OFFSET + len(_rows)

            # Drag handle
            handle = tk.Label(rows_frame, text="⠿", bg=PANEL, fg=DIM,
                              font=(_FONT, _BASE_FONT_SIZE), cursor="fleur", padx=4)
            handle.grid(row=row_idx, column=0, sticky="w", pady=2, padx=(4,0))

            # Section name
            name_lbl = tk.Label(rows_frame, text=_label_map.get(key, key), bg=PANEL, fg=TEXT,
                     font=(_FONT, _BASE_FONT_SIZE - 2), anchor="w")
            name_lbl.grid(row=row_idx, column=1, sticky="ew", pady=2, padx=(4,0))

            # ON/OFF toggle — always in column 2, so all buttons align
            tog = tk.Label(rows_frame, text="ON" if visible else "OFF",
                           bg=BORDER, fg=GREEN if visible else SUBTEXT,
                           font=(_FONT, _BASE_FONT_SIZE - 2, "bold"),
                           cursor="hand2", padx=8, pady=2, width=4, anchor="center")
            tog.grid(row=row_idx, column=2, sticky="e", pady=2, padx=(0,4))


            _rows.append({"key": key, "handle": handle, "name_lbl": name_lbl, "tog": tog, "state": state})

            # Toggle click
            def _click(e, s=state, t=tog):
                k = s["key"]
                w = getattr(self, f"_section_wrapper_{k}", None)
                if s["visible"]:
                    self._hidden_sections.add(k)
                    s["visible"] = False
                    t.config(text="OFF", fg=SUBTEXT)
                    if w:
                        w.pack_forget()
                        w.grid_forget()
                else:
                    self._hidden_sections.discard(k)
                    s["visible"] = True
                    t.config(text="ON", fg=GREEN)
                self._apply_section_order()
                self.update_idletasks()
                self.geometry(f"{self.winfo_width()}x{self.winfo_reqheight()}")
                self._save_position()

            tog.bind("<Button-1>", _click)
            tog.bind("<Enter>", lambda e, t=tog, s=state: t.config(fg=ACCENT1))
            tog.bind("<Leave>", lambda e, t=tog, s=state: t.config(
                fg=GREEN if s["visible"] else SUBTEXT))
            self._bind_tooltip(tog,
                f"## {_label_map.get(key, key).upper()} SECTION\n"
                "Toggle this section's visibility in the overlay.\n"
                "ON = shown  ·  OFF = hidden from the overlay.")
            self._bind_tooltip(handle,
                "## ⠿  DRAG TO REORDER\n"
                f"Drag this handle up or down to move\n"
                f"the {_label_map.get(key, key)} section to a new position in the overlay.")

            # ── Drag-to-reorder on the handle ─────────────────────────────────
            drag = {"start_y": 0, "index": 0, "ghost": None}

            def _drag_start(e, s=state, r=handle):
                drag["start_y"] = e.y_root
                drag["index"]   = next(
                    (i for i, x in enumerate(_rows) if x["key"] == s["key"]), 0)
                # Ghost highlight
                for rd in _rows:
                    if rd["key"] == s["key"]:
                        for w in (rd["handle"], rd["name_lbl"], rd["tog"]):
                            try: w.config(bg=BORDER)
                            except Exception: pass

            def _drag_motion(e, s=state, r=handle):
                dy      = e.y_root - drag["start_y"]
                row_h   = max(r.winfo_height(), 20)
                steps   = int(dy / row_h)
                old_idx = drag["index"]
                new_idx = max(0, min(len(_rows) - 1, old_idx + steps))
                if new_idx != old_idx:
                    item = _rows.pop(old_idx)
                    _rows.insert(new_idx, item)
                    drag["index"]   = new_idx
                    drag["start_y"] = e.y_root
                    # Re-grid all widgets in new order
                    for i, rd in enumerate(_rows):
                        rd["handle"].grid(row=_SECTION_ROW_OFFSET + i, column=0)
                        rd["name_lbl"].grid(row=_SECTION_ROW_OFFSET + i, column=1)
                        rd["tog"].grid(row=_SECTION_ROW_OFFSET + i, column=2)
                    self._section_order = [rd["key"] for rd in _rows]
                    self._apply_section_order()

            def _drag_end(e, s=state, r=handle):
                for rd in _rows:
                    if rd["key"] == s["key"]:
                        for w in (rd["handle"], rd["name_lbl"], rd["tog"]):
                            try: w.config(bg=PANEL)
                            except Exception: pass
                self._section_order = [rd["key"] for rd in _rows]
                self._apply_section_order()
                self._save_position()

            handle.bind("<ButtonPress-1>",  _drag_start)
            handle.bind("<B1-Motion>",      _drag_motion)
            handle.bind("<ButtonRelease-1>", _drag_end)

        _build_rows()

        tk.Frame(popup, bg=BORDER, height=1).pack(fill="x", padx=8, pady=(4, 6))


    def _settings_importexport(self, tog_grid, popup):
        # ── Import / Export settings ──────────────────────────────────────────
        ie_row = tk.Frame(popup, bg=PANEL, padx=12, pady=4)
        ie_row.pack(fill="x")

        exp_btn = tk.Label(ie_row, text="EXPORT", bg=BORDER, fg=ACCENT2,
                           font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), cursor="hand2",
                           padx=10, pady=3)
        exp_btn.pack(side="left", padx=(0, 6))
        exp_btn.bind("<Enter>",    lambda e: exp_btn.config(fg=GREEN))
        exp_btn.bind("<Leave>",    lambda e: exp_btn.config(fg=ACCENT2))
        exp_btn.bind("<Button-1>", lambda e: self._export_settings())
        self._bind_tooltip(exp_btn,
            "## EXPORT SETTINGS\n"
            "Save all current colours, behaviour toggles, layout,\n"
            "and poll/log settings to a portable JSON file.\n"
            "Share or back up your exact configuration.")

        imp_btn = tk.Label(ie_row, text="IMPORT", bg=BORDER, fg=ACCENT2,
                           font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), cursor="hand2",
                           padx=10, pady=3)
        imp_btn.pack(side="left")
        imp_btn.bind("<Enter>",    lambda e: imp_btn.config(fg=GREEN))
        imp_btn.bind("<Leave>",    lambda e: imp_btn.config(fg=ACCENT2))
        imp_btn.bind("<Button-1>", lambda e: self._import_settings())
        self._bind_tooltip(imp_btn,
            "## IMPORT SETTINGS\n"
            "Load a previously exported settings JSON file.\n"
            "All colours, layout, and behaviour will be applied\n"
            "immediately and saved.")

        tk.Label(ie_row, text="all settings + theme", bg=PANEL, fg=SUBTEXT,
                 font=(_FONT, _BASE_FONT_SIZE - 3)).pack(side="left", padx=(8, 0))




    def _open_settings(self):
        if self._settings_popup and self._settings_popup.winfo_exists():
            self._settings_popup.destroy()
            self._settings_popup = None
            return
        if self._settings_pending:
            return
        self._settings_pending = True

        popup = self._make_popup()
        popup.configure(bg=PANEL)
        # registered in _settings_popup AFTER geometry is locked (see _finalize below)

        # ── Title bar ─────────────────────────────────────────────────────────
        _make_titlebar(popup, PANEL, BORDER, SUBTEXT, RED,
                       on_close=popup.destroy,
                       title_text="⚙ SETTINGS", title_fg=ACCENT2, title_bg=PANEL,
                       separator_color=ACCENT2)
        tk.Frame(popup, bg=PANEL, height=6).pack(fill="x")  # spacing below title bar

        # ── All settings rows — single outer frame so buttons align end-to-end ──
        all_rows = tk.Frame(popup, bg=PANEL, padx=12, pady=2)
        all_rows.pack(fill="x")
        all_rows.columnconfigure(1, weight=1)  # label column stretches; col2=button

        tog_grid = all_rows  # toggles use the shared frame directly

        self._settings_startup(tog_grid, popup)
        self._settings_aot(tog_grid, popup)
        self._settings_lock(tog_grid, popup)
        self._settings_clickthrough(tog_grid, popup)
        self._settings_tooltips(tog_grid, popup)
        self._settings_mtt(tog_grid, popup)
        self._settings_rates(tog_grid, popup)
        self._settings_tempunit(tog_grid, popup)
        self._settings_gpu_sel(tog_grid, popup)
        self._settings_sections(tog_grid, popup)
        self._settings_importexport(tog_grid, popup)
        # ── Position popup ────────────────────────────────────────────────────
        # Snapshot original (base-9) font sizes before any remapping
        _sp_originals = {}
        def _snapshot(w):
            try:
                f = w.cget("font")
                if isinstance(f, str):
                    f = list(self.tk.splitlist(f))
                else:
                    f = list(f)
                if len(f) >= 2 and f[0] == _FONT:
                    _sp_originals[id(w)] = (w, list(f))
            except Exception:
                pass
            for c in w.winfo_children():
                _snapshot(c)
        _snapshot(popup)
        popup._font_originals = _sp_originals

        # Apply fonts before measuring so the size is accurate
        self._apply_font_size(self._font_size, root=popup)
        popup.update_idletasks()
        pw = popup.winfo_reqwidth()
        ph = popup.winfo_reqheight()
        bx, by = self._popup_pos(pw, ph)
        popup.geometry(f"+{bx}+{by}")
        # Re-affirm position after tkinter finishes any pending layout passes,
        # then register and pin topmost so it stays above the main overlay
        def _finalize():
            self._settings_pending = False
            if not popup.winfo_exists():
                return
            popup.geometry(f"+{bx}+{by}")
            self._settings_popup = popup
            self._raise_popup("_settings_popup")
            popup.bind("<ButtonPress>", lambda e: self._raise_popup("_settings_popup"))
            self._pin_popup_topmost(popup)
        popup.after(30, _finalize)
        popup.focus_set()

    # ── Tooltip system ───────────────────────────────────────────────────────

    def _tooltip_allowed(self):
        """Tooltips only show if enabled in settings AND (Ctrl is held OR click-through is OFF)."""
        if not self._tooltips_enabled:
            return False
        return self._ctrl_ok()

    def _show_tooltip(self, widget, text):
        """Show a styled floating tooltip near the widget. Respects Ctrl / click-through gate."""
        self._cancel_tooltip()
        def _actually_show():
            if not self._tooltip_allowed():
                return
            if not widget.winfo_exists():
                return
            self._cancel_tooltip()  # destroy any previous
            tip = self._make_popup(alpha=0.96)
            tip.configure(bg=PANEL)
            self._tooltip_window = tip

            # Accent bar on left
            tk.Frame(tip, bg=ACCENT3, width=2).pack(side="left", fill="y")

            inner = tk.Frame(tip, bg=PANEL, padx=8, pady=6)
            inner.pack(side="left")

            _tip_fs = max(_BASE_FONT_SIZE - 2, self._font_size - 1)
            for i, line in enumerate(text.strip().split("\n")):
                line = line.strip()
                if not line:
                    continue
                if line.startswith("##"):
                    tk.Label(inner, text=line[2:].strip(), bg=PANEL, fg=ACCENT3,
                             font=(_FONT, _tip_fs, "bold"), anchor="w",
                             justify="left").pack(fill="x")
                elif line.startswith("#"):
                    tk.Label(inner, text=line[1:].strip(), bg=PANEL, fg=TEXT,
                             font=(_FONT, _tip_fs, "bold"), anchor="w",
                             justify="left").pack(fill="x")
                else:
                    tk.Label(inner, text=line, bg=PANEL, fg=SUBTEXT,
                             font=(_FONT, _tip_fs), anchor="w",
                             justify="left").pack(fill="x")

            tip.update_idletasks()
            tw = tip.winfo_reqwidth()
            th = tip.winfo_reqheight()
            # Position: try below widget, flip above if off-screen
            try:
                wx = widget.winfo_rootx()
                wy = widget.winfo_rooty()
                wh = widget.winfo_height()
                sw = self.winfo_screenwidth()
                sh = self.winfo_screenheight()
                x = wx
                y = wy + wh + 4
                if y + th > sh - 10:
                    y = wy - th - 4
                x = max(4, min(x, sw - tw - 4))
                tip.geometry(f"+{x}+{y}")
            except Exception:
                pass

        self._tooltip_after = self.after(520, _actually_show)

    def _hide_tooltip(self, event=None):
        self._cancel_tooltip()

    def _cancel_tooltip(self):
        if self._tooltip_after is not None:
            try: self.after_cancel(self._tooltip_after)
            except Exception: pass
            self._tooltip_after = None
        if self._tooltip_window is not None:
            try:
                if self._tooltip_window.winfo_exists():
                    self._tooltip_window.destroy()
            except Exception: pass
            self._tooltip_window = None

    def _bind_tooltip(self, widget, text):
        """Attach enter/leave tooltip bindings to a widget."""
        widget.bind("<Enter>", lambda e, w=widget, t=text: self._show_tooltip(w, t), add="+")
        widget.bind("<Leave>", self._hide_tooltip, add="+")

    def _show_tooltip_guide(self):
        """Show the stylish tooltip-guide intro popup (replaces old v1.7? help button)."""
        if self._ttguide_popup:
            try:
                if self._ttguide_popup.winfo_exists():
                    self._ttguide_popup.destroy()
                    return
            except Exception:
                pass

        popup = self._make_popup()
        popup.configure(bg=PANEL)
        self._ttguide_popup = popup
        self._raise_popup("_ttguide_popup")
        popup.bind("<ButtonPress>", lambda e: self._raise_popup("_ttguide_popup"))
        popup.after(30, lambda: self._pin_popup_topmost(popup) if popup.winfo_exists() else None)

        # Title bar
        _make_titlebar(popup, BORDER, BORDER, SUBTEXT, RED,
                       on_close=popup.destroy,
                       title_text="PyDisplay  ·  Quick Reference", title_fg=TEXT, title_bg=BORDER,
                       separator_color=ACCENT3)

        # Body
        body = tk.Frame(popup, bg=PANEL, padx=10, pady=6)
        body.pack(fill="both")

        def _section(title, color=ACCENT3):
            tk.Label(body, text=title, bg=PANEL, fg=color,
                     font=(_FONT, _BASE_FONT_SIZE - 1, "bold")).pack(anchor="w", pady=(8, 1))
            tk.Frame(body, bg=color, height=1).pack(fill="x", pady=(0, 4))

        def _row(label, desc, label_fg=TEXT):
            row = tk.Frame(body, bg=PANEL)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=label, bg=PANEL, fg=label_fg,
                     font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), width=18, anchor="w").pack(side="left")
            tk.Label(row, text=desc, bg=PANEL, fg=SUBTEXT,
                     font=(_FONT, _BASE_FONT_SIZE - 2), anchor="w", justify="left").pack(side="left", padx=(6, 0))

        # Intro blurb
        intro = tk.Frame(body, bg=BORDER, padx=10, pady=8)
        intro.pack(fill="x", pady=(0, 6))
        tk.Label(intro, bg=BORDER, fg=ACCENT3,
                 text="✦  Hover any button or stat to see a tooltip",
                 font=(_FONT, _BASE_FONT_SIZE - 1, "bold")).pack(anchor="w")
        tk.Label(intro, bg=BORDER, fg=SUBTEXT,
                 text="Tooltips only appear when you can interact —\n"
                      "hold  Ctrl  or turn off Click-Through in Settings.\n"
                      "Not seeing any?  Make sure  Show Tooltips  is ON in ⚙ Settings.",
                 font=(_FONT, _BASE_FONT_SIZE - 2), justify="left").pack(anchor="w", pady=(2, 0))

        _section("  ·  Controls")
        _row("Ctrl + drag",        "Move or resize the overlay")
        _row("Ctrl + click hdr",   "Collapse / expand a section")
        _row("Ctrl + click stat",  "Bust storage cache / open tools")
        _row("Click-Through OFF",  "Always interactive — no Ctrl needed")

        _section("  ·  Main Buttons")
        _row("◉ LOG",              "Toggle CSV session logging")
        _row("◈ THEME",            "Open colour picker & theme manager")
        _row("⚙ SETTINGS",         "Toggle behaviours, poll rate, layout")
        _row("?",                  "This reference panel (you're here!)")

        _section("  ·  Sections")
        _row("▸ GPU",              "Load · VRAM · Temp · Wattage")
        _row("▸ CPU",              "Load · Frequency · Processes")
        _row("▸ MEMORY",           "RAM usage · Peak tracking · Memory Cleaner")
        _row("▸ NETWORK",          "Down/Up speed · Tools dropdown")
        _row("▸ DISK",             "Read/Write I/O · Peak tracking")
        _row("▸ STORAGE",          "Per-drive usage · Ctrl-click to refresh")

        _section("  ·  Network Tools")
        _row("▶ TOOLS",            "Expand speed test & IP lookup")
        _row("▶ SPEED TEST",       "Native speed test — ping, down, up (no browser)")
        _row("⌖ IP LOOKUP",        "Fetch & display your public IP info")

        _section("  ·  Memory Tools")
        _row("▶ TOOLS",            "Expand the Memory Cleaner dropdown")
        _row("🧹 MEMORY CLEAN",    "Open the Memory Cleaner popup")
        _row("Safe Clean",         "Trims working sets, flushes modified pages & caches\n"
                                   "                   — safe for games & browsers")
        _row("Aggressive Clean",   "All Safe steps + standby list purge & memory\n"
                                   "                   compression — may cause brief stutter")

        _section("  ·  Settings")
        _row("START WITH WINDOWS", "Add / remove Windows startup entry")
        _row("ALWAYS ON TOP",      "Keep overlay above all windows")
        _row("LOCK POSITION",      "Prevent accidental drag / resize")
        _row("CLICK-THROUGH",      "Pass clicks through — use Ctrl to interact")
        _row("MINIMIZE TO TRAY",   "Hide to tray icon instead of closing")
        _row("POLL RATE",          "How often stats refresh (0.5 – 5 s)")
        _row("LOG EVERY",          "How often a log line is written")
        _row("TEMP UNIT",          "°C or °F for GPU/CPU temperatures")
        _row("EXPORT / IMPORT",    "Portable settings + theme JSON backup")

        _section("  ·  Theme Picker")
        _row("Colour swatches",    "Click any swatch to pick a new colour")
        _row("↺  reset arrow",     "Restore that slot to its default colour")
        _row("Dropdown Tools",     "One colour swatch controls ALL tool buttons\n"
                                   "                   across Network & Memory dropdowns")
        _row("SAVE THEME",         "Save current colours as a named theme")
        _row("RESET THEME",        "Revert everything to the Default theme")

        self._apply_font_size(self._font_size, root=popup)
        popup.update_idletasks()
        pw = popup.winfo_reqwidth()
        ph = popup.winfo_reqheight()
        px, py = self._popup_pos(pw, ph)
        popup.geometry(f"+{px}+{py}")
        popup.after(20, lambda: popup.geometry(f"+{px}+{py}") if popup.winfo_exists() else None)


    def _build_topbar(self):
        # ── Custom title bar with X — matches page 1 & 2 exactly ───────────────
        _main_tb = tk.Frame(self, bg=BG, height=28)
        _main_tb.pack(fill="x")
        _main_tb.pack_propagate(False)
        def _x_label():
            return " HIDE" if self._minimize_to_tray else " ✕ "
        _hide_mode = self._minimize_to_tray
        self._close_btn = tk.Label(_main_tb, text=_x_label(), bg=BORDER, fg=SUBTEXT,
                                   font=(_FONT, _BASE_FONT_SIZE - 2 if _hide_mode else _BASE_FONT_SIZE, "bold"), cursor="hand2", padx=6, pady=2)
        self._close_btn.pack(side="left", padx=(12, 0), pady=(6, 0))
        def _close_click(e):
            if self._minimize_to_tray:
                self._hide_to_tray()
            else:
                self._on_close()
        self._close_btn.bind("<Button-1>", _close_click)
        self._close_btn.bind("<Enter>", lambda e: self._close_btn.config(fg=RED))
        self._close_btn.bind("<Leave>", lambda e: self._close_btn.config(fg=SUBTEXT))
        # Only show tooltip when in HIDE (tray) mode — the X is self-explanatory
        if _hide_mode:
            self._bind_tooltip(self._close_btn,
                "## HIDE TO TRAY\n"
                "Hides the overlay and puts an icon in the system tray.\n"
                "Right-click the tray icon to restore or quit PyDisplay.")

        # PyDisplay label + help button — right side of title bar, vertically centred
        _tb_right = tk.Frame(_main_tb, bg=BG)
        _tb_right.pack(side="right", padx=(0, 6))
        _pd_row = tk.Frame(_tb_right, bg=BG)
        _pd_row.pack(anchor="e")
        tk.Label(_pd_row, text="PyDisplay", bg=BG, fg=GREEN,
                 font=(_FONT, _BASE_FONT_SIZE, "bold")).pack(side="left")
        tk.Label(_pd_row, text=f" - v{_APP_VERSION}", bg=BG, fg=SUBTEXT,
                 font=(_FONT, _BASE_FONT_SIZE - 2)).pack(side="left")

        # Datetime label for TOP position — sits in drag spacer between ✕ and PyDisplay
        self._time_top_lbl = tk.Label(_main_tb, text="", bg=BG, fg=SUBTEXT,
                                      font=(_FONT, _BASE_FONT_SIZE - 2), anchor="center")
        # packed dynamically by _update_datetime_lbl

        top = tk.Frame(self, bg=BG)
        top.pack(fill="x", padx=12, pady=(3, 0))

        self._log_lbl = tk.Label(top, text="◉ LOG", bg=BORDER, fg=SUBTEXT,
                                  font=(_FONT, _BASE_FONT_SIZE, "bold"), cursor="hand2",
                                  padx=6, pady=2, relief="flat", width=7, anchor="center")
        self._log_lbl.pack(side="left", padx=(0, 4))
        self._log_lbl.bind("<Enter>", self._log_enter)
        self._log_lbl.bind("<Leave>", self._log_leave)
        self._log_lbl.bind("<Button-1>", lambda e: self._toggle_log() if self._ctrl_ok(e.state) else None)
        self._bind_tooltip(self._log_lbl,
            "## ◉ LOG\n"
            "Toggle live session logging to a text file.\n"
            "Red = recording · Grey = idle.\n"
            "File saved to %APPDATA%\\PyDisplay\\PyDisplay_log.txt\n"
            "# Ctrl+Click to activate")

        self._theme_lbl = tk.Label(top, text="◈ THEME", bg=BORDER, fg=SUBTEXT,
                                    font=(_FONT, _BASE_FONT_SIZE, "bold"), cursor="hand2",
                                    padx=6, pady=2, relief="flat")
        self._theme_lbl.pack(side="left", padx=(0, 4))
        self._theme_lbl.bind("<Enter>", lambda e: self._theme_lbl.config(bg=ACCENT3, fg=BG))
        self._theme_lbl.bind("<Leave>", lambda e: self._theme_lbl.config(bg=BORDER, fg=SUBTEXT))
        self._theme_lbl.bind("<Button-1>", lambda e: self._open_color_picker() if self._ctrl_ok(e.state) else None)
        self._bind_tooltip(self._theme_lbl,
            "## ◈ THEME\n"
            "Open the colour picker to customise every\n"
            "section accent, background, and text colour.\n"
            "Save named themes and switch between them.\n"
            "# Ctrl+Click to open")

        self._settings_lbl = tk.Label(top, text="⚙ SETTINGS", bg=BORDER, fg=ACCENT2,
                                       font=(_FONT, _BASE_FONT_SIZE, "bold"), cursor="hand2",
                                       padx=6, pady=2, relief="flat")
        self._settings_lbl.pack(side="left", padx=(0, 0))
        self._settings_lbl.bind("<Enter>", lambda e: self._settings_lbl.config(bg=ACCENT2, fg=BG))
        self._settings_lbl.bind("<Leave>", lambda e: self._settings_lbl.config(bg=BORDER, fg=ACCENT2))
        self._settings_lbl.bind("<Button-1>", lambda e: self._open_settings() if self._ctrl_ok(e.state) else None)
        self._bind_tooltip(self._settings_lbl,
            "## ⚙ SETTINGS\n"
            "Toggle always-on-top, click-through, lock position,\n"
            "tray mode, poll rate, log interval, temp unit,\n"
            "section visibility & order, and import/export.\n"
            "# Ctrl+Click to open")

        self._help_lbl = tk.Label(top, text="HELP?", bg=BORDER, fg=ACCENT3,
                                   font=(_FONT, _BASE_FONT_SIZE, "bold"), cursor="hand2",
                                   padx=6, pady=2, relief="flat")
        self._help_lbl.pack(side="left", padx=(4, 0))
        self._help_lbl.bind("<Enter>", lambda e: self._help_lbl.config(bg=ACCENT3, fg=BG))
        self._help_lbl.bind("<Leave>", lambda e: self._help_lbl.config(bg=BORDER, fg=ACCENT3))
        self._help_lbl.bind("<Button-1>", lambda e: self._show_tooltip_guide() if self._ctrl_ok(e.state) else None)
        self._bind_tooltip(self._help_lbl,
            "## ?  Quick Reference\n"
            "Opens a handy cheat-sheet listing every button,\n"
            "section, and keyboard shortcut in PyDisplay.\n"
            "# Ctrl+Click to open")

        def _resize_top_btns(e=None):
            try:
                # Measure HELP? width using a temporary label so we don't
                # flicker the real button or get stale reqwidth readings
                tmp = tk.Label(top, text="HELP?",
                               font=self._help_lbl.cget("font"),
                               padx=int(self._help_lbl.cget("padx")),
                               pady=int(self._help_lbl.cget("pady")))
                top.update_idletasks()
                help_full_w = tmp.winfo_reqwidth()
                tmp.destroy()

                total = (self._log_lbl.winfo_reqwidth() +
                         self._theme_lbl.winfo_reqwidth() +
                         self._settings_lbl.winfo_reqwidth() +
                         help_full_w + 12)  # 3×4px gaps

                if total > top.winfo_width():
                    self._help_lbl.config(text="?")
                else:
                    self._help_lbl.config(text="HELP?")
            except Exception:
                pass
        self._resize_top_btns = _resize_top_btns
        top.bind("<Configure>", _resize_top_btns)

        outer = tk.Frame(self, bg=BG, padx=12)
        outer.pack(fill="x", pady=(1, 4))
        self._outer = outer


    def _build_gpu(self):
        # ── GPU ───────────────────────────────────────────────────────────────
        gf, self._gpu_title, self._gpu_hdr = self._section(self._outer, "▸ GPU", self._color_gpu, "gpu")
        self._bind_tooltip(self._gpu_hdr._arrow,
            "## ▸ GPU\n"
            "Collapse or expand the GPU section.\n"
            "# Ctrl+Click to toggle")
        self._bind_tooltip(self._gpu_hdr._title,
            "## GPU Section\n"
            "Tracks your graphics card in real time:\n"
            "load, VRAM usage, temperature, and wattage.\n"
            "Supports NVIDIA (pynvml), AMD, and Intel Arc.")
        gpu_name_row = tk.Frame(gf, bg=PANEL)
        gpu_name_row.pack(fill="x", pady=(0, 4))
        self.gpu_name = tk.Label(gpu_name_row, text="Detecting...", bg=PANEL, fg=SUBTEXT,
                                  font=(_FONT, _BASE_FONT_SIZE - 2), anchor="w")
        self.gpu_name.pack(side="left")
        self._bind_tooltip(self.gpu_name,
            "## GPU Model\n"
            "Your detected graphics card name.\n"
            "Auto-detected on startup via pynvml or WMI.")
        # ── NVIDIA Drivers link ───────────────────────────────────────────────
        self._drv_btn = tk.Label(
            gpu_name_row, text="↗ ...", bg=BORDER, fg="#9999cc",
            font=(_FONT, _BASE_FONT_SIZE - 3, "bold"), cursor="arrow", padx=5, pady=2
        )
        self._drv_btn.pack(side="right")
        self._drv_btn.bind("<Button-1>",
            lambda e: self._ctrl_ok(e.state) and __import__("webbrowser").open(
                "https://www.nvidia.com/en-us/drivers/") or None)
        self._drv_btn.bind("<Enter>",
            lambda e: self._drv_btn.config(fg=GREEN, cursor="hand2") if self._ctrl_ok(e.state) else None)
        self._drv_btn.bind("<Leave>",
            lambda e: self._drv_btn.config(fg="#9999cc", cursor="arrow"))
        self._bind_tooltip(self._drv_btn,
            "## ↗ NVIDIA DRIVERS\n"
            "Shows your current driver version.\n"
            "Click to open the NVIDIA driver download page.\n"
            "# Ctrl+Click to open in browser")
        # Fetch driver version once in background and update label
        def _fetch_driver():
            ver = None
            try:
                import pynvml as _nv
                _nv.nvmlInit()
                v = _nv.nvmlSystemGetDriverVersion()
                ver = v.decode() if isinstance(v, bytes) else str(v)
                _nv.nvmlShutdown()
            except Exception:
                pass
            lbl = f"↗ {ver}" if ver else "↗ DRIVERS"
            self.after(0, lambda: self._drv_btn.config(text=lbl))
        threading.Thread(target=_fetch_driver, daemon=True).start()
        self.gpu_usage = MiniBar(gf, "LOAD", self._color_gpu, "%", 100)
        self.gpu_usage.pack(fill="x", pady=2)
        self._bind_tooltip(self.gpu_usage,
            "## GPU LOAD\n"
            "Current GPU utilisation as a percentage.\n"
            "Bar turns yellow >60%, red >85%.")
        self.vram_bar = MiniBar(gf, "VRAM", self._color_gpu, "GB", 1)  # max_val set on first poll
        self.vram_bar.pack(fill="x", pady=2)
        self._bind_tooltip(self.vram_bar,
            "## VRAM\n"
            "Video memory used vs total (GB).\n"
            "High VRAM usage can cause stuttering in games.")
        gr = tk.Frame(gf, bg=PANEL)
        gr.pack(fill="x", pady=(4, 0))
        for col in range(4):
            gr.columnconfigure(col, weight=1)
        for col, txt in enumerate(["POWER", "ACTIVE °C", "HIGH °C", "LOW °C"]):
            tk.Label(gr, text=txt, bg=PANEL, fg=SUBTEXT,
                     font=(_FONT, _BASE_FONT_SIZE - 3, "bold"), anchor="center").grid(
                     row=0, column=col, sticky="ew", padx=2)
        self.gpu_watt_lbl     = tk.Label(gr, text="—", bg=PANEL, fg=self._color_gpu,
                                          font=(_FONT, _BASE_FONT_SIZE, "bold"), anchor="center")
        self.gpu_watt_lbl.grid(row=1, column=0, sticky="ew", padx=2)
        self._bind_tooltip(self.gpu_watt_lbl,
            "## GPU POWER\n"
            "Current GPU power draw in watts.\n"
            "Requires NVIDIA pynvml or OHM/LHM for AMD.")
        self.gpu_temp_lbl     = tk.Label(gr, text="—", bg=PANEL, fg=self._color_gpu,
                                          font=(_FONT, _BASE_FONT_SIZE, "bold"), anchor="center")
        self.gpu_temp_lbl.grid(row=1, column=1, sticky="ew", padx=2)
        self._bind_tooltip(self.gpu_temp_lbl,
            "## GPU ACTIVE TEMP\n"
            "Current GPU core temperature.\n"
            "Unit can be changed to °F in Settings.")
        self.gpu_temp_max_lbl = tk.Label(gr, text="—", bg=PANEL, fg=self._color_gpu,
                                          font=(_FONT, _BASE_FONT_SIZE, "bold"), anchor="center")
        self.gpu_temp_max_lbl.grid(row=1, column=2, sticky="ew", padx=2)
        self._bind_tooltip(self.gpu_temp_max_lbl,
            "## GPU HIGH TEMP\n"
            "Highest temperature recorded this session.\n"
            "Resets when PyDisplay restarts.")
        self.gpu_temp_min_lbl = tk.Label(gr, text="—", bg=PANEL, fg=self._color_gpu,
                                          font=(_FONT, _BASE_FONT_SIZE, "bold"), anchor="center")
        self.gpu_temp_min_lbl.grid(row=1, column=3, sticky="ew", padx=2)
        self._bind_tooltip(self.gpu_temp_min_lbl,
            "## GPU LOW TEMP\n"
            "Lowest temperature recorded this session.\n"
            "Resets when PyDisplay restarts.")


    def _build_cpu(self):
        # ── CPU ───────────────────────────────────────────────────────────────
        cf, self._cpu_title, self._cpu_hdr = self._section(self._outer, "▸ CPU", self._color_cpu, "cpu")
        self._bind_tooltip(self._cpu_hdr._arrow,
            "## ▸ CPU\n"
            "Collapse or expand the CPU section.\n"
            "# Ctrl+Click to toggle")
        self._bind_tooltip(self._cpu_hdr._title,
            "## CPU Section\n"
            "Tracks processor load, real-time clock speed,\n"
            "process/thread/handle counts this session.")
        # Run WMI on a thread with a timeout — wmi.WMI() can block indefinitely
        # on startup if the WMI service is slow, which would freeze the UI.
        cpu_name = "CPU"
        try:
            import platform
            cpu_name = platform.processor()[:44] or "CPU"
        except Exception:
            pass
        try:
            import wmi as _wmi
            import pythoncom as _pcom
            _cpu_result = [cpu_name]
            def _get_cpu_name():
                try:
                    _pcom.CoInitialize()
                    _w = _wmi.WMI()
                    _cpu_result[0] = _w.Win32_Processor()[0].Name.strip()[:44]
                except Exception:
                    pass
                finally:
                    try: _pcom.CoUninitialize()
                    except Exception: pass
            _t = threading.Thread(target=_get_cpu_name, daemon=True)
            _t.start()
            _t.join(timeout=3.0)
            cpu_name = _cpu_result[0]
        except Exception:
            pass
        tk.Label(cf, text=cpu_name, bg=PANEL, fg=SUBTEXT,
                 font=(_FONT, _BASE_FONT_SIZE - 2), anchor="w").pack(fill="x", pady=(0, 4))
        self.cpu_usage = MiniBar(cf, "LOAD", self._color_cpu, "%", 100)
        self.cpu_usage.pack(fill="x", pady=2)
        self._bind_tooltip(self.cpu_usage,
            "## CPU LOAD\n"
            "Overall CPU utilisation across all cores.\n"
            "Bar turns yellow >60%, red >85%.")
        cr = tk.Frame(cf, bg=PANEL)
        cr.pack(fill="x", pady=(4, 0))
        self.cpu_freq_max_lbl = self._pill(cr, "MAX GHz",   self._color_cpu)
        self._bind_tooltip(self.cpu_freq_max_lbl,
            "## MAX GHz\n"
            "Highest CPU frequency seen this session (GHz).\n"
            "Measured via WMI PercentProcessorPerformance.")
        self.cpu_freq_min_lbl = self._pill(cr, "MIN GHz",   self._color_cpu)
        self._bind_tooltip(self.cpu_freq_min_lbl,
            "## MIN GHz\n"
            "Lowest CPU frequency seen this session (GHz).\n"
            "Useful for spotting thermal throttling.")
        self.cpu_proc_lbl     = self._pill(cr, "PROCESSES", self._color_cpu)
        self._bind_tooltip(self.cpu_proc_lbl,
            "## PROCESSES\n"
            "Total number of running processes.\n"
            "Updated every ~2 minutes (cached for performance).")
        self.cpu_thread_lbl   = self._pill(cr, "THREADS",   self._color_cpu)
        self._bind_tooltip(self.cpu_thread_lbl,
            "## THREADS\n"
            "Total active threads across all processes.\n"
            "Updated every ~2 minutes.")
        self.cpu_handle_lbl   = self._pill(cr, "HANDLES",   self._color_cpu)
        self._bind_tooltip(self.cpu_handle_lbl,
            "## HANDLES\n"
            "Total open OS handles (files, sockets, etc.).\n"
            "Very high handle counts can indicate a leak.")


    def _build_mem(self):
        # ── MEMORY ────────────────────────────────────────────────────────────
        mf, self._mem_title, self._mem_hdr = self._section(self._outer, "▸ MEMORY", self._color_mem, "mem")
        self._bind_tooltip(self._mem_hdr._arrow,
            "## ▸ MEMORY\n"
            "Collapse or expand the RAM section.\n"
            "# Ctrl+Click to toggle")
        self._bind_tooltip(self._mem_hdr._title,
            "## MEMORY Section\n"
            "Tracks system RAM usage, total capacity,\n"
            "current percent, and peak session usage.")
        ram = psutil.virtual_memory()
        ram_total_gb = round(ram.total / (1024**3))
        self.ram_bar = MiniBar(mf, "RAM", self._color_mem, "GB", ram_total_gb)
        self.ram_bar.pack(fill="x", pady=2)
        self._bind_tooltip(self.ram_bar,
            "## RAM USAGE\n"
            "Current physical memory used vs installed total (GB).\n"
            "Bar turns yellow >60%, red >85%.")
        mr = tk.Frame(mf, bg=PANEL)
        mr.pack(fill="x", pady=(4, 0))
        for col in range(4):
            mr.columnconfigure(col, weight=1)
        for col, txt in enumerate(["USAGE", "USED", "TOTAL", "PEAK"]):
            tk.Label(mr, text=txt, bg=PANEL, fg=SUBTEXT,
                     font=(_FONT, _BASE_FONT_SIZE - 3, "bold"), anchor="center").grid(
                     row=0, column=col, sticky="ew", padx=2)
        self.ram_pct_lbl   = tk.Label(mr, text="—", bg=PANEL, fg=self._color_mem,
                                       font=(_FONT, _BASE_FONT_SIZE, "bold"), anchor="center")
        self.ram_pct_lbl.grid(row=1, column=0, sticky="ew", padx=2)
        self._bind_tooltip(self.ram_pct_lbl,
            "## RAM USAGE %\n"
            "Current RAM utilisation as a percentage.")
        self.ram_used_lbl  = tk.Label(mr, text="—", bg=PANEL, fg=self._color_mem,
                                       font=(_FONT, _BASE_FONT_SIZE, "bold"), anchor="center")
        self.ram_used_lbl.grid(row=1, column=1, sticky="ew", padx=2)
        self._bind_tooltip(self.ram_used_lbl,
            "## RAM USED\n"
            "How many gigabytes of RAM are currently in use.")
        self.ram_total_lbl = tk.Label(mr, text="—", bg=PANEL, fg=self._color_mem,
                                       font=(_FONT, _BASE_FONT_SIZE, "bold"), anchor="center")
        self.ram_total_lbl.grid(row=1, column=2, sticky="ew", padx=2)
        self._bind_tooltip(self.ram_total_lbl,
            "## RAM TOTAL\n"
            "Total installed system RAM in gigabytes.")
        self.ram_peak_lbl  = tk.Label(mr, text="—", bg=PANEL, fg=self._color_mem,
                                       font=(_FONT, _BASE_FONT_SIZE, "bold"), anchor="center")
        self.ram_peak_lbl.grid(row=1, column=3, sticky="ew", padx=2)
        self._bind_tooltip(self.ram_peak_lbl,
            "## RAM PEAK\n"
            "Highest RAM usage percentage seen this session.\n"
            "Resets when PyDisplay restarts.")

        # ── Memory Tools dropdown (Memory Cleaner) ────────────────────────────
        mem_tools_drop = tk.Frame(mf, bg=BORDER, padx=4, pady=2)

        def _toggle_mem_tools(e=None):
            if not self._ctrl_ok(e.state if e else 0):
                return
            if mem_tools_drop.winfo_ismapped():
                mem_tools_drop.pack_forget()
                mem_tools_btn.config(text="▶ TOOLS", fg="#9999cc")
            else:
                mem_tools_drop.pack(fill="x", pady=(2, 0))
                mem_tools_btn.config(text="▼ TOOLS", fg=GREEN)
            self.update_idletasks()
            self.geometry(f"{self.winfo_width()}x{self.winfo_reqheight()}")

        mr.columnconfigure(4, weight=0)
        mem_tools_btn = tk.Label(
            mr, text="▶ TOOLS", bg=BORDER, fg="#9999cc",
            font=(_FONT, _BASE_FONT_SIZE - 3, "bold"), cursor="arrow", padx=5, pady=2
        )
        mem_tools_btn.grid(row=0, column=4, rowspan=2, sticky="e", padx=(4, 0))
        mem_tools_btn.bind("<Button-1>", _toggle_mem_tools)
        mem_tools_btn.bind("<Enter>",  lambda e: mem_tools_btn.config(fg=GREEN, cursor="hand2") if self._ctrl_ok(e.state) else None)
        mem_tools_btn.bind("<Leave>",  lambda e: mem_tools_btn.config(fg="#9999cc", cursor="arrow"))
        self._bind_tooltip(mem_tools_btn,
            "## ▶ TOOLS\n"
            "Expand memory tools: Safe Clean and Aggressive Clean.\n"
            "# Ctrl+Click to expand / collapse")

        clean_btn = self._mem_clean_btn = tk.Label(
            mem_tools_drop, text="🧹 MEMORY CLEAN", bg=BORDER, fg=self._color_tools,
            font=(_FONT, _BASE_FONT_SIZE - 3, "bold"), cursor="arrow", padx=5, pady=2
        )
        clean_btn.pack(fill="x")
        clean_btn.bind("<Button-1>", lambda e: self._mem_clean_popup() if self._ctrl_ok(e.state) else None)
        clean_btn.bind("<Enter>",   lambda e: clean_btn.config(fg=GREEN, cursor="hand2") if self._ctrl_ok(e.state) else None)
        clean_btn.bind("<Leave>",   lambda e: clean_btn.config(fg=self._color_tools, cursor="arrow"))
        self._bind_tooltip(clean_btn,
            "## 🧹 MEMORY CLEAN\n"
            "Opens the memory cleaner. Choose between\n"
            "Safe Clean (gaming-friendly) or Aggressive Clean\n"
            "(maximum recovery, may stutter in games).\n"
            "# Ctrl+Click to open")


    def _mem_clean_popup(self):
        """Open the memory cleaner popup with Safe / Aggressive mode selection inside."""
        # If already open, raise it
        if self._mem_safe_popup and self._mem_safe_popup.winfo_exists():
            self._mem_safe_popup.lift()
            return

        _mode      = {"aggressive": False}   # current selected mode
        _running   = {"v": False}
        _spin_job  = [None]
        _spin_chars = ["◐", "◓", "◑", "◒"]
        _spin_state = {"idx": 0}

        dlg = self._make_popup()
        dlg.configure(bg=PANEL)
        self._mem_safe_popup = dlg          # reuse one attr — only one popup now
        self._raise_popup("_mem_safe_popup")
        dlg.bind("<ButtonPress>", lambda e: self._raise_popup("_mem_safe_popup"))
        dlg.after(30, lambda: self._pin_popup_topmost(dlg) if dlg.winfo_exists() else None)

        def _close_popup():
            try:
                if self._mem_clean_btn:
                    self._mem_clean_btn.config(text="🧹 MEMORY CLEAN",
                                               fg=self._color_tools, cursor="arrow")
            except Exception:
                pass
            if dlg.winfo_exists():
                dlg.destroy()

        # ── Title bar ─────────────────────────────────────────────────────────
        _make_titlebar(dlg, PANEL, BORDER, SUBTEXT, RED,
                       on_close=_close_popup,
                       title_text="🧹 MEMORY CLEAN", title_fg=self._color_tools,
                       title_bg=PANEL, separator_color=self._color_tools)

        # ── Inner content ─────────────────────────────────────────────────────
        inner = tk.Frame(dlg, bg=PANEL, padx=16, pady=10)
        inner.pack(fill="x")

        # ── Mode selector (Safe / Aggressive) ─────────────────────────────────
        mode_row = tk.Frame(inner, bg=PANEL)
        mode_row.pack(fill="x", pady=(0, 8))

        def _set_mode(aggressive):
            _mode["aggressive"] = aggressive
            if aggressive:
                safe_sel.config(fg=SUBTEXT, bg=BORDER)
                aggr_sel.config(fg=self._color_tools, bg=DIM)
            else:
                safe_sel.config(fg=self._color_tools, bg=DIM)
                aggr_sel.config(fg=SUBTEXT, bg=BORDER)

        safe_sel = tk.Label(mode_row, text="⚡ SAFE", bg=DIM, fg=self._color_tools,
                            font=(_FONT, _BASE_FONT_SIZE - 2, "bold"),
                            cursor="hand2", padx=8, pady=3)
        safe_sel.pack(side="left", padx=(0, 4))
        safe_sel.bind("<Button-1>", lambda e: _set_mode(False) if not _running["v"] else None)
        safe_sel.bind("<Enter>",    lambda e: safe_sel.config(fg=GREEN) if not _running["v"] else None)
        safe_sel.bind("<Leave>",    lambda e: safe_sel.config(fg=self._color_tools if not _mode["aggressive"] else SUBTEXT))

        aggr_sel = tk.Label(mode_row, text="🔥 AGGRESSIVE", bg=BORDER, fg=SUBTEXT,
                            font=(_FONT, _BASE_FONT_SIZE - 2, "bold"),
                            cursor="hand2", padx=8, pady=3)
        aggr_sel.pack(side="left")
        aggr_sel.bind("<Button-1>", lambda e: _set_mode(True) if not _running["v"] else None)
        aggr_sel.bind("<Enter>",    lambda e: aggr_sel.config(fg=GREEN) if not _running["v"] else None)
        aggr_sel.bind("<Leave>",    lambda e: aggr_sel.config(fg=self._color_tools if _mode["aggressive"] else SUBTEXT))

        # Mode description label
        mode_desc = tk.Label(inner, text="Gaming-friendly. No stutters.", bg=PANEL, fg=SUBTEXT,
                             font=(_FONT, _BASE_FONT_SIZE - 3), anchor="w")
        mode_desc.pack(fill="x", pady=(0, 6))

        def _update_desc():
            if _mode["aggressive"]:
                mode_desc.config(text="⚠ Purges standby list. May stutter in games.")
            else:
                mode_desc.config(text="Gaming-friendly. No stutters.")

        # Patch _set_mode to also update desc
        _orig_set_mode = _set_mode
        def _set_mode(aggressive):
            _orig_set_mode(aggressive)
            _update_desc()
        safe_sel.bind("<Button-1>", lambda e: _set_mode(False) if not _running["v"] else None)
        aggr_sel.bind("<Button-1>", lambda e: _set_mode(True)  if not _running["v"] else None)

        tk.Frame(inner, bg=BORDER, height=1).pack(fill="x", pady=(0, 8))

        # ── Spinner / status ──────────────────────────────────────────────────
        spin_lbl = tk.Label(inner, text="–  Ready", bg=PANEL, fg=SUBTEXT,
                            font=(_FONT, _BASE_FONT_SIZE - 1, "bold"))
        spin_lbl.pack(pady=(0, 8))

        # ── Result rows ───────────────────────────────────────────────────────
        _fields = {}
        for label, key, color in [
            ("Before", "before", SUBTEXT),
            ("After",  "after",  self._color_tools),
            ("Freed",  "freed",  GREEN),
        ]:
            row = tk.Frame(inner, bg=PANEL)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=f"{label}:", bg=PANEL, fg=SUBTEXT,
                     font=(_FONT, _BASE_FONT_SIZE - 2), anchor="e", width=8).pack(side="left")
            val = tk.Label(row, text="—", bg=PANEL, fg=color,
                           font=(_FONT, _BASE_FONT_SIZE, "bold"), anchor="w")
            val.pack(side="left", padx=(4, 0))
            _fields[key] = val

        # ── Bottom bar ────────────────────────────────────────────────────────
        tk.Frame(dlg, bg=BORDER, height=1).pack(fill="x", padx=8, pady=(8, 0))
        btn_row = tk.Frame(dlg, bg=PANEL, pady=6)
        btn_row.pack(fill="x", padx=12)

        run_btn = tk.Label(btn_row, text="RUN", bg=BORDER, fg=GREEN,
                           font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), cursor="hand2",
                           padx=10, pady=3)
        run_btn.pack(side="left")
        run_btn.bind("<Enter>", lambda e: run_btn.config(fg=ACCENT1) if run_btn.cget("text") in ("RUN", "RE-RUN") else None)
        run_btn.bind("<Leave>", lambda e: run_btn.config(fg=GREEN)   if run_btn.cget("text") in ("RUN", "RE-RUN") else None)

        close_btn = tk.Label(btn_row, text="CLOSE", bg=BORDER, fg=SUBTEXT,
                             font=(_FONT, _BASE_FONT_SIZE - 2, "bold"), cursor="hand2",
                             padx=10, pady=3)
        close_btn.pack(side="right")
        close_btn.bind("<Button-1>", lambda e: _close_popup())
        close_btn.bind("<Enter>",    lambda e: close_btn.config(fg=RED))
        close_btn.bind("<Leave>",    lambda e: close_btn.config(fg=SUBTEXT))

        # ── Spinner tick ──────────────────────────────────────────────────────
        def _tick():
            if not dlg.winfo_exists() or not _running["v"]:
                return
            _spin_state["idx"] = (_spin_state["idx"] + 1) % len(_spin_chars)
            ch  = _spin_chars[_spin_state["idx"]]
            cur = spin_lbl.cget("text")
            tail = cur.split("  ", 1)[1] if "  " in cur else cur
            spin_lbl.config(text=f"{ch}  {tail}")
            _spin_job[0] = self.after(120, _tick)

        def _on_phase(msg):
            if dlg.winfo_exists():
                spin_lbl.config(text=f"{_spin_chars[_spin_state['idx']]}  {msg}")

        # ── Run on demand ─────────────────────────────────────────────────────
        def _start_clean():
            if _running["v"]:
                return
            _running["v"] = True
            aggressive = _mode["aggressive"]
            accent = self._color_tools

            # Lock mode selectors while running
            safe_sel.unbind("<Button-1>"); aggr_sel.unbind("<Button-1>")

            run_btn.config(text="RUNNING", fg=SUBTEXT, cursor="arrow")
            run_btn.unbind("<Button-1>")
            for f in _fields.values():
                f.config(text="—")
            _, used_before, _ = _mc_get_ram_mb()
            before_str = f"{used_before/1024:.2f} GB" if used_before >= 1024 else f"{used_before:,.0f} MB"
            _fields["before"].config(text=before_str)
            spin_lbl.config(text="◐  Starting…", fg=accent)
            _spin_job[0] = self.after(120, _tick)

            def _work():
                return _mc_run(aggressive=aggressive,
                               log_cb=lambda m: self.after(0, lambda msg=m: _on_phase(msg)))

            def _done(freed):
                _running["v"] = False
                try:
                    if _spin_job[0]: self.after_cancel(_spin_job[0])
                except Exception:
                    pass
                if not dlg.winfo_exists():
                    return
                # Re-enable mode selectors
                safe_sel.bind("<Button-1>", lambda e: _set_mode(False) if not _running["v"] else None)
                aggr_sel.bind("<Button-1>", lambda e: _set_mode(True)  if not _running["v"] else None)
                _, used_after, _ = _mc_get_ram_mb()
                sign = "+" if freed >= 0 else ""
                if abs(freed) >= 1000:
                    freed_str = f"{sign}{freed/1024:.1f} GB"
                    after_str = f"{used_after/1024:.2f} GB"
                else:
                    freed_str = f"{sign}{freed:,.0f} MB"
                    after_str = f"{used_after:,.0f} MB"
                spin_lbl.config(text="✓  Complete", fg=GREEN)
                _fields["after"].config(text=after_str)
                _fields["freed"].config(text=freed_str, fg=GREEN)
                run_btn.config(text="RE-RUN", fg=GREEN, cursor="hand2")
                run_btn.bind("<Button-1>", lambda e: _start_clean())
                run_btn.bind("<Enter>",    lambda e: run_btn.config(fg=ACCENT1))
                run_btn.bind("<Leave>",    lambda e: run_btn.config(fg=GREEN))

            def _thread_run():
                freed = _work()
                self.after(0, lambda f=freed: _done(f))

            threading.Thread(target=_thread_run, daemon=True).start()

        run_btn.bind("<Button-1>", lambda e: _start_clean())

        # ── Position popup ────────────────────────────────────────────────────
        self._apply_font_size(self._font_size, root=dlg)
        dlg.update_idletasks()
        dw = max(dlg.winfo_reqwidth(), 270)
        dh = dlg.winfo_reqheight()
        ax = self.winfo_rootx(); ay = self.winfo_rooty(); aw = self.winfo_width()
        sw = self.winfo_screenwidth(); sh = self.winfo_screenheight()
        x = ax + (aw - dw) // 2
        y = ay - dh
        for pa in ("_speedtest_popup", "_iplookup_popup"):
            p = getattr(self, pa, None)
            if p and p.winfo_exists():
                try:
                    y = min(y, int(p.geometry().split("+")[2]) - dh)
                except Exception:
                    pass
        x = max(10, min(x, sw - dw - 10))
        y = max(10, min(y, sh - dh - 10))
        dlg.geometry(f"{dw}x{dh}+{x}+{y}")

    def _mem_safe_clean(self):
        self._mem_clean_popup()

    _mem_aggressive_clean = _mem_safe_clean

    def _build_net(self):
        # ── NETWORK ───────────────────────────────────────────────────────────
        nf, self._net_title, self._net_hdr = self._section(self._outer, "▸ NETWORK", self._color_net, "net")
        self._bind_tooltip(self._net_hdr._arrow,
            "## ▸ NETWORK\n"
            "Collapse or expand the Network section.\n"
            "# Ctrl+Click to toggle")
        self._bind_tooltip(self._net_hdr._title,
            "## NETWORK Section\n"
            "Live download and upload speeds in MB/s,\n"
            "peak tracking, and network tools dropdown.")
        self.net_down = MiniBar(nf, "DOWN", self._color_net, "MB/s", 100)
        self.net_down.pack(fill="x", pady=2)
        self._bind_tooltip(self.net_down,
            "## DOWNLOAD SPEED\n"
            "Current inbound network throughput (MB/s).\n"
            "Measured across all active interfaces.")
        self.net_up = MiniBar(nf, "UP", self._color_net, "MB/s", 100)
        self.net_up.pack(fill="x", pady=2)
        self._bind_tooltip(self.net_up,
            "## UPLOAD SPEED\n"
            "Current outbound network throughput (MB/s).\n"
            "Measured across all active interfaces.")
        nr = tk.Frame(nf, bg=PANEL)
        nr.pack(fill="x", pady=(4, 0))
        self.net_peak_down_lbl = self._pill(nr, "PEAK ↓", self._color_net)
        self._bind_tooltip(self.net_peak_down_lbl,
            "## PEAK DOWNLOAD\n"
            "Highest download speed seen this session (MB/s).")
        self.net_peak_up_lbl   = self._pill(nr, "PEAK ↑", self._color_net)
        self._bind_tooltip(self.net_peak_up_lbl,
            "## PEAK UPLOAD\n"
            "Highest upload speed seen this session (MB/s).")

        # ── Tools dropdown (Speed Test + IP Lookup) ───────────────────────────
        net_tools_drop = tk.Frame(nf, bg=BORDER, padx=4, pady=2)

        def _toggle_net_tools(e=None):
            if not self._ctrl_ok(e.state if e else 0):
                return
            if net_tools_drop.winfo_ismapped():
                net_tools_drop.pack_forget()
                tools_btn.config(text="▶ TOOLS", fg="#9999cc")
            else:
                net_tools_drop.pack(fill="x", pady=(2, 0))
                tools_btn.config(text="▼ TOOLS", fg=GREEN)
            self.update_idletasks()
            self.geometry(f"{self.winfo_width()}x{self.winfo_reqheight()}")

        tools_btn = tk.Label(
            nr, text="▶ TOOLS", bg=BORDER, fg="#9999cc",
            font=(_FONT, _BASE_FONT_SIZE - 3, "bold"), cursor="arrow", padx=5, pady=2
        )
        tools_btn.pack(side="right")
        tools_btn.bind("<Button-1>", _toggle_net_tools)
        tools_btn.bind("<Enter>", lambda e: tools_btn.config(fg=GREEN, cursor="hand2") if self._ctrl_ok(e.state) else None)
        tools_btn.bind("<Leave>", lambda e: tools_btn.config(fg="#9999cc", cursor="arrow"))
        self._bind_tooltip(tools_btn,
            "## ▶ TOOLS\n"
            "Expand network tools: Speed Test and IP Lookup.\n"
            "# Ctrl+Click to expand / collapse")

        st_btn = self._st_btn = tk.Label(
            net_tools_drop, text="▶ SPEED TEST", bg=BORDER, fg=self._color_tools,
            font=(_FONT, _BASE_FONT_SIZE - 3, "bold"), cursor="arrow", padx=5, pady=2
        )
        st_btn.pack(fill="x", pady=(0, 1))
        st_btn.bind("<Button-1>", lambda e: self._speedtest() if self._ctrl_ok(e.state) else None)
        st_btn.bind("<Enter>", lambda e: st_btn.config(fg=GREEN, cursor="hand2") if self._ctrl_ok(e.state) else None)
        st_btn.bind("<Leave>", lambda e: st_btn.config(fg=self._color_tools, cursor="arrow"))
        self._bind_tooltip(st_btn,
            "## ▶ SPEED TEST\n"
            "Run a native speed test using Ookla servers.\n"
            "Shows ping, download and upload in Mbps.\n"
            "No browser needed — runs fully in-app.\n"
            "# Ctrl+Click to run")

        ip_btn = self._ip_btn = tk.Label(
            net_tools_drop, text="⌖ IP LOOKUP", bg=BORDER, fg=self._color_tools,
            font=(_FONT, _BASE_FONT_SIZE - 3, "bold"), cursor="arrow", padx=5, pady=2
        )
        ip_btn.pack(fill="x")
        ip_btn.bind("<Button-1>", lambda e: self._ip_lookup() if self._ctrl_ok(e.state) else None)
        ip_btn.bind("<Enter>", lambda e: ip_btn.config(fg=GREEN, cursor="hand2") if self._ctrl_ok(e.state) else None)
        ip_btn.bind("<Leave>", lambda e: ip_btn.config(fg=self._color_tools, cursor="arrow"))
        self._bind_tooltip(ip_btn,
            "## ⌖ IP LOOKUP\n"
            "Shows your local IPv4, IPv6 and public IP.\n"
            "Click any value to copy it to clipboard.\n"
            "# Ctrl+Click to open")


    def _build_disk(self):
        # ── DISK I/O ──────────────────────────────────────────────────────────
        df, self._disk_title, self._disk_hdr = self._section(self._outer, "▸ DISK", self._color_disk, "disk")
        self._bind_tooltip(self._disk_hdr._arrow,
            "## ▸ DISK\n"
            "Collapse or expand the Disk I/O section.\n"
            "# Ctrl+Click to toggle")
        self._bind_tooltip(self._disk_hdr._title,
            "## DISK I/O Section\n"
            "Real-time disk read and write throughput (MB/s)\n"
            "combined across all physical drives.")
        self.disk_read  = MiniBar(df, "READ",  self._color_disk, "MB/s", 500)
        self.disk_read.pack(fill="x", pady=2)
        self._bind_tooltip(self.disk_read,
            "## DISK READ\n"
            "Current disk read throughput in MB/s.\n"
            "Combined across all drives.")
        self.disk_write = MiniBar(df, "WRITE", self._color_disk, "MB/s", 500)
        self.disk_write.pack(fill="x", pady=2)
        self._bind_tooltip(self.disk_write,
            "## DISK WRITE\n"
            "Current disk write throughput in MB/s.\n"
            "Combined across all drives.")
        dr = tk.Frame(df, bg=PANEL)
        dr.pack(fill="x", pady=(4, 0))
        self.disk_peak_read_lbl  = self._pill(dr, "PEAK R", self._color_disk)
        self._bind_tooltip(self.disk_peak_read_lbl,
            "## PEAK READ\n"
            "Highest read speed seen this session (MB/s).")
        self.disk_peak_write_lbl = self._pill(dr, "PEAK W", self._color_disk)
        self._bind_tooltip(self.disk_peak_write_lbl,
            "## PEAK WRITE\n"
            "Highest write speed seen this session (MB/s).")

        # ── STORAGE ───────────────────────────────────────────────────────────
        stf, self._storage_title, self._storage_hdr = self._section(self._outer, "▸ STORAGE", self._color_storage, "storage")
        self._bind_tooltip(self._storage_hdr._arrow,
            "## ▸ STORAGE\n"
            "Collapse or expand the Storage section.\n"
            "# Ctrl+Click to toggle")
        self._bind_tooltip(self._storage_hdr._title,
            "## STORAGE Section\n"
            "Per-drive used / total space and free space.\n"
            "Cached hourly — Ctrl+Click the header to force refresh.")
        self._storage_bars   = []   # list of MiniBar
        self._storage_frame  = stf


    def _build_statusbar(self):
        # ── Status bar ────────────────────────────────────────────────────────
        sf = tk.Frame(self, bg=BG, height=14)
        sf.pack(fill="x", padx=12, pady=(2, 8))
        sf.pack_propagate(False)
        # datetime label — packed right first so ticker doesn't eat its space
        self.time_lbl = tk.Label(sf, text="", bg=BG, fg=SUBTEXT, font=(_FONT, _BASE_FONT_SIZE - 2),
                                 anchor="center")
        # (packed only when datetime_format is active — see _update_datetime_lbl)
        # Scrolling ticker canvas
        self._ticker_canvas = tk.Canvas(sf, bg=BG, height=14,
                                        highlightthickness=0, bd=0)
        self._ticker_canvas.pack(side="left", fill="both", expand=True)
        self._ticker_text_id  = self._ticker_canvas.create_text(
            0, 7, text="", anchor="w", fill=DIM, font=(_FONT, _BASE_FONT_SIZE - 2))
        self._ticker_text_id2 = self._ticker_canvas.create_text(
            0, 7, text="", anchor="w", fill=DIM, font=(_FONT, _BASE_FONT_SIZE - 2))
        self._ticker_x      = 0.0
        self._ticker_target = ""
        self.after(300, self._ticker_step)
        self.after(100, self._update_datetime_lbl)


    def _build(self):
        # ── Top bar ───────────────────────────────────────────────────────────
        self._build_topbar()
        self._build_gpu()
        self._build_cpu()
        self._build_mem()
        self._build_net()
        self._build_disk()
        self._build_statusbar()

    def _ticker_set(self, text):
        if text != self._ticker_target:
            self._ticker_target = text
            self._ticker_canvas.itemconfig(self._ticker_text_id,  text=text)
            self._ticker_canvas.itemconfig(self._ticker_text_id2, text=text)
            self._ticker_x = 0.0
            self._ticker_canvas.coords(self._ticker_text_id,  0, 7)
            self._ticker_canvas.coords(self._ticker_text_id2, 0, 7)

    def _ticker_step(self):
        try:
            cw   = self._ticker_canvas.winfo_width()
            bbox = self._ticker_canvas.bbox(self._ticker_text_id)
            tw   = (bbox[2] - bbox[0]) if bbox else 0
            if tw > 0 and cw > 0:
                self._ticker_x -= 1.2
                GAP  = 40
                loop = tw + GAP
                if self._ticker_x < -tw:
                    self._ticker_x += loop
                self._ticker_canvas.coords(self._ticker_text_id,  self._ticker_x, 7)
                self._ticker_canvas.coords(self._ticker_text_id2, self._ticker_x + loop, 7)
        except Exception:
            pass
        self.after(30, self._ticker_step)

    def _update_datetime_lbl(self):
        """Show/hide and update the datetime label based on _datetime_format and _datetime_position."""
        fmt = self._datetime_format
        pos = self._datetime_position
        if fmt == "time":
            txt = datetime.datetime.now().strftime("%H:%M:%S")
        elif fmt == "date":
            txt = datetime.datetime.now().strftime("%Y-%m-%d")
        elif fmt == "both":
            txt = datetime.datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
        else:
            txt = ""

        # Hide both first, then show the right one
        if self.time_lbl.winfo_ismapped():
            self.time_lbl.pack_forget()
        if hasattr(self, "_time_top_lbl") and self._time_top_lbl.winfo_ismapped():
            self._time_top_lbl.pack_forget()

        if txt:
            if pos == "top" and hasattr(self, "_time_top_lbl"):
                self._time_top_lbl.config(text=txt)
                self._time_top_lbl.pack(side="left", padx=(8, 0), fill="y")
            else:
                self.time_lbl.config(text=txt)
                if not self.time_lbl.winfo_ismapped():
                    self.time_lbl.pack(side="right", fill="y", padx=(5, 0), before=self._ticker_canvas)
        self.after(1000, self._update_datetime_lbl)

    def _poll(self):
        # Skip this tick if the previous fetch thread is still running.
        # Prevents thread pile-up when hardware calls (WMI, pynvml) stall.
        if self._fetch_running:
            self.after(self._refresh_ms, self._poll)
            return
        self._fetch_running = True
        threading.Thread(target=self._fetch, daemon=True).start()

    def _fetch(self):
        try:
            cpu_pct  = psutil.cpu_percent(interval=0.5)
            freq_ghz = get_cpu_freq_ghz()
            now_proc = time.time()
            if self._proc_ts == 0.0 or (now_proc - self._proc_ts) >= 120:
                procs = threads = handles = 0
                try:
                    for p in psutil.process_iter(['num_threads', 'num_handles']):
                        procs   += 1
                        threads += p.info['num_threads'] or 0
                        handles += p.info['num_handles'] or 0
                except Exception:
                    pass
                self._proc_cache = (procs, threads, handles)
                self._proc_ts    = now_proc
            procs, threads, handles = self._proc_cache
            gpu      = get_gpu_stats(self)
            net_down, net_up = get_network_speed()
            disk_read, disk_write = get_disk_io()
            now = time.time()
            if self._disk_usage_cache is None or (now - self._disk_usage_ts) >= 3600:
                self._disk_usage_cache = get_disk_usage()
                self._disk_usage_ts    = now
            disk_usage = self._disk_usage_cache
            ram      = psutil.virtual_memory()
            self.after(0, self._apply, cpu_pct, freq_ghz, procs, threads, handles,
                       gpu, net_down, net_up, disk_read, disk_write, disk_usage, ram)
        except Exception as e:
            _log_error("_fetch", e)
        finally:
            # Always release the guard so the next poll can proceed
            self._fetch_running = False

    def _apply(self, cpu_pct, freq_ghz, procs, threads, handles,
               gpu, net_down, net_up, disk_read, disk_write, disk_usage, ram):

        # GPU
        self.gpu_name.config(text=gpu["name"])
        self.gpu_usage.set(gpu["usage"])
        self.vram_bar.max_val = gpu["vram_total"]
        self.vram_bar.set(gpu["vram_used"])
        self.gpu_watt_lbl.config(text=f"{gpu['wattage']:.0f}W" if gpu["wattage"] else "N/A")
        self.gpu_temp_lbl.config(text=self._fmt_temp(gpu["temp"])       if gpu["temp"]    else "N/A")
        if gpu["temp"] is not None:
            self._gpu_temp_min = min(self._gpu_temp_min, gpu["temp"])
            self._gpu_temp_max = max(self._gpu_temp_max, gpu["temp"])
        self.gpu_temp_max_lbl.config(text=self._fmt_temp(self._gpu_temp_max) if self._gpu_temp_max != float('-inf') else "N/A")
        self.gpu_temp_min_lbl.config(text=self._fmt_temp(self._gpu_temp_min) if self._gpu_temp_min != float('inf') else "N/A")
        self._gpu_title.config(fg=RED if gpu["usage"] > 85 else self._color_gpu)

        # CPU
        self.cpu_usage.set(cpu_pct)
        if freq_ghz is not None:
            self._freq_min_seen = min(self._freq_min_seen, freq_ghz)
            self._freq_max_seen = max(self._freq_max_seen, freq_ghz)
        self.cpu_freq_max_lbl.config(text=f"{self._freq_max_seen:.2f} GHz" if self._freq_max_seen > 0 else "N/A")
        self.cpu_freq_min_lbl.config(text=f"{self._freq_min_seen:.2f} GHz" if self._freq_min_seen != float('inf') else "N/A")
        self.cpu_proc_lbl.config(text=str(procs))
        self.cpu_thread_lbl.config(text=str(threads))
        self.cpu_handle_lbl.config(text=str(handles) if handles else "N/A")
        self._cpu_title.config(fg=RED if cpu_pct > 85 else self._color_cpu)

        # Memory
        ram_used  = ram.used / (1024**3)
        ram_total = ram.total / (1024**3)
        self._ram_peak_pct = max(self._ram_peak_pct, ram.percent)
        self.ram_bar.max_val = round(ram_total)
        self.ram_bar.set(ram_used)
        self.ram_used_lbl.config(text=f"{ram_used:.1f} GB")
        self.ram_total_lbl.config(text=f"{ram_total:.0f} GB")
        self.ram_pct_lbl.config(text=f"{ram.percent:.0f}%")
        self.ram_peak_lbl.config(text=f"{self._ram_peak_pct:.0f}%")
        self._mem_title.config(fg=RED if ram.percent > 85 else self._color_mem)

        # Network
        self._net_peak_down = max(self._net_peak_down, net_down)
        self._net_peak_up   = max(self._net_peak_up,   net_up)
        net_max = max(100, net_down * 1.5, net_up * 1.5)
        self.net_down.max_val = net_max
        self.net_up.max_val   = net_max
        self.net_down.set(net_down)
        self.net_up.set(net_up)
        self.net_peak_down_lbl.config(text=f"{self._net_peak_down:.2f} MB/s")
        self.net_peak_up_lbl.config(text=f"{self._net_peak_up:.2f} MB/s")
        self._net_title.config(fg=RED if max(net_down, net_up) > 85 else self._color_net)

        # Disk I/O
        self._disk_peak_read  = max(self._disk_peak_read,  disk_read)
        self._disk_peak_write = max(self._disk_peak_write, disk_write)
        disk_io_max = max(100, disk_read * 1.5, disk_write * 1.5)
        self.disk_read.max_val  = disk_io_max
        self.disk_write.max_val = disk_io_max
        self.disk_read.set(disk_read)
        self.disk_write.set(disk_write)
        self.disk_peak_read_lbl.config(text=f"{self._disk_peak_read:.2f} MB/s")
        self.disk_peak_write_lbl.config(text=f"{self._disk_peak_write:.2f} MB/s")
        self._disk_title.config(fg=RED if max(disk_read, disk_write) > 400 else self._color_disk)

        # Storage (rebuild only when data changes — cached hourly, no flicker)
        if disk_usage != self._last_disk_usage_applied:
            self._last_disk_usage_applied = disk_usage
            for bar in self._storage_bars:
                bar.destroy()
            self._storage_bars.clear()
            for mp, used, total, pct in disk_usage:
                label = mp.rstrip("\\").rstrip("/") or mp
                bar = MiniBar(self._storage_frame, label, self._color_storage, "GB_STORAGE", total)
                bar.pack(fill="x", pady=2)
                bar.set(used)
                lbl = bar._drive_lbl
                lbl.bind("<Button-1>", lambda e, l=lbl: self._storage_bust(l, e))
                lbl.bind("<Enter>",    lambda e, l=lbl: l.config(fg=GREEN,   cursor="hand2") if self._ctrl_ok(e.state) else None)
                lbl.bind("<Leave>",    lambda e, l=lbl: l.config(fg=SUBTEXT, cursor="arrow"))
                self._storage_bars.append(bar)
            if not disk_usage:
                bar = MiniBar(self._storage_frame, "N/A", self._color_storage, "GB", 1)
                bar.pack(fill="x", pady=2)
                self._storage_bars.append(bar)
            # Resize window to fit after storage bars are added
            self.after(50, lambda: (
                self.update_idletasks(),
                self.geometry(f"{self.winfo_width()}x{self.winfo_reqheight()}")
            ))

        self._storage_title.config(fg=RED if any(p > 90 for *_, p in disk_usage) else self._color_storage)

        # Status bar — active hotkey reminder
        if self._click_through_on:
            self._ticker_set("Hold CTRL to interact • drag to move • corner to resize")
        else:
            self._ticker_set("Click-through OFF • always interactive")

        # Cache snapshot for logging
        self._last_snapshot = {
            "gpu": gpu,
            "cpu_pct": cpu_pct, "freq_ghz": freq_ghz,
            "procs": procs, "threads": threads, "handles": handles,
            "ram_used": ram.used / (1024**3), "ram_total": ram.total / (1024**3),
            "ram_pct": ram.percent,
            "net_down": net_down, "net_up": net_up,
            "disk_read": disk_read, "disk_write": disk_write,
            "disk_usage": disk_usage,
        }

        # Periodically purge dead widget references from _font_originals.
        # Widgets are created/destroyed frequently (storage bars, tooltips, popups)
        # and holding dead id→widget pairs is the primary source of RAM growth.
        self._font_originals_prune_counter += 1
        if self._font_originals_prune_counter >= 60:   # every ~60 polls (≈1 min at 1 s)
            self._font_originals_prune_counter = 0
            if hasattr(self, "_font_originals"):
                dead = [wid for wid, (w, _) in self._font_originals.items()
                        if not w.winfo_exists()]
                for wid in dead:
                    del self._font_originals[wid]

        self.after(self._refresh_ms, self._poll)


if __name__ == "__main__":
    # Show picker on first run (no config) or after dep install if no position saved yet
    _show_picker = not _has_saved_position() or _reopen_picker_flagged() or bool(_dep_result and _dep_result.get("reopen_placement"))
    if _show_picker:
        # ── First-run placement picker ────────────────────────────────────────
        # Enumerate physical monitors via ctypes (Windows only).
        # Falls back to single virtual-screen if unavailable.
        def _get_monitors():
            """Return list of (x, y, w, h) for each monitor, primary first."""
            try:
                monitors = []
                MonitorEnumProc = ctypes.WINFUNCTYPE(
                    ctypes.c_bool,
                    ctypes.c_ulong, ctypes.c_ulong,
                    ctypes.POINTER(ctypes.c_long * 4),
                    ctypes.c_double,
                )
                def _callback(hMonitor, hdcMonitor, lprcMonitor, dwData):
                    r = lprcMonitor.contents
                    x, y, x2, y2 = r[0], r[1], r[2], r[3]
                    monitors.append((x, y, x2 - x, y2 - y))
                    return True
                _user32.EnumDisplayMonitors(None, None, MonitorEnumProc(_callback), 0)
                if monitors:
                    # Put primary (contains 0,0) first
                    monitors.sort(key=lambda m: (not (m[0] <= 0 < m[0]+m[2] and m[1] <= 0 < m[1]+m[3])))
                    return monitors
            except Exception:
                pass
            return None  # signal fallback to Tk virtual screen



        root = tk.Tk()
        root.title("PyDisplay — Choose Position")
        root.configure(bg=PANEL)
        root.resizable(False, False)
        root.wm_attributes("-topmost", True)
        root.overrideredirect(True)

        _chosen = {}

        # ── Detect monitors ───────────────────────────────────────────────────
        _monitors = _get_monitors()
        _mon_state = {"idx": 0}
        if _monitors:
            _mx, _my, _mw, _mh = _monitors[0]
        else:
            _mx, _my = 0, 0
            _mw = root.winfo_screenwidth()
            _mh = root.winfo_screenheight()
        _active_mon = {"x": _mx, "y": _my, "w": _mw, "h": _mh}

        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()

        # Custom title bar with X
        _make_titlebar(root, BG, BORDER, SUBTEXT, RED, on_close=root.destroy)

        tk.Label(root, text="Where would you like PyDisplay?",
                 bg=PANEL, fg=TEXT, font=(_FONT, _BASE_FONT_SIZE + 1, "bold"),
                 pady=12).pack(fill="x", padx=16)
        tk.Label(root, text="Click a zone to place PyDisplay.",
                 bg=PANEL, fg=SUBTEXT, font=(_FONT, _BASE_FONT_SIZE)).pack(fill="x", padx=16)

        # ── Monitor selector (only when > 1 monitor detected) ─────────────────
        if _monitors and len(_monitors) > 1:
            tk.Frame(root, bg=BORDER, height=1).pack(fill="x", padx=8, pady=(4, 0))
            mon_row = tk.Frame(root, bg=PANEL)
            mon_row.pack(fill="x", padx=16, pady=(6, 0))
            tk.Label(mon_row, text="Screen:", bg=PANEL, fg=SUBTEXT,
                     font=(_FONT, _BASE_FONT_SIZE)).pack(side="left", padx=(0, 8))
            _mon_btns = []
            def _select_monitor(idx, btns):
                _mon_state["idx"] = idx
                _mx2, _my2, _mw2, _mh2 = _monitors[idx]
                _active_mon["x"] = _mx2; _active_mon["y"] = _my2
                _active_mon["w"] = _mw2; _active_mon["h"] = _mh2
                for i, b in enumerate(btns):
                    b.config(fg=GREEN if i == idx else SUBTEXT)
            for _mi, (_mmx, _mmy, _mmw, _mmh) in enumerate(_monitors):
                _lbl2 = f"{'PRIMARY' if _mi == 0 else f'#{_mi+1}'}  {_mmw}×{_mmh}"
                _mb = tk.Label(mon_row, text=_lbl2, bg=BORDER,
                               fg=GREEN if _mi == 0 else SUBTEXT,
                               font=(_FONT, _BASE_FONT_SIZE - 1, "bold"), cursor="hand2",
                               padx=8, pady=3)
                _mb.pack(side="left", padx=(0, 4))
                _mon_btns.append(_mb)
            for _mi2, _mb2 in enumerate(_mon_btns):
                _mb2.bind("<Button-1>",
                          lambda e, i=_mi2, bs=_mon_btns: _select_monitor(i, bs))
                _mb2.bind("<Enter>",  lambda e, b=_mb2: b.config(fg=ACCENT2))
                _mb2.bind("<Leave>",  lambda e, b=_mb2, i=_mi2: b.config(
                    fg=GREEN if i == _mon_state["idx"] else SUBTEXT))

        tk.Frame(root, bg=BORDER, height=1).pack(fill="x", padx=8, pady=(6, 0))

        ZONES = [
            ("Top-Left",      0.0, 0.0),
            ("Top-Center",    0.5, 0.0),
            ("Top-Right",     1.0, 0.0),
            ("Center-Left",   0.0, 0.5),
            ("Center",        0.5, 0.5),
            ("Center-Right",  1.0, 0.5),
            ("Bottom-Left",   0.0, 1.0),
            ("Bottom-Center", 0.5, 1.0),
            ("Bottom-Right",  1.0, 1.0),
        ]

        lbl_hint = tk.Label(root, text="", bg=PANEL, fg=GREEN,
                            font=(_FONT, _BASE_FONT_SIZE, "bold"), height=1)

        def _pick(fx, fy):
            # Use saved overlay size if available, else fall back to defaults
            _cfg_aw, _cfg_ah = 320, 600
            try:
                with open(_CFG_PATH) as _cf:
                    _saved = json.load(_cf)
                    _cfg_aw = _saved.get("w", _cfg_aw)
                    _cfg_ah = _saved.get("h", _cfg_ah)
            except Exception:
                pass
            AW = max(100, _cfg_aw)
            AH = max(100, _cfg_ah)
            _mx3 = _active_mon["x"]; _my3 = _active_mon["y"]
            _mw3 = _active_mon["w"]; _mh3 = _active_mon["h"]
            # Scale margin proportionally to monitor resolution (min 8px, max 20px)
            MARGIN = max(8, min(20, int(_mw3 / 192)))
            # Calculate position relative to monitor, clamp within it, then add monitor offset
            rel_x = int(fx * (_mw3 - AW))
            rel_y = int(fy * (_mh3 - AH))
            rel_x = max(MARGIN, min(rel_x, _mw3 - AW - MARGIN))
            rel_y = max(MARGIN, min(rel_y, _mh3 - AH - MARGIN))
            _chosen["x"] = _mx3 + rel_x
            _chosen["y"] = _my3 + rel_y
            root.destroy()

        zone_frame = tk.Frame(root, bg=PANEL)
        zone_frame.pack(padx=16, pady=14)

        # Map zone names to grid positions (row, col)
        ZONE_GRID = {
            "Top-Left": (0,0), "Top-Center": (0,1), "Top-Right": (0,2),
            "Center-Left": (1,0), "Center": (1,1), "Center-Right": (1,2),
            "Bottom-Left": (2,0), "Bottom-Center": (2,1), "Bottom-Right": (2,2),
        }

        # Short display labels for each zone
        ZONE_LABELS = {
            "Top-Left": "Top\nLeft", "Top-Center": "Top\nCenter", "Top-Right": "Top\nRight",
            "Center-Left": "Mid\nLeft", "Center": "Middle", "Center-Right": "Mid\nRight",
            "Bottom-Left": "Bot\nLeft", "Bottom-Center": "Bot\nCenter", "Bottom-Right": "Bot\nRight",
        }

        for name, fx, fy in ZONES:
            row, col = ZONE_GRID[name]
            short = ZONE_LABELS[name]

            # Outer rectangle frame
            outer = tk.Frame(zone_frame, bg=BORDER, width=100, height=70,
                             cursor="hand2")
            outer.grid(row=row, column=col, padx=4, pady=4)
            outer.grid_propagate(False)

            # Inner rectangle with label
            inner = tk.Label(outer, text=short, bg=DIM, fg=ACCENT2,
                             font=(_FONT, _BASE_FONT_SIZE - 1, "bold"), justify="center",
                             cursor="hand2", relief="flat")
            inner.place(relx=0.5, rely=0.5, anchor="center", width=76, height=46)

            def _enter(e, o=outer, i=inner, n=name):
                o.config(bg=DIM)
                i.config(bg=SUBTEXT, fg=GREEN)
                lbl_hint.config(text=n)
            def _leave(e, o=outer, i=inner):
                o.config(bg=BORDER)
                i.config(bg=DIM, fg=ACCENT2)
                lbl_hint.config(text="")
            def _click(e, x=fx, y=fy):
                _pick(x, y)
            for w in (outer, inner):
                w.bind("<Enter>",    _enter)
                w.bind("<Leave>",    _leave)
                w.bind("<Button-1>", _click)

        lbl_hint.pack(pady=(0, 4))
        tk.Frame(root, bg=BORDER, height=1).pack(fill="x", padx=8)

        btn_row = tk.Frame(root, bg=PANEL, pady=8)
        btn_row.pack(fill="x", padx=16)

        for txt, fx, fy, color in [
            ("USE DEFAULT  (Top-Right)", 1.0, 0.0, ACCENT2),
            ("SKIP",                     0.0, 0.0, SUBTEXT),
        ]:
            def _cmd(e, x=fx, y=fy): _pick(x, y)
            b = tk.Label(btn_row, text=txt, bg=BORDER, fg=color,
                         font=(_FONT, _BASE_FONT_SIZE - 1, "bold"), cursor="hand2",
                         padx=10, pady=5)
            b.pack(side="left", padx=(0, 8))
            b.bind("<Button-1>", _cmd)
            b.bind("<Enter>",    lambda e, w=b: w.config(fg=GREEN))
            b.bind("<Leave>",    lambda e, w=b, c=color: w.config(fg=c))

        root.update_idletasks()
        pw = root.winfo_reqwidth()
        ph = root.winfo_reqheight()
        root.geometry(f"+{sw//2 - pw//2}+{sh//2 - ph//2}")
        root.mainloop()

        # Write chosen position to pos.json, merging any existing keys (preserve w/h)
        if _chosen:
            _write_config({"x": _chosen["x"], "y": _chosen["y"]})

    try:
        app = App()
        app.mainloop()
    except Exception:
        msg = traceback.format_exc()
        _write_crash_log(msg)
        try:
            import tkinter.messagebox as _mb
            _mb.showerror("PyDisplay — Startup Error",
                f"PyDisplay failed to start:\n\n{msg}\n\n"
                f"Full details saved to:\n{_LOG_PATH}")
        except Exception:
            pass

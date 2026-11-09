"""How many worker processes long jobs (tuning, backtests, slate) may start, so the PC stays usable.

Two limits (config `resources`, changeable on the Settings page):
  free_cores        cores always left for you (default 2)
  max_ram_fraction  the whole PC — GridSim plus everything else you have open — should stay at or below this share
                    of memory (default 0.8). Workers are counted against what is free when the job starts.
A tuning/backtest worker holds a few seasons of play-by-play: measured ~2.6 GB each.
"""
from __future__ import annotations

import os
import sys

TUNE_WORKER_GB = 3.0        # measured ~2.6 GB per worker (3 seasons + cached models), plus margin
SLATE_WORKER_GB = 1.5       # one season


def memory_gb() -> tuple[float | None, float | None]:
    """(total, available) physical memory in GB; (None, None) if unknown."""
    try:
        if sys.platform == "win32":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MEMORYSTATUSEX()
            m.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
                return m.ullTotalPhys / 2**30, m.ullAvailPhys / 2**30
            return None, None
        if os.path.exists("/proc/meminfo"):
            info = {}
            for line in open("/proc/meminfo"):
                k, v = line.split(":", 1)
                info[k] = int(v.split()[0]) / 2**20
            return info.get("MemTotal"), info.get("MemAvailable", info.get("MemFree"))
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30
        return total, None
    except Exception:
        return None, None


def limits(cfg=None) -> tuple[float, int]:
    r = (cfg.raw.get("resources") or {}) if cfg is not None else {}
    frac = float(r.get("max_ram_fraction", 0.8))
    return min(max(frac, 0.1), 1.0), max(0, int(r.get("free_cores", 2)))


def worker_count(cfg=None, per_worker_gb: float = TUNE_WORKER_GB, jobs: int | None = None,
                 main_gb: float = 1.5, verbose: bool = False) -> int:
    """Workers that fit both limits (at least 1)."""
    frac, free = limits(cfg)
    n = max(1, (os.cpu_count() or 2) - free)
    total, avail = memory_gb()
    why = f"{n} by cores ({os.cpu_count()} - {free} kept free)"
    if total:
        # memory GridSim may use = what's free now, minus what keeps the whole PC at or below frac of total
        budget = (frac * total) if avail is None else min(frac * total, avail - (1 - frac) * total)
        by_mem = max(1, int((budget - main_gb) // per_worker_gb))
        if by_mem < n:
            n = by_mem
            why = (f"{n} by memory ({total:.0f} GB total, {avail:.0f} GB free; keeping the PC at or below "
                   f"{100 * frac:.0f}% at ~{per_worker_gb:g} GB per worker)")
    if jobs is not None:
        n = max(1, min(n, jobs))
    if verbose:
        print(f"workers: {why}", flush=True)
    return n

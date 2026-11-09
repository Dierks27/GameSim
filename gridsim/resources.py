"""How many worker processes long jobs (tuning, backtests, slate) may start, so the PC stays usable.

Two limits (config `resources`, changeable on the Settings page):
  free_cores        cores always left for you (default 2)
  max_ram_fraction  the whole PC — GridSim plus everything else you have open — should stay at or below this share
                    of memory (default 0.8). Workers are counted against what is free when the job starts.
Measured worker memory (resident): ~1.9 GB holding one season of play-by-play, ~3.1 GB with three, ~3.5 GB with
four (each prepared season also carries earlier seasons for kicking/QB pools, plus up to 10 cached models).
"""
from __future__ import annotations

import os
import sys

SLATE_WORKER_GB = 2.2       # one season, a few models


def season_worker_gb(n_seasons: int) -> float:
    """Memory budget for a worker that holds n seasons (measured 1.9 / 3.1 / 3.5 GB for 1 / 3 / 4, plus margin)."""
    return 1.5 + 0.55 * max(1, n_seasons)


TUNE_WORKER_GB = season_worker_gb(4)


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
    """Workers that fit both limits (at least 1), each needing per_worker_gb."""
    return _count(cfg, lambda n: per_worker_gb, jobs, main_gb, verbose)


def tune_worker_count(cfg=None, n_seasons: int = 4, main_gb: float = 1.5, verbose: bool = False) -> int:
    """Workers for tuning with a PinnedPool(seasons=...): with n >= n_seasons each worker holds one season, with
    fewer each holds about n_seasons / n of them."""
    return _count(cfg, lambda n: season_worker_gb(-(-n_seasons // n)), None, main_gb, verbose)


def _count(cfg, per_worker, jobs, main_gb, verbose) -> int:
    frac, free = limits(cfg)
    cores = max(1, (os.cpu_count() or 2) - free)
    if jobs is not None:
        cores = max(1, min(cores, jobs))
    total, avail = memory_gb()
    if not total:
        n = min(cores, 4)                                   # memory unknown: stay modest
        why = f"{n} (memory size unknown)"
    else:
        # memory GridSim may use = what's free now, minus what keeps the whole PC at or below frac of total
        budget = (frac - 0.25) * total if avail is None else min(frac * total, avail - (1 - frac) * total)
        n = cores
        while n > 1 and main_gb + n * per_worker(n) > budget:
            n -= 1
        why = (f"{n} of {cores} possible ({total:.0f} GB total, "
               f"{'?' if avail is None else f'{avail:.0f}'} GB free; whole PC kept at or below {100 * frac:.0f}%, "
               f"~{per_worker(n):.1f} GB per worker)")
        if main_gb + per_worker(1) > budget and verbose:
            print("note: even one worker is more than the memory limit allows right now — running with one, slowly. "
                  "Closing other programs helps.", flush=True)
    if verbose:
        print(f"workers: {why}", flush=True)
    return n
